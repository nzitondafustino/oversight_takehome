"""Versioned document lifecycle, with real compiler readings replayed and no provider calls."""
import copy

from conftest import POLICY_DOCUMENTS, COMPILER_RECORDINGS, extraction_decisions, published_version, review_extraction, upload_documents


def plan(client, **fields):
    return client.post("/v1/documents/extraction-plan", json=fields)


def materialize(client, extraction, recording_set="live"):
    review = review_extraction(client, extraction, decisions=extraction_decisions(extraction, recording_set))
    assert review.status_code == 201, review.text
    response = client.post(f"/v1/documents/extractions/{extraction['extraction_id']}/ruleset", json={"publish": True, "actor": "policy owner", "note": "Publish the reviewed document lifecycle update"})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "published"
    return response.json()


def test_full_run_resolves_latest_documents_and_can_publish(client):
    refs = upload_documents(client)
    request = {"run_mode": "full", "recording_set": "live", "ruleset_name": "company-policy"}
    resolved = plan(client, **request)
    assert resolved.status_code == 200, resolved.text
    assert {item["document_id"] for item in resolved.json()["documents"]} == {item["document_id"] for item in refs}
    response = client.post("/v1/documents/extractions", json=request)
    assert response.status_code == 201, response.text
    version = materialize(client, response.json())
    assert version["name"] == "company-policy"
    assert len(version["artifact"]["rules"]) == 42
    repeated = client.post(f"/v1/documents/extractions/{response.json()['extraction_id']}/ruleset", json={"publish": True, "actor": "policy owner", "note": "Retry the same publication"})
    assert repeated.status_code == 201
    assert repeated.json()["ruleset_id"] == version["ruleset_id"]


def test_incremental_run_resolves_filename_and_preserves_other_rules(client):
    upload_documents(client)
    base = published_version(client, name="company-policy")
    filename = "merchant-category-risk.md"
    request = {"run_mode": "incremental", "filenames": [filename], "ruleset_name": base["name"]}
    resolved = plan(client, **request)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["base_ruleset_id"] == base["ruleset_id"]
    assert resolved.json()["affected_rule_ids"] == [rule["rule_id"] for rule in base["artifact"]["rules"] if rule["policy_doc"] == filename]
    response = client.post("/v1/documents/extractions", json=request)
    assert response.status_code == 201, response.text
    updated = materialize(client, response.json())
    assert updated["version"] == base["version"] + 1
    before = {rule["rule_id"]: rule for rule in base["artifact"]["rules"]}
    for rule in updated["artifact"]["rules"]:
        if rule["policy_doc"] != filename:
            assert rule == before[rule["rule_id"]]


def test_removal_drops_derived_rules_prunes_only_references_and_publishes(client):
    refs = upload_documents(client)
    base = published_version(client)
    target = next(ref for ref in refs if client.get(f"/v1/documents/{ref['document_id']}").json()["filename"] == "merchant-category-risk.md")
    request = {"run_mode": "remove", "document_ids": [target["document_id"]], "base_ruleset_id": base["ruleset_id"]}
    resolved = plan(client, **request)
    assert resolved.status_code == 200, resolved.text
    removed = {rule["rule_id"] for rule in base["artifact"]["rules"] if rule["policy_doc"] == "merchant-category-risk.md"}
    assert set(resolved.json()["removed_rule_ids"]) == removed
    response = client.post("/v1/documents/extractions", json=request)
    assert response.status_code == 201, response.text
    updated = materialize(client, response.json())
    assert not removed & {rule["rule_id"] for rule in updated["artifact"]["rules"]}
    assert client.get(f"/v1/documents/{target['document_id']}").status_code == 200
    assert client.get(f"/v1/rulesets/{base['ruleset_id']}").json()["artifact_sha256"] == base["artifact_sha256"]
    assert plan(client, run_mode="incremental", base_ruleset_id=updated["ruleset_id"]).json()["error"]["code"] == "no_document_changes"
    restored = client.post("/v1/documents/extractions", json={"run_mode": "incremental", "document_ids": [target["document_id"]], "base_ruleset_id": updated["ruleset_id"]})
    assert restored.status_code == 201, restored.text
    restored_version = materialize(client, restored.json())
    assert {rule["rule_id"] for rule in restored_version["artifact"]["rules"]} == {rule["rule_id"] for rule in base["artifact"]["rules"]}


def test_selector_ambiguity_missing_changes_and_live_removal_are_refused(client):
    refs = upload_documents(client)
    published_version(client)
    assert plan(client, run_mode="incremental").json()["error"]["code"] == "no_document_changes"
    duplicate = copy.deepcopy(POLICY_DOCUMENTS[0])
    upload_documents(client, [duplicate])
    response = plan(client, run_mode="incremental", filenames=[duplicate["filename"]])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ambiguous_document"
    response = plan(client, run_mode="remove", document_ids=[refs[0]["document_id"]], execution_mode="live", extraction_model="unused")
    assert response.status_code == 422


def test_live_incremental_reads_only_changed_document_and_replays_for_publication(make_client, settings, tmp_path, monkeypatch):
    import hashlib
    import json
    import expense_validator.policy_compiler as module
    fixtures = {json.loads(path.read_text())["document_sha256"]: json.loads(path.read_text())["response"] for path in COMPILER_RECORDINGS["fixtures"].glob("*.json")}
    calls = []
    def provider(llm, key):
        def complete(prompt, text):
            calls.append(hashlib.sha256(text.encode()).hexdigest())
            return fixtures[hashlib.sha256(text.removeprefix("Updated introduction.\n\n").encode()).hexdigest()]
        return complete
    monkeypatch.setattr(module, "openrouter", provider)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    recordings = tmp_path / "readings"
    recordings.mkdir()
    client = make_client(app_settings=settings.model_copy(update={"allow_live": True, "allowed_models": frozenset({"vendor/model-a"}), "compiler_recording_sets": {**COMPILER_RECORDINGS, "recorded": recordings}, "compiler_live_recording_set": "recorded"}))
    refs = upload_documents(client)
    request = {"run_mode": "full", "execution_mode": "live", "extraction_model": "vendor/model-a", "temperature": 0.0, "ruleset_name": "company-policy"}
    initial = client.post("/v1/documents/extractions", json=request)
    assert initial.status_code == 201, initial.text
    base = materialize(client, initial.json(), "fixtures")
    assert len(calls) == len(refs)
    target = next(item for item in initial.json()["documents"] if item["filename"] == "merchant-category-risk.md")
    source = next(item for item in POLICY_DOCUMENTS if item["filename"] == target["filename"])
    revised = client.post(f"/v1/documents/{target['document_id']}/revisions", json={"content": "Updated introduction.\n\n" + source["content"]})
    assert revised.status_code == 201
    request["run_mode"] = "incremental"
    updated = client.post("/v1/documents/extractions", json=request)
    assert updated.status_code == 201, updated.text
    assert updated.json()["run"]["changed_document_ids"] == [target["document_id"]]
    assert len(calls) == len(refs) + 1
    version = materialize(client, updated.json(), "fixtures")
    assert len(calls) == len(refs) + 1
    rejected = client.post("/v1/documents/extractions", json={**request, "filenames": [target["filename"]], "temperature": 1.0})
    assert rejected.status_code == 409, rejected.text
    assert len(calls) == len(refs) + 1  # unchanged source readings cannot silently be regenerated for a new temperature
    before = {rule["rule_id"]: rule for rule in base["artifact"]["rules"]}
    for rule in version["artifact"]["rules"]:
        if rule["policy_doc"] != target["filename"]:
            assert rule == before[rule["rule_id"]]


def test_background_auto_selection_pins_revisions_before_new_arrivals(client):
    refs = upload_documents(client)
    work = []
    client.app.state.run_in_background = work.append
    response = client.post("/v1/documents/extraction-jobs", json={"run_mode": "full", "recording_set": "live"})
    assert response.status_code == 202, response.text
    target = refs[0]
    source = client.get(f"/v1/documents/{target['document_id']}").json()
    client.post(f"/v1/documents/{target['document_id']}/revisions", json={"content": source["content"] + "\nNew revision."})
    work[0]()
    job = client.get(f"/v1/documents/extraction-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    assert job["progress"]["total"] == len(refs)
    extraction = client.get(f"/v1/documents/extractions/{job['extraction_id']}").json()
    assert next(item for item in extraction["documents"] if item["document_id"] == target["document_id"])["revision"] == 1


def test_new_document_can_restore_known_rules_and_runtime_cites_its_versioned_source(client):
    from conftest import validate
    from evaluation_support import create, dataset
    from expense_api.worker import Worker
    upload_documents(client)
    base = published_version(client)
    response = client.post("/v1/documents/extractions", json={"run_mode": "remove", "base_ruleset_id": base["ruleset_id"], "filenames": ["merchant-category-risk.md"]})
    assert response.status_code == 201, response.text
    removed = materialize(client, response.json())
    source = next(item for item in POLICY_DOCUMENTS if item["filename"] == "merchant-category-risk.md")
    filename = "updated-merchant-category-risk.md"
    upload_documents(client, [{**source, "filename": filename}])
    response = client.post("/v1/documents/extractions", json={"run_mode": "incremental", "base_ruleset_id": removed["ruleset_id"]})
    assert response.status_code == 201, response.text
    restored = materialize(client, response.json())
    run = validate(client, restored["ruleset_id"], receipt_ids=("RCP-011",), preview=False)
    assert run.status_code == 201, run.text
    finding = next(item for item in run.json()["results"]["results"][0]["exceptions"] if item["rule_id"] == "MCC-01")
    assert finding["policy_doc"] == filename
    data = dataset(client, ("RCP-011",))
    queued = create(client, data["dataset_id"], restored["ruleset_id"], execution_mode="replay")
    assert queued.status_code == 201, queued.text
    assert Worker(client.app.state.settings, client.app.state.database).run_once()
    evaluated = client.get(f"/v1/evaluations/{queued.json()['evaluation_id']}").json()
    assert evaluated["status"] == "completed", evaluated
