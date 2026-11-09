"""Define backend-neutral classification and reflection contracts."""

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from openrouter import OpenRouter

    from expense_validator.models.settings import ValidatorSettings
    from expense_validator.normalization.models import NormalizedLine, NormalizedReceipt


# Map instructions and line input to response text; the validator handles
# counting, caching, and cache-only replay for every backend.
Complete = Callable[[str, str], str]


class Category(NamedTuple):
    restricted: bool
    examples: str


class Vocabulary(NamedTuple):
    """Values the model may return, from the taxonomy and the field catalog."""

    categories: Mapping[str, Category]
    recipient_types: tuple[str, ...]
    venue_types: tuple[str, ...]
    item_kinds: tuple[str, ...]


class ClassifiedLine(NamedTuple):
    """A line's category and attributes, or an error with neither."""

    line_id: str
    category: str | None = None
    restricted: bool | None = None
    confidence: Decimal | None = None
    recipient_name: str | None = None
    recipient_type: str | None = None
    venue_type: str | None = None
    item_kind: str | None = None
    essential_toiletry: bool | None = None
    error: str | None = None
    details: dict | None = None  # a backend's own evidence, such as Jev's probabilities; never read by the engine


class Backend(NamedTuple):
    """Define the classifier interface used by the validator.

    Attributes:
        check_model: Validate the configured model or raise ValueError.
        prompt: Build instructions included in the reported prompt hash.
        cache_key: Build the model and contract cache prefix.
        cache_folder: Cache subfolder; empty means the cache root.
        connect: Create a live completion using the shared OpenRouter client.
        classify_receipt: Classify selected line IDs in receipt context and order.
            Finalize each line once; isolate failures to the affected line.
    """

    check_model: Callable[[str | None], None]
    prompt: Callable[[Vocabulary], str]
    cache_key: Callable[[str, Vocabulary], str]
    cache_folder: str
    connect: Callable[["OpenRouter", "ValidatorSettings", Vocabulary], Complete]
    classify_receipt: Callable[..., "tuple[ClassifiedLine, ...]"]  # (normalized, complete, settings, vocabulary, only=None)


class ReflectionResult(BaseModel):
    """Represent an evidence-based review without changing the classification.

    Attributes:
        verdict: Accept, request revision, or report uncertainty.
        reason: Explanation of the verdict.
        suggested_category: Optional evidence-supported category.
        feedback: Optional instructions for a classifier revision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    verdict: Literal["accept", "revise", "uncertain"]
    reason: str = Field(pattern=r"\S")
    suggested_category: str | None = None
    feedback: str | None = None


class Reviewer(NamedTuple):
    """Define an optional classification reviewer.

    Attributes:
        check_model: Validate the reviewer model.
        prompt: Build review instructions for prompt hashing.
        cache_key: Build the review cache prefix.
        cache_folder: Separate review cache folder.
        connect: Create the review completion.
        review: Return a checked verdict or raise ClassificationError.
    """

    check_model: Callable[[str | None], None]
    prompt: Callable[[Vocabulary], str]
    cache_key: Callable[[str, Vocabulary], str]
    cache_folder: str
    connect: Callable[["OpenRouter", "ValidatorSettings", Vocabulary], Complete]
    review: Callable[["NormalizedReceipt", "NormalizedLine", ClassifiedLine, Complete, Vocabulary], ReflectionResult]


class RevisionContext(NamedTuple):
    """What a structured revision is told about the answer it revises, besides the line itself.

    Attributes:
        verdict: The review's verdict, revise.
        reason: The review's one-sentence reason.
        feedback: What the reviewer says to reconsider.
        suggested_category: The reviewer's category, or None when it could not tell.
        retry: This revision's number, from 1.
        max_retries: The most revisions the line may have.
        validation_error: The previous revision's error, when it failed the checks every answer gets.
    """

    verdict: str
    reason: str | None
    feedback: str
    suggested_category: str | None
    retry: int
    max_retries: int
    validation_error: str | None = None


class Reviser(NamedTuple):
    """Define an optional feedback-guided classifier revision.

    The registered backend validates the model.

    Attributes:
        prompt: Build revision instructions.
        cache_key: Build the revision cache prefix.
        cache_folder: Separate revision cache folder.
        connect: Create the revision completion.
        revise: Reclassify and finalize once; return an error-only line on failure. Given a
            RevisionContext as `context`, it sends the structured request instead.
        structured_prompt: Build the instructions for a structured revision, or None when the reviser
            takes the reviewer's feedback text alone.
    """

    prompt: Callable[[Vocabulary], str]
    cache_key: Callable[[str, Vocabulary], str]
    cache_folder: str
    connect: Callable[["OpenRouter", "ValidatorSettings", Vocabulary], Complete]
    revise: Callable[..., ClassifiedLine]
    structured_prompt: Callable[[Vocabulary], str] | None = None
