/**
 * Approval readiness of one ruleset version: what it is made of, how it was reviewed, and exactly what stands in the
 * way of its next step, each linked to where it is resolved. The API and the compiler decide; this explains them.
 */
import { CheckCircle2, CircleDashed } from "lucide-react";
import { Link } from "react-router-dom";
import { useChangeRequests } from "@/api/queries";
import type { RulesetDetail } from "@/api/types";
import { formatInteger, shortHash } from "@/lib/format";
import { Stat } from "@/pages/evaluations/display";

export interface VersionBlocker {
  text: string;
  to?: string;
}

/** What stops this version's approval now, from its state and its extraction's open change requests. */
export function versionBlockers(version: RulesetDetail, openRequests: { change_request_id: string; comparison_id: string; rule_id: string; field: string }[]): VersionBlocker[] {
  const blockers: VersionBlocker[] = [];
  if (version.status === "approved" || version.status === "published") return blockers;
  if (version.compilation === null) blockers.push({ text: "It was uploaded as an artifact, not compiled, so it cannot be approved." });
  if (version.status === "draft") blockers.push({ text: "Submit it for review first." });
  if (version.status === "in_review" && version.review?.decision !== "accepted") blockers.push({ text: "Its review has not been accepted yet." });
  const extraction = version.compilation?.extraction_id;
  for (const request of openRequests)
    blockers.push({
      text: `${request.rule_id} · ${request.field}: a change was requested and is still open.`,
      to: extraction ? `/documents/extractions/${encodeURIComponent(extraction)}?tab=review&item=${encodeURIComponent(request.comparison_id)}` : undefined,
    });
  return blockers;
}

export function ApprovalSummary({ version }: { version: RulesetDetail }) {
  const extractionId = version.compilation?.extraction_id ?? undefined;
  const requests = useChangeRequests(extractionId);
  const all = requests.data ?? [];
  const open = all.filter((item) => item.status === "open");
  const blockers = versionBlockers(version, open);
  const decisions = version.review?.decisions?.length ?? 0;
  const done = version.status === "approved" || version.status === "published";
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-2">
        <Stat label="Fields needing review" value={formatInteger(version.compilation?.comparisons_needing_review ?? 0)} />
        <Stat label="Decisions accepted" value={formatInteger(decisions)} hint={version.review ? `review ${version.review.decision}` : "no review yet"} />
        <Stat label="Changes requested" value={formatInteger(open.length)} hint={`${all.length - open.length} resolved`} />
        <Stat label="Blocking issues" value={formatInteger(blockers.length)} />
      </dl>
      {done ? (
        <p role="status" className="flex gap-2 text-[13px] text-status-pass">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
          {version.status === "published" ? "Published. It never changes now." : "Approved: the compiler rebuilt and verified it."}
        </p>
      ) : blockers.length ? (
        <div role="alert" className="space-y-1 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
          <p className="flex items-center gap-1.5 font-semibold text-status-review-text">
            <CircleDashed className="size-4" aria-hidden />
            Not ready for approval
          </p>
          <ul className="space-y-0.5" aria-label="What blocks approval">
            {blockers.map((blocker, index) => (
              <li key={index}>
                {blocker.text}{" "}
                {blocker.to ? (
                  <Link to={blocker.to} className="text-xs font-medium text-primary hover:underline">
                    Open this field
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p role="status" className="text-[13px]">
          Ready for approval. Approving rebuilds the ruleset from its stored documents and decisions, and is refused unless the compiler reproduces exactly this artifact.
        </p>
      )}
      <dl className="space-y-1 text-xs">
        <div>
          <dt className="text-muted-foreground">Ruleset version</dt>
          <dd>
            {version.name} v{version.version} · {version.ruleset_version}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Final ruleset hash</dt>
          <dd className="break-all font-mono" title={version.artifact_sha256}>
            {version.artifact_sha256}
          </dd>
        </div>
        {version.compilation ? (
          <div>
            <dt className="text-muted-foreground">Source revisions</dt>
            <dd>
              <ul>
                {version.compilation.documents.map((document) => (
                  <li key={`${document.document_id}-${document.revision ?? ""}`}>
                    {document.revision != null ? (
                      <Link to={`/documents/${encodeURIComponent(document.document_id)}?revision=${document.revision}`} className="text-primary hover:underline">
                        {document.filename}
                      </Link>
                    ) : (
                      document.filename
                    )}
                    {document.revision != null ? ` · revision ${document.revision}` : " · sent inline"} <span className="font-mono text-subtle-foreground">{shortHash(document.sha256)}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}
