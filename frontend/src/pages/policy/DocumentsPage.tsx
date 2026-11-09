/**
 * Policy documents: a library grouped by policy category, searchable and filterable by business information, from
 * which exact revisions are selected for compilation; and the history of compilations, on its own tab.
 */
import { ChevronDown, ChevronRight, Copy, EyeOff, FileText, GitCompare, Loader2, Search, Upload, Wand2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useDocumentLibrary, useExtractions, useMarkDuplicates } from "@/api/queries";
import type { DocumentSummary } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SELECT } from "@/pages/evaluations/display";
import { ActorField, useActor } from "./actor";
import { ApprovalBadge, DocumentDrawer } from "./DocumentPanels";
import {
  allScopes,
  APPROVAL_TEXT,
  approvalOf,
  CATEGORIES,
  clusters,
  deduplication,
  documentUsage,
  displayTitle,
  filterAndSort,
  groupByCategory,
  identicalContent,
  INITIAL_QUERY,
  isHiddenDuplicate,
  keepOnly,
  keptCopy as keptCopyOf,
  oneOfEach,
  scopeText,
  similarlyNamed,
  UNCLASSIFIED,
  type CategoryKey,
  type LibraryQuery,
  type SortKey,
} from "./library";

export { ExtractionHistory } from "./ExtractionHistory";

/** A compile link for exact revisions: document_id:revision, comma-separated. */
export function compileLink(selected: readonly { document_id: string; revision: number }[]): string {
  return `/documents/compile?d=${selected.map((item) => `${encodeURIComponent(item.document_id)}:${item.revision}`).join(",")}`;
}

export function compareLink(a: { document_id: string; revision: number }, b: { document_id: string; revision: number }): string {
  return `/documents/compare?a=${encodeURIComponent(a.document_id)}:${a.revision}&b=${encodeURIComponent(b.document_id)}:${b.revision}`;
}

function DocumentRow({ document, selected, onToggle, onOpen, twins, similar, inCluster = false, hidden = false, onRestore, onKeep, hiddenCopies = 0 }: {
  document: DocumentSummary;
  /** Within identical copies: keep this one and hide the others. */
  onKeep?: () => void;
  /** Identical copies hidden behind this one. */
  hiddenCopies?: number;
  /** A hidden duplicate, shown only when hidden duplicates are: it can be restored. */
  hidden?: boolean;
  onRestore?: () => void;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
  twins: DocumentSummary[];
  similar: DocumentSummary[];
  inCluster?: boolean;
}) {
  const title = displayTitle(document);
  const scope = scopeText(document);
  return (
    <li className={cn("grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-start gap-3 px-4 py-2.5 sm:grid-cols-[1.5rem_minmax(0,1fr)_6rem_7rem]", selected && "bg-primary-soft/60")}>
      <input type="checkbox" aria-label={`Select ${title} (${document.filename})`} checked={selected} onChange={onToggle} className="mt-1 size-3.5 accent-primary" />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={onOpen} className="text-left text-[13px] font-medium text-foreground hover:text-primary hover:underline">
            {title}
          </button>
          <ApprovalBadge status={approvalOf(document)} />
          {hidden ? (
            <>
              <Badge variant="outline">
                <EyeOff aria-hidden />
                Hidden duplicate
              </Badge>
              {onRestore ? (
                <button type="button" onClick={onRestore} className="text-xs text-primary hover:underline">
                  Restore
                </button>
              ) : null}
            </>
          ) : null}
          {onKeep ? (
            <Button type="button" size="sm" variant="outline" className="ml-auto h-6 px-2 text-xs" onClick={onKeep}>
              Keep this one
            </Button>
          ) : null}
        </div>
        <p className="truncate font-mono text-[11px] text-subtle-foreground">
          {document.filename}
          {inCluster ? <span title={document.document_id}> · copy …{document.document_id.slice(-6)}</span> : null}
        </p>
        {document.description ? <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{document.description}</p> : null}
        {hiddenCopies ? (
          <p className="mt-0.5 flex items-center gap-1 text-[11px] text-subtle-foreground">
            <EyeOff className="size-3" aria-hidden />
            Kept copy: {hiddenCopies} identical {hiddenCopies === 1 ? "duplicate" : "duplicates"} hidden
          </p>
        ) : null}
        {scope || twins.length || similar.length ? (
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-subtle-foreground">
            {scope ? <span>{scope}</span> : null}
            {twins.length && !inCluster && !hiddenCopies ? (
              <Badge variant="info" title={`Same text as ${twins.map((twin) => twin.filename).join(", ")}`}>
                <Copy aria-hidden />
                Identical content
              </Badge>
            ) : null}
            {similar.length ? (
              <Badge variant="outline" title={`Similarly named: ${similar.map((other) => `${other.filename} r${other.revision}`).join(", ")}. Open it to compare.`}>
                Similar name
              </Badge>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="tabular hidden text-xs sm:block">Revision {document.revision}</div>
      <div className="text-right text-xs text-muted-foreground sm:text-left" title={formatDateTime(document.created_at)}>
        {formatRelative(document.created_at)}
      </div>
    </li>
  );
}

/**
 * Identical copies (one SHA-256) as one logical entry: its title, the copies' approvals as they are (one status only
 * when they all agree), and the copies themselves on demand, each its own document with its own approval.
 */
function CopiesEntry({ copies, expanded, onExpand, selected, onToggle, onOpen, row, keeping, keep }: {
  copies: DocumentSummary[];
  /** The copy chosen to keep, awaiting confirmation, and how to confirm (or, with null, cancel) it. */
  keeping: DocumentSummary | null;
  keep: { confirm: () => void; cancel: () => void; start: () => void; pending: boolean; error: unknown; actor: string; setActor: (value: string) => void };
  expanded: boolean;
  onExpand: () => void;
  selected: boolean;
  onToggle: () => void;
  onOpen: (document: DocumentSummary) => void;
  row: (document: DocumentSummary) => React.ReactNode;
}) {
  const first = copies[0]!;
  const statuses = copies.map(approvalOf);
  const same = statuses.every((status) => status === statuses[0]);
  const counts = (["approved", "needs_review", "draft"] as const).map((status) => [status, statuses.filter((item) => item === status).length] as const).filter(([, count]) => count);
  const panel = `copies-${first.content_sha256.slice(0, 12)}`;
  return (
    <li aria-label={`${displayTitle(first)}, ${copies.length} identical copies`}>
      <div className={cn("grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-start gap-3 px-4 py-2.5 sm:grid-cols-[1.5rem_minmax(0,1fr)_6rem_7rem]", selected && "bg-primary-soft/60")}>
        <input type="checkbox" aria-label={`Select ${displayTitle(first)} (one copy)`} title="Selects one copy: the approved one when there is one" checked={selected} onChange={onToggle} className="mt-1 size-3.5 accent-primary" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => onOpen(first)} className="text-left text-[13px] font-medium text-foreground hover:text-primary hover:underline">
              {displayTitle(first)}
            </button>
            {same ? (
              <ApprovalBadge status={statuses[0]!} />
            ) : (
              <span className="text-xs text-status-review-text" title="Each copy is approved on its own">
                Approvals differ: {counts.map(([status, count]) => `${count} ${APPROVAL_TEXT[status].toLowerCase()}`).join(", ")}
              </span>
            )}
          </div>
          <p className="truncate font-mono text-[11px] text-subtle-foreground">{first.filename}</p>
          {first.description ? <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{first.description}</p> : null}
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <button type="button" aria-expanded={expanded} aria-controls={panel} onClick={onExpand} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
              {expanded ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
              {copies.length} identical copies
            </button>
            <button type="button" onClick={keep.start} className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
              <Copy className="size-3.5" aria-hidden />
              Deduplicate: keep one
            </button>
          </div>
        </div>
        <div className="tabular hidden text-xs sm:block">Revision {first.revision}</div>
        <div className="text-right text-xs text-muted-foreground sm:text-left" title={formatDateTime(first.created_at)}>
          {formatRelative(first.created_at)}
        </div>
      </div>
      {keeping ? (
        <div role="region" aria-label={`Keep one copy of ${displayTitle(first)}`} className="mx-4 mb-2 ml-11 space-y-2 rounded-md border border-primary/30 bg-card px-3 py-2.5 text-[13px]">
          <p>
            Keep copy <span className="font-mono text-xs">…{keeping.document_id.slice(-6)}</span> ({APPROVAL_TEXT[approvalOf(keeping)].toLowerCase()}) and hide the other {copies.length - 1}.
          </p>
          <p className="text-xs text-muted-foreground">Nothing is deleted: the hidden copies keep their revisions, approvals and stored text, and can be shown and restored. To keep another copy, choose Keep this one on it.</p>
          <div className="flex flex-wrap items-end gap-2">
            {!keep.actor.trim() ? (
              <div className="w-52">
                <ActorField actor={keep.actor} setActor={keep.setActor} id={`keep-actor-${first.content_sha256.slice(0, 8)}`} />
              </div>
            ) : null}
            <Button type="button" size="sm" disabled={!keep.actor.trim() || keep.pending} onClick={keep.confirm}>
              {keep.pending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              Keep this copy, hide {copies.length - 1}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={keep.cancel}>
              Cancel
            </Button>
          </div>
          {keep.error ? <ErrorState error={keep.error} /> : null}
        </div>
      ) : null}
      {expanded ? (
        <ul id={panel} className="ml-8 divide-y divide-border border-l border-border bg-muted/20" aria-label="Copies">
          {copies.map(row)}
        </ul>
      ) : null}
    </li>
  );
}

export function Library({ onUpload }: { onUpload: () => void }) {
  const navigate = useNavigate();
  const documents = useDocumentLibrary();
  const extractions = useExtractions({ limit: 100, offset: 0 });
  const [query, setQuery] = useState<LibraryQuery>(INITIAL_QUERY);
  const [selected, setSelected] = useState<Map<string, DocumentSummary>>(new Map());
  // Every category starts collapsed; a search opens them all, so its matches show.
  const [expandedGroups, setExpandedGroups] = useState<Set<CategoryKey>>(new Set());
  const [showHidden, setShowHidden] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [keeping, setKeeping] = useState<DocumentSummary | null>(null);
  const [actor, setActor] = useActor();
  const mark = useMarkDuplicates();
  const [openId, setOpenId] = useState<string | null>(null);
  const [expandedCopies, setExpandedCopies] = useState<Set<string>>(new Set());
  const all = useMemo(() => documents.data ?? [], [documents.data]);
  const byId = useMemo(() => new Map(all.map((document) => [document.document_id, document])), [all]);
  const hiddenCount = useMemo(() => all.filter((document) => isHiddenDuplicate(document, byId)).length, [all, byId]);
  const listed = useMemo(() => (showHidden ? all : all.filter((document) => !isHiddenDuplicate(document, byId))), [all, byId, showHidden]);
  const plan = useMemo(() => deduplication(all), [all]);
  const hiddenBehind = useMemo(() => {
    const counts = new Map<string, number>();
    for (const document of all) if (isHiddenDuplicate(document, byId)) counts.set(document.duplicate_of!, (counts.get(document.duplicate_of!) ?? 0) + 1);
    return counts;
  }, [all, byId]);
  const shown = useMemo(() => filterAndSort(listed, query), [listed, query]);
  const groups = useMemo(() => groupByCategory(shown), [shown]);
  const twins = useMemo(() => identicalContent(all), [all]);
  const similar = useMemo(() => similarlyNamed(all), [all]);
  const usage = useMemo(() => documentUsage(all, extractions.data?.items ?? []), [all, extractions.data]);  // shown in the drawer only
  const scopes = useMemo(() => allScopes(all), [all]);
  const searching = query.search.trim() !== "";
  useEffect(() => {
    if (searching) setExpandedGroups(new Set(groups.map((group) => group.key)));
  }, [searching, query.search, groups]);

  if (documents.isPending) return <LoadingRows rows={6} label="Loading documents" />;
  if (documents.isError) return <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />;
  if (!all.length)
    return (
      <EmptyState
        icon={<FileText className="size-5" aria-hidden />}
        title="No policy documents yet"
        description="Upload the policy Markdown documents to compile rules from them."
        action={
          <Button size="sm" onClick={onUpload}>
            <Upload aria-hidden />
            Upload document
          </Button>
        }
      />
    );

  const set = (change: Partial<LibraryQuery>) => setQuery((current) => ({ ...current, ...change }));
  const toggle = (document: DocumentSummary) =>
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(document.document_id)) next.delete(document.document_id);
      else next.set(document.document_id, document);
      return next;
    });
  // Selecting many selects one copy of each text: identical copies would compile the same text twice.
  const setGroup = (members: DocumentSummary[], on: boolean) =>
    setSelected((current) => {
      const next = new Map(current);
      for (const document of members) next.delete(document.document_id);
      if (on) for (const document of oneOfEach(members)) next.set(document.document_id, document);
      return next;
    });
  const everyOne = oneOfEach(shown);
  const allPicked = everyOne.length > 0 && everyOne.every((document) => selected.has(document.document_id));
  const chosen = [...selected.values()];
  const open = openId ? all.find((document) => document.document_id === openId) ?? null : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2" role="search" aria-label="Find documents">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle-foreground" aria-hidden />
          <Input aria-label="Search documents" placeholder="Search by title, filename or description" className="h-9 pl-8 text-[13px]" value={query.search} onChange={(event) => set({ search: event.target.value })} />
        </div>
        <select aria-label="Category" className={cn(SELECT, "w-56")} value={query.category} onChange={(event) => set({ category: event.target.value as CategoryKey | "" })}>
          <option value="">All categories</option>
          {CATEGORIES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
          <option value={UNCLASSIFIED}>Unclassified</option>
        </select>
        <select aria-label="Scope" className={cn(SELECT, "w-44")} value={query.scope} onChange={(event) => set({ scope: event.target.value })} disabled={!scopes.length}>
          <option value="">{scopes.length ? "Any scope" : "No scope stated"}</option>
          {scopes.map((scope) => (
            <option key={scope} value={scope}>
              {scope}
            </option>
          ))}
        </select>
        <select aria-label="Sort by" className={cn(SELECT, "w-40")} value={query.sort} onChange={(event) => set({ sort: event.target.value as SortKey })}>
          <option value="title">Sort by title</option>
          <option value="updated">Sort by last updated</option>
          <option value="revision">Sort by revision</option>
        </select>
      </div>

      {chosen.length ? (
        <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-card px-3 py-2 shadow-sm" role="region" aria-label="Selection">
          <span className="text-[13px] font-medium">
            {chosen.length} {chosen.length === 1 ? "document" : "documents"} selected
          </span>
          <Button type="button" size="sm" variant="ghost" onClick={() => setSelected(new Map())}>
            <X aria-hidden />
            Clear selection
          </Button>
          <div className="ml-auto flex gap-2">
            {chosen.length === 2 ? (
              <Button asChild size="sm" variant="outline">
                <Link to={compareLink(chosen[0]!, chosen[1]!)}>
                  <GitCompare aria-hidden />
                  Compare
                </Link>
              </Button>
            ) : null}
            <Button type="button" size="sm" onClick={() => navigate(compileLink(chosen))}>
              <Wand2 aria-hidden />
              Compile selected
            </Button>
          </div>
        </div>
      ) : null}

      <div role="radiogroup" aria-label="Approval" className="flex flex-wrap gap-1.5">
        {(
          [
            ["", "All"],
            ["approved", "Approved"],
            ["needs_review", "Needs review"],
            ["draft", "Draft"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key || "all"}
            type="button"
            role="radio"
            aria-checked={query.approval === key}
            onClick={() => set({ approval: key })}
            className={cn("rounded-full border px-3 py-1 text-xs font-medium", query.approval === key ? "border-primary bg-primary-soft text-primary" : "border-border text-muted-foreground hover:bg-muted")}
          >
            {label} · {key ? listed.filter((document) => approvalOf(document) === key).length : listed.length}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <label className="flex items-center gap-1.5 font-medium text-foreground">
          <input
            type="checkbox"
            className="size-3.5 accent-primary"
            checked={allPicked}
            ref={(element) => {
              if (element) element.indeterminate = !allPicked && everyOne.some((document) => selected.has(document.document_id));
            }}
            onChange={(event) => setGroup(shown, event.target.checked)}
            disabled={!everyOne.length}
          />
          Select all
        </label>
        <span aria-live="polite">{shown.length === listed.length ? `${listed.length} documents` : `${shown.length} of ${listed.length} documents`}</span>
        {hiddenCount ? (
          <button type="button" className="text-primary hover:underline" onClick={() => setShowHidden((value) => !value)}>
            {showHidden ? `Hide ${hiddenCount === 1 ? "the duplicate" : `the ${hiddenCount} duplicates`}` : `${hiddenCount} ${hiddenCount === 1 ? "duplicate" : "duplicates"} hidden · show`}
          </button>
        ) : null}
        {plan.some((change) => change.duplicate_of) ? (
          <Button type="button" size="sm" variant="outline" className="ml-auto h-7 text-xs" onClick={() => setConfirming(true)}>
            <Copy aria-hidden />
            Deduplicate all: keep one of each ({plan.filter((change) => change.duplicate_of).length} to hide)
          </Button>
        ) : null}
      </div>
      {confirming ? (
        <div role="region" aria-label="Hide duplicates" className="space-y-2 rounded-md border border-primary/30 bg-card px-3 py-2.5 text-[13px]">
          <p>
            Hide {plan.filter((change) => change.duplicate_of).length} copies whose text is identical to another document. One copy of each is kept: the approved one where there is one, else the oldest. To choose which copy to keep, use Deduplicate: keep one on a document instead.
          </p>
          <p className="text-xs text-muted-foreground">Nothing is deleted: every copy keeps its revisions, approvals and stored text, and any can be restored. Documents that are only similar are never hidden: compare them instead.</p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-52">
              <ActorField actor={actor} setActor={setActor} id="dedupe-actor" />
            </div>
            <Button type="button" size="sm" disabled={!actor.trim() || mark.isPending} onClick={() => mark.mutate({ actor: actor.trim(), changes: plan }, { onSuccess: () => { setConfirming(false); setSelected(new Map()); } })}>
              {mark.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              Hide duplicates
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
          {mark.isError ? <ErrorState error={mark.error} /> : null}
        </div>
      ) : null}
      {groups.length === 0 ? <EmptyState title="No document matches" description="Try other words, or clear the filters." className="py-6" /> : null}
      {groups.map((group) => {
        const isCollapsed = !expandedGroups.has(group.key);
        const picked = group.documents.filter((document) => selected.has(document.document_id)).length;
        const panel = `group-${group.key}`;
        return (
          <section key={group.key} className="overflow-hidden rounded-lg border border-border bg-card" aria-label={group.label}>
            <header className="flex items-center gap-2 border-b border-border bg-muted/40 px-4 py-2">
              <input
                type="checkbox"
                aria-label={`Select all in ${group.label}`}
                checked={picked > 0 && oneOfEach(group.documents).every((document) => selected.has(document.document_id))}
                ref={(element) => {
                  if (element) element.indeterminate = picked > 0 && !oneOfEach(group.documents).every((document) => selected.has(document.document_id));
                }}
                onChange={(event) => setGroup(group.documents, event.target.checked)}
                className="size-3.5 accent-primary"
              />
              <button
                type="button"
                className="flex flex-1 items-center gap-1.5 text-left text-[13px] font-semibold"
                aria-expanded={!isCollapsed}
                aria-controls={panel}
                onClick={() =>
                  setExpandedGroups((current) => {
                    const next = new Set(current);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  })
                }
              >
                {isCollapsed ? <ChevronRight className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
                {group.label}
                <span className="font-normal text-muted-foreground">· {group.documents.length}</span>
              </button>
              {group.key === UNCLASSIFIED ? <span className="hidden text-xs text-subtle-foreground md:inline">Open a document to choose its category</span> : null}
            </header>
            {isCollapsed ? null : (
              <ul id={panel} className="divide-y divide-border" aria-label={`${group.label} documents`}>
                {clusters(group.documents).map((cluster) => {
                  const row = (document: DocumentSummary, inCluster = false) => (
                    <DocumentRow
                      key={document.document_id}
                      document={document}
                      selected={selected.has(document.document_id)}
                      onToggle={() => toggle(document)}
                      onOpen={() => setOpenId(document.document_id)}
                      twins={twins.get(document.document_id) ?? []}
                      similar={similar.get(document.document_id) ?? []}
                      inCluster={inCluster}
                      hidden={isHiddenDuplicate(document, byId)}
                      hiddenCopies={showHidden ? 0 : hiddenBehind.get(document.document_id) ?? 0}
                      onKeep={inCluster ? () => setKeeping(document) : undefined}
                      onRestore={actor.trim() ? () => mark.mutate({ actor: actor.trim(), changes: [{ document_id: document.document_id, duplicate_of: null }] }) : undefined}
                    />
                  );
                  if (cluster.length === 1) return row(cluster[0]!);
                  return (
                    <CopiesEntry
                      key={`copies-${cluster[0]!.content_sha256}`}
                      copies={cluster}
                      expanded={expandedCopies.has(cluster[0]!.content_sha256)}
                      onExpand={() =>
                        setExpandedCopies((current) => {
                          const next = new Set(current);
                          if (next.has(cluster[0]!.content_sha256)) next.delete(cluster[0]!.content_sha256);
                          else next.add(cluster[0]!.content_sha256);
                          return next;
                        })
                      }
                      selected={cluster.some((document) => selected.has(document.document_id))}
                      onToggle={() => {
                        const chosenCopy = cluster.find((document) => selected.has(document.document_id));
                        if (chosenCopy) toggle(chosenCopy);
                        else toggle(cluster.find((document) => approvalOf(document) === "approved") ?? cluster[0]!);
                      }}
                      onOpen={(document) => setOpenId(document.document_id)}
                      row={(document) => row(document, true)}
                      keeping={keeping && cluster.some((copy) => copy.document_id === keeping.document_id) ? keeping : null}
                      keep={{
                        start: () => {
                          setKeeping(keptCopyOf(cluster));
                          if (!expandedCopies.has(cluster[0]!.content_sha256)) setExpandedCopies((current) => new Set([...current, cluster[0]!.content_sha256]));
                        },
                        cancel: () => setKeeping(null),
                        confirm: () =>
                          keeping &&
                          mark.mutate(
                            { actor: actor.trim(), changes: keepOnly(all.filter((document) => document.content_sha256 === cluster[0]!.content_sha256), keeping) },
                            { onSuccess: () => { setKeeping(null); setSelected(new Map()); } },
                          ),
                        pending: mark.isPending,
                        error: mark.isError ? mark.error : null,
                        actor,
                        setActor,
                      }}
                    />
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
      <DocumentDrawer
        document={open}
        usage={open ? usage.get(open.document_id) : undefined}
        twins={open ? twins.get(open.document_id) ?? [] : []}
        similar={open ? similar.get(open.document_id) ?? [] : []}
        onClose={() => setOpenId(null)}
      />
    </div>
  );
}

/** The document library lives in Policy Management now; an old link, history tab included, lands in the right place. */
export function DocumentsRedirect() {
  const [params] = useSearchParams();
  return <Navigate to={params.get("tab") === "history" ? "/policies?tab=review" : "/policies?tab=documents"} replace />;
}
