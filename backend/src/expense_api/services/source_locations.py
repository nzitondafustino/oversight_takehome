"""Where a compared field's source is, in the exact revision the compiler read, verified rather than guessed.

A comparison's `evidence` is its clause's text and `clause_hash` the SHA-256 of that text. A clause is located only
when: the revision's text, read from storage, matches both its own stored hash and the hash the compiler recorded;
the evidence matches its clause hash; and the evidence occurs exactly once in the revision, word for word. Then its
lines are given, and within them the values the two readings disagree on, where each occurs as a whole number or
phrase. Anything less is reported as unverified, with why: no position is ever approximated.
"""

import hashlib
import re
from typing import Any

from expense_validator.compiler.extract import NUMBER_WORDS, possible_limits


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _phrases(field: str, value: Any) -> set[str]:
    """The literal texts a reading's value could appear as in the clause. None for values with no literal form."""
    if value is None or isinstance(value, bool):
        return set()
    if field == "thresholds" and isinstance(value, list):
        return {str(item.get("value")) for item in value if isinstance(item, dict) and item.get("value") not in (None, "")}
    if field in ("severities", "review_signal", "rule"):
        return set()  # labels the policy states in its own words, never as these tokens
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str) and len(item.strip()) >= 2}
    if isinstance(value, str) and len(value.strip()) >= 2:
        return {value}
    return set()


def _pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase)
    if re.fullmatch(r"\d+(?:\.\d+)?", phrase):
        # 75 never matches inside 750 or 75.50; 2 also matches "two", as the compiler reads "Two or more receipts".
        words = [word for word, number in NUMBER_WORDS.items() if str(number) == phrase]
        spelled = "".join(rf"|\b{word}\b" for word in words)
        return re.compile(rf"(?<![\d.]){escaped}(?![\d]|\.\d){spelled}", re.IGNORECASE)
    return re.compile(escaped)


def _spans(content: str, start: int, end: int, pattern: re.Pattern) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in pattern.finditer(content, start, end) if "\n" not in match.group(0)]


def _position(content: str, offset: int) -> tuple[int, int]:
    """1-based line and 0-based column of an offset."""
    line = content.count("\n", 0, offset) + 1
    return line, offset - (content.rfind("\n", 0, offset) + 1)


def locate(content: str, stored_sha256: str, recorded_sha256: str | None, comparison) -> dict:
    """The verified location of one comparison's clause and the disputed values in it, or why there is none."""
    result: dict[str, Any] = {"revision_verified": False, "clause_verified": False, "location": None, "heading_line": None, "highlights": [], "reason": None}
    actual = _sha256(content)
    if actual != stored_sha256 or (recorded_sha256 is not None and actual != recorded_sha256):
        result["reason"] = "This revision's text is not the text the compiler read, so no position in it is shown."
        return result
    result["revision_verified"] = True
    location = comparison.location or {}
    line = location.get("line") if isinstance(location, dict) else None
    lines = content.split("\n")
    if isinstance(line, int) and 1 <= line <= len(lines) and lines[line - 1].startswith(f"**{comparison.rule_id} — "):
        result["heading_line"] = line  # the rule's own heading, checked against the text
    evidence = comparison.evidence
    if evidence is None:
        result["reason"] = "The compiler located no clause for this field."
        return result
    if comparison.clause_hash != _sha256(evidence):
        result["reason"] = "The quoted clause does not match its recorded hash, so its position is not shown."
        return result
    result["clause_verified"] = True
    count = content.count(evidence)
    if count != 1:
        result["reason"] = "The clause is not found word for word in this revision." if count == 0 else "The clause occurs more than once in this revision, so its position is ambiguous."
        return result
    start = content.index(evidence)
    end = start + len(evidence)
    from_line, _ = _position(content, start)
    to_line, _ = _position(content, end)
    result["location"] = {"from_line": from_line, "to_line": to_line}
    result["highlights"] = _highlights(content, start, end, comparison)
    return result


def _highlights(content: str, start: int, end: int, comparison) -> list[dict]:
    """Each place in the clause a disputed value occurs: a value one reading has and the other does not, or, for an
    unconfirmed field, each number in the clause that could state a limit."""
    regex, llm = _phrases(comparison.field, comparison.regex_value), _phrases(comparison.field, comparison.llm_value)
    wanted: dict[str, str] = {}
    if comparison.status == "unconfirmed":
        clause = content[start:end]
        for begin, finish in possible_limits(clause):  # a rule ID, a merchant code or a count of rules is never one
            wanted.setdefault(clause[begin:finish], "clause")
    elif comparison.status != "agreed":
        for phrase in regex - llm:
            wanted[phrase] = "regex"
        for phrase in llm - regex:
            wanted[phrase] = "llm"
    found = []
    for phrase, side in sorted(wanted.items()):
        for begin, finish in _spans(content, start, end, _pattern(phrase)):
            line, column = _position(content, begin)
            found.append({"line": line, "start": column, "end": column + (finish - begin), "text": content[begin:finish], "side": side})
    return sorted(found, key=lambda item: (item["line"], item["start"]))

