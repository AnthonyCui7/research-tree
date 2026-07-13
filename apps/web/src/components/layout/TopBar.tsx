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
      if (event.key === "Escape" && document.activeElement === inputRef.current) {
        setQuery("");
        inputRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <header className="top-bar">
      <div className="top-bar-title">
        {sidebarCollapsed ? <button className="sidebar-reopen-button icon-button" type="button" onClick={onToggleSidebar} aria-label="Show workspace sidebar" title="Show sidebar">
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3.5" width="14" height="13" rx="2" />
            <path d="M8.5 3.5v13" />
          </svg>
        </button> : null}
        <strong>{workspaceTitle}</strong>
      </div>
      <div className="top-bar-actions">
        <button className="top-bar-button" type="button" onClick={onOpenHistory} disabled={!tree}>History</button>
        <div className="workspace-search">
          <label className="search-control">
            <span className="sr-only">Search papers and branches in this workspace</span>
            <input ref={inputRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search papers and branches" disabled={!tree} />
            <kbd>⌘K</kbd>
          </label>
          {query.trim() ? (
            <div className="search-results" role="listbox" aria-label="Workspace search results">
              {results.length > 0 ? results.map((node) => (
                <button key={node.id} type="button" role="option" onClick={() => { onSelectNode(node.id); setQuery(""); }}>
                  <span>{node.kind === "root" ? "Topic" : node.kind === "branch" ? "Branch" : "Paper"}</span>
                  <strong>{node.title}</strong>
                </button>
              )) : <p>No matches in this workspace.</p>}
            </div>
          ) : null}
        </div>
      </div>
      <div className="profile-panel" aria-label="Profile and sign-in status">
        <div className="profile-mark" aria-hidden="true">RT</div>
        <div className="profile-copy">
          <span>Local profile</span>
          <button type="button" disabled>Sign in later</button>
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
