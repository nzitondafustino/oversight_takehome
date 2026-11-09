import { Loader2, Rocket, Send, ShieldCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useBackends, useExtractionBaseline, useRulesetComparisons, useRulesetTransition } from "@/api/queries";
import type { PublishMode, RulesetDetail, RulesetTransition } from "@/api/types";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { formatDateTime } from "@/lib/format";
import { SELECT } from "@/pages/evaluations/display";
import { ActorField, useActor } from "./actor";
import { DecisionForm } from "./DecisionForm";
import { changeTexts } from "./review/issues";
import { rulesOf } from "./RulesView";

function titlesOf(version: RulesetDetail): Record<string, string> {
  return Object.fromEntries(rulesOf(version).map((rule) => [rule.rule_id, rule.title ?? rule.rule_id]));
}

/** What approving this version authorizes: each rule it changes from the published version. No earlier approval covers it. */
function ApprovalScope({ extractionId, titles }: { extractionId: string; titles: Record<string, string> }) {
  const baseline = useExtractionBaseline(extractionId).data;
  if (!baseline?.published) return null;
  const rules = [...new Set((baseline.rule_changes ?? []).map((change) => change.rule_id))];
  const name = `${baseline.published.name} v${baseline.published.version}`;
  return (
    <div className="rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label="What this approval authorizes">
      <p className="font-medium">What this approval authorizes</p>
      {rules.length ? (
        <ul className="mt-0.5 space-y-0.5">
          {rules.map((ruleId) => (
            <li key={ruleId}>
              <span className="font-mono text-xs">{ruleId}</span>{titles[ruleId] ? ` ${titles[ruleId]}` : ""}: {changeTexts(ruleId, baseline).map((change) => `${change.label} ${change.before} → ${change.after}`).join("; ")}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">No rule does anything different from {name}.</p>
      )}
      <p className="text-xs text-muted-foreground">Compared with {name}, the published version. Its approval does not cover these changes: each version is approved for exactly what it does.</p>
    </div>
  );
}

/** An API refusal with what it lists: what remains, the compiler's reasons, the allowed modes. */
function Refusal({ error }: { error: unknown }) {
  return (
    <div className="space-y-1">
      <ErrorState error={error} />
      {error instanceof ApiError && error.details ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-primary">What the API reported</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 font-mono">{JSON.stringify(error.details, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2.5 rounded-md border border-border p-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}

/**
 * The one step a version's state allows, sent through the API, which checks it again and asks the compiler at
 * approval and publication. Nothing here moves a version the API refuses, and a refusal is shown as given.
 */
export function LifecycleActions({ version, canPublish = true }: { version: RulesetDetail; /** The API's readiness: Publish is offered only when it says nothing stands in the way. */ canPublish?: boolean }) {
  const transition = useRulesetTransition();
  const backends = useBackends();
  const [actor, setActor] = useActor();
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<PublishMode | "">("");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const compiled = version.compilation;
  const needsDecisions = version.status === "in_review" && !version.review && compiled !== null && !compiled.ready_for_approval && !compiled.prepared_review;
  const comparisons = useRulesetComparisons(needsDecisions ? version.ruleset_id : undefined, true);

  const send = (step: RulesetTransition) => transition.mutate({ rulesetId: version.ruleset_id, transition: step }, { onSuccess: () => { setReason(""); setConfirmed(false); } });
  const who = actor.trim();
  const busy = transition.isPending;
  const spinner = busy ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null;

  let body: ReactNode;
  if (version.status === "draft") {
    body = (
      <Step title="Submit for review">
        {version.review?.decision === "rejected" ? <p className="text-xs text-status-flag-text">Rejected by {version.review.reviewer.claimed}: {version.review.reason}. Submitting again starts a new review of the same content.</p> : null}
        <Input aria-label="Reason (optional)" placeholder="Reason (optional)" className="h-8 text-[13px]" value={reason} onChange={(event) => setReason(event.target.value)} />
        <Button size="sm" disabled={busy} onClick={() => send({ step: "submit", body: { ...(who ? { actor: who } : {}), ...(reason.trim() ? { reason: reason.trim() } : {}) } })}>
          {spinner ?? <Send aria-hidden />}
          Submit for review
        </Button>
      </Step>
    );
  } else if (version.status === "in_review" && !version.review) {
    body = (
      <Step title="Review this version">
        <p className="text-xs text-muted-foreground">
          {compiled === null
            ? "An uploaded artifact can be reviewed, but no approval can ever cover it."
            : compiled.prepared_review
              ? `Accepting uses the ${(compiled.prepared_review.decisions ?? []).length} ${(compiled.prepared_review.decisions ?? []).length === 1 ? "decision" : "decisions"} recorded in this compilation's review. The compiler checks them again before accepting.`
              : compiled.ready_for_approval
                ? "The compiler's readings agree on every field: no decisions are needed."
                : "The compiler needs a decision for each field below before it accepts this review."}
        </p>
        <Input aria-label="Reason" placeholder="Reason (required to reject)" className="h-8 text-[13px]" value={reason} onChange={(event) => setReason(event.target.value)} />
        {needsDecisions ? (
          comparisons.isPending ? (
            <LoadingRows rows={2} label="Loading fields to decide" />
          ) : comparisons.isError ? (
            <ErrorState error={comparisons.error} />
          ) : (
            <DecisionForm
              comparisons={comparisons.data.comparisons}
              reviewer={who}
              pending={busy}
              submitLabel="Accept with these decisions"
              onSubmit={(decisions) => send({ step: "review", body: { actor: who, decision: "accepted", decisions, ...(reason.trim() ? { reason: reason.trim() } : {}) } })}
            />
          )
        ) : (
          <Button size="sm" disabled={busy || !who} title={who ? undefined : "Say who is reviewing"} onClick={() => send({ step: "review", body: { actor: who, decision: "accepted", ...(reason.trim() ? { reason: reason.trim() } : {}) } })}>
            {spinner ?? <ThumbsUp aria-hidden />}
            Accept
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={busy || !who || !reason.trim()} title={!reason.trim() ? "A rejection needs a reason" : undefined} onClick={() => send({ step: "review", body: { actor: who, decision: "rejected", reason: reason.trim() } })}>
          <ThumbsDown aria-hidden />
          Reject
        </Button>
      </Step>
    );
  } else if (version.status === "in_review") {
    const modes = backends.data?.rulesets.publication_modes ?? [];
    const simulation = compiled?.reading_kind === "simulation";
    // Simulation readings never authorize production (publication_blockers): such a mode is shown but never chosen.
    const usable = modes.filter((item) => !(item === "production" && simulation));
    const chosen = mode || (usable.includes("production") ? "production" : usable[0] ?? "");
    const blocked =
      compiled === null
        ? "This version is an uploaded artifact: the compiler's approval covers only a version it compiled from policy documents."
        : !modes.length
          ? "This deployment allows no publication mode."
          : !usable.length
            ? "This version was compiled from simulation readings, which never authorize production, and this deployment publishes only for production."
            : null;
    body = (
      <Step title="Approve">
        <p className="text-xs text-muted-foreground">
          The compiler rebuilds the candidate from the stored documents and decisions, and approves only if it would publish exactly this artifact.
        </p>
        {version.compilation?.extraction_id && !version.edits ? <ApprovalScope extractionId={version.compilation.extraction_id} titles={titlesOf(version)} /> : null}
        {blocked ? (
          <div className="space-y-2"><p className="text-xs text-status-review-text">{blocked}</p>{compiled === null ? <Button asChild size="sm"><Link to="/policies?tab=documents">Compile from policy documents</Link></Button> : null}</div>
        ) : (
          <>
            <label htmlFor="approve-mode" className="block text-xs font-medium">
              Publication the approval authorizes
            </label>
            <select id="approve-mode" className={SELECT} value={chosen} onChange={(event) => setMode(event.target.value as PublishMode)}>
              {modes.map((item) => (
                <option key={item} value={item} disabled={item === "production" && simulation}>
                  {item}
                  {item === "production" && simulation ? " · simulation readings never authorize production" : ""}
                </option>
              ))}
            </select>
            <p className="text-xs text-subtle-foreground">Only the modes this deployment allows are offered.</p>
            <Input aria-label="Note (optional)" placeholder="Note (optional)" className="h-8 text-[13px]" value={note} onChange={(event) => setNote(event.target.value)} />
            <Button size="sm" disabled={busy || !who || !chosen} title={who ? undefined : "Say who is approving"} onClick={() => send({ step: "approve", body: { actor: who, mode: chosen as PublishMode, ...(note.trim() ? { note: note.trim() } : {}) } })}>
              {spinner ?? <ShieldCheck aria-hidden />}
              Approve for {chosen || "…"}
            </Button>
          </>
        )}
      </Step>
    );
  } else if (version.status === "approved" && !canPublish) {
    body = (
      <Step title="Publish">
        <p className="text-xs text-status-review-text">Publication is blocked: resolve what is listed above first. Publishing still rebuilds the version and checks its approval.</p>
      </Step>
    );
  } else if (version.status === "approved") {
    body = (
      <Step title="Publish">
        <p className="text-xs text-muted-foreground">
          The approval is checked again before publication. Approval reference <span className="break-all font-mono">{version.approval_reference}</span>.
        </p>
        <label className="flex cursor-pointer gap-2 rounded-md border border-status-review-border bg-status-review-soft p-2 text-[13px]">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 size-3.5 accent-primary" />
          <span>
            Publish {version.name} v{version.version} for <span className="font-semibold">{version.approval_mode}</span>.
            {version.approval_mode === "production" ? " Validation and evaluation will be able to use it." : " A simulation publication is used only as a preview."}
          </span>
        </label>
        <Button size="sm" disabled={busy || !confirmed} onClick={() => send({ step: "publish", body: { ...(who ? { actor: who } : {}), ...(version.approval_reference ? { approval_reference: version.approval_reference } : {}) } })}>
          {spinner ?? <Rocket aria-hidden />}
          Publish
        </Button>
      </Step>
    );
  } else {
    body = (
      <Step title="Published">
        <p className="text-[13px]">
          Published for <span className="font-semibold">{version.publication_mode}</span> {formatDateTime(version.published_at)}. No step changes it now.
        </p>
        {version.publication_mode === "production" ? (
          <Button asChild size="sm" variant="outline">
            <Link to="/validate">Validate receipts</Link>
          </Button>
        ) : null}
      </Step>
    );
  }

  return (
    <div className="space-y-3">
      {version.status !== "published" ? <ActorField actor={actor} setActor={setActor} id="lifecycle-actor" /> : null}
      {body}
      {transition.isError ? <Refusal error={transition.error} /> : null}
    </div>
  );
}
