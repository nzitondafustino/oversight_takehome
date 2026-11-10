"""Compile, review, approve and verify through the core's PolicyCompiler, in replay only. No approval logic lives here.

The compiler's approval binds the exact agreement or review output and the candidate ruleset's hash,
and its publish rebuilds the candidate from the documents, recordings and decisions before it writes anything.
This module only runs those steps and checks that what they would publish is byte for byte the stored version.
"""

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from expense_validator import (
    Approval,
    ApprovalRefused,
    CompilerSettings,
    ConfigurationError,
    ExtractionResult,
    InvalidInputError,
    PolicyCompiler,
    RecordedWith,
    ReplayAnswerMissing,
    ReviewResult,
)
from pydantic import ValidationError

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.services.compilation_documents import CompilationDocuments
from expense_api.services.rulesets import sha256


@dataclass(frozen=True)
class Rebuilt:
    """A stored version's compilation, run again: the same extraction, and the same review if one was recorded."""

    compiler: PolicyCompiler
    documents: list[dict]
    extraction: ExtractionResult
    review: ReviewResult | None
    decisions: list[dict] | None
    policy: str | None
    rule_overrides: list[dict] = ()  # the version's reviewed logic changes, given to the compiler at every step


class Compilation:
    def __init__(self, settings: Settings, documents: CompilationDocuments | None = None):
        # documents resolves a stored version's document references back to their exact text, from S3.
        self.settings, self.documents = settings, documents

    @staticmethod
    def with_logic(compiler: PolicyCompiler, logic_snapshot: str | None) -> PolicyCompiler:
        """Use a version's immutable scoped logic through the public compiler settings."""
        return compiler if logic_snapshot is None else PolicyCompiler(compiler.settings.model_copy(update={"rule_logic_text": logic_snapshot}))

    def extract(self, documents: list[dict], recording_set: str, recorded_with: dict | None = None) -> ExtractionResult:
        return self.run_extract(self.replay_compiler(recording_set, recorded_with), documents, recording_set)

    def run_extract(self, compiler: PolicyCompiler, documents: list[dict], recording_set: str, rule_overrides: list[dict] | None = None) -> ExtractionResult:
        try:
            return compiler.extract(documents, rule_overrides=rule_overrides or ())
        except InvalidInputError as error:
            if str(error).startswith("invalid rule override"):  # a reviewed rule change the compiler refuses before building
                raise ApiError(422, "invalid_rule_change", str(error)) from None
            raise ApiError(422, "invalid_documents", str(error)) from None
        except ReplayAnswerMissing as error:
            raise ApiError(409, "replay_recording_missing", f"{len(error.missing)} documents have no recorded reading in {recording_set!r}, and replay never calls a model", {"missing_documents": list(error.missing)}) from None
        except ConfigurationError as error:  # for example readings recorded only with other settings, which replay never uses
            raise ApiError(409, "recordings_unusable", str(error)) from None

    def review(self, record: dict, decisions: list[dict], policy: str) -> ReviewResult:
        rebuilt = self.rebuild(record, with_review=False)
        try:
            return rebuilt.compiler.review(rebuilt.extraction, decisions, policy=policy, documents=rebuilt.documents)
        except InvalidInputError as error:
            raise ApiError(422, "invalid_review_decisions", str(error)) from None

    def rebuild(self, record: dict, with_review: bool = True) -> Rebuilt:
        """Run the stored compilation again and refuse it unless it reproduces the stored hashes exactly."""
        stored = record["compilation"]
        if self.documents is None:
            raise ApiError(500, "compiler_configuration_error", "this compilation cannot read the documents a version was compiled from")
        documents = self.documents.resolve(stored["documents"])  # the exact texts, checked: the compiler never sees S3
        compiler = self.with_logic(self.replay_compiler(stored["recording_set"], stored.get("recorded_with")), stored.get("logic_snapshot"))
        overrides = stored.get("rule_overrides") or []  # reviewed logic changes, compiled in again exactly
        extraction = self.run_extract(compiler, documents, stored["recording_set"], overrides)
        candidate = None if extraction.candidate is None else extraction.candidate.ruleset_sha256
        if extraction.extraction_sha256 != stored["extraction_sha256"] or candidate != record["artifact_sha256"]:
            raise ApiError(409, "compilation_changed", "the compiler no longer reproduces this version from its documents and recordings: create a new version")
        review = decisions = policy = None
        if with_review and record.get("review") and record["review"].get("decisions") is not None:
            decisions, policy = record["review"]["decisions"], record["review"]["policy"]
            review = compiler.review(extraction, decisions, policy=policy, documents=documents)
            if review.review_sha256 != record["review"]["review_sha256"]:
                raise ApiError(409, "compilation_changed", "the recorded review no longer reproduces: create a new version")
        return Rebuilt(compiler, documents, extraction, review, decisions, policy, overrides)

    def approve(self, rebuilt: Rebuilt, approved_by: str, note: str) -> Approval:
        """The core's own approval of the exact review, or of the extraction when it needed none."""
        approved_at = datetime.now(UTC).isoformat(timespec="seconds")  # the server's time, never the caller's
        try:
            return rebuilt.compiler.approve(rebuilt.review or rebuilt.extraction, approved_by=approved_by, approved_at=approved_at, note=note)
        except ApprovalRefused as error:
            raise ApiError(422, "approval_refused", str(error)) from None

    def verify(self, rebuilt: Rebuilt, record: dict, approval: Approval, mode: str) -> None:
        """Run the core's publish into a scratch folder: it must publish, and publish exactly this version."""
        review = {} if rebuilt.decisions is None else {"decisions": rebuilt.decisions, "review_policy": rebuilt.policy}
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "ruleset.json"
            try:
                result = rebuilt.compiler.publish(rebuilt.documents, approval=approval, out=out, mode=mode, rule_overrides=rebuilt.rule_overrides, **review)
            except InvalidInputError as error:
                raise ApiError(422, "approval_does_not_hold", str(error)) from None
            written = out.read_text() if result.published else None
        if not result.published:
            raise ApiError(422, "approval_does_not_hold", "the compiler's publication safeguards refuse this approval for this version", {"reasons": list(result.reasons)})
        if sha256(written) != record["artifact_sha256"] or result.ruleset_sha256 != record["artifact_sha256"]:
            raise ApiError(422, "approval_does_not_hold", "the compiler would publish a different ruleset than this version")

    def replay_compiler(self, recording_set: str, recorded_with: dict | None = None) -> PolicyCompiler:
        """A compiler that reads one recording set, and with recorded_with only readings recorded live with those settings."""
        chosen = None if recorded_with is None else RecordedWith(**recorded_with)
        try:
            return PolicyCompiler.from_config(self.settings.core_config, mode="replay", recordings_dir=self._recordings(recording_set), recorded_with=chosen)
        except ConfigurationError as error:
            raise ApiError(500, "compiler_configuration_error", f"the policy compiler cannot be configured: {error}") from None

    def live_compiler(self, extraction_model: str, temperature: float | None, timeout_seconds: int | None, reading_id: str | None = None) -> PolicyCompiler:
        """A compiler that calls the model and records each reading in the live recording set, as a model reading.

        With reading_id, it reads every document again rather than replay a recorded reading, and records each under that ID.
        """
        try:
            settings = CompilerSettings.from_config(
                self.settings.core_config, mode="live", recordings_dir=self._recordings(self.settings.compiler_live_recording_set), cache_dir=None,
                extraction_model=extraction_model, temperature=temperature, timeout_seconds=timeout_seconds,
            )
        except ConfigurationError as error:
            raise ApiError(422, "unsupported_configuration", str(error)) from None
        if settings.openrouter_api_key is None:
            raise ApiError(503, "live_extraction_unavailable", "live extraction needs OPENROUTER_API_KEY in the deployment's environment")
        try:
            return PolicyCompiler(settings.model_copy(update={"reading_id": reading_id}))
        except ConfigurationError as error:
            raise ApiError(500, "compiler_configuration_error", f"the policy compiler cannot be configured: {error}") from None

    def _recordings(self, recording_set: str) -> Path:
        path = self.settings.compiler_recording_sets.get(recording_set)
        if path is None:
            raise ApiError(422, "unknown_recording_set", f"no compiler recording set {recording_set!r}", {"available": sorted(self.settings.compiler_recording_sets)})
        return path


def parse_approval(data: dict) -> Approval:
    try:
        return Approval.model_validate(data)
    except ValidationError as error:
        first = error.errors(include_url=False)[0]
        raise ApiError(422, "invalid_approval", f"not an approval record: {'.'.join(map(str, first['loc']))}: {first['msg']}") from None
