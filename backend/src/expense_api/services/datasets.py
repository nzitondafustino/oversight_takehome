"""Evaluation datasets: YAML, JSON or JSONL uploads, normalized into one canonical form and stored immutably.

A case is one receipt in the core's raw format (data/README.md), with optional answers inline, as
data/receipts.jsonl holds them: receipt-level `labels` (`disposition`, and `expected_exceptions` with
`review_signals`) and line-level `labels` (`category`, `restricted`). Any of them may be left out, so a dataset
can be unlabelled or partly labelled. The answers are split off here and never reach the validator. The receipt
is checked as the validator will check it: by the core's ingestion in production mode, which refuses any answer
left on it, and then by the core's admission, which refuses amounts that contradict each other.

- JSON and YAML: one object, `{"name", "description", "context_complete", "receipts": [case, ...]}`. Every key
  but `receipts` is optional, and the form fields give or confirm them.
- JSONL: one complete case per non-empty line. The form fields give the name and description.

The canonical form is JSON with sorted keys and exact decimals. It and the original bytes are stored with their
SHA-256, and a stored version never changes: uploading changed content under the same name makes a new version.
"""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

import yaml
from bson import Binary, ObjectId
from expense_validator import ValidatorSettings
from expense_validator.classification.core.common import load_taxonomy
from expense_validator.compiler.validate import read_fields
from expense_validator.engine.inputs import admit_receipt
from expense_validator.ingestion import IngestionError, IngestionMode, ingest_record
from expense_validator.policy.schema import RULE_ID_PATTERN
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from expense_api.api.json_body import parse_json
from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories.evaluations import DatasetRepository
from expense_api.services.rulesets import now, sha256

SCHEMA_VERSION = "evaluation-dataset/1"
FORMATS = {".yaml": "yaml", ".yml": "yaml", ".json": "json", ".jsonl": "jsonl"}
MAX_ERRORS = 20  # a malformed upload reports at most this many problems


class DatasetInvalid(Exception):
    def __init__(self, problems: list[dict]):
        super().__init__(f"{len(problems)} problems")
        self.problems = problems


# --- Answers, as a case may give them. Every field is optional, so labels can be partial. ---


class _Labels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LineLabels(_Labels):
    category: str | None = Field(default=None, min_length=1)
    restricted: StrictBool | None = None


class ExpectedException(_Labels):
    rule_id: str = Field(pattern=RULE_ID_PATTERN)
    severity: Literal["critical", "high", "medium", "low"]
    line_ids: list[str]  # empty for a receipt-level exception, as the core allows
    policy_doc: str | None = None
    reason: str | None = None


class ExpectedSignal(_Labels):
    rule_id: str = Field(pattern=RULE_ID_PATTERN)
    policy_doc: str | None = None
    reason: str | None = None


class ReceiptLabels(_Labels):
    disposition: Literal["flag", "review", "pass"] | None = None
    expected_exceptions: list[ExpectedException] | None = None
    review_signals: list[ExpectedSignal] | None = None


# --- Reading each format ---


class DataLoader(yaml.SafeLoader):
    """A safe loader with JSON's scalars: no YAML 1.1 surprises (`NO` as false, `1:30` as 90, bare timestamps),
    numbers with a fraction as exact Decimals, and no aliases or repeated keys. Tags beyond the safe set are
    refused by SafeLoader itself, so no object is ever constructed from a tag."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.composer.ComposerError(None, None, "aliases are not allowed", self.peek_event().start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"repeated key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


DataLoader.yaml_implicit_resolvers = {}
for _tag, _pattern, _first in (
    ("null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
    ("bool", r"^(?:true|True|TRUE|false|False|FALSE)$", list("tTfF")),
    ("int", r"^[-+]?(?:0|[1-9][0-9]*)$", list("-+0123456789")),
    ("float", r"^[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$", list("-+0123456789")),
):
    DataLoader.add_implicit_resolver(f"tag:yaml.org,2002:{_tag}", re.compile(_pattern), _first)


def _decimal(loader: DataLoader, node) -> Decimal:
    try:
        value = Decimal(loader.construct_scalar(node))
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite():
        raise yaml.constructor.ConstructorError(None, None, "not a finite number", node.start_mark)
    return value


DataLoader.add_constructor("tag:yaml.org,2002:float", _decimal)


def _check_plain(value: object, path: str) -> None:
    """Refuse anything JSON could not hold, such as a date or bytes from an explicit YAML tag."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DatasetInvalid([{"location": path or "document", "message": "every key must be text"}])
            _check_plain(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_plain(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, bool, int, Decimal)):
        raise DatasetInvalid([{"location": path or "document", "message": f"{type(value).__name__} is not a JSON value"}])


def _read_yaml(raw: bytes) -> tuple[object, list[int | None]]:
    """The document, and the line each case starts on."""
    try:
        loader = DataLoader(raw.decode("utf-8"))
        try:
            node = loader.get_single_node()
            document = None if node is None else loader.construct_document(node)
        finally:
            loader.dispose()
    except UnicodeDecodeError:
        raise DatasetInvalid([{"location": "file", "message": "the file is not UTF-8"}]) from None
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark or error.context_mark
        location = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "file"
        raise DatasetInvalid([{"location": location, "message": f"invalid YAML: {error.problem or error.context}"}]) from None
    except yaml.YAMLError as error:
        raise DatasetInvalid([{"location": "file", "message": f"invalid YAML: {type(error).__name__}"}]) from None
    lines: list[int | None] = []
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if key.value == "receipts" and isinstance(value, yaml.SequenceNode):
                lines = [item.start_mark.line + 1 for item in value.value]
    return document, lines


def _read_json(raw: bytes) -> object:
    try:
        return parse_json(raw)
    except UnicodeDecodeError:
        raise DatasetInvalid([{"location": "file", "message": "the file is not UTF-8"}]) from None
    except json.JSONDecodeError as error:
        raise DatasetInvalid([{"location": f"line {error.lineno}, column {error.colno}", "message": f"invalid JSON: {error.msg}"}]) from None
    except RecursionError:
        raise DatasetInvalid([{"location": "file", "message": "invalid JSON: nested too deeply"}]) from None


def _read_jsonl(raw: bytes) -> tuple[list[tuple[str, object]], list[dict]]:
    cases, problems = [], []
    for number, line in enumerate(raw.split(b"\n"), start=1):
        if not line.strip():
            continue
        try:
            cases.append((f"line {number}", parse_json(line)))
        except UnicodeDecodeError:
            problems.append({"location": f"line {number}", "message": "the line is not UTF-8"})
        except json.JSONDecodeError as error:
            problems.append({"location": f"line {number}, column {error.colno}", "message": f"invalid JSON: {error.msg}"})
        except RecursionError:
            problems.append({"location": f"line {number}", "message": "invalid JSON: nested too deeply"})
    return cases, problems


# --- Normalizing ---

METADATA_KEYS = {"name", "description", "context_complete", "receipts"}
# Files written before customer_id was removed may name one. It is accepted and ignored: it decides nothing now.
LEGACY_KEYS = {"customer_id"}


def parse_dataset(raw: bytes, format: str, *, name: str | None, description: str | None, context_complete: bool | None, catalog: tuple[set[str], dict], max_receipts: int) -> dict:
    """Return the canonical dataset, or raise DatasetInvalid listing what is wrong and where."""
    metadata: dict = {}
    if format == "jsonl":
        located, problems = _read_jsonl(raw)
        if problems:
            raise DatasetInvalid(problems[:MAX_ERRORS])
    else:
        if format == "yaml":
            document, lines = _read_yaml(raw)
        else:
            document, lines = _read_json(raw), []
        if not isinstance(document, dict):
            raise DatasetInvalid([{"location": "document", "message": 'a JSON or YAML dataset is one object with "receipts": a list of cases'}])
        _check_plain(document, "")
        unknown = sorted(set(document) - METADATA_KEYS - LEGACY_KEYS)
        if unknown:
            raise DatasetInvalid([{"location": "document", "message": f"unknown keys: {unknown}"}])
        if not isinstance(document.get("receipts"), list):
            raise DatasetInvalid([{"location": "receipts", "message": "must be a list of cases"}])
        metadata = document
        located = [(_case_location(index, lines), case) for index, case in enumerate(document["receipts"])]
    name = _agreed("name", name, metadata.get("name"))
    description = _agreed("description", description, metadata.get("description"))
    context_complete = _agreed("context_complete", context_complete, metadata.get("context_complete"))
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise DatasetInvalid([{"location": "name", "message": "a dataset name is 1 to 64 letters, digits, '.', '_' or '-', given in the form or the file"}])
    if description is not None and (not isinstance(description, str) or len(description) > 2000):
        raise DatasetInvalid([{"location": "description", "message": "at most 2000 characters of text"}])
    if context_complete is not None and not isinstance(context_complete, bool):
        raise DatasetInvalid([{"location": "context_complete", "message": "must be true or false"}])
    if not located:
        raise DatasetInvalid([{"location": "receipts", "message": "a dataset needs at least one receipt"}])
    if len(located) > max_receipts:
        raise DatasetInvalid([{"location": "receipts", "message": f"at most {max_receipts} receipts per dataset, not {len(located)}"}])
    cases, problems = _cases(located, catalog)
    if problems:
        raise DatasetInvalid(problems[:MAX_ERRORS])
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": description,
        # Whether the receipts are every one that could matter to a rule spanning receipts, such as TIME-03.
        # True unless the dataset says otherwise: a dataset is evaluated as one whole context.
        "context_complete": True if context_complete is None else context_complete,
        "cases": cases,
    }


def _case_location(index: int, lines: list[int | None]) -> str:
    return f"receipts[{index}]" + (f" (line {lines[index]})" if index < len(lines) and lines[index] else "")


def _agreed(field: str, given: object, in_file: object) -> object:
    if given is not None and in_file is not None and given != in_file:
        raise DatasetInvalid([{"location": field, "message": f"the form and the file give different values for {field}"}])
    return given if given is not None else in_file


def _cases(located: list[tuple[str, object]], catalog: tuple[set[str], dict]) -> tuple[list[dict], list[dict]]:
    cases, problems = [], []
    receipt_ids: dict[str, str] = {}
    line_ids: dict[str, str] = {}
    for location, record in located:
        try:
            case = _case(record, catalog)
        except DatasetInvalid as invalid:
            problems.extend({"location": f"{location}: {item['location']}" if item["location"] else location, "message": item["message"]} for item in invalid.problems)
            continue
        receipt = case["receipt"]
        if receipt["receipt_id"] in receipt_ids:
            problems.append({"location": location, "message": f"receipt_id repeats, first at {receipt_ids[receipt['receipt_id']]}"})
            continue
        repeated = [line["line_id"] for line in receipt["line_items"] if line["line_id"] in line_ids]
        if repeated:
            problems.append({"location": location, "message": f"line_id repeats from an earlier receipt: {repeated}"})
            continue
        receipt_ids[receipt["receipt_id"]] = location
        line_ids.update(dict.fromkeys((line["line_id"] for line in receipt["line_items"]), location))
        cases.append(case)
    return cases, problems


def _case(record: object, catalog: tuple[set[str], dict]) -> dict:
    """Split one case into the receipt the validator reads and the answers kept for scoring."""
    categories, fields = catalog
    if not isinstance(record, dict):
        raise DatasetInvalid([{"location": "", "message": "a case must be an object"}])
    _check_plain(record, "")
    lines = record.get("line_items")
    if not isinstance(lines, list) or not all(isinstance(line, dict) for line in lines):
        raise DatasetInvalid([{"location": "line_items", "message": "must be a list of objects"}])
    receipt = {key: value for key, value in record.items() if key not in ("labels", "notes")}
    receipt["line_items"] = [{key: value for key, value in line.items() if key != "labels"} for line in lines]
    try:
        admit_receipt(ingest_record(receipt, IngestionMode.PRODUCTION).receipt, fields)  # the core's own contract
    except IngestionError as error:
        raise DatasetInvalid([{"location": "", "message": error.reason}]) from None
    except ValueError as error:  # admission: amounts, catalog values, entity
        raise DatasetInvalid([{"location": "", "message": str(error).splitlines()[0]}]) from None
    labels = _labels(ReceiptLabels, record.get("labels"), "labels")
    expected_lines = {}
    for index, line in enumerate(lines):
        label = _labels(LineLabels, line.get("labels"), f"line_items[{index}].labels")
        if label.category is not None and label.category not in categories:
            raise DatasetInvalid([{"location": f"line_items[{index}].labels.category", "message": "not a category in the taxonomy"}])
        if label.category is not None or label.restricted is not None:
            expected_lines[line["line_id"]] = label.model_dump()
    if (labels.expected_exceptions is None) != (labels.review_signals is None):
        raise DatasetInvalid([{"location": "labels", "message": "give expected_exceptions and review_signals together, or neither: findings are labelled as a whole"}])
    findings = None
    if labels.expected_exceptions is not None:
        known = {line["line_id"] for line in receipt["line_items"]}
        for index, item in enumerate(labels.expected_exceptions):
            if not set(item.line_ids) <= known:
                raise DatasetInvalid([{"location": f"labels.expected_exceptions[{index}].line_ids", "message": "names a line that is not on this receipt"}])
        findings = {
            "exceptions": sorted(({"rule_id": item.rule_id, "severity": item.severity, "line_ids": sorted(item.line_ids)} for item in labels.expected_exceptions), key=lambda item: item["rule_id"]),
            "signals": sorted({item.rule_id for item in labels.review_signals}),
        }
        rules = [item["rule_id"] for item in findings["exceptions"]]
        if len(rules) != len(set(rules)):
            raise DatasetInvalid([{"location": "labels.expected_exceptions", "message": "one entry per rule, listing all its lines"}])
    notes = record.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise DatasetInvalid([{"location": "notes", "message": "must be text"}])
    return {"receipt": receipt, "expected": {"disposition": labels.disposition, "findings": findings, "lines": expected_lines}, "notes": notes}


def _labels(model: type[BaseModel], value: object, location: str):
    if value is None:
        return model()
    try:
        return model.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        path = ".".join(str(part) for part in first["loc"])
        raise DatasetInvalid([{"location": f"{location}.{path}" if path else location, "message": first["msg"]}]) from None


def canonical_text(value: object) -> str:
    """Canonical JSON: sorted keys, no spaces, and every Decimal written exactly as it was read."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(key, ensure_ascii=False)}:{canonical_text(value[key])}" for key in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical_text(item) for item in value) + "]"
    if isinstance(value, Decimal):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def statistics(dataset: dict) -> dict:
    cases = dataset["cases"]
    return {
        "receipts": len(cases),
        "lines": sum(len(case["receipt"]["line_items"]) for case in cases),
        "labelled": {
            "receipts_with_disposition": sum(1 for case in cases if case["expected"]["disposition"] is not None),
            "receipts_with_findings": sum(1 for case in cases if case["expected"]["findings"] is not None),
            "lines_with_category": sum(1 for case in cases for label in case["expected"]["lines"].values() if label["category"] is not None),
        },
    }


class DatasetService:
    def __init__(self, settings: Settings, repository: DatasetRepository):
        self.settings, self.repository = settings, repository

    def create(self, *, filename: str, raw: bytes, name: str | None, description: str | None, context_complete: bool | None) -> tuple[dict, bool]:
        """Store a new immutable version, or return the version already holding exactly these bytes.

        Returns the record and whether it was created.
        """
        format = FORMATS.get(_extension(filename))
        if format is None:
            raise ApiError(415, "unsupported_dataset_format", "upload a .yaml, .yml, .json or .jsonl file", {"supported": sorted(FORMATS)})
        try:
            dataset = parse_dataset(raw, format, name=name, description=description, context_complete=context_complete,
                                    catalog=self._catalog(), max_receipts=self.settings.max_dataset_receipts)
        except DatasetInvalid as invalid:
            raise ApiError(422, "invalid_dataset", "the dataset is not valid", invalid.problems) from None
        text = canonical_text(dataset)
        original_sha256 = _sha256_bytes(raw)
        existing = self.repository.find_upload(dataset["name"], original_sha256)
        if existing is not None:
            return existing, False  # the same bytes again: the same version, so a retried upload makes no duplicate
        record = {
            "_id": f"dataset_{ObjectId()}",
            "name": dataset["name"],
            "description": dataset["description"],
            "context_complete": dataset["context_complete"],
            "schema_version": SCHEMA_VERSION,
            "format": format,
            "filename": filename,
            "original": Binary(raw),
            "original_sha256": original_sha256,
            "canonical_json": text,
            "canonical_sha256": sha256(text),
            "stats": statistics(dataset),
            "created_at": now(),
        }
        return self.repository.create(record), True

    def get(self, dataset_id: str, *, with_content: bool = False) -> dict:
        record = self.repository.get(dataset_id, with_content=with_content)
        if record is None:
            raise ApiError(404, "dataset_not_found", f"no dataset {dataset_id}")
        return record

    def page(self, name: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        return self.repository.page(name, limit, offset)

    def _catalog(self) -> tuple[set[str], dict]:
        """The taxonomy's categories and the field catalog, as the deployment's validator reads them."""
        core = ValidatorSettings.from_config(self.settings.core_config, mode="deterministic")
        return set(load_taxonomy(core.taxonomy_path)), read_fields(core.fields_path.read_text())


def load_canonical(record: dict) -> dict:
    """The stored canonical dataset, refused when it no longer matches its hash."""
    text = record["canonical_json"]
    if sha256(text) != record["canonical_sha256"]:
        raise ApiError(500, "integrity_error", f"dataset {record['_id']} does not match its stored hash")
    return parse_json(text.encode())


def _extension(filename: str) -> str:
    match = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return match.group(0).lower() if match else ""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
