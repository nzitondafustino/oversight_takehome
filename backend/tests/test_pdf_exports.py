"""Parse real PDF exports of stored replay results; no provider calls."""
import copy
from io import BytesIO
from pypdf import PdfReader
from conftest import create_ruleset, validate
from evaluation_support import Workbench, dataset
from expense_api.services.rulesets import now


def read_pdf(response):
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert ".pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(response.content))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_validation_pdf_contains_all_receipts_and_findings(client, monkeypatch):
    version = create_ruleset(client)
    run = validate(client, version["ruleset_id"], receipt_ids=("RCP-001", "RCP-011")).json()
    from expense_validator import ExpenseValidator
    def no_validation(*args, **kwargs):
        raise AssertionError("export must not rerun validation")
    monkeypatch.setattr(ExpenseValidator, "validate_batch", no_validation)
    text = read_pdf(client.get(f"/v1/runs/{run['run_id']}/export?format=pdf"))
    assert "Northwind Global" in text
    assert "RCP-001" in text and "RCP-011" in text
    assert "MCC-01" in text and "Open questions" in text
    assert client.get(f"/v1/runs/{run['run_id']}").json() == run


def test_evaluation_pdf_exports_selected_metrics_and_results(make_client, settings):
    bench = Workbench(make_client, settings)
    identifier = bench.run()["evaluation_id"]
    original = bench.database["evaluations"].find_one({"_id": identifier})
    rescored = copy.deepcopy(original["metrics"])
    rescored["schema_version"] = "evaluation-metrics/pdf-test"
    bench.database["evaluations"].update_one({"_id": identifier}, {"$push": {"rescorings": {"schema_version": rescored["schema_version"], "rescored_at": now(), "metrics": rescored}}})
    before = bench.database["evaluations"].find_one({"_id": identifier})
    text = read_pdf(bench.client.get(f"/v1/evaluations/{identifier}/export?format=pdf&scoring=latest"))
    assert "rescored" in text and "Spending and latency" in text
    assert "Classification" in text and "RCP-001" in text
    assert "original" in read_pdf(bench.client.get(f"/v1/evaluations/{identifier}/export?format=pdf&scoring=original"))
    assert before == bench.database["evaluations"].find_one({"_id": identifier})


def test_comparison_pdf_keeps_runs_and_warnings(make_client, settings):
    bench = Workbench(make_client, settings)
    first = bench.run(idempotency_key="one")
    bench.dataset = dataset(bench.client, ("RCP-001",), name="comparison-dataset")
    second = bench.run(idempotency_key="two")
    params = [("evaluation_ids", first["evaluation_id"]), ("evaluation_ids", second["evaluation_id"])]
    text = read_pdf(bench.client.get("/v1/evaluations/compare/export", params=params))
    assert first["evaluation_id"] in text and second["evaluation_id"] in text
    assert "Comparability" in text and "context" in text.lower() and "baseline" in text
    assert bench.client.get("/v1/evaluations/compare/export", params=params[:1]).status_code == 422
    assert bench.client.get("/v1/evaluations/compare/export", params=[params[0], params[0]]).status_code == 422


def test_pdf_export_preserves_guards(make_client, settings):
    bench = Workbench(make_client, settings)
    identifier = bench.queue()
    assert bench.client.get(f"/v1/evaluations/{identifier}/export?format=pdf").status_code == 409
    assert bench.client.get("/v1/runs/run_missing/export?format=pdf").status_code == 404
    assert bench.get(identifier)["status"] == "queued"


def test_pdf_wraps_long_untrusted_text_and_preserves_accented_names():
    from expense_api.services.pdf_reports import Report
    report = Report("Receipt text")
    report.section("Merchant", {"name": "Café São Paulo & <b>literal</b>", "notes": "Long receipt text " * 4000 + "END OF RECEIPT"})
    reader = PdfReader(BytesIO(report.finish()))
    assert len(reader.pages) > 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Café São Paulo" in text and "<b>literal</b>" in text
    assert "END OF RECEIPT" in " ".join(text.split())
