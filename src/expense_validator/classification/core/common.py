"""Build shared inputs and finalize classifications without backend dependencies."""

import json
import re
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from expense_validator.classification.core.models import Category, ClassifiedLine, ReflectionResult, Vocabulary
from expense_validator.classification.core.recipients import NOT_PEOPLE, names_a_recipient, recipient_mentions
from expense_validator.compiler.validate import read_fields
from expense_validator.exceptions import ClassificationError
from expense_validator.normalization.models import NormalizedLine, NormalizedReceipt


# The taxonomy sends phones, prescriptions and venues to `other`, and over-the-counter medication to health_otc.
ITEM_KIND_CATEGORIES = {"mobile_phone": "other", "prescription_medication": "other", "otc_medication": "health_otc"}
VENUE_CATEGORY = "other"
TOILETRY_CATEGORY = "personal_care"


# Only finalize sets this out-of-catalog sentinel: EXC-03 requests review,
# GIFT-04 stays open, and other rules retain the category.
UNIDENTIFIED_RECIPIENT = "unidentified"


TAXONOMY_ROW = re.compile(r"^\| `(\w+)` \| (yes|no) \| (.+?) \|$", re.MULTILINE)


def load_taxonomy(path: Path) -> dict[str, Category]:
    """Load the category table, the only source of restricted flags."""
    rows = TAXONOMY_ROW.findall(path.read_text())
    if not rows:
        raise ValueError(f"{path} holds no taxonomy table")
    return {name: Category(restricted == "yes", examples) for name, restricted, examples in rows}


def load_vocabulary(taxonomy_path: Path, fields_path: Path) -> Vocabulary:
    fields = read_fields(fields_path.read_text())
    return Vocabulary(
        categories=load_taxonomy(taxonomy_path),
        recipient_types=tuple(fields["item.recipient_type"]["values"]),
        venue_types=tuple(fields["item.venue_type"]["values"]),
        item_kinds=tuple(fields["item.item_kind"]["values"]),
    )


def receipt_language(line_languages: Sequence[str]) -> str | None:
    """Return the language of more than half the lines, or None. Inferred, not recorded."""
    language, count = Counter(line_languages).most_common(1)[0]
    return language if count * 2 > len(line_languages) else None


def line_input(normalized: NormalizedReceipt, line: NormalizedLine) -> str:
    """Build the JSON the model reads for one line.

    Excludes submitted_expense_type, which DOC-03 checks against the category, and the facts rules decide on.
    """
    receipt = normalized.receipt
    item = next(item for item in receipt.line_items if item.line_id == line.line_id)
    fields = {
        "original_description": line.description_normalized,
        "description_language": line.language,
    }
    if line.translation_status == "translated":
        fields["english_translation"] = line.translation
    fields |= {
        "receipt_language": receipt_language([other.language for other in receipt.line_items]),
        "merchant_name": receipt.merchant_name,
        "merchant_category": receipt.mcc_description,
        "itemized_receipt": receipt.itemized,
        "quantity": item.quantity,
        "unit_price": str(item.unit_price),
        "currency": receipt.currency,
    }
    return json.dumps(fields, ensure_ascii=False)


def check_consistent(answer) -> None:
    """Refuse attributes that contradict the category. Takes any answer with those four fields."""
    if answer.item_kind is not None and ITEM_KIND_CATEGORIES.get(answer.item_kind) != answer.category:
        raise ClassificationError(f"item_kind {answer.item_kind} contradicts category {answer.category}")
    if answer.venue_type is not None and answer.category != VENUE_CATEGORY:
        raise ClassificationError(f"venue_type contradicts category {answer.category}")
    if answer.essential_toiletry is not None and answer.category != TOILETRY_CATEGORY:
        raise ClassificationError(f"essential_toiletry contradicts category {answer.category}")


def finalize(line: ClassifiedLine, normalized: NormalizedLine, vocabulary: Vocabulary) -> ClassifiedLine:
    """Finalize recipient evidence and validate one classification.

    Preserve explicit names and source spelling. Conflicting names become
    unidentified recipients, with mentions retained in details. Invalid taxonomy,
    catalog, or attribute combinations produce an error-only line.

    Raises:
        ClassificationError: If classification and source line IDs differ.
    """
    if line.line_id != normalized.line_id:
        raise ClassificationError("classification line identity mismatch")
    if line.error:
        return ClassifiedLine(line.line_id, error=line.error)
    mentions = recipient_mentions(normalized.description_normalized, normalized.translation)
    names = {mention.text.casefold(): mention.text for mention in reversed(mentions)}  # the first spelling wins
    if line.category not in (None, "service_fee"):
        if line.recipient_name is None and line.recipient_type is None and names_a_recipient(normalized.description_normalized, normalized.translation):
            line = line._replace(recipient_type=UNIDENTIFIED_RECIPIENT)
        if line.recipient_name is None and len(names) == 1:
            line = line._replace(recipient_name=next(iter(names.values())))
        elif isinstance(line.recipient_name, str) and line.recipient_name.casefold() in names:
            line = line._replace(recipient_name=names[line.recipient_name.casefold()])
    if line.category is not None:
        try:
            _check_line(line, normalized, vocabulary)
        except ClassificationError as error:
            return ClassifiedLine(line.line_id, error=str(error))
    name = line.recipient_name.casefold() if isinstance(line.recipient_name, str) else None
    if len(names) > 1 or (name and names and not all(other in name or name in other for other in names)):
        conflict = {"recipient_mentions": [mention.model_dump() for mention in mentions], "recipient_name": line.recipient_name, "recipient_type": line.recipient_type}
        # A disputed identity must not be used for per-recipient totals.
        return line._replace(recipient_name=None, recipient_type=UNIDENTIFIED_RECIPIENT, recipient_type_confidence=None, details={**(line.details or {}), "evidence_conflict": conflict})
    return line


ATTRIBUTE_CONFIDENCE_FIELDS = ("recipient_type", "venue_type", "item_kind", "essential_toiletry")


def _is_confidence(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and 0 <= value <= 1


def _check_line(line: ClassifiedLine, normalized: NormalizedLine, vocabulary: Vocabulary) -> None:
    if line.category not in vocabulary.categories:
        raise ClassificationError("category is not in taxonomy")
    if line.restricted is not vocabulary.categories[line.category].restricted:
        raise ClassificationError("restricted is not taxonomy-derived")
    if line.confidence is not None and not _is_confidence(line.confidence):
        raise ClassificationError("invalid category confidence")
    for field in ATTRIBUTE_CONFIDENCE_FIELDS:
        confidence = getattr(line, f"{field}_confidence")
        if confidence is not None and (getattr(line, field) is None or not _is_confidence(confidence)):
            raise ClassificationError(f"invalid {field} confidence")
    for field, allowed in (("recipient_type", (*vocabulary.recipient_types, UNIDENTIFIED_RECIPIENT)), ("venue_type", vocabulary.venue_types), ("item_kind", vocabulary.item_kinds)):
        if getattr(line, field) is not None and getattr(line, field) not in allowed:
            raise ClassificationError(f"invalid {field}")
    check_consistent(line)
    if line.recipient_name is not None:
        if not isinstance(line.recipient_name, str):
            raise ClassificationError("recipient name is not text")
        if NOT_PEOPLE.search(line.recipient_name):
            raise ClassificationError("recipient name is a known product or brand")
        texts = (normalized.description_normalized, normalized.translation or "")
        if not line.recipient_name.strip() or not any(line.recipient_name.casefold() in text.casefold() for text in texts):
            raise ClassificationError("recipient name has no source evidence")


# Shared review and revision helpers.

SIGNATURE_FIELDS = ("category", "recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry")


def classification_signature(line: ClassifiedLine) -> tuple:
    """What a classification says, without its confidence or evidence: two equal signatures say the same."""
    return tuple(getattr(line, name) for name in SIGNATURE_FIELDS)


def _proposal(line: ClassifiedLine) -> dict:
    return {name: getattr(line, name) for name in SIGNATURE_FIELDS if getattr(line, name) is not None}


def review_input(normalized: NormalizedReceipt, line: NormalizedLine, proposed: ClassifiedLine) -> str:
    """A review's request text: the line's own input, and the classification proposed for it."""
    return json.dumps({"line": json.loads(line_input(normalized, line)), "proposed": _proposal(proposed)}, ensure_ascii=False)


def revision_input(normalized: NormalizedReceipt, line: NormalizedLine, previous: ClassifiedLine, feedback: str) -> str:
    """Build revision input including feedback to keep its cache identity distinct."""
    return json.dumps({"line": json.loads(line_input(normalized, line)), "previous_classification": _proposal(previous), "reviewer_feedback": feedback}, ensure_ascii=False)


def structured_revision_input(normalized: NormalizedReceipt, line: NormalizedLine, previous: ClassifiedLine, context, vocabulary) -> str:
    """Build a structured revision's input: the line as classified, the answer revised with its confidence, the
    review, the definitions of the categories in question, and where this retry stands. Nothing else is added."""
    named = [category for category in (previous.category, context.suggested_category) if category is not None and category in vocabulary.categories]
    request = {
        "line": json.loads(line_input(normalized, line)),
        "previous_classification": _proposal(previous) | {"confidence": None if previous.confidence is None else str(previous.confidence)},
        "review": {"verdict": context.verdict, "reason": context.reason, "feedback": context.feedback, "suggested_category": context.suggested_category},
        "categories_in_question": {category: vocabulary.categories[category].examples for category in dict.fromkeys(named)},
        "retry": {"number": context.retry, "max": context.max_retries},
    }
    if context.validation_error is not None:
        request["failed_revision"] = context.validation_error
    return json.dumps(request, ensure_ascii=False)


def check_reflection(result: ReflectionResult, vocabulary: Vocabulary, proposed: ClassifiedLine) -> ReflectionResult:
    """Return a reviewer's result when it is usable.

    Raises:
        ClassificationError: If a suggested category is outside the taxonomy, a revise verdict gives no feedback,
            or another verdict suggests a different category.
    """
    if not isinstance(result, ReflectionResult):
        raise ClassificationError(f"a reviewer returned {type(result).__name__}, not a ReflectionResult")
    if result.suggested_category is not None and result.suggested_category not in vocabulary.categories:
        raise ClassificationError(f"reflection suggested a category outside the taxonomy: {result.suggested_category[:60]!r}")
    if result.verdict == "revise" and not (result.feedback and result.feedback.strip()):
        raise ClassificationError("a revise verdict needs feedback")
    if result.verdict != "revise" and result.suggested_category not in (None, proposed.category):
        raise ClassificationError(f"only a revise verdict may suggest another category, not {result.verdict}")
    return result


def parse_reflection(raw: str, vocabulary: Vocabulary, proposed: ClassifiedLine) -> ReflectionResult:
    """Parse a reviewer's JSON answer and check it.

    Raises:
        ClassificationError: If the answer is malformed or unusable.
    """
    try:
        result = ReflectionResult.model_validate_json(raw)
    except ValidationError as error:
        problems = ", ".join(f"{'.'.join(map(str, item['loc'])) or 'answer'}: {item['type']}" for item in error.errors(include_input=False))
        raise ClassificationError(f"reflection is malformed: {problems}") from None
    return check_reflection(result, vocabulary, proposed)
