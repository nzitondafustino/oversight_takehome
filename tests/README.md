# YAML policy tests

Run from the project root:

```sh
.venv/bin/python -m pytest -q                 # everything
.venv/bin/python -m pytest -q tests/compiler  # the rule compiler only
```

A receipt fixture about policy may give `$lines` as `total_amount` or `total_amount_usd`, for totals that agree with its lines, so it stays consistent without repeating them. Monetary cases never use it.

Add cases to an existing file, or create any `.yaml` or `.yml` file anywhere
under `cases/`. Files are discovered recursively in sorted order. No Python
changes are needed for new cases in the supported suites.

Every file declares `suite: rules`, `suite: rulesets`, `suite: extraction`, `suite: agreement`, `suite: build_rules`, `suite: validate`, `suite: semantics`, `suite: models`, `suite: admission`, `suite: facts`, `suite: checks`, `suite: helpers`, `suite: replacements`, `suite: approvals`, `suite: carveouts`, `suite: uncertainty`, `suite: report`, `suite: evaluation`, `suite: hardening`, `suite: adversarial`, `suite: adversarial_policy`, `suite: policy_decisions`, `suite: completeness`, `suite: inevitable`, `suite: offline_eval`, or `suite: submission_integrity`.
Optional `templates` can hold YAML anchors; anchors are local to a file.

| Suite | Sections | Input keys | Expected result |
| --- | --- | --- | --- |
| submission_integrity | exploits | receipts, reason | `error`: the exact error that refuses it, and it can get no result at all. |
| submission_integrity | refused | receipt | Error regex: these amounts contradict each other. |
| submission_integrity | admitted | receipt | Values in its JSON form, as admitted and unchanged. |
| submission_integrity | unresolved | receipt | `disposition`, `decided`, `active` and `open`, from `evaluate_receipts`: missing evidence is not a contradiction. |
| submission_integrity | adversarial | result, receipt | `result` is reconciled, unresolved or rejected. A regex if rejected. Otherwise `monetary`, the states, and any of `disposition`, `decided`, `active`, `open_includes` and `open_excludes`. |
| submission_integrity | unverified_usd | receipts, complete | `rules` by receipt, one result whatever USD values the receipts with no verifiable conversion are given, with no trace of them. |
| submission_integrity | decided | receipt | `disposition`, `decided`, `active`, `monetary` and reason codes. |
| submission_integrity | itemization_claims | receipt | The disposition, or `refused`, with `itemization_complete` absent, true and false. |
| submission_integrity | unsupported_refused | receipt | Error regex: outside the input contract. |
| rules | accepted, refused | rule | Accepted: nonempty mapping of model fields to exact values. Refused: validation error regex. |
| rulesets | accepted, refused | ruleset | Accepted: rule_ids in published order and params keyed by rule ID. Refused: validation error regex. |
| extraction | documents | policy_doc | Title, rule_ids in order, and how each context block starts. |
| extraction | clauses | policy_doc, rule_id | Every extracted field, plus where the clause text starts and ends. |
| extraction | inline | text | Same as documents, plus optional exact clause `texts` and `thresholds`. |
| extraction | refused | text | Error regex. |
| agreement | cases | text, response | Each rule with the fields that did not agree, by status; `{}` means all agreed. |
| agreement | refused | response | Error regex. |
| build_rules | accepted | text, logic | Fields the built rule must have. |
| build_rules | refused | text, logic | Error regex. |
| validate | cases | text, logic | Exact ordered list of issues. |
| semantics | conditions | condition, facts | `result`: true or false. |
| semantics | rules | rule_id, facts | `fires`: whether the real rule catches these facts. |
| semantics | replacements | facts | `replaced`: the real rules out of play for this receipt. |
| models | accepted | model, data | Values in its JSON form, where a name can go deeper: `line_items.0.category`. Also round-trips and cannot be changed. |
| models | refused | model, data | Error regex. |
| models | json_refused | model, data | Error regex, with `data` sent as JSON text. |
| models | left_out_refused | model, data, field | Error regex, with `field` taken out of `data`. |
| models | evidence_numbers | decimal | `value`: the exact text kept for that Decimal. |
| models | classified | line, output | Values on the new line. What the receipt printed must not change. |
| models | classified_refused | line, output | Error regex. |
| models | receipt_classified | receipt, outputs | Values on the new receipt. Its own fields must not change. |
| models | receipt_classified_refused | receipt, outputs | Error regex. |
| admission | admitted | receipt | Values in its JSON form. |
| admission | left_out_admitted | receipt, field | Values in its JSON form, with `field` taken out of `receipt`. |
| admission | refused | receipt | Error regex. |
| admission | left_out_refused | receipt, field | Error regex, with `field` taken out of `receipt`. |
| admission | regional | rule_id, receipt | `fires` on the first line of the admitted receipt, and `replaced`: the real rules out of play. |
| admission | totals | field, decimal | `total`: the exact text kept for Decimal(`decimal`) in that total, from a mapping and a built Receipt. |
| admission | totals_refused | field, decimal | Error regex, from a mapping and a built Receipt. |
| admission | readmitted | receipt | Values on a Receipt built directly from `receipt`, then admitted. |
| admission | readmitted_refused | receipt | Error regex: a Receipt built directly from `receipt` is not admitted. |
| admission | copied_refused | receipt, update | Error regex: an admitted receipt copied with `update` is not admitted again. |
| admission | catalog_refused | receipt, fields | Error regex, with `fields` as the catalog. |
| admission | states | line, field, min_confidence | `state`: the classification state, also after JSON. |
| admission | states_refused | line, field, min_confidence | Error regex. |
| admission | state_catalog_refused | line, field, fields | Error regex, with `fields` as the catalog. |
| facts | receipt, item | receipt | Named facts and their exact values, where 0, `"0"`, false and null all differ. |
| facts | catalog_refused | receipt, fields | Error regex, with `fields` as the catalog. |
| facts | receipt_rules, item_rules | rule_id, receipt | `fires`: whether the real rule catches these facts. |
| facts | replacements | receipt | `replaced`: the real rules out of play for this receipt. |
| checks | findings | receipt | Any of `findings`, by rule_id, severity, scope and line_ids; `pending_helpers`, by rule_id and helper; `undetermined`, by rule_id and line_id; and `complete`. From the real rules. |
| checks | details | rule_id, receipt | Values on the first finding for that rule, in its JSON form. |
| checks | rules | rules, receipt | As in findings, from these rules. |
| checks | refused | rules, receipt | Error regex: these rules cannot be run. |
| helpers | outcomes | rule_id, receipt_id, complete, receipts | `findings` and `undetermined` from that real helper rule, in a context of `receipts`. |
| helpers | details | rule_id, receipt_id, complete, receipts | Values on the first finding, in its JSON form. |
| helpers | runs | rule_ids, receipt_id, complete, receipts, with_context | Any of `findings`, `pending_helpers`, `undetermined` and `complete` from `run_checks` on those rules. |
| helpers | baggage | receipt_id, complete, receipts | `line_ids`, `total_usd` and `within` from `baggage_allowance`. |
| helpers | contexts | customer_id, complete, receipts | `receipt_ids` held, in order, and `complete`. |
| helpers | refused | customer_id, complete, receipts | Error regex: this context cannot be built. |
| helpers | recipients | line_id | `recipient_name` and `recipient_type` a classifier would read from that real line. Evaluation only. |
| replacements | real | rule_ids, receipt_id, complete, with_context, receipts | Any of `active`, `unaffected`, `replaced`, `possible`, `undetermined`, `moot`, `pending`, `moot_pending` and `complete`, from those real rules. |
| replacements | made_up | rules, findings, undetermined, pending | As in real, from resolving the findings given. |
| replacements | refused | rules, findings, undetermined, pending | Error regex: this resolution cannot go ahead. |
| approvals | real | receipt | Any of `active`, `replaced`, `waived`, `gaps`, `undetermined`, `possible` and `complete`, from the real ruleset. |
| approvals | made_up | rules, references, findings, undetermined, pending | As in real, from resolving the findings given. |
| approvals | refused | rules, references, findings, undetermined, pending | Error regex: this resolution cannot go ahead. |
| carveouts | real | receipt_id, complete, receipts | Any of `active`, `unaffected`, `replaced`, `waived`, `excused`, `rejected`, `undetermined_carveouts`, `undetermined` and `complete`, from the real ruleset. |
| carveouts | made_up | rules, receipt_id, complete, receipts | As in real, from these rules. |
| carveouts | refused | rules, receipt_id, complete, receipts | Error regex: this resolution cannot go ahead. |
| uncertainty | assessed | receipt_id, complete, receipts | Any of `category`, `secondary`, `eligible` and `exempt`. |
| uncertainty | evaluated | receipt_id, complete, receipts | Any of `active`, `candidates`, `excused`, `open`, `open_lines`, `carve_open`, `signals`, `meal`, `complete` and `decided`, from every step on the view. `open_lines_include` lists entries that must be among them. |
| uncertainty | refused | rules | Error regex: no threshold to assess with. |
| report | real | receipt_id, complete, receipts | Any of `exceptions`, `unresolved`, `replaced`, `waived`, `excused`, `uncitable`, `signals`, `uncertainty`, `groups_of_uncertainty`, `open`, `open_includes`, `decided`, `sources` and `source_severities`. |
| report | made_up | rules, receipt_id, complete, receipts | As in real, from these rules. |
| report | refused | rules, receipt_id, complete, receipts | Error regex: this report cannot be made. |
| evaluation | cases | receipt_id, complete, receipts | Any of `disposition`, `decided`, `reasons`, `exceptions`, `signals`, `uncertainty`, `open`, `unresolved` and `audit`, from `evaluate_receipts`. |
| evaluation | refused | customer_id, receipts | Error regex: this run cannot go ahead. |
| adversarial | scenarios | receipts, complete | By receipt ID, any of `disposition`, `decided`, `active`, `severities`, `unresolved`, `uncertainty`, `audit`, `open_includes` and `open_excludes`. Also holds in every receipt order. |
| adversarial_policy | bounds | bound, receipts, complete | By receipt ID, any of `disposition`, `decided`, `active`, `severities`, `cited`, `rules`, `unresolved`, `uncertainty`, `audit`, `replaced`, `waived`, `evidence`, `cites_include` and `explains`. `bound` is checked against the rule's param and worked through on its own. |
| adversarial_policy | replacement | receipts, complete | As in bounds, and every group's citation and provenance must be its rule's. |
| adversarial_policy | isolation | receipts, complete | As in bounds. |
| adversarial_policy | conflicts_refused | receipts | Error regex. |
| adversarial_policy | separate_runs | runs | By customer, then as in bounds, with the runs made in either order. |
| adversarial_policy | ladders | rule, vary, amounts, receipts, complete | `statuses`: the rule's status at each amount, and the status may only move one way as the amount does. |
| adversarial_policy | spend_bounds | vary, amounts, receipts | `least` and `most` meal spend per person at each amount. |
| adversarial_policy | unrelated | receipts, other | `rules`, and the first receipt's result is the same with and without `other`. |
| adversarial_policy | ineligible | rule, receipts, discount | `status`, the same with and without `discount`. |
| adversarial_policy | categorical | line_id, receipts | `rule`, `severity` and `lines`, the same however `line_id` is made uncertain. |
| policy_decisions | decisions, partial | receipts, complete | As in adversarial_policy bounds. Also holds in any receipt or line order. |
| policy_decisions | refused | receipts | Error regex. |
| completeness | refund_rules | rule, source, receipt, line | `purchase`, `refund` and `zero`: the rule's status with the line at each sign. `beside`: the lines it cites with a purchase and a refund together. `source` must be in the policy document. |
| offline_eval | rates, prf, rule_scores, dispositions, exact, proofs, classification, buckets, calibration, families | Their own inputs: see the file | Hand-worked scores. `expected` names only the values compared, at any depth. |
| offline_eval | sources_refused | prediction_set | Error regex: this set of classifications cannot be loaded. |
| inevitable | cases, at_the_cap | receipts, complete | As in adversarial_policy bounds, and by receipt ID `inevitable`: null, or the `rule_ids` and number of `readings`, with the rules of each reading checked against a rerun. `cases` also hold in any receipt or line order. |
| completeness | unknowns | condition, facts | `result`: true, false or null when it turns on field `u` having no value. |
| completeness | usd, officials | receipts, complete | As in adversarial_policy bounds, and `notes`, text a group's source notes must hold. Also holds in any receipt or line order. |
| hardening | directives | directive, receipts | `disposition`, from a ruleset with EXC-01 changed as `directive` says. |
| hardening | directives_refused | directive, receipts | Error regex, as above. |
| hardening | tampered_refused | receipts, tamper | Error regex: `check_run` on a real run changed as `tamper` says. |
| hardening | completeness | patches, receipts, complete | Any of `disposition`, `decided`, `reasons`, `unresolved`, `open` and `audit`, on rules changed by `patches`. |
| hardening | ties | patches, extra_rules, receipts, complete | The same result whatever order the rules come in, plus any of `replaced_by`, `excused_by` and `notes`. |

Each case also needs a nonempty `id` and `expected`. IDs must be unique within
a suite and section across all files. Unknown keys, duplicate YAML keys,
invalid regexes, and empty sections fail collection with the source path.
Omit sections that a file does not use.

For example, create `cases/compiler/one_rule.yaml`:

```yaml
suite: extraction
inline:
  - id: a document with one rule
    text: "**AA-01 — One.** First clause."
    expected:
      title: null
      rule_ids: [AA-01]
      context_starts: []
```

Test IDs include the relative YAML path and case ID, making failures easy to
locate and select with pytest `-k`. Ruleset acceptance cases also verify
canonical JSON round trips. The loader reads the catalog once per collection.

New types of behavior outside these suites require a Python runner;
new inputs, boundary cases, and regressions for existing behavior require YAML only.
