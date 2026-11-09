import { RecordActions } from "@/components/management/RecordActions";
import { CompilationMetrics } from "./PolicyAnalytics";
/**
 * One compilation, from what the compiler produced to a ruleset version: steps 3 to 5. Review the differences,
 * see exactly what stands between the extraction and approval, and make the draft version, which the ruleset
 * lifecycle then approves and publishes with the compiler's own safeguards.
 */
import { ArrowLeft, ArrowRight, CheckCircle2, CircleDashed, GitBranchPlus, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useChangeRequests, useCreateRuleset, useExtraction, useExtractionReview } from "@/api/queries";
import type { ChangeRequest, ExtractionReviewDetail, ExtractionSummary } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDateTime, formatInteger, shortHash } from "@/lib/format";
import { Section } from "@/pages/dashboard/Section";
import { Facts, SELECT, Stat } from "@/pages/evaluations/display";
import { ExtractionReview } from "./review/ReviewViews";
import { RULESET_STATUS_TEXT } from "./rulesetText";
import { WorkflowStatusBadge, WorkflowSteps } from "./WorkflowSteps";
import { approvalBlockers, latestReviews, summarize, workflowStatus, type Blocker } from "./workflow";

/** schemas/rulesets.py RulesetName: the versions of one name form one history. */
const RULESET_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const TABS = ["review", "approval", "provenance"] as const;
type Tab = (typeof TABS)[number];

function stepOf(extraction: ExtractionSummary): { current: number; done: number } {
  const status = workflowStatus(extraction);
  if (status === "published") return { current: 4, done: 5 };
  if (status === "approved") return { current: 4, done: 4 };
  if (status === "ready_for_approval") return { current: 3, done: 3 };
  return { current: 2, done: 2 };
}

function Summary({ extraction, blockers }: { extraction: ExtractionSummary; blockers: number }) {
  const summary = summarize(extraction);
  const unknown = (value: number | null) => (value === null ? "—" : formatInteger(value));
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8" aria-label="Compilation summary">
      <Stat label="Documents processed" value={formatInteger(summary.documents)} />
      <Stat label="Rules extracted" value={unknown(summary.rules)} />
      <Stat label="Accepted without review" value={unknown(summary.rulesAgreed)} hint="rules both readings agree on" />
      <Stat label="Rules requiring review" value={unknown(summary.rulesNeedingReview)} />
      <Stat label="Conflicting fields" value={formatInteger(summary.conflicting)} />
      <Stat label="Missing fields" value={formatInteger(summary.missing)} hint="found by one reading only, or unconfirmed" />
      <Stat label="Blocking issues" value={formatInteger(blockers)} />
      <Stat label="Readiness" value={blockers ? "Not ready" : "Ready"} hint={blockers ? "see Approval" : "for a draft version"} />
    </dl>
  );
}

function CreateVersion({ extraction, blocked }: { extraction: ExtractionSummary; blocked: boolean }) {
  const navigate = useNavigate();
  const create = useCreateRuleset();
  const ready = extraction.reviews.filter((review) => review.ready_for_approval);
  const [name, setName] = useState("");
  const [reviewId, setReviewId] = useState("");
  const chosenReview = reviewId || ready.at(-1)?.review_id || "";
  const issues: string[] = [];
  if (!RULESET_NAME.test(name)) issues.push("A name is 1 to 64 letters, digits, '.', '_' or '-'. Versions of one name form one history.");
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor="ruleset-name" className="block text-xs font-medium">
            Ruleset name
          </label>
          <Input id="ruleset-name" className="h-9 font-mono text-[13px]" placeholder="policies" value={name} onChange={(event) => setName(event.target.value.trim())} disabled={blocked} />
        </div>
        {ready.length > 1 ? (
          <div className="space-y-1">
            <label htmlFor="ruleset-review" className="block text-xs font-medium">
              From review
            </label>
            <select id="ruleset-review" className={SELECT} value={chosenReview} onChange={(event) => setReviewId(event.target.value)}>
              {ready.map((review) => (
                <option key={review.review_id} value={review.review_id}>
                  Accepted review of {formatDateTime(review.created_at)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      {name && issues.length ? <p className="text-xs text-status-review-text">{issues.join(" ")}</p> : null}
      {create.isError ? <ErrorState error={create.error} /> : null}
      <Button
        type="button"
        disabled={blocked || issues.length > 0 || create.isPending}
        onClick={() =>
          create.mutate(
            { name, extraction: { extraction_id: extraction.extraction_id, ...(chosenReview ? { review_id: chosenReview } : {}) } },
            { onSuccess: (version) => navigate(`/policies/${encodeURIComponent(version.ruleset_id)}`) },
          )
        }
      >
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <GitBranchPlus aria-hidden />}
        Create draft version for approval
      </Button>
    </div>
  );
}

function Approval({ extraction, latest, requests, onOpen }: { extraction: ExtractionSummary; latest: ExtractionReviewDetail | undefined; requests: readonly ChangeRequest[]; onOpen: (blocker: Blocker) => void }) {
  const blockers = approvalBlockers(extraction, latest, requests);
  const open = requests.filter((item) => item.status === "open").length;
  const unresolved = new Set(blockers.map((item) => item.comparisonId).filter(Boolean)).size;
  const versions = extraction.versions ?? [];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="Approval summary">
        <Stat label="Compared fields" value={formatInteger(extraction.comparison_count)} hint={`${extraction.review_required.length} needed a decision`} />
        <Stat label="Decisions recorded" value={formatInteger(latest?.decisions.length ?? 0)} hint="in the latest review" />
        <Stat label="Changes requested" value={formatInteger(open)} hint={`${requests.length - open} resolved`} />
        <Stat label="Unresolved mismatches" value={formatInteger(unresolved)} />
        <Stat label="Blocking issues" value={formatInteger(blockers.length)} />
        <Stat label="Ruleset versions" value={formatInteger(versions.length)} />
      </dl>
      {blockers.length ? (
        <div role="alert" className="space-y-2 rounded-md border border-status-review-border bg-status-review-soft px-4 py-3">
          <p className="flex items-center gap-2 text-[13px] font-semibold text-status-review-text">
            <CircleDashed className="size-4" aria-hidden />
            Not ready for approval: {blockers.length} {blockers.length === 1 ? "item stands" : "items stand"} in the way.
          </p>
          <ul className="space-y-1 text-[13px]" aria-label="What blocks approval">
            {blockers.map((blocker, index) => (
              <li key={index} className="flex flex-wrap items-center gap-2">
                <span>{blocker.text}</span>
                {blocker.comparisonId ? (
                  <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => onOpen(blocker)}>
                    Open this field
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div role="status" className="flex gap-2 rounded-md border border-status-pass-border bg-status-pass-soft px-4 py-3 text-[13px]">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-status-pass" aria-hidden />
          <p>
            <span className="font-semibold">Ready for approval.</span> The compiler accepts {extraction.ready_for_approval ? "this extraction without review" : "the latest review"}. Approving still rebuilds the ruleset from the stored
            documents and decisions, and is refused unless it reproduces exactly the same artifact.
          </p>
        </div>
      )}
      <Facts
        items={[
          ["Candidate ruleset SHA-256", <span className="break-all font-mono text-xs">{extraction.candidate_ruleset_sha256 ?? "none: the compiler built no candidate"}</span>],
          ["Reading", `${extraction.provenance.execution_mode === "live" ? "Live" : "Replay"} · ${extraction.provenance.reading_kind === "model" ? "model readings" : "simulation readings, which never authorize production"}`],
          [
            "Source revisions",
            <ul className="space-y-0.5 text-xs">
              {extraction.documents.map((document) => (
                <li key={document.document_id}>
                  <Link to={`/documents/${encodeURIComponent(document.document_id)}?revision=${document.revision}`} className="text-primary hover:underline">
                    {document.filename}
                  </Link>{" "}
                  revision {document.revision}
                </li>
              ))}
            </ul>,
          ],
        ]}
      />
      {versions.length ? (
        <div className="space-y-1.5">
          <h3 className="text-sm font-semibold">Ruleset versions made from this compilation</h3>
          <ul className="space-y-1 text-[13px]">
            {versions.map((version) => (
              <li key={version.ruleset_id}>
                <Link to={`/policies/${encodeURIComponent(version.ruleset_id)}`} className="inline-flex items-center gap-1 text-primary hover:underline">
                  {version.name} v{version.version} · {RULESET_STATUS_TEXT[version.status] ?? version.status}
                  <ArrowRight className="size-3.5" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">Approval and publication happen on the version, where the compiler checks them again.</p>
        </div>
      ) : null}
      {extraction.status === "no_candidate" ? null : (
        <div className="space-y-2 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Make a draft version</h3>
          <p className="text-xs text-muted-foreground">The next version of a ruleset name, as a draft: it then goes through review, approval and publication.</p>
          <CreateVersion extraction={extraction} blocked={blockers.length > 0} />
        </div>
      )}
    </div>
  );
}

function Provenance({ extraction }: { extraction: ExtractionSummary }) {
  const provenance = extraction.provenance;
  return (
    <div className="space-y-4">
      <Section id="manage-reviews" title="Saved reviews" description="Editing a review records a new signed decision. Reviews used by ruleset versions are protected.">
        {extraction.reviews.length ? extraction.reviews.map((review) => <div key={review.review_id} className="mb-3 flex flex-wrap items-center gap-2">
          <span className="mr-auto font-mono text-xs">{review.review_id}</span>
          <Button asChild variant="outline" size="sm"><Link to={`/documents/extractions/${encodeURIComponent(extraction.extraction_id)}?tab=review&review=${encodeURIComponent(review.review_id)}`}>Edit review</Link></Button>
          <RecordActions path={`/v1/documents/extractions/${encodeURIComponent(extraction.extraction_id)}/reviews/${encodeURIComponent(review.review_id)}`} title="review" onDeleted={() => {}} />
        </div>) : <p className="text-sm text-muted-foreground">No saved reviews.</p>}
      </Section>
      <Facts
        items={[
          ["Mode", provenance.execution_mode === "live" ? "Live" : "Replay"],
          ["Recordings", provenance.recording_set],
          ["Readings", provenance.reading_kind === "model" ? "Model readings" : "Simulation readings (never authorize production)"],
          ["Model · temperature", provenance.extraction_model ? `${provenance.extraction_model} · ${provenance.temperature ?? "default"}` : "Recorded readings"],
          ["Recorded with", provenance.recorded_with ? `${provenance.recorded_with.extraction_model} · ${provenance.recorded_with.temperature}` : "—"],
          ["Compiled", formatDateTime(extraction.created_at)],
          ["Extraction ID", <span className="font-mono text-xs">{extraction.extraction_id}</span>],
          ["Extraction SHA-256", <span className="break-all font-mono text-xs">{extraction.extraction_sha256}</span>],
          ["Result SHA-256", <span className="break-all font-mono text-xs">{extraction.result_sha256}</span>],
          ["Prompt", <span className="font-mono text-xs">{shortHash(provenance.prompt_sha256)}</span>],
          ["Overrides", provenance.overrides.join(", ") || "—"],
        ]}
      />
      <div>
        <h3 className="text-sm font-semibold">Documents read</h3>
        <ul className="mt-1 space-y-1 text-xs">
          {extraction.documents.map((document) => (
            <li key={document.document_id}>
              <Link to={`/documents/${encodeURIComponent(document.document_id)}?revision=${document.revision}`} className="text-primary hover:underline">
                {document.filename}
              </Link>{" "}
              revision {document.revision} <span className="font-mono text-subtle-foreground">{shortHash(document.content_sha256)}</span>
              {document.reading_error ? <span className="block text-status-flag-text">{document.reading_error}</span> : null}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="text-sm font-semibold">Reviews</h3>
        {extraction.reviews.length ? (
          <ul className="mt-1 space-y-1 text-xs">
            {extraction.reviews.map((item) => (
              <li key={item.review_id}>
                {formatDateTime(item.created_at)} · <Badge variant={item.ready_for_approval ? "pass" : "review"}>{item.ready_for_approval ? "Accepted by the compiler" : "Not accepted"}</Badge> · {item.remaining} undecided ·{" "}
                {item.error_issues} issues <span className="font-mono text-subtle-foreground">{item.review_id}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[13px] text-muted-foreground">No review yet.</p>
        )}
      </div>
    </div>
  );
}

export function ExtractionPage() {
  const { extractionId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const extraction = useExtraction(extractionId);
  const requests = useChangeRequests(extractionId);
  const { latest: latestSummary } = extraction.data ? latestReviews(extraction.data) : { latest: undefined };
  const latest = useExtractionReview(extractionId, latestSummary?.review_id);
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/policies?tab=review">
        <ArrowLeft aria-hidden />
        Policy Management
      </Link>
    </Button>
  );
  if (extraction.isPending) return <><PageHeader title="Compilation" actions={back} /><LoadingRows rows={6} label="Loading compilation" /></>;
  if (extraction.isError) return <><PageHeader title="Compilation" actions={back} /><ErrorState error={extraction.error} onRetry={() => void extraction.refetch()} /></>;

  const data = extraction.data;
  const changeRequests = requests.data ?? [];
  const blockers = approvalBlockers(data, latest.data, changeRequests);
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : data.review_required.length && !data.ready_for_approval ? "review" : "approval";
  const select = (next: { tab?: Tab; item?: string }) =>
    setParams(
      (current) => {
        const out = new URLSearchParams(current);
        if (next.tab) out.set("tab", next.tab);
        if (next.item) out.set("item", next.item);
        return out;
      },
      { replace: true },
    );
  const steps = stepOf(data);
  return (
    <>
      <PageHeader
        title="Compilation results"
        description={`${data.documents.length} documents compiled ${formatDateTime(data.created_at)}, by ${data.provenance.execution_mode === "live" ? "a live model reading" : "replay"}. A stored snapshot: it never changes.`}
        actions={back}
      />
      <div className="mb-4 space-y-3">
        <WorkflowSteps current={steps.current} done={steps.done} />
        <div className="flex flex-wrap items-center gap-2">
          <WorkflowStatusBadge status={workflowStatus({ ...data, open_change_requests: changeRequests.filter((item) => item.status === "open").length })} />
          {data.status === "no_candidate" ? <span className="text-xs text-status-flag-text">The compiler built no candidate ruleset from these documents.</span> : null}
        </div>
        <Summary extraction={data} blockers={blockers.length} />
      </div>
      <Tabs value={tab} onValueChange={(value) => select({ tab: value as Tab })}>
        <TabsList>
          <TabsTrigger value="review">Review · {data.review_required.length}</TabsTrigger>
          <TabsTrigger value="approval">Approval</TabsTrigger>
          <TabsTrigger value="provenance">Provenance</TabsTrigger>
        </TabsList>
        <TabsContent value="review">
          <Section id="review" title="Review differences" description="Each policy is read twice, by matching its text and by AI. Where the readings disagree, decide what the policy means, beside its own words.">
            <ExtractionReview key={`${params.get("item") ?? ""}-${params.get("review") ?? "latest"}`} extraction={data} initialId={params.get("item")} reviewId={params.get("review")} />
          </Section>
        </TabsContent>
        <TabsContent value="approval">
          <Section id="approval" title="Approval readiness" description="What the compiler and the reviewers say stands between this compilation and an approved ruleset.">
            <Approval extraction={data} latest={latest.data} requests={changeRequests} onOpen={(blocker) => select({ tab: "review", item: blocker.comparisonId })} />
          </Section>
        </TabsContent>
        <TabsContent value="provenance">
          <Section id="provenance" title="Provenance" description="How this compilation was made, for reproducing or auditing it.">
            <div className="mb-4"><CompilationMetrics analytics={data.analytics} /></div>
            <Provenance extraction={data} />
          </Section>
        </TabsContent>
      </Tabs>
    </>
  );
}
