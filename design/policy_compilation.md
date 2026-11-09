# Rules compilation

Status: implemented Python package. Policy documents and developer-authored logic become a source-checked ruleset through review and approval. The restored original diagram illustrates the target architecture, including planned incremental processing, document formats, and archival. The implementation and limitations are described below. See the [diagram](offline_policy_compiler.html), [PNG](offline_policy_compiler.png), and [validation design](expense_validation.md).

## Inputs and boundaries

| Input | Role |
|---|---|
| Markdown policy documents | Source wording, clauses, rule IDs, limits, severities, and citations |
| `rules/rule_logic.yaml` | Developer-authored conditions, helpers, replacements, carve-outs, and process logic |
| `rules/fields.yaml` | Allowed fields, values, units, and input contracts |
| Model responses or recordings | Independent structured reading of policy text |
| Review decisions or resolutions | Human interpretation with source-bound rationale |
| Approval | Sign-off bound to the reviewed output and executable candidate |

An LLM reads policy fields; it does not freely author executable logic. Source agreement cannot replace schema and semantic validation.

## Lifecycle

| Step | Behavior | Code | Writes |
|---|---|---|---|
| 1. Read independently | Regex extraction and an LLM reading supplied live or through replay | `extract_document`, `extract_with_llm` | Optional live cache and recordings |
| 2. Compare | Compare presence, title, section, severity, review signals, thresholds, references, and quote | `compare_document`, `build_comparisons` | No publication |
| 3. Review | Resolve disagreements or inspect all fields against the exact source and extraction | `reconcile`, `PolicyCompiler.review` | Caller persists decisions |
| 4. Validate candidate | Combine clauses with developer logic; validate schema, catalog, source fidelity, and resolved limits | `prepare_candidate_from_texts`, `validate_ruleset` | No publication |
| 5. Approve | Require a complete valid interpretation and bind it to the executable candidate | `PolicyCompiler.approve`, `approval.approve` | Caller saves facade approvals; `save_approval` is write-once |
| 6. Publish | Rebuild and verify that approval covers the exact candidate | `PolicyCompiler.publish`, `publish_with_agreement` | Ruleset only when gates pass |

Candidate validation also runs during extraction, exposing executable-logic blockers before approval. These are decision gates, not separate worker jobs.

## Evidence-backed review

Comparison states are `agreed`, `disagree`, `unconfirmed`, `only_regex`, and `only_llm`. Each comparison exposes its document, rule, field, readings, available clause text and location, clause hash, and extraction identity.

Two supported review paths share source checks:

- **Resolutions:** identify document, rule, field, value, reviewer, rationale, and clause hash to resolve non-agreed fields.
- **Typed review decisions:** inspect `exceptions_only` or `all_fields`; confirm agreement, accept a reading, override with rationale, or reject. Decisions bind to the extraction and comparison. Rejection and invalid or incomplete decisions block approval.

Do not combine resolutions and typed decisions in one reviewed publication. The facade rechecks the same documents when reviewing and publishing.

Threshold evidence must use recognized unit forms. Unrelated merchant codes, and a count of the rules a clause names ("those three"), are not limits. Resolved values must remain consistent with developer-authored executable logic; differing limits require a logic change and renewed validation.

## Rule overrides and additions

A reviewed change to the logic is a rule override (`compiler/overrides.py`), applied to the logic text before the build, so the compiler builds, faithful-checks and hashes it like hand-written logic, and approval binds the result.

- **Change:** an existing rule's editable fields (status, title, outcome, severity, conditions, waiver reference, param values). A rule's ID, kind, document, quote and provenance never change, and a param keeps its name, type and unit.
- **Addition (`add: true`):** a new check rule for a clause the documents state under its own rule ID that no rule implements. It also gives `scope`, `source_quote` and `params`; its kind is `check`, and its document and title come from its clause. Its quote must be in that clause, and its limits and severity must be what the clause states.

A clause no rule implements stops the build: the extraction has no candidate and reports a discrepancy of field `rule` stating the clause's title, text, severities, review signal and limits, for the reviewer who writes its rule. A rule that no longer does what its clause states is a discrepancy of that field (`thresholds`, `severities`, `review_signal`), and one that quotes words its clause no longer has is a `source_quote` discrepancy, which only a change to the logic file corrects.

## Approval binds interpretation and execution

| Identity | Protects |
|---|---|
| `output_sha256` | Exact agreement or reviewed output, including readings, decisions, and provenance kind |
| `ruleset_sha256` | Executable rules and source clauses, including developer logic |

Binding only interpretation misses executable changes. Binding only execution omits what the reviewer accepted. Publication recomputes both and refuses stale approval.

Approvals bind exact output and candidate hashes. Legacy approvals require fresh approval after the single-company identity change. Reviewer identity comes from the caller; hashes detect content changes but are not digital signatures.

## Recording and publication modes

`PolicyCompiler` supports live OpenRouter extraction and offline replay. Configured recordings include generation identity (format 3). Legacy document-hash recordings remain supported by the strict CLI. Conflicting recording writes are refused.

`recording.kind` distinguishes `model` and `simulation`. Production requires model readings and matching approval. Simulation cannot write the configured production path. The editable label is provenance, not independent proof of provider origin.

Prepare the strict report for review:

```sh
python -m expense_validator.compiler.build_rules --strict-agreement \
    --recordings DIR --resolutions FILE --out PATH --report PATH
```

Add `--approval FILE` for approved publication and explicitly select `--mode production` or `--mode simulation`. The non-strict build is development-only: it can overwrite the configured ruleset without agreement or approval.

The facade returns publication blockers. It refuses existing outputs unless `replace=True`. It does not activate a version, archive previous releases, or automatically save facade approvals.

## Validation and limitations

Compiler, approval, publication, facade, and typed-review tests cover stale document/logic hashes, invalid decisions, recording conflicts, and simulation/production separation using offline fixtures.

- Current input is Markdown. PDF/DOCX conversion and OCR are outside the package.
- The package compiles the documents it is given. Incremental runs over changed documents are planned by the API (`backend/POLICY_EXTRACTION.md`); automatic retirement, version tagging and archival are not implemented.
- Threshold checks enforce recognized forms and supported logic, not unrestricted natural-language meaning.
- Simulated agreement measures workflow behavior, not extraction quality. Measure authentic provider runs separately; do not inherit historical counts or provenance claims.
- Persistence, authenticated sign-off, activation, rollback history, and AWS services belong to the integrating backend.
