import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { request } from "@/api/client";
import type { RulesetDetail } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Input } from "@/components/ui/misc";
import { ActorField, useActor } from "./actor";

/** Similarity is never approval: this shortcut requires exact artifact identity and compiler verification. */
export function DuplicatePublication({ version }: { version: RulesetDetail }) {
  const [open, setOpen] = useState(false);
  const [actor, setActor] = useActor();
  const [reason, setReason] = useState("");
  const client = useQueryClient();
  const navigate = useNavigate();
  const path = `/v1/rulesets/${encodeURIComponent(version.ruleset_id)}/duplicate-publication`;
  const match = useQuery({ queryKey: ["records", "duplicate-publication", version.ruleset_id], queryFn: () => request<{ published: { name: string; version: number; mode: string } | null }>(path), retry: false });
  const publish = useMutation({ mutationFn: () => request<RulesetDetail>(path, { method: "POST", body: { actor: actor.trim(), rationale: reason.trim() } }), onSuccess: data => { void client.invalidateQueries({ queryKey: ["records"] }); setOpen(false); navigate(`/policies/${encodeURIComponent(data.ruleset_id)}`); } });
  const close = useCallback(() => { if (!publish.isPending) setOpen(false); }, [publish.isPending]);
  if (match.isError) return <ErrorState error={match.error} />;
  if (!match.data?.published) return null;
  const donor = match.data.published;
  return <div className="space-y-2 rounded-lg border border-primary bg-primary-soft p-4" role="region" aria-label="Identical published rules">
    <h2 className="font-semibold">These rules are already published</h2>
    <p className="text-sm">The complete artifact is identical to {donor.name} v{donor.version}. You can delete this draft or review version, or publish an identical successor with a fresh approval.</p>
    <Button size="sm" onClick={() => setOpen(true)}>Publish anyway</Button>
    <Drawer open={open} onClose={close} title="Publish identical rules anyway?">
      <form className="space-y-4" onSubmit={event => { event.preventDefault(); publish.mutate(); }}>
        <p className="text-sm">This creates a new version for {donor.mode}, rechecks the published source documents and review decisions, records your acceptance, and obtains a new approval before publishing. This version and the previous publication remain unchanged.</p>
        <ActorField actor={actor} setActor={setActor} id="duplicate-actor" />
        <label className="block text-sm space-y-2">Reason for publishing again<Input value={reason} maxLength={1000} onChange={event => setReason(event.target.value)} /></label>
        {publish.isError ? <ErrorState error={publish.error} /> : null}
        <div className="flex gap-2"><Button type="button" variant="outline" disabled={publish.isPending} onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" disabled={publish.isPending || !actor.trim() || !reason.trim()}>{publish.isPending ? "Publishing…" : "Confirm publication"}</Button></div>
      </form>
    </Drawer>
  </div>;
}
