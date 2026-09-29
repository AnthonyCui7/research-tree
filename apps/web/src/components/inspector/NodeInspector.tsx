import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { longDateLabel, pluralize } from "../../lib/format";
import {
  chipClass,
  compactActionClass,
  compactPrimaryActionClass,
  errorNoticeClass,
  iconButtonClass,
  kickerClass,
  kickerTypeClass,
  primaryActionClass,
} from "../../lib/controlClasses";
import { authorLine, publicationDate } from "../tree/TreeNode";
import { PanelHeader } from "../panel/RightPanel";
import { ErrorBoundary } from "../ui/ErrorBoundary";
import {
  BookIcon,
  CalendarIcon,
  ChevronDownIcon,
  ClockIcon,
  DownloadIcon,
  EllipsisIcon,
  ExternalIcon,
  PaperIcon,
  QuoteIcon,
  TreeIcon,
  VenueIcon,
} from "../ui/icons";
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
  /** Whether that menu is open, which the button shows. */
  actionsOpen?: boolean;
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
  actionsOpen = false,
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
              aria-expanded={actionsOpen}
              title="Actions"
            >
              <EllipsisIcon className="size-4" />
            </button>
          ) : null
        }
      />
      <div
        className="scrollbar-rt min-h-0 flex-1 animate-settle overflow-y-auto px-6 pt-6 pb-12"
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
  const venue = node.venue && node.venue !== PREPRINT_VENUE ? node.venue : null;
  const hasLinks = Boolean(pdfUrl || node.arxivLink || node.semanticScholarLink);

  return (
    <>
      <Title>{node.title}</Title>
      <AuthorList authors={node.authors} />
      <Facts>
        {venue ? (
          <Fact icon={<VenueIcon className="size-4" />} wide>
            {venue}
          </Fact>
        ) : null}
        <Fact icon={<CalendarIcon className="size-4" />}>{publicationDate(node)}</Fact>
        {node.citationCount !== null ? (
          <Fact icon={<QuoteIcon className="size-4" />}>
            {compactCount(node.citationCount)} {node.citationCount === 1 ? "citation" : "citations"}
          </Fact>
        ) : null}
      </Facts>

      {hasLinks ? (
        <div className="mt-4 flex flex-wrap items-center gap-4">
          {pdfUrl ? (
            <button className={primaryActionClass} type="button" onClick={() => setReaderOpen(true)}>
              <BookIcon className="size-4" />
              Read with annotations
            </button>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {pdfUrl ? (
              <SourceLink href={pdfUrl} icon={<DownloadIcon className="size-4" />}>
                PDF
              </SourceLink>
            ) : null}
            {node.arxivLink ? (
              <SourceLink href={node.arxivLink} icon={<ExternalIcon className="size-4" />}>
                arXiv
              </SourceLink>
            ) : null}
            {node.semanticScholarLink ? (
              <SourceLink href={node.semanticScholarLink} icon={<ExternalIcon className="size-4" />}>
                Semantic Scholar
              </SourceLink>
            ) : null}
          </div>
        </div>
      ) : null}
      {/* The reader is a modal, so it can sit here, under the button that opens it,
          where its fallback is read if its code cannot be loaded. */}
      {readerOpen ? (
        <ErrorBoundary
          fallback={
            <p className={cx(errorNoticeClass, "mt-3 mb-0")} role="alert">
              The reader could not load. Reload the page to try again.
            </p>
          }
        >
          <Suspense fallback={null}>
            <AnnotatedPaperReader
              workspaceId={workspaceId}
              paper={node}
              onClose={() => setReaderOpen(false)}
              onOpenAssistant={onOpenAssistant}
            />
          </Suspense>
        </ErrorBoundary>
      ) : null}

      {node.tldr ? <Callout label="TLDR">{node.tldr}</Callout> : null}
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

/** What retrieval records as the venue of an arXiv preprint, which has none. */
const PREPRINT_VENUE = "N/A";

const SHOWN_AUTHORS = 5;

/** Every author up to a handful, then how many more, as a reader scans a byline. */
function AuthorList({ authors }: { authors: string[] }) {
  const names = authors.map((author) => author.trim()).filter(Boolean);
  if (names.length === 0) return null;
  const shown = names.slice(0, SHOWN_AUTHORS);
  const more = names.length - shown.length;
  return (
    <p className="mt-4 mb-0 text-14 text-text-secondary text-trim">
      {shown.join(", ")}
      {more > 0 ? <span className="text-text-muted">, and {more} more</span> : null}
    </p>
  );
}

/** Abstracts run long; the first lines say whether the rest is worth reading. */
function Abstract({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > ABSTRACT_PREVIEW_CHARACTERS;
  const folded = long && !expanded;
  return (
    <>
      <p
        className={cx(
          "m-0 max-w-[68ch] text-14 leading-6 text-text-primary text-trim",
          folded && "line-clamp-6 [mask-image:linear-gradient(to_bottom,black_60%,transparent)]",
        )}
      >
        {text}
      </p>
      {long ? (
        <button
          className="mt-2 flex items-center gap-1 border-0 bg-transparent p-0 text-13 leading-4 font-medium text-accent-deep hover:text-accent"
          type="button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
        >
          {expanded ? "Show less" : "Show the full abstract"}
          <ChevronDownIcon className={cx("size-4 transition-transform duration-200", expanded && "rotate-180")} />
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
    <Section label="Similar papers" count={papers.length}>
      <ul className="m-0 grid list-none gap-4 p-0">
        {visible.map((paper) => (
          <li key={paper.paper_id}>
            <Reference
              title={paper.title}
              byline={`${authorLine(paper.authors ?? [])} · ${publicationDate({
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
          className="mt-2 flex items-center gap-1 border-0 bg-transparent p-0 text-13 leading-4 font-medium text-accent-deep hover:text-accent"
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer" : `Show all ${papers.length}`}
          <ChevronDownIcon className={cx("size-4 transition-transform duration-200", showAll && "rotate-180")} />
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
      {/* A branch that groups other branches has no path of its own to count. */}
      {readingPath.length > 0 ? (
        <Facts>
          <Fact icon={<PaperIcon className="size-4" />}>
            {pluralize(readingPath.length, "paper")} in its reading path
          </Fact>
        </Facts>
      ) : null}
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
        <Section label="Reading path" count={readingPath.length}>
          {/* Each row's padding is pulled back out on every side, so its number
              sits on the column's edge and only the hover wash reaches past it. */}
          <ol className="-m-2 grid list-none p-0">
            {readingPath.map((paper, index) => (
              <li className="relative" key={paper.id}>
                {index < readingPath.length - 1 ? (
                  <span
                    className="absolute top-8 -bottom-1 left-[17.5px] w-px bg-accent-border"
                    aria-hidden="true"
                  />
                ) : null}
                <button
                  className="group relative flex w-full gap-3 rounded-md border-0 bg-transparent p-2 text-left transition-[background-color] duration-150 hover:bg-surface-subtle"
                  type="button"
                  onClick={() => onSelectNode(paper.id)}
                >
                  <span
                    className="grid h-5 w-5 flex-none place-items-center rounded-full border border-accent-border bg-accent-subtle text-11 font-semibold text-accent-deep tabular-nums transition-[background-color,color] duration-150 group-hover:bg-accent group-hover:text-white"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-13 font-medium text-text-primary">
                      {paper.title}
                    </span>
                    <span className="mt-1.5 block text-12 text-text-muted text-trim">
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
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <input
        ref={inputRef}
        className="w-full rounded-lg border border-accent bg-surface px-3 py-2 text-20 font-semibold text-text-primary shadow-[0_0_0_4px_var(--color-accent-subtle)] outline-0"
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
      <div className="flex items-center gap-2">
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
      <Facts>
        <Fact icon={<TreeIcon className="size-4" />}>
          {pluralize(tree.branchCount, "branch", "branches")}
        </Fact>
        <Fact icon={<PaperIcon className="size-4" />}>{pluralize(tree.paperCount, "paper")}</Fact>
        {updated ? <Fact icon={<ClockIcon className="size-4" />}>Updated {updated}</Fact> : null}
      </Facts>
      <Section label="Overview">
        <Prose>{node.overview}</Prose>
      </Section>
      {node.suggestedReadingDirection ? (
        <Callout label="Where to start">{node.suggestedReadingDirection}</Callout>
      ) : null}
      {node.whyItMatters && node.whyItMatters !== node.overview ? (
        <Section label="Why it matters">
          <Prose>{node.whyItMatters}</Prose>
        </Section>
      ) : null}
      {/* The branches are the tree itself, a click away on the canvas; listing
          them again here only duplicates what is already on screen. */}
      {node.anchorPaper ? <SurveyAnchor label="Root survey" paper={node.anchorPaper} /> : null}
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
        "m-0 font-semibold text-text-primary text-trim [overflow-wrap:anywhere]",
        large ? "text-24" : "text-20",
      )}
    >
      {children}
    </h3>
  );
}

/** The short facts under a title, each with the icon that says what it is. */
function Facts({ children }: { children: ReactNode }) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-13 leading-4 text-text-muted">
      {children}
    </div>
  );
}

function Fact({ icon, wide = false, children }: { icon: ReactNode; wide?: boolean; children: ReactNode }) {
  return (
    <span className={cx("inline-flex min-w-0 items-start gap-1.5", wide && "basis-full")}>
      <span className="flex-none text-text-muted/80">{icon}</span>
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </span>
  );
}

/** Every panel — root, branch and paper — reads as the same run of labelled sections. */
function Section({ label, count, children }: { label: string; count?: number; children: ReactNode }) {
  return (
    <section className="mt-8">
      <h4 className={cx(kickerClass, "m-0 mb-4 flex items-center gap-2")}>
        <span className="text-trim">{label}</span>
        {count !== undefined ? (
          <span className="tracking-normal text-text-secondary tabular-nums text-trim">{count}</span>
        ) : null}
      </h4>
      {children}
    </section>
  );
}

/**
 * The one passage a panel leads with: a paper's TLDR, or where to start in a
 * topic. Set apart the way the canvas sets the root apart, with the accent wash.
 */
function Callout({ label, children }: { label: string; children: string }) {
  return (
    <section className="mt-8 rounded-xl border border-accent-border/70 bg-accent-wash p-4">
      <h4 className={cx(kickerTypeClass, "m-0 mb-4 text-accent-deep text-trim")}>{label}</h4>
      <p className="m-0 max-w-[68ch] text-14 leading-6 text-text-primary text-trim">{children}</p>
    </section>
  );
}

function Prose({ children }: { children: string }) {
  return <p className="m-0 max-w-[68ch] text-14 leading-6 text-text-primary text-trim">{children}</p>;
}

/**
 * The survey that orients the topic or the branch, shown the way every other
 * paper in a panel is: a reference with its byline and links.
 */
function SurveyAnchor({ label, paper }: { label: string; paper: PaperDetails }) {
  return (
    <Section label={label}>
      <div className="rounded-xl border border-hairline p-4">
        <Reference
          title={paper.title}
          byline={`${authorLine(paper.authors)} · ${publicationDate(paper)}`}
          arxivLink={paper.arxivLink}
          semanticScholarLink={paper.semanticScholarLink}
        />
      </div>
    </Section>
  );
}

/** Where else the paper lives: quiet next to the one action the panel offers. */
function SourceLink({ href, icon, children }: { href: string; icon: ReactNode; children: string }) {
  return (
    <a
      className="-my-1 inline-flex items-center gap-1.5 py-1 text-13 leading-4 font-medium text-text-secondary no-underline transition-[color] duration-150 hover:text-accent-deep"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {icon}
      {children}
    </a>
  );
}

/**
 * A paper cited in a panel: its title, then who and when, and where to read
 * it. Both lines are trimmed to their letters, so the space around a reference
 * is the space between the text itself.
 */
function Reference({
  title,
  byline,
  arxivLink,
  semanticScholarLink,
}: {
  title: string;
  byline: string;
  arxivLink: string | null;
  semanticScholarLink: string | null;
}) {
  return (
    <>
      <p className="m-0 text-13 font-medium text-text-primary text-trim [overflow-wrap:anywhere]">{title}</p>
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-12 text-text-muted">
        <span className="text-trim">{byline}</span>
        {arxivLink ? <InlineLink href={arxivLink}>arXiv</InlineLink> : null}
        {semanticScholarLink ? <InlineLink href={semanticScholarLink}>Semantic Scholar</InlineLink> : null}
      </div>
    </>
  );
}

function InlineLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      className="font-medium text-accent-deep no-underline text-trim hover:text-accent hover:underline"
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {children}
      <ExternalIcon className="ml-0.5 inline-block size-3 align-middle" />
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
    <Section label="Open questions" count={questions.length}>
      <ul className="m-0 grid max-w-[68ch] list-none gap-6 p-0">
        {questions.map((question) => (
          <li className="flex gap-3 text-14 leading-6 text-text-primary" key={question}>
            {/* Centred on the capitals of the first line. */}
            <span className="mt-0.5 h-1.5 w-1.5 flex-none rounded-full bg-accent-border" aria-hidden="true" />
            <span className="min-w-0 text-trim">{question}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
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
