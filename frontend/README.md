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

With Docker, from `backend/`: `docker compose up --build -d`. The app is served at http://localhost:3000.

- **API base URL.** `VITE_API_BASE_URL` defaults to `/api` on the same origin. The API has no CORS middleware, so the
  Vite dev server and the container's nginx (`nginx.conf`) proxy `/api` to it, and the backend stays unchanged.
- **Design tokens.** Every color is defined once in `src/styles/globals.css` and used through Tailwind utilities
  (`bg-primary`, `text-status-flag-text`) or `var(--color-…)` in charts.
- **Company-wide policies.** All employees share versioned policies. Country and entity select regional rules.
  All relevant receipts are submitted together; context completeness defaults to true.
- **Data.** Every value comes from the API. The API has no aggregate endpoints, so dashboard totals are summed over one
  page (up to 100 records), and each card states the records it covers. Money is summed exactly from the API's decimal
  strings.
- **Polling.** Only evaluation jobs that are `queued`, `running` or `cancel_requested` are polled, every 2 s. Polling
  stops when a job finishes, and its metrics, results and the lists are then refetched. A job state older than the
  one cached (by `updated_at`) never replaces it, so a slow poll cannot undo a cancel.
- **Evaluations.** `/evaluations` lists jobs; `/evaluations/new` starts one; `/evaluations/datasets` holds dataset
  versions and uploads. Replay is the default and spends nothing new. Classification is chosen as an orchestrator
  (Direct or LangGraph, whose model nodes are listed with their providers), a provider (OpenRouter) and a
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
- **Policy lifecycle.** Each step goes through the API, which checks it again and asks the compiler at approval and
  publication; a refusal is shown with what the API reported. Approval offers only the publication modes discovery
  lists, never production for simulation readings, and never an uploaded artifact. Publication needs an explicit
  confirmation. Actors are claims the API records, not identities. Replay extraction needs an explicitly chosen
  recording set; live extraction needs the deployment's permission, a model and a paid-call confirmation.
