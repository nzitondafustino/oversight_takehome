# Policy extraction modes

These modes use the existing Markdown policy format and deterministic rule implementations. A model reads policy facts; it does not invent executable policy logic. A clause ID no rule implements is compiled anyway: the build stops and reports it as a discrepancy of field `rule`, with what the clause states, and a reviewer writes its rule as a rule override with `add: true` (scope, source_quote, params and conditions). The compiler builds and checks an added rule like hand-written logic: its quote must be in its clause, and its limits and severity must be what the clause states.

| Endpoint | Purpose |
|---|---|
| `POST /v1/documents/extraction-plan` | Resolve documents, revisions, baseline and affected rule IDs. Makes no provider calls and writes nothing. |
| `POST /v1/documents/extractions` | Execute the same request and save an immutable extraction. |
| `POST /v1/documents/extraction-jobs` | Execute in the background, with the resolved revisions pinned before it starts. Prefer this for live runs. |
| `POST /v1/documents/extractions/{extraction_id}/review` | Resolve the interpretation differences reported by the extraction. |
| `POST /v1/documents/extractions/{extraction_id}/ruleset` | Create a draft, or approve and publish a ready reviewed extraction as a new version. |

The original request shape, `documents: [{document_id, revision}], recording_set: ...`, continues to work without `run_mode`.

## Selecting inputs

With `run_mode`, callers can combine:

- `documents`: exact `{document_id, revision}` references.
- `document_ids`: IDs resolved to the latest revision.
- `filenames`: exact names resolved to one current, non-hidden document. Ambiguous names return `409` with IDs to choose from.
- `base_ruleset_id`: an explicit published, compiled baseline.
- `ruleset_name`: resolve the latest published version of that name for incremental/removal runs. Without either baseline selector, use the latest published version company-wide.

A candidate contains one document per filename and at most 50 documents. IDs and revisions are returned by the planning endpoint. Source documents must be present in the document library; older inline compilations resolve by exact filename and content hash, never approximate text.

## 1. Full run

Send this body to the planning or extraction endpoint:

```json
{
  "run_mode": "full",
  "ruleset_name": "policies",
  "execution_mode": "replay",
  "recording_set": "live"
}
```

With no document selectors, resolve all latest, non-hidden library documents. Explicit selectors define the complete replacement manifest. This is a full rebuild, including any previously excluded documents selected again.

`live` above is a **recording-set name**, not the execution mode. Choose an available recording set from `GET /v1/backends`; replay never calls a provider. A missing recorded reading returns `409`.

## 2. Incremental run

```json
{
  "run_mode": "incremental",
  "ruleset_name": "policies",
  "filenames": ["merchant-category-risk.md"]
}
```

The backend combines the changed documents with the baseline's pinned source manifest. Without document selectors, it detects new documents and content changes in the latest library revisions. No changes returns `422 no_document_changes`.

Only rules derived from affected documents are rebuilt. Unaffected rules, including their provenance and approved corrections, are retained exactly. Removed clauses drop their derived rules. Reference sets and replacement links are adjusted only where their targets were added or removed; these dependent rules are explicitly reported as `dependency_rule_ids` and remain subject to review and compiler validation.

Replay inherits the baseline recording set and generation settings. For live extraction, supply `execution_mode: "live"`, an allowed `extraction_model`, and its generation settings. Before any paid work, unchanged documents must already have matching recordings in the live recording set. Changing the model/temperature cannot silently re-read unchanged documents: use a full run to change generation settings.

## 3. Document removal

```json
{
  "run_mode": "remove",
  "ruleset_name": "policies",
  "filenames": ["merchant-category-risk.md"]
}
```

Exclude these documents and their derived rules from the next candidate. Removal uses replay only. It retains the uploaded revisions, old rulesets and source evidence for audit; it does not physically erase stored documents.

Exclusions carry forward through incremental versions, so automatic detection does not re-add a removed document. Explicitly select its ID/name in an incremental request to restore it. A full run deliberately rebuilds the selected complete manifest.

Removal of required process directives or unresolved dependencies can make a candidate invalid. Such a version cannot be published; the returned blockers explain what needs attention.

## Review and publish a new version

Extraction responses include `run` with the baseline ID, resolved document manifest, affected/retained/removed/dependent rule IDs and exclusions. Resolve `review_required` comparisons through the existing review endpoint first. Review decisions name the returned extraction hash; decisions are not silently copied to changed interpretations.

Then send to `POST /v1/documents/extractions/{extraction_id}/ruleset`:

```json
{
  "publish": true,
  "actor": "policy.owner",
  "note": "Publish the reviewed policy document update",
  "mode": "production"
}
```

The backend resolves the ruleset name from the run and selects the latest ready review. Supply `name` and/or `review_id` explicitly to override that selection. An unresolved latest review returns `409`.

Omit `publish` (or set it to `false`) to create a draft for the normal lifecycle UI. When `publish` is true, actor and note are required, and the normal submit → accept → approve → publish checks run. Publication rebuilds the exact scoped logic and documents and obtains a fresh approval. Simulation readings cannot authorize production. Sequential retries resume/return the existing version for the same extraction, name and selected review.

Validation and evaluation bind finding evidence to the published version’s stored source manifest. New filenames and revised documents do not fall back to repository policy text. Uploaded legacy artifacts without compilation evidence retain their original behavior.

Published artifacts and previous versions remain unchanged. No historical receipt lookup or replacement company identifier is introduced.
