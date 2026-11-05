"""Deterministic clean-up of printed text. It changes how text is encoded, never what it says.

NFC and not NFKC: NFKC would rewrite full-width characters, ligatures and symbols such as
"½", which are what the receipt printed. Spelling is left alone too, so "Cafe" stays "Cafe".
"""

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
    """Text in NFC, with invisible characters gone and every run of whitespace one plain space.

    Pure and idempotent. Text with nothing visible in it comes back empty.
    """
    if not isinstance(description, str):
        raise TypeError(f"normalize_text needs text, got {type(description).__name__}")
    kept = "".join(character for character in description if not _is_removed(character))
    words = [word for word in kept.split() if word.strip(_JOINERS)]
    # Last, as removing a character can leave a letter next to the accent that belongs to it.
    return unicodedata.normalize("NFC", " ".join(words))
