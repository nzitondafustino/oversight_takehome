"""Score classified lines against the labelled answers. Calls no model and reads no file.

Callers classify a dataset's receipts themselves, and pass each NormalizedReceipt with its ClassifiedLines.
The command line, in cli/classification.py, does that with the configured LLM classifier.
"""

from collections import Counter
from collections.abc import Mapping, Sequence

from expense_validator.classification.core.models import ClassifiedLine
from expense_validator.evaluation.dataset import Dataset
from expense_validator.evaluation.metrics import classification_scores, rate
from expense_validator.evaluation.predictions import LinePrediction, PredictionSet, Source
from expense_validator.normalization.models import NormalizedReceipt

ATTRIBUTES = ("recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry")

Classified = Sequence[tuple[NormalizedReceipt, Sequence[ClassifiedLine]]]  # each receipt, with its lines' results in order


def score_lines(lines: Sequence[Mapping]) -> dict:
    """Score lines, counting a failed line as wrong on category and restricted flag."""
    total = len(lines)
    return {
        **classification_scores(lines),
        "restricted_accuracy": rate(sum(1 for line in lines if line["restricted"] is line["gold_restricted"]), total),
        "failures": rate(sum(1 for line in lines if line["error"] is not None), total),
    }


def line_prediction(classified: ClassifiedLine) -> LinePrediction:
    """Convert a classified line to a prediction. A failed line is left unclassified."""
    return LinePrediction(**{name: value for name, value in classified._asdict().items() if name not in ("error", "details")})


def _scored_line(classified: ClassifiedLine, description: str, gold_category: str, gold_restricted: bool) -> dict:
    return {
        "line_id": classified.line_id,
        "description": description,
        "gold_category": gold_category,
        "gold_restricted": gold_restricted,
        "category": classified.category,
        "restricted": classified.restricted,
        "confidence": classified.confidence,
        "error": classified.error,
    }


def _attributes_read(classified: ClassifiedLine, description: str) -> dict | None:
    found = {name: getattr(classified, name) for name in ATTRIBUTES if getattr(classified, name) is not None}
    return {"line_id": classified.line_id, "description": description, "category": classified.category, **found} if found else None


def classification_metrics(dataset: Dataset, classified: Classified, *, model: str | None, translation_model: str | None) -> dict:
    """Score the classified receipts against the dataset's answers.

    Args:
        dataset: The labelled receipts. Only their answers are read.
        classified: Each receipt as normalized, with its classified lines, in the order to report them.
        model: The classification model, as reported.
        translation_model: The translation model, as reported.
    """
    lines, classified_lines, attributes, translations = [], [], [], Counter()
    for normalized, results in classified:
        translations.update(line.translation_status for line in normalized.lines)
        classified_lines.extend(results)
        answers = {answer.line_id: answer for answer in dataset.answers[normalized.receipt_id].lines}
        for line, result in zip(normalized.lines, results):
            answer = answers[line.line_id]
            lines.append(_scored_line(result, line.description_normalized, answer.category, answer.restricted))
            read = _attributes_read(result, line.description_normalized)
            if read:
                attributes.append(read)

    return {
        "model": model,
        "translation_model": translation_model,
        "translations": dict(sorted(translations.items())),
        "scores": score_lines(lines),
        "confidence_levels": dict(sorted(Counter(str(line.confidence) for line in classified_lines).items())),
        "attributes_read": attributes,
        "failed": [line for line in lines if line["error"] is not None],
        "wrong": [line for line in lines if line["error"] is None and line["category"] != line["gold_category"]],
    }


def missing_recordings(results: Sequence) -> list[dict]:
    """Return every LangGraph attempt that replay had no recording for, in validate_batch results.

    A missing recording is missing evidence, not a classifier failure. Ordinary replay lets such a line follow
    the configured fallback, so strict evaluation must find this list empty before scoring anything: otherwise
    a gap would read as an error, or a fallback would answer in a model's place. A backend chosen alone raises
    ReplayAnswerMissing instead, so it never appears here.
    """
    found = []
    for result in results:
        for line in result.line_items:
            audit = (line.classification_details or {}).get("orchestration") or {}
            for attempt in audit.get("attempts", ()):
                if attempt["outcome"] == "missing_recording":
                    found.append({"line_id": line.line_id, "name": attempt["name"], "backend": attempt["backend"], "model": attempt["model"], "call": "revision" if attempt.get("revision") else "classification"})
            for review in (audit.get("reflection") or {}).get("reviews", ()):
                if review["outcome"] == "missing_recording":
                    found.append({"line_id": line.line_id, "name": review["of"], "backend": review["reviewer"], "model": review["model"], "call": "review"})
    return found


def result_predictions(results: Sequence, source: Source) -> PredictionSet:
    """Return validate_batch results as predictions, which policy_outputs can score. Failed lines are left out."""
    made = []
    for result in results:
        for line in result.line_items:
            if line.classification_error is None:
                made.append(LinePrediction(line_id=line.line_id, category=line.category, restricted=line.restricted, confidence=None if line.confidence is None else str(line.confidence), **(line.attributes or {})))
    return PredictionSet(source=source, predictions=tuple(made))


def routing_summary(results: Sequence) -> dict:
    """Count, from the orchestration audit of LangGraph results, who answered, how often a line went on, and the
    calls made beyond each line's first, reviews and revisions included. Results without an audit, from a backend
    chosen alone, count as lines with no fallback and no extra call."""
    audits = [(line.classification_details or {}).get("orchestration") for result in results for line in result.line_items]
    lines = len(audits)
    return {
        "lines": lines,
        "answered_by": dict(sorted(Counter(audit["answered_by"] for audit in audits if audit).items())),
        "fallback": rate(sum(1 for audit in audits if audit and audit["fallback"]), lines),
        "results": dict(sorted(Counter(audit["result"] for audit in audits if audit).items())),
        "extra_backend_calls": sum((audit["reflection"]["calls"] if "reflection" in audit else len(audit["attempts"])) - 1 for audit in audits if audit),
        "reviews": sum(len(audit["reflection"]["reviews"]) for audit in audits if audit and "reflection" in audit),
        "revisions": sum(audit["reflection"]["revisions"] for audit in audits if audit and "reflection" in audit),
        "routed_for": dict(sorted(Counter(attempt["outcome"] for audit in audits if audit for attempt in audit["attempts"] if attempt["routed_on"]).items())),
    }


def classification_predictions(classified: Classified, model: str) -> PredictionSet:
    """Return the classified lines as predictions of kind actual, which policy_outputs can score. Failed lines are left out."""
    source = Source(kind="actual", name="baseline-llm", description="One request per line: category, stated attributes, coarse model-reported confidence", model=model)
    return PredictionSet(
        source=source,
        predictions=tuple(line_prediction(line) for _, results in classified for line in results if line.error is None),
    )
