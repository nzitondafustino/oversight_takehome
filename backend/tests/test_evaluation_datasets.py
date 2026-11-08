"""Evaluation datasets: YAML, JSON and JSONL into one canonical form, refused uploads, and immutable versions."""

import copy
import json

import mongomock
import pytest
import yaml
from conftest import case_ids, load_cases
from evaluation_support import golden_lines, upload

CASES = load_cases("evaluation_uploads")


def _serialize(case: dict) -> bytes:
    if "content" in case:
        return case["content"].encode(case.get("encoding", "utf-8"))
    records = []
    golden = {json.loads(line)["receipt_id"]: json.loads(line) for line in golden_lines()}
    for item in case["receipts"]:
        record = copy.deepcopy(golden[item["id"]])
        record.update(item.get("receipt", {}))
        record["line_items"][0].update(item.get("line", {}))
        for name in item.get("remove", []):
            del record[name]
        if "labels" in item:
            record["labels"] = item["labels"]
        records.append(record)
    if case["filename"].endswith(".jsonl"):
        return ("\n".join(json.dumps(record) for record in records) + "\n").encode()
    document = {**case.get("document", {}), "receipts": records}
    if case["filename"].endswith(".json"):
        return json.dumps(document).encode()
    text = yaml.safe_dump(document, sort_keys=True)
    for quoted in case.get("yaml_unquote", []):
        text = text.replace(quoted, quoted.strip("'"))
    return text.encode()


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_a_refused_upload_says_where_and_stores_nothing(make_client, settings, case):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(app_settings=settings.model_copy(update=case.get("settings", {})), mongo=mongo)
    form = {"name": "golden", **case.get("form", {})}
    if case["filename"].endswith((".json", ".yaml")) and "name" not in case.get("form", {}):
        form.pop("name")  # the file names it
    response = upload(client, _serialize(case), filename=case["filename"], **form)
    body = response.json()
    assert (response.status_code, body["error"]["code"]) == (case["expect"]["status"], case["expect"]["code"]), response.text
    if "location" in case["expect"] or "message" in case["expect"]:
        first = body["error"]["details"][0]
        assert case["expect"].get("location", "") in first["location"], first
        assert case["expect"].get("message", "") in first["message"], first
    assert mongo["expense_api"]["evaluation_datasets"].count_documents({}) == 0


def _formats() -> dict[str, bytes]:
    """The same labelled receipts written in each format the API accepts."""
    records = [json.loads(line) for line in golden_lines()]
    document = {"name": "golden", "description": "The project's 32 labelled receipts", "receipts": records}
    return {
        "golden.jsonl": ("\n".join(golden_lines()) + "\n").encode(),
        "golden.json": json.dumps(document, indent=2).encode(),
        "golden.yaml": yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode(),
        "golden.yml": yaml.safe_dump(document, default_flow_style=True, allow_unicode=True).encode(),
    }


def test_every_format_normalizes_to_the_same_canonical_dataset(client):
    stored = {}
    for filename, content in _formats().items():
        response = upload(client, content, filename=filename, description="The project's 32 labelled receipts" if filename.endswith(".jsonl") else None)
        assert response.status_code == 201, response.text
        stored[filename] = response.json()
    assert len({record["canonical_sha256"] for record in stored.values()}) == 1
    assert len({record["original_sha256"] for record in stored.values()}) == len(stored)  # the originals differ, and each is kept
    assert {record["format"] for record in stored.values()} == {"jsonl", "json", "yaml"}
    record = stored["golden.yaml"]
    assert record["stats"] == {"receipts": 32, "lines": 79, "labelled": {"receipts_with_disposition": 32, "receipts_with_findings": 32, "lines_with_category": 79}}


def test_the_canonical_dataset_keeps_exact_amounts_and_keeps_answers_apart(client):
    record = upload(client, _formats()["golden.yaml"], filename="golden.yaml").json()
    detail = client.get(f"/v1/evaluations/datasets/{record['dataset_id']}", params={"include_cases": True}).json()
    first = detail["cases"][0]
    original = json.loads(golden_lines()[0])
    assert first["receipt"]["total_amount"] == original["total_amount"] and isinstance(first["receipt"]["total_amount"], float)
    assert "labels" not in first["receipt"] and all("labels" not in line for line in first["receipt"]["line_items"])
    assert first["expected"] == {"disposition": "pass", "findings": {"exceptions": [], "signals": []}, "lines": {line["line_id"]: line["labels"] for line in original["line_items"]}}
    assert first["notes"] == original["notes"]


def test_unlabelled_and_partly_labelled_datasets_are_accepted(client):
    records = [json.loads(line) for line in golden_lines(("RCP-001", "RCP-002"))]
    unlabelled = copy.deepcopy(records[0])
    del unlabelled["labels"], unlabelled["notes"]
    for line in unlabelled["line_items"]:
        del line["labels"]
    partial = copy.deepcopy(records[1])
    partial["labels"] = {"disposition": partial["labels"]["disposition"]}  # no findings
    del partial["line_items"][0]["labels"]
    partial["line_items"][1]["labels"] = {"category": partial["line_items"][1]["labels"]["category"]}  # no restricted flag
    response = upload(client, "\n".join(json.dumps(record) for record in (unlabelled, partial)), name="partial")
    assert response.status_code == 201, response.text
    assert response.json()["stats"]["labelled"] == {"receipts_with_disposition": 1, "receipts_with_findings": 0, "lines_with_category": 2}
    cases = client.get(f"/v1/evaluations/datasets/{response.json()['dataset_id']}", params={"include_cases": True}).json()["cases"]
    assert cases[0]["expected"] == {"disposition": None, "findings": None, "lines": {}}
    assert cases[1]["expected"]["findings"] is None and partial["line_items"][0]["line_id"] not in cases[1]["expected"]["lines"]


def test_versions_are_immutable_and_the_same_bytes_are_one_version(client, settings):
    first = upload(client, "\n".join(golden_lines(("RCP-001",))))
    assert first.status_code == 201 and first.json()["version"] == 1
    again = upload(client, "\n".join(golden_lines(("RCP-001",))))
    assert again.status_code == 200 and again.json() == first.json()  # a retried upload makes no duplicate
    changed = upload(client, "\n".join(golden_lines(("RCP-001", "RCP-002"))))
    assert changed.status_code == 201 and changed.json()["version"] == 2 and changed.json()["dataset_id"] != first.json()["dataset_id"]
    stored = client.get(f"/v1/evaluations/datasets/{first.json()['dataset_id']}").json()
    assert stored["canonical_sha256"] == first.json()["canonical_sha256"] and stored["stats"]["receipts"] == 1  # version 1 is unchanged
    page = client.get("/v1/evaluations/datasets", params={"name": "golden"}).json()
    assert [item["version"] for item in page["items"]] == [2, 1] and page["total"] == 2


def test_an_unknown_dataset_is_not_found(client):
    upload(client, "\n".join(golden_lines(("RCP-001",))))
    response = client.get("/v1/evaluations/datasets/dataset_000000000000000000000000")
    assert response.status_code == 404 and response.json()["error"]["code"] == "dataset_not_found"
    assert client.get("/v1/evaluations/datasets").json()["total"] == 1


def test_the_original_upload_is_kept_byte_for_byte(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    content = _formats()["golden.yml"]
    record = upload(client, content, filename="golden.yml").json()
    stored = mongo["expense_api"]["evaluation_datasets"].find_one({"_id": record["dataset_id"]})
    assert bytes(stored["original"]) == content


def test_a_file_written_when_datasets_named_a_customer_still_uploads(client):
    """Such a file may name a customer_id. It decides nothing now, so it is accepted and not kept."""
    document = {"name": "legacy", "customer_id": "acme", "receipts": [json.loads(line) for line in golden_lines(("RCP-001",))]}
    response = upload(client, json.dumps(document), filename="legacy.json")
    assert response.status_code == 201, response.text
    assert "customer_id" not in response.json()
