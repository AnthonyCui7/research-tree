import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { cx } from "../../lib/cx";
import { DROPDOWN_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { longDateLabel } from "../../lib/format";
import { authorLine, kickerClass, publicationDate } from "../tree/TreeNode";
import { PanelClose } from "../panel/RightPanel";

// The reader carries a PDF engine, which is most of what the app would ship.
// Loading it when a paper is opened keeps it out of the first page load.
const AnnotatedPaperReader = lazy(() =>
  import("../reader/AnnotatedPaperReader").then((module) => ({
    default: module.AnnotatedPaperReader,
  })),
);
import type {
  BranchPathViewModel,
  BranchTreeNode,
  PaperAnalysisEntry,
  PaperDetails,
  PaperReference,
  PaperTreeNode,
  ReadingOrderEntry,
  SimilarPaper,
  TreeNodeId,
  TreeNodeViewModel,
  TreeViewModel,
  WorkspaceScopeSummary,
} from "../../lib/types";

type NodeInspectorProps = {
  node: TreeNodeViewModel;
  tree: TreeViewModel;
  updatedAt: string | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onClose: () => void;
  /** Opens the workspace assistant; the reader's Assistant button uses it. */
  onOpenAssistant?: () => void;
};

export function NodeInspector({
  node,
  tree,
  updatedAt,
  onSelectNode,
  onClose,
  onOpenAssistant,
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
      <div
        className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[18px] pr-11 pb-7"
        key={node.id}
      >
        {node.kind === "root" ? (
          <RootView node={node} tree={tree} updatedAt={updatedAt} onSelectNode={onSelectNode} />
        ) : null}
        {node.kind === "branch" ? (
          <BranchView node={node} tree={tree} onSelectNode={onSelectNode} />
        ) : null}
        {node.kind === "paper" ? (
          <PaperView
            node={node}
            workspaceId={tree.workspaceId}
            onSelectNode={onSelectNode}
            onOpenAssistant={onOpenAssistant}
          />
        ) : null}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- paper --- */

function PaperView({
  node,
  workspaceId,
  onSelectNode,
  onOpenAssistant,
}: {
  node: PaperTreeNode;
  workspaceId: string;
  onSelectNode: (nodeId: TreeNodeId) => void;
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
      {node.whyReadHere ? <PathStep node={node} /> : null}
      {node.abstract ? <Section title="Abstract" body={node.abstract} /> : null}
      {node.analysis.length > 0 ? <Analysis entries={node.analysis} /> : null}
      <PaperReferences title="Read before" references={node.readBefore} onSelectNode={onSelectNode} />
      <PaperReferences title="Read after" references={node.readAfter} onSelectNode={onSelectNode} />
      <Chips label="Tags" values={node.secondaryTags} />
      {node.similarPapers.length > 0 ? <SimilarPapers papers={node.similarPapers} /> : null}
    </>
  );
}

/** Where the paper sits on its reading path, and the path's reason for it. */
function PathStep({ node }: { node: PaperTreeNode }) {
  const position = `Step ${node.readingIndex} of ${node.readingLength}`;
  return (
    <SectionShell title="Why read it here">
      <p className="mt-[7px] mb-0 text-[11.5px] text-text-muted">
        {node.pathLabel ? `${position} · ${node.pathLabel}` : position}
      </p>
      <p className="mt-1 mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary">
        {node.whyReadHere}
      </p>
    </SectionShell>
  );
}

function Analysis({ entries }: { entries: PaperAnalysisEntry[] }) {
  return (
    <SectionShell title="Analysis">
      <dl className="mt-2 mb-0 grid w-[min(100%,62ch)] gap-2.5">
        {entries.map((entry) => (
          <div key={entry.label}>
            <dt className={kickerClass}>{entry.label}</dt>
            <dd className="mt-0.5 ml-0 text-[13px] leading-[1.62] text-text-primary">{entry.body}</dd>
          </div>
        ))}
      </dl>
    </SectionShell>
  );
}

/** Papers the card names as prerequisites or follow-ups; a row links when the paper has a card. */
function PaperReferences({
  title,
  references,
  onSelectNode,
}: {
  title: string;
  references: PaperReference[];
  onSelectNode: (nodeId: TreeNodeId) => void;
}) {
  if (references.length === 0) {
    return null;
  }
  return (
    <SectionShell title={title}>
      <ul className="m-0 mt-1 list-none p-0">
        {references.map((reference) => (
          <li key={reference.paperId}>
            <ReferenceRow reference={reference} onSelectNode={onSelectNode} />
          </li>
        ))}
      </ul>
    </SectionShell>
  );
}

function ReferenceRow({
  reference,
  onSelectNode,
}: {
  reference: PaperReference;
  onSelectNode: (nodeId: TreeNodeId) => void;
}) {
  const nodeId = reference.nodeId;
  const className =
    "-mx-2.5 block w-[calc(100%+20px)] rounded-md px-2.5 py-[7px] text-left text-[12.5px] font-semibold leading-[1.4] text-text-primary [overflow-wrap:anywhere]";
  if (!nodeId) {
    return <span className={className}>{reference.title}</span>;
  }
  return (
    <button
      className={cx(className, "border-0 bg-transparent transition-[background-color] duration-150 hover:bg-surface-subtle")}
      type="button"
      onClick={() => onSelectNode(nodeId)}
    >
      {reference.title}
    </button>
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [readerOpen, setReaderOpen] = useState(false);
  const menu = useExitAnimation(menuOpen, DROPDOWN_EXIT_MS);
  const pdfUrl = paperPdfUrl(paper);

  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setMenuOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [menuOpen]);

  if (!paper.arxivLink && !paper.semanticScholarLink && !pdfUrl) {
    return null;
  }
  return (
    <div className="relative mt-3 flex flex-wrap gap-1.5">
      {paper.arxivLink ? <SourceLink href={paper.arxivLink}>arXiv ↗</SourceLink> : null}
      {paper.semanticScholarLink ? (
        <SourceLink href={paper.semanticScholarLink}>Semantic Scholar ↗</SourceLink>
      ) : null}
      {pdfUrl ? (
        <button
          className={cx(
            "rounded-[6px] border px-2.5 py-[5px] text-[11.5px] font-medium text-text-secondary transition-[background-color,border-color,color] duration-150 hover:border-accent hover:text-accent-deep",
            menuOpen ? "border-border-strong bg-surface-subtle" : "border-border bg-surface",
          )}
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
        >
          PDF
        </button>
      ) : null}
      {menu.present ? (
        <>
          <div
            className="fixed inset-0 z-dropdown"
            role="presentation"
            onMouseDown={() => setMenuOpen(false)}
          />
          <div
            className={cx(
              "absolute top-[calc(100%+6px)] right-0 z-menu w-[230px] origin-top-right overflow-hidden rounded-[9px] border border-border bg-surface shadow-menu",
              menu.closing ? "animate-dropdown-exit" : "animate-dropdown-enter",
            )}
            role="menu"
            aria-label="PDF options"
          >
            <PdfMenuItem href={pdfUrl} onClick={() => setMenuOpen(false)}>
              Download PDF
            </PdfMenuItem>
            <PdfMenuItem
              onClick={() => {
                setMenuOpen(false);
                setReaderOpen(true);
              }}
            >
              Open PDF <span className="text-text-muted">· inline annotations</span>
            </PdfMenuItem>
          </div>
        </>
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

function PdfMenuItem({
  children,
  href,
  onClick,
  disabled,
  title,
}: {
  children: React.ReactNode;
  href?: string | null;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  const className =
    "block w-full px-3 py-2 text-left text-xs text-text-primary no-underline transition-[background-color] duration-150";
  if (href && !disabled) {
    return (
      <a
        className={cx(className, "hover:bg-surface-subtle hover:no-underline")}
        href={href}
        target="_blank"
        rel="noreferrer"
        role="menuitem"
        onClick={onClick}
      >
        {children}
      </a>
    );
  }
  return (
    <button
      className={cx(
        className,
        "border-0 bg-transparent enabled:hover:bg-surface-subtle disabled:cursor-not-allowed disabled:text-text-muted",
      )}
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled || !onClick}
      title={title}
    >
      {children}
    </button>
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
          className={showAllButtonClass}
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
}: {
  node: BranchTreeNode;
  tree: TreeViewModel;
  onSelectNode: (nodeId: TreeNodeId) => void;
}) {
  const paths = useMemo(
    () =>
      node.paths.map((path) => ({
        path,
        papers: path.paperNodeIds.flatMap((nodeId) => {
          const paper = tree.nodesById[nodeId];
          return paper?.kind === "paper" ? [paper] : [];
        }),
      })),
    [tree, node.paths],
  );
  const paperCount = paths.reduce((total, entry) => total + entry.papers.length, 0);
  return (
    <>
      <h2 className="m-0 max-w-[40ch] text-[17px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary [overflow-wrap:anywhere]">
        {node.title}
      </h2>
      <p className="mt-1.5 mb-0 text-xs text-text-secondary">
        <b className="font-semibold text-text-primary">{paperCount}</b>{" "}
        {paperCount === 1 ? "paper" : "papers"}{" "}
        {paths.length > 1 ? `across ${paths.length} reading paths` : "in reading path"}
      </p>
      <Section title="Overview" body={node.description} />
      {node.whyItMatters ? <Section title="Why it matters" body={node.whyItMatters} /> : null}
      {node.anchorPaper ? <SurveyAnchor label="Branch survey" paper={node.anchorPaper} /> : null}
      {paths.map(({ path, papers }) =>
        papers.length > 0 ? (
          <ReadingPath key={path.pathId} path={path} papers={papers} onSelectNode={onSelectNode} />
        ) : null,
      )}
      <Chips label="Tags" values={node.tags} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/**
 * One reading path: what it covers, its papers in order, and why that order.
 * The heading is the path's own label, which is also the caption over its row
 * on the canvas.
 */
function ReadingPath({
  path,
  papers,
  onSelectNode,
}: {
  path: BranchPathViewModel;
  papers: PaperTreeNode[];
  onSelectNode: (nodeId: TreeNodeId) => void;
}) {
  return (
    <SectionShell title={path.label || "Reading path"}>
      {path.description ? (
        <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary">
          {path.description}
        </p>
      ) : null}
      <ol className="m-0 mt-2 flex list-none flex-col p-0">
        {papers.map((paper, index) => (
          <li key={paper.id}>
            <PaperRow
              index={index + 1}
              title={paper.title}
              meta={`${authorLine(paper.authors)} · ${publicationDate(paper)}`}
              onClick={() => onSelectNode(paper.id)}
            />
          </li>
        ))}
      </ol>
      {path.rationale ? (
        <p className="mt-2 mb-0 w-[min(100%,62ch)] text-[12.5px] leading-[1.6] text-text-secondary">
          <b className="mr-1 font-semibold text-text-primary">Why this order</b>
          {path.rationale}
        </p>
      ) : null}
    </SectionShell>
  );
}

/* ----------------------------------------------------------------- root --- */

function RootView({
  node,
  tree,
  updatedAt,
  onSelectNode,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
  tree: TreeViewModel;
  updatedAt: string | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
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
      {tree.scope ? <Scope scope={tree.scope} title={node.title} /> : null}
      {node.whyItMatters && node.whyItMatters !== node.overview ? (
        <Section title="Why it matters" body={node.whyItMatters} />
      ) : null}
      {/* The branches are the tree itself, a click away on the canvas; listing
          them again here only duplicates what is already on screen. */}
      {node.anchorPaper ? <SurveyAnchor label="Root survey" paper={node.anchorPaper} /> : null}
      {node.suggestedReadingDirection ? (
        <Section title="Where to start" body={node.suggestedReadingDirection} />
      ) : null}
      {tree.readingOrder.length > 0 ? (
        <ReadingOrder entries={tree.readingOrder} onSelectNode={onSelectNode} />
      ) : null}
      <Chips label="Key terms" values={node.keyTerms} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/** The boundary the build committed to: what the topic includes and leaves out. */
function Scope({ scope, title }: { scope: WorkspaceScopeSummary; title: string }) {
  const label = scope.label !== title ? scope.label : "";
  if (!label && !scope.rationale) {
    return null;
  }
  return (
    <SectionShell title="Scope">
      {label ? (
        <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.5] font-semibold text-text-primary">
          {label}
        </p>
      ) : null}
      {scope.rationale ? (
        <p
          className={cx(
            "mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary",
            label ? "mt-1" : "mt-[7px]",
          )}
        >
          {scope.rationale}
        </p>
      ) : null}
    </SectionShell>
  );
}

const READING_ORDER_PREVIEW = 6;

/** Every path's papers in one sequence, as the document orders them. */
function ReadingOrder({
  entries,
  onSelectNode,
}: {
  entries: ReadingOrderEntry[];
  onSelectNode: (nodeId: TreeNodeId) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? entries : entries.slice(0, READING_ORDER_PREVIEW);
  return (
    <SectionShell title="Reading order">
      <ol className="m-0 mt-2 flex list-none flex-col p-0">
        {visible.map((entry, index) => {
          const nodeId = entry.nodeId;
          return (
            <li key={`${entry.order}:${entry.paperId}`}>
              <PaperRow
                index={index + 1}
                title={entry.title}
                meta={readingOrderMeta(entry)}
                onClick={nodeId ? () => onSelectNode(nodeId) : undefined}
              />
            </li>
          );
        })}
      </ol>
      {entries.length > READING_ORDER_PREVIEW ? (
        <button
          className={showAllButtonClass}
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer" : `Show all ${entries.length}`}
        </button>
      ) : null}
    </SectionShell>
  );
}

function readingOrderMeta(entry: ReadingOrderEntry): string {
  const parts = [authorLine(entry.authors), publicationDate(entry)];
  if (entry.branchTitle) {
    parts.push(entry.branchTitle);
  }
  return parts.join(" · ");
}

/* -------------------------------------------------------------- shared --- */

const showAllButtonClass =
  "mt-3 w-full rounded-[7px] border border-border bg-surface px-3 py-2 text-center text-[11.5px] font-semibold text-text-primary transition-[background-color,border-color,color] duration-150 hover:border-accent hover:bg-surface-subtle hover:text-accent-deep";

/** A numbered paper in a list; a button when the paper has a card to go to. */
function PaperRow({
  index,
  title,
  meta,
  onClick,
}: {
  index: number;
  title: string;
  meta: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span
        className="mt-px grid h-5 w-5 flex-none place-items-center rounded-full bg-accent-subtle text-[10.5px] font-semibold text-accent-deep"
        aria-hidden="true"
      >
        {index}
      </span>
      <span className="min-w-0">
        <span className="block text-[12.5px] font-semibold leading-[1.4] text-text-primary">{title}</span>
        <span className="mt-0.5 block text-[11px] text-text-muted">{meta}</span>
      </span>
    </>
  );
  const rowClass = "-mx-2.5 flex w-[calc(100%+20px)] gap-[11px] rounded-md px-2.5 py-[9px] text-left";
  if (!onClick) {
    return <div className={rowClass}>{content}</div>;
  }
  return (
    <button
      className={cx(rowClass, "border-0 bg-transparent transition-[background-color] duration-150 hover:bg-surface-subtle")}
      type="button"
      onClick={onClick}
    >
      {content}
    </button>
  );
}

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

export function inspectorLabel(node: TreeNodeViewModel): string {
  if (node.kind === "root") return "Research topic details";
  if (node.kind === "branch") return "Research branch details";
  return "Paper details";
}
