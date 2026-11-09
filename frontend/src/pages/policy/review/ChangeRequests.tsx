/** Change requests on one compared field: raised, listed and resolved. A request rewrites nothing. */
import { MessageSquareWarning } from "lucide-react";
import { useState } from "react";
import { useCreateChangeRequest, useResolveChangeRequest } from "@/api/queries";
import type { ChangeRequest, FieldComparison } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { formatDateTime } from "@/lib/format";

export function ChangeRequests({ extractionId, comparison, requests, actor, startOpen = false }: { extractionId: string; comparison: FieldComparison; requests: ChangeRequest[]; actor: string; startOpen?: boolean }) {
  const create = useCreateChangeRequest();
  const resolve = useResolveChangeRequest();
  const [open, setOpen] = useState(startOpen);
  const [correction, setCorrection] = useState("");
  const [comment, setComment] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const id = comparison.comparison_id;
  return (
    <div className="space-y-2 border-t border-border pt-3">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold">Change requests</h4>
        {!open ? (
          <Button type="button" size="sm" variant="outline" className="ml-auto" onClick={() => setOpen(true)}>
            <MessageSquareWarning aria-hidden />
            Request changes
          </Button>
        ) : null}
      </div>
      {requests.length ? (
        <ul className="space-y-2 text-[13px]" aria-label="Change requests for this field">
          {requests.map((item) => (
            <li key={item.change_request_id} className="rounded-md border border-border px-3 py-2">
              <p className="flex flex-wrap items-center gap-1.5">
                <Badge variant={item.status === "open" ? "flag" : "pass"}>{item.status === "open" ? "Open" : "Resolved"}</Badge>
                <span className="text-xs text-muted-foreground">
                  by {item.requested_by.claimed} · {formatDateTime(item.created_at)}
                </span>
              </p>
              <p className="mt-1">{item.requested_correction}</p>
              {item.comment ? <p className="text-xs text-muted-foreground">{item.comment}</p> : null}
              {item.status === "resolved" ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Resolved by {item.resolved_by?.claimed} {item.resolved_at ? `· ${formatDateTime(item.resolved_at)}` : ""}: {item.resolution_note}
                </p>
              ) : (
                <div className="mt-2 flex gap-2">
                  <Input aria-label={`Resolution note for ${item.change_request_id}`} placeholder="How it was addressed" className="h-8 text-[13px]" value={notes[item.change_request_id] ?? ""} onChange={(event) => setNotes((all) => ({ ...all, [item.change_request_id]: event.target.value }))} />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!actor || !(notes[item.change_request_id] ?? "").trim() || resolve.isPending}
                    onClick={() => resolve.mutate({ extractionId, changeRequestId: item.change_request_id, body: { actor, note: notes[item.change_request_id]!.trim() } })}
                  >
                    Resolve
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : !open ? (
        <p className="text-xs text-muted-foreground">None. A request records what should be corrected; it changes no document or rule, and blocks approval while open.</p>
      ) : null}
      {resolve.isError ? <ErrorState error={resolve.error} /> : null}
      {open ? (
        <form
          className="space-y-2 rounded-md border border-border p-3"
          aria-label="Request changes"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(
              { extractionId, body: { actor, comparison_id: id, requested_correction: correction.trim(), ...(comment.trim() ? { comment: comment.trim() } : {}) } },
              {
                onSuccess: () => {
                  setOpen(false);
                  setCorrection("");
                  setComment("");
                },
              },
            );
          }}
        >
          <p className="text-xs text-muted-foreground">
            For {comparison.rule_id} · {comparison.field} in {comparison.policy_doc}.
          </p>
          <label htmlFor={`${id}-correction`} className="block text-xs font-medium">
            Requested correction
          </label>
          <textarea id={`${id}-correction`} rows={2} required value={correction} onChange={(event) => setCorrection(event.target.value)} className="w-full rounded-md border border-input bg-card p-2 text-[13px]" />
          <label htmlFor={`${id}-comment`} className="block text-xs font-medium">
            Comment
          </label>
          <Input id={`${id}-comment`} className="h-8 text-[13px]" value={comment} onChange={(event) => setComment(event.target.value)} />
          {create.isError ? <ErrorState error={create.error} /> : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={!actor || !correction.trim() || create.isPending} title={!actor ? "Say who is reviewing" : undefined}>
              Send request
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
