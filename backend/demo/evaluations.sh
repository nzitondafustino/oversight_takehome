#!/usr/bin/env bash
# Replay-only evaluation demo: calls no paid model. Run it with the stack up (docker compose up -d from backend/).
# Needs curl and jq. Reads the repository's files beside this script; writes its working files to the current folder.
#
# 1. Publishes a ruleset version through the lifecycle (the README's end-to-end demo, steps 1 to 5), unless
#    RULESET names a version already published for production.
# 2. Uploads the project's 32 labelled receipts (JSONL) and the five-receipt YAML example.
# 3. Evaluates the 32 receipts with two backends, openrouter and jev, and the five with LangGraph and reflection.
# 4. Polls each job, prints its metrics and spending, compares the two comparable runs, and exports one.
set -euo pipefail
API=${API:-localhost:8000}
post() { curl -sf -X POST "$API$1" -H 'content-type: application/json' --data-binary "${2:-@-}"; }
get() { curl -sf "$API$1"; }
BACKEND=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(dirname "$BACKEND")

if [ -z "${RULESET:-}" ]; then
  for f in "$REPO"/data/policy-docs/*.md; do
    jq -n --arg fn "$(basename "$f")" --rawfile t "$f" '{filename: $fn, content: $t}' | post /v1/documents | jq -c '{document_id, revision}'
  done | jq -s . > refs.json
  jq '{documents: ., recording_set: "live"}' refs.json | post /v1/documents/extractions > extraction.json
  EXTRACTION=$(jq -r .extraction_id extraction.json)
  jq --arg q "is a **high** exception, regardless of stated business purpose" '{actor: "r.eviewer", extraction_sha256,
      decisions: [.extraction_sha256 as $x | .review_required[] | {comparison_id, extraction_sha256: $x, reviewer: "r.eviewer"}
        + (if .rule_id == "TECH-01" then {action: "override", value: $q, rationale: "the quote the executable rule signs"} else {action: "accept_regex"} end)]}' extraction.json \
    | post /v1/documents/extractions/$EXTRACTION/review > review.json
  RULESET=$(jq -n --arg x "$EXTRACTION" --arg r "$(jq -r .review_id review.json)" '{name: "policies", extraction: {extraction_id: $x, review_id: $r}}' \
    | post /v1/rulesets | jq -r .ruleset_id)
  for step in submit review approve publish; do
    jq -n --arg s $step '{} + (if $s == "review" then {actor: "r.eviewer", decision: "accepted"} elif $s == "approve" then {actor: "a.pprover"} else {} end)' \
      | post /v1/rulesets/$RULESET/$step > /dev/null
  done
fi
echo "ruleset $RULESET: $(get "/v1/rulesets/$RULESET" | jq -r '"\(.status) for \(.publication_mode)"')"

# Datasets: the same labelled receipts as data/receipts.jsonl, and the YAML example. Each is an immutable version.
GOLDEN=$(curl -sf -X POST "$API/v1/evaluations/datasets" -F name=golden -F file=@"$REPO/data/receipts.jsonl" | jq -r .dataset_id)
FIVE=$(curl -sf -X POST "$API/v1/evaluations/datasets" -F file=@"$BACKEND/examples/langgraph-golden-5.yaml" | jq -r .dataset_id)
get "/v1/evaluations/datasets" | jq -c '.items[] | {dataset_id, name, version, format, canonical_sha256: .canonical_sha256[:12], stats}'

# Evaluations: replay, the default. Each POST only queues a job; the worker runs it.
queue() { jq -n --arg d "$1" --arg r "$RULESET" --arg l "$2" --argjson cls "$3" \
  '{dataset_id: $d, ruleset_id: $r, label: $l, classification: $cls, idempotency_key: "demo-\($l)"}' | post /v1/evaluations | jq -r .evaluation_id; }
OPENROUTER=$(queue "$GOLDEN" openrouter '{"replay_set": "openrouter"}')
JEV=$(queue "$GOLDEN" jev '{"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"}')
LANGGRAPH=$(queue "$FIVE" langgraph-reflection '{"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": {"enabled": true}}}')

# Poll each job until it finishes.
for id in $OPENROUTER $JEV $LANGGRAPH; do
  until get "/v1/evaluations/$id" | jq -e '.status | IN("completed", "failed", "cancelled", "budget_exhausted")' > /dev/null; do
    get "/v1/evaluations/$id" | jq -c '{evaluation_id, status, progress}'; sleep 1
  done
  get "/v1/evaluations/$id" | jq -c '{label, status, result_context, progress: .progress.processed_receipts, summary, spend: .spend_summary}'
done

# Metrics, with their denominators; spending by purpose.
for id in $OPENROUTER $JEV $LANGGRAPH; do
  get "/v1/evaluations/$id/metrics" | jq -c '{category_accuracy: .classification.accuracy | "\(.numerator)/\(.denominator)", macro_f1: .classification.macro.f1,
    disposition_accuracy: .disposition.accuracy | "\(.numerator)/\(.denominator)", findings: .findings.micro, outcomes: .classification.outcomes}'
  get "/v1/evaluations/$id" | jq -c '.spend | {new_spend_usd, cost_completeness, historical, by_purpose: (.by_purpose | map_values(.calls.replayed))}'
done

# History, a filter, and the receipts where Jev got a labelled line wrong.
get "/v1/evaluations?dataset_id=$GOLDEN" | jq -c '.items[] | {label: .label, backend: .configuration.classification_backend, status, accuracy: .summary.category_accuracy}'
get "/v1/evaluations/$JEV/results?classification_correct=false" | jq -c '.items[] | {receipt_id, lines: .scoring.lines | map_values(select(.outcome != "correct"))}'

# The two runs on one dataset and ruleset, side by side; then the LangGraph run, which is marked not comparable.
jq -n --arg a "$OPENROUTER" --arg b "$JEV" '{evaluation_ids: [$a, $b]}' | post /v1/evaluations/compare \
  | jq -c '.comparability, (.evaluations[] | {backend, model_label, accuracy: .category_accuracy.value, macro_f1, disposition: .disposition_accuracy.value, calls: .provider_calls.replayed, new_spend_usd, historical: .historical_cost_usd.cost_usd})'
jq -n --arg a "$OPENROUTER" --arg b "$LANGGRAPH" '{evaluation_ids: [$a, $b]}' | post /v1/evaluations/compare | jq -c .comparability

# Exports: deterministic JSON and formula-safe CSV.
curl -sf -OJ "$API/v1/evaluations/$JEV/export?format=json"
curl -sf -OJ "$API/v1/evaluations/$JEV/export?format=csv"
ls -l "$JEV.json" "$JEV.csv"
