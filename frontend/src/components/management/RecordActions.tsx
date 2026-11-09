import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { request } from "@/api/client";
import { ErrorState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Input } from "@/components/ui/misc";

/** Delete is confirmed explicitly; stored immutable payloads remain available internally for audit. */
export function RecordActions({ path, title, field, value, onDeleted, recordName, deleteDisabled = false }: {
  path: string; title: string; field?: "label" | "description"; value?: string | null;
  onDeleted: () => void; recordName?: string; deleteDisabled?: boolean;
}) {
  const client = useQueryClient();
  const [action, setAction] = useState<"edit" | "delete" | null>(null);
  const [text, setText] = useState(value ?? "");
  const save = useMutation({
    mutationFn: () => request(path, { method: action === "delete" ? "DELETE" : "PATCH", ...(action === "edit" && field ? { body: { [field]: text.trim() || null } } : {}) }),
    onSuccess: () => { const deleted = action === "delete"; setAction(null); void client.invalidateQueries({ queryKey: ["records"] }); if (deleted) onDeleted(); },
  });
  const close = useCallback(() => { if (!save.isPending) setAction(null); }, [save.isPending]);
  function open(next: "edit" | "delete") { save.reset(); setText(value ?? ""); setAction(next); }
  return <>
    {field ? <Button type="button" variant="outline" size="sm" onClick={() => open("edit")}>Edit {field}</Button> : null}
    <Button type="button" variant="outline" size="sm" className="text-status-flag-text" aria-label={recordName ? `Delete ${title}: ${recordName}` : undefined} disabled={deleteDisabled} title={deleteDisabled ? "Cancel or finish the evaluation before deleting it" : undefined} onClick={() => open("delete")}>Delete {title}</Button>
    <Drawer open={action !== null} onClose={close} title={action === "delete" ? `Delete ${title}?` : `Edit ${field}`}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {action === "delete" ? <p className="text-sm text-muted-foreground">Are you sure you want to delete this {title}{recordName ? <>: <strong>{recordName}</strong></> : null}? This removes it from normal views. Stored results and source records are retained for history. Records required by active jobs or ruleset approvals cannot be deleted.</p> : <label className="block space-y-2 text-sm"><span className="capitalize">{field}</span><Input aria-label={`New ${field}`} value={text} maxLength={field === "label" ? 120 : 2000} onChange={(e) => setText(e.target.value)} /></label>}
        {save.isError ? <ErrorState error={save.error} /> : null}
        <div className="flex gap-2"><Button type="button" variant="outline" disabled={save.isPending} onClick={() => setAction(null)}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : action === "delete" ? "Confirm deletion" : "Save changes"}</Button></div>
      </form>
    </Drawer>
  </>;
}
