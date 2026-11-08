"""Read records stored before the single-company migration, which still name a customer_id.

The current core schemas refuse that field, so a stored core result that holds it cannot be read as stored.
Callers verify the stored bytes against their hash first; only then is the obsolete field left out of what is
read and returned, at the places such records hold it. Nothing is written back: stored text, hashes, approvals
and audit events stay exactly as they were, and no decision is recomputed.
"""

OBSOLETE = "customer_id"


def without_scope(record: dict) -> dict:
    """One stored object without the obsolete field. Every other key and value is kept."""
    return {key: value for key, value in record.items() if key != OBSOLETE}


def is_legacy_extraction(data: dict) -> bool:
    """Whether a stored ExtractionResult was written with customer scope, which the core's model now refuses."""
    agreement = data.get("agreement")
    return OBSOLETE in data or (isinstance(agreement, dict) and OBSOLETE in agreement) or any(isinstance(item, dict) and OBSOLETE in item for item in data.get("comparisons", []))


def extraction_without_scope(data: dict) -> dict:
    """A stored ExtractionResult as the current core reads it: the result, its agreement and each comparison."""
    current = without_scope(data)
    if isinstance(current.get("agreement"), dict):
        current["agreement"] = without_scope(current["agreement"])
    current["comparisons"] = [without_scope(item) if isinstance(item, dict) else item for item in current.get("comparisons", [])]
    return current


def decisions_without_scope(decisions: list[dict] | None) -> list[dict] | None:
    """Stored ReviewDecision records, as a response shows them."""
    return None if decisions is None else [without_scope(item) if isinstance(item, dict) else item for item in decisions]
