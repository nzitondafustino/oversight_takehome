"""Move policy document text written before S3 storage out of MongoDB, into one S3 object per revision.

    python -m expense_api.migrate_documents            # a dry run: what would move, and any record that cannot
    python -m expense_api.migrate_documents --apply    # move them
    python -m expense_api.migrate_documents --orphans  # report S3 objects no record references (report only)

It reads the deployment's settings, as the API does (EXPENSE_API_*, AWS_S3_BUCKET, AWS_REGION), and S3 credentials
from boto3's standard chain. It never runs at startup. For each record still holding its text, in ID order:

1. Its text must match its recorded SHA-256 and size; a record that does not is reported and left as it is.
2. The text is written create-only to policy-documents/{document_id}/revisions/{revision}/document.md. An object
   already there, as an interrupted run leaves one, is used only if it holds exactly this text.
3. The object is read back and checked against the hash.
4. Only then is the record changed, in one atomic update that sets its S3 reference and removes its text, and only if
   it still holds that text with that hash. ID, revision, timestamps and every other field are unchanged.

So it can be stopped at any point and run again: a moved record is no longer selected, and an object written before an
interruption is verified and reused. Output names records and keys, never document text or credentials.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter

from pymongo import MongoClient

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import DocumentRepository
from expense_api.services.document_store import DocumentStore, ObjectExists, revision_key

BATCH = 100


def migrate(repository: DocumentRepository, store: DocumentStore | None, apply: bool, report=print, prefix: str = "policy-documents") -> Counter:
    """Move every legacy record, or with apply False say what would move: a dry run needs no store, and calls no S3.
    Returns a count by outcome."""
    if apply and store is None:
        raise ValueError("moving records needs the document store")
    counts: Counter = Counter()
    after = None
    while batch := repository.legacy(after, BATCH):
        for record in batch:
            after = record["_id"]
            outcome = _one(repository, store, record, apply)
            counts[outcome] += 1
            key = store.key(record["document_id"], record["revision"]) if store else revision_key(prefix, record["document_id"], record["revision"])
            report(json.dumps({"record": record["_id"], "key": key, "outcome": outcome}))
    return counts


def _one(repository: DocumentRepository, store: DocumentStore, record: dict, apply: bool) -> str:
    data = record["content"].encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    if digest != record["content_sha256"] or len(data) != record.get("size_bytes", len(data)):
        return "hash_mismatch"  # never move text that is not what was recorded: left for a person to look at
    if not apply:
        return "would_move"
    key = store.key(record["document_id"], record["revision"])
    try:
        stored = store.put(key, data, digest)
    except ObjectExists:
        stored = store.existing(key)
        if stored is None:
            return "object_conflict"
    try:
        store.read(stored.reference(), digest, len(data))  # read back, whether just written or left by an earlier run
    except ApiError as error:
        return "object_conflict" if error.code == "integrity_error" else f"unverified:{error.code}"
    return "moved" if repository.migrate_to_storage(record["_id"], digest, stored.reference()) else "changed_meanwhile"


def orphans(repository: DocumentRepository, store: DocumentStore, report=print) -> int:
    """Report S3 objects no record references, and objects requests noted as orphaned. Deletes nothing."""
    referenced = repository.referenced_keys()
    found = [key for key in store.keys() if key not in referenced]
    for key in found:
        report(json.dumps({"unreferenced_key": key}))
    for noted in repository.noted_orphans():
        report(json.dumps({"noted_orphan": {name: str(value) for name, value in noted.items()}}))
    return len(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="move the records; without it, only report what would move")
    parser.add_argument("--orphans", action="store_true", help="report S3 objects no record references, and delete nothing")
    args = parser.parse_args(argv)
    settings = Settings()
    client = MongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=settings.mongo_timeout_ms, tz_aware=True)
    try:
        database = client[settings.mongo_database]
        repository = DocumentRepository(database["documents"], database["document_orphans"])
        if args.orphans:
            return 1 if orphans(repository, DocumentStore(settings)) else 0
        # A dry run reads MongoDB only, so it runs where S3 is not yet configured.
        counts = migrate(repository, DocumentStore(settings) if args.apply else None, args.apply, prefix=settings.s3_document_prefix)
        print(json.dumps({"apply": args.apply, "counts": dict(counts)}))
        return 0 if set(counts) <= {"moved", "would_move", "changed_meanwhile"} else 1
    except ApiError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
