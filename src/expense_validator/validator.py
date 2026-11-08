"""Validate receipts with an immutable policy snapshot.

Each call uses independent model wrappers and retains no request state.
"""

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from openrouter import OpenRouter
from pydantic import ValidationError

from expense_validator.classification.backends.registry import BACKENDS, REVIEWERS, REVISERS
from expense_validator.classification.core.common import line_input, load_vocabulary
from expense_validator.classification.core.models import ClassifiedLine, Complete
from expense_validator.compiler.llm_extract import cached
from expense_validator.compiler.validate import read_fields
from expense_validator.config import EARLIER_NAMES, LANGGRAPH, PROMPT_IDENTITY_VERSION, PROMPT_IDENTITY_VERSIONS, LangGraphConfig, canonical_backend
from expense_validator.engine.evaluation import ReceiptEvaluation
from expense_validator.engine.models import CLASSIFIED_FIELDS, Receipt
from expense_validator.engine.policy_engine import PolicyEngine
from expense_validator.exceptions import ClassificationError, ConfigurationError, InvalidInputError, ReplayAnswerMissing
from expense_validator.evidence import PolicyEvidence
from expense_validator.ingestion import IngestionError, IngestionMode, ingest_record
from expense_validator.normalization import LlmTranslator, NoOpTranslator, NormalizedReceipt, TranslationError, Translator, normalize_receipt
from expense_validator.normalization.translation import RETRIES, replay_only
from expense_validator.normalization.translation import openrouter_completion as translation_completion
from expense_validator.policy.schema import RuleSet
from expense_validator.models.settings import CONFIG_CACHE, ValidatorSettings, _ConfigCache, _first_line, check_model_ids
from expense_validator.models.results import (
    BatchResult,
    DispositionReasonResult,
    ExceptionResult,
    LineResult,
    Metadata,
    Mode,
    OpenQuestionResult,
    Provenance,
    ReceiptResult,
    ReviewSignalResult,
    UncertaintyResult,
)

ATTRIBUTES = ("recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry")
RETRIEVER = "none"  # There is no retrieval stage: every active rule is evaluated against every receipt.


# Given a call's purpose and its adapter's name, the OpenRouter client (or one with the same methods) to send it with.
ClientFactory = Callable[[str, str], "OpenRouter"]


class _Request:
    """Per-call model wrappers, missing replay answers and model call count."""

    def __init__(self, settings: ValidatorSettings, vocabulary, classifier_model: Complete | None, translator: Translator | None, client_factory: "ClientFactory | None" = None):
        self.missing: list[str] = []
        self._client_factory = client_factory
        self.calls = 0
        self._openrouter: OpenRouter | None = None  # built on first use, and only in live mode
        replay = settings.mode == "replay"
        # Wrap all backend calls uniformly; only LangGraph routes between backends.
        if settings.classification_backend == LANGGRAPH:
            self.backend = None
            self.chain_missing: list[str] = []  # kept apart: a missing answer a later backend gave in its place is no failure
            self.chain = tuple(self._link(settings, child, vocabulary) for child in settings.langgraph.backends)
            self.reflection = self._reflection(settings, vocabulary) if settings.langgraph.reflection.enabled else None
        else:
            self.backend = BACKENDS[settings.classification_backend]
            if replay:
                ask = replay_only(self.missing, ClassificationError)
            elif classifier_model is not None:
                ask = classifier_model
            else:
                ask = self.backend.connect(self._client(settings), settings, vocabulary)
            key = self.backend.cache_key(settings.classification_model, vocabulary)
            self.complete = self._counted(ask) if settings.cache_dir is None else cached(self._counted(ask), key, settings.cache_dir / self.backend.cache_folder)
        if translator is not None:
            self.translator = translator if isinstance(translator, NoOpTranslator) else _CountedTranslator(translator, self)
        elif not settings.translation_enabled:
            self.translator = NoOpTranslator()
        else:
            translate_with = replay_only(self.missing, TranslationError) if replay else self._live_translation(settings)
            self.translator = LlmTranslator(self._cache(self._counted(translate_with), settings.translation_model, settings))

    def _link(self, settings: ValidatorSettings, child, vocabulary):
        """One backend of the langgraph order: its own settings, model call, cache folder and model key."""
        from expense_validator.classification.orchestrator import Link

        backend = BACKENDS[child.backend]
        complete, own = self._model_call(settings, backend, child.backend, child.model, vocabulary, "classification")
        return Link(child.name, child.backend, child.model, backend, complete, own)

    def _reflection(self, settings: ValidatorSettings, vocabulary):
        """The reviewer, and a reviser for each backend in the order that can take feedback, each with its own calls."""
        from expense_validator.classification.orchestrator import Reflection, ReviewerLink, ReviserLink

        configured = settings.langgraph.reflection
        reviewer = REVIEWERS[configured.reviewer.backend]
        review, _ = self._model_call(settings, reviewer, configured.reviewer.backend, configured.reviewer.model, vocabulary, "review")
        revisers = []
        for child in settings.langgraph.backends:
            reviser = REVISERS.get(child.backend)
            revisers.append(None if reviser is None else ReviserLink(child.name, child.backend, child.model, reviser, self._model_call(settings, reviser, child.backend, child.model, vocabulary, "revision")[0]))
        return Reflection(ReviewerLink(configured.reviewer.backend, configured.reviewer.model, reviewer, review), tuple(revisers), configured.max_retries, configured.max_total_calls_per_line)

    def _model_call(self, settings: ValidatorSettings, capability, backend_name: str, model: str, vocabulary, purpose: str):
        """Build a lazy, counted completion with capability-specific caching and replay.

        purpose (classification, review or revision) and the adapter's name are what a client_factory is given, so
        that a caller can account for each call, such as by metering it, while the adapter's own code sends it.
        """
        own = settings.model_copy(update={"classification_backend": backend_name, "classification_model": model})  # this request's copy
        if settings.mode == "replay":
            ask = replay_only(self.chain_missing, ClassificationError)
        else:
            connected: list[Complete] = []

            def ask(prompt: str, text: str) -> str:  # connects on first use, so an unused backend builds nothing
                if not connected:
                    client = self._client(own) if self._client_factory is None else self._client_factory(purpose, backend_name)
                    connected.append(capability.connect(client, own, vocabulary))
                return connected[0](prompt, text)

        counted = self._counted(ask)
        complete = counted if settings.cache_dir is None else cached(counted, capability.cache_key(model, vocabulary), settings.cache_dir / capability.cache_folder)
        return complete, own

    def _counted(self, complete: Complete) -> Complete:
        def call(prompt: str, text: str) -> str:
            self.calls += 1
            return complete(prompt, text)

        return call

    @staticmethod
    def _cache(complete: Complete, key: str, settings: ValidatorSettings) -> Complete:
        return complete if settings.cache_dir is None else cached(complete, key, settings.cache_dir)

    def _client(self, settings: ValidatorSettings) -> OpenRouter:
        if self._openrouter is None:
            self._openrouter = OpenRouter(api_key=settings.openrouter_api_key.get_secret_value(), timeout_ms=settings.timeout_seconds * 1000, retry_config=RETRIES)
        return self._openrouter

    def _live_translation(self, settings: ValidatorSettings) -> Complete:
        if settings.openrouter_api_key is None:
            raise ConfigurationError("translation is enabled in live mode: pass a translator, or set OPENROUTER_API_KEY")
        return translation_completion(self._client(settings), settings.translation_model, settings.temperature)


class _CountedTranslator:
    """Count each call to an injected translator."""

    def __init__(self, inner: Translator, request: _Request):
        self.inner, self.request = inner, request

    def translate(self, text: str, source_language: str) -> str:
        self.request.calls += 1
        return self.inner.translate(text, source_language)


class ExpenseValidator:
    """Validate a batch of receipts per call against an immutable policy snapshot."""

    def __init__(self, settings: ValidatorSettings, *, classifier_model: Complete | None = None, translator: Translator | None = None, client_factory: "ClientFactory | None" = None):
        """Create a validator.

        Args:
            settings: Validator settings.
            classifier_model: Completion function used instead of OpenRouter, live mode only.
            translator: Translator used instead of OpenRouter, live mode only.
            client_factory: For LangGraph, the OpenRouter client each node, reviewer and reviser sends through, given
                the call's purpose (classification, review or revision) and its adapter's name. Its requests and
                parsing stay the adapters' own; a caller uses it to account for each call, such as by metering it.
                Unset: one shared OpenRouter client.

        Raises:
            ConfigurationError: If the settings cannot work.
        """
        self.settings = settings
        self._classifier_model, self._translator, self._client_factory = classifier_model, translator, client_factory
        self._check(settings)
        try:
            self.vocabulary = load_vocabulary(settings.taxonomy_path, settings.fields_path)
            ruleset_text = settings.ruleset_path.read_text()
            self.engine = PolicyEngine(RuleSet.from_json(ruleset_text), read_fields(settings.fields_path.read_text()))
        except (OSError, ValidationError, ValueError, TypeError, KeyError) as error:
            raise ConfigurationError(f"the policy configuration cannot be loaded: {str(error).splitlines()[0]}") from None
        self.ruleset_sha256 = hashlib.sha256(ruleset_text.encode()).hexdigest()
        self.prompt_sha256 = self._prompt_sha256(settings, PROMPT_IDENTITY_VERSION)

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        *,
        mode: Mode,
        cache_dir: "Path | None | _ConfigCache" = CONFIG_CACHE,
        classification_model: str | None = None,
        translation_model: str | None = None,
        classifier_model: Complete | None = None,
        translator: Translator | None = None,
        classification_backend: str | None = None,
        langgraph_config: "Mapping[str, object] | LangGraphConfig | None" = None,
    ) -> "ExpenseValidator":
        """Create a validator from a config.yaml.

        Args:
            config_path: Path to config.yaml.
            mode: replay, live or deterministic.
            cache_dir: Answer cache; the config's llm_cache by default, None for no cache.
            classification_model: OpenRouter model ID replacing the config's.
            translation_model: OpenRouter model ID replacing the config's.
            classifier_model: Completion function used instead of OpenRouter. Give it its own cache_dir.
            translator: Translator used instead of OpenRouter.
            classification_backend: an adapter from classification.backends.registry, chat_completions or decisions, or
                langgraph, replacing the config's.
            langgraph_config: Changes to the config's classification.langgraph, field by field: a backends list
                replaces the whole list, and a routing field only that field. Only for the langgraph backend.
        """
        settings = ValidatorSettings.from_config(
            config_path,
            mode=mode,
            cache_dir=cache_dir,
            classification_model=classification_model,
            translation_model=translation_model,
            classification_backend=classification_backend,
            langgraph_config=langgraph_config,
        )
        return cls(settings, classifier_model=classifier_model, translator=translator)

    def _check(self, settings: ValidatorSettings) -> None:
        _check_backend_settings(settings, self._classifier_model)
        for name in ("ruleset_path", "fields_path", "taxonomy_path"):
            if not getattr(settings, name).is_file():
                raise ConfigurationError(f"{name}: no file at {getattr(settings, name)}")
        if not settings.policy_docs_path.is_dir():
            raise ConfigurationError(f"policy_docs_path: no folder at {settings.policy_docs_path}")
        injected = self._classifier_model is not None or self._translator is not None
        if settings.mode in ("replay", "deterministic") and injected:
            raise ConfigurationError(f"{settings.mode} mode calls no model, so it takes no classifier_model or translator")
        if settings.mode == "replay" and (settings.cache_dir is None or not settings.cache_dir.is_dir()):
            raise ConfigurationError("replay mode needs cache_dir: a folder of saved answers")
        if settings.mode != "deterministic" and not settings.classification_model and settings.classification_backend != LANGGRAPH:
            raise ConfigurationError("classification_model is needed outside deterministic mode")
        if settings.translation_enabled and settings.mode != "deterministic" and not settings.translation_model:
            raise ConfigurationError("translation is enabled, so translation_model is needed")

    def validate_batch(
        self,
        receipts: Iterable[Receipt | Mapping[str, object]],
        *,
        context_complete: bool = True,
        classification_model: str | None = None,
        translation_model: str | None = None,
        classification_backend: str | None = None,
        langgraph_config: "Mapping[str, object] | LangGraphConfig | None" = None,
    ) -> BatchResult:
        """Validate receipts together.

        Receipts are evaluated in one context, as rules such as TIME-03 read across receipts.

        Args:
            receipts: Receipt objects or raw records.
            context_complete: Whether every receipt that could matter is included, True by default: the caller
                is expected to submit all relevant receipts in one batch. With False, totals spanning receipts
                that a missing receipt could change are neither confirmed nor cleared.
            classification_model: OpenRouter model ID for this request only.
            translation_model: OpenRouter model ID for this request only. Does not enable translation.
            classification_backend: an adapter, chat_completions or decisions, for this request only. Choosing the other backend needs
                a classification_model for it; langgraph takes its backends' models from its own configuration.
            langgraph_config: Changes to this validator's langgraph configuration, for this request only, field by
                field: a backends list replaces the whole list, and a routing field only that field.

        Returns:
            Results for every receipt, in receipt ID order.

        Raises:
            InvalidInputError: If an argument or receipt is invalid.
            ConfigurationError: If a model override is invalid or cannot apply in this mode.
            ReplayAnswerMissing: If replay lacks a needed answer.
            EvidenceError: If an exception's policy evidence cannot be verified.
        """
        if not isinstance(context_complete, bool):
            raise InvalidInputError("context_complete must be True or False")
        overrides = self._check_overrides(classification_model=classification_model, translation_model=translation_model)
        overrides |= self._check_backend(classification_backend, overrides)
        overrides |= self._check_langgraph(langgraph_config, overrides.get("classification_backend", self.settings.classification_backend))
        settings = self.settings.model_copy(update=overrides)  # this call's own copy: the validator is not changed
        _check_backend_settings(settings, self._classifier_model)
        admitted = [self._admit(receipt) for receipt in receipts]
        ids = [receipt.receipt_id for receipt in admitted]
        repeated = sorted({receipt_id for receipt_id in ids if ids.count(receipt_id) > 1})
        if repeated:
            raise InvalidInputError(f"receipt IDs given more than once: {repeated}")
        if settings.mode == "deterministic":
            staged = [(receipt, None, None, None) for receipt in admitted]
            classified_receipts = admitted
        else:
            staged, classified_receipts = self._classify(admitted, settings)
        try:
            run = self.engine.evaluate(classified_receipts, complete=context_complete)
        except ValueError as error:
            raise InvalidInputError(str(error).splitlines()[0]) from None
        evidence = PolicyEvidence(settings.policy_docs_path)
        provenance = Provenance(
            mode=settings.mode,
            context_complete=context_complete,
            receipts_in_context=len(admitted),
            ruleset_sha256=self.ruleset_sha256,
            model_overrides=tuple(sorted(name for name, value in overrides.items() if value is not None)),
            classification_backend=None if settings.mode == "deterministic" else settings.classification_backend,
        )
        results = [self._result(run.result_for(receipt.receipt_id), receipt, normalized, classified, latency, evidence, provenance, settings) for receipt, normalized, classified, latency in staged]
        return BatchResult(mode=settings.mode, context_complete=context_complete, results=tuple(sorted(results, key=lambda result: result.receipt_id)))

    def _check_overrides(self, **models: object) -> dict[str, str]:
        overrides = check_model_ids(**models)
        if overrides and self.settings.mode == "deterministic":
            raise ConfigurationError("deterministic mode runs no model, so a request cannot choose one")
        if "classification_model" in overrides and self._classifier_model is not None:
            raise ConfigurationError("this validator classifies with the classifier_model it was given, so a request cannot choose another model")
        if "translation_model" in overrides and self._translator is not None:
            raise ConfigurationError("this validator translates with the translator it was given, so a request cannot choose another model")
        return overrides

    def _check_backend(self, backend: str | None, overrides: dict) -> dict:
        if backend is None:
            return {}
        backend = canonical_backend(backend)  # an earlier adapter name, such as jev, is the same adapter
        if backend not in BACKENDS and backend != LANGGRAPH:
            raise ConfigurationError(f"classification_backend must be {' or '.join([*BACKENDS, LANGGRAPH])}")
        if self.settings.mode == "deterministic":
            raise ConfigurationError("deterministic mode runs no model, so a request cannot choose a classification backend")
        if backend == LANGGRAPH:
            if "classification_model" in overrides:
                raise ConfigurationError("the langgraph backend takes each backend's model from langgraph_config, so a request cannot give it a classification_model")
            return {"classification_backend": backend, "classification_model": None}  # each of its backends has its own model
        if backend != self.settings.classification_backend and "classification_model" not in overrides:
            raise ConfigurationError(f"choosing the {backend} backend needs a classification_model for it")
        return {"classification_backend": backend}

    def _check_langgraph(self, override: object, backend: str) -> dict:
        if override is None:
            return {}
        if self.settings.mode == "deterministic":
            raise ConfigurationError("deterministic mode runs no model, so a request cannot configure langgraph")
        if backend != LANGGRAPH:
            raise ConfigurationError(f"langgraph_config applies only to the langgraph backend, and this request's is {backend}")
        try:
            return {"langgraph": self.settings.langgraph.merged(override)}  # a new, frozen config: the validator's is not changed
        except ValueError as error:
            raise ConfigurationError(f"langgraph_config is not valid: {_first_line(error)}") from None

    def prompt_sha256_at(self, version: int) -> str | None:
        """The prompt hash under one prompt identity version, such as a stored job's, to check it still holds.

        Only a LangGraph hash names adapters, so only it differs between versions; a direct backend's never does.
        """
        if version not in PROMPT_IDENTITY_VERSIONS:
            raise ConfigurationError(f"prompt identity version must be one of {PROMPT_IDENTITY_VERSIONS}, not {version!r}")
        return self._prompt_sha256(self.settings, version)

    def _prompt_sha256(self, settings: ValidatorSettings, version: int) -> str | None:
        if settings.mode == "deterministic":
            return None
        if settings.classification_backend == LANGGRAPH:
            # Hash all backend prompts, aliases, models, and routing settings. The prompts are the adapters' own; only
            # the names written into the identity follow the version.
            def named(backend: str) -> str:
                return EARLIER_NAMES.get(backend, backend) if version == 1 else backend

            order = [{"name": child.name, "backend": named(child.backend), "model": child.model, "prompt": BACKENDS[child.backend].prompt(self.vocabulary)} for child in settings.langgraph.backends]
            identity = {"backends": order, "routing": settings.langgraph.routing.model_dump()}
            reflection = settings.langgraph.reflection
            if reflection.enabled:  # left out when off, so that an order without reflection keeps its hash
                dumped = reflection.model_dump()
                if dumped.get("reviewer") is not None:
                    dumped["reviewer"] = {**dumped["reviewer"], "backend": named(dumped["reviewer"]["backend"])}
                identity["reflection"] = {
                    **dumped,
                    "reviewer_prompt": REVIEWERS[reflection.reviewer.backend].prompt(self.vocabulary),
                    "revision_prompts": {child.name: REVISERS[child.backend].prompt(self.vocabulary) for child in settings.langgraph.backends if child.backend in REVISERS},
                }
            return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return hashlib.sha256(BACKENDS[settings.classification_backend].prompt(self.vocabulary).encode()).hexdigest()

    def _admit(self, receipt: Receipt | Mapping[str, object]) -> Receipt:
        if isinstance(receipt, Receipt):
            return receipt
        if not isinstance(receipt, Mapping):
            raise InvalidInputError(f"a receipt must be a Receipt or a mapping, not {type(receipt).__name__}")
        try:
            if self.settings.mode == "deterministic":
                return Receipt.model_validate(receipt)
            return ingest_record(receipt, IngestionMode.PRODUCTION).receipt
        except IngestionError as error:
            raise InvalidInputError(str(error)) from None
        except ValidationError as error:
            raise InvalidInputError(f"invalid receipt: {error.error_count()} problems, first at {'.'.join(map(str, error.errors()[0]['loc']))}") from None

    def _classify(self, receipts: list[Receipt], settings: ValidatorSettings):
        request = _Request(settings, self.vocabulary, self._classifier_model, self._translator, self._client_factory)
        staged, classified_receipts = [], []
        for receipt in receipts:
            calls, started = request.calls, time.perf_counter()
            try:
                normalized = normalize_receipt(receipt, request.translator)
            except ValueError as error:
                raise InvalidInputError(str(error).splitlines()[0]) from None
            if request.backend is None:
                from expense_validator.classification.orchestrator import classify_receipt as orchestrate

                lines = orchestrate(normalized, request.chain, settings.langgraph.routing, self.vocabulary, request.reflection)
                # A missing answer fails the request only for a line no later backend answered.
                unanswered = {line_input(normalized, item) for item, line in zip(normalized.lines, lines) if line.error is not None}
                request.missing.extend(text for text in request.chain_missing if text in unanswered and text not in request.missing)
            else:
                lines = request.backend.classify_receipt(normalized, request.complete, settings, self.vocabulary)
            classified = {line.line_id: line for line in lines}
            latency = round((time.perf_counter() - started) * 1000) if request.calls > calls else None
            staged.append((receipt, normalized, classified, latency))
            fields = {line_id: {name: value for name, value in line._asdict().items() if name in CLASSIFIED_FIELDS and value is not None} for line_id, line in classified.items()}
            classified_receipts.append(receipt.with_classifications(fields))
        if request.missing:
            raise ReplayAnswerMissing(request.missing)
        return staged, classified_receipts

    def _result(
        self,
        result: ReceiptEvaluation,
        receipt: Receipt,
        normalized: NormalizedReceipt | None,
        classified: dict[str, ClassifiedLine] | None,
        latency: int | None,
        evidence: Callable,
        provenance: Provenance,
        settings: ValidatorSettings,
    ) -> ReceiptResult:
        review_signals = [ReviewSignalResult(rule_id=group.rule_id, reason=_explanations(group)) for group in result.review_signals]
        if result.uncertainty is not None:
            review_signals.append(ReviewSignalResult(rule_id=result.uncertainty.rule_id, reason=_explanations(result.uncertainty)))
        deterministic = settings.mode == "deterministic"
        return ReceiptResult(
            receipt_id=receipt.receipt_id,
            disposition=result.disposition,
            line_items=tuple(
                _line(item, None if normalized is None else normalized.lines[index], item if classified is None else classified[item.line_id])
                for index, item in enumerate(receipt.line_items)
            ),
            exceptions=tuple(
                ExceptionResult(
                    rule_id=group.rule_id,
                    policy_doc=group.policy_doc,
                    severity=group.severity,
                    line_ids=group.line_ids,
                    evidence=evidence(group.policy_doc, group.rule_id, group.source_quote).text,
                    source_quote=group.source_quote,
                    explanation=_explanations(group),
                )
                for group in result.active_exceptions
            ),
            review_signals=tuple(review_signals),
            decided=result.decided,
            disposition_reasons=tuple(DispositionReasonResult(code=reason.code, basis=reason.basis, rule_ids=reason.rule_ids) for reason in result.reasons),
            open_questions=tuple(OpenQuestionResult(rule_id=question.rule_id, line_ids=question.line_ids, reasons=question.reasons) for question in result.open_questions),
            uncertainty=tuple(
                UncertaintyResult(line_id=item.line_id, field=item.field, state=item.state, reason=item.reason, confidence=None if item.confidence is None else float(item.confidence))
                for item in (result.uncertainty.uncertainty if result.uncertainty else ())
            ),
            metadata=Metadata(
                model=None if deterministic else _model_label(settings),
                translation_model=settings.translation_model if settings.translation_enabled and not deterministic else None,
                retriever=RETRIEVER,
                prompt_sha256=self._prompt_sha256(settings, PROMPT_IDENTITY_VERSION),
                ruleset_version=self.engine.ruleset.ruleset_version,
                replayed=settings.mode == "replay",
                latency_ms=latency,
            ),
            provenance=provenance,
        )


def _explanations(group) -> str:
    return "; ".join(dict.fromkeys(source.explanation for source in group.sources))


def _line(item, normalized_line, classified) -> LineResult:
    attributes = {name: getattr(classified, name) for name in ATTRIBUTES if getattr(classified, name) is not None}
    translated = normalized_line is not None and normalized_line.translation_status == "translated"
    return LineResult(
        line_id=item.line_id,
        description_raw=item.description_raw,
        category=classified.category,
        restricted=classified.restricted,
        confidence=None if classified.confidence is None else float(classified.confidence),
        english_translation=normalized_line.translation if translated else None,
        attributes=attributes or None,
        classification_error=getattr(classified, "error", None),
        classification_details=getattr(classified, "details", None),
    )


def _model_label(settings: ValidatorSettings) -> str | None:
    if settings.classification_backend != LANGGRAPH:
        return settings.classification_model
    return ", ".join(f"{child.name}={child.backend}:{child.model}" for child in settings.langgraph.backends)


def _check_backend_settings(settings: ValidatorSettings, classifier_model: Complete | None) -> None:
    """Refuse a classification backend and model that cannot work together. Only langgraph falls back, and only as configured."""
    if settings.classification_backend == LANGGRAPH:
        _check_langgraph_settings(settings, classifier_model)
        return
    backend = BACKENDS.get(settings.classification_backend)
    if backend is None:
        raise ConfigurationError(f"classification_backend must be {' or '.join([*BACKENDS, LANGGRAPH])}")
    if settings.mode != "deterministic" or settings.classification_model is not None:
        try:
            backend.check_model(settings.classification_model)
        except ValueError as error:
            raise ConfigurationError(str(error)) from None
    if settings.mode == "live" and classifier_model is None and settings.openrouter_api_key is None:
        raise ConfigurationError("live mode needs OPENROUTER_API_KEY, or a classifier_model passed in")


def _check_langgraph_settings(settings: ValidatorSettings, classifier_model: Complete | None) -> None:
    """Refuse a langgraph order that cannot work: every backend registered, never langgraph, and each model its own."""
    if settings.classification_model is not None:
        raise ConfigurationError("the langgraph backend takes each backend's model from its langgraph backends, so it takes no classification_model")
    if classifier_model is not None:
        raise ConfigurationError("the langgraph backend calls each of its backends' own models, so it takes no classifier_model")
    if settings.mode == "deterministic":
        return
    if not settings.langgraph.backends:
        raise ConfigurationError("the langgraph backend needs at least one backend in classification.langgraph.backends")
    for child in settings.langgraph.backends:
        backend = BACKENDS.get(child.backend)
        if backend is None:
            raise ConfigurationError(f"langgraph backend {child.name}: backend must be {' or '.join(BACKENDS)}, not {child.backend!r}")
        try:
            backend.check_model(child.model)
        except ValueError as error:
            raise ConfigurationError(f"langgraph backend {child.name}: {error}") from None
    reflection = settings.langgraph.reflection
    if reflection.enabled:
        reviewer = REVIEWERS.get(reflection.reviewer.backend)
        if reviewer is None:
            can = " or ".join(REVIEWERS) or "none"
            raise ConfigurationError(f"langgraph reflection: the reviewer must be a backend that can review, {can}, not {reflection.reviewer.backend!r}")
        try:
            reviewer.check_model(reflection.reviewer.model)
        except ValueError as error:
            raise ConfigurationError(f"langgraph reflection reviewer: {error}") from None
    try:
        import expense_validator.classification.orchestrator  # noqa: F401
    except ImportError:
        raise ConfigurationError("the langgraph backend needs the optional langgraph package: pip install '.[langgraph]'") from None
    if settings.mode == "live" and settings.openrouter_api_key is None:
        raise ConfigurationError("live mode needs OPENROUTER_API_KEY, or a classifier_model passed in")
