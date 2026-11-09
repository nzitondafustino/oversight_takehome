import { describe, expect, it } from "vitest";
import type { FieldComparison, RuleLogic } from "@/api/types";
import { choicesFor } from "@/pages/policy/review/IssueReview";
import { attachIssues, decisionEffect, headline, whyFlagged } from "@/pages/policy/review/issues";
import { relationships } from "@/pages/policy/review/RuleEffect";

const base: FieldComparison = {
  comparison_id: "cmp-1", document_id: "doc-1", policy_doc: "policy.md", rule_id: "ZZ-01", field: "rule", status: "only_llm",
  regex_value: "absent", llm_value: "present", agreed_value: null, evidence: null, location: null, clause_hash: null, extraction_sha256: "a".repeat(64), review_required: true,
};
const labels = (comparison: FieldComparison) => choicesFor(comparison, []).map((choice) => choice.label);

describe("issue kinds", () => {
  it("offers only what fits a rule one reading found and the other did not", () => {
    expect(headline(base)).toBe("Discard ZZ-01: the AI reading found a rule the policy does not contain");
    expect(labels(base)).toEqual(["Discard it", "Ask for clarification"]);  // nothing to correct in a rule that is not there
    const missed = { ...base, status: "only_regex" as const, regex_value: "present", llm_value: "absent" };
    expect(whyFlagged(missed)).toBe("The policy has a heading for this rule, but the AI reading did not report it.");
    expect(labels(missed)).toEqual(["Keep the rule", "Ask for clarification", "Reject the interpretation"]);
  });

  it("offers each reading that has a value, a correction, clarification and rejection for a difference", () => {
    const quote = { ...base, field: "quote", status: "disagree" as const, regex_value: null, llm_value: "Never allowed." };
    expect(labels(quote)).toEqual(["Use: Never allowed.", "Neither: enter the correct value", "Ask for clarification", "Reject the interpretation"]);
  });

  it("says whether a severity decision matches the compiled rule", () => {
    const logic = { kind: "check", severity: "high", severity_cases: [], param_uses: {} } as RuleLogic;
    const severities = { ...base, field: "severities", status: "disagree" as const };
    expect(decisionEffect(severities, logic, ["high"])).toEqual({ fits: true, text: "The compiled rule ZZ-01 raises a high exception: no rule change is needed." });
    expect(decisionEffect(severities, logic, ["medium"]).fits).toBe(false);
    expect(decisionEffect({ ...base, field: "quote" }, logic, "x").fits).toBeNull();  // documentation only
  });

  it("ties an older review's unattached conflict to the decided field of the rule it names, and leaves others alone", () => {
    const limits = { ...base, comparison_id: "cmp-2", rule_id: "TIME-03", field: "thresholds", status: "disagree" as const };
    const issues = [
      { level: "error", code: "executable_conflict", comparison_id: null, message: "the reviewed interpretation disagrees with the executable rules: error faithful: TIME-03: min_receipts is 2 count, not in its clause." },
      { level: "error", code: "no_candidate", comparison_id: null, message: "there is no valid candidate ruleset" },
    ];
    expect(attachIssues(issues, [{ ...limits, regex_value: [{ value: "2", unit: "count" }] }], [{ comparison_id: "cmp-2", action: "override", value: [] }]).map((issue) => [issue.comparison_id, issue.code])).toEqual([["cmp-2", "interpretation_mismatch"], [null, "no_candidate"]]);
    expect(attachIssues(issues, [limits], []).map((issue) => issue.comparison_id)).toEqual([null, null]);  // never on an undecided field
    // Stored already tied to its field, before the compiler told a mismatch from a rule to correct.
    const tied = [{ ...issues[0]!, comparison_id: "cmp-2" }];
    expect(attachIssues(tied, [{ ...limits, regex_value: [{ value: "2", unit: "count" }] }], [{ comparison_id: "cmp-2", action: "override", value: [] }])[0]!.code).toBe("interpretation_mismatch");
  });

  it("names who a rule replaces and who replaces it", () => {
    const logic = {
      "REG-02": { kind: "check", applies_when: { op: "eq", field: "receipt.entity", value: "APAC" }, replaces: [{ rule_id: "MEAL-01", level: "receipt" }], param_uses: {} },
      "MEAL-01": { kind: "check", replaces: [], param_uses: {} },
    } as unknown as Record<string, RuleLogic>;
    expect(relationships("MEAL-01", logic, { "REG-02": "Meal cap" })).toEqual(["Replaced by REG-02 (Meal cap) when the entity is “APAC” on every receipt it applies to: there MEAL-01 is not reported."]);
  });
});
