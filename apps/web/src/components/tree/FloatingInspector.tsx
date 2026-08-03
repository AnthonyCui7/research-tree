import { useEffect, useState, type CSSProperties } from "react";
import type { PaperDetails, SimilarPaper, TreeNodeViewModel } from "../../lib/types";
import { authorLine, publicationDate } from "./TreeNode";
import { cx } from "../../lib/cx";
import {
  beginPanelResize,
  clampResizablePanelWidth,
  resizablePanelMaxWidth,
  useViewportWidth,
} from "../../lib/resizablePanel";

type FloatingInspectorProps = {
  node: TreeNodeViewModel | null;
  onClose: () => void;
  sidebarCollapsed: boolean;
};

const INSPECTOR_MIN_WIDTH = 420;

export function FloatingInspector({ node, onClose, sidebarCollapsed }: FloatingInspectorProps) {
  const [visibleNode, setVisibleNode] = useState<TreeNodeViewModel | null>(node);
  const [isClosing, setIsClosing] = useState(false);
  const [panelWidth, setPanelWidth] = useState(510);
  const viewportWidth = useViewportWidth();
  const maxWidth = resizablePanelMaxWidth(sidebarCollapsed, viewportWidth);

  useEffect(() => {
    if (node) {
      setVisibleNode(node);
      setIsClosing(false);
      return;
    }
    if (visibleNode) {
      setIsClosing(true);
    }
  }, [node, visibleNode]);

  useEffect(() => {
    if (!node) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [node, onClose]);

  if (!visibleNode) {
    return null;
  }
  const sourcePaper = visibleNode.kind === "paper" ? visibleNode : visibleNode.anchorPaper;

  return (
    <aside
      key={visibleNode.id}
      className="fixed top-16 right-0 bottom-0 z-panel flex h-auto w-[min(var(--inspector-panel-width,510px),var(--inspector-panel-max-width,calc(100%_-_80px)))] animate-interface-right-enter flex-col overflow-visible border-l border-border bg-surface px-[26px] pt-6 pb-[22px] shadow-panel-left data-[state=closing]:pointer-events-none data-[state=closing]:animate-interface-right-exit max-[980px]:top-auto max-[980px]:left-[68px] max-[980px]:h-[min(72vh,680px)] max-[980px]:w-auto max-[980px]:border-l-0 max-[980px]:border-t max-[720px]:left-0 max-[720px]:h-[min(80vh,720px)]"
      data-state={isClosing ? "closing" : "open"}
      aria-label="Selected node details"
      style={{
        "--inspector-panel-width": `${clampResizablePanelWidth(panelWidth, INSPECTOR_MIN_WIDTH, maxWidth)}px`,
        "--inspector-panel-max-width": `${maxWidth}px`,
      } as CSSProperties}
      onAnimationEnd={(event) => {
        if (isClosing && event.target === event.currentTarget) {
          setVisibleNode(null);
        }
      }}
    >
      <button className="group absolute top-1/2 -left-1 z-[1] grid h-12 w-2 -translate-y-1/2 cursor-ew-resize touch-none place-items-center rounded-full border border-border bg-surface p-0 shadow-control transition-[background-color,border-color,box-shadow,transform] duration-150 hover:border-accent hover:shadow-[0_8px_18px_rgb(31_35_40_/_14%)] focus-visible:border-accent focus-visible:shadow-[0_8px_18px_rgb(31_35_40_/_14%)] max-[980px]:hidden" type="button" onPointerDown={(event) => beginPanelResize(event, { startWidth: panelWidth, minWidth: INSPECTOR_MIN_WIDTH, maxWidth, onWidth: setPanelWidth })} aria-label="Resize details panel"><span className="relative block h-[42px] w-1.5 rounded-full bg-[color-mix(in_srgb,var(--color-surface-subtle)_60%,var(--color-surface))] transition-[background-color,transform] duration-150 before:absolute before:top-2.5 before:bottom-2.5 before:left-1/2 before:block before:w-px before:-translate-x-1/2 before:bg-text-secondary before:opacity-80 before:content-[''] group-hover:bg-accent-subtle group-focus-visible:bg-accent-subtle" aria-hidden="true" /></button>
      <div className="flex items-center justify-between gap-4 border-b border-border pb-[18px]">
        <div className="min-w-0">
          <span className="text-[11px] font-semibold leading-[1.35] text-text-secondary">
            {headingLabel(visibleNode.kind)}
          </span>
          <h2 className="mt-1 mb-0 w-[min(100%,40ch)] max-w-[40ch] text-[23px] font-bold leading-[1.2] tracking-normal text-balance text-text-primary [overflow-wrap:anywhere]">{visibleNode.title}</h2>
        </div>
        <button className="grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:bg-surface-subtle enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px] max-[720px]:h-10 max-[720px]:w-10" type="button" onClick={onClose} aria-label="Close details" title="Close">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
        </button>
      </div>

      <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto">
        {visibleNode.kind === "root" ? (
          <RootLearningDetails node={visibleNode} />
        ) : null}

        {visibleNode.kind === "branch" ? (
          <BranchLearningDetails node={visibleNode} />
        ) : null}

        {visibleNode.kind === "paper" ? <PaperLearningDetails paper={visibleNode} /> : null}
      </div>

      {sourcePaper ? <PaperSourceActions paper={sourcePaper} /> : null}
    </aside>
  );
}


function RootLearningDetails({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
}) {
  return (
    <div className="py-[22px] pb-[26px]">
      <NodeTextSection label="Overview" value={node.overview} />
      <NodeTextSection label="Why it matters" value={node.whyItMatters} />
      <PaperReferenceDetails paper={node.anchorPaper} showTitle />
      <NodeTextSection label="Where to start" value={node.suggestedReadingDirection} />
      <TagList tags={node.keyTerms} label="Key terms" />
      <QuestionList questions={node.openQuestions} />
    </div>
  );
}

function BranchLearningDetails({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "branch" }>;
}) {
  return (
    <div className="py-[22px] pb-[26px]">
      <NodeTextSection label="Overview" value={node.description} />
      <NodeTextSection label="Why it matters" value={node.whyItMatters} />
      <PaperReferenceDetails paper={node.anchorPaper} showTitle />
      <TagList tags={node.tags} label="Tags" />
      <QuestionList questions={node.openQuestions} />
    </div>
  );
}

function PaperLearningDetails({ paper }: { paper: PaperDetails }) {
  return (
    <div className="py-[22px] pb-[26px]">
      <p className="m-0 w-[min(100%,60ch)] text-xs leading-normal text-text-secondary">{fullAuthorList(paper.authors)}</p>
      <PaperFacts paper={paper} />
      <LearningSection label="Summary" value={paper.tldr || "Unavailable"} />
      <LearningSection label="Why it matters" value={paper.importance || "Unavailable"} />
      {paper.abstract ? <AbstractSection abstract={paper.abstract} /> : null}
      {paper.similarPapers.length > 0 ? <SimilarPapers papers={paper.similarPapers} /> : null}
    </div>
  );
}

function PaperReferenceDetails({
  paper,
  showTitle = false,
}: {
  paper: PaperDetails | null;
  showTitle?: boolean;
}) {
  if (!paper) {
    return null;
  }
  return (
    <div className="mt-[18px] border-t border-border pt-[18px]">
      {showTitle ? (
        <div className="grid gap-1.5 pb-3">
          <span className={metadataLabelClass}>Survey anchor</span>
          <h3 className="m-0 text-base leading-[1.35] text-text-primary [overflow-wrap:anywhere]">{paper.title}</h3>
        </div>
      ) : null}
      <p className="m-0 w-[min(100%,60ch)] text-xs leading-normal text-text-secondary">{fullAuthorList(paper.authors)}</p>
      <PaperFacts paper={paper} />
      <LearningSection label="Summary" value={paper.tldr || "Unavailable"} />
      <LearningSection label="Why it matters" value={paper.importance || "Unavailable"} />
      {paper.abstract ? <AbstractSection abstract={paper.abstract} /> : null}
    </div>
  );
}

function PaperFacts({ paper }: { paper: PaperDetails }) {
  return (
    <dl className="mt-4 mb-0 grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-3 border-y border-border py-3.5 pb-4">
      <div>
        <dt className="text-[10px] font-semibold leading-[1.3] tracking-[0.045em] text-text-secondary">Published</dt>
        <dd className="m-0 text-xs font-semibold leading-[1.4] text-text-primary [overflow-wrap:anywhere]">{publicationDate(paper)}</dd>
      </div>
      <div>
        <dt className="text-[10px] font-semibold leading-[1.3] tracking-[0.045em] text-text-secondary">Venue</dt>
        <dd className="m-0 text-xs font-semibold leading-[1.4] text-text-primary [overflow-wrap:anywhere]">{paper.venue || "Unlisted"}</dd>
      </div>
      {paper.citationCount !== null ? (
        <div>
          <dt className="text-[10px] font-semibold leading-[1.3] tracking-[0.045em] text-text-secondary">Citations</dt>
          <dd className="m-0 text-xs font-semibold leading-[1.4] text-text-primary [overflow-wrap:anywhere]">{paper.citationCount.toLocaleString()}</dd>
        </div>
      ) : null}
    </dl>
  );
}

function NodeTextSection({ label, value }: { label: string; value: string }) {
  return (
    <section className="mt-[18px] grid gap-2 border-t border-border pt-[18px] first:mt-0 first:border-t-0 first:pt-0">
      <span className={metadataLabelClass}>{label}</span>
      <p className={detailParagraphClass}>{value}</p>
    </section>
  );
}

function LearningSection({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <section className="mt-[18px] grid gap-2 border-t border-border pt-[18px] first-of-type:mt-0 first-of-type:border-t-0 first-of-type:pt-5">
      <span className={cx(metadataLabelClass, sectionLabelTone(label))}>{label}</span>
      <p className={detailParagraphClass}>{value}</p>
    </section>
  );
}

function AbstractSection({ abstract }: { abstract: string }) {
  return (
    <section className="mt-[22px] grid gap-2 border-t border-border pt-[18px]">
      <span className={cx(metadataLabelClass, "text-[#756a5e]")}>Abstract</span>
      <p className={detailParagraphClass}>{abstract}</p>
    </section>
  );
}

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  const [showAll, setShowAll] = useState(false);
  const visiblePapers = showAll ? papers : papers.slice(0, 3);

  return (
    <section className="mt-7 grid gap-0 border-t border-border pt-[18px]">
      <span className={cx(metadataLabelClass, "pb-3.5 text-text-primary")}>Similar papers</span>
      {visiblePapers.map((paper, index) => (
        <article key={paper.paper_id} className={cx("grid gap-[5px] border-t border-border py-3.5", !showAll && index === 2 && "max-h-[52px] overflow-hidden opacity-45 blur-[2.4px]")}>
          <strong className="text-[13px] font-semibold leading-[1.45] text-text-primary [overflow-wrap:anywhere]">{paper.title}</strong>
          <span className="text-[11px] leading-[1.4] text-text-secondary">{authorLine(paper.authors ?? [])}</span>
          <span className="text-[11px] leading-[1.4] text-text-secondary">{publicationDate({ publicationDate: paper.publication_date ?? null, year: paper.year })}</span>
          {paper.arxiv_link || paper.s2_link ? (
            <div className="flex flex-wrap gap-2 pt-[3px]">
              {paper.arxiv_link ? <ExternalLink href={paper.arxiv_link} className={similarLinkClass}>arXiv ↗</ExternalLink> : null}
              {paper.s2_link ? <ExternalLink href={paper.s2_link} className={similarLinkClass}>Semantic Scholar ↗</ExternalLink> : null}
            </div>
          ) : null}
        </article>
      ))}
      {papers.length > 3 ? (
        <div className={cx("relative z-[1] -mt-[42px] bg-[linear-gradient(to_bottom,transparent_0%,color-mix(in_srgb,var(--color-surface)_78%,transparent)_34%,var(--color-surface)_68%)] pt-[42px]", showAll && "mt-2 bg-transparent pt-0")}>
          <button
            className="block min-h-10 w-full rounded-[3px] border border-border-strong bg-[color-mix(in_srgb,var(--color-surface)_90%,var(--color-surface-subtle))] px-3 py-[9px] text-xs font-semibold text-text-primary hover:border-accent hover:text-accent-deep"
            type="button"
            onClick={() => setShowAll((current) => !current)}
            aria-expanded={showAll}
          >
            {showAll ? "Show fewer papers" : "Show more papers"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

const metadataLabelClass = "text-xs font-bold leading-[1.3] tracking-[0.01em] text-text-secondary";
const detailParagraphClass = "m-0 w-[min(100%,62ch)] text-sm leading-[1.65] text-[color-mix(in_srgb,var(--color-text-primary)_82%,var(--color-text-secondary))]";
const similarLinkClass = "text-[11px] font-semibold text-accent-deep underline decoration-[color-mix(in_srgb,var(--color-accent)_45%,var(--color-border))] underline-offset-[3px] hover:decoration-accent";

function sectionLabelTone(label: string): string {
  if (label === "Summary") return "text-accent-deep";
  if (label === "Why it matters") return "text-[#526575]";
  return "";
}

function fullAuthorList(authors: string[]): string {
  return authors.length > 0 ? authors.join(", ") : "Authors unavailable";
}

function PaperSourceActions({ paper }: { paper: PaperDetails }) {
  if (!paper.arxivLink && !paper.semanticScholarLink) {
    return null;
  }
  return (
    <footer className="-mx-[26px] -mb-[22px] mt-[18px] border-t border-border bg-surface px-[26px] py-3.5" aria-label="Original paper sources">
      <div className="flex flex-wrap gap-2">
        {paper.arxivLink ? <ExternalLink href={paper.arxivLink} className="rounded-sm border border-text-primary bg-text-primary px-2.5 py-2 text-xs font-semibold text-surface no-underline transition-[background-color,border-color,color] duration-150 hover:border-accent">arXiv ↗</ExternalLink> : null}
        {paper.semanticScholarLink ? (
          <ExternalLink href={paper.semanticScholarLink} className="rounded-sm border border-border-strong bg-surface px-2.5 py-2 text-xs font-semibold text-text-primary no-underline transition-[background-color,border-color,color] duration-150 hover:border-accent hover:bg-accent-subtle hover:text-accent-deep">
            Semantic Scholar ↗
          </ExternalLink>
        ) : null}
      </div>
    </footer>
  );
}

function ExternalLink({
  href,
  children,
  className,
}: {
  href: string;
  children: string;
  className?: string;
}) {
  return (
    <a href={href} className={className} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function headingLabel(kind: TreeNodeViewModel["kind"]): string {
  if (kind === "root") {
    return "Topic";
  }
  if (kind === "branch") {
    return "Branch";
  }
  return "Paper";
}

function TagList({ tags, label }: { tags: string[]; label: string }) {
  if (tags.length === 0) {
    return null;
  }
  return (
    <section className="mt-[18px] grid gap-2 border-t border-border pt-[18px]" aria-label={label}>
      <span className={metadataLabelClass}>{label}</span>
      <p className={detailParagraphClass}>{tags.join(" · ")}</p>
    </section>
  );
}

function QuestionList({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <section className="mt-[18px] grid gap-2 border-t border-border pt-[18px]">
      <span className={metadataLabelClass}>Open questions</span>
      <ul className="m-0 w-[min(100%,62ch)] list-disc pl-5 text-sm leading-[1.65] text-text-secondary marker:text-text-secondary">
        {questions.map((question) => (
          <li className="mb-2.5 pl-1 leading-[1.55] last:mb-0" key={question}>{question}</li>
        ))}
      </ul>
    </section>
  );
}
