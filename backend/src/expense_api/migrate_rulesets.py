"""Move the policy document text compiled ruleset versions hold in MongoDB into S3, leaving references in its place.

    python -m expense_api.migrate_rulesets            # a dry run: what would move, and any snapshot that cannot
    python -m expense_api.migrate_rulesets --apply    # move them
    python -m expense_api.migrate_rulesets --orphans  # report ruleset-documents objects no version references

It reads the deployment's settings, as the API does, and never runs at startup. For each version whose
`compilation.documents` still holds text, in ID order:

1. Every entry's text must match the hash the compiler read (`sha256`); a snapshot that does not is reported as
   inconsistent and left exactly as it is, whatever its status.
2. Each text is put in S3 as a new version would put it: the document revision's own object when that revision is in
   S3 with exactly this text, else a create-only object at ruleset-documents/{ruleset_id}/documents/{sha256}.md. An
   object an interrupted run left is used only if it holds exactly this text.
3. Every reference is read back and its text compared with the original, byte for byte.
4. Only then is the version changed: one atomic update replaces the entries, text and all, with their references,
   only while they are still exactly as read. The artifact, its hash, the approval, status, timestamps and every other
   field are untouched, so a published version's identity and its approval are unchanged, and a rebuild is given the
   same documents as before.

It can be stopped at any point and run again. Output names versions and keys, never document text or credentials.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter

from pymongo import MongoClient

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import DocumentRepository, RulesetRepository
from expense_api.services.compilation_documents import CompilationDocuments
from expense_api.services.document_store import DocumentStore

BATCH = 50


def migrate(rulesets: RulesetRepository, documents: DocumentRepository, store: DocumentStore | None, apply: bool, report=print) -> Counter:
    """Move every version holding document text, or with apply False say what would move. A dry run needs no store."""
    if apply and store is None:
        raise ValueError("moving snapshots needs the document store")
    counts: Counter = Counter()
    after = None
    while batch := rulesets.with_document_text(after, BATCH):
        for record in batch:
            after = record["_id"]
            outcome, detail = _one(rulesets, documents, store, record, apply)
            counts[outcome] += 1
            report(json.dumps({"ruleset": record["_id"], "name": record["name"], "version": record["version"], "status": record["status"], "outcome": outcome, **detail}))
    return counts


def _one(rulesets: RulesetRepository, documents: DocumentRepository, store: DocumentStore | None, record: dict, apply: bool) -> tuple[str, dict]:
    original = record["compilation"]["documents"]
    wrong = [entry["filename"] for entry in original if "content" in entry and hashlib.sha256(entry["content"].encode("utf-8")).hexdigest() != entry["sha256"]]
    if wrong:
        return "inconsistent_snapshot", {"documents": wrong}  # never moved, never changed: for a person to look at
    plan = [_with_revision(documents, entry) for entry in original]
    reused = sum(1 for entry in plan if entry.get("storage"))
    detail = {"documents": len(original), "reusing_revision_objects": reused}
    if not apply:
        return "would_move", detail
    keep = [entry for entry in original if "content" not in entry]
    if keep:  # an entry already referenced and another with text: never written by this code, so never guessed at
        return "inconsistent_snapshot", {"documents": [entry["filename"] for entry in keep]}
    snapshots = CompilationDocuments(lambda: store)
    try:
        persisted = snapshots.persist(record["_id"], plan)
        resolved = snapshots.resolve(persisted.entries)  # read back: every reference must give back the exact text
    except ApiError as error:
        return f"unverified:{error.code}", detail
    if [item["content"] for item in resolved] != [entry["content"] for entry in original]:
        return "unverified:integrity_error", detail
    moved = rulesets.move_compilation_documents(record["_id"], original, persisted.entries)
    return ("moved" if moved else "changed_meanwhile"), detail


def _with_revision(documents: DocumentRepository, entry: dict) -> dict:
    """The entry with its document revision's S3 reference, when that revision holds exactly this text in S3."""
    if entry.get("revision") is None or "content" not in entry:
        return entry
    revision = documents.metadata(entry["document_id"], entry["revision"])
    if revision is None or "storage" not in revision or revision["content_sha256"] != entry["sha256"]:
        return entry
    return entry | {"storage": revision["storage"], "content_sha256": revision["content_sha256"]}


def orphans(rulesets: RulesetRepository, store: DocumentStore, report=print) -> int:
    """Report objects under the ruleset prefix that no version references. Deletes nothing."""
    referenced = rulesets.document_keys()
    found = [key for key in store.keys(root=store.ruleset_prefix) if key not in referenced]
    for key in found:
        report(json.dumps({"unreferenced_key": key}))
    return len(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="move the snapshots; without it, only report what would move")
    parser.add_argument("--orphans", action="store_true", help="report ruleset-documents objects no version references, and delete nothing")
    args = parser.parse_args(argv)
    settings = Settings()
    client = MongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=settings.mongo_timeout_ms, tz_aware=True)
    try:
        database = client[settings.mongo_database]
        rulesets, documents = RulesetRepository(database["rulesets"]), DocumentRepository(database["documents"])
        if args.orphans:
            return 1 if orphans(rulesets, DocumentStore(settings)) else 0
        counts = migrate(rulesets, documents, DocumentStore(settings) if args.apply else None, args.apply)
        print(json.dumps({"apply": args.apply, "counts": dict(counts)}))
        return 0 if set(counts) <= {"moved", "would_move", "changed_meanwhile"} else 1
    except ApiError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
