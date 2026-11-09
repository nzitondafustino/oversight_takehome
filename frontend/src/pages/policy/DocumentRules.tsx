/**
 * From a policy document to what runs: the rules the published version takes from it, each with where it applies,
 * whether it runs, and what it replaces or is replaced by; and how many of the document's fields a pending compilation
 * still needs a decision on, linked to that review.
 */
import { Link } from "react-router-dom";
import { useExtraction, useExtractionReview, useExtractions, useRuleset, useRulesetChanges, useRulesets } from "@/api/queries";
import type { ExtractionListItem, RuleLogic } from "@/api/types";
import { LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Section } from "@/pages/dashboard/Section";
import { attachIssues, changeTexts, resolutionOf } from "./review/issues";
import { executableText, relationships } from "./review/RuleEffect";
import { plainRule } from "./rules";
import { rulesOf } from "./RulesView";
import { latestReviews, pendingCompilations } from "./workflow";

function OpenIssues({ filename, pending }: { filename: string; pending: ExtractionListItem }) {
  const extraction = useExtraction(pending.extraction_id);
  const { latest } = latestReviews(pending);
  const saved = useExtractionReview(pending.extraction_id, latest?.review_id);
  if (!extraction.data) return null;
  const fields = extraction.data.review_required.filter((item) => item.policy_doc === filename);
  const review = saved.data ? { ...saved.data, issues: attachIssues(saved.data.issues, extraction.data.review_required, saved.data.decisions) } : undefined;
  const open = fields.filter((item) => ["open", "has_issue"].includes(resolutionOf(item, review))).length;
  const href = `/policies?tab=review&compilation=${encodeURIComponent(pending.extraction_id)}&document=${encodeURIComponent(filename)}`;
  return (
    <p className="rounded-md border border-border px-3 py-2 text-[13px]" role="status">
      {open ? <span className="font-medium text-status-review-text">{open} {open === 1 ? "issue needs" : "issues need"} a decision</span> : <span className="font-medium">No open issues</span>} in the pending compilation of this document.{" "}
      {fields.length ? <Link className="text-primary hover:underline" to={href}>{open ? "Review them" : "See its decisions"}</Link> : null}
    </p>
  );
}

export function DocumentRules({ filename }: { filename: string }) {
  const versions = useRulesets({ limit: 100, offset: 0 });
  const published = (versions.data?.items ?? []).filter((item) => item.status === "published").sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const version = useRuleset(published?.ruleset_id);
  const changes = useRulesetChanges(published?.ruleset_id).data;
  const extractions = useExtractions({ limit: 100, offset: 0 });
  const pending = pendingCompilations(extractions.data?.items ?? [], versions.data?.items ?? []).find((item) => item.documents.some((document) => document.filename === filename));
  const all = version.data ? rulesOf(version.data) : [];
  const logic = Object.fromEntries(all.map((rule) => [rule.rule_id, rule as unknown as RuleLogic]));
  const titles = Object.fromEntries(all.map((rule) => [rule.rule_id, rule.title ?? rule.rule_id]));
  const rules = all.filter((rule) => rule.policy_doc === filename);
  return (
    <Section id="document-rules" title="Rules from this document" description={published ? `What runs on receipts, in ${published.name} v${published.version}, the published version.` : "No version is published yet."}>
      <div className="space-y-2">
        {pending ? <OpenIssues filename={filename} pending={pending} /> : null}
        {versions.isPending || (published && version.isPending) ? (
          <LoadingRows rows={3} label="Loading rules" />
        ) : !rules.length ? (
          <p className="text-[13px] text-muted-foreground">{published ? "The published version has no rule from this document." : "Compile and publish this document to see its rules."}</p>
        ) : (
          <ul className="divide-y divide-border" aria-label="Rules from this document">
            {rules.map((rule) => {
              const plain = plainRule(rule);
              const runs = executableText(logic[rule.rule_id]!);
              const related = relationships(rule.rule_id, logic, titles);
              return (
                <li key={rule.rule_id} className="space-y-0.5 py-2 text-[13px]">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs">{rule.rule_id}</span>
                    <span className="font-medium">{plain.name}</span>
                    <Badge variant={runs.variant}>{runs.label}</Badge>
                  </p>
                  <p className="text-xs text-muted-foreground">{plain.applies} {plain.outcome.replace(/ Takes the place of [^.]*\./, "")}{plain.limits.length ? ` Limits: ${plain.limits.join(" · ")}.` : ""}</p>
                  {changes?.against && changeTexts(rule.rule_id, { published: changes.against, rule_changes: changes.rule_changes }).length ? (
                    <p className="text-xs">
                      <Badge variant="info">Changed</Badge> since {changes.against.name} v{changes.against.version}: {changeTexts(rule.rule_id, { published: changes.against, rule_changes: changes.rule_changes }).map((change) => `${change.label} ${change.before} → ${change.after}`).join("; ")}
                    </p>
                  ) : null}
                  {related.map((sentence) => (
                    <p key={sentence} className="text-xs">{sentence}</p>
                  ))}
                </li>
              );
            })}
          </ul>
        )}
        {published ? (
          <Link className="text-xs text-primary hover:underline" to={`/policies?tab=rules&version=${encodeURIComponent(published.ruleset_id)}`}>
            All rules of {published.name} v{published.version}
          </Link>
        ) : null}
      </div>
    </Section>
  );
}
