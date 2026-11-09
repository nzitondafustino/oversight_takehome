"""Extract interpretations of selected document revisions with the core's PolicyCompiler, and review them.

An extraction stores the core's ExtractionResult exactly. A review reloads that snapshot, so it is of exactly the
extraction it names, and the core rejects one altered since. Neither is an approval: a candidate ruleset version
made from them enters the ruleset lifecycle as a draft, where only the compiler's approval can publish it.
"""

import json
from collections.abc import Callable

from bson import ObjectId
from expense_validator import ExtractionResult, InvalidInputError
from expense_validator.policy.schema import RuleSet
from expense_validator.compiler.llm_extract import observe_calls

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import ExtractionRepository
from expense_api.schemas.documents import ChangeRequestCreate, ChangeRequestResolve, ExtractionCreate, ReviewCreate
from expense_api.services.compilation import Compilation
from expense_api.services.compilation_metrics import CompilationMeter
from expense_api.services.discovery import extraction_models
from expense_api.services.documents import DocumentService
from expense_api.services.legacy import extraction_without_scope, is_legacy_extraction
from expense_api.services.rulesets import BEHAVIOUR_FIELDS, now, rule_changes, sha256, verified_artifact
from expense_api.services.source_locations import locate


class ExtractionService:
    def __init__(self, settings: Settings, documents: DocumentService, repository: ExtractionRepository, compilation: Compilation):
        self.settings, self.documents, self.repository, self.compilation = settings, documents, repository, compilation

    def plan(self, request: ExtractionCreate):
        from expense_api.services.extraction_plan import resolve
        if request._resolved_plan is None:
            request._resolved_plan = resolve(self, request)
        return request._resolved_plan

    def check(self, request: ExtractionCreate) -> list[dict]:
        """Refuse a request that cannot run, before any work: the mode, the model, and each exact revision."""
        self._authorize(request)
        plan = self.plan(request)
        if request.run_mode and request.execution_mode == "replay":
            self.compilation.replay_compiler(plan.recording_set, plan.recorded_with)  # configuration check only, no extraction
        return plan.revisions

    def create(self, request: ExtractionCreate, progress: Callable[[dict], None] | None = None) -> dict:
        meter = CompilationMeter()
        with observe_calls(meter.record):
            return self._create(request, progress, meter)

    def _create(self, request: ExtractionCreate, progress: Callable[[dict], None] | None, meter: CompilationMeter) -> dict:
        """Extract the exact selected revisions and store the core's result as an immutable snapshot.

        With progress, a live extraction reads the documents one at a time first, reporting each. The core records
        each live reading and replays a recording it has, so the whole extraction that follows calls no model again.
        """
        revisions = self.check(request)
        plan = self.plan(request)
        inputs = [_compiler_input(revision) for revision in revisions]
        # Reviewed corrections of the draft rules, compiled in and validated like the rule logic, kept with who and why.
        overrides = [{"rule_id": change.rule_id, "set": change.set, "reviewer": request.actor, "rationale": change.rationale, **({"add": True} if change.add else {})} for change in request.rule_overrides]
        if request.carry_overrides_from is not None:  # the published version's corrections, as they were made and by whom
            given = {item["rule_id"] for item in overrides}
            overrides = sorted([*(item for item in self._published_overrides(request.carry_overrides_from) if item["rule_id"] not in given), *overrides], key=lambda item: item["rule_id"])
        if plan.report and plan.report["base_ruleset_id"] and request.carry_overrides_from is None:
            allowed = set(plan.report["affected_rule_ids"])
            given = {item["rule_id"] for item in overrides}
            overrides = [*(item for item in self._published_overrides(plan.report["base_ruleset_id"]) if item["rule_id"] in allowed - given), *overrides]
        if plan.report:
            overrides = [item for item in overrides if item["rule_id"] in plan.report["affected_rule_ids"]]
        report = progress or (lambda _: None)
        if request.execution_mode == "live":
            recording_set = self.settings.compiler_live_recording_set
            compiler = self.compilation.live_compiler(request.extraction_model, request.temperature, request.timeout_seconds)
            if plan.report and plan.report["mode"] == "incremental":
                unchanged = [item for item in inputs if item["document_id"] not in plan.report["changed_document_ids"]]
                if unchanged:
                    # Refuse before paid work if any unchanged source cannot be replayed under this generation.
                    replay = self.compilation.replay_compiler(recording_set, {"extraction_model": request.extraction_model, "temperature": compiler.settings.temperature})
                    self.compilation.run_extract(replay, unchanged, recording_set)
            compiler = self.compilation.with_logic(compiler, plan.logic_snapshot)
            if progress is not None:
                for index, document in enumerate(inputs):
                    report({"stage": "reading", "done": index, "total": len(inputs), "current": document["filename"]})
                    try:
                        compiler.extract([document], rule_overrides=overrides)  # the same settings the whole extraction uses, so its reading is reused
                    except Exception:  # noqa: BLE001 - a document refused alone is judged again, with the others, below
                        pass
        else:
            recording_set, recorded_with = plan.recording_set, plan.recorded_with
            compiler = self.compilation.with_logic(self.compilation.replay_compiler(recording_set, recorded_with), plan.logic_snapshot)
        report({"stage": "building", "done": len(inputs), "total": len(inputs), "current": None})
        result = self.compilation.run_extract(compiler, inputs, recording_set, overrides)
        if request.execution_mode == "live":
            # The core records each live reading under its model and temperature: every later step replays exactly those.
            recorded_with = {"extraction_model": result.extraction_model, "temperature": result.temperature}
        text = result.model_dump_json()
        read = {document.filename: document for document in result.documents}
        extraction = {
            "_id": f"ext_{ObjectId()}",
            "logic_snapshot": plan.logic_snapshot,
            "run": plan.report,
            "status": "no_candidate" if result.candidate is None else ("ready_for_approval" if result.ready_for_approval else "review_required"),
            "documents": [
                {"document_id": revision["document_id"], "revision": revision["revision"], "filename": revision["filename"], "content_sha256": revision["content_sha256"], "reading_error": read[revision["filename"]].reading_error}
                for revision in revisions
            ],
            "provenance": {
                "execution_mode": request.execution_mode,
                "recording_set": recording_set,
                "recorded_with": recorded_with,
                "reading_kind": result.reading_kind,
                "extraction_model": result.extraction_model,
                "temperature": result.temperature,
                "timeout_seconds": result.timeout_seconds,
                "overrides": list(result.overrides),
                "rule_overrides_from": request.carry_overrides_from,
                "prompt_sha256": result.prompt_sha256,
            },
            "extraction_sha256": result.extraction_sha256,
            "output_sha256": result.output_sha256,
            "candidate_ruleset_sha256": None if result.candidate is None else result.candidate.ruleset_sha256,
            "ready_for_approval": result.ready_for_approval,
            "blockers": list(result.blockers),
            "agreement_summary": result.agreement.get("summary", {}),
            "comparison_count": len(result.comparisons),
            "rule_overrides": overrides,
            "result_json": text,  # the core's whole result: candidate, agreement, review items, comparisons, clauses
            "result_sha256": sha256(text),
            "created_at": now(),
            "analytics": meter.summary([{**revision, "reading_error": read[revision["filename"]].reading_error} for revision in revisions]),
        }
        self.repository.insert(extraction)
        return self.get(extraction["_id"])

    def get(self, extraction_id: str) -> dict:
        extraction = self.repository.get(extraction_id)
        if extraction is None:
            raise ApiError(404, "extraction_not_found", f"no extraction {extraction_id}")
        return self._with_history([extraction | {"reviews": self.repository.reviews_of(extraction_id)}])[0]

    def page(self, document_id: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """A page of extractions, newest first, each with its reviews. No stored core result is read."""
        items, total = self.repository.page(document_id, limit, offset)
        reviews = self.repository.reviews_of_each([item["_id"] for item in items])
        return self._with_history([item | {"reviews": [review for review in reviews if review["extraction_id"] == item["_id"]]} for item in items]), total

    def _with_history(self, items: list[dict]) -> list[dict]:
        """Each extraction with the ruleset versions made from it and its open change requests. Read only."""
        ids = [item["_id"] for item in items]
        versions, open_requests = self.repository.versions_of_each(ids), self.repository.open_change_requests_of_each(ids)
        return [item | {"versions": versions.get(item["_id"], []), "open_change_requests": open_requests.get(item["_id"], 0)} for item in items]

    def source(self, extraction_id: str, comparison_id: str) -> dict:
        """One comparison's clause, verified in the exact revision this extraction read, read from storage."""
        extraction = self.get(extraction_id)
        comparison = next((item for item in self.result(extraction_id).comparisons if item.comparison_id == comparison_id), None)
        if comparison is None:
            raise ApiError(404, "comparison_not_found", f"extraction {extraction_id} has no comparison {comparison_id}")
        read = next((item for item in extraction["documents"] if item["document_id"] == comparison.document_id), None)
        if read is None:
            raise ApiError(404, "document_not_found", f"extraction {extraction_id} read no document {comparison.document_id}")
        revision = self.documents.get(read["document_id"], read["revision"])
        found = locate(revision["content"], revision["content_sha256"], read["content_sha256"], comparison)
        document = {"document_id": revision["document_id"], "revision": revision["revision"], "filename": revision["filename"], "content_sha256": revision["content_sha256"], "content": revision["content"]}
        return {"comparison_id": comparison_id, "document": document, "section": (comparison.location or {}).get("section"), **found}

    def baseline(self, extraction_id: str) -> dict:
        """This compilation beside the latest published version. Read only: nothing is carried over by reading it.

        A field whose clause text and readings are what the published version reviewed is unchanged, and its earlier
        decision can be made again explicitly; any other earlier decision is changed and must be decided anew. Decisions
        always name one extraction, so none is ever applied to another without a reviewer saving it again.
        """
        self.get(extraction_id)
        result = self.result(extraction_id)
        published = self.repository.latest_published()
        if published is None:
            return {"published": None}
        compiled = published.get("compilation") or {}
        before = {(item["policy_doc"], item["rule_id"], item["field"]): item for item in json.loads(compiled.get("comparisons_json") or "[]")}
        accepted = {decision["comparison_id"]: decision for decision in (published.get("review") or {}).get("decisions") or []}
        decisions, clauses = [], {}
        for comparison in result.comparisons:
            earlier = before.get((comparison.policy_doc, comparison.rule_id, comparison.field))
            if earlier is None:
                continue
            if earlier["clause_hash"] != comparison.clause_hash:
                clauses[(comparison.policy_doc, comparison.rule_id)] = {"policy_doc": comparison.policy_doc, "rule_id": comparison.rule_id}
            decision = accepted.get(earlier["comparison_id"])
            if decision is None or not comparison.review_required:
                continue
            same = all(earlier[name] == getattr(comparison, name) for name in ("clause_hash", "status", "regex_value", "llm_value"))
            previous = {name: earlier[name] for name in ("comparison_id", "status", "regex_value", "llm_value", "evidence", "clause_hash")}
            decisions.append({"comparison_id": comparison.comparison_id, "state": "unchanged" if same else "changed", "previous": previous, "decision": decision})
        changes = []
        if result.candidate is not None:
            old, new = json.loads(verified_artifact(published)), json.loads(result.candidate.ruleset.to_json())
            changes = rule_changes(old, new, {rule["rule_id"] for rule in old["rules"]} | {rule["rule_id"] for rule in new["rules"]}, BEHAVIOUR_FIELDS)
        ref = {"ruleset_id": published["_id"], "name": published["name"], "version": published["version"], "published_at": published.get("published_at")}
        return {"published": ref, "rule_changes": changes, "changed_clauses": list(clauses.values()), "decisions": decisions}

    def get_review(self, extraction_id: str, review_id: str) -> dict:
        review = self.repository.get_review(extraction_id, review_id)
        if review is None:
            raise ApiError(404, "review_not_found", f"no review {review_id} of extraction {extraction_id}")
        return review

    # --- Change requests: a reviewer's request that one field be corrected. Nothing is rewritten by one. ---

    def request_change(self, extraction_id: str, request: ChangeRequestCreate) -> dict:
        self.get(extraction_id)
        comparison = next((item for item in self.result(extraction_id).comparisons if item.comparison_id == request.comparison_id), None)
        if comparison is None:
            raise ApiError(422, "unknown_comparison", f"extraction {extraction_id} has no comparison {request.comparison_id}")
        record = {
            "_id": f"chg_{ObjectId()}",
            "extraction_id": extraction_id,
            "comparison_id": comparison.comparison_id,
            "rule_id": comparison.rule_id,
            "field": comparison.field,
            "policy_doc": comparison.policy_doc,
            "document_id": comparison.document_id,
            "requested_correction": request.requested_correction,
            "comment": request.comment,
            "status": "open",
            "requested_by": {"claimed": request.actor, "verified": False},
            "created_at": now(),
            "resolved_by": None,
            "resolution_note": None,
            "resolved_at": None,
        }
        self.repository.insert_change_request(record)
        return record

    def change_requests(self, extraction_id: str) -> list[dict]:
        self.get(extraction_id)
        return self.repository.change_requests_of(extraction_id)

    def resolve_change(self, extraction_id: str, request_id: str, request: ChangeRequestResolve) -> dict:
        existing = next((item for item in self.change_requests(extraction_id) if item["_id"] == request_id), None)
        if existing is None:
            raise ApiError(404, "change_request_not_found", f"no change request {request_id} of extraction {extraction_id}")
        if existing["status"] != "open":
            raise ApiError(409, "change_request_resolved", f"change request {request_id} is already resolved")
        return self.repository.resolve_change_request(extraction_id, request_id, {"resolved_by": {"claimed": request.actor, "verified": False}, "resolution_note": request.note, "resolved_at": now()})

    def refuse_open_change_requests(self, extraction_id: str, step: str) -> None:
        """Changes were requested on this extraction's fields: refuse a step that would use it until they are resolved."""
        open_requests = [item for item in self.repository.change_requests_of(extraction_id) if item["status"] == "open"] if self.repository.change_requests is not None else []
        if open_requests:
            raise ApiError(409, "changes_requested", f"{len(open_requests)} change requests on this extraction are open: resolve them before {step}",
                           {"change_requests": [{"change_request_id": item["_id"], "rule_id": item["rule_id"], "field": item["field"]} for item in open_requests]})

    def result(self, extraction_id: str, *, for_new_step: bool = False) -> ExtractionResult:
        """The stored core ExtractionResult, refused if it no longer matches its hash.

        One stored before the single-company migration is read for audit without its obsolete customer_id. Its
        hashes were made with that scope, so it can start no new step: for_new_step refuses it.
        """
        extraction = self.repository.get(extraction_id, with_result=True)
        if extraction is None:
            raise ApiError(404, "extraction_not_found", f"no extraction {extraction_id}")
        text = extraction["result_json"]
        if sha256(text) != extraction["result_sha256"]:
            raise ApiError(500, "integrity_error", f"extraction {extraction_id} does not match its stored hash")
        data = json.loads(text)
        if not is_legacy_extraction(data):
            return ExtractionResult.model_validate_json(text)
        if for_new_step:
            raise ApiError(409, "legacy_extraction", f"extraction {extraction_id} was stored before the single-company migration: it stays readable for audit, but a new review or version needs a new extraction")
        return ExtractionResult.model_validate_json(json.dumps(extraction_without_scope(data)))

    def review(self, extraction_id: str, request: ReviewCreate) -> dict:
        """Run the core's review of the stored snapshot, with the original revisions, and store it, ready or not."""
        extraction = self.get(extraction_id)
        if request.extraction_sha256 != extraction["extraction_sha256"]:
            raise ApiError(409, "stale_extraction", "the review names another extraction than this one", {"extraction_sha256": extraction["extraction_sha256"]})
        result = self.result(extraction_id, for_new_step=True)
        revisions = self._revisions([(item["document_id"], item["revision"]) for item in extraction["documents"]])
        compiler = self.compilation.with_logic(self.compilation.replay_compiler(extraction["provenance"]["recording_set"], extraction["provenance"]["recorded_with"]), extraction.get("logic_snapshot"))
        try:
            reviewed = compiler.review(result, request.decisions, policy=request.review_policy, documents=[_compiler_input(revision) for revision in revisions])
        except InvalidInputError as error:
            raise ApiError(422, "invalid_review_decisions", str(error)) from None
        text = reviewed.model_dump_json()
        review = {
            "_id": f"rev_{ObjectId()}",
            "extraction_id": extraction_id,
            "actor": {"claimed": request.actor, "verified": False},
            "extraction_sha256": reviewed.extraction_sha256,
            "review_sha256": reviewed.review_sha256,
            "candidate_ruleset_sha256": reviewed.candidate_ruleset_sha256,
            "policy": reviewed.policy,
            "decisions": request.decisions,
            "ready_for_approval": reviewed.ready_for_approval,
            "complete": reviewed.complete,
            "remaining": list(reviewed.remaining),
            "issues": [issue.model_dump() for issue in reviewed.issues],
            "result_json": text,  # the core's ReviewResult: every field with its decision and final value
            "result_sha256": sha256(text),
            "created_at": now(),
        }
        self.repository.insert_review(review)
        return review

    def candidate(self, extraction_id: str, review_id: str | None) -> tuple[RuleSet, dict]:
        """The candidate ruleset of a reviewed extraction, and the compilation a ruleset version keeps of it."""
        extraction = self.get(extraction_id)
        self.refuse_open_change_requests(extraction_id, "a version is made from it")
        if extraction["status"] == "no_candidate":
            raise ApiError(409, "no_candidate", "the compiler built no valid candidate ruleset from these documents", {"blockers": extraction["blockers"]})
        prepared = None
        if review_id is not None:
            review = self.repository.get_review(extraction_id, review_id)
            if review is None:
                raise ApiError(404, "review_not_found", f"no review {review_id} of extraction {extraction_id}")
            if not review["ready_for_approval"]:
                raise ApiError(409, "review_not_ready", "this review leaves fields undecided or has issues, so it cannot become a candidate", {"remaining": review["remaining"]})
            prepared = {"review_id": review_id, "policy": review["policy"], "decisions": review["decisions"], "review_sha256": review["review_sha256"]}
        elif not extraction["ready_for_approval"]:
            raise ApiError(409, "review_required", "the extraction has fields that need a review decision: name a ready review", {"blockers": extraction["blockers"]})
        result = self.result(extraction_id, for_new_step=True)
        revisions = self._revisions([(item["document_id"], item["revision"]) for item in extraction["documents"]])
        provenance = extraction["provenance"]
        return result.candidate.ruleset, {
            "logic_snapshot": extraction.get("logic_snapshot"),
            "run": extraction.get("run"),
            "recording_set": provenance["recording_set"],
            "recorded_with": provenance["recorded_with"],
            "reading_kind": result.reading_kind,
            "extraction_sha256": result.extraction_sha256,
            "output_sha256": result.output_sha256,
            "ready_for_approval": result.ready_for_approval,
            "blockers": list(result.blockers),
            "comparisons_needing_review": sum(comparison.review_required for comparison in result.comparisons),
            # Each revision's text, for the version to store by reference: an S3-backed revision's own object is reused.
            "documents": [_compiler_input(revision) | {"sha256": revision["content_sha256"], "revision": revision["revision"], "storage": revision.get("storage"), "content_sha256": revision["content_sha256"]} for revision in revisions],
            "comparisons_json": json.dumps([comparison.model_dump(mode="json") for comparison in result.comparisons]),
            "extraction_id": extraction_id,
            "prepared_review": prepared,
            "rule_overrides": extraction.get("rule_overrides") or [],  # compiled in again at approval and publication
        }

    def _published_overrides(self, ruleset_id: str) -> list[dict]:
        version = None if self.repository.rulesets is None else self.repository.rulesets.find_one({"_id": ruleset_id}, {"status": True, "compilation.rule_overrides": True})
        if version is None:
            raise ApiError(404, "ruleset_not_found", f"no ruleset {ruleset_id}")
        if version["status"] != "published":
            raise ApiError(409, "not_published", "only a published version's rule corrections are carried into a new compilation")
        return list((version.get("compilation") or {}).get("rule_overrides") or [])

    def _authorize(self, request: ExtractionCreate) -> None:
        if request.execution_mode != "live":
            return
        if not self.settings.allow_live:
            raise ApiError(403, "live_inference_disabled", "this deployment does not allow live extraction")
        if self.settings.compiler_live_recording_set is None:
            raise ApiError(503, "live_extraction_unavailable", "this deployment has no live recording set to record readings into")
        allowed = extraction_models(self.settings)
        if request.extraction_model not in allowed:
            raise ApiError(422, "model_not_allowed", f"not an allowed extraction model: {request.extraction_model}", {"allowed": allowed})

    def _revisions(self, selected: list[tuple[str, int]]) -> list[dict]:
        """The exact stored revisions, one per document: a candidate holds one revision of each document."""
        repeated = sorted({document_id for document_id, _ in selected if [item for item, _ in selected].count(document_id) > 1})
        if repeated:
            raise ApiError(422, "repeated_document", f"select one revision of each document: {repeated}")
        revisions = [self.documents.get(document_id, revision) for document_id, revision in selected]
        return sorted(revisions, key=lambda revision: revision["filename"])


def _compiler_input(revision: dict) -> dict:
    """The core's PolicyDocumentInput: the original text, unchanged, under the document's own ID."""
    return {"document_id": revision["document_id"], "filename": revision["filename"], "content": revision["content"]}
