import { useEffect, useMemo, useRef, useState } from "react";
import type { TreeNodeId, TreeViewModel } from "../../lib/types";

type TopBarProps = {
  workspaceTitle: string;
  tree: TreeViewModel | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onOpenHistory: () => void;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
};

export function TopBar({
  workspaceTitle,
  tree,
  onSelectNode,
  onOpenHistory,
  sidebarCollapsed,
  onToggleSidebar,
}: TopBarProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!tree || !normalized) return [];
    return tree.nodes.filter((node) => searchText(node).includes(normalized)).slice(0, 8);
  }, [query, tree]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
      if (event.key === "Escape") {
        setQuery("");
        inputRef.current?.blur();
      }
    }
    function onPointerDown(event: PointerEvent) {
      if (!searchRef.current?.contains(event.target as Node)) {
        setQuery("");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, []);

  return (
    <header className="relative z-[calc(var(--z-panel)_+_1)] grid min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-[18px] border-b border-border bg-surface px-5 max-[720px]:h-auto max-[720px]:grid-cols-[minmax(0,1fr)_auto] max-[720px]:grid-rows-[auto_auto] max-[720px]:gap-x-3 max-[720px]:gap-y-2.5 max-[720px]:p-3">
      <div className="flex min-w-0 items-center gap-3">
        {sidebarCollapsed ? <button className="grid h-8 w-8 flex-none place-items-center rounded-md border border-border bg-surface p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:border-border enabled:hover:bg-surface enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px]" type="button" onClick={onToggleSidebar} aria-label="Show workspace sidebar" title="Show sidebar">
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3.5" width="14" height="13" rx="2" />
            <path d="M8.5 3.5v13" />
          </svg>
        </button> : null}
        <strong className="min-w-0 truncate text-[15px] font-semibold leading-normal tracking-normal text-text-primary">{workspaceTitle}</strong>
      </div>
      <div className="flex min-w-0 items-center justify-center gap-2 max-[720px]:col-span-full max-[720px]:row-start-2 max-[720px]:w-full">
        <button className="min-h-[34px] flex-none rounded-sm border border-border-strong bg-surface px-[11px] py-[7px] text-xs font-semibold text-text-primary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:border-accent enabled:hover:bg-accent-subtle enabled:hover:text-accent-deep disabled:cursor-not-allowed disabled:border-border disabled:bg-surface-subtle disabled:text-text-muted" type="button" onClick={onOpenHistory} disabled={!tree?.currentVersionHash}>History</button>
        <div className="relative min-w-0 flex-1" ref={searchRef}>
          <label className="grid w-[300px] grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-sm border border-border bg-surface-subtle px-[9px] py-[7px] transition-[background-color,border-color] duration-200 ease-research focus-within:border-accent focus-within:bg-surface max-[720px]:w-full">
            <span className="sr-only">Search papers and branches in this workspace</span>
            <input className="w-full min-w-0 border-0 bg-transparent text-xs text-text-primary outline-0 placeholder:text-text-secondary" ref={inputRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search papers and branches" disabled={!tree} />
            <kbd className="rounded-[3px] border border-border bg-surface px-1 py-0.5 font-sans text-[10px] text-text-secondary">⌘K</kbd>
          </label>
          {query.trim() ? (
            <div className="scrollbar-rt absolute top-[calc(100%_+_6px)] right-0 z-dropdown max-h-[min(460px,70vh)] w-[min(420px,calc(100vw_-_32px))] overflow-y-auto rounded-md border border-border-strong bg-surface shadow-popover" aria-label="Workspace search results">
              {results.length > 0 ? results.map((node) => (
                <button className="grid w-full gap-[3px] border-0 border-b border-border bg-transparent px-[13px] py-[11px] text-left last:border-b-0 hover:bg-accent-subtle" key={node.id} type="button" onClick={() => { onSelectNode(node.id); setQuery(""); }}>
                  <span className="text-[10px] text-text-secondary">{node.kind === "root" ? "Topic" : node.kind === "branch" ? "Branch" : "Paper"}</span>
                  <strong className="text-[13px] leading-[1.35]">{node.title}</strong>
                </button>
              )) : <p className="m-0 p-[15px] text-[13px] text-text-secondary">No matches in this workspace.</p>}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex min-w-[132px] items-center justify-self-end gap-2 max-[980px]:hidden" aria-label="Profile and sign-in status">
        <div className="grid h-[30px] w-[30px] flex-none place-items-center rounded-full border border-border-strong bg-surface text-[10px] font-bold text-accent-deep" aria-hidden="true">RT</div>
        <div className="grid min-w-0 gap-0.5">
          <span className="truncate text-[11px] font-semibold text-text-primary">Local profile</span>
          <button className="w-fit border-0 bg-transparent p-0 text-[10px] text-text-secondary disabled:cursor-default disabled:text-text-muted" type="button" disabled title="Accounts are not available in the local build">Sign in later</button>
        </div>
      </div>
    </header>
  );
}

function searchText(node: TreeViewModel["nodes"][number]): string {
  if (node.kind === "root") {
    return `${node.title} ${node.overview} ${node.keyTerms.join(" ")}`.toLowerCase();
  }
  if (node.kind === "branch") {
    return `${node.title} ${node.description} ${node.whyItMatters} ${node.tags.join(" ")}`.toLowerCase();
  }
  return `${node.title} ${node.authors.join(" ")} ${node.tldr || ""} ${node.importance}`.toLowerCase();
}
