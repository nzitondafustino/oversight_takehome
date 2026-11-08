"""Bind reviewer approval to exact agreement and candidate hashes.

Changed readings or executable rules invalidate approval. Simulation approval
never authorizes production publication.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from expense_validator.compiler.agreement import output_sha256
from expense_validator.compiler.llm_extract import RECORDING_KINDS


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_a_legacy_customer(cls, data):
        # Approvals saved before customer_id was removed still carry it. Their output then holds it too, so its
        # hash never matches an output built now: such an approval is read, and refused as no longer covering.
        if isinstance(data, dict) and "customer_id" in data:
            data = {name: value for name, value in data.items() if name != "customer_id"}
        return data

    approved_by: str = Field(pattern=r"\S")
    approved_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")  # ISO 8601, from the caller
    note: str = ""
    reading_kind: str  # simulation or model, copied from the approved output for anyone reading the record
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ruleset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: dict


def approve(output: dict, ruleset_sha256: str | None, approved_by: str, approved_at: str, note: str = "") -> Approval:
    """Approve an agreement output and candidate ruleset.

    Raises:
        ValueError: If the output is not ready or the candidate did not validate.
    """
    if not output["ready_for_approval"]:
        summary = output["summary"]
        raise ValueError(
            f"not ready for approval: {summary['rules_needing_review']} rules need review, "
            f"{len(summary['resolution_errors'])} resolutions were refused, {summary['reading_errors']} readings failed"
        )
    if ruleset_sha256 is None:
        raise ValueError("not ready for approval: there is no valid candidate ruleset")
    if output["reading_kind"] not in RECORDING_KINDS:
        raise ValueError(f"the output's reading_kind must be one of {RECORDING_KINDS}")
    return Approval(
        approved_by=approved_by,
        approved_at=approved_at,
        note=note,
        reading_kind=output["reading_kind"],
        output_sha256=output["output_sha256"],
        ruleset_sha256=ruleset_sha256,
        output=output,
    )


def save_approval(approval: Approval, path: Path) -> None:
    """Write an approval record once.

    Raises:
        FileExistsError: If the path already exists.
    """
    if path.exists():
        raise FileExistsError(f"{path} exists, and an approval is never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(approval.model_dump_json(indent=2) + "\n")


def load_approval(path: Path) -> Approval:
    return Approval.model_validate(json.loads(path.read_text()))


def why_approval_fails(approval: Approval, current_output: dict, current_ruleset_sha256: str | None) -> str | None:
    """Return why an approval does not cover the current output and ruleset, or None."""
    held = approval.output
    if output_sha256(held) != approval.output_sha256 or held.get("reading_kind") != approval.reading_kind:
        return "the approval record does not match the output it holds"
    if approval.output_sha256 != current_output["output_sha256"]:
        return "the agreement output has changed since it was approved"
    if approval.ruleset_sha256 != current_ruleset_sha256:
        return "the candidate ruleset has changed since it was approved"
    return None


def approval_holds(approval: Approval, current_output: dict, current_ruleset_sha256: str | None) -> bool:
    return why_approval_fails(approval, current_output, current_ruleset_sha256) is None
