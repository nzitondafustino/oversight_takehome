"""CRUD actions preserve signed payloads, version history, references and paid-call idempotency."""
from conftest import extract, published_version, review_extraction, upload_documents
from evaluation_support import Workbench, dataset


def test_dataset_metadata_and_deletion_preserve_content_and_reserve_version_numbers(make_client, settings):
    bench = Workbench(make_client, settings)
    identifier = bench.dataset["dataset_id"]
    original = bench.database["evaluation_datasets"].find_one({"_id": identifier})
    response = bench.client.patch(f"/v1/evaluations/datasets/{identifier}", json={"description": "Updated description"})
    assert response.status_code == 200
    updated = bench.database["evaluation_datasets"].find_one({"_id": identifier})
    assert updated["canonical_json"] == original["canonical_json"]
    assert updated["canonical_sha256"] == original["canonical_sha256"]
    job = bench.queue()
    assert bench.client.delete(f"/v1/evaluations/datasets/{identifier}").status_code == 409
    assert bench.client.post(f"/v1/evaluations/{job}/cancel", json={}).status_code == 200
    assert bench.client.delete(f"/v1/evaluations/datasets/{identifier}").status_code == 200
    assert bench.client.get(f"/v1/evaluations/datasets/{identifier}").status_code == 404
    assert bench.client.get("/v1/evaluations/datasets").json()["total"] == 0
    assert bench.database["evaluation_datasets"].find_one({"_id": identifier})["canonical_json"] == original["canonical_json"]
    replacement = dataset(bench.client, ("RCP-001", "RCP-002"))
    assert replacement["version"] == original["version"] + 1


def test_completed_evaluation_can_be_renamed_and_deleted_without_losing_results(make_client, settings):
    bench = Workbench(make_client, settings)
    job = bench.run(idempotency_key="original")
    identifier = job["evaluation_id"]
    before = list(bench.database["evaluation_results"].find({"evaluation_id": identifier}))
    assert bench.client.patch(f"/v1/evaluations/{identifier}", json={"label": "Updated label"}).status_code == 200
    assert bench.get(identifier)["label"] == "Updated label"
    assert bench.client.delete(f"/v1/evaluations/{identifier}").status_code == 200
    assert bench.client.get(f"/v1/evaluations/{identifier}").status_code == 404
    assert bench.client.get("/v1/evaluations").json()["total"] == 0
    assert list(bench.database["evaluation_results"].find({"evaluation_id": identifier})) == before
    assert bench.create(idempotency_key="original").json()["error"]["code"] == "evaluation_deleted"


def test_active_evaluation_cannot_be_deleted(make_client, settings):
    bench = Workbench(make_client, settings)
    identifier = bench.queue()
    assert bench.client.delete(f"/v1/evaluations/{identifier}").status_code == 409
    assert bench.get(identifier)["status"] == "queued"


def test_review_revision_and_delete_preserve_signed_history_and_protect_version_references(client):
    extraction = extract(client, upload_documents(client)).json()
    original = review_extraction(client, extraction).json()
    path = f"/v1/documents/extractions/{extraction['extraction_id']}/reviews/{original['review_id']}"
    revised = client.patch(path, json={"actor": "reviewer", "extraction_sha256": extraction["extraction_sha256"], "review_policy": "exceptions_only", "decisions": original["decisions"]})
    assert revised.status_code == 201, revised.text
    latest = revised.json()
    assert latest["review_id"] != original["review_id"]
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
    created = client.post("/v1/rulesets", json={"name": "review-reference", "extraction": {"extraction_id": extraction["extraction_id"], "review_id": latest["review_id"]}})
    assert created.status_code == 201, created.text
    assert client.delete(path.replace(original["review_id"], latest["review_id"])).status_code == 409


def test_rule_removal_creates_an_inactive_draft_and_can_be_restored(client):
    published = published_version(client)
    response = client.request("DELETE", f"/v1/rulesets/{published['ruleset_id']}/rules/ALC-02", json={"actor": "editor", "rationale": "Remove this check from the next version"})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "draft"
    assert next(r for r in draft["artifact"]["rules"] if r["rule_id"] == "ALC-02")["status"] == "not_evaluable"
    assert client.get(f"/v1/rulesets/{published['ruleset_id']}").json()["artifact_sha256"] == published["artifact_sha256"]
    restored = client.post(f"/v1/rulesets/{draft['ruleset_id']}/edits", json={"actor": "editor", "rationale": "Restore this check", "changes": [{"rule_id": "ALC-02", "set": {"status": "active"}}]})
    assert restored.status_code == 201, restored.text
    assert next(r for r in restored.json()["artifact"]["rules"] if r["rule_id"] == "ALC-02")["status"] == "active"


def test_draft_deletion_hides_version_but_reserves_its_number(client):
    from conftest import create_ruleset
    draft = create_ruleset(client, name="draft-deletion")
    path = f"/v1/rulesets/{draft['ruleset_id']}"
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
    assert client.get("/v1/rulesets", params={"name": "draft-deletion"}).json()["total"] == 0
    assert client.delete(path).status_code == 404
    replacement = create_ruleset(client, name="draft-deletion")
    assert replacement["version"] == draft["version"] + 1


def test_published_ruleset_cannot_be_deleted(client):
    version = published_version(client)
    path = f"/v1/rulesets/{version['ruleset_id']}"
    assert client.delete(path).status_code == 409
    assert client.get(path).json()["artifact_sha256"] == version["artifact_sha256"]


def test_in_review_version_can_be_deleted(client):
    from conftest import create_ruleset
    draft = create_ruleset(client)
    path = f"/v1/rulesets/{draft['ruleset_id']}"
    assert client.post(path + "/submit", json={"actor": "reviewer"}).status_code == 200
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404


def test_duplicate_publication_rebuilds_with_fresh_approval_and_preserves_original(client):
    from conftest import create_ruleset
    published = published_version(client)
    uploaded = create_ruleset(client, name="duplicate", artifact=published["artifact"])
    path = f"/v1/rulesets/{uploaded['ruleset_id']}"
    assert client.get(path + "/duplicate-publication").json()["published"]["ruleset_id"] == published["ruleset_id"]
    response = client.post(path + "/duplicate-publication", json={"actor": "duplicate reviewer", "rationale": "Publish the identical policy as a new version"})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "published"
    assert result["ruleset_id"] != uploaded["ruleset_id"]
    assert result["artifact_sha256"] == published["artifact_sha256"]
    assert result["approval_reference"] != published["approval_reference"]
    assert result["source"]["reference"] == published["ruleset_id"]
    assert client.get(path).json()["status"] == "draft"
    original = client.get(path).json()
    assert original["superseded_by"]["ruleset_id"] == result["ruleset_id"]
    assert uploaded["ruleset_id"] not in [item["ruleset_id"] for item in client.get("/v1/rulesets").json()["items"]]
    history = client.get("/v1/rulesets", params={"include_superseded": True}).json()
    assert uploaded["ruleset_id"] in [item["ruleset_id"] for item in history["items"]]
    repeated = client.post(path + "/duplicate-publication", json={"actor": "duplicate reviewer", "rationale": "Retry publication"})
    assert repeated.status_code == 201
    assert repeated.json()["ruleset_id"] == result["ruleset_id"]
    assert client.get("/v1/rulesets", params={"include_superseded": True}).json()["total"] == history["total"]
    # Publications made before structured duplicate lineage existed are recognized without a data rewrite.
    client.app.state.database["rulesets"].update_one({"_id": result["ruleset_id"]}, {"$unset": {"duplicate_of": ""}})
    assert client.get(path).json()["superseded_by"]["ruleset_id"] == result["ruleset_id"]
    assert client.post(path + "/submit", json={"actor": "reviewer"}).status_code == 409
    assert client.get(f"/v1/rulesets/{published['ruleset_id']}").json()["approval_reference"] == published["approval_reference"]


def test_similar_artifact_cannot_use_duplicate_publication(client):
    from conftest import create_ruleset
    published = published_version(client)
    artifact = published["artifact"]
    artifact["ruleset_version"] += "-different"
    draft = create_ruleset(client, name="different", artifact=artifact)
    path = f"/v1/rulesets/{draft['ruleset_id']}/duplicate-publication"
    assert client.get(path).json()["published"] is None
    assert client.post(path, json={"actor": "reviewer", "rationale": "Try different content"}).status_code == 409
