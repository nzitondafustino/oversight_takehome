"""Register backends and their optional review and revision capabilities.

A backend is an execution adapter: the endpoint protocol, request and response contract a model needs, named for that
protocol. It is neither a provider nor a model. Every registered adapter sends its requests through the shared
OpenRouter client, so OpenRouter is the provider of each, and the model decides which adapter it needs: each adapter's
own check_model accepts it. The adapters' earlier names, openrouter and jev, are config.LEGACY_BACKENDS.
"""

from typing import NamedTuple

from expense_validator.classification.backends import jev, llm
from expense_validator.classification.core.models import Backend, Reviewer, Reviser
from expense_validator.config import CHAT_COMPLETIONS, DECISIONS

BACKENDS: dict[str, Backend] = {
    CHAT_COMPLETIONS: llm.BACKEND,  # an LLM through chat completions; the default (earlier: openrouter)
    DECISIONS: jev.BACKEND,  # a decision model, TypeSafe's Jev, through the Decisions API (earlier: jev)
}


class Adapter(NamedTuple):
    """How a registered backend reaches its model: the provider it calls and the protocol it speaks."""

    provider: str
    protocol: str


# Which provider each adapter calls. Recordings are keyed by model and prompt, never by these names.
ADAPTERS: dict[str, Adapter] = {
    CHAT_COMPLETIONS: Adapter(provider="openrouter", protocol=CHAT_COMPLETIONS),
    DECISIONS: Adapter(provider="openrouter", protocol=DECISIONS),
}

# Optional capabilities, used only by LangGraph's reflection. A backend has them only if it registers them here.
REVIEWERS: dict[str, Reviewer] = {
    CHAT_COMPLETIONS: llm.REVIEWER,  # Jev cannot review: it answers bounded choices and writes no reason or feedback
}
REVISERS: dict[str, Reviser] = {  # a backend that can classify again with a reviewer's feedback
    CHAT_COMPLETIONS: llm.REVISER,  # Jev cannot: its Decisions API takes typed questions and receipt text, with no feedback channel
}


def backends_for(model: str) -> list[str]:
    """The registered backends whose own check accepts this model ID: the adapters that could run it."""
    accepted = []
    for name, backend in BACKENDS.items():
        try:
            backend.check_model(model)
        except ValueError:
            continue
        accepted.append(name)
    return accepted
