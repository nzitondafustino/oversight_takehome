# Expense Policy Validator: frontend

React 19, TypeScript, Vite, Tailwind CSS 4, shadcn/ui (Radix primitives as source in `src/components/ui`), React Router,
TanStack Query, Recharts and Lucide. UI-1: the shared layout, navigation and Dashboard. UI-2: receipt
validation and run history. UI-3: the Evaluation Workbench (`/evaluations`). UI-4: Documents (`/documents`: revisions,
extraction and its review) and Policy Management (`/policies`: versions, comparison, lifecycle and audit history).

```sh
npm install
npm run dev         # http://localhost:5173, proxies /api to API_PROXY_TARGET (default http://localhost:8000)
npm run typecheck
npm test            # vitest: fetch is stubbed, no network
npm run build
```

With Docker, from the repository root: `docker compose up --build -d`. The app is served at http://localhost:3000.

- **API base URL.** `VITE_API_BASE_URL` defaults to `/api` on the same origin. The API has no CORS middleware, so the
  Vite dev server and the container's nginx (`nginx.conf`) proxy `/api` to it, and the backend stays unchanged.
- **Design tokens.** Every color is defined once in `src/styles/globals.css` and used through Tailwind utilities
  (`bg-primary`, `text-status-flag-text`) or `var(--color-…)` in charts.
- **Company-wide policies.** All employees share versioned policies. Country and entity select regional rules.
  All relevant receipts are submitted together; context completeness defaults to true.
- **Data.** Every value comes from the API. The API has no aggregate endpoints, so dashboard totals are summed over one
  page (up to 100 records), and each card states the records it covers. Money is summed exactly from the API's decimal
  strings.
- **Attempt history.** A LangGraph line's expanded panel, and its row in evaluation results, shows each classification and revision (node, model, outcome, confidence), each review (verdict, reason, feedback, suggestion), the calls and revisions against their limits, why it stopped, and whether its confidence was withheld.
- **Polling.** Only evaluation jobs that are `queued`, `running` or `cancel_requested` are polled, every 2 s. Polling
  stops when a job finishes, and its metrics, results and the lists are then refetched. A job state older than the
  one cached (by `updated_at`) never replaces it, so a slow poll cannot undo a cancel.
- **Policy Management.** `/policies` has three tabs. **Documents**: the library, grouped by category, with each revision's own approval (Draft, Needs review, Approved) beside its title, quick filters for them, search, category and scope filters, and identical copies (same SHA-256) shown as one entry that expands to its copies, each keeping its own approval; categories start collapsed (a search opens them), Select all picks one copy of each document shown, and deduplication (after a confirmation) keeps one copy of identical documents: the one a person picks with Keep this one, or for Deduplicate all the approved one where there is one; each other copy is marked a duplicate of it so it is hidden without being deleted and can be shown and restored. A document page shows its Markdown Formatted (rendered, raw HTML never run) or as Source, the exact stored text; a drawer approves one exact revision (submit, approve, return to draft with a note) and shows its history, and edits its description. **Rules**: a ruleset version's executable rules as compact rows (`ID | name | severity`) that expand to plain English (when each applies, what it checks, its limits, its outcome, its source document), with raw JSON folded under Technical details. A compiled version's rules can be edited with forms (name, outcome, severity, required evidence, conditions from the field catalog, param values in their fixed units); the changes are listed before and after and saved, with a required rationale, as the next draft version through `POST /v1/rulesets/{id}/edits`, never into the version shown. The compiler's refusal is listed with a link to each rule. **Review**: where the text pattern (regex) and AI (LLM) readings disagree, one issue at a time, Differences first (also Unconfirmed, Missing, All, Resolved), each beside the original Markdown scrolled to its clause and highlighted only where the API verified the position in the exact revision, with the disputed value marked; Approve interpretation, Correct value (field-specific controls, amounts kept exact) or Request changes, saved explicitly with feedback after showing exactly what will change (a saved correction is shown as a third, corrected interpretation), a Quick decision (one click: "No, it is not a limit" for a number that is not one, or take a reading), and for limits, each value tied to the rule parameter that holds it (what is compared, how, what it applies to, the rule whose threshold it is, where the clause states it, or that no parameter holds it), Previous/Next and arrow keys, and progress. A published version's decisions are shown read-only. `/documents` redirects here; document detail, compile and compare pages remain under `/documents/…` (compiling runs as a background job, `POST /v1/documents/extraction-jobs`, shown with a progress bar, the document being read, a refresh every second and, on failure, the reason and Try again; the job is kept in the address, so a reload keeps following it), and a version's page has the same Documents, Rules and Review tabs beside its next step: an Edit → Resolve → Approve → Publish summary from `GET /v1/rulesets/{id}/readiness` (rules changed, review issues left, validation errors, approval), plain-language blockers linked to their rule or issue, and Publish offered only when the API says the version may be published, after a confirmation.
- **Evaluations.** `/evaluations` lists jobs; `/evaluations/new` starts one; `/evaluations/datasets` holds dataset
  versions and uploads. Replay is the default and spends nothing new. Classification is chosen as an orchestrator
  (Direct, or LangGraph, where each configured node, in order, has its own model dropdown showing the provider and adapter; a changed node sends the whole node list as `classification.langgraph.backends` for that run only, while routing stays as the deployment configures it; reflection can be switched on or off for the run, with its revision retries, model calls per line and structured feedback context, each checked against the maximums discovery reports and sent only when it differs from the deployment's), a provider (OpenRouter) and a
  classification model, every option from `GET /v1/backends`; the translation model is chosen apart. The execution
  adapter a model needs (chat completions, or the Decisions API for Jev) follows from the model, is shown beside it,
  and is what the request sends as `classification.backend`, as earlier runs recorded it. The receipt validation page
  uses the same selector. The ruleset, classification and recording set are chosen automatically only when
  unambiguous and compatible (one ruleset name published for production; the set named after the adapter, or the
  only one with its answers); otherwise the form asks. Live needs the deployment's
  permission and a paid-call confirmation, plus at least one limit (spending, requests or receipts, combinable under
  Advanced settings) or, for no limit at all, the typed word "uncapped". Changing the mode or a limit withdraws the
  confirmations. Errors show once a field is used or the form submitted. Metrics of a running job are labelled
  provisional; a provisional pass is never shown as a pass.
- **Evaluation results and comparison.** A finished job's page shows its stored metrics in six numbered sections:
  classification, policy decisions (the 3×3 disposition matrix with a not-evaluated column and undecided counts),
  exception detection per rule, evidence, the keyword baseline, and coverage with error analysis. A run scored with an
  earlier version says so and can be rescored (no model call); latest and original scoring can be switched.
  `/evaluations/compare` keeps its side-by-side table of what ran, quality, latency and spending, and below it an
  Evaluation report: the same six kinds of metric for every run compared, in sequence (classification per category with
  confusion matrices, per-rule P/R/F1 with TP·FP·FN and a filter, disposition matrices, baseline wins and losses,
  evidence mismatches, diagnosed failures), each against the first run. Each run's metrics come from
  `GET /v1/evaluations/{id}/metrics`, shared with its detail page; provisional metrics cached while a run was watched
  are read again. A block with nothing to score shows "Not scored" with its reason, or "Not tested", never zero.
- **Export PDF.** Saves the view on screen (styles, charts, filters, open sections, the current table page) as one
  continuous PDF page, without fetching anything; JSON and CSV come from the API and hold the complete record.
- **Review fixes.** An issue the compiler refuses says what is wrong in plain words (for limits, which stated value the
  decision leaves out and the rule parameter that checks it) and offers its fixes in one click where they are certain:
  use the reading that states what the policy does, reuse an earlier version's decision on the same question, enter
  the correct value, or, where the compiled rule is what disagrees, correct the draft rule in place (compiled again as
  a new compilation). A clause the documents state with no rule is shown as "A new clause needs a rule", with a form
  for its rule (the clause's limits, severity and outcome filled in, the quoted words chosen from its sentences,
  conditions that may compare a field with a limit); the compiler checks it like any rule.
- **Policy lifecycle.** Each step goes through the API, which checks it again and asks the compiler at approval and
  publication; a refusal is shown with what the API reported. Approval offers only the publication modes discovery
  lists, never production for simulation readings, and never an uploaded artifact. Publication needs an explicit
  confirmation. Actors are claims the API records, not identities. Replay extraction needs an explicitly chosen
  recording set; live extraction needs the deployment's permission, a model and a paid-call confirmation.
