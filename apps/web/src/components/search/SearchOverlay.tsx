import { useEffect, useMemo, useRef, useState } from "react";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { branchTint } from "../../lib/familyTint";
import { pluralize } from "../../lib/format";
import { SearchIcon } from "../ui/icons";
import { authorLine, publicationDate } from "../tree/TreeNode";
import type { BranchTreeNode, PaperTreeNode, TreeNodeId, TreeViewModel } from "../../lib/types";

type SearchOverlayProps = {
  tree: TreeViewModel;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onClose: () => void;
};

type Result =
  | { kind: "branch"; node: BranchTreeNode }
  | { kind: "paper"; node: PaperTreeNode };

const MAX_PAPER_RESULTS = 6;
const IDLE_PAPER_RESULTS = 4;

export function SearchOverlay({ tree, onSelectNode, onClose }: SearchOverlayProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const { closing, dismiss } = useDismissAnimation(onClose, DIALOG_EXIT_MS);

  // Escape is claimed here rather than left to the shell, so the overlay gets to
  // play its exit instead of being unmounted the moment the key lands.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        dismiss();
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dismiss]);

  const { branches, papers, results } = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const allBranches = tree.nodes.filter((node): node is BranchTreeNode => node.kind === "branch");
    const allPapers = tree.nodes.filter((node): node is PaperTreeNode => node.kind === "paper");
    const matchedBranches = normalized
      ? allBranches.filter((node) => branchText(node).includes(normalized))
      : allBranches;
    const matchedPapers = (
      normalized ? allPapers.filter((node) => paperText(node).includes(normalized)) : allPapers
    ).slice(0, normalized ? MAX_PAPER_RESULTS : IDLE_PAPER_RESULTS);
    return {
      branches: matchedBranches,
      papers: matchedPapers,
      results: [
        ...matchedBranches.map((node): Result => ({ kind: "branch", node })),
        ...matchedPapers.map((node): Result => ({ kind: "paper", node })),
      ],
    };
  }, [query, tree]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  // Keyboard traversal is advertised in the footer, so it has to actually keep
  // the highlighted row in view as it moves.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  function open(result: Result | undefined) {
    if (!result) return;
    onSelectNode(result.node.id);
    dismiss();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (results.length === 0 ? 0 : (index + 1) % results.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) =>
        results.length === 0 ? 0 : (index - 1 + results.length) % results.length,
      );
    } else if (event.key === "Enter") {
      event.preventDefault();
      open(results[activeIndex]);
    }
  }

  return (
    <div
      className={cx(
        "fixed inset-0 z-overlay flex items-start justify-center bg-[rgb(31_35_40_/_30%)] pt-[92px] max-[720px]:pt-14",
        closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
      )}
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      <div
        className={cx(
          "flex max-h-[60vh] w-[620px] max-w-[calc(100vw-48px)] flex-col overflow-hidden rounded-[13px] bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
        role="dialog"
        aria-modal="true"
        aria-label="Search this workspace"
        onKeyDown={onKeyDown}
      >
        <div className="flex flex-none items-center gap-[11px] border-b border-hairline px-[18px] py-3.5">
          <SearchIcon className="h-[15px] w-[15px] flex-none text-text-muted" />
          {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
          <input
            className="min-w-0 flex-1 border-0 bg-transparent text-[14.5px] text-text-primary outline-0 placeholder:text-text-muted"
            autoFocus
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search branches and papers…"
            aria-label="Search branches and papers"
          />
          <kbd className="flex-none rounded-sm border border-border px-1.5 py-px font-sans text-[10.5px] text-text-muted">
            esc
          </kbd>
        </div>

        <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-2 pt-2 pb-2.5" ref={listRef}>
          {branches.length > 0 ? (
            <>
              <GroupLabel>Branches</GroupLabel>
              {branches.map((node, index) => (
                <ResultRow
                  key={node.id}
                  active={activeIndex === index}
                  onHover={() => setActiveIndex(index)}
                  onSelect={() => open({ kind: "branch", node })}
                  badge="B"
                  tint={branchTint(node.family)}
                  title={node.title}
                  trailing={pluralize(node.paperCount, "paper")}
                />
              ))}
            </>
          ) : null}
          {papers.length > 0 ? (
            <>
              <GroupLabel>Papers</GroupLabel>
              {papers.map((node, index) => {
                const resultIndex = branches.length + index;
                return (
                  <ResultRow
                    key={node.id}
                    active={activeIndex === resultIndex}
                    onHover={() => setActiveIndex(resultIndex)}
                    onSelect={() => open({ kind: "paper", node })}
                    badge="P"
                    tint="#f1f3f4"
                    title={node.title}
                    subtitle={`${authorLine(node.authors)} · ${publicationDate(node)} · ${node.branchTitle}`}
                  />
                );
              })}
            </>
          ) : null}
          {results.length === 0 ? (
            <p className="m-0 p-7 text-center text-[13px] text-text-muted">
              No branches or papers match “{query.trim()}”.
            </p>
          ) : null}
        </div>

        <div className="flex flex-none items-center gap-3.5 border-t border-hairline bg-surface-muted px-[18px] py-[9px] text-[10.5px] text-text-muted">
          <span className="flex items-center gap-1.5 max-[520px]:hidden">
            <FooterKey>↑↓</FooterKey> navigate
          </span>
          <span className="flex items-center gap-1.5 max-[520px]:hidden">
            <FooterKey>⏎</FooterKey> open
          </span>
          <span className="ml-auto">Searches this workspace only</span>
        </div>
      </div>
    </div>
  );
}

function GroupLabel({ children }: { children: string }) {
  return (
    <div className="px-3 pt-2 pb-1 text-[10.5px] font-semibold tracking-[0.06em] text-text-muted uppercase">
      {children}
    </div>
  );
}

function FooterKey({ children }: { children: string }) {
  return (
    <kbd className="rounded-sm border border-border bg-surface px-1 font-sans text-[10.5px]">
      {children}
    </kbd>
  );
}

function ResultRow({
  active,
  onHover,
  onSelect,
  badge,
  tint,
  title,
  subtitle,
  trailing,
}: {
  active: boolean;
  onHover: () => void;
  onSelect: () => void;
  badge: string;
  tint: string;
  title: string;
  subtitle?: string;
  trailing?: string;
}) {
  return (
    <button
      className={cx(
        "flex w-full items-center gap-[11px] rounded-md border-0 px-3 py-2 text-left transition-[background-color] duration-100",
        active ? "bg-surface-subtle" : "bg-transparent",
      )}
      type="button"
      data-active={active}
      onMouseMove={onHover}
      onFocus={onHover}
      onClick={onSelect}
    >
      <span
        className="grid h-5 w-5 flex-none place-items-center rounded-[6px] border border-border text-[9.5px] font-semibold text-text-secondary"
        style={{ background: tint }}
        aria-hidden="true"
      >
        {badge}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-text-primary">{title}</span>
        {subtitle ? (
          <span className="mt-px block truncate text-[11px] text-text-muted">{subtitle}</span>
        ) : null}
      </span>
      {trailing ? <span className="flex-none text-[11px] text-text-muted">{trailing}</span> : null}
    </button>
  );
}

function branchText(node: BranchTreeNode): string {
  return `${node.title} ${node.description} ${node.whyItMatters} ${node.tags.join(" ")}`.toLowerCase();
}

function paperText(node: PaperTreeNode): string {
  return `${node.title} ${node.authors.join(" ")} ${node.tldr ?? ""} ${node.importance} ${node.branchTitle}`.toLowerCase();
}
