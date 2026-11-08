"""Compile in-memory policy documents through evidence and approval gates.

Each compiler snapshots settings, logic, and fields. Calls retain no request
state; publication requires approval for the exact rebuilt candidate.
"""

import dataclasses
import json
import os
import threading
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import ValidationError

from expense_validator.compiler.approval import approve
from expense_validator.compiler.build_rules import Candidate, prepare_candidate_from_texts, publication_blockers
from expense_validator.compiler.extract import extract_document
from expense_validator.compiler.review import build_comparisons, check_extraction, extraction_identity, review_extraction, review_output
from expense_validator.compiler.llm_extract import (
    PROMPT,
    Complete,
    cache_key,
    cached,
    configured_path,
    document_sha256,
    model_recording,
    openrouter,
    recorded,
    recorded_settings,
    recording_identity,
    recording_kind,
    replay_configured,
)
from expense_validator.compiler.validate import read_fields
from expense_validator.config import LlmConfig
from expense_validator.exceptions import ApprovalRefused, ConfigurationError, InvalidInputError, ReplayAnswerMissing
from expense_validator.models.compiler import (
    Approval,
    CandidateRuleset,
    DocumentReading,
    ExtractionResult,
    FieldComparison,
    PolicyDocumentInput,
    PublicationResult,
    PublishMode,
    Resolution,
    ReviewDecision,
    ReviewItem,
    ReviewPolicy,
    ReviewResult,
)
from expense_validator.models.settings import (
    CONFIG_CACHE,
    REPLAY_TAKES_NO_GENERATION,
    CompilerMode,
    CompilerSettings,
    RecordedWith,
    _ConfigCache,
    check_model_ids,
    generation_overrides,
)

Documents = Iterable[PolicyDocumentInput | Mapping[str, object]]
Resolutions = Iterable[Resolution | Mapping[str, object]]
Decisions = Iterable[ReviewDecision | Mapping[str, object]]


class PolicyCompiler:
    """Extract, review, approve and publish the policy rules, one set of documents per call."""

    def __init__(self, settings: CompilerSettings, *, completion: Complete | None = None):
        """Create a compiler.

        Args:
            settings: Compiler settings.
            completion: Completion function used instead of OpenRouter, live mode only. Its readings are
                simulation, which never authorize production, and are never cached.

        Raises:
            ConfigurationError: If the settings cannot work.
        """
        self.settings = settings
        self._completion = completion
        self._check()
        try:
            self._logic_text = settings.rule_logic_path.read_text()
            self._fields_text = settings.fields_path.read_text()
            read_fields(self._fields_text)
        except (OSError, ValueError) as error:
            raise ConfigurationError(f"the rule logic or field catalog cannot be loaded: {str(error).splitlines()[0]}") from None

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        *,
        mode: CompilerMode,
        recordings_dir: Path | None = None,
        cache_dir: "Path | None | _ConfigCache" = CONFIG_CACHE,
        extraction_model: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
        recorded_with: RecordedWith | None = None,
        completion: Complete | None = None,
    ) -> "PolicyCompiler":
        """Create a compiler from a config.yaml.

        Args:
            config_path: Path to config.yaml.
            mode: replay or live.
            recordings_dir: Recorded readings: read in replay mode, and written in live mode when given.
            cache_dir: Live answer cache; the config's llm_cache by default, None for no cache.
            extraction_model: OpenRouter model ID replacing the config's llm.model.
            temperature: Replaces the config's llm.temperature, from 0 to 2.
            timeout_seconds: Replaces the config's llm.timeout_seconds.
            recorded_with: Replay only the recordings made with these settings.
            completion: Completion function used instead of OpenRouter, live mode only.
        """
        settings = CompilerSettings.from_config(
            config_path, mode=mode, recordings_dir=recordings_dir, cache_dir=cache_dir, extraction_model=extraction_model, temperature=temperature, timeout_seconds=timeout_seconds,
            recorded_with=recorded_with,
        )
        return cls(settings, completion=completion)

    def _check(self) -> None:
        settings = self.settings
        for name in ("rule_logic_path", "fields_path"):
            if not getattr(settings, name).is_file():
                raise ConfigurationError(f"{name}: no file at {getattr(settings, name)}")
        if settings.mode == "live" and settings.recorded_with is not None:
            raise ConfigurationError("recorded_with chooses recordings to replay, so live mode takes none")
        if settings.mode == "replay":
            if self._completion is not None:
                raise ConfigurationError("replay mode calls no model, so it takes no completion")
            if settings.recordings_dir is None or not settings.recordings_dir.is_dir():
                raise ConfigurationError("replay mode needs recordings_dir: a folder of recorded readings")
            if settings.extraction_model is not None:
                raise ConfigurationError(REPLAY_TAKES_NO_GENERATION)
            return
        check_model_ids(extraction_model=settings.extraction_model)
        if not settings.extraction_model:
            raise ConfigurationError("live mode needs extraction_model: set llm.model in config.yaml or pass one")
        if self._completion is None and settings.openrouter_api_key is None:
            raise ConfigurationError("live mode needs OPENROUTER_API_KEY, or a completion passed in")
        if settings.recordings_dir is not None:
            if self._completion is not None:
                raise ConfigurationError("an injected completion's answers are not a model's, so live mode records none of them")
            stored = sorted(settings.recordings_dir.glob("*.json")) if settings.recordings_dir.is_dir() else []
            try:
                kinds = {json.loads(path.read_text()).get("kind") for path in stored}
            except (OSError, ValueError, AttributeError):
                raise ConfigurationError("recordings_dir holds a file that is not a recording") from None
            if kinds - {"model"}:
                raise ConfigurationError("recordings_dir holds recordings that are not model readings: record live answers in a folder of their own")

    def extract(
        self,
        documents: Documents,
        *,
        resolutions: Resolutions = (),
        extraction_model: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
    ) -> ExtractionResult:
        """Extract and validate policy documents without publication.

        Resolve returned review items before approval.

        Args:
            documents: Policy documents, as models or mappings.
            resolutions: Reviewer resolutions, as models or mappings.
            extraction_model: OpenRouter model ID for this request only, live mode only.
            temperature: For this request only, from 0 to 2, live mode only.
            timeout_seconds: For this request only, live mode only.

        Raises:
            InvalidInputError: If an argument, document or resolution is invalid.
            ConfigurationError: If an override is invalid or cannot apply, or the recordings are inconsistent.
            ReplayAnswerMissing: If replay has no recording for a document; .missing lists the file names.
        """
        overrides = generation_overrides(extraction_model=extraction_model, temperature=temperature, timeout_seconds=timeout_seconds)
        texts, ids, candidate, kind, generation = self._prepare(documents, resolutions, overrides)
        return _extraction_result(self.settings.mode, texts, ids, candidate, kind, generation)

    def review(self, extraction: ExtractionResult, decisions: Decisions, *, policy: ReviewPolicy, documents: Documents) -> ReviewResult:
        """Validate reviewer decisions without changing the extraction.

        Invalid decisions remain issues and block approval.

        Args:
            extraction: The result of extract, made without resolutions.
            decisions: Review decisions, as models or mappings.
            policy: exceptions_only needs a decision for every field not agreed; all_fields for every field.
            documents: The documents the extraction read, for the source checks. They must be the same.

        Raises:
            InvalidInputError: If an argument is invalid, the documents differ, or the extraction was altered.
        """
        if policy not in ("exceptions_only", "all_fields"):
            raise InvalidInputError("policy must be exceptions_only or all_fields")
        if not isinstance(extraction, ExtractionResult):
            raise InvalidInputError("extraction must be an ExtractionResult")
        summary = extraction.agreement.get("summary", {})
        if summary.get("rules_resolved") or summary.get("resolution_errors"):
            raise InvalidInputError("this extraction applied resolutions: review an extraction made without them, as decisions replace them")
        check_extraction(extraction)
        texts, _ = _admit_documents(documents)
        read = {d.filename: d.sha256 for d in extraction.documents}
        if {name: document_sha256(text) for name, text in texts.items()} != read:
            raise InvalidInputError("documents must be exactly the ones this extraction read")
        return review_extraction(extraction, _admit_decisions(decisions), policy, texts)

    def approve(self, extraction: ExtractionResult | ReviewResult, *, approved_by: str, approved_at: str, note: str = "") -> Approval:
        """Approve an exact extraction or review.

        The caller is responsible for saving the approval.

        Args:
            extraction: The extraction, or the review of one, being approved.
            approved_by: Who approves, as the caller has authenticated them.
            approved_at: ISO 8601 time, from the caller.
            note: Free text kept with the record.

        Raises:
            ApprovalRefused: If something blocks approval or the record is invalid.
        """
        if isinstance(extraction, ReviewResult):
            return _approve_review(extraction, approved_by, approved_at, note)
        ruleset_sha256 = None if extraction.candidate is None else extraction.candidate.ruleset_sha256
        try:
            return approve(extraction.agreement, ruleset_sha256, approved_by, approved_at, note)
        except (ValueError, KeyError) as error:
            raise ApprovalRefused(str(error).splitlines()[0]) from None

    def publish(
        self,
        documents: Documents,
        *,
        approval: Approval | Mapping[str, object] | None,
        out: Path,
        mode: PublishMode,
        resolutions: Resolutions = (),
        decisions: Decisions | None = None,
        review_policy: ReviewPolicy | None = None,
        extraction_model: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
        replace: bool = False,
    ) -> PublicationResult:
        """Rebuild and publish a candidate only when its approval still holds.

        Recheck document and candidate hashes. Simulation never writes production.

        Args:
            documents: The approved source documents.
            approval: Approval bound to the candidate or reviewed interpretation.
            out: Output path whose parent directory exists.
            mode: Production requires model readings and approval; simulation cannot
                write the production ruleset.
            resolutions: Approved resolutions; mutually exclusive with decisions.
            decisions: Approved review decisions, rechecked against these documents.
            review_policy: Approved review policy; required with decisions.
            extraction_model: Live extraction model override, as in extract.
            temperature: Live temperature override, as in extract.
            timeout_seconds: Live timeout override, as in extract.
            replace: Whether to replace an existing output; disabled by default.

        Returns:
            Publication status and blockers, including approval or evidence failures.

        Raises:
            InvalidInputError: If arguments, documents, resolutions, or approval are invalid.
            ConfigurationError: If overrides or recordings are incompatible.
            ReplayAnswerMissing: If replay lacks a document recording.
        """
        overrides = generation_overrides(extraction_model=extraction_model, temperature=temperature, timeout_seconds=timeout_seconds)
        if mode not in ("production", "simulation"):
            raise InvalidInputError("mode must be production or simulation")
        if not isinstance(replace, bool):
            raise InvalidInputError("replace must be True or False")
        out = Path(out)
        if not out.parent.is_dir():
            raise InvalidInputError(f"out: no folder at {out.parent}")
        if approval is not None and not isinstance(approval, Approval):
            try:
                approval = Approval.model_validate(approval)
            except ValidationError as error:
                raise InvalidInputError(f"invalid approval: {_first_error(error)}") from None
        if decisions is not None and (tuple(resolutions) or review_policy is None):
            raise InvalidInputError("publishing a review takes its decisions and review_policy, and no resolutions")
        texts, ids, candidate, kind, generation = self._prepare(documents, resolutions, overrides)
        if decisions is not None:
            extraction = _extraction_result(self.settings.mode, texts, ids, candidate, kind, generation)
            reviewed = self.review(extraction, decisions, policy=review_policy, documents=documents)
            blocking = [issue.message for issue in reviewed.issues if issue.level == "error"]
            blocking += [f"review incomplete: {len(reviewed.remaining)} fields still need a decision"] if reviewed.remaining else []
            # Bind approval to this exact review instead of the agreement output.
            candidate = dataclasses.replace(candidate, reasons=blocking, output=review_output(reviewed))
        reasons = publication_blockers(candidate, kind, approval, mode, out, self.settings.ruleset_path)
        if not reasons:
            reasons = _write(out, candidate.ruleset.to_json(), replace)
        return PublicationResult(
            published=not reasons,
            mode=mode,
            out=out,
            reading_kind=kind,
            output_sha256=candidate.output["output_sha256"],
            ruleset_sha256=candidate.ruleset_sha256,
            reasons=tuple(reasons),
            **generation,
        )

    def _prepare(self, documents, resolutions, overrides) -> tuple[dict[str, str], dict[str, str], Candidate, str, dict]:
        texts, ids = _admit_documents(documents)
        admitted = _admit_resolutions(resolutions)
        complete, kind, generation = self._reader(texts, overrides)
        candidate = prepare_candidate_from_texts(texts, self._logic_text, self._fields_text, complete, admitted, kind)
        return texts, ids, candidate, kind, generation

    def _reader(self, texts: dict[str, str], overrides: dict[str, object]) -> tuple[Complete, str, dict]:
        """Return this request's completion function, the kind of reading it gives, and the settings it used."""
        settings = self.settings
        if settings.mode == "replay":
            if overrides:
                raise ConfigurationError(REPLAY_TAKES_NO_GENERATION)
            if settings.recorded_with is not None:
                return self._replay_configured(texts)
            missing = [name for name, text in texts.items() if not (settings.recordings_dir / f"{document_sha256(text)}.json").is_file()]
            if missing:
                raise ReplayAnswerMissing(missing)
            try:
                kind = recording_kind(settings.recordings_dir, list(texts.values()))
            except ValueError as error:
                raise ConfigurationError(str(error).splitlines()[0]) from None
            return recorded(settings.recordings_dir), kind, _generation(None, None, None, ())
        if self._completion is not None:
            if overrides:
                raise ConfigurationError(f"an injected completion ignores {', '.join(sorted(overrides))}")
            # Keep simulated answers out of the live model cache.
            return self._completion, "simulation", _generation(settings.extraction_model, None, None, ())
        effective = CompilerSettings.model_validate({**settings.model_dump(), **overrides})  # this request's own snapshot
        llm = LlmConfig(model=effective.extraction_model, url=effective.llm_url, temperature=effective.temperature, timeout_seconds=effective.timeout_seconds)
        complete = _request_failures_as_reading_errors(openrouter(llm, effective.openrouter_api_key.get_secret_value()))
        if effective.cache_dir is not None:
            complete = cached(complete, cache_key(effective.extraction_model, effective.temperature), effective.cache_dir)
        if effective.recordings_dir is not None:
            names = {document_sha256(text): name for name, text in texts.items()}
            origin = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "timeout_seconds": effective.timeout_seconds}
            generation = _recording_generation(effective.llm_url, effective.extraction_model, effective.temperature)
            complete = model_recording(complete, effective.recordings_dir, generation, lambda text: names[document_sha256(text)], origin)
        return complete, "model", _generation(effective.extraction_model, effective.temperature, effective.timeout_seconds, tuple(sorted(overrides)))


    def _replay_configured(self, texts: dict[str, str]) -> tuple[Complete, str, dict]:
        """Replay recordings made with exactly settings.recorded_with, or fail closed."""
        settings, chosen = self.settings, self.settings.recorded_with
        generation = _recording_generation(settings.llm_url, chosen.extraction_model, chosen.temperature)
        missing = []
        for name, text in texts.items():
            if configured_path(settings.recordings_dir, recording_identity(text, PROMPT, **generation)).is_file():
                continue
            others = recorded_settings(settings.recordings_dir, text)
            if others:
                raise ConfigurationError(f"{name}: recorded only with other settings, which replay never uses: {others}")
            if (settings.recordings_dir / f"{document_sha256(text)}.json").is_file():
                raise ConfigurationError(f"{name}: only a recording by document hash exists, which records no generation settings: replay it without recorded_with")
            missing.append(name)
        if missing:
            raise ReplayAnswerMissing(missing)
        return replay_configured(settings.recordings_dir, generation), "model", _generation(chosen.extraction_model, chosen.temperature, None, ())


def _recording_generation(url: str, model: str, temperature: float) -> dict:
    return {"url": url, "model": model, "temperature": temperature}


def _generation(model: str | None, temperature: float | None, timeout_seconds: int | None, overrides: tuple[str, ...]) -> dict:
    return {"extraction_model": model, "temperature": temperature, "timeout_seconds": timeout_seconds, "overrides": overrides}


def _approve_review(review: ReviewResult, approved_by: str, approved_at: str, note: str) -> Approval:
    """Approve one complete, valid review: the approval holds the review itself, so its hash binds every decision."""
    if not isinstance(review, ReviewResult):
        raise ApprovalRefused("only a ReviewResult can be approved as a review")
    output = review_output(review)
    if output["output_sha256"] != review.review_sha256:
        raise ApprovalRefused("the review does not match its review_sha256: it has been altered")
    if not review.ready_for_approval:
        errors = [issue for issue in review.issues if issue.level == "error"]
        raise ApprovalRefused(f"not ready for approval: {len(review.remaining)} fields need a decision and {len(errors)} issues block it")
    try:
        return Approval(
            approved_by=approved_by,
            approved_at=approved_at,
            note=note,
            reading_kind=review.reading_kind,
            output_sha256=review.review_sha256,
            ruleset_sha256=review.candidate_ruleset_sha256,
            output=output,
        )
    except ValidationError as error:
        raise ApprovalRefused(f"invalid approval: {_first_error(error)}") from None


def _admit_decisions(decisions: Decisions) -> list[ReviewDecision]:
    if isinstance(decisions, (str, bytes, Mapping)) or not isinstance(decisions, Iterable):
        raise InvalidInputError("decisions must be a list of review decisions")
    admitted = []
    for index, decision in enumerate(decisions):
        # Preserve omitted values rather than treating them as explicit null overrides.
        data = decision.model_dump(exclude_unset=True) if isinstance(decision, ReviewDecision) else decision
        try:
            admitted.append(ReviewDecision.model_validate(data))
        except ValidationError as error:
            raise InvalidInputError(f"decisions.{index}: {_first_error(error)}") from None
    return admitted


def _first_error(error: ValidationError) -> str:
    # Exclude input values from validation messages.
    first = error.errors()[0]
    return f"{'.'.join(str(part) for part in first['loc']) or 'value'}: {first['msg']}"


def _admit_documents(documents: Documents) -> tuple[dict[str, str], dict[str, str]]:
    if isinstance(documents, (str, bytes, Mapping)) or not isinstance(documents, Iterable):
        raise InvalidInputError("documents must be a list of policy documents")
    admitted = []
    for index, document in enumerate(documents):
        data = document.model_dump() if isinstance(document, PolicyDocumentInput) else document
        try:
            admitted.append(PolicyDocumentInput.model_validate(data))
        except ValidationError as error:
            raise InvalidInputError(f"documents.{index}: {_first_error(error)}") from None
    if not admitted:
        raise InvalidInputError("documents must hold at least one policy document")
    for name in ("filename", "document_id"):
        values = [getattr(document, name) for document in admitted]
        repeated = sorted({value for value in values if values.count(value) > 1})
        if repeated:
            raise InvalidInputError(f"{name}s given more than once: {repeated}")
    for document in admitted:
        try:
            extract_document(document.content, document.filename)
        except ValueError as error:
            raise InvalidInputError(_without_quoted_line(str(error))) from None
    return {d.filename: d.content for d in admitted}, {d.filename: d.document_id for d in admitted}


def _admit_resolutions(resolutions: Resolutions) -> list[Resolution]:
    if isinstance(resolutions, (str, bytes, Mapping)) or not isinstance(resolutions, Iterable):
        raise InvalidInputError("resolutions must be a list of resolutions")
    admitted = []
    for index, resolution in enumerate(resolutions):
        data = resolution.model_dump() if isinstance(resolution, Resolution) else resolution
        try:
            admitted.append(Resolution.model_validate(data))
        except ValidationError as error:
            raise InvalidInputError(f"resolutions.{index}: {_first_error(error)}") from None
    return admitted


def _without_quoted_line(message: str) -> str:
    """Drop the document line a malformed-heading error quotes, so the message holds no policy text."""
    head, marker, _ = message.splitlines()[0].partition(": malformed rule heading")
    return head + marker


def _request_failures_as_reading_errors(complete: Complete) -> Complete:
    """Turn a failed request into a ValueError, which leaves that document with a reading error and no answer."""

    def call(prompt: str, text: str) -> str:
        try:
            return complete(prompt, text)
        except (OSError, KeyError, IndexError, TypeError) as error:
            raise ValueError(f"the model request failed: {type(error).__name__}") from None

    return call


def _write(out: Path, text: str, replace: bool) -> list[str]:
    """Write the ruleset whole, and never over an existing file unless replace. Returns why it was not written."""
    if not replace:
        try:
            with out.open("x") as file:
                file.write(text)
        except FileExistsError:
            return [f"{out} exists, and publish replaces a ruleset only with replace=True"]
        return []
    partial = out.with_name(f".{out.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    partial.write_text(text)
    os.replace(partial, out)
    return []


def _extraction_result(mode, texts, ids, candidate: Candidate, kind, generation: dict) -> ExtractionResult:
    output = candidate.output
    documents = tuple(
        DocumentReading(document_id=ids[entry["policy_doc"]], filename=entry["policy_doc"], sha256=entry["document_sha256"], reading_error=entry.get("reading_error"))
        for entry in output["documents"]
    )
    review_items = tuple(
        ReviewItem(document_id=ids[entry["policy_doc"]], policy_doc=entry["policy_doc"], rule_id=rule["rule_id"], clause_hash=rule["clause_hash"], **item)
        for entry in output["documents"]
        for rule in entry["rules"]
        for item in rule["disagreed"]
    )
    built = None if candidate.ruleset is None else CandidateRuleset(ruleset=candidate.ruleset, ruleset_sha256=candidate.ruleset_sha256)
    return ExtractionResult(
        mode=mode,
        reading_kind=kind,
        **generation,
        prompt_sha256=document_sha256(PROMPT),
        documents=documents,
        clauses=candidate.clauses,
        review_items=review_items,
        validation=candidate.issues,
        blockers=tuple(candidate.reasons),
        ready_for_approval=not candidate.reasons,
        output_sha256=output["output_sha256"],
        agreement=json.loads(json.dumps(output, default=str)),
        candidate=built,
        **_comparisons(texts, ids, candidate, kind, generation, documents),
    )


def _comparisons(texts, ids, candidate: Candidate, kind, generation: dict, documents) -> dict:
    """Return every field comparison and the extraction identity they all carry."""
    entries = build_comparisons(candidate.report["results"], texts, ids, candidate.clauses)
    read = [{"filename": d.filename, "document_id": d.document_id, "sha256": d.sha256, "reading_error": d.reading_error} for d in documents]
    settings = {"extraction_model": generation["extraction_model"], "temperature": generation["temperature"]}
    core = [{name: entry[name] for name in ("comparison_id", "policy_doc", "rule_id", "field", "status", "regex_value", "llm_value", "clause_hash")} for entry in entries]
    identity = extraction_identity(kind, document_sha256(PROMPT), settings, read, core)
    return {"extraction_sha256": identity, "comparisons": tuple(FieldComparison(**entry, extraction_sha256=identity) for entry in entries)}
