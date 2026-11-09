"""Extractions run in the background, so a slow live extraction never holds a request open, with their progress.

A job is checked before it is accepted (mode, model and every exact revision), so a request that cannot run is
refused at once. It then runs in a thread of the API process and records each step in MongoDB: queued, running with
the document being read, then succeeded with its extraction, or failed with the reason. A job whose process stopped
reports itself interrupted once it has made no progress for STALE; a live job is never retried automatically, as
that could pay for readings again (readings already recorded are reused by a new extraction at no cost).
"""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from bson import ObjectId

from expense_api.errors import ApiError
from expense_api.repositories.extraction_jobs import ExtractionJobRepository
from expense_api.schemas.documents import ExtractionCreate
from expense_api.services.extractions import ExtractionService
from expense_api.services.rulesets import now

log = logging.getLogger("expense_api")

STALE = timedelta(minutes=15)


def in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="extraction-job", daemon=True).start()


class ExtractionJobs:
    def __init__(self, repository: ExtractionJobRepository, extractions: ExtractionService, run: Callable[[Callable[[], None]], None] = in_thread):
        self.repository, self.extractions, self.run_later = repository, extractions, run

    def start(self, request: ExtractionCreate) -> dict:
        revisions = self.extractions.check(request)
        moment = now()
        job = {
            "_id": f"job_{ObjectId()}",
            "status": "queued",
            "execution_mode": request.execution_mode,
            "extraction_model": request.extraction_model,
            "documents": [revision["filename"] for revision in revisions],
            "progress": {"stage": "queued", "done": 0, "total": len(revisions), "current": None},
            "extraction_id": None,
            "error": None,
            "created_at": moment,
            "started_at": None,
            "finished_at": None,
            "updated_at": moment,
        }
        self.repository.insert(job)
        self.run_later(lambda: self.run(job["_id"], request))
        return job

    def run(self, job_id: str, request: ExtractionCreate) -> None:
        moment = now()
        self.repository.update(job_id, {"status": "running", "started_at": moment, "updated_at": moment})
        try:
            extraction = self.extractions.create(request, progress=lambda progress: self.repository.update(job_id, {"progress": progress, "updated_at": now()}))
        except ApiError as error:
            self._finish(job_id, {"status": "failed", "error": {"code": error.code, "message": error.message, "details": error.details}})
        except Exception:  # noqa: BLE001 - recorded on the job, never lost in a thread
            log.exception("extraction job %s failed", job_id)
            self._finish(job_id, {"status": "failed", "error": {"code": "extraction_failed", "message": "the extraction stopped on an unexpected error: see the API log", "details": None}})
        else:
            total = len(self.extractions.plan(request).revisions)
            self._finish(job_id, {"status": "succeeded", "extraction_id": extraction["_id"], "progress": {"stage": "done", "done": total, "total": total, "current": None}})

    def get(self, job_id: str) -> dict:
        job = self.repository.get(job_id)
        if job is None:
            raise ApiError(404, "extraction_job_not_found", f"no extraction job {job_id}")
        if job["status"] in ("queued", "running") and now() - job["updated_at"] > STALE:
            interrupted = {"status": "failed", "error": {"code": "interrupted", "message": "the extraction made no progress for 15 minutes, so the API stopped while running it: start it again (readings already recorded are reused, at no cost)", "details": None}}
            job = self._finish(job_id, interrupted) or self.repository.get(job_id)
        return job

    def _finish(self, job_id: str, changes: dict) -> dict | None:
        moment = now()
        return self.repository.update(job_id, {**changes, "finished_at": moment, "updated_at": moment})
