"""Receipt ingestion: validated receipts for the pipeline, with answers kept apart for scoring."""

from expense_validator.ingestion.loader import ingest_record, iter_jsonl, iter_jsonl_lines
from expense_validator.ingestion.models import (
    ExpectedException,
    ExpectedSignal,
    GroundTruth,
    IngestedReceipt,
    IngestionError,
    IngestionMode,
    LineLabels,
)

__all__ = [
    "ExpectedException",
    "ExpectedSignal",
    "GroundTruth",
    "IngestedReceipt",
    "IngestionError",
    "IngestionMode",
    "LineLabels",
    "ingest_record",
    "iter_jsonl",
    "iter_jsonl_lines",
]
