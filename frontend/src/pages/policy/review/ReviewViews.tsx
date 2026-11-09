/** The issue review for a compilation (editable until its rules are published) and for a version (read-only). */
import { useRef } from "react";
import { useAllExtractionComparisons, useChangeRequests, useExtractionReview, useRulesetComparisons } from "@/api/queries";
import type { ExtractionSummary, RulesetDetail } from "@/api/types";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { rulesOf } from "../RulesView";
import { latestReviews, workflowStatus } from "../workflow";
import { IssueReview } from "./IssueReview";
import { logicOf } from "./LimitUses";
import { RuleDiscrepancies } from "./VersionPanels";

export function ExtractionReview({ extraction, initialId, initialDocument, reviewId }: { extraction: ExtractionSummary; initialId?: string | null; initialDocument?: string | null; reviewId?: string | null }) {
  const comparisons = useAllExtractionComparisons(extraction.extraction_id);
  const { latest } = latestReviews(extraction);
  const selectedReview = reviewId && extraction.reviews.some((item) => item.review_id === reviewId) ? reviewId : latest?.review_id;
  const saved = useExtractionReview(extraction.extraction_id, selectedReview);
  const requests = useChangeRequests(extraction.extraction_id);
  // Loading is shown only before the review first appears: a save that makes the first review must not unmount it,
  // or the reviewer loses their place and the outcome of what they saved (the save's own result is shown meanwhile).
  const shown = useRef(false);
  if (comparisons.isPending || (latest && saved.isPending && !shown.current)) return <LoadingRows rows={5} label="Loading the review" />;
  shown.current = true;
  if (comparisons.isError) return <ErrorState error={comparisons.error} onRetry={() => void comparisons.refetch()} />;
  const status = workflowStatus(extraction);
  const published = status === "published" || status === "approved";
  return (
    <div className="space-y-4">
      <RuleDiscrepancies extraction={extraction} />
      <IssueReview
        owner={{ extractionId: extraction.extraction_id }}
        extraction={extraction}
        comparisons={comparisons.data}
        ruleTitles={extraction.rule_titles ?? {}}
        ruleLogic={extraction.rule_logic ?? {}}
        saved={saved.data}
        readOnly={published}
        readOnlyReason={published ? "Rules from this compilation are approved or published: its decisions are kept as history and no longer change." : undefined}
        requests={requests.data ?? []}
        extractionSha={extraction.extraction_sha256}
        initialId={initialId}
        initialDocument={initialDocument}
        stage={{ approvalHref: `/documents/extractions/${encodeURIComponent(extraction.extraction_id)}?tab=approval`, versions: extraction.versions ?? [], blocked: extraction.status === "no_candidate" ? (extraction.discrepancies?.length ? "a compiled rule needs correction first" : "the compiler built no draft ruleset") : undefined }}
      />
    </div>
  );
}

export function VersionReview({ version }: { version: RulesetDetail }) {
  const comparisons = useRulesetComparisons(version.compilation ? version.ruleset_id : undefined);
  if (!version.compilation) return <p className="text-[13px] text-muted-foreground">This version was uploaded as an artifact, not compiled from documents, so it has no interpretations to review.</p>;
  if (comparisons.isPending) return <LoadingRows rows={5} label="Loading the review" />;
  if (comparisons.isError) return <ErrorState error={comparisons.error} onRetry={() => void comparisons.refetch()} />;
  const titles = Object.fromEntries(rulesOf(version).map((rule) => [rule.rule_id, rule.title ?? rule.rule_id]));
  const decisions = version.review?.decisions ?? [];
  return (
    <IssueReview
      owner={{ rulesetId: version.ruleset_id }}
      comparisons={comparisons.data.comparisons}
      ruleTitles={titles}
      ruleLogic={Object.fromEntries(rulesOf(version).map((rule) => [rule.rule_id, logicOf(rule)]))}
      saved={{ decisions: decisions as never, remaining: [], issues: [] }}
      readOnly
      readOnlyReason={
        version.status === "published" || version.status === "approved"
          ? "This version's decisions are history: shown as they were accepted, with their source."
          : "Decisions for this version are made in its compilation's review, or in its lifecycle step."
      }
    />
  );
}
