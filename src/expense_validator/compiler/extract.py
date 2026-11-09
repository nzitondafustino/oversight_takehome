"""Extract rule clauses and regex-readable fields while retaining surrounding text."""

import re
from dataclasses import dataclass
from decimal import Decimal

CLAUSE = re.compile(r"^\*\*([A-Z]+-\d{2}) — (.+?)\.\*\*\s*(.*)$")
RULE_LIKE = re.compile(r"^\*\*[A-Z]+-\d")
NOTE = re.compile(r"^\*\*[^*]+\.\*\*")
HEADING = re.compile(r"^(#+) (.+)$")

RULE_ID = re.compile(r"\b[A-Z]+-\d{2}\b")
SEVERITY = re.compile(r"\*\*(critical|high|medium|low)\*\*")
REVIEW_SIGNAL = "**review signal**"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
DIGITS = r"\d[\d,]*(?:\.\d+)?"
NUMBER = rf"(?:{DIGITS}|{'|'.join(NUMBER_WORDS)})"
THRESHOLDS = [
    (re.compile(rf"\$({DIGITS})"), "usd"),
    (re.compile(rf"\bEUR ({DIGITS})"), "eur"),
    (re.compile(rf"\b({NUMBER})(?:\s+or\s+more)?\s+nights?\b", re.IGNORECASE), "nights"),
    (re.compile(rf"\b({NUMBER})\s+hours?\b", re.IGNORECASE), "hour"),
    (re.compile(r"\b(\d{2}):\d{2}\b"), "hour"),
    (re.compile(rf"\battendee\s+count\s+of\s+({NUMBER})\b", re.IGNORECASE), "count"),
    (re.compile(rf"\b({NUMBER})\s+or\s+more\s+receipts\b", re.IGNORECASE), "count"),
]
# A number mentioned in a clause. "one" is left out: it is far more often "one recipient" than a limit.
NUMBER_MENTION = re.compile(r"\d[\d,]*(?:\.\d+)?|\b(?:two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)
# Numbers that name something rather than limit it: a merchant category code, such as "MCC 5813" or "MCCs 5813 and 5921".
IDENTIFIERS = re.compile(r"\bMCCs?\s+\d{4}(?:\s*(?:,|or|and)\s*\d{4})*\b")
# "those three" after a list of rules counts the rules just named, as in "replaces ALC-01, ALC-02 and ALC-03: report
# REG-01 instead of those three". Followed by a noun such as "receipts", it is a quantity and stays a possible limit.
RULE_COUNT = re.compile(rf"\b(?:those|these|the|all)\s+({'|'.join(NUMBER_WORDS)})\b(?=\s*(?:rules?\b|[.,;:)]|$))", re.IGNORECASE)


@dataclass(frozen=True)
class Threshold:
    value: Decimal
    unit: str


@dataclass(frozen=True)
class Clause:
    rule_id: str
    title: str
    policy_doc: str
    section: str | None
    text: str
    severities: list[str]
    review_signal: bool
    thresholds: list[Threshold]
    references: list[str]


@dataclass(frozen=True)
class PolicyDocument:
    policy_doc: str
    title: str | None
    clauses: list[Clause]
    context: list[str]


def _unique(values: list) -> list:
    return list(dict.fromkeys(values))


def _number(written: str) -> Decimal:
    return Decimal(NUMBER_WORDS.get(written.lower(), written.replace(",", "")))


def thresholds_in(text: str) -> list[Threshold]:
    """Return the limits a text states, each read in its unit's own form, such as $200 or four nights."""
    found = [
        (match.start(), Threshold(_number(match.group(1)), unit))
        for pattern, unit in THRESHOLDS
        for match in pattern.finditer(text)
    ]
    return _unique([threshold for _, threshold in sorted(found, key=lambda pair: pair[0])])


def possible_limits(text: str) -> list[tuple[int, int]]:
    """Return the spans of the numbers in a clause that could state a limit.

    A rule ID, a merchant category code, and a count of the rules the clause names ("those three" after three rule
    IDs) are not limits, so a clause holding only those has nothing for a reviewer to confirm.
    """
    rules_named = len(set(RULE_ID.findall(text)))

    def blank(match: re.Match) -> str:  # blanked, not removed, so every span is an offset in the original text
        return " " * len(match.group(0))

    def blank_rule_count(match: re.Match) -> str:
        return blank(match) if NUMBER_WORDS[match.group(1).lower()] <= rules_named else match.group(0)

    plain = RULE_COUNT.sub(blank_rule_count, IDENTIFIERS.sub(blank, RULE_ID.sub(blank, text)))
    return [match.span() for match in NUMBER_MENTION.finditer(plain)]


def _clause(heading: re.Match, lines: list[str], policy_doc: str, section: str | None) -> Clause:
    rule_id, title, first_line = heading.groups()
    text = "\n".join([first_line, *lines]).strip()
    return Clause(
        rule_id=rule_id,
        title=title,
        policy_doc=policy_doc,
        section=section,
        text=text,
        severities=_unique(SEVERITY.findall(text)),
        review_signal=REVIEW_SIGNAL in text,
        thresholds=thresholds_in(text),
        references=[found for found in _unique(RULE_ID.findall(text)) if found != rule_id],
    )


def extract_document(text: str, policy_doc: str) -> PolicyDocument:
    """Extract the rule clauses of one policy document.

    Raises:
        ValueError: If a rule heading is malformed or a rule ID repeats.
    """
    title = None
    section = None
    blocks: list[tuple[re.Match | None, str | None, list[str]]] = []
    current = None
    after_blank = True

    for number, line in enumerate(text.splitlines(), start=1):
        heading = HEADING.match(line)
        clause = CLAUSE.match(line)
        if heading:
            current = None
            if len(heading.group(1)) == 1 and title is None:
                title = heading.group(2)
            else:
                section = heading.group(2)
        elif clause:
            current = (clause, section, [])
            blocks.append(current)
        elif RULE_LIKE.match(line):
            raise ValueError(f"{policy_doc}:{number}: malformed rule heading: {line}")
        else:
            ends_clause = current is not None and current[0] is not None and after_blank and NOTE.match(line)
            if line.strip() and (current is None or ends_clause):
                current = (None, None, [])
                blocks.append(current)
            if current is not None:
                current[2].append(line)
        after_blank = not line.strip()

    clauses = [_clause(match, lines, policy_doc, where) for match, where, lines in blocks if match]
    ids = [clause.rule_id for clause in clauses]
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        raise ValueError(f"{policy_doc}: duplicate rule ids: {duplicates}")
    context = ["\n".join(lines).strip() for match, _, lines in blocks if match is None]
    return PolicyDocument(policy_doc=policy_doc, title=title, clauses=clauses, context=context)
