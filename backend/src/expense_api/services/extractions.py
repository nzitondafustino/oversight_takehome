"""Extract interpretations of selected document revisions with the core's PolicyCompiler, and review them.

An extraction stores the core's ExtractionResult exactly. A review reloads that snapshot, so it is of exactly the
extraction it names, and the core rejects one altered since. Neither is an approval: a candidate ruleset version
made from them enters the ruleset lifecycle as a draft, where only the compiler's approval can publish it.
"""

import json

from bson import ObjectId
from expense_validator import ExtractionResult, InvalidInputError
from expense_validator.policy.schema import RuleSet

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import ExtractionRepository
from expense_api.schemas.documents import ExtractionCreate, ReviewCreate
from expense_api.services.compilation import Compilation
from expense_api.services.documents import DocumentService
from expense_api.services.legacy import extraction_without_scope, is_legacy_extraction
from expense_api.services.rulesets import now, sha256


class ExtractionService:
    def __init__(self, settings: Settings, documents: DocumentService, repository: ExtractionRepository, compilation: Compilation):
        self.settings, self.documents, self.repository, self.compilation = settings, documents, repository, compilation

    def create(self, request: ExtractionCreate) -> dict:
        """Extract the exact selected revisions and store the core's result as an immutable snapshot."""
        self._authorize(request)
        revisions = self._revisions([(item.document_id, item.revision) for item in request.documents])
        inputs = [_compiler_input(revision) for revision in revisions]
        if request.execution_mode == "live":
            recording_set = self.settings.compiler_live_recording_set
            compiler = self.compilation.live_compiler(request.extraction_model, request.temperature, request.timeout_seconds)
        else:
            recording_set, recorded_with = request.recording_set, None if request.recorded_with is None else request.recorded_with.model_dump()
            compiler = self.compilation.replay_compiler(recording_set, recorded_with)
        result = self.compilation.run_extract(compiler, inputs, recording_set)
        if request.execution_mode == "live":
            # The core records each live reading under its model and temperature: every later step replays exactly those.
            recorded_with = {"extraction_model": result.extraction_model, "temperature": result.temperature}
        text = result.model_dump_json()
        read = {document.filename: document for document in result.documents}
        extraction = {
            "_id": f"ext_{ObjectId()}",
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
                "prompt_sha256": result.prompt_sha256,
            },
            "extraction_sha256": result.extraction_sha256,
            "output_sha256": result.output_sha256,
            "candidate_ruleset_sha256": None if result.candidate is None else result.candidate.ruleset_sha256,
            "ready_for_approval": result.ready_for_approval,
            "blockers": list(result.blockers),
            "agreement_summary": result.agreement.get("summary", {}),
            "comparison_count": len(result.comparisons),
            "result_json": text,  # the core's whole result: candidate, agreement, review items, comparisons, clauses
            "result_sha256": sha256(text),
            "created_at": now(),
        }
        self.repository.insert(extraction)
        return self.get(extraction["_id"])

    def get(self, extraction_id: str) -> dict:
        extraction = self.repository.get(extraction_id)
        if extraction is None:
            raise ApiError(404, "extraction_not_found", f"no extraction {extraction_id}")
        return extraction | {"reviews": self.repository.reviews_of(extraction_id)}

    def page(self, document_id: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """A page of extractions, newest first, each with its reviews. No stored core result is read."""
        items, total = self.repository.page(document_id, limit, offset)
        reviews = self.repository.reviews_of_each([item["_id"] for item in items])
        return [item | {"reviews": [review for review in reviews if review["extraction_id"] == item["_id"]]} for item in items], total

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
        compiler = self.compilation.replay_compiler(extraction["provenance"]["recording_set"], extraction["provenance"]["recorded_with"])
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
            "recording_set": provenance["recording_set"],
            "recorded_with": provenance["recorded_with"],
            "reading_kind": result.reading_kind,
            "extraction_sha256": result.extraction_sha256,
            "output_sha256": result.output_sha256,
            "ready_for_approval": result.ready_for_approval,
            "blockers": list(result.blockers),
            "comparisons_needing_review": sum(comparison.review_required for comparison in result.comparisons),
            "documents": [_compiler_input(revision) | {"sha256": revision["content_sha256"], "revision": revision["revision"]} for revision in revisions],
            "comparisons_json": json.dumps([comparison.model_dump(mode="json") for comparison in result.comparisons]),
            "extraction_id": extraction_id,
            "prepared_review": prepared,
        }

    def _authorize(self, request: ExtractionCreate) -> None:
        if request.execution_mode != "live":
            return
        if not self.settings.allow_live:
            raise ApiError(403, "live_inference_disabled", "this deployment does not allow live extraction")
        if self.settings.compiler_live_recording_set is None:
            raise ApiError(503, "live_extraction_unavailable", "this deployment has no live recording set to record readings into")
        if request.extraction_model not in self.settings.allowed_models:
            raise ApiError(422, "model_not_allowed", f"not an allowed model: {request.extraction_model}", {"allowed": sorted(self.settings.allowed_models)})

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
