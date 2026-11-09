"""GET /v1/runs/{run_id}/export: the stored run as JSON or CSV. Deterministic bytes, and nothing is re-validated."""

import csv
import hashlib
import io
import json

import pytest
from conftest import create_ruleset, validate
from expense_validator import ExpenseValidator

from expense_api.services.runs import CSV_COLUMNS


def _export(client, run, **params):
    return client.get(f"/v1/runs/{run['run_id']}/export", params={**params})


def _rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8"), newline="")))


BRIEF = ["receipt_id", "disposition", "line_items", "exceptions", "review_signals", "metadata"]


def test_the_json_export_is_the_run_with_each_receipt_in_the_briefs_shape_in_canonical_form(findings_run):
    client, _, run = findings_run
    response = _export(client, run, format="json")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == f'attachment; filename="{run["run_id"]}.json"'
    assert (response.headers["x-content-type-options"], response.headers["cache-control"]) == ("nosniff", "no-store")
    body = json.loads(response.content)
    assert body["export_schema"] == "run-export/2"
    assert {key: value for key, value in body.items() if key not in ("export_schema", "receipts")} == {key: value for key, value in run.items() if key != "results"}
    assert [record["receipt_id"] for record in body["receipts"]] == [result["receipt_id"] for result in run["results"]["results"]]
    for record, result in zip(body["receipts"], run["results"]["results"]):
        assert list(record) == BRIEF
        assert [list(line) for line in record["line_items"]] == [["line_id", "description_raw", "category", "restricted", "confidence"]] * len(result["line_items"])
        assert [list(item) for item in record["exceptions"]] == [["rule_id", "policy_doc", "severity", "line_ids", "evidence", "explanation"]] * len(result["exceptions"])
        assert (record["metadata"]["decided"], record["metadata"]["open_questions"]) == (result["decided"], result["open_questions"])  # nothing is lost: it is in metadata
    assert response.content == (json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")  # compact, in the fields' own order
    assert _export(client, run).content == response.content  # json is the default


def test_the_csv_export_has_one_row_per_finding_with_its_receipt_and_rule(findings_run):
    client, _, run = findings_run
    response = _export(client, run, format="csv")
    assert response.status_code == 200 and response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="{run["run_id"]}.csv"'
    assert response.content.decode("utf-8").split("\r\n")[0] == ",".join(CSV_COLUMNS)
    rows = _rows(response)
    findings = client.get(f"/v1/runs/{run['run_id']}/findings", params={"limit": 100}).json()["items"]
    assert [row["finding_id"] for row in rows] == [item["finding_id"] for item in findings]
    for row, item in zip(rows, findings):
        assert (row["receipt_id"], row["kind"], row["rule_id"], row["severity"]) == (item["receipt_id"], item["kind"], item["rule_id"] or "", item["severity"] or "")
        assert json.loads(row["record"]) == item["record"]  # nested evidence, whole, as canonical JSON
        assert json.loads(row["disposition_reasons"]) == item["disposition_reasons"]
        assert (json.loads(row["line_ids"]) if row["line_ids"] else None) == item["line_ids"]
        assert (row["run_id"], row["ruleset_id"], row["ruleset_sha256"]) == (run["run_id"], run["ruleset"]["ruleset_id"], run["ruleset"]["artifact_sha256"])
        assert (row["ruleset_version"], row["ruleset_label"], row["classification_backend"]) == (str(run["ruleset"]["version"]), "v1", "chat_completions")
    doc_03 = next(row for row in rows if row["rule_id"] == "DOC-03")
    assert (doc_03["line_ids"], doc_03["policy_doc"]) == ("[]", "receipt-documentation-requirements.md")  # a receipt-level target
    uncertainty = next(row for row in rows if row["kind"] == "uncertainty")
    assert (uncertainty["rule_id"], uncertainty["field"], uncertainty["reason"]) == ("", "recipient_type", "secondary_field_uncertain")


def test_exports_are_byte_for_byte_repeatable(findings_run):
    client, _, run = findings_run
    for fmt in ("json", "csv"):
        first, second = _export(client, run, format=fmt).content, _export(client, run, format=fmt).content
        assert first == second and hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_unicode_is_kept_in_utf_8(findings_run):
    client, _, run = findings_run
    japanese = validate(client, run["ruleset"]["ruleset_id"], receipt_ids=("RCP-019",)).json()
    for exported, text in ((run, "—"), (japanese, "インチ")):
        rows = _rows(_export(client, exported, format="csv"))
        assert any(text in cell for row in rows for cell in row.values())
        assert text in _export(client, exported, format="json").content.decode("utf-8")  # not escaped as \\u sequences


def test_csv_cells_are_quoted_and_cannot_run_as_formulas(findings_run):
    client, mongo, run = findings_run
    runs = mongo["expense_api"]["runs"]
    stored = runs.find_one({"_id": run["run_id"]})
    results = json.loads(stored["results_json"])
    # Text the core passes through from receipts: a formula, a leading minus and an at-sign, a comma, quotes and a line break.
    planted = ['=HYPERLINK("http://example.test","x")', "-2+3", "@SUM(A1)", 'Lunch, "client", day 1\nday 2']
    exceptions = [exception for result in results["results"] for exception in result["exceptions"]]
    assert len(exceptions) == len(planted)  # GIFT-02, ALC-03, DOC-03, HOUSE-01
    for exception, text in zip(exceptions, planted):
        exception["explanation"] = text
    changed = json.dumps(results)
    runs.update_one({"_id": run["run_id"]}, {"$set": {"results_json": changed, "results_sha256": hashlib.sha256(changed.encode()).hexdigest()}})

    rows = _rows(_export(client, run, format="csv"))
    explanations = [row["explanation"] for row in rows if row["kind"] == "exception"]
    assert "'=HYPERLINK(\"http://example.test\",\"x\")" in explanations and "'-2+3" in explanations
    assert 'Lunch, "client", day 1\nday 2' in explanations  # quoted and round-tripped, line break and all
    assert sorted(json.loads(row["record"])["explanation"] for row in rows if row["kind"] == "exception") == sorted(planted)  # the record column keeps the exact text
    assert not [row for row in rows for column in CSV_COLUMNS if row[column].startswith(("=", "+", "-", "@"))]


def test_a_run_with_no_findings_exports_only_the_header(client):
    run = validate(client, create_ruleset(client)["ruleset_id"]).json()  # RCP-001 passes
    assert _export(client, run, format="csv").content == (",".join(CSV_COLUMNS) + "\r\n").encode("utf-8")
    assert json.loads(_export(client, run, format="json").content)["receipts"][0]["exceptions"] == []


def test_an_export_is_of_a_known_run_and_a_known_format(findings_run):
    client, _, run = findings_run
    unknown = {**run, "run_id": "run_000000000000000000000000"}
    for fmt in ("json", "csv"):
        response = _export(client, unknown, format=fmt)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "run_not_found")
    for fmt in ("xlsx", "JSON", ""):
        response = _export(client, run, format=fmt)
        assert (response.status_code, response.json()["error"]["code"]) == (422, "unsupported_export_format")


def test_exporting_calls_no_validator_and_no_model(make_client):
    built = []

    def build(settings):
        built.append(settings.mode)
        return ExpenseValidator(settings)

    client = make_client(build_validator=build)
    run = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-014",)).json()
    assert built == ["replay"]
    for fmt in ("json", "csv"):
        assert _export(client, run, format=fmt).status_code == 200
    client.get(f"/v1/runs/{run['run_id']}/findings")
    assert built == ["replay"]  # the stored result is read; nothing ran again (and the network guard allows no call)


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_no_secret_reaches_an_export(make_client, settings, monkeypatch, fmt):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-never-exported")
    client = make_client()
    run = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-014",)).json()
    assert b"sk-or-never-exported" not in _export(client, run, format=fmt).content
