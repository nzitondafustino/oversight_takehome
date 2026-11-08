import { UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Input } from "@/components/ui/misc";

const STORAGE_KEY = "epv.actor";

function stored(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Who this viewer says they are, remembered in this browser only. The API records it as a claim. */
export function useActor() {
  const [actor, setActor] = useState(stored);
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, actor);
    } catch {
      // storage may be blocked: the name then lasts for this page only
    }
  }, [actor]);
  return [actor, setActor] as const;
}

export function ActorField({ actor, setActor, id = "actor" }: { actor: string; setActor: (value: string) => void; id?: string }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="flex items-center gap-1 text-xs font-medium text-foreground">
        <UserRound className="size-3.5" aria-hidden />
        Acting as
      </label>
      <Input id={id} className="h-8 text-[13px]" placeholder="Your name, such as j.doe" value={actor} maxLength={200} onChange={(event) => setActor(event.target.value)} autoComplete="off" />
      <p className="text-xs leading-4 text-subtle-foreground">Recorded with each step as a claim: this deployment authenticates no one.</p>
    </div>
  );
}

/** A comparison value as the compiler gave it. Null is shown as missing, never as an empty value. */
export function ValueText({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-status-review-text">missing</span>;
  if (typeof value === "string") return <span className="whitespace-pre-wrap break-words">“{value}”</span>;
  return <code className="break-all font-mono text-xs">{JSON.stringify(value)}</code>;
}
