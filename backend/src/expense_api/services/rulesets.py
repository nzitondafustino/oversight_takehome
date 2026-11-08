"""Check and store compiled rulesets with the core's own schema, runtime check and evidence check."""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from expense_validator import ConfigurationError, EvidenceError, ExpenseValidator, ValidatorSettings
from expense_validator.evidence import PolicyEvidence
from expense_validator.policy.schema import CheckRule, RuleSet
from bson import ObjectId
from pydantic import ValidationError

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


def verified_artifact(record: dict) -> str:
    """The record's artifact text, refused when it no longer matches the hash it was stored with."""
    text = record["artifact_json"]
    if sha256(text) != record["artifact_sha256"]:
        raise ApiError(500, "integrity_error", f"ruleset {record['_id']} does not match its stored hash")
    return text


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
        problems = self._runtime_problems(text) + self._evidence_problems(ruleset)
        if problems:
            raise ApiError(422, "invalid_ruleset", "the ruleset cannot run against this deployment's catalog and policy documents", problems)
        if request.based_on_version is not None and self.repository.latest_version(request.name) != request.based_on_version:
            raise ApiError(409, "version_conflict", f"version {request.based_on_version} of {request.name} is not the latest version: start again from the latest")
        source = request.source if "source" in request.model_fields_set or compilation is None else request.source.model_copy(update={"kind": "policy_compiler"})
        created, active = now(), [rule for rule in ruleset.rules if rule.status == "active"]
        return self.repository.create({
            "_id": f"rs_{ObjectId()}",  # increasing within a process, so equal timestamps still list in order
            "name": request.name,
            "status": "draft",
            "based_on_version": request.based_on_version,
            "source": source.model_dump(),
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
            "transitions": [{"from_status": None, "to_status": "draft", "at": created, "actor": None, "content_hash": sha256(text), "approval_reference": None, "reason": None}],
        }, request.based_on_version)

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

    def page(self, name: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        return self.repository.page(name, limit, offset)

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

    def _evidence_problems(self, ruleset: RuleSet) -> list[dict]:
        """Every rule that can raise an exception must quote its clause, or a run citing it would fail."""
        evidence = PolicyEvidence(self._core_settings().policy_docs_path)
        problems = []
        for rule in ruleset.rules:
            if isinstance(rule, CheckRule) and rule.status == "active" and rule.outcome == "exception":
                try:
                    evidence(rule.policy_doc, rule.rule_id, rule.source_quote)
                except EvidenceError as error:
                    problems.append({"stage": "evidence", "rule_id": rule.rule_id, "msg": str(error)})
        return problems


def _parse(artifact: dict) -> RuleSet:
    try:
        return RuleSet.model_validate(artifact)
    except ValidationError as error:
        details = [{"stage": "schema", "loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in error.errors(include_url=False)]
        raise ApiError(422, "invalid_ruleset", "the artifact does not match the core's ruleset schema", details) from None
