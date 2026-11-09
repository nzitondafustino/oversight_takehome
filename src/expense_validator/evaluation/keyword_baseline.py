"""A keyword matcher over restricted terms: the simpler system the validator is meant to replace, as a baseline.

It reads only a line's raw description: no translation, no model, no merchant code, no answers. A line is
restricted when a term of a restricted category occurs in it as whole words, case-insensitively; its category is
the first such category in TERMS order (more specific first: a gift card before a gift, a smart watch before a
watch). A line no term matches is predicted not restricted, with no category: the baseline knows only restricted
terms, so its category predictions are comparable on the restricted categories alone.

Where the terms come from, so that no answer shaped them:
- each category's "Typical examples" in data/category-taxonomy.md, and
- the items each clause lists in data/policy-docs (PERS-01, PERS-02, PERS-03, HOUSE-01, GIFT-02, ENT-02, TECH-01),
- plus, marked below, common names for the taxonomy's "beer, wine, spirits", as any keyword list would hold.
Written from those sources, not from the receipts. The author had read parts of the receipts while building the
validator, so this is a baseline written in good faith, not one written blind.
"""

import re
from collections.abc import Mapping, Sequence

from expense_validator.evaluation.metrics import classification_scores

KEYWORD_BASELINE_VERSION = "keyword-baseline/1"

# Category: terms. Plurals are matched by an optional trailing s or es.
TERMS: dict[str, tuple[str, ...]] = {
    "gift_card": ("gift card", "prepaid card", "voucher", "cash equivalent"),
    "personal_electronics": (
        "headphone", "earbud", "smart watch", "smartwatch", "fitness tracker", "tablet", "e-reader", "games console", "game console",
        "camera", "drone", "television", "portable speaker", "item tracker", "screen protector", "charger",
    ),
    "alcohol": (
        "beer", "wine", "spirits", "cocktail", "corkage",
        # common names for beer, wine and spirits, beyond the taxonomy's own words
        "liquor", "lager", "ale", "vodka", "whisky", "whiskey", "bourbon", "gin", "rum", "tequila", "champagne", "prosecco", "brandy", "cider", "sake",
    ),
    "tobacco": ("cigarette", "cigar", "tobacco", "vape", "vaping", "e-liquid"),
    "gambling": ("casino", "casino chip", "betting", "bet", "lottery", "wager"),
    "jewelry": ("jewelry", "jewellery", "bracelet", "necklace", "precious metal", "watch"),
    "personal_care": ("toiletries", "toiletry", "cosmetics", "razor", "haircut", "salon", "spa", "massage"),
    "clothing": ("clothing", "apparel", "footwear", "shoe", "belt", "tie", "bag", "fashion accessory"),
    "household_goods": (
        "cleaning supplies", "laundry products", "paper towel", "toilet paper", "tissues", "kitchenware", "drinkware", "bedding",
        "furniture", "small appliance", "home décor", "home decor",
    ),
    "gift": ("flowers", "gift basket", "greeting card", "branded merchandise", "gift"),
}

PATTERNS: dict[str, re.Pattern] = {
    category: re.compile(r"\b(" + "|".join(re.escape(term) + r"(?:e?s)?" for term in sorted(terms, key=len, reverse=True)) + r")\b", re.IGNORECASE)
    for category, terms in TERMS.items()
}


def classify(description: str | None) -> dict:
    """The baseline's reading of one line: its category or None, whether it is restricted, and the words matched."""
    for category, pattern in PATTERNS.items():
        found = pattern.search(description or "")
        if found:
            return {"category": category, "restricted": True, "matched": found.group(0)}
    return {"category": None, "restricted": False, "matched": None}


def compare(lines: Sequence[Mapping]) -> dict:
    """Score the main engine and the baseline on the same lines, with the same functions and denominators.

    Args:
        lines: Each {line_id, receipt_id, description, gold_category, gold_restricted, category, restricted}: the
            answers and the main engine's prediction (None where it gave none or failed, which counts as wrong).
    """
    main = [{**line, "confidence": None} for line in lines]
    baseline = [{**line, **{name: value for name, value in classify(line["description"]).items() if name != "matched"}, "confidence": None} for line in lines]
    restricted_categories = set(TERMS)
    comparable = lambda scored: [line for line in scored if line["gold_category"] in restricted_categories]
    outcome = {"both_right": [], "main_only": [], "baseline_only": [], "both_wrong": []}
    for line, ours, theirs in zip(lines, main, baseline):
        right = (ours["restricted"] is line["gold_restricted"], theirs["restricted"] is line["gold_restricted"])
        key = {(True, True): "both_right", (True, False): "main_only", (False, True): "baseline_only", (False, False): "both_wrong"}[right]
        outcome[key].append({"receipt_id": line["receipt_id"], "line_id": line["line_id"], "description": line["description"], "gold_category": line["gold_category"],
                             "gold_restricted": line["gold_restricted"], "main": {"category": ours["category"], "restricted": ours["restricted"]},
                             "baseline": {"category": theirs["category"], "restricted": theirs["restricted"], "matched": classify(line["description"])["matched"]}})
    scores = {name: classification_scores(scored) for name, scored in (("main", main), ("baseline", baseline))}
    restricted_only = {name: classification_scores(comparable(scored)) if comparable(scored) else None for name, scored in (("main", main), ("baseline", baseline))}
    return {
        "version": KEYWORD_BASELINE_VERSION,
        "lines": len(lines),
        "restricted": {name: {**scores[name]["restricted"], "matrix": scores[name]["restricted_matrix"]["rows_gold_columns_predicted"]} for name in scores},
        # Category scores over the lines whose answer is a restricted category: the only categories the baseline names.
        "restricted_categories": {name: None if value is None else {"lines": value["lines"], "accuracy": value["accuracy"], "macro": value["macro"]} for name, value in restricted_only.items()},
        "difference": {name: _difference(scores["main"]["restricted"][name], scores["baseline"]["restricted"][name]) for name in ("precision", "recall", "f1")},
        "lines_by_outcome": {key: len(value) for key, value in outcome.items()},
        "outcomes": outcome,
        "not_comparable": {"findings": "the baseline flags restricted terms on lines; it raises no policy rule finding", "disposition": "the baseline decides no receipt disposition"},
    }


def _difference(main: float | None, baseline: float | None) -> float | None:
    return None if main is None or baseline is None else round(main - baseline, 6)
