"""GET /v1/runs/{run_id}/findings: the stored core result, one finding per core record, filtered and paged."""

import pytest
from conftest import case_ids, create_ruleset, load_cases, validate

CASES = load_cases("run_findings")
INVALID = load_cases("run_findings", "invalid")
RECORD_LISTS = {"exception": "exceptions", "review_signal": "review_signals", "open_question": "open_questions", "uncertainty": "uncertainty"}


def _findings(client, run, **params):
    return client.get(f"/v1/runs/{run['run_id']}/findings", params={**params})


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_findings_filter_and_page_in_a_fixed_order(findings_run, case):
    client, _, run = findings_run
    response = _findings(client, run, **case["params"])
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["finding_id"] for item in page["items"]] == case["expect"]
    assert page["total"] == case.get("total", len(case["expect"]))


@pytest.mark.parametrize("case", INVALID, ids=case_ids(INVALID))
def test_an_invalid_findings_filter_is_refused(findings_run, case):
    client, _, run = findings_run
    response = _findings(client, run, **case["params"])
    assert (response.status_code, response.json()["error"]["code"]) == (422, case["code"])


def test_each_finding_is_the_cores_record_unchanged(findings_run):
    client, _, run = findings_run
    items = _findings(client, run, limit=100).json()["items"]
    results = {result["receipt_id"]: result for result in run["results"]["results"]}
    for item in items:
        receipt_id, kind, index = item["finding_id"].split(":")
        assert item["record"] == results[receipt_id][RECORD_LISTS[kind]][int(index)]  # evidence, quote, reasons: all of it
        assert (item["kind"], item["receipt_id"], item["receipt_disposition"], item["receipt_decided"]) == (kind, receipt_id, results[receipt_id]["disposition"], results[receipt_id]["decided"])
    # Every core record appears once: nothing merged, nothing dropped.
    assert len(items) == sum(len(result[field]) for result in results.values() for field in RECORD_LISTS.values())


def test_targets_severity_and_rules_are_only_what_the_core_says(findings_run):
    client, _, run = findings_run
    items = {item["finding_id"]: item for item in _findings(client, run, limit=100).json()["items"]}
    receipt_level = items["RCP-014:exception:1"]  # DOC-03: a receipt finding that names no line
    assert (receipt_level["rule_id"], receipt_level["severity"], receipt_level["line_ids"]) == ("DOC-03", "medium", [])
    assert items["RCP-014:exception:2"]["line_ids"] == ["RCP-014-L1", "RCP-014-L2", "RCP-014-L3"]
    signal = items["RCP-013:review_signal:1"]
    assert (signal["rule_id"], signal["severity"], signal["line_ids"]) == ("EXC-03", None, None)
    uncertainty = items["RCP-013:uncertainty:0"]
    assert (uncertainty["rule_id"], uncertainty["severity"], uncertainty["line_ids"]) == (None, None, ["RCP-013-L1"])
    assert uncertainty["record"]["reason"] == "secondary_field_uncertain" and uncertainty["disposition_reasons"] == []
    gift = items["RCP-013:exception:0"]
    rcp_013 = next(result for result in run["results"]["results"] if result["receipt_id"] == "RCP-013")
    assert gift["disposition_reasons"] == [{"code": "exception_flags", "basis": "EXC-01", "rule_ids": ["GIFT-02"]}]
    assert gift["disposition_reasons"] == [reason for reason in rcp_013["disposition_reasons"] if "GIFT-02" in reason["rule_ids"]]
    assert gift["record"]["policy_doc"] == "gifts-and-awards-policy.md" and gift["record"]["evidence"] and gift["record"]["source_quote"]


def test_a_run_with_nothing_found_has_no_findings(client):
    run = validate(client, create_ruleset(client)["ruleset_id"]).json()  # RCP-001 passes
    assert _findings(client, run).json() == {"run_id": run["run_id"], "items": [], "total": 0, "limit": 50, "offset": 0}


def test_an_unknown_run_has_no_findings(findings_run):
    client, _, _ = findings_run
    response = client.get("/v1/runs/run_000000000000000000000000/findings")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "run_not_found")


@pytest.mark.parametrize("damage", ["edited", "unreadable", "missing"])
def test_a_damaged_stored_result_is_an_error_never_an_empty_page(findings_run, damage):
    client, mongo, run = findings_run
    runs = mongo["expense_api"]["runs"]
    stored = runs.find_one({"_id": run["run_id"]})
    changes = {
        "edited": {"$set": {"results_json": stored["results_json"].replace("GIFT-02", "GIFT-09")}},  # caught by the hash
        "unreadable": {"$set": {"results_json": "{\"results\": []"}, "$unset": {"results_sha256": ""}},  # a run without a hash: caught on reading
        "missing": {"$unset": {"results_json": ""}},
    }[damage]
    runs.update_one({"_id": run["run_id"]}, changes)
    for path in ("findings", "export"):
        response = client.get(f"/v1/runs/{run['run_id']}/{path}")
        assert (response.status_code, response.json()["error"]["code"]) == (500, "corrupt_run")
    response = client.get(f"/v1/runs/{run['run_id']}")
    assert (response.status_code, response.json()["error"]["code"]) == (500, "corrupt_run")
