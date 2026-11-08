import { ArrowLeft, CheckCircle2, CircleDashed, GitBranchPlus, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useCreateRuleset, useExtraction, useExtractionComparisons, useReviewExtraction } from "@/api/queries";
import type { ExtractionReviewDetail, ExtractionSummary } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { formatDateTime, formatInteger, humanize, shortHash } from "@/lib/format";
import { Pager, Section } from "@/pages/dashboard/Section";
import { Facts, SELECT, Stat } from "@/pages/evaluations/display";
import { ActorField, useActor } from "./actor";
import { ComparisonCard, DecisionForm } from "./DecisionForm";
import { ExtractionStatusBadge } from "./DocumentsPage";

const PAGE_SIZE = 25;
/** schemas/rulesets.py RulesetName: the versions of one name form one history. */
const RULESET_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function ReviewOutcome({ review, extraction }: { review: ExtractionReviewDetail; extraction: ExtractionSummary }) {
  const named = new Map(extraction.review_required.map((item) => [item.comparison_id, `${item.rule_id} ${item.field}`]));
  return review.ready_for_approval ? (
    <div role="status" className="flex gap-2 rounded-md border border-status-pass-border bg-status-pass-soft px-3 py-2 text-[13px]">
      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-status-pass" aria-hidden />
      <p>
        <span className="font-semibold">The compiler accepts this review.</span> Review {review.review_id} can become a draft version below. Approval is still a separate lifecycle step.
      </p>
    </div>
  ) : (
    <div role="alert" className="space-y-1 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
      <p className="flex gap-2 font-semibold text-status-review-text">
        <CircleDashed className="mt-0.5 size-4 shrink-0" aria-hidden />
        Not ready: the compiler does not accept this review yet. It is stored, and cannot become a candidate.
      </p>
      {review.remaining.length ? <p>Still undecided: {review.remaining.map((id) => named.get(id) ?? id).join(", ")}</p> : null}
      {review.issues.length ? (
        <ul className="list-inside list-disc">
          {review.issues.map((issue, index) => (
            <li key={index}>
              <span className="font-mono text-xs">{issue.code}</span> {issue.message}
              {issue.comparison_id ? ` (${named.get(issue.comparison_id) ?? issue.comparison_id})` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CreateVersion({ extraction }: { extraction: ExtractionSummary }) {
  const navigate = useNavigate();
  const create = useCreateRuleset();
  const ready = extraction.reviews.filter((review) => review.ready_for_approval);
  const [name, setName] = useState("");
  const [reviewId, setReviewId] = useState("");
  const chosenReview = reviewId || ready.at(-1)?.review_id || "";

  if (extraction.status === "no_candidate") return <p className="text-[13px] text-muted-foreground">The compiler built no candidate ruleset from these documents, so no version can be made from this extraction.</p>;
  if (!extraction.ready_for_approval && !ready.length) return <p className="text-[13px] text-muted-foreground">A version can be made once a review the compiler accepts is stored, or from an extraction that needs none.</p>;

  const issues: string[] = [];
  if (!RULESET_NAME.test(name)) issues.push("A name is 1 to 64 letters, digits, '.', '_' or '-'. Versions of one name form one history.");
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor="ruleset-name" className="block text-xs font-medium">
            Ruleset name
          </label>
          <Input id="ruleset-name" className="h-9 font-mono text-[13px]" placeholder="policies" value={name} onChange={(event) => setName(event.target.value.trim())} />
        </div>
        {ready.length ? (
          <div className="space-y-1">
            <label htmlFor="ruleset-review" className="block text-xs font-medium">
              From review
            </label>
            <select id="ruleset-review" className={SELECT} value={chosenReview} onChange={(event) => setReviewId(event.target.value)}>
              {ready.map((review) => (
                <option key={review.review_id} value={review.review_id}>
                  {review.review_id} · {formatDateTime(review.created_at)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      <p className="text-xs text-subtle-foreground">The next version of this name, as a draft. It still goes through submit, review, approval and publication.</p>
      {name && issues.length ? <p className="text-xs text-status-review-text">{issues.join(" ")}</p> : null}
      {create.isError ? <ErrorState error={create.error} /> : null}
      <Button
        type="button"
        size="sm"
        disabled={issues.length > 0 || create.isPending}
        onClick={() =>
          create.mutate(
            { name, extraction: { extraction_id: extraction.extraction_id, ...(chosenReview ? { review_id: chosenReview } : {}) } },
            { onSuccess: (version) => navigate(`/policies/${encodeURIComponent(version.ruleset_id)}`) },
          )
        }
      >
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <GitBranchPlus aria-hidden />}
        Create draft version
      </Button>
    </div>
  );
}

function AllComparisons({ extractionId }: { extractionId: string }) {
  const [offset, setOffset] = useState(0);
  const comparisons = useExtractionComparisons(extractionId, { limit: PAGE_SIZE, offset });
  if (comparisons.isPending) return <LoadingRows rows={4} label="Loading comparisons" />;
  if (comparisons.isError) return <ErrorState error={comparisons.error} onRetry={() => void comparisons.refetch()} />;
  return (
    <>
      <ul className="space-y-2">
        {comparisons.data.items.map((comparison) => (
          <ComparisonCard key={comparison.comparison_id} comparison={comparison} />
        ))}
      </ul>
      <Pager offset={offset} limit={PAGE_SIZE} total={comparisons.data.total} onChange={setOffset} busy={comparisons.isFetching} />
    </>
  );
}

export function ExtractionPage() {
  const { extractionId = "" } = useParams();
  const extraction = useExtraction(extractionId);
  const review = useReviewExtraction();
  const [actor, setActor] = useActor();
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/documents">
        <ArrowLeft aria-hidden />
        Documents
      </Link>
    </Button>
  );
  if (extraction.isPending) return <><PageHeader title="Extraction" actions={back} /><LoadingRows rows={6} label="Loading extraction" /></>;
  if (extraction.isError) return <><PageHeader title="Extraction" actions={back} /><ErrorState error={extraction.error} onRetry={() => void extraction.refetch()} /></>;

  const data = extraction.data;
  const provenance = data.provenance;
  const summary = data.agreement_summary as { rules?: number; rules_agreed?: number; rules_needing_review?: number; fields?: Record<string, number> };
  return (
    <>
      <PageHeader title={`Extraction ${data.extraction_id}`} description={`${data.documents.length} documents read ${formatDateTime(data.created_at)}. A stored snapshot: it never changes.`} actions={back} />
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <ExtractionStatusBadge status={data.status} />
        <Badge variant={provenance.execution_mode === "live" ? "accent" : "outline"} className="capitalize">
          {provenance.execution_mode}
        </Badge>
        <Badge variant={provenance.reading_kind === "model" ? "outline" : "review"} title={provenance.reading_kind === "simulation" ? "Simulation readings never authorize production" : undefined}>
          {provenance.reading_kind} readings
        </Badge>
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Section id="review" title="Review" description="A decision for each field the compiler could not settle. The compiler decides whether the review is ready.">
            {data.review_required.length === 0 ? (
              <EmptyState title="Nothing needs a decision" description={data.ready_for_approval ? "The regex and model readings agree on every field." : "The compiler reports no field to decide."} className="py-6" />
            ) : (
              <>
                <div className="mb-3 max-w-sm">
                  <ActorField actor={actor} setActor={setActor} id="extraction-actor" />
                </div>
                {review.data ? <div className="mb-3"><ReviewOutcome review={review.data} extraction={data} /></div> : null}
                {review.isError ? <ErrorState error={review.error} className="mb-3" /> : null}
                <DecisionForm
                  comparisons={data.review_required}
                  reviewer={actor}
                  pending={review.isPending}
                  submitLabel="Send review"
                  onSubmit={(decisions) => review.mutate({ extractionId: data.extraction_id, body: { actor: actor.trim(), extraction_sha256: data.extraction_sha256, decisions } })}
                />
              </>
            )}
          </Section>
          <Section id="new-version" title="Draft ruleset version">
            <CreateVersion extraction={data} />
          </Section>
          <Section id="comparisons" title="Every compared field" description={`${formatInteger(data.comparison_count)} fields, as the compiler compared them, with their source evidence.`}>
            <AllComparisons extractionId={data.extraction_id} />
          </Section>
        </div>
        <div className="space-y-4">
          <Section id="agreement" title="Agreement">
            <dl className="grid grid-cols-2 gap-2">
              <Stat label="Rules agreed" value={`${formatInteger(summary.rules_agreed)}/${formatInteger(summary.rules)}`} />
              <Stat label="Rules needing review" value={formatInteger(summary.rules_needing_review)} />
            </dl>
            {summary.fields ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Fields: {Object.entries(summary.fields).map(([status, count]) => `${count} ${status.replace("_", " ")}`).join(" · ")}
              </p>
            ) : null}
            {data.blockers.length ? (
              <div className="mt-3">
                <p className="text-xs font-medium">Unresolved before review</p>
                <ul className="mt-1 space-y-0.5 text-xs text-status-review-text">
                  {data.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Section>
          <Section id="provenance" title="Provenance">
            <Facts
              className="sm:grid-cols-1"
              items={[
                ["Recording set", provenance.recording_set],
                ["Model · temperature", provenance.extraction_model ? `${provenance.extraction_model} · ${provenance.temperature ?? "default"}` : "Recorded readings"],
                ["Recorded with", provenance.recorded_with ? `${provenance.recorded_with.extraction_model} · ${provenance.recorded_with.temperature}` : "—"],
                ["Extraction SHA-256", <span className="break-all font-mono text-xs">{data.extraction_sha256}</span>],
                ["Candidate ruleset", <span className="break-all font-mono text-xs">{data.candidate_ruleset_sha256 ?? "none"}</span>],
                ["Prompt", <span className="font-mono text-xs">{shortHash(provenance.prompt_sha256)}</span>],
                ["Overrides", provenance.overrides.join(", ") || "—"],
              ]}
            />
            <p className="mt-3 text-xs font-medium">Documents read</p>
            <ul className="mt-1 space-y-1 text-xs">
              {data.documents.map((document) => (
                <li key={document.document_id}>
                  <Link to={`/documents/${encodeURIComponent(document.document_id)}`} className="text-primary hover:underline">
                    {document.filename}
                  </Link>{" "}
                  r{document.revision} <span className="font-mono text-subtle-foreground">{shortHash(document.content_sha256)}</span>
                  {document.reading_error ? <span className="block text-status-flag-text">{document.reading_error}</span> : null}
                </li>
              ))}
            </ul>
          </Section>
          <Section id="reviews" title="Reviews" description="Every review is kept, ready or not.">
            {data.reviews.length ? (
              <ul className="space-y-1.5 text-xs">
                {data.reviews.map((item) => (
                  <li key={item.review_id} className="rounded-md border border-border px-2.5 py-1.5">
                    <span className="font-mono">{item.review_id}</span> <Badge variant={item.ready_for_approval ? "pass" : "review"}>{item.ready_for_approval ? "Ready" : "Not ready"}</Badge>
                    <span className="block text-subtle-foreground">
                      {humanize(item.policy)} · {item.remaining} undecided · {item.error_issues} errors · {formatDateTime(item.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted-foreground">No review yet.</p>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}
