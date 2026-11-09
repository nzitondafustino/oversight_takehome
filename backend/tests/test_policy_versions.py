"""A policy amended between versions: what is a policy change, what is a mismatch, and what an earlier decision or
approval still covers. Documents are revised through the API; each revision's reading is an authored simulation
reading of the revised text (the recorded fixture reading, with the thresholds the revised text states)."""

import hashlib
import json
import shutil

import mongomock
import pytest
from conftest import COMPILER_RECORDINGS, DECISIONS, POLICY_DOCUMENTS, extract, upload_documents

FIXTURES = COMPILER_RECORDINGS["fixtures"]
TRANSACTIONS, REGIONAL = "transaction-pattern-rules.md", "regional-addendum.md"
SPLIT = (TRANSACTIONS, "Two or more receipts", "Three or more receipts")
UNRELATED = (TRANSACTIONS, "Transaction times are recorded in local time", "All transaction times are recorded in local time")
REWORDED = (REGIONAL, "report REG-01 instead of those three", "report REG-01 in place of those three")
NEW_CLAUSE = (TRANSACTIONS, "TIME-02 covers it.", "TIME-02 covers it.\n\n**TIME-05 — Large weekend purchase.** A receipt over $500 dated on a weekend raises a **review signal**.")
TIME_05_READING = {"rule_id": "TIME-05", "title": "Large weekend purchase", "section": None, "severities": [], "review_signal": True, "thresholds": [{"value": "500", "unit": "usd"}], "references": [], "quote": "A receipt over $500 dated on a weekend"}
ADD_TIME_05 = {"rule_id": "TIME-05", "add": True, "rationale": "the revised policy adds TIME-05",
               "set": {"outcome": "review_signal", "scope": "receipt", "source_quote": "A receipt over $500 dated on a weekend",
                       "params": {"total_usd": {"type": "number", "value": "500", "unit": "usd"}}, "applies_when": {"op": "gt", "field": "receipt.total_amount_usd", "value": {"param": "total_usd"}}}}
THREE = {"rule_id": "TIME-03", "set": {"params": {"min_receipts": {"type": "number", "value": "3", "unit": "count"}}}, "rationale": "the policy now asks for three receipts"}
TEXT = {document["filename"]: document["content"] for document in POLICY_DOCUMENTS}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _revised(change) -> str:
    filename, old, new = change
    assert old in TEXT[filename]
    return TEXT[filename].replace(old, new, 1)


def _record(folder, change, thresholds=None, added=None):
    """An authored reading of a revised document: its recorded reading, with TIME-03's thresholds if given, and a new rule's."""
    entry = json.loads((FIXTURES / f"{_sha(TEXT[change[0]])}.json").read_text())
    response = json.loads(entry["response"].replace(change[1], change[2]))  # a quote of the revised words reads them as revised
    response["rules"] += [added] if added else []
    for rule in response["rules"]:
        if thresholds is not None and rule["rule_id"] == "TIME-03":
            rule["thresholds"] = thresholds
    text = _revised(change)
    entry |= {"document_sha256": _sha(text), "origin": "authored in a test: the reading of a revised document", "response": json.dumps(response, ensure_ascii=False)}
    (folder / f"{_sha(text)}.json").write_text(json.dumps(entry))


@pytest.fixture
def app(make_client, settings, tmp_path):
    folder = tmp_path / "amended"
    shutil.copytree(FIXTURES, folder)
    _record(folder, SPLIT, [{"value": "3", "unit": "count"}, {"value": "50", "unit": "usd"}, {"value": "75", "unit": "usd"}])
    _record(folder, UNRELATED)
    _record(folder, REWORDED)
    _record(folder, NEW_CLAUSE, added=TIME_05_READING)
    changed = settings.model_copy(update={"compiler_recording_sets": {**settings.compiler_recording_sets, "amended": folder}, "allowed_publication_modes": frozenset({"production", "simulation"})})
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(app_settings=changed, mongo=mongo)
    client.mongo = mongo
    return client


def _review(app, extraction: dict, decisions: list[dict]) -> dict:
    body = {"actor": "r.eviewer", "extraction_sha256": extraction["extraction_sha256"], "decisions": decisions}
    return app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/review", json=body).json()


def _version(app, extraction: dict, review: dict) -> dict:
    response = app.post("/v1/rulesets", json={"name": "policies", "extraction": {"extraction_id": extraction["extraction_id"], "review_id": review["review_id"]}})
    assert response.status_code == 201, response.text
    return response.json()


def _steps(app, version: dict, steps) -> dict:
    bodies = {"submit": {}, "review": {"actor": "r.eviewer", "decision": "accepted"}, "approve": {"actor": "a.pprover", "mode": "simulation"}, "publish": {}}
    for step in steps:
        response = app.post(f"/v1/rulesets/{version['ruleset_id']}/{step}", json=bodies[step])
        assert response.status_code == 200, response.text
    return response.json()


def _carried(baseline: dict, extraction: dict) -> list[dict]:
    """The published decisions of unchanged fields, saved again for this extraction: what a reviewer's reuse sends."""
    return [{**item["decision"], "comparison_id": item["comparison_id"], "extraction_sha256": extraction["extraction_sha256"]} for item in baseline["decisions"] if item["state"] == "unchanged"]


def _min_receipts(version: dict) -> str:
    return next(rule for rule in version["artifact"]["rules"] if rule["rule_id"] == "TIME-03")["params"]["min_receipts"]["value"]


@pytest.fixture
def v1(app):
    """Version 1, published from revision 1 of every document, where TIME-03 needs two receipts."""
    refs = upload_documents(app)
    ids = {document["filename"]: ref["document_id"] for ref, document in zip(refs, POLICY_DOCUMENTS)}
    extraction = extract(app, refs, recording_set="amended").json()
    wanted = {(item["rule_id"], item["field"]): item for item in DECISIONS["fixtures"]}
    decisions = [{"comparison_id": item["comparison_id"], "extraction_sha256": extraction["extraction_sha256"], "reviewer": "r.eviewer", **{k: v for k, v in wanted[(item["rule_id"], item["field"])].items() if k not in ("rule_id", "field")}} for item in extraction["review_required"]]
    published = _steps(app, _version(app, extraction, _review(app, extraction, decisions)), ("submit", "review", "approve", "publish"))
    return ids, published


def _revise(app, ids: dict, change, **fields) -> dict:
    """Store the change as revision 2 of its document, and compile every document at its latest revision."""
    stored = app.post(f"/v1/documents/{ids[change[0]]}/revisions", json={"content": _revised(change), "based_on_revision": 1})
    assert stored.status_code == 201, stored.text
    refs = [{"document_id": document_id, "revision": 2 if filename == change[0] else 1} for filename, document_id in ids.items()]
    return extract(app, refs, recording_set="amended", **fields)


def test_an_amended_limit_is_a_rule_discrepancy_until_the_draft_rule_states_it(app, v1):
    ids, published = v1
    revised = _revise(app, ids, SPLIT).json()
    assert revised["status"] == "no_candidate"
    detail = app.get(f"/v1/documents/extractions/{revised['extraction_id']}").json()
    # The draft rule logic still checks two receipts: a discrepancy with the revised text, never a reviewer's error.
    [discrepancy] = detail["discrepancies"]
    assert (discrepancy["rule_id"], discrepancy["field"], discrepancy["compiled"]["min_receipts"]) == ("TIME-03", "thresholds", {"value": "2", "unit": "count"})
    assert {"value": "3", "unit": "count"} in discrepancy["stated"]
    refs = [{"document_id": document_id, "revision": 2 if filename == TRANSACTIONS else 1} for filename, document_id in ids.items()]
    # A correction of the draft rule needs who and why.
    unattributed = app.post("/v1/documents/extractions", json={"documents": refs, "recording_set": "amended", "rule_overrides": [THREE]})
    without_reason = app.post("/v1/documents/extractions", json={"documents": refs, "recording_set": "amended", "actor": "r.eviewer", "rule_overrides": [{**THREE, "rationale": None}]})
    assert unattributed.status_code == without_reason.status_code == 422
    corrected = extract(app, refs, recording_set="amended", rule_overrides=[THREE], actor="r.eviewer").json()
    assert corrected["status"] != "no_candidate" and corrected["rule_overrides"] == [{**THREE, "reviewer": "r.eviewer"}]
    # Beside the published version, a proposed change to TIME-03, and the clause that changed.
    baseline = app.get(f"/v1/documents/extractions/{corrected['extraction_id']}/baseline").json()
    assert baseline["published"]["ruleset_id"] == published["ruleset_id"]
    assert baseline["rule_changes"] == [{"rule_id": "TIME-03", "field": "params.min_receipts", "before": {"type": "number", "value": "2", "unit": "count", "source": "policy"}, "after": {"type": "number", "value": "3", "unit": "count", "source": "policy"}}]
    assert baseline["changed_clauses"] == [{"policy_doc": TRANSACTIONS, "rule_id": "TIME-03"}]
    # A correction the revised text does not state is refused, as a discrepancy again.
    four = {**THREE, "set": {"params": {"min_receipts": {"type": "number", "value": "4", "unit": "count"}}}, "rationale": "probe"}
    wrong = app.get(f"/v1/documents/extractions/{extract(app, refs, recording_set='amended', rule_overrides=[four], actor='r.eviewer').json()['extraction_id']}").json()
    assert wrong["status"] == "no_candidate" and wrong["discrepancies"][0]["compiled"]["min_receipts"]["value"] == "4"


def test_an_amendment_is_published_as_a_new_version_and_no_earlier_decision_or_approval_covers_it(app, v1):
    ids, published = v1
    corrected = _revise(app, ids, SPLIT, rule_overrides=[THREE], actor="r.eviewer").json()
    # Version 1's decisions name version 1's extraction: given to this one, none of them applies.
    stale = _review(app, corrected, published["review"]["decisions"])
    assert not stale["ready_for_approval"] and {issue["code"] for issue in stale["issues"]} == {"stale_extraction"}
    # The unchanged fields' published decisions are offered again; saved for this extraction, the compiler accepts them.
    baseline = app.get(f"/v1/documents/extractions/{corrected['extraction_id']}/baseline").json()
    assert {item["state"] for item in baseline["decisions"]} == {"unchanged"}
    assert {item["comparison_id"] for item in baseline["decisions"]} == {item["comparison_id"] for item in corrected["review_required"]}
    draft = _version(app, corrected, _review(app, corrected, _carried(baseline, corrected)))
    assert draft["status"] == "draft" and draft["version"] == published["version"] + 1
    _steps(app, draft, ("submit", "review"))
    # Version 1's approval does not authorize version 2's behaviour.
    earlier = json.loads(app.mongo["expense_api"]["rulesets"].find_one({"_id": published["ruleset_id"]})["approval_json"])
    refused = app.post(f"/v1/rulesets/{draft['ruleset_id']}/approve", json={"actor": "a.pprover", "approval": earlier})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "approval_does_not_hold"
    v2 = _steps(app, draft, ("approve", "publish"))
    v2 = app.get(f"/v1/rulesets/{v2['ruleset_id']}").json()
    assert v2["status"] == "published" and _min_receipts(v2) == "3"
    assert v2["compilation"]["rule_overrides"] == [{**THREE, "reviewer": "r.eviewer"}]  # what changed, who and why
    # Side by side: version 2 against the version published before it, which is version 1.
    changes = app.get(f"/v1/rulesets/{v2['ruleset_id']}/changes").json()
    assert changes["against"]["ruleset_id"] == published["ruleset_id"]
    assert [(item["rule_id"], item["field"], item["before"]["value"], item["after"]["value"]) for item in changes["rule_changes"]] == [("TIME-03", "params.min_receipts", "2", "3")]
    assert app.get(f"/v1/rulesets/{published['ruleset_id']}/changes").json() == {"against": None, "rule_changes": []}
    # Version 1 is unchanged: two receipts, published, with its own decisions.
    again = app.get(f"/v1/rulesets/{published['ruleset_id']}").json()
    assert _min_receipts(again) == "2" and again["status"] == "published" and again["review"] == published["review"]


def test_a_revision_of_unrelated_text_changes_no_rule_and_its_decisions_carry_over(app, v1):
    ids, _ = v1
    revised = _revise(app, ids, UNRELATED).json()
    baseline = app.get(f"/v1/documents/extractions/{revised['extraction_id']}/baseline").json()
    assert baseline["rule_changes"] == [] and baseline["changed_clauses"] == [{"policy_doc": TRANSACTIONS, "rule_id": "TIME-01"}]
    assert {item["state"] for item in baseline["decisions"]} == {"unchanged"} and baseline["decisions"]
    review = _review(app, revised, _carried(baseline, revised))
    assert review["ready_for_approval"], review["issues"]


def test_a_reworded_clause_marks_its_earlier_decision_for_revalidation(app, v1):
    ids, _ = v1
    revised = _revise(app, ids, REWORDED).json()
    baseline = app.get(f"/v1/documents/extractions/{revised['extraction_id']}/baseline").json()
    rule_of = {item["comparison_id"]: item["rule_id"] for item in revised["review_required"]}
    states = {rule_of[item["comparison_id"]]: item["state"] for item in baseline["decisions"]}
    assert states == {"REG-01": "changed", "MCC-03": "unchanged"}
    assert baseline["changed_clauses"] == [{"policy_doc": REGIONAL, "rule_id": "REG-01"}] and baseline["rule_changes"] == []
    # Only the unchanged decision is carried: REG-01 still needs a decision, so the review is not ready.
    review = _review(app, revised, _carried(baseline, revised))
    assert not review["ready_for_approval"] and len(review["remaining"]) == 1



def test_editing_a_published_rule_makes_a_new_draft_and_never_changes_the_published_one(app, v1):
    _, published = v1
    edited = app.post(f"/v1/rulesets/{published['ruleset_id']}/edits", json={"actor": "r.eviewer", "rationale": "clearer name", "changes": [{"rule_id": "ALC-01", "set": {"title": "Alcohol needs an entertainment context"}}]})
    assert edited.status_code == 201, edited.text
    assert edited.json()["status"] == "draft" and edited.json()["edits"]["based_on"] == published["ruleset_id"]
    assert app.get(f"/v1/rulesets/{published['ruleset_id']}").json()["artifact_sha256"] == published["artifact_sha256"]


def test_a_new_compilation_keeps_the_published_corrections_with_who_made_them_when_asked(app, v1):
    ids, _ = v1
    corrected = _revise(app, ids, SPLIT, rule_overrides=[THREE], actor="r.eviewer").json()
    baseline = app.get(f"/v1/documents/extractions/{corrected['extraction_id']}/baseline").json()
    v2 = _steps(app, _version(app, corrected, _review(app, corrected, _carried(baseline, corrected))), ("submit", "review", "approve", "publish"))
    refs = [{"document_id": document_id, "revision": 2 if filename == TRANSACTIONS else 1} for filename, document_id in ids.items()]
    plain = extract(app, refs, recording_set="amended").json()
    assert plain["status"] == "no_candidate"  # without the correction, the rule logic checks two receipts again
    kept = extract(app, refs, recording_set="amended", carry_overrides_from=v2["ruleset_id"]).json()
    assert kept["status"] != "no_candidate" and kept["rule_overrides"] == [{**THREE, "reviewer": "r.eviewer"}]  # the original reviewer, not the caller
    assert kept["provenance"]["rule_overrides_from"] == v2["ruleset_id"]
    draft = _version(app, kept, _review(app, kept, _carried(app.get(f"/v1/documents/extractions/{kept['extraction_id']}/baseline").json(), kept)))
    assert app.post("/v1/documents/extractions", json={"documents": refs, "recording_set": "amended", "carry_overrides_from": draft["ruleset_id"]}).json()["error"]["code"] == "not_published"


def test_a_new_clause_reports_the_rule_it_needs_and_a_reviewer_adds_it_in_a_full_or_incremental_run(app, v1):
    ids, published = v1
    revised = _revise(app, ids, NEW_CLAUSE).json()
    assert revised["status"] == "no_candidate"
    # Not refused before compiling: the build says what the new clause states, for the rule a reviewer writes.
    [needed] = app.get(f"/v1/documents/extractions/{revised['extraction_id']}").json()["discrepancies"]
    assert (needed["rule_id"], needed["field"], needed["compiled"]) == ("TIME-05", "rule", None)
    assert needed["stated"]["thresholds"] == [{"value": "500", "unit": "usd"}] and needed["stated"]["review_signal"] is True
    refs = [{"document_id": document_id, "revision": 2 if filename == TRANSACTIONS else 1} for filename, document_id in ids.items()]
    added = extract(app, refs, recording_set="amended", rule_overrides=[ADD_TIME_05], actor="r.eviewer").json()
    assert added["status"] != "no_candidate" and added["rule_overrides"] == [{**ADD_TIME_05, "reviewer": "r.eviewer"}]
    detail = app.get(f"/v1/documents/extractions/{added['extraction_id']}").json()
    assert detail["discrepancies"] == []
    # An incremental run from the published version: the new clause is compiled, never refused up front.
    incremental = app.post("/v1/documents/extractions", json={"run_mode": "incremental", "base_ruleset_id": published["ruleset_id"], "recording_set": "amended", "document_ids": [ids[TRANSACTIONS]]})
    assert incremental.status_code in (200, 201, 202), incremental.text
    assert [item["field"] for item in app.get(f"/v1/documents/extractions/{incremental.json()['extraction_id']}").json()["discrepancies"]] == ["rule"]
    with_rule = app.post("/v1/documents/extractions", json={"run_mode": "incremental", "base_ruleset_id": published["ruleset_id"], "recording_set": "amended", "document_ids": [ids[TRANSACTIONS]], "actor": "r.eviewer", "rule_overrides": [ADD_TIME_05]})
    assert with_rule.status_code in (200, 201, 202), with_rule.text
    assert app.get(f"/v1/documents/extractions/{with_rule.json()['extraction_id']}").json()["discrepancies"] == []
    # A limit the new clause does not state is refused by the compiler's faithful check, as for any rule.
    wrong = {**ADD_TIME_05, "set": {**ADD_TIME_05["set"], "params": {"total_usd": {"type": "number", "value": "400", "unit": "usd"}}}}
    refused = app.get(f"/v1/documents/extractions/{extract(app, refs, recording_set='amended', rule_overrides=[wrong], actor='r.eviewer').json()['extraction_id']}").json()
    assert refused["status"] == "no_candidate" and refused["discrepancies"][0]["field"] == "thresholds"
