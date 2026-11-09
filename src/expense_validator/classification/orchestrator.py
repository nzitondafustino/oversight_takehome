"""Route lines through registered backends with optional bounded reflection.

Keep the last usable answer, otherwise the last abstention or failure. Routing
uses each answer's threshold without comparing confidence across backends.
Review acceptance ends a line; revision uses the first eligible reviser when
the answering backend cannot revise. Uncertain, repeated, or exhausted reviews
fall back. Failed reviews retain the answer. Unresolved objections withhold
confidence for the engine's existing uncertainty handling.

With structured feedback context, each revision is also told the review's verdict, reason and suggestion, the
previous confidence, the categories in question and its retry number; a revision that fails validation is retried
with its error while retries and calls remain; and only a reviewer's accept verifies an answer, so a line that ends
any other way is unresolved.

Route only selected line IDs within their full receipt context. Call budgets
and a recursion limit bound execution. The compiled graph shares no request
state and requires the optional langgraph extra.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from expense_validator.classification.core.common import check_reflection, classification_signature
from expense_validator.classification.core.models import Backend, ClassifiedLine, Complete, RevisionContext, Reviewer, Reviser, Vocabulary
from expense_validator.config import LangGraphRoutingConfig
from expense_validator.exceptions import NOT_RECORDED, ClassificationError, ConfigurationError
from expense_validator.normalization.models import NormalizedReceipt

@dataclass(frozen=True)
class Link:
    """One backend in a request's order, with its own model call and settings."""

    name: str  # its alias, such as primary
    backend_name: str  # its registry name, such as jev
    model: str
    backend: Backend
    complete: Complete  # counted, cached in the backend's own folder under its model, and replayed by the validator
    settings: object  # the request's settings with this backend and model


@dataclass(frozen=True)
class ReviewerLink:
    backend_name: str
    model: str
    reviewer: Reviewer
    complete: Complete  # counted, cached in the reviewer's own folder, and replayed by the validator


@dataclass(frozen=True)
class ReviserLink:
    name: str  # the alias of the backend in the order that revises
    backend_name: str
    model: str
    reviser: Reviser
    complete: Complete  # counted, cached in the revision folder, and replayed by the validator


@dataclass(frozen=True)
class Reflection:
    reviewer: ReviewerLink
    revisers: tuple[ReviserLink | None, ...]  # one per backend in the order: None where that backend cannot revise
    max_retries: int
    max_calls: int
    structured: bool = False  # feedback_context: structured


@dataclass(frozen=True)
class Context:
    """Everything one invocation needs, and nothing shared with any other."""

    chain: tuple[Link, ...]
    routing: LangGraphRoutingConfig
    normalized: NormalizedReceipt
    vocabulary: Vocabulary
    reflection: Reflection | None = None


class State(TypedDict):
    lines: dict[str, dict]  # each line's run, by line ID: see _start


def assess(line: ClassifiedLine, routing: LangGraphRoutingConfig) -> str:
    """What one backend's answer for a line is, whatever the routing then does with it."""
    if line.error is not None:
        return "missing_recording" if line.error == NOT_RECORDED else "error"
    if line.category is None:
        return "no_category"
    if line.confidence is None or line.confidence < Decimal(str(routing.min_confidence)):
        return "low_confidence"
    return "accepted"


def wants_next(outcome: str, routing: LangGraphRoutingConfig) -> bool:
    """Whether the routing sends a line with this outcome on. An abstention goes on as a failure does."""
    if outcome in ("error", "missing_recording", "no_category"):
        return routing.fallback_on_error
    return outcome == "low_confidence" and routing.fallback_on_low_confidence


def _rank(line: ClassifiedLine) -> int:
    return 0 if line.error is not None else 1 if line.category is None else 2  # a failure, an abstention, a usable answer


def _start() -> dict:
    return {
        "phase": "classify",  # classify, review, revise or done
        "next": 0,  # the next backend's place in the order
        "answer": None,  # the kept answer: (alias, ClassifiedLine, which revision it is, 0 for none)
        "attempts": (),  # every classification and revision, in order
        "reviews": (),
        "fallbacks": (),  # each time reflection, or a revision's low confidence, sent the line on
        "objections": (),  # each revise verdict made, as (classification, suggested category, feedback)
        "verdicts": {},  # each classification reviewed, by signature, with the review that judged it
        "revisions": 0,
        "calls": 0,  # classifications, revisions and reviews: every call, failed or replayed
        "feedback": None,
        "review": None,  # structured: the objection the next revision answers, and its place in reviews
        "validation_error": None,  # structured: why the previous revision could not be used
        "reviser": None,  # the place in the order of the backend that revises next
        "result": None,
    }


def _done(run: dict, result: str) -> dict:
    return {**run, "phase": "done", "result": result}


def _keep(run: dict, name: str, line: ClassifiedLine, revision: int = 0) -> dict:
    if run["answer"] is None or _rank(line) >= max(_rank(run["answer"][1]), 1):
        return {**run, "answer": (name, line, revision)}  # by order: a later usable answer wins; an abstention replaces only a failure
    return run


def _attempt(name: str, backend: str, model: str, line: ClassifiedLine, outcome: str, routed_on: bool, answered: bool, revision: int = 0) -> dict:
    attempt = {"name": name, "backend": backend, "model": model, "outcome": outcome, "routed_on": routed_on, "answered": answered, "confidence": None if line.confidence is None else float(line.confidence), "error": line.error}
    return attempt if not revision else {**attempt, "revision": revision}


def _listening(complete: Complete) -> tuple[Complete, list]:
    """The model call, and a list that holds True once it has returned an answer, recorded or live."""
    answered = []

    def call(prompt: str, text: str) -> str:
        answer = complete(prompt, text)
        answered.append(True)
        return answer

    return call, answered


def _fall_back(run: dict, reason: str, context: Context) -> dict:
    """Send a line on to the next backend in the order, or end it with this reason when none is left."""
    if run["next"] < len(context.chain):
        return {**run, "phase": "classify", "fallbacks": run["fallbacks"] + ({"from": run["answer"][0], "reason": reason},)}
    return _done(run, reason)


def _spent(run: dict, context: Context) -> bool:
    return context.reflection is not None and run["calls"] >= context.reflection.max_calls


def _classify(state: State, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    lines = dict(state["lines"])
    by_place: dict[int, list[str]] = {}
    for line_id, run in lines.items():
        if run["phase"] == "classify":
            if _spent(run, context):
                lines[line_id] = _done(run, "call_budget")
            else:
                by_place.setdefault(run["next"], []).append(line_id)
    for place in sorted(by_place):
        link = context.chain[place]
        found = _run_link(link, by_place[place], context)
        for line_id in by_place[place]:
            lines[line_id] = _classified(lines[line_id], place, link, found[line_id], context)
    return {"lines": lines}


def _run_link(link: Link, line_ids: list[str], context: Context) -> dict[str, tuple[ClassifiedLine, bool]]:
    """Ask a backend about each routed line by its ID, one at a time, so each call belongs to its own line."""
    found = {}
    for line_id in line_ids:
        complete, answered = _listening(link.complete)
        lines = link.backend.classify_receipt(context.normalized, complete, link.settings, context.vocabulary, only=frozenset({line_id}))
        if [line.line_id for line in lines] != [line_id]:
            raise ConfigurationError(f"the {link.backend_name} backend did not classify exactly the line it was asked about")
        found[line_id] = (lines[0], bool(answered))
    return found


def _classified(run: dict, place: int, link: Link, found: tuple[ClassifiedLine, bool], context: Context) -> dict:
    line, answered = found
    outcome = assess(line, context.routing)
    goes_on = wants_next(outcome, context.routing)
    has_next = place + 1 < len(context.chain)
    run = _keep(run, link.name, line)
    run = {**run, "attempts": run["attempts"] + (_attempt(link.name, link.backend_name, link.model, line, outcome, goes_on and has_next, answered),), "calls": run["calls"] + 1, "next": place + 1}
    if goes_on:
        return {**run, "phase": "classify"} if has_next else _done(run, "exhausted")
    if context.reflection is not None and _rank(line) == 2:
        return {**run, "phase": "review"}
    return _done(run, "accepted" if outcome == "accepted" else "routing_off")


def _review(state: State, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    reflection = context.reflection
    lines = dict(state["lines"])
    normalized_lines = {line.line_id: line for line in context.normalized.lines}
    for line_id, run in lines.items():
        if run["phase"] != "review":
            continue
        if _spent(run, context):
            lines[line_id] = _done(run, "call_budget")
            continue
        name, proposed, revision = run["answer"]
        entry = {"of": name, "revision": revision, "reviewer": reflection.reviewer.backend_name, "model": reflection.reviewer.model}
        signature = classification_signature(proposed)
        earlier = run["verdicts"].get(signature)
        if earlier is not None:  # the same request again would add nothing: its verdict stands, with no call
            run = {**run, "reviews": run["reviews"] + (entry | {key: earlier[key] for key in ("verdict", "reason", "suggested_category", "feedback")} | {"outcome": "reused", "answered": False, "error": None},)}
            lines[line_id] = _after_verdict(run, proposed, earlier["result"], context)
            continue
        review, answered = _listening(reflection.reviewer.complete)
        try:
            result = check_reflection(reflection.reviewer.reviewer.review(context.normalized, normalized_lines[line_id], proposed, review, context.vocabulary), context.vocabulary, proposed)
        except ClassificationError as error:
            missing = str(error) == NOT_RECORDED
            entry |= {"outcome": "missing_recording" if missing else "error", "answered": bool(answered), "verdict": None, "reason": None, "suggested_category": None, "feedback": None, "error": str(error)}
            lines[line_id] = _done({**run, "reviews": run["reviews"] + (entry,), "calls": run["calls"] + 1}, "review_missing_recording" if missing else "reviewer_failed")
            continue
        entry |= {"outcome": "verdict", "answered": True, "verdict": result.verdict, "reason": result.reason, "suggested_category": result.suggested_category, "feedback": result.feedback, "error": None}
        run = {**run, "reviews": run["reviews"] + (entry,), "calls": run["calls"] + 1, "verdicts": {**run["verdicts"], signature: {**entry, "result": result}}}
        lines[line_id] = _after_verdict(run, proposed, result, context)
    return {"lines": lines}


def _after_verdict(run: dict, proposed: ClassifiedLine, result, context: Context) -> dict:
    if result.verdict == "accept":
        return _done(run, "accepted")
    if result.verdict == "uncertain":  # neither accept nor revise: another backend may settle it
        return _fall_back(run, "reviewer_uncertain", context)
    return _after_objection(run, proposed, result, context)


def _after_objection(run: dict, proposed: ClassifiedLine, result, context: Context) -> dict:
    objection = (classification_signature(proposed), result.suggested_category, " ".join(result.feedback.split()).casefold())
    repeated = objection in run["objections"]
    run = {**run, "objections": run["objections"] + (() if repeated else (objection,))}
    if repeated:
        return _fall_back(run, "repeated_objection", context)
    if run["revisions"] >= context.reflection.max_retries:
        return _fall_back(run, "retries_exhausted", context)
    reviser = _reviser(run["answer"][0], context)
    if reviser is None:
        return _fall_back(run, "no_reviser", context)
    if _spent(run, context):
        return _done(run, "call_budget")
    revise = {**run, "phase": "revise", "feedback": result.feedback, "reviser": reviser}
    if context.reflection.structured:
        review = {"verdict": result.verdict, "reason": result.reason, "feedback": result.feedback, "suggested_category": result.suggested_category}
        revise |= {"review": (review, len(run["reviews"]) - 1), "validation_error": None}
    return revise


def _reviser(name: str, context: Context) -> int | None:
    """The answering backend, if it can take feedback; else the first in the order that can."""
    revisers = context.reflection.revisers
    own = next(place for place, link in enumerate(context.chain) if link.name == name)
    if revisers[own] is not None:
        return own
    return next((place for place, reviser in enumerate(revisers) if reviser is not None), None)


def _revise(state: State, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    lines = dict(state["lines"])
    normalized_lines = {line.line_id: line for line in context.normalized.lines}
    for line_id, run in lines.items():
        if run["phase"] != "revise":
            continue
        if _spent(run, context):
            lines[line_id] = _done(run, "call_budget")
            continue
        link = context.reflection.revisers[run["reviser"]]
        previous = run["answer"][1]
        complete, answered = _listening(link.complete)
        revision = run["revisions"] + 1
        if context.reflection.structured:
            review, answers = run["review"]
            told = RevisionContext(review["verdict"], review["reason"], review["feedback"], review["suggested_category"], revision, context.reflection.max_retries, run["validation_error"])
            revised = link.reviser.revise(context.normalized, normalized_lines[line_id], previous, run["feedback"], complete, context.vocabulary, context=told)
        else:
            revised = link.reviser.revise(context.normalized, normalized_lines[line_id], previous, run["feedback"], complete, context.vocabulary)
        outcome = assess(revised, context.routing)
        goes_on = _rank(revised) == 2 and wants_next(outcome, context.routing)
        attempt = _attempt(link.name, link.backend_name, link.model, revised, outcome, goes_on and run["next"] < len(context.chain), bool(answered), revision)
        if context.reflection.structured:  # which review it answered, and whether it was retrying a failed revision
            attempt |= {"answers_review": answers, "max_retries": context.reflection.max_retries, "retried_validation_error": run["validation_error"] is not None}
        run = {**run, "revisions": revision, "calls": run["calls"] + 1, "attempts": run["attempts"] + (attempt,)}
        if context.reflection.structured and outcome == "error" and revision < context.reflection.max_retries and not _spent(run, context):
            # Failed validation: try again with the error, while retries and calls remain. A missing recording is never retried.
            lines[line_id] = {**run, "validation_error": (revised.error or "")[:200]}
            continue
        if _rank(revised) < 2:
            lines[line_id] = _fall_back(run, "revision_missing_recording" if outcome == "missing_recording" else "revision_failed", context)
        elif classification_signature(revised) == classification_signature(previous):
            lines[line_id] = _fall_back(run, "revision_unchanged", context)
        else:
            run = _keep(run, link.name, revised, revision)
            if goes_on:
                lines[line_id] = _fall_back(run, outcome, context) if run["next"] < len(context.chain) else _done(run, "exhausted")
            else:
                lines[line_id] = {**run, "phase": "review"}
    return {"lines": lines}


PHASES = ("classify", "review", "revise")


def _next(state: State) -> str:
    phases = {run["phase"] for run in state["lines"].values()}
    return next((phase for phase in PHASES if phase in phases), END)


def _build():
    graph = StateGraph(State, context_schema=Context)
    graph.add_node("classify", _classify)
    graph.add_node("review", _review)
    graph.add_node("revise", _revise)
    graph.add_edge(START, "classify")
    for node in PHASES:
        graph.add_conditional_edges(node, _next, [*PHASES, END])
    return graph.compile()


GRAPH = _build()  # compiled once, and never changed: a request's settings travel only in its Context


def recursion_limit(chain: int, lines: int, reflection: Reflection | None) -> int:
    """The supersteps a correct run can take, plus a margin: every superstep spends a call on, or ends, a line."""
    per_line = chain if reflection is None else reflection.max_calls
    return lines * (per_line + 1) + 5


def classify_receipt(normalized: NormalizedReceipt, chain: tuple[Link, ...], routing: LangGraphRoutingConfig, vocabulary: Vocabulary, reflection: Reflection | None = None) -> tuple[ClassifiedLine, ...]:
    """Classify receipt lines through ordered backends and optional reflection.

    Args:
        normalized: Receipt and normalized lines.
        chain: Ordered backend connections.
        routing: Fallback conditions and confidence threshold.
        vocabulary: Allowed categories and attributes.
        reflection: Optional reviewer, revisers, and call limits.

    Returns:
        Final classifications with backend details and orchestration audit metadata.
        Unresolved reviews withhold confidence. Prompts and raw responses are omitted.

    Raises:
        ConfigurationError: If the chain or reviser configuration is invalid.
        RuntimeError: If execution exceeds its recursion bound.
    """
    if not chain:
        raise ConfigurationError("the langgraph backend needs at least one backend")
    if reflection is not None and len(reflection.revisers) != len(chain):
        raise ConfigurationError("reflection needs one reviser entry per backend in the order")
    ids = tuple(line.line_id for line in normalized.lines)
    context = Context(chain, routing, normalized, vocabulary, reflection)
    try:
        state = GRAPH.invoke({"lines": {line_id: _start() for line_id in ids}}, context=context, config={"recursion_limit": recursion_limit(len(chain), len(ids), reflection)})
    except GraphRecursionError:
        raise RuntimeError("the orchestrator went past its bound on supersteps, which a correct run never reaches") from None
    lines = []
    for line_id in ids:
        run = state["lines"][line_id]
        name, line, _ = run["answer"]
        audit = {"answered_by": name, "fallback": sum(1 for attempt in run["attempts"] if "revision" not in attempt) > 1, "result": run["result"], "attempts": list(run["attempts"])}
        if reflection is not None:
            if reflection.structured:  # only a reviewer's accept verifies an answer: a limit, a failure or a gap never does
                unresolved = run["result"] != "accepted" and _rank(line) == 2
            else:
                unresolved = run["result"] != "accepted" and _rank(line) == 2 and any(review["verdict"] in ("revise", "uncertain") for review in run["reviews"])
            audit["reflection"] = {"calls": run["calls"], "revisions": run["revisions"], "reviews": list(run["reviews"]), "fallbacks": list(run["fallbacks"]), "unresolved": unresolved}
            if reflection.structured:
                audit["reflection"] |= {"feedback_context": "structured", "stop_reason": run["result"], "max_retries": reflection.max_retries, "max_calls": reflection.max_calls}
            if unresolved:  # the category stands; the engine's no_confidence state takes the doubt the review left
                audit["reflection"]["withheld_confidence"] = None if line.confidence is None else float(line.confidence)
                line = line._replace(confidence=None)
        lines.append(line._replace(details={**(line.details or {}), "orchestration": audit}))
    return tuple(lines)
