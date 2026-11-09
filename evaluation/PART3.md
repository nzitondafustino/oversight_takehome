# Part 3 — Evaluation report

Every number below comes from evaluations stored by the API, rescored with scoring version `evaluation-metrics/3`. Their original scores (`evaluation-metrics/1`) are kept beside the rescores and never overwritten. The dashboard shows the same numbers from the same stored records: Evaluations → an evaluation → Metrics for one run, and Evaluations → Compare, whose Evaluation report sets every metric below side by side for the runs chosen.

| Run | Evaluation | Dataset | Classifier | Mode |
|---|---|---|---|---|
| Golden · Haiku | `eval_6af004a666c2b68e0c83fc43` | golden_dataset v1 (32 receipts, 79 labelled lines) | anthropic/claude-haiku-5.5 | live |
| Golden · Jev | `eval_6aeff3dc66c2b68e0c83fc3f` | golden_dataset v1 | typesafe/jev-1.14 | live |
| Robustness · Haiku | `eval_6af167d991af8ea8718baa77` | robustness_dataset v1 (12 receipts, 17 labelled lines) | anthropic/claude-haiku-5.5 | live |
| Robustness · Jev | `eval_6af1672191af8ea8718baa76` | robustness_dataset v1 | typesafe/jev-1.14 | live |

All four use ruleset `policies` v1/v2 (artifact `83c9d13d…`, the same rules) with translation by tencent/hy-mt2-30b-a3b. The robustness set is developer-authored and labelled. It is a challenge set, not an independent benchmark.

## Metrics and their formulas

The code lives in `src/expense_validator/evaluation/`. The backend composes it in `backend/src/expense_api/services/evaluation_metrics.py`. A rate is `numerator / denominator`, shown with both counts and a Wilson 95% interval. With a zero denominator it has no value: it is shown as "—", never as 0 or 1.

**Classification** (`metrics.classification_scores`), per labelled line:
- Category accuracy = correct lines / all labelled lines. A line with no category, a classifier failure, or a receipt that failed counts as wrong. Accuracy when predicted and coverage are kept as before.
- Per category c: precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2TP/(2TP+FP+FN), support = TP+FN. A category never predicted has precision 0.
- Macro precision, recall and F1 = the mean of the per-category values, over the categories that appear in the answers. This project adds no categories: the classifier is constrained to the taxonomy, so mapping back is the identity. A predicted category that no answer has is listed (`extra_predicted`), never dropped.
- Restricted precision, recall and F1: the positive class is `restricted = true`, counted independently of the categories. The 2×3 matrix is answer (true, false) by prediction (true, false, none). A missing restricted value counts as not restricted.

**Disposition** (`metrics.disposition_scores`): the 3×3 matrix over the engine's own dispositions (flag, review, pass). A fourth column holds receipts that could not be evaluated; they are never correct. A pass or flag that the engine did not fully decide (`decided = false`) stays in its cell, and its undecided count is shown beside it, so a provisional pass is never shown as a cleared one. False pass, missed flag, false flag, abstention and undecided rates are unchanged.

**Exception detection** (`detection.detection_scores`): expected exceptions and review signals are matched one to one on (receipt, rule ID).
- TP = matched; FP = raised and not expected (an extra of the same rule on a receipt is a duplicate); FN = expected and not raised. A receipt that could not be evaluated misses all its expected findings.
- A correct receipt disposition never counts as a detected finding.
- Micro = the same formulas over all rules. "Without uncertainty signals" leaves out EXC-03, which the answers never contain.
- Macro = the mean over **tested** rules: those with at least one expected finding. A tested rule never raised has precision 0. A rule with no expected finding is "not tested", and its precision, recall and F1 are undefined, never perfect.
- Missed and unexpected findings are listed. The right lines under the wrong rule are counted as an attribution confusion.

**Evidence** (`detection.evidence_scores`), for matched exceptions only:
- Exact = cited line set == expected line set (as sets, so order does not matter), divided by matched exceptions whose answer lists lines.
- Line precision = |cited ∩ expected| / |cited|; line recall = |cited ∩ expected| / |expected|, summed over matches.
- An answer without line IDs is "not scorable". Signals cite no lines.

**Keyword baseline** (`keyword_baseline.py`, `keyword-baseline/1`):
- What it reads: whole-word, case-insensitive terms from the taxonomy's examples and the policy clauses' item lists, plus common names for "beer, wine, spirits", matched over each line's raw description only. It uses no translation, model, merchant code or answers.
- How it's scored: with the same functions on the same lines as the engine. Category scores compare only the restricted-category lines, the only categories it names.
- What it can't do: it raises no finding and decides no disposition, so those are reported as not comparable.

**Error analysis** (`error_analysis.diagnose`): each observed failure goes to a stage by fixed rules, and names the rule that placed it.
1. A wrong, missing or failed category is translation if the line is non-English and untranslated, and classification otherwise.
2. A line left uncertain (EXC-03) is classification.
3. Findings, signals and dispositions a root cause explains are listed under it.
4. Otherwise, a wrong finding is rule logic.

Retrieval is never attributed: the engine retrieves no policy text at run time (its metadata says `retriever: none`). A receipt that did not run is an execution failure, not a stage failure.

**Rule coverage** (`coverage.rule_coverage`) is measured on the evaluated receipts with the engine's own facts and conditions:
- **Gate held:** the rule's `applies_when` is true on the receipt.
- **Subject:** a line the rule's selector matches is present. **Execution coverage** means a rule met its subject at least once.
- **Positive-case coverage:** the answers expect the rule at least once.
- Process rules apply to every receipt; guidance and not-evaluable rules never run.

## 1. How accurately are line items classified?

| | Golden · Haiku | Golden · Jev | Robustness · Haiku | Robustness · Jev |
|---|---|---|---|---|
| Category accuracy | 79/79 (100%) | 78/79 (98.7%) | 17/17 (100%) | 15/17 (88.2%) |
| Macro P · R · F1 | 1.000 · 1.000 · 1.000 | 1.000 · 0.995 · 0.997 | 1.000 · 1.000 · 1.000 | 0.958 · 0.920 · 0.924 |
| Coverage | 100% | 98.7% (1 classifier failure) | 100% | 94.1% (1 failure) |

Jev's misses are:
- Golden: RCP-017-L2 "Gratuity". The classifier failed with "essential_toiletry contradicts category service_fee".
- Robustness: RBX-001-L1 "Guinness 0.0 pint draught" was classified as alcohol.
- Robustness: RBX-009-L1 "Stadium suite tickets x4" failed with "venue_type contradicts category airfare".

## 2. How accurately are restricted expenses identified?

| Restricted (positive = true) | Golden · Haiku | Golden · Jev | Robustness · Haiku | Robustness · Jev |
|---|---|---|---|---|
| TP · FP · FN · TN | 21 · 0 · 0 · 58 | 21 · 0 · 0 · 57 (+1 no value) | 5 · 0 · 0 · 12 | 5 · 1 · 0 · 10 (+1 no value) |
| Precision · Recall · F1 | 1.000 · 1.000 · 1.000 | 1.000 · 1.000 · 1.000 | 1.000 · 1.000 · 1.000 | 0.833 · 1.000 · 0.909 |

## 3. Does it detect the right exceptions and cite the right evidence?

**Disposition** (rows expected, columns predicted; no receipt failed to run):

| Run | flag→flag | review→review | pass→pass | Errors | Undecided |
|---|---|---|---|---|---|
| Golden · Haiku | 18 | 5 | 9 | none: 32/32 | 1 flag |
| Golden · Jev | 18 | 5 | 9 | none: 32/32 | 2 flags |
| Robustness · Haiku | 6 | 1 | 4 | 1 flag sent to review (RBX-012): 11/12 | 2 of the flags, the RBX-012 review, and the 1 correct review |
| Robustness · Jev | 6 | 1 | 3 | 1 flag sent to review (RBX-009), 1 pass flagged (RBX-001): 10/12 | the RBX-009 review only |

**Exception detection, micro:**

| | Golden · Haiku | Golden · Jev | Robustness · Haiku | Robustness · Jev |
|---|---|---|---|---|
| Expected · raised | 37 · 38 | 37 · 38 | 10 · 13 | 10 · 11 |
| TP · FP · FN | 37 · 1 · 0 | 36 · 2 · 1 | 9 · 4 · 1 | 9 · 2 · 1 |
| P · R · F1 | 0.974 · 1.000 · 0.987 | 0.947 · 0.973 · 0.960 | 0.692 · 0.900 · 0.783 | 0.818 · 0.900 · 0.857 |
| F1 without EXC-03 | 1.000 | 0.986 | 0.947 | 0.900 |
| Macro F1 (tested rules) | 1.000 (23 rules) | 0.991 (23) | 0.952 (7) | 0.810 (7) |

**Per rule, golden set.** All 23 tested rules score precision and recall 1.000 under Haiku:
- **Rules:** ALC-01/02/03, DOC-01, DOC-03, GIFT-01/02/04, HOUSE-01, LAUNDRY-01, MCC-01, MCC-02 (7), MEAL-01, PERS-01/02/03, REG-01, TECH-01/02/04, TIME-01/02/03.
- **Under Jev:** the same, except DOC-03 (3 expected, 2 raised, recall 0.667).
- **EXC-03** has no positive case, so its precision is 0 (1 raised under Haiku, 2 under Jev).
- **Robustness set:** Haiku misses GIFT-04 once (recall 0.5). Jev raises ALC-01 once wrongly (precision 0.5) and misses ENT-01 (recall 0).

**Evidence**: every matched exception cites exactly the expected lines, in all four runs (28/28, 27/27, 6/6, 6/6). Line precision and line recall are both 1.000, and no answer lacked line IDs. Evidence errors would show up here as mismatches; none were observed.

## 4. Better or worse than a keyword baseline?

| Restricted decision | Golden: engine (Haiku) | Golden: keyword | Robustness: engine (Jev) | Robustness: keyword |
|---|---|---|---|---|
| TP · FP · FN | 21 · 0 · 0 | 9 · 2 · 12 | 5 · 1 · 0 | 1 · 0 · 4 |
| Precision · Recall · F1 | 1.000 · 1.000 · 1.000 | 0.818 · 0.429 · 0.563 | 0.833 · 1.000 · 0.909 | 1.000 · 0.200 · 0.333 |
| Category accuracy, restricted-category lines | 1.000 | 0.429 | 1.000 | 0.200 |

The engine wins on recall and loses no line to the baseline on golden under Haiku: 14 lines go its way and none the other.

Where the engine wins: what keywords can't see.
- **Translated text:** "Vino tinto Rioja Reserva", "Weisswein 0,2l", "生ビール 中", "iPad Air 11インチ", "タブレットケース".
- **Brands and wine names:** "Marlboro Gold pack", "Chianti Classico", "Barolo 2016", "Draft IPA", "Kindle Paperwhite".
- **Household items not in its lists:** "Tide Pods", "Dish soap".
- **Names that lie:** "Beer battered cod" and "Rum cake slice" are food, but the baseline flags them.

Where the baseline wins, it's against Jev:
- **RBX-001 "Guinness 0.0":** Jev calls it alcohol; the baseline matches no term, so it's correct.
- **RBX-009 "Stadium suite tickets":** Jev failed on the line, while the baseline gives the right not-restricted answer.
- **RCP-017 "Gratuity":** Jev failed the line; the baseline is right by default.

No line was wrong under both. The baseline's precision is honest here: 0.818 on golden and 1.000 on robustness, but only because it says "restricted" so rarely. Findings and dispositions are not comparable: the baseline produces neither.

## 5. Where does it fail, and which stage is responsible?

Observed failures from the stored runs, as the error analysis placed them (each is shown on the dashboard with its evidence):

1. **RBX-001-L1 "Guinness 0.0 pint draught"** (robustness · Jev) — **classification.**
   - Expected food_beverage, not restricted; Jev answered alcohol, restricted.
   - Led to: an unexpected ALC-01 high exception, and a **pass flagged**.
   - The decisions model read the brand and missed "0.0". Improvement: guidance and an example for alcohol-free variants. The LLM classifier gets this line right.
2. **RBX-009-L1 "Stadium suite tickets x4"** (robustness · Jev) — **classification.**
   - The classifier failed: its venue_type answer contradicted the category airfare it chose, and the line was refused.
   - Led to: ENT-01 (an entertainment venue without VP approval) missed, an EXC-03 signal, and a **flag sent to review**.
   - Improvement: constrain the attribute questions to the chosen category before asking them.
3. **RBX-012-L1 "Fruit basket for L. Haddad, Ministry of Health"** (robustness · Haiku) — **classification**, from uncertainty.
   - The category, gift, was right but its confidence was below the EXC-03 threshold. The line was left out of the rules that read the category, so GIFT-04 (gifts to government officials, critical) stayed open.
   - Led to: GIFT-04 missed, and a **flag sent to review**.
   - It failed safe: a person still sees it, but a critical rule went unraised. Improvement: calibrate confidence (it is the model's own coarse estimate), or let GIFT-04 run on a recipient match even when the category is uncertain.
4. **RCP-017-L2 "Gratuity"** (golden · Jev) — **classification.**
   - The classifier failed: it answered essential_toiletry on a service fee and was refused.
   - Led to: DOC-03 (expense type does not match the contents) missed, and an EXC-03 signal. The disposition was still right.

No failure was placed in **translation**: every non-English line was translated and classified correctly. None was placed in **retrieval**, which this engine doesn't have. None was placed in **rule logic** in these runs: every missed or extra finding traces to a line the classifier misread or was unsure of. The rule-logic path is covered by test cases (`tests/cases/offline_eval/quality.yaml`), not invented here. The golden set's only "failure" under Haiku is RCP-013: recipient types were uncertain, which raised an EXC-03 signal. The answers never expect that signal, although the receipt's review disposition was correct.

## Rule coverage (golden set)

- **Ruleset:** 42 rules, of which 35 are executable checks and carve-outs.
- **Execution coverage, 28/35:** these rules met a receipt holding what they examine.
- **Positive-case coverage, 23/35:** the answers expect these rules at least once.
- **Never met their subject:**
  - DOC-02 and DOC-04: the gate never held, as no receipt is illegible or lacks a rate.
  - MEAL-02: the gate never held.
  - ENT-01, ENT-02, GIFT-03 and TECH-03: the gate held but there were no venue, gambling, employee-gift or handset lines.
- **Ran but have no positive case:** HEALTH-01, PERS.BAGGAGE, REG-02, REG-03 and REG-04.
- **Never run by design:** MCC-03 (guidance) and TIME-04 (not evaluable).

So the golden set can't tell how well 12 executable rules detect violations. The robustness set adds positive cases for ENT-01 and HEALTH-01. Helper rules (MEAL-01, GIFT-01, TIME-03…) examine every receipt their gate admits, so their subject count is the gate count, an upper bound.

## Reflection

**What it does well.**
- **Classification:** on the golden set, the LLM classifier with translation gets every line right, including the multilingual lines and the "names lie" traps. Restricted recall is 1.000 on both sets and with both classifiers.
- **Evidence:** cited evidence is exact wherever an exception matches.
- **Separation:** detection is deterministic once the lines are read. No rule-logic failure appears in these runs.

**Where it fails.**
- **The classifier is the single point of failure:** the decisions model (Jev) misreads alcohol-free variants and refuses lines whose attributes contradict their category.
- **Uncertainty fails safe at a cost:** an uncertain category keeps a line out of the rules that read it. That is safe, since the receipt goes to review, but it can leave a critical rule (GIFT-04) unraised.
- **Strict scoring penalises EXC-03:** every uncertainty signal counts as a false positive, because the answers never include one.

**What the baseline reveals.** Keyword matching is high-precision and low-recall: about 0.43 recall on golden. Its failures are translation, brands and names that lie, exactly what the dataset was built to test. It beats the engine only where the engine's classifier is wrong or refuses a line. That is an argument for better classifier guidance, not for keywords.

**Limits of the golden set.**
- **Size:** 32 receipts give wide intervals. 100% on 32 receipts has a 95% interval down to 89%.
- **Untested rules:** 12 executable rules have no positive case.
- **Not held out:** the prompts were written with this data's taxonomy in view, so golden results flatter the classifier. The robustness set (12 receipts) is the only check against that, and it is developer-written.

**Next, with more time:**
1. A larger, held-out, independently labelled set with positive cases for every rule.
2. Cost-weighted scoring: a missed critical violation against a reviewer-hour wasted on a false flag or uncertainty review.
3. Calibrating classifier confidence against observed accuracy, and a reliability curve per classifier.
4. An ablation of what carries detection: line description, MCC and receipt metadata.
5. Scoring the robustness runs' EXC-03 reviews by whether a person would have caught the case, not as plain false positives.

## Reproducing

```sh
# Score a finished evaluation with the current scoring version, keeping its original scores:
curl -X POST localhost:8000/v1/evaluations/<evaluation_id>/rescore
curl localhost:8000/v1/evaluations/<evaluation_id>/metrics                    # the latest scoring
curl "localhost:8000/v1/evaluations/<evaluation_id>/metrics?scoring=original" # as first scored
```

Each stored evaluation keeps:
- its dataset (version and canonical hash), ruleset (version and artifact hash), execution mode and models;
- its per-receipt results with their hashes;
- its metrics with their scoring version and baseline version;
- every rescoring with its time.

Rescoring the same run with the same version returns the stored rescore. Scoring is deterministic, and the tests check that a rescore reproduces the stored numbers exactly.
