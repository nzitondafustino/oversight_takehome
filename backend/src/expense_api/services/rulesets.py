"""Check and store compiled rulesets with the core's own schema, runtime check and evidence check."""

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from expense_validator import ConfigurationError, EvidenceError, ExpenseValidator, ValidatorSettings
from expense_validator.evidence import PolicyEvidence, policy_evidence
from expense_validator.policy.schema import CheckRule, RuleSet
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import RulesetRepository
from expense_api.schemas.rulesets import RulesetCreate

if TYPE_CHECKING:
    from expense_api.services.compilation import Compilation
    from expense_api.services.extractions import ExtractionService


def now() -> datetime:
    """The current UTC time at MongoDB's millisecond precision, so a stored time reads back equal."""
    moment = datetime.now(UTC)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ArtifactStore:
    """Stored rulesets as files named by their SHA-256, as the validator reads its ruleset from a file."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, record: dict) -> Path:
        text = verified_artifact(record)
        path = self.directory / f"{record['artifact_sha256']}.json"
        # The database is the record: a file that differs from it is replaced, never trusted.
        if not path.is_file() or path.read_text() != text:
            self.directory.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
            partial.write_text(text)
            os.replace(partial, path)  # readers never see a partial file
        return path

    def policy_docs(self, record: dict, documents) -> Path | None:
        """Materialize verified, immutable version sources for runtime evidence, cached by source manifest hash."""
        compiled = record.get("compilation")
        if compiled is None:
            return None  # Uploaded legacy artifacts still use the deployment's policy directory.
        entries = compiled["documents"]
        manifest = json.dumps(sorted((entry["filename"], entry["sha256"]) for entry in entries))
        folder = self.directory / "policy-documents" / sha256(manifest)
        folder.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            filename = entry["filename"]
            if Path(filename).name != filename or not filename.endswith(".md"):
                raise ApiError(500, "integrity_error", "a compiled source has an invalid filename")
            path = folder / filename
            if path.is_file() and sha256(path.read_text()) == entry["sha256"]:
                continue
            [source] = documents.resolve([entry])
            partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
            partial.write_text(source["content"])
            os.replace(partial, path)
        return folder


def verified_artifact(record: dict) -> str:
    """The record's artifact text, refused when it no longer matches the hash it was stored with."""
    text = record["artifact_json"]
    if sha256(text) != record["artifact_sha256"]:
        raise ApiError(500, "integrity_error", f"ruleset {record['_id']} does not match its stored hash")
    return text


log = logging.getLogger(__name__)


class RulesetService:
    def __init__(self, settings: Settings, repository: RulesetRepository, compilation: "Compilation", extractions: "ExtractionService"):
        self.settings = settings
        self.repository = repository
        self.compilation = compilation
        self.extractions = extractions

    def create(self, request: RulesetCreate) -> dict:
        """Store a new draft version: compiled from policy documents by the core, or an uploaded artifact.

        Either way the artifact is checked as the core would load it. Only a compiled version can later be
        approved, as the compiler's approval binds the extraction it came from.
        """
        if request.extraction is not None:
            ruleset, compilation = self.extractions.candidate(request.extraction.extraction_id, request.extraction.review_id)
        elif request.compile is not None:
            ruleset, compilation = self._compile(request)
        else:
            ruleset, compilation = _parse(request.artifact), None
        text = ruleset.to_json()  # the core's canonical form, with exact decimal strings
        problems = self._runtime_problems(text) + self._evidence_problems(ruleset, None if compilation is None else compilation["documents"])
        if problems:
            raise ApiError(422, "invalid_ruleset", "the ruleset cannot run against this deployment's catalog and policy documents", problems)
        if request.based_on_version is not None and self.repository.latest_version(request.name) != request.based_on_version:
            raise ApiError(409, "version_conflict", f"version {request.based_on_version} of {request.name} is not the latest version: start again from the latest")
        source = request.source if "source" in request.model_fields_set or compilation is None else request.source.model_copy(update={"kind": "policy_compiler"})
        return self._store_version(request.name, request.based_on_version, request.based_on_version, source.model_dump(), ruleset, text, compilation, None)

    def copy_duplicate(self, record: dict, published: dict, actor: str, rationale: str) -> dict:
        """Create an identical successor with the published version's verified compilation, never its approval."""
        text = verified_artifact(record)
        if text != verified_artifact(published):
            raise ApiError(409, "no_identical_publication", "the complete artifacts must be identical")
        compiled = published["compilation"]
        texts = self.compilation.documents.resolve(compiled["documents"])
        compilation = {**compiled, "documents": [{**entry, "content": item["content"]} for entry, item in zip(compiled["documents"], texts)]}
        return self._store_version(record["name"], record["version"], None,
            {"kind": "policy_compiler", "reference": published["_id"], "description": f"Duplicate of {record['_id']}; requested by {actor}: {rationale}"[:500]},
            RuleSet.from_json(text), text, compilation, None, duplicate_of=record["_id"])

    def _store_version(self, name: str, based_on_version: int | None, expected_latest: int | None, source: dict, ruleset: RuleSet, text: str, compilation: dict | None, edits: dict | None, duplicate_of: str | None = None) -> dict:
        """Store one new draft version: its documents to S3 first, then the record. expected_latest makes another
        version created since a conflict; None takes the next number."""
        created, active = now(), [rule for rule in ruleset.rules if rule.status == "active"]
        ruleset_id = f"rs_{ObjectId()}"  # increasing within a process, so equal timestamps still list in order
        # The documents' text goes to S3 first, by reference; the version is stored only once every object is.
        persisted = None
        if compilation is not None:
            persisted = self.compilation.documents.persist(ruleset_id, compilation["documents"])
            compilation = {**compilation, "documents": persisted.entries}
        record = {
            "_id": ruleset_id,
            "name": name,
            "status": "draft",
            "based_on_version": based_on_version,
            "source": source,
            "artifact_json": text,
            "artifact_sha256": sha256(text),
            "ruleset_version": ruleset.ruleset_version,
            "schema_version": ruleset.schema_version,
            "rule_count": len(ruleset.rules),
            "active_rule_count": len(active),
            "compilation": compilation,
            "review": None,
            "approval_json": None,
            "approval_reference": None,
            "approval_mode": None,
            "publication_mode": None,
            "created_at": created,
            "submitted_at": None,
            "approved_at": None,
            "published_at": None,
            "transitions": [{"from_status": None, "to_status": "draft", "at": created, "actor": None if edits is None else {"claimed": edits["actor"], "verified": False}, "content_hash": sha256(text), "approval_reference": None, "reason": None if edits is None else edits["rationale"]}],
            "edits": edits,
        }
        if duplicate_of is not None:
            record["duplicate_of"] = duplicate_of
        try:
            return self.repository.create(record, expected_latest)
        except ApiError:  # another version took this number: nothing references what this request wrote
            if persisted is not None:
                self.compilation.documents.discard(persisted.created, ruleset_id, "version_not_stored")
            raise
        except PyMongoError:
            if persisted is not None:
                return self._after_failed_insert(ruleset_id, persisted)
            raise

    def edit(self, ruleset_id: str, request) -> dict:
        """A new draft version with reviewed changes to some rules' logic. The version edited never changes.

        The changes are rule overrides (compiler/overrides.py): compiled again, by replay, from this version's own
        documents and readings, so the compiler builds, validates and hashes the result, and its review decisions
        carry over to be checked again. Approval and publication then go through the lifecycle as for any draft.
        """
        source = self.get(ruleset_id)
        compiled = source.get("compilation")
        if compiled is None:
            raise ApiError(409, "not_compiled", "this version was uploaded as an artifact: only a version the compiler built can be edited")
        new = [{"rule_id": change.rule_id, "set": change.set, "reviewer": request.actor, "rationale": change.rationale or request.rationale} for change in request.changes]
        overrides = merge_overrides(compiled.get("rule_overrides") or [], new)
        texts = self.compilation.documents.resolve(compiled["documents"])
        logic_snapshot = compiled.get("logic_snapshot")
        if logic_snapshot:
            import yaml
            logic = yaml.safe_load(logic_snapshot)
            wanted = {change.rule_id for change in request.changes}
            promoted = [entry for entry in logic.get("retained_rules", []) if entry["rule_id"] in wanted]
            logic["retained_rules"] = [entry for entry in logic.get("retained_rules", []) if entry["rule_id"] not in wanted]
            logic["rules"] += [{key: value for key, value in entry.items() if key not in ("policy_doc", "provenance")} for entry in promoted]
            logic_snapshot = yaml.safe_dump(logic, sort_keys=False)
        compiler = self.compilation.with_logic(self.compilation.replay_compiler(compiled["recording_set"], compiled.get("recorded_with")), logic_snapshot)
        extraction = self.compilation.run_extract(compiler, texts, compiled["recording_set"], overrides)
        if extraction.candidate is None:
            # Fields awaiting a review decision are not what refused the edit: their decisions carry over.
            caused = [blocker for blocker in extraction.blockers if not blocker.startswith("unresolved:")] or list(extraction.blockers)
            blockers = [{"message": blocker, "rule_id": _rule_of(blocker)} for blocker in caused]
            raise ApiError(422, "rule_edit_invalid", "the compiler refuses these changes: " + "; ".join(item["message"] for item in blockers[:3]), {"blockers": blockers})
        ruleset = extraction.candidate.ruleset
        text = ruleset.to_json()
        changes = rule_changes(json.loads(source["artifact_json"]), json.loads(text), {change.rule_id for change in request.changes})
        if not changes:
            raise ApiError(422, "no_change", "these changes leave every rule as it was")
        problems = self._runtime_problems(text) + self._evidence_problems(ruleset, texts)
        if problems:
            raise ApiError(422, "invalid_ruleset", "the ruleset cannot run against this deployment's catalog and policy documents", problems)
        review = source.get("review") or {}
        prepared = compiled.get("prepared_review")
        if review.get("decisions"):  # the decisions this version was accepted with: checked again by the compiler at review
            prepared = {"review_id": f"from {ruleset_id}", "policy": review["policy"], "decisions": review["decisions"], "review_sha256": review["review_sha256"]}
        documents = [{**entry, "content": item["content"]} for entry, item in zip(compiled["documents"], texts)]
        compilation = {
            **{name: compiled.get(name) for name in ("recording_set", "recorded_with", "extraction_id", "reading_kind")},
            "logic_snapshot": logic_snapshot,
            "extraction_sha256": extraction.extraction_sha256,
            "output_sha256": extraction.output_sha256,
            "ready_for_approval": extraction.ready_for_approval,
            "blockers": list(extraction.blockers),
            "comparisons_needing_review": sum(comparison.review_required for comparison in extraction.comparisons),
            "documents": documents,
            "comparisons_json": json.dumps([comparison.model_dump(mode="json") for comparison in extraction.comparisons]),
            "prepared_review": prepared,
            "rule_overrides": overrides,
        }
        edits = {"based_on": ruleset_id, "based_on_version": source["version"], "based_on_artifact_sha256": source["artifact_sha256"], "actor": request.actor, "rationale": request.rationale, "at": now(), "changes": changes}
        return self._store_version(source["name"], source["version"], None, {"kind": "rule_edit", "description": request.rationale[:500], "reference": ruleset_id}, ruleset, text, compilation, edits)

    def _after_failed_insert(self, ruleset_id: str, persisted) -> dict:
        """The version may or may not have been stored. Its objects are deleted only once MongoDB says it was not."""
        try:
            stored = self.repository.get(ruleset_id)
        except PyMongoError:
            log.error("kept the documents of %s: whether it was stored is unknown, so they are left for reconciliation", ruleset_id)
            raise
        if stored is not None:
            return stored  # stored after all, despite the error
        self.compilation.documents.discard(persisted.created, ruleset_id, "version_not_stored")
        raise ApiError(503, "database_unavailable", "the ruleset version could not be stored: try again")

    def _compile(self, request: RulesetCreate) -> tuple[RuleSet, dict]:
        documents = [document.model_dump() for document in request.compile.documents]
        extraction = self.compilation.extract(documents, request.compile.recording_set)
        if extraction.candidate is None:
            raise ApiError(422, "compilation_failed", "the compiler built no valid candidate ruleset from these documents", {"blockers": list(extraction.blockers)})
        read = {document.filename: document.sha256 for document in extraction.documents}
        return extraction.candidate.ruleset, {
            "recording_set": request.compile.recording_set,
            "reading_kind": extraction.reading_kind,
            "extraction_sha256": extraction.extraction_sha256,
            "output_sha256": extraction.output_sha256,
            "ready_for_approval": extraction.ready_for_approval,
            "blockers": list(extraction.blockers),
            "comparisons_needing_review": sum(comparison.review_required for comparison in extraction.comparisons),
            "documents": [{**document, "sha256": read[document["filename"]]} for document in documents],
            # Every comparison, which review decisions name by comparison_id: kept as the compiler gave them.
            "comparisons_json": json.dumps([comparison.model_dump(mode="json") for comparison in extraction.comparisons]),
        }

    def get(self, ruleset_id: str) -> dict:
        record = self.repository.get(ruleset_id)
        if record is None:
            raise ApiError(404, "ruleset_not_found", f"no ruleset {ruleset_id}")
        verified_artifact(record)
        return record

    def changes(self, ruleset_id: str) -> dict:
        """What this version does differently from the version published before it (or, unpublished, the one published
        now): every rule field that differs, before and after. Read only; both versions stay as they are."""
        record = self.get(ruleset_id)
        earlier = self.repository.published_before(ruleset_id, record.get("published_at") if record["status"] == "published" else None)
        if earlier is None:
            return {"against": None, "rule_changes": []}
        old, new = json.loads(verified_artifact(earlier)), json.loads(verified_artifact(record))
        ids = {rule["rule_id"] for rule in old["rules"]} | {rule["rule_id"] for rule in new["rules"]}
        against = {"ruleset_id": earlier["_id"], "name": earlier["name"], "version": earlier["version"], "published_at": earlier.get("published_at")}
        return {"against": against, "rule_changes": rule_changes(old, new, ids, BEHAVIOUR_FIELDS)}

    def page(self, name: str | None, limit: int, offset: int, include_superseded: bool = False) -> tuple[list[dict], int]:
        return self.repository.page(name, limit, offset, include_superseded)

    def source(self, ruleset_id: str, comparison) -> dict:
        """One comparison's clause, verified in the exact text this version was compiled from, read from storage."""
        from expense_api.services.source_locations import locate

        record = self.get(ruleset_id)
        entry = next((item for item in record["compilation"]["documents"] if item["filename"] == comparison.policy_doc), None)
        if entry is None:
            raise ApiError(404, "document_not_found", f"this version was compiled from no document {comparison.policy_doc}")
        [text] = self.compilation.documents.resolve([entry])  # checked against the hash the compiler read
        found = locate(text["content"], entry["sha256"], entry["sha256"], comparison)
        document = {"document_id": entry["document_id"], "revision": entry.get("revision"), "filename": entry["filename"], "content_sha256": entry["sha256"], "content": text["content"]}
        return {"comparison_id": comparison.comparison_id, "document": document, "section": (comparison.location or {}).get("section"), **found}

    def _core_settings(self) -> ValidatorSettings:
        return ValidatorSettings.from_config(self.settings.core_config, mode="deterministic")

    def _runtime_problems(self, text: str) -> list[dict]:
        """Build the validator a run would build, which checks the rules against the field catalog."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ruleset.json"
            path.write_text(text)
            try:
                ExpenseValidator(self._core_settings().model_copy(update={"ruleset_path": path}))
            except ConfigurationError as error:
                return [{"stage": "runtime", "msg": str(error)}]
        return []

    def _evidence_problems(self, ruleset: RuleSet, documents: list[dict] | None = None) -> list[dict]:
        """Every rule that can raise an exception must quote its clause, or a run citing it would fail."""
        evidence = PolicyEvidence(self._core_settings().policy_docs_path)
        texts = None if documents is None else {item["filename"]: item["content"] for item in documents}
        problems = []
        for rule in ruleset.rules:
            if isinstance(rule, CheckRule) and rule.status == "active" and rule.outcome == "exception":
                try:
                    if texts is None:
                        evidence(rule.policy_doc, rule.rule_id, rule.source_quote)
                    elif rule.policy_doc not in texts:
                        raise EvidenceError(f"no stored policy source for {rule.policy_doc}")
                    else:
                        policy_evidence(texts[rule.policy_doc], rule.policy_doc, rule.rule_id, rule.source_quote)
                except EvidenceError as error:
                    problems.append({"stage": "evidence", "rule_id": rule.rule_id, "msg": str(error)})
        return problems


def _parse(artifact: dict) -> RuleSet:
    try:
        return RuleSet.model_validate(artifact)
    except ValidationError as error:
        details = [{"stage": "schema", "loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in error.errors(include_url=False)]
        raise ApiError(422, "invalid_ruleset", "the artifact does not match the core's ruleset schema", details) from None


EDITED_FIELDS = ("status", "title", "outcome", "severity", "applies_when", "select", "waived_by_ref", "params")
# Everything that decides what a rule does, for comparing two versions rather than one edit.
BEHAVIOUR_FIELDS = (*EDITED_FIELDS, "kind", "scope", "helper", "severity_cases", "replaces", "modifies", "requires_ref", "directive")
RULE_ID_IN = re.compile(r"\b([A-Z][A-Z0-9]*(?:[-.][A-Z0-9]+)+)\b")


def merge_overrides(previous: list[dict], new: list[dict]) -> list[dict]:
    """Earlier overrides with these applied over them, rule by rule: a field given again replaces the earlier one,
    a param replaces that param. Each rule keeps one override, with the latest reviewer and rationale."""
    merged = {item["rule_id"]: {**item, "set": dict(item["set"])} for item in previous}
    for item in new:
        current = merged.get(item["rule_id"])
        if current is None:
            merged[item["rule_id"]] = {**item, "set": dict(item["set"])}
            continue
        changes = dict(current["set"])
        for field, value in item["set"].items():
            changes[field] = {**(changes.get("params") or {}), **value} if field == "params" and isinstance(value, dict) else value
        merged[item["rule_id"]] = {**item, "set": changes, **({"add": True} if current.get("add") else {})}  # an added rule stays one
    return sorted(merged.values(), key=lambda item: item["rule_id"])


def rule_changes(before: dict, after: dict, rule_ids: set[str], fields: tuple[str, ...] = EDITED_FIELDS) -> list[dict]:
    """Each field of these rules that differs between two artifacts, before and after. A rule only one artifact has
    is one change of its field "rule", from or to "present"."""
    old = {rule["rule_id"]: rule for rule in before["rules"]}
    new = {rule["rule_id"]: rule for rule in after["rules"]}
    changes = []
    for rule_id in sorted(rule_ids):
        if (rule_id in old) != (rule_id in new):
            changes.append({"rule_id": rule_id, "field": "rule", "before": "present" if rule_id in old else None, "after": "present" if rule_id in new else None})
            continue
        for field in fields:
            if field == "params":
                names = sorted(set((old.get(rule_id) or {}).get("params") or {}) | set((new.get(rule_id) or {}).get("params") or {}))
                for name in names:
                    was, now_ = ((old.get(rule_id) or {}).get("params") or {}).get(name), ((new.get(rule_id) or {}).get("params") or {}).get(name)
                    if was != now_:
                        changes.append({"rule_id": rule_id, "field": f"params.{name}", "before": was, "after": now_})
                continue
            was, now_ = (old.get(rule_id) or {}).get(field), (new.get(rule_id) or {}).get(field)
            if was != now_:
                changes.append({"rule_id": rule_id, "field": field, "before": was, "after": now_})
    return changes


def _rule_of(message: str) -> str | None:
    """The rule a compiler message is about, when it names one."""
    match = RULE_ID_IN.search(message.split(":", 2)[-1]) or RULE_ID_IN.search(message)
    return match.group(1) if match else None
