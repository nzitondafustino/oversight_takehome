"""Optional English translation behind a small interface, with each result checked."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol

import httpx
from openrouter import OpenRouter
from openrouter.components import ChatResult
from openrouter.errors import OpenRouterError
from openrouter.utils import BackoffStrategy, RetryConfig

from expense_validator.compiler.llm_extract import cached
from expense_validator.config import Config
from expense_validator.exceptions import NOT_RECORDED, TranslationError
from expense_validator.normalization.text import normalize_text

# not_requested: translation is off. skipped: nothing to do, as the text is English or empty.
TranslationStatus = Literal["not_requested", "translated", "skipped", "failed"]

# The default template of Tencent's Hy-MT2 models, which take one user message and answer in plain
# text. It names no source language, so these models work that out from the text.
PROMPT = """Translate the following text into English.
Note that you should only output the translated result without any additional explanation:"""

# The SDK's default retries a server or connection error for up to an hour, on every line.
RETRIES = RetryConfig("backoff", BackoffStrategy(500, 5_000, 1.5, 30_000), True)


class Translator(Protocol):
    def translate(self, text: str, source_language: str) -> str:
        """Return English text, or raise TranslationError."""
        ...


class NoOpTranslator:
    """Translation switched off."""

    def translate(self, text: str, source_language: str) -> str:
        raise TranslationError("translation is switched off")


@dataclass(frozen=True)
class LlmTranslator:
    """Translate through a completion function."""

    complete: Callable[[str, str], str]

    def translate(self, text: str, source_language: str) -> str:
        # Without the blank line, tencent/hy-mt2-30b-a3b returned null content for some lines, every time.
        # Why is not known. It is part of the text so that the cache tells the two requests apart.
        return self.complete(PROMPT, f"{text}\n\n")


def openrouter_completion(client: OpenRouter, model: str, temperature: float) -> Callable[[str, str], str]:
    """Return a translation completion function through the OpenRouter SDK."""

    def complete(prompt: str, text: str) -> str:
        try:
            result = client.chat.send(
                model=model,
                temperature=temperature,
                messages=[{"role": "user", "content": f"{prompt}\n\n{text}"}],
                stream=False,
            )
            content = result.choices[0].message.content
        except (OpenRouterError, httpx.HTTPError, IndexError) as error:
            raise TranslationError(f"translation request failed: {type(error).__name__}: {error}") from error
        if not isinstance(content, str):
            raise TranslationError(f"translation response holds no text: {response_facts(result, content)}")
        return content

    return complete


def response_facts(result: ChatResult, content: object) -> str:
    """Describe a response with no text, without quoting any text."""
    usage = result.usage
    return (
        f"content_type={type(content).__name__} finish_reason={result.choices[0].finish_reason} "
        f"prompt_tokens={usage.prompt_tokens if usage else None} completion_tokens={usage.completion_tokens if usage else None} "
        f"model={result.model} generation={result.id}"
    )


def replay_only(missing: list[str], error: type[Exception]) -> Callable[[str, str], str]:
    """Return a completion function that records each request as missing and fails."""

    def refuse(prompt: str, text: str) -> str:
        missing.append(text)
        raise error(NOT_RECORDED)

    return refuse


def build_translator(config: Config, *, replay: list[str] | None = None) -> Translator:
    """Return the translator the settings ask for.

    Args:
        config: Project configuration.
        replay: If given, answer only from the cache and record each miss here.

    Raises:
        ValueError: If translation is enabled but cannot run.
    """
    settings = config.translation
    if not settings.enabled:
        return NoOpTranslator()
    if replay is not None:
        if not settings.model:
            raise ValueError("translation is enabled, so set translation.model in config.yaml")
        return LlmTranslator(cached(replay_only(replay, TranslationError), settings.model, config.paths.llm_cache))
    if config.openrouter_api_key is None or not settings.model:
        raise ValueError("translation is enabled, so set OPENROUTER_API_KEY in .env and translation.model in config.yaml")
    client = OpenRouter(
        api_key=config.openrouter_api_key.get_secret_value(),
        timeout_ms=config.llm.timeout_seconds * 1000,
        retry_config=RETRIES,
    )
    complete = openrouter_completion(client, settings.model, config.llm.temperature)
    return LlmTranslator(cached(complete, settings.model, config.paths.llm_cache))


class TranslationOutcome(NamedTuple):
    status: TranslationStatus
    translation: str | None = None
    error: str | None = None


def translate_description(text: str, language: str, translator: Translator) -> TranslationOutcome:
    """Translate one normalized description. A failure is an outcome, not an exception."""
    if isinstance(translator, NoOpTranslator):
        return TranslationOutcome("not_requested")
    if not text or _is_english(language):
        return TranslationOutcome("skipped")
    try:
        result = translator.translate(text, language)
    except TranslationError as error:
        return TranslationOutcome("failed", error=str(error) or "translation failed")
    problem = _problem_with(text, result)
    if problem:
        return TranslationOutcome("failed", error=problem)
    return TranslationOutcome("translated", translation=normalize_text(result))


def _is_english(language: str) -> bool:
    return language.strip().casefold().replace("_", "-").split("-")[0] == "en"


def _problem_with(text: str, result: object) -> str | None:
    """Return why a translation fails language-independent checks, or None."""
    if not isinstance(result, str):
        return f"translator returned {type(result).__name__}, not text"
    if not normalize_text(result):
        return "translator returned blank text"
    if len(result.strip().splitlines()) > 1:
        return "translator returned several lines for one line of text"
    if _numbers(result) != _numbers(text):
        return "translation does not keep the numbers in the text"
    return None


def _numbers(text: str) -> list[str]:
    """Return the digit runs in text, comparing full-width and plain digits alike."""
    return sorted(re.findall(r"\d+", unicodedata.normalize("NFKC", text)))
