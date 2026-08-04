import { useEffect, useMemo, useState } from "react";
import { branchTint } from "../../lib/familyTint";
import { cx } from "../../lib/cx";
import { DROPDOWN_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { longDateLabel, pluralize } from "../../lib/format";
import { authorLine, publicationDate } from "../tree/TreeNode";
import { PanelClose } from "../panel/RightPanel";
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
};

export function NodeInspector({ node, tree, updatedAt, onSelectNode, onClose }: NodeInspectorProps) {
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
        {node.kind === "paper" ? <PaperView node={node} /> : null}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- paper --- */

function PaperView({ node }: { node: PaperTreeNode }) {
  return (
    <>
      <h2 className="m-0 max-w-[40ch] text-[17px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary [overflow-wrap:anywhere]">
        {node.title}
      </h2>
      <p className="mt-[7px] mb-0 text-xs text-text-secondary">{paperMeta(node)}</p>
      <SourceLinks paper={node} />
      <Section title="TLDR" body={node.tldr || "Unavailable"} />
      {node.importance ? <Section title="Why it matters" body={node.importance} /> : null}
      {node.abstract ? <Section title="Abstract" body={node.abstract} /> : null}
      {node.similarPapers.length > 0 ? <SimilarPapers papers={node.similarPapers} /> : null}
    </>
  );
}

function SourceLinks({ paper }: { paper: PaperDetails }) {
  const [menuOpen, setMenuOpen] = useState(false);
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
            {/* Inline annotations need a backend that resolves and anchors every
                reference; until it lands the entry is shown but inert. */}
            <PdfMenuItem disabled title="Inline annotations are not available yet">
              Download PDF <span className="text-text-muted">· inline annotations</span>
            </PdfMenuItem>
          </div>
        </>
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
    <section className="mt-5 border-t border-hairline pt-4">
      <h3 className="m-0 text-[13px] font-bold text-text-primary">Similar papers</h3>
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
          className="mt-3 rounded-[6px] border border-border bg-surface px-3 py-1.5 text-[11px] font-semibold text-text-primary transition-[border-color,color] duration-150 hover:border-accent hover:text-accent-deep"
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer" : `Show all ${papers.length}`}
        </button>
      ) : null}
    </section>
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
  const readingPath = useMemo(() => papersForBranch(tree, node.branchNodeId), [tree, node.branchNodeId]);
  return (
    <>
      <h2 className="m-0 max-w-[40ch] text-[17px] font-semibold leading-[1.35] tracking-[-0.01em] text-text-primary [overflow-wrap:anywhere]">
        {node.title}
      </h2>
      <p className="mt-1.5 mb-0 text-xs text-text-secondary">
        {pluralize(readingPath.length, "paper")} in reading path
      </p>
      <LabelledSection label="Overview" body={node.description} />
      {node.whyItMatters ? <LabelledSection label="Why it matters" body={node.whyItMatters} /> : null}
      {node.anchorPaper ? <AnchorCard label="Branch survey" paper={node.anchorPaper} /> : null}
      {readingPath.length > 0 ? (
        <section className="mt-5">
          <SmallCaps>Reading path</SmallCaps>
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
        </section>
      ) : null}
      <Chips label="Tags" values={node.tags} />
      <Questions questions={node.openQuestions} />
    </>
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
  const branches = useMemo(
    () => tree.nodes.filter((entry): entry is BranchTreeNode => entry.kind === "branch"),
    [tree],
  );
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
      <LabelledSection label="Overview" body={node.overview} />
      {node.whyItMatters && node.whyItMatters !== node.overview ? (
        <LabelledSection label="Why it matters" body={node.whyItMatters} />
      ) : null}
      {node.anchorPaper ? <AnchorCard label="Field survey" paper={node.anchorPaper} /> : null}
      {branches.length > 0 ? (
        <section className="mt-5">
          <SmallCaps>Branches</SmallCaps>
          <div className="mt-1.5 flex flex-col">
            {branches.map((branch) => (
              <button
                className="-mx-2.5 flex w-[calc(100%+20px)] items-center gap-2.5 rounded-md border-0 bg-transparent px-2.5 py-[9px] text-left transition-[background-color] duration-150 hover:bg-surface-subtle"
                key={branch.id}
                type="button"
                onClick={() => onSelectNode(branch.id)}
              >
                <span
                  className="h-2.5 w-2.5 flex-none rounded-[3px] border border-node-border"
                  style={{ background: branchTint(branch.family) }}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 text-[12.5px] font-semibold leading-[1.4] text-text-primary">
                  {branch.title}
                </span>
                <span className="flex-none text-[11px] text-text-muted">{branch.paperCount}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {node.suggestedReadingDirection ? (
        <LabelledSection label="Where to start" body={node.suggestedReadingDirection} />
      ) : null}
      <Chips label="Key terms" values={node.keyTerms} />
      <Questions questions={node.openQuestions} />
    </>
  );
}

/* -------------------------------------------------------------- shared --- */

function Section({ title, body }: { title: string; body: string }) {
  return (
    <section className="mt-[18px] first-of-type:mt-5">
      <h3 className="m-0 text-[13px] font-bold text-text-primary">{title}</h3>
      <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary">
        {body}
      </p>
    </section>
  );
}

function LabelledSection({ label, body }: { label: string; body: string }) {
  return (
    <section className="mt-[18px]">
      <SmallCaps>{label}</SmallCaps>
      <p className="mt-[7px] mb-0 w-[min(100%,62ch)] text-[13px] leading-[1.62] text-text-primary">
        {body}
      </p>
    </section>
  );
}

function AnchorCard({ label, paper }: { label: string; paper: PaperDetails }) {
  return (
    <div className="mt-[18px] rounded-lg border border-border bg-surface px-3.5 py-3">
      <span className="text-[10px] font-semibold tracking-[0.06em] text-text-muted uppercase">
        {label}
      </span>
      <p className="mt-1 mb-0 text-[12.5px] font-semibold leading-[1.4] text-text-primary [overflow-wrap:anywhere]">
        {paper.title}
      </p>
      <p className="mt-0.5 mb-0 text-[11px] text-text-muted">
        {authorLine(paper.authors)} · {paper.year ?? "n.d."}
      </p>
      {paper.arxivLink || paper.semanticScholarLink ? (
        <div className="mt-2 flex flex-wrap gap-3">
          {paper.arxivLink ? <InlineLink href={paper.arxivLink}>arXiv ↗</InlineLink> : null}
          {paper.semanticScholarLink ? (
            <InlineLink href={paper.semanticScholarLink}>Semantic Scholar ↗</InlineLink>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Chips({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) {
    return null;
  }
  return (
    <section className="mt-5" aria-label={label}>
      <SmallCaps>{label}</SmallCaps>
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
    </section>
  );
}

function Questions({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <section className="mt-5">
      <SmallCaps>Open questions</SmallCaps>
      <ul className="m-0 mt-2 w-[min(100%,62ch)] list-disc pl-[18px] text-[13px] leading-[1.62] text-text-secondary marker:text-border-strong">
        {questions.map((question) => (
          <li className="mb-2 pl-1 last:mb-0" key={question}>
            {question}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SmallCaps({ children }: { children: string }) {
  return (
    <span className="block text-[10.5px] font-semibold tracking-[0.06em] text-text-muted uppercase">
      {children}
    </span>
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
