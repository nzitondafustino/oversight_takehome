"""Part 3's quality metrics on a real replayed run of the 32 golden receipts, through the API and the worker:
exception detection per rule, evidence, the disposition matrix, the keyword baseline, error analysis and rule
coverage, and how a run scored with an earlier scoring version is rescored without losing its original scores."""

import json

import pytest
from conftest import REPLAY_SETS
from evaluation_support import Workbench
from fastapi.testclient import TestClient

from expense_api.config import Settings
from expense_api.main import create_app
from expense_api.services.evaluation_metrics import METRICS_SCHEMA_VERSION


@pytest.fixture(scope="module")
def golden(core_config, tmp_path_factory):
    settings = Settings(core_config=core_config, artifact_dir=tmp_path_factory.mktemp("artifacts"), replay_sets=REPLAY_SETS, default_replay_set="openrouter", allow_preview_validation=True)
    clients = []

    def make_client(app_settings, mongo):
        client = TestClient(create_app(app_settings, mongo_client=mongo), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    bench = Workbench(make_client, settings, receipt_ids=None)
    evaluation_id = bench.queue(label="golden")
    bench.drain()
    yield bench, evaluation_id
    for client in clients:
        client.__exit__(None, None, None)


def test_a_finished_run_holds_every_part_3_metric_with_its_definition(golden):
    bench, evaluation_id = golden
    metrics = bench.get(evaluation_id, "/metrics")
    assert metrics["schema_version"] == METRICS_SCHEMA_VERSION and metrics["scored"]["kind"] == "original"
    assert set(metrics["scoring"]["definitions"]) == {"classification", "restricted", "disposition", "findings", "evidence", "baseline"}
    classification = metrics["classification"]
    assert classification["lines"] == sum(row["tp"] + row["fn"] for row in classification["per_category"].values())  # every labelled line, whatever the count
    matrix = classification["restricted_matrix"]["rows_gold_columns_predicted"]
    assert matrix["true"]["true"] == classification["restricted"]["tp"] and sum(sum(row.values()) for row in matrix.values()) == classification["restricted"]["lines"]
    disposition = metrics["disposition"]["matrix_with_not_evaluated"]
    assert sum(disposition["row_totals"].values()) == metrics["disposition"]["receipts"] == disposition["correct"] + disposition["incorrect"]


def test_findings_are_scored_rule_by_rule_and_untested_rules_are_never_perfect(golden):
    bench, evaluation_id = golden
    findings = bench.get(evaluation_id, "/metrics")["findings"]
    per_rule = findings["per_rule"]
    assert findings["micro"]["tp"] + findings["micro"]["fn"] == findings["expected"] == sum(row["expected"] for row in per_rule.values())
    assert findings["micro"]["tp"] + findings["micro"]["fp"] == findings["predicted"]
    for rule_id, row in per_rule.items():
        if row["status"] == "not_tested":  # every ruleset rule is listed; one with nothing to score has no score
            assert (row["precision"], row["recall"], row["f1"]) == (None, None, None), rule_id
        if row["status"] == "tested":
            assert row["recall"] is not None
    assert findings["rules_by_status"]["not_tested"], "the golden data has rules with no positive case"
    assert findings["macro"]["rules"] == len(findings["rules_by_status"]["tested"])


def test_the_keyword_baseline_is_scored_on_the_same_lines_and_both_sides_wins_are_shown(golden):
    bench, evaluation_id = golden
    metrics = bench.get(evaluation_id, "/metrics")
    baseline = metrics["baseline"]
    assert baseline["available"] and baseline["version"] == "keyword-baseline/1" and baseline["lines"] == metrics["classification"]["restricted"]["lines"]
    assert baseline["restricted"]["main"] == {**{key: metrics["classification"]["restricted"][key] for key in ("tp", "fp", "fn", "precision", "recall", "f1")}, "matrix": metrics["classification"]["restricted_matrix"]["rows_gold_columns_predicted"]}
    assert sum(baseline["lines_by_outcome"].values()) == baseline["lines"]
    assert baseline["outcomes"]["main_only"], "the baseline misses restricted lines the classifier reads, such as untranslated ones"
    assert baseline["not_comparable"]["disposition"]


def test_rule_coverage_separates_running_from_positive_cases(golden):
    bench, evaluation_id = golden
    coverage = bench.get(evaluation_id, "/metrics")["coverage"]
    assert coverage["available"] and coverage["receipts"] == 32
    exercised, positive = set(coverage["exercised"]), set(coverage["with_positive_cases"])
    assert positive - exercised == set(), "a rule with a positive case met its subject"
    assert exercised - positive, "some rules run on the golden data and never meet a positive case"
    assert coverage["per_rule"]["MCC-03"]["execution"] == "never_runs"  # loaded, never exercised


def test_an_earlier_scoring_is_kept_and_a_rescore_is_stored_beside_it(golden):
    bench, evaluation_id = golden
    jobs = bench.database["evaluations"]
    stored = jobs.find_one({"_id": evaluation_id})
    original = {**stored["metrics"], "schema_version": "evaluation-metrics/1"}  # as a run scored before version 2 holds it
    jobs.update_one({"_id": evaluation_id}, {"$set": {"metrics": original, "metrics_schema_version": "evaluation-metrics/1"}, "$unset": {"rescorings": ""}})
    before = bench.get(evaluation_id, "/metrics")
    assert before["scored"] == {"kind": "original", "original_schema_version": "evaluation-metrics/1", "current_schema_version": METRICS_SCHEMA_VERSION, "rescorings": []}
    rescored = bench.client.post(f"/v1/evaluations/{evaluation_id}/rescore").json()
    assert rescored["scored"]["kind"] == "rescored" and rescored["schema_version"] == METRICS_SCHEMA_VERSION
    # Recomputed from the stored results, the scores are the same: scoring is reproducible.
    strip = lambda metrics: {key: value for key, value in metrics.items() if key not in ("scored", "schema_version", "evaluation_id", "status", "provisional")}
    assert json.dumps(strip(rescored), sort_keys=True) == json.dumps(strip(stored["metrics"]), sort_keys=True)
    # The original stays as it was, and asking again stores nothing new.
    assert bench.get(evaluation_id, "/metrics?scoring=original")["schema_version"] == "evaluation-metrics/1"
    again = bench.client.post(f"/v1/evaluations/{evaluation_id}/rescore").json()
    assert again["scored"]["rescorings"] == rescored["scored"]["rescorings"] and len(jobs.find_one({"_id": evaluation_id})["rescorings"]) == 1
    assert bench.client.post(f"/v1/evaluations/{bench.queue(label='queued')}/rescore").json()["error"]["code"] == "evaluation_not_finished"
