"""Deterministic text clean-up. NFC, not NFKC, so printed characters such as full-width digits survive."""

import unicodedata

# Control, format and surrogate characters print nothing, so removing one joins what looked joined.
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
# Format characters that change how a word in some scripts is written, so they stay inside a word.
_JOINERS = "‌‍"


def _is_removed(character: str) -> bool:
    return (
        not character.isspace()
        and character not in _JOINERS
        and unicodedata.category(character) in _REMOVED_CATEGORIES
    )


def normalize_text(description: str) -> str:
    """Return NFC text without invisible characters and with single spaces. Idempotent."""
    if not isinstance(description, str):
        raise TypeError(f"normalize_text needs text, got {type(description).__name__}")
    kept = "".join(character for character in description if not _is_removed(character))
    words = [word for word in kept.split() if word.strip(_JOINERS)]
    # Last, as removing a character can leave a letter next to the accent that belongs to it.
    return unicodedata.normalize("NFC", " ".join(words))
