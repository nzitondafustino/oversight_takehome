# Single-company upgrade

All employees share the same ruleset version history. Employee country and entity still select regional
policy rules. Requests, responses, queries and new records have no customer identifier. Submit all relevant
receipts in one batch; `context_complete` defaults to true. The engine does not fetch historical receipts.

## Existing MongoDB databases

Stop writers and back up the database before upgrading. Startup's `ensure_indexes` checks the global unique
identities before removing legacy scope indexes and creating the current indexes. It is safe to run again.
Rulesets and datasets are unique by `(name, version)`; documents by `(document_id, revision)`; evaluation jobs
by `idempotency_key`. No IDs, versions, artifacts, results, approval records or hashes are rewritten.

If old namespaces contain the same name/version or idempotency key, startup refuses the index migration.
No history or legacy indexes are removed in that case. Resolve the conflicting identities explicitly before
retrying; do not silently renumber immutable versions or delete runs to make a unique index succeed.
The API reports database unavailability until its indexes can be established.

Legacy metadata may remain in stored historical documents. It no longer scopes queries. Stored run and
evaluation-result JSON keeps its original bytes and hashes; reads verify those hashes first and omit obsolete
scope metadata from the public result. Existing dispositions are not recomputed. Stored extraction results, compiled
versions' comparisons and their review decisions are read the same way (`services/legacy.py`): the hash is
verified first, then `customer_id` is left out of what is read and returned, and nothing is written back. A
pre-migration extraction starts no new review or version (409 `legacy_extraction`), and a pre-migration version
cannot be approved or published again, as the compiler no longer reproduces its hashes (409 `compilation_changed`).

## Recordings, extraction reviews and approvals

Line-classification replay identities do not change, so existing Haiku and Jev receipt recordings remain
usable without paid calls. Document-hash policy recordings also remain usable.

Configured policy recordings now use identity format 3, without a scope hash. Format 2 files remain historical
artifacts and do not match a format 3 request. Replay fails closed when there is no matching recording; it
never makes a paid fallback call. A new configured recording requires an explicitly authorized extraction,
or use the existing document-hash replay set when appropriate.

Extraction/review/approval output hashes change when the obsolete scope is removed. Legacy approval records
remain readable for audit, but cannot authorize a newly constructed output. Review and approve a new candidate
through the normal lifecycle; do not patch old approval hashes or mark an uploaded artifact as published.
Already published immutable rulesets remain usable for validation. Interrupted old extraction/publication
workflows need a new extraction/review; interrupted evaluation jobs whose request fingerprints changed should
be replaced by a new job while keeping the old job's history.

## Offline demo verification

From `backend`, `.venv/bin/python -m pytest` includes
`tests/test_single_company.py`. Its published replay test compiles from existing model recordings, reviews,
approves and publishes a production ruleset in an isolated mock database, then validates RCP-001 without a
customer field or completeness override. It checks a fully decided pass and the OpenAPI contract. It makes
no paid calls and changes no deployment data.
