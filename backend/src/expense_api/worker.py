"""The evaluation worker: python -m expense_api.worker [--once].

It runs in its own process, from the same image as the API, and shares only MongoDB with it. Jobs are persisted
before any worker sees them, so a job outlives an API or worker restart.

- Claiming is one atomic findAndModify from queued to running, so two workers never run the same job.
- A claimed job has a lease, renewed by a heartbeat before every receipt and every provider call. Every write a
  worker makes to its job is conditional on still holding that lease, so a worker that lost its lease stops
  without overwriting the job.
- A lease that expires means its worker was lost. A replay job is requeued, up to max_evaluation_attempts, and
  resumes after the receipts it stored: replay calls no model, so running again costs nothing and gives the same
  answers. A live job is never retried, as that could pay for calls again: it fails as worker_lost, keeping the
  results and spending it recorded. A job whose cancellation was asked for is cancelled.
- Cancellation is cooperative: the worker stops before the next receipt or provider call. A provider request
  already sent is not cancelled, and its cost is still recorded.
"""

import argparse
import logging
import signal
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from pymongo import MongoClient
from pymongo.database import Database

from expense_api.config import Settings
from expense_api.repositories import EvaluationRepository, ensure_indexes
from expense_api.services.evaluation_runner import Lease, Outcome, final_state, openrouter_client, run_job
from expense_api.services.rulesets import now
from expense_api.services.spending import EvaluationStopped, load_pricing

log = logging.getLogger("expense_api.worker")


class Worker:
    def __init__(self, settings: Settings, database: Database, *, worker_id: str | None = None, clock: Callable[[], datetime] = now, client_factory: Callable = openrouter_client):
        self.settings, self.database = settings, database
        self.jobs = EvaluationRepository(database["evaluations"])
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self.clock, self.client_factory = clock, client_factory
        self.pricing = load_pricing(settings.pricing_table)
        self.stopping = threading.Event()  # set on SIGTERM: stop at the next receipt or call

    def run_once(self) -> bool:
        """Recover stale jobs, then claim and run one queued job. Returns whether a job ran."""
        self.recover_stale()
        moment = self.clock()
        job = self.jobs.claim(self.worker_id, moment, self._expiry(moment))
        if job is None:
            return False
        if job.get("started_at") is None:
            self.jobs.owned_update(job["_id"], self.worker_id, {"started_at": moment})
        log.info("running %s (%s, attempt %s)", job["_id"], job["execution_mode"], job["attempts"])
        outcome = run_job(job, settings=self.settings, database=self.database, lease=self._lease(job), client_factory=self.client_factory, pricing=self.pricing)
        self._finish(job, outcome)
        return True

    def run_forever(self) -> None:
        while not self.stopping.is_set():
            if not self.run_once():
                self.stopping.wait(self.settings.worker_poll_seconds)

    def recover_stale(self) -> int:
        """Settle every job whose worker's lease expired. Returns how many were settled."""
        settled = 0
        for job in self.jobs.stale(self.clock()):
            expected = {"status": job["status"], "lease.worker_id": job["lease"]["worker_id"], "lease.expires_at": job["lease"]["expires_at"]}
            if job["status"] == "running" and job["execution_mode"] == "replay" and job["attempts"] < self.settings.max_evaluation_attempts:
                changed = self.jobs.transition(job["_id"], expected, {"status": "queued", "lease": None, "updated_at": self.clock()})
                log.warning("requeued %s: its worker's lease expired", job["_id"])
            else:
                if job["status"] == "cancel_requested":
                    outcome = Outcome(stop_reason="cancelled")
                elif job["execution_mode"] == "live":
                    outcome = Outcome(error={"code": "worker_lost", "message": "the worker stopped during a live run, which is never retried automatically, as that could pay for calls again: start a new evaluation"})
                else:
                    outcome = Outcome(error={"code": "worker_lost", "message": f"the worker was lost {job['attempts']} times"})
                changed = self.jobs.transition(job["_id"], expected, final_state(job, outcome, self.database))
                log.warning("settled %s after its worker's lease expired", job["_id"])
            settled += changed is not None
        return settled

    def _lease(self, job: dict) -> Lease:
        def guard() -> None:
            if self.stopping.is_set():
                raise EvaluationStopped("worker_stopping")
            moment = self.clock()
            current = self.jobs.owned_update(job["_id"], self.worker_id, {"lease.heartbeat_at": moment, "lease.expires_at": self._expiry(moment)})
            if current is None:
                raise EvaluationStopped("lease_lost")
            if current["status"] == "cancel_requested":
                raise EvaluationStopped("cancelled")

        def update(changes: dict) -> None:
            if self.jobs.owned_update(job["_id"], self.worker_id, changes) is None:
                raise EvaluationStopped("lease_lost")

        return Lease(guard, update)

    def _finish(self, job: dict, outcome: Outcome) -> None:
        if outcome.stop_reason == "lease_lost":
            log.warning("lost the lease on %s: another worker or a recovery settles it", job["_id"])
            return
        if outcome.stop_reason == "worker_stopping":
            if job["execution_mode"] == "replay":
                self.jobs.owned_update(job["_id"], self.worker_id, {"status": "queued", "lease": None, "updated_at": self.clock()})  # resumes, at no cost
                return
            outcome = Outcome(error={"code": "worker_stopped", "message": "the worker was stopped during a live run, which is never resumed automatically"})
        if self.jobs.owned_update(job["_id"], self.worker_id, final_state(job, outcome, self.database)) is None:
            log.warning("could not settle %s: its lease was lost", job["_id"])

    def _expiry(self, moment: datetime) -> datetime:
        return moment + timedelta(seconds=self.settings.worker_lease_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queued evaluations.")
    parser.add_argument("--once", action="store_true", help="run queued jobs until none is left, then exit")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    client = MongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=settings.mongo_timeout_ms, tz_aware=True)
    database = client[settings.mongo_database]
    ensure_indexes(database)
    worker = Worker(settings, database)
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    log.info("worker %s started", worker.worker_id)
    if arguments.once:
        while worker.run_once():
            pass
    else:
        worker.run_forever()
    client.close()


if __name__ == "__main__":
    main()
