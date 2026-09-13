import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { cx } from "../../lib/cx";
import { longDateLabel } from "../../lib/format";
import { compactActionClass, compactPrimaryActionClass } from "../../lib/controlClasses";
import { authorLine, publicationDate } from "../tree/TreeNode";
import { PanelClose } from "../panel/RightPanel";
import { EllipsisIcon } from "../ui/icons";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";

// The reader carries a PDF engine, which is most of what the app would ship.
// Loading it when a paper is opened keeps it out of the first page load.
const AnnotatedPaperReader = lazy(() =>
  import("../reader/AnnotatedPaperReader").then((module) => ({
    default: module.AnnotatedPaperReader,
  })),
);
import type {
  BranchTreeNode,
  PaperDetails,
  PaperTreeNode,
  SimilarPaper,
  TreeNodeId,
  TreeNodeViewModel,
  TreeViewModel,
} from "../../lib/types";

type NodeInspectorProps = {
  node: TreeNodeViewModel;
  tree: TreeViewModel;
  updatedAt: string | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onClose: () => void;
  /** Opens the workspace assistant; the reader's Assistant button uses it. */
  onOpenAssistant?: () => void;
  /** Opens the card's actions menu from the button beside the close control. */
  onOpenActions?: (trigger: HTMLElement) => void;
  /** The branch heading is an input while true. */
  renaming?: boolean;
  onRenameSubmit?: (label: string) => Promise<void>;
  onRenameCancel?: () => void;
};

export function NodeInspector({
  node,
  tree,
  updatedAt,
  onSelectNode,
  onClose,
  onOpenAssistant,
  onOpenActions,
  renaming = false,
  onRenameSubmit,
  onRenameCancel,
}: NodeInspectorProps) {
  return (
    // Node details carry their own heading, so there is no panel title to show.
    // Rather than keep a bar that would sit empty, the close control rests over
    // the top-right of the content — pinned to the panel, so it survives the
    // scroll, and on the panel's own surface so text passing under it stays
    // legible.
    <>
      <PanelClose
        className="absolute top-3.5 right-3.5 z-[2] bg-surface"
        onClose={onClose}
        label="Close details"
      />
      {node.kind !== "root" && onOpenActions ? (
        <button
          className="absolute top-3.5 right-[46px] z-[2] grid h-[26px] w-[26px] place-items-center rounded-[6px] border-0 bg-surface p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
          type="button"
          onClick={(event) => onOpenActions(event.currentTarget)}
          aria-label={node.kind === "branch" ? "Branch actions" : "Paper actions"}
          aria-haspopup="menu"
          title="Actions"
        >
          <EllipsisIcon className="h-3.5 w-3.5" />
        </button>
      ) : null}
      <div
        className={cx(
          "scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[18px] pb-7",
          node.kind !== "root" && onOpenActions ? "pr-[76px]" : "pr-11",
        )}
        key={node.id}
      >
        {node.kind === "root" ? <RootView node={node} tree={tree} updatedAt={updatedAt} /> : null}
        {node.kind === "branch" ? (
          <BranchView
            node={node}
            tree={tree}
            onSelectNode={onSelectNode}
            renaming={renaming}
            onRenameSubmit={onRenameSubmit}
            onRenameCancel={onRenameCancel}
          />
        ) : null}
        {node.kind === "paper" ? (
          <PaperView node={node} workspaceId={tree.workspaceId} onOpenAssistant={onOpenAssistant} />
        ) : null}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- paper --- */

function PaperView({
  node,
  workspaceId,
  onOpenAssistant,
}: {
  node: PaperTreeNode;
  workspaceId: string;
  onOpenAssistant?: () => void;
}) {
  return (
    <>
      <h2 className="m-0 max-w-[40ch] text-[17px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary [overflow-wrap:anywhere]">
        {node.title}
      </h2>
      <p className="mt-[7px] mb-0 text-xs text-text-secondary">{paperMeta(node)}</p>
      <SourceLinks paper={node} workspaceId={workspaceId} onOpenAssistant={onOpenAssistant} />
      <Section title="TLDR" body={node.tldr || "Unavailable"} />
      {node.importance ? <Section title="Why it matters" body={node.importance} /> : null}
      {node.abstract ? <Section title="Abstract" body={node.abstract} /> : null}
      {node.similarPapers.length > 0 ? <SimilarPapers papers={node.similarPapers} /> : null}
    </>
  );
}

function SourceLinks({
  paper,
  workspaceId,
  onOpenAssistant,
}: {
  paper: PaperDetails;
  workspaceId: string;
  onOpenAssistant?: () => void;
}) {
  const [menuAnchor, setMenuAnchor] = useState<MenuAnchor | null>(null);
  const [readerOpen, setReaderOpen] = useState(false);
  const pdfUrl = paperPdfUrl(paper);

  if (!paper.arxivLink && !paper.semanticScholarLink && !pdfUrl) {
    return null;
  }
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {paper.arxivLink ? <SourceLink href={paper.arxivLink}>arXiv ↗</SourceLink> : null}
      {paper.semanticScholarLink ? (
        <SourceLink href={paper.semanticScholarLink}>Semantic Scholar ↗</SourceLink>
      ) : null}
      {pdfUrl ? (
        <button
          className={cx(
            "rounded-[6px] border px-2.5 py-[5px] text-[11.5px] font-medium text-text-secondary transition-[background-color,border-color,color] duration-150 hover:border-accent hover:text-accent-deep",
            menuAnchor ? "border-border-strong bg-surface-subtle" : "border-border bg-surface",
          )}
          type="button"
          onClick={(event) => setMenuAnchor(anchorFromEvent(event.currentTarget, "right"))}
          aria-expanded={menuAnchor !== null}
          aria-haspopup="menu"
        >
          PDF
        </button>
      ) : null}
      {menuAnchor && pdfUrl ? (
        <PopoverMenu
          anchor={menuAnchor}
          onClose={() => setMenuAnchor(null)}
          label="PDF options"
          width={230}
        >
          <MenuSection>
            <MenuItem href={pdfUrl}>Download PDF</MenuItem>
            <MenuItem onClick={() => setReaderOpen(true)}>
              Open PDF <span className="text-text-muted">· inline annotations</span>
            </MenuItem>
          </MenuSection>
        </PopoverMenu>
      ) : null}
      {readerOpen ? (
        <Suspense fallback={null}>
          <AnnotatedPaperReader
            workspaceId={workspaceId}
            paper={paper}
            onClose={() => setReaderOpen(false)}
            onOpenAssistant={onOpenAssistant}
          />
        </Suspense>
      ) : null}
    </div>
  );
}

/** The PDF enrichment already resolved, falling back to arXiv's own PDF path. */
export function paperPdfUrl(paper: PaperDetails): string | null {
  const resolved = paper.content?.sourceUrl;
  if (resolved) {
    return resolved;
  }
  const abstractUrl = paper.arxivLink;
  if (abstractUrl?.includes("arxiv.org/abs/")) {
    return abstractUrl.replace("/abs/", "/pdf/");
  }
  return null;
}

function SourceLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      className="rounded-[6px] border border-border bg-surface px-2.5 py-[5px] text-[11.5px] font-medium text-text-secondary no-underline transition-[border-color,color] duration-150 hover:border-accent hover:text-accent-deep hover:no-underline"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {children}
    </a>
  );
}

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? papers : papers.slice(0, 3);
  return (
    <SectionShell title="Similar papers">
      <div className="mt-0.5 flex flex-col">
        {visible.map((paper) => (
          <article className="border-b border-hairline-soft py-[11px]" key={paper.paper_id}>
            <p className="m-0 text-[12.5px] font-semibold leading-[1.45] text-text-primary [overflow-wrap:anywhere]">
              {paper.title}
            </p>
            <p className="mt-[3px] mb-0 text-[11px] text-text-muted">
              {authorLine(paper.authors ?? [])} ·{" "}
              {publicationDate({ publicationDate: paper.publication_date ?? null, year: paper.year })}
            </p>
            {paper.arxiv_link || paper.s2_link ? (
              <div className="mt-[5px] flex flex-wrap gap-3">
                {paper.arxiv_link ? <InlineLink href={paper.arxiv_link}>arXiv ↗</InlineLink> : null}
                {paper.s2_link ? (
                  <InlineLink href={paper.s2_link}>Semantic Scholar ↗</InlineLink>
                ) : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {papers.length > 3 ? (
        <button
          className="mt-3 w-full rounded-[7px] border border-border bg-surface px-3 py-2 text-center text-[11.5px] font-semibold text-text-primary transition-[background-color,border-color,color] duration-150 hover:border-accent hover:bg-surface-subtle hover:text-accent-deep"
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer" : `Show all ${papers.length}`}
        </button>
      ) : null}
    </SectionShell>
  );
}

function InlineLink({ href, children }: { href: string; children: string }) {
  return (
    <a className="text-[11px] text-accent no-underline hover:text-accent-deep hover:underline" href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

/* --------------------------------------------------------------- branch --- */

function BranchView({
  node,
  tree,
  onSelectNode,
  renaming,
  onRenameSubmit,
  onRenameCancel,
}: {
  node: BranchTreeNode;
  tree: TreeViewModel;
  onSelectNode: (nodeId: TreeNodeId) => void;
  renaming: boolean;
  onRenameSubmit?: (label: string) => Promise<void>;
  onRenameCancel?: () => void;
}) {
  const readingPath = useMemo(() => papersForBranch(tree, node.branchNodeId), [tree, node.branchNodeId]);
  return (
    <>
      {renaming && onRenameSubmit && onRenameCancel ? (
        <RenameForm title={node.title} onSubmit={onRenameSubmit} onCancel={onRenameCancel} />
      ) : (
        <h2 className="m-0 max-w-[40ch] text-[17px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary [overflow-wrap:anywhere]">
          {node.title}
        </h2>
      )}
      <p className="mt-1.5 mb-0 text-xs text-text-secondary">
        <b className="font-semibold text-text-primary">{readingPath.length}</b>{" "}
        {readingPath.length === 1 ? "paper" : "papers"} in reading path
      </p>
      <Section title="Overview" body={node.description} />
      {node.whyItMatters ? <Section title="Why it matters" body={node.whyItMatters} /> : null}
      {node.anchorPaper ? <SurveyAnchor label="Branch survey" paper={node.anchorPaper} /> : null}
      {readingPath.length > 0 ? (
        <SectionShell title="Reading path">
          <ol className="m-0 mt-2 flex list-none flex-col p-0">
            {readingPath.map((paper, index) => (
              <li key={paper.id}>
                <button
                  className="-mx-2.5 flex w-[calc(100%+20px)] gap-[11px] rounded-md border-0 bg-transparent px-2.5 py-[9px] text-left transition-[background-color] duration-150 hover:bg-surface-subtle"
                  type="button"
                  onClick={() => onSelectNode(paper.id)}
                >
                  <span
                    className="mt-px grid h-5 w-5 flex-none place-items-center rounded-full bg-accent-subtle text-[10.5px] font-semibold text-accent-deep"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[12.5px] font-semibold leading-[1.4] text-text-primary">
                      {paper.title}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-text-muted">
                      {authorLine(paper.authors)} · {publicationDate(paper)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </SectionShell>
      ) : null}
      <Chips label="Tags" values={node.tags} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/**
 * The branch heading as an input. Enter saves, Escape cancels; a label left
 * unchanged or emptied sends nothing.
 */
function RenameForm({
  title,
  onSubmit,
  onCancel,
}: {
  title: string;
  onSubmit: (label: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(title);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const trimmed = label.trim();
  const unchanged = !trimmed || trimmed === title;

  async function submit() {
    if (unchanged) {
      onCancel();
      return;
    }
    setBusy(true);
    try {
      await onSubmit(trimmed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <input
        ref={inputRef}
        className="w-full rounded-[7px] border-[1.5px] border-accent bg-surface px-2.5 py-1.5 text-[16px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary outline-0 shadow-[0_0_0_3px_var(--color-accent-subtle)]"
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onCancel();
          }
        }}
        aria-label="Branch name"
        maxLength={200}
        disabled={busy}
      />
      <div className="flex items-center gap-1.5">
        <button className={compactPrimaryActionClass} type="submit" disabled={busy || unchanged}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button className={compactActionClass} type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/* ----------------------------------------------------------------- root --- */

function RootView({
  node,
  tree,
  updatedAt,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
  tree: TreeViewModel;
  updatedAt: string | null;
}) {
  const updated = longDateLabel(updatedAt);
  return (
    <>
      <h2 className="m-0 text-[19px] font-bold tracking-[-0.015em] text-text-primary [overflow-wrap:anywhere]">
        {node.title}
      </h2>
      <p className="mt-2.5 mb-0 flex flex-wrap gap-3.5 text-xs text-text-secondary">
        <span>
          <b className="font-semibold text-text-primary">{tree.branchCount}</b> branches
        </span>
        <span>
          <b className="font-semibold text-text-primary">{tree.paperCount}</b> papers
        </span>
        {updated ? <span>updated {updated}</span> : null}
      </p>
      <Section title="Overview" body={node.overview} />
      {node.whyItMatters && node.whyItMatters !== node.overview ? (
        <Section title="Why it matters" body={node.whyItMatters} />
      ) : null}
      {/* The branches are the tree itself, a click away on the canvas; listing
          them again here only duplicates what is already on screen. */}
      {node.anchorPaper ? <SurveyAnchor label="Root survey" paper={node.anchorPaper} /> : null}
      {node.suggestedReadingDirection ? (
        <Section title="Where to start" body={node.suggestedReadingDirection} />
      ) : null}
      <Chips label="Key terms" values={node.keyTerms} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/* -------------------------------------------------------------- shared --- */

/** Every panel — root, branch and paper — reads as the same run of sections. */
function Section({ title, body }: { title: string; body: string }) {
  return (
    <SectionShell title={title}>
      <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary">
        {body}
      </p>
    </SectionShell>
  );
}

function SectionShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-[18px]">
      <h3 className="m-0 text-[13px] font-bold text-text-primary">{title}</h3>
      {children}
    </section>
  );
}

/**
 * The survey that orients the topic or the branch. It is one more section of the
 * panel, written the same way as the reading path and similar papers, rather
 * than a boxed aside floating in the middle of them.
 */
function SurveyAnchor({ label, paper }: { label: string; paper: PaperDetails }) {
  return (
    <SectionShell title={label}>
      <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.5] font-semibold text-text-primary [overflow-wrap:anywhere]">
        {paper.title}
      </p>
      <p className="mt-[3px] mb-0 text-[11.5px] text-text-muted">
        {authorLine(paper.authors)} · {publicationDate(paper)}
      </p>
      {paper.arxivLink || paper.semanticScholarLink ? (
        <div className="mt-[7px] flex flex-wrap gap-3">
          {paper.arxivLink ? <InlineLink href={paper.arxivLink}>arXiv ↗</InlineLink> : null}
          {paper.semanticScholarLink ? (
            <InlineLink href={paper.semanticScholarLink}>Semantic Scholar ↗</InlineLink>
          ) : null}
        </div>
      ) : null}
    </SectionShell>
  );
}

function Chips({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <SectionShell title={label}>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {values.map((value) => (
          <span
            className="rounded-[20px] bg-surface-subtle px-2.5 py-[3px] text-[11.5px] text-text-secondary"
            key={value}
          >
            {value}
          </span>
        ))}
      </div>
    </SectionShell>
  );
}

function Questions({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <SectionShell title="Open questions">
      <ul className="m-0 mt-2 w-[min(100%,62ch)] list-disc pl-[18px] text-[13px] leading-[1.62] text-text-secondary marker:text-border-strong">
        {questions.map((question) => (
          <li className="mb-2 pl-1 last:mb-0" key={question}>
            {question}
          </li>
        ))}
      </ul>
    </SectionShell>
  );
}

function paperMeta(paper: PaperTreeNode): string {
  const parts = [authorLine(paper.authors), publicationDate(paper)];
  if (paper.venue) {
    parts.push(paper.venue);
  }
  if (paper.citationCount !== null) {
    parts.push(`${compactCount(paper.citationCount)} citations`);
  }
  return parts.join(" · ");
}

function compactCount(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function papersForBranch(tree: TreeViewModel, branchNodeId: string): PaperTreeNode[] {
  return tree.nodes
    .filter((node): node is PaperTreeNode => node.kind === "paper" && node.branchId === branchNodeId)
    .sort(
      (left, right) => left.position.y - right.position.y || left.readingIndex - right.readingIndex,
    );
}

export function inspectorLabel(node: TreeNodeViewModel): string {
  if (node.kind === "root") return "Research topic details";
  if (node.kind === "branch") return "Research branch details";
  return "Paper details";
}
