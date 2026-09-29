import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { useModalDialog } from "../../lib/modalDialog";
import { branchTint } from "../../lib/familyTint";
import { pluralize } from "../../lib/format";
import { keycapClass, kickerClass } from "../../lib/controlClasses";
import { PaperIcon, SearchIcon, TreeIcon } from "../ui/icons";
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

/**
 * One text box that filters the tree. Focus stays in the box the whole time:
 * the rows are options the arrow keys move through, not controls to tab to,
 * and the box tells assistive technology which row is current.
 */
export function SearchOverlay({ tree, onSelectNode, onClose }: SearchOverlayProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const { ref, closing, dismiss } = useModalDialog(DIALOG_EXIT_MS, inputRef);

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
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  function open(result: Result | undefined) {
    if (!result) return;
    onSelectNode(result.node.id);
    dismiss();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    // The shell's shortcuts stop at a modal, so the one that opened this
    // overlay is answered here: pressed again, it closes it.
    event.stopPropagation();
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dismiss();
    } else if (event.key === "ArrowDown") {
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
    } else if (event.key === "Escape") {
      event.preventDefault();
      dismiss();
    }
  }

  return (
    <dialog
      ref={ref}
      className={cx(
        "fixed inset-0 m-0 flex h-full max-h-none w-full max-w-none items-start justify-center border-0 bg-transparent p-6 pt-[92px] text-text-primary outline-none max-[720px]:pt-14",
        closing ? "[&::backdrop]:animate-backdrop-exit" : "[&::backdrop]:animate-backdrop-enter",
      )}
      tabIndex={-1}
      aria-label="Search this workspace"
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={onKeyDown}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      <div
        className={cx(
          "flex max-h-[60vh] w-[640px] max-w-full flex-col overflow-hidden rounded-2xl bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
      >
        {/* The glyph sits in a slot as wide as a result's icon tile, so the
            query lines up with the titles it matches. */}
        <div className="box-content flex h-14 flex-none items-center gap-3 border-b border-hairline px-5">
          <span className="grid h-7 w-7 flex-none place-items-center text-text-muted" aria-hidden="true">
            <SearchIcon className="size-4" />
          </span>
          <input
            ref={inputRef}
            className="min-w-0 flex-1 border-0 bg-transparent text-15 text-text-primary outline-0 placeholder:text-text-muted"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search branches and papers…"
            aria-label="Search branches and papers"
            role="combobox"
            aria-expanded="true"
            aria-autocomplete="list"
            aria-controls="search-results"
            aria-activedescendant={results.length > 0 ? optionId(activeIndex) : undefined}
          />
          <kbd className={keycapClass}>esc</kbd>
        </div>

        <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-2 pt-1 pb-2" ref={listRef}>
          <div
            role="listbox"
            id="search-results"
            aria-label="Matching branches and papers"
            // A click on a row must not take focus from the box.
            onMouseDown={(event) => event.preventDefault()}
          >
            {branches.length > 0 ? (
              <div role="group" aria-labelledby="search-group-branches">
                <GroupLabel id="search-group-branches">Branches</GroupLabel>
                {branches.map((node, index) => (
                  <ResultRow
                    key={node.id}
                    id={optionId(index)}
                    active={activeIndex === index}
                    onHover={() => setActiveIndex(index)}
                    onSelect={() => open({ kind: "branch", node })}
                    icon={<TreeIcon className="size-4" />}
                    tint={branchTint(node.family)}
                    title={node.title}
                    trailing={pluralize(node.paperCount, "paper")}
                  />
                ))}
              </div>
            ) : null}
            {papers.length > 0 ? (
              <div role="group" aria-labelledby="search-group-papers">
                <GroupLabel id="search-group-papers">Papers</GroupLabel>
                {papers.map((node, index) => {
                  const resultIndex = branches.length + index;
                  return (
                    <ResultRow
                      key={node.id}
                      id={optionId(resultIndex)}
                      active={activeIndex === resultIndex}
                      onHover={() => setActiveIndex(resultIndex)}
                      onSelect={() => open({ kind: "paper", node })}
                      icon={<PaperIcon className="size-4" />}
                      tint="var(--color-surface)"
                      title={node.title}
                      subtitle={`${authorLine(node.authors)} · ${publicationDate(node)} · ${node.branchTitle}`}
                    />
                  );
                })}
              </div>
            ) : null}
          </div>
          {results.length === 0 ? (
            <p className="m-0 p-8 text-center text-13 text-text-muted">
              No branches or papers match “{query.trim()}”.
            </p>
          ) : null}
        </div>

        <div className="box-content flex h-10 flex-none items-center gap-4 border-t border-hairline px-5 text-12 text-text-muted">
          <span className="flex items-center gap-1.5 max-[520px]:hidden">
            <kbd className={keycapClass}>↑↓</kbd> navigate
          </span>
          <span className="flex items-center gap-1.5 max-[520px]:hidden">
            <kbd className={keycapClass}>⏎</kbd> open
          </span>
          <span className="ml-auto">Searches this workspace only</span>
        </div>
      </div>
    </dialog>
  );
}

function optionId(index: number): string {
  return `search-result-${index}`;
}

function GroupLabel({ id, children }: { id: string; children: string }) {
  return (
    <div className={cx(kickerClass, "px-3 pt-3 pb-1")} id={id} role="presentation">
      {children}
    </div>
  );
}

function ResultRow({
  id,
  active,
  onHover,
  onSelect,
  icon,
  tint,
  title,
  subtitle,
  trailing,
}: {
  id: string;
  active: boolean;
  onHover: () => void;
  onSelect: () => void;
  icon: ReactNode;
  tint: string;
  title: string;
  subtitle?: string;
  trailing?: string;
}) {
  return (
    <div
      className={cx(
        "flex w-full cursor-default items-center gap-3 rounded-md px-3 py-2 text-left transition-[background-color] duration-100",
        active ? "bg-surface-subtle" : "bg-transparent",
      )}
      id={id}
      role="option"
      aria-selected={active}
      onMouseMove={onHover}
      onClick={onSelect}
    >
      <span
        className="grid h-7 w-7 flex-none place-items-center rounded-sm border border-hairline text-text-secondary"
        style={{ background: tint }}
        aria-hidden="true"
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-14 font-medium text-text-primary">{title}</span>
        {subtitle ? (
          <span className="block truncate text-12 text-text-muted">{subtitle}</span>
        ) : null}
      </span>
      {trailing ? <span className="flex-none text-12 text-text-muted">{trailing}</span> : null}
    </div>
  );
}

function branchText(node: BranchTreeNode): string {
  return `${node.title} ${node.description} ${node.whyItMatters} ${node.tags.join(" ")}`.toLowerCase();
}

function paperText(node: PaperTreeNode): string {
  return `${node.title} ${node.authors.join(" ")} ${node.tldr ?? ""} ${node.importance} ${node.branchTitle}`.toLowerCase();
}
