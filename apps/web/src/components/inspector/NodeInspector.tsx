import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { longDateLabel, pluralize } from "../../lib/format";
import {
  chipClass,
  compactActionClass,
  compactPrimaryActionClass,
  iconButtonClass,
  kickerClass,
  secondaryActionClass,
} from "../../lib/controlClasses";
import { authorLine, publicationDate } from "../tree/TreeNode";
import { PanelHeader } from "../panel/RightPanel";
import { BookIcon, DownloadIcon, EllipsisIcon, ExternalIcon } from "../ui/icons";
import type {
  BranchTreeNode,
  PaperDetails,
  PaperTreeNode,
  SimilarPaper,
  TreeNodeId,
  TreeNodeViewModel,
  TreeViewModel,
} from "../../lib/types";

// The reader carries a PDF engine, which is most of what the app would ship.
// Loading it when a paper is opened keeps it out of the first page load.
const AnnotatedPaperReader = lazy(() =>
  import("../reader/AnnotatedPaperReader").then((module) => ({
    default: module.AnnotatedPaperReader,
  })),
);

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

/** The details of the selected card: the topic, a branch, or a paper. */
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
    <>
      <PanelHeader
        title={<span className={kickerClass}>{KIND_LABEL[node.kind]}</span>}
        onClose={onClose}
        closeLabel="Close details"
        actions={
          node.kind !== "root" && onOpenActions ? (
            <button
              className={iconButtonClass}
              type="button"
              onClick={(event) => onOpenActions(event.currentTarget)}
              aria-label={node.kind === "branch" ? "Branch actions" : "Paper actions"}
              aria-haspopup="menu"
              title="Actions"
            >
              <EllipsisIcon className="h-4 w-4" />
            </button>
          ) : null
        }
      />
      <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-5 pb-10" key={node.id}>
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

const KIND_LABEL = { root: "Research topic", branch: "Research branch", paper: "Paper" } as const;

export function inspectorLabel(node: TreeNodeViewModel): string {
  return `${KIND_LABEL[node.kind]} details`;
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
  const [readerOpen, setReaderOpen] = useState(false);
  const pdfUrl = paperPdfUrl(node);
  const hasLinks = Boolean(pdfUrl || node.arxivLink || node.semanticScholarLink);

  return (
    <>
      <Title>{node.title}</Title>
      <AuthorList authors={node.authors} />
      <p className="mt-1 mb-0 text-[12.5px] leading-[1.5] text-text-muted">{paperFacts(node)}</p>

      {hasLinks ? (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          {pdfUrl ? (
            <button className={compactPrimaryActionClass} type="button" onClick={() => setReaderOpen(true)}>
              <BookIcon className="h-3.5 w-3.5" />
              Read with annotations
            </button>
          ) : null}
          {pdfUrl ? (
            <LinkButton href={pdfUrl} icon={<DownloadIcon className="h-3.5 w-3.5" />}>
              PDF
            </LinkButton>
          ) : null}
          {node.arxivLink ? (
            <LinkButton href={node.arxivLink} icon={<ExternalIcon className="h-3 w-3" />}>
              arXiv
            </LinkButton>
          ) : null}
          {node.semanticScholarLink ? (
            <LinkButton href={node.semanticScholarLink} icon={<ExternalIcon className="h-3 w-3" />}>
              Semantic Scholar
            </LinkButton>
          ) : null}
        </div>
      ) : null}

      <Section label="TLDR">
        <Prose>{node.tldr || "Unavailable"}</Prose>
      </Section>
      {node.importance ? (
        <Section label="Why it matters">
          <Prose>{node.importance}</Prose>
        </Section>
      ) : null}
      {node.abstract ? (
        <Section label="Abstract">
          <Abstract text={node.abstract} />
        </Section>
      ) : null}
      {node.similarPapers.length > 0 ? <SimilarPapers papers={node.similarPapers} /> : null}

      {readerOpen ? (
        <Suspense fallback={null}>
          <AnnotatedPaperReader
            workspaceId={workspaceId}
            paper={node}
            onClose={() => setReaderOpen(false)}
            onOpenAssistant={onOpenAssistant}
          />
        </Suspense>
      ) : null}
    </>
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

const SHOWN_AUTHORS = 5;

/** Every author up to a handful, then how many more, as a reader scans a byline. */
function AuthorList({ authors }: { authors: string[] }) {
  const names = authors.map((author) => author.trim()).filter(Boolean);
  if (names.length === 0) return null;
  const shown = names.slice(0, SHOWN_AUTHORS);
  const more = names.length - shown.length;
  return (
    <p className="mt-2 mb-0 text-[13px] leading-[1.5] text-text-secondary">
      {shown.join(", ")}
      {more > 0 ? `, and ${more} more` : null}
    </p>
  );
}

/** Abstracts run long; the first lines say whether the rest is worth reading. */
function Abstract({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > ABSTRACT_PREVIEW_CHARACTERS;
  return (
    <>
      <Prose className={cx(long && !expanded && "line-clamp-6")}>{text}</Prose>
      {long ? (
        <button
          className="mt-1.5 border-0 bg-transparent p-0 text-[12.5px] font-medium text-accent-deep hover:underline"
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </>
  );
}

/** About six lines of the panel's default width. */
const ABSTRACT_PREVIEW_CHARACTERS = 480;

const SIMILAR_PREVIEW = 3;

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? papers : papers.slice(0, SIMILAR_PREVIEW);
  return (
    <Section label="Similar papers">
      <ul className="m-0 grid list-none gap-2 p-0">
        {visible.map((paper) => (
          <li key={paper.paper_id}>
            <PaperReference
              title={paper.title}
              meta={`${authorLine(paper.authors ?? [])} · ${publicationDate({
                publicationDate: paper.publication_date ?? null,
                year: paper.year,
              })}`}
              arxivLink={paper.arxiv_link ?? null}
              semanticScholarLink={paper.s2_link ?? null}
            />
          </li>
        ))}
      </ul>
      {papers.length > SIMILAR_PREVIEW ? (
        <button
          className={cx(secondaryActionClass, "mt-2.5 h-8 w-full text-[12.5px]")}
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer" : `Show all ${papers.length}`}
        </button>
      ) : null}
    </Section>
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
        <Title>{node.title}</Title>
      )}
      <p className="mt-2 mb-0 text-[12.5px] text-text-muted">
        {pluralize(readingPath.length, "paper")} in its reading path
      </p>
      <Section label="Overview">
        <Prose>{node.description}</Prose>
      </Section>
      {node.whyItMatters ? (
        <Section label="Why it matters">
          <Prose>{node.whyItMatters}</Prose>
        </Section>
      ) : null}
      {node.anchorPaper ? <SurveyAnchor label="Branch survey" paper={node.anchorPaper} /> : null}
      {readingPath.length > 0 ? (
        <Section label="Reading path">
          <ol className="m-0 -mx-2 grid list-none gap-px p-0">
            {readingPath.map((paper, index) => (
              <li key={paper.id}>
                <button
                  className="flex w-full gap-3 rounded-lg border-0 bg-transparent px-2 py-2 text-left transition-[background-color] duration-150 hover:bg-surface-subtle"
                  type="button"
                  onClick={() => onSelectNode(paper.id)}
                >
                  <span
                    className="mt-px grid h-5 w-5 flex-none place-items-center rounded-full bg-accent-subtle text-[11px] font-semibold text-accent-deep tabular-nums"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13.5px] leading-[1.4] font-medium text-text-primary">
                      {paper.title}
                    </span>
                    <span className="mt-0.5 block text-[12px] text-text-muted">
                      {authorLine(paper.authors)} · {publicationDate(paper)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </Section>
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
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <input
        ref={inputRef}
        className="w-full rounded-md border border-accent bg-surface px-2.5 py-1.5 text-[17px] leading-[1.35] font-semibold tracking-[-0.01em] text-text-primary shadow-[0_0_0_3px_var(--color-accent-subtle)] outline-0"
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
      <Title large>{node.title}</Title>
      <p className="mt-2 mb-0 text-[12.5px] text-text-muted">
        {pluralize(tree.branchCount, "branch", "branches")} · {pluralize(tree.paperCount, "paper")}
        {updated ? ` · Updated ${updated}` : null}
      </p>
      <Section label="Overview">
        <Prose>{node.overview}</Prose>
      </Section>
      {node.whyItMatters && node.whyItMatters !== node.overview ? (
        <Section label="Why it matters">
          <Prose>{node.whyItMatters}</Prose>
        </Section>
      ) : null}
      {/* The branches are the tree itself, a click away on the canvas; listing
          them again here only duplicates what is already on screen. */}
      {node.anchorPaper ? <SurveyAnchor label="Root survey" paper={node.anchorPaper} /> : null}
      {node.suggestedReadingDirection ? (
        <Section label="Where to start">
          <Prose>{node.suggestedReadingDirection}</Prose>
        </Section>
      ) : null}
      <Chips label="Key terms" values={node.keyTerms} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/* -------------------------------------------------------------- shared --- */

function Title({ children, large = false }: { children: string; large?: boolean }) {
  return (
    <h3
      className={cx(
        "m-0 leading-[1.3] font-semibold tracking-[-0.015em] text-text-primary [overflow-wrap:anywhere]",
        large ? "text-[21px]" : "text-[18px]",
      )}
    >
      {children}
    </h3>
  );
}

/** Every panel — root, branch and paper — reads as the same run of labelled sections. */
function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h4 className={cx(kickerClass, "m-0 mb-2")}>{label}</h4>
      {children}
    </section>
  );
}

function Prose({ children, className }: { children: string; className?: string }) {
  return (
    <p className={cx("m-0 max-w-[68ch] text-[14px] leading-[1.65] text-text-primary", className)}>
      {children}
    </p>
  );
}

/** A paper named inside a panel: title, byline, and where to find it. */
function PaperReference({
  title,
  meta,
  arxivLink,
  semanticScholarLink,
}: {
  title: string;
  meta: string;
  arxivLink: string | null;
  semanticScholarLink: string | null;
}) {
  return (
    <div className="rounded-lg border border-hairline px-3.5 py-3">
      <p className="m-0 text-[13.5px] leading-[1.45] font-medium text-text-primary [overflow-wrap:anywhere]">
        {title}
      </p>
      <p className="mt-1 mb-0 text-[12px] text-text-muted">{meta}</p>
      {arxivLink || semanticScholarLink ? (
        <div className="mt-2 flex flex-wrap gap-3">
          {arxivLink ? <InlineLink href={arxivLink}>arXiv</InlineLink> : null}
          {semanticScholarLink ? (
            <InlineLink href={semanticScholarLink}>Semantic Scholar</InlineLink>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The survey that orients the topic or the branch, shown the way every other
 * paper in a panel is: a reference with its byline and links.
 */
function SurveyAnchor({ label, paper }: { label: string; paper: PaperDetails }) {
  return (
    <Section label={label}>
      <PaperReference
        title={paper.title}
        meta={`${authorLine(paper.authors)} · ${publicationDate(paper)}`}
        arxivLink={paper.arxivLink}
        semanticScholarLink={paper.semanticScholarLink}
      />
    </Section>
  );
}

function LinkButton({ href, icon, children }: { href: string; icon: ReactNode; children: string }) {
  return (
    <a className={cx(compactActionClass, "no-underline")} href={href} target="_blank" rel="noreferrer">
      {icon}
      {children}
    </a>
  );
}

function InlineLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      className="inline-flex items-center gap-1 text-[12px] font-medium text-accent-deep no-underline hover:underline"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {children}
      <ExternalIcon className="h-2.5 w-2.5" />
    </a>
  );
}

function Chips({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <Section label={label}>
      <div className="flex flex-wrap gap-1.5">
        {values.map((value) => (
          <span className={chipClass} key={value}>
            {value}
          </span>
        ))}
      </div>
    </Section>
  );
}

function Questions({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <Section label="Open questions">
      <ul className="m-0 max-w-[68ch] list-disc pl-[18px] text-[14px] leading-[1.65] text-text-primary marker:text-border-strong">
        {questions.map((question) => (
          <li className="mb-1.5 pl-1 last:mb-0" key={question}>
            {question}
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Byline facts after the authors: when, where, and how often it is cited. */
function paperFacts(paper: PaperTreeNode): string {
  const parts = [publicationDate(paper)];
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
