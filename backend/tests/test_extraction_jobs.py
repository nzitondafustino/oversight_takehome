"""Extractions in the background: refused at once when they cannot run, then followed step by step to their
extraction or their error. No test calls a model: live extraction is played from the recorded model readings."""

from datetime import timedelta

import mongomock
import pytest
from conftest import POLICY_DOCUMENTS, extract, upload_documents

from expense_api.repositories.extraction_jobs import ExtractionJobRepository
from expense_api.services.compilation import Compilation
from expense_api.services.rulesets import now

MEALS = next(document for document in POLICY_DOCUMENTS if document["filename"] == "meals-and-entertainment-policy.md")


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


def started(make_client, mongo, **settings_kw):
    """An app whose background work runs at once, so a test reads the job after it finished."""
    client = make_client(mongo=mongo, **settings_kw)
    client.app.state.run_in_background = lambda work: work()
    return client


def test_a_job_runs_the_extraction_and_names_it_with_its_progress(make_client, mongo):
    app = started(make_client, mongo)
    refs = upload_documents(app)
    accepted = app.post("/v1/documents/extraction-jobs", json={"documents": refs, "recording_set": "live"})
    assert accepted.status_code == 202, accepted.text
    assert accepted.headers["Location"] == f"/v1/documents/extraction-jobs/{accepted.json()['job_id']}"
    job = app.get(accepted.headers["Location"]).json()
    assert (job["status"], job["progress"], job["error"]) == ("succeeded", {"stage": "done", "done": 9, "total": 9, "current": None}, None)
    # The same snapshot a direct extraction makes of the same revisions.
    direct = extract(app, refs).json()
    assert app.get(f"/v1/documents/extractions/{job['extraction_id']}").json()["extraction_sha256"] == direct["extraction_sha256"]


def test_a_job_that_cannot_run_is_refused_at_once_and_none_is_stored(make_client, mongo):
    app = started(make_client, mongo)
    refused = app.post("/v1/documents/extraction-jobs", json={"documents": [{"document_id": "doc_" + "0" * 24, "revision": 1}], "recording_set": "live"})
    assert refused.status_code == 404
    assert mongo["expense_api"]["extraction_jobs"].count_documents({}) == 0


def test_a_failure_while_running_is_kept_on_the_job(make_client, mongo):
    app = started(make_client, mongo)
    refs = upload_documents(app, documents=[{**MEALS, "content": MEALS["content"] + "\nA clause no reading was recorded for.\n"}])
    job = app.get(app.post("/v1/documents/extraction-jobs", json={"documents": refs, "recording_set": "live"}).headers["Location"]).json()
    assert job["status"] == "failed" and job["error"]["code"] == "replay_recording_missing" and job["extraction_id"] is None


def test_a_live_job_reads_each_document_in_turn_and_reports_it(make_client, mongo, settings, monkeypatch):
    reported = []
    live = settings.model_copy(update={"allow_live": True, "compiler_live_recording_set": "live", "allowed_models": frozenset({"anthropic/claude-haiku-5.5"}), "allowed_classification_models": frozenset({"anthropic/claude-haiku-5.5"})})
    # The recorded model readings stand in for the model: nothing is paid for.
    monkeypatch.setattr(Compilation, "live_compiler", lambda self, *_: self.replay_compiler("live", None))
    app = started(make_client, mongo, app_settings=live)
    refs = upload_documents(app, documents=POLICY_DOCUMENTS[:3])
    update = ExtractionJobRepository.update
    monkeypatch.setattr(ExtractionJobRepository, "update", lambda self, job_id, changes, **kw: (reported.append(changes["progress"]) if "progress" in changes else None) or update(self, job_id, changes, **kw))
    job = app.get(app.post("/v1/documents/extraction-jobs", json={"documents": refs, "execution_mode": "live", "extraction_model": "anthropic/claude-haiku-5.5"}).headers["Location"]).json()
    assert job["status"] == "succeeded", job
    names = [document["filename"] for document in POLICY_DOCUMENTS[:3]]
    assert reported == [
        *({"stage": "reading", "done": index, "total": 3, "current": name} for index, name in enumerate(names)),
        {"stage": "building", "done": 3, "total": 3, "current": None},
        {"stage": "done", "done": 3, "total": 3, "current": None},
    ]


def test_a_job_whose_process_stopped_reports_itself_interrupted(make_client, mongo):
    app = started(make_client, mongo)
    old = now() - timedelta(minutes=20)
    mongo["expense_api"]["extraction_jobs"].insert_one({"_id": "job_stopped", "status": "running", "execution_mode": "live", "extraction_model": "m", "documents": ["a.md"], "progress": {"stage": "reading", "done": 0, "total": 1, "current": "a.md"},
                                                         "extraction_id": None, "error": None, "created_at": old, "started_at": old, "finished_at": None, "updated_at": old})
    job = app.get("/v1/documents/extraction-jobs/job_stopped").json()
    assert (job["status"], job["error"]["code"]) == ("failed", "interrupted")
    assert app.get("/v1/documents/extraction-jobs/job_none").status_code == 404
