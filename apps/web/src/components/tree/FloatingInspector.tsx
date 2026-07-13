import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import type { PaperDetails, SimilarPaper, TreeNodeViewModel } from "../../lib/types";
import { authorLine, publicationDate } from "./TreeNode";

type FloatingInspectorProps = {
  node: TreeNodeViewModel | null;
  onClose: () => void;
  sidebarCollapsed: boolean;
};

const DESKTOP_SIDEBAR_WIDTH = 252;
const INSPECTOR_MIN_WIDTH = 420;

export function FloatingInspector({ node, onClose, sidebarCollapsed }: FloatingInspectorProps) {
  const [visibleNode, setVisibleNode] = useState<TreeNodeViewModel | null>(node);
  const [isClosing, setIsClosing] = useState(false);
  const [panelWidth, setPanelWidth] = useState(510);
  const nextNodeRef = useRef<TreeNodeViewModel | null>(null);

  useEffect(() => {
    if (node) {
      if (visibleNode && visibleNode.id !== node.id) {
        nextNodeRef.current = null;
        setVisibleNode(node);
        setIsClosing(false);
        return;
      }
      setVisibleNode(node);
      setIsClosing(false);
      return;
    }
    if (visibleNode) {
      nextNodeRef.current = null;
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

  function beginResize(event: ReactPointerEvent<HTMLButtonElement>) {
    if (window.innerWidth <= 980) return;
    event.preventDefault();
    const startX = event.clientX;
    const maxWidth = resizablePanelMaxWidth(sidebarCollapsed);
    const startWidth = clampResizablePanelWidth(panelWidth, INSPECTOR_MIN_WIDTH, maxWidth);
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";
    const onPointerMove = (moveEvent: PointerEvent) => {
      setPanelWidth(clampResizablePanelWidth(startWidth + startX - moveEvent.clientX, INSPECTOR_MIN_WIDTH, maxWidth));
    };
    const onPointerUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  }

  if (!visibleNode) {
    return null;
  }
  const sourcePaper = visibleNode.kind === "paper" ? visibleNode : visibleNode.anchorPaper;

  return (
    <aside
      key={visibleNode.id}
      className="floating-inspector"
      data-state={isClosing ? "closing" : "open"}
      aria-label="Selected node details"
      style={{
        "--inspector-panel-width": `${clampResizablePanelWidth(panelWidth, INSPECTOR_MIN_WIDTH, resizablePanelMaxWidth(sidebarCollapsed))}px`,
        "--inspector-panel-max-width": `${resizablePanelMaxWidth(sidebarCollapsed)}px`,
      } as CSSProperties}
      onAnimationEnd={() => {
        if (isClosing) {
          if (nextNodeRef.current) {
            setVisibleNode(nextNodeRef.current);
            nextNodeRef.current = null;
            setIsClosing(false);
          } else {
            setVisibleNode(null);
          }
        }
      }}
    >
      <button className="inspector-resize-handle" type="button" onPointerDown={beginResize} aria-label="Resize details panel"><span className="drag-pill" aria-hidden="true" /></button>
      <div className="inspector-heading">
        <div>
          <span className={visibleNode.kind === "paper" ? "eyebrow paper-inspector-eyebrow" : "eyebrow"}>
            {headingLabel(visibleNode.kind)}
          </span>
          <h2 className={visibleNode.kind === "paper" ? "paper-inspector-title" : undefined}>{visibleNode.title}</h2>
        </div>
        <button className="inspector-close icon-button" type="button" onClick={onClose} aria-label="Close details" title="Close">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
        </button>
      </div>

      <div className="inspector-content">
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

function resizablePanelMaxWidth(sidebarCollapsed: boolean): number {
  return Math.max(0, window.innerWidth - (sidebarCollapsed ? 0 : DESKTOP_SIDEBAR_WIDTH));
}

function clampResizablePanelWidth(width: number, minWidth: number, maxWidth: number): number {
  const effectiveMinWidth = Math.min(minWidth, maxWidth);
  return Math.min(maxWidth, Math.max(effectiveMinWidth, width));
}

function RootLearningDetails({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
}) {
  return (
    <div className="node-learning-detail">
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
    <div className="node-learning-detail">
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
    <div className="paper-learning-detail">
      <p className="paper-authors-full">{fullAuthorList(paper.authors)}</p>
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
    <div className="paper-reference-detail">
      {showTitle ? (
        <div className="paper-reference-heading">
          <span className="metadata-label paper-section-label paper-reference-label">Survey anchor</span>
          <h3>{paper.title}</h3>
        </div>
      ) : null}
      <p className="paper-authors-full">{fullAuthorList(paper.authors)}</p>
      <PaperFacts paper={paper} />
      <LearningSection label="Summary" value={paper.tldr || "Unavailable"} />
      <LearningSection label="Why it matters" value={paper.importance || "Unavailable"} />
      {paper.abstract ? <AbstractSection abstract={paper.abstract} /> : null}
    </div>
  );
}

function PaperFacts({ paper }: { paper: PaperDetails }) {
  return (
    <dl className="paper-facts">
      <div>
        <dt>Published</dt>
        <dd>{publicationDate(paper)}</dd>
      </div>
      <div>
        <dt>Venue</dt>
        <dd>{paper.venue || "Unlisted"}</dd>
      </div>
      {paper.citationCount !== null ? (
        <div>
          <dt>Citations</dt>
          <dd>{paper.citationCount.toLocaleString()}</dd>
        </div>
      ) : null}
    </dl>
  );
}

function NodeTextSection({ label, value }: { label: string; value: string }) {
  return (
    <section className="node-text-section">
      <span className="metadata-label paper-section-label node-section-label">{label}</span>
      <p>{value}</p>
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
    <section className="paper-learning-section">
      <span className={`metadata-label ${sectionLabelClass(label)}`}>{label}</span>
      <p>{value}</p>
    </section>
  );
}

function AbstractSection({ abstract }: { abstract: string }) {
  return (
    <section className="paper-abstract">
      <span className="metadata-label paper-section-label paper-section-abstract">Abstract</span>
      <p>{abstract}</p>
    </section>
  );
}

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  const [showAll, setShowAll] = useState(false);
  const visiblePapers = showAll ? papers : papers.slice(0, 3);

  return (
    <section className="similar-papers">
      <span className="similar-papers-heading metadata-label paper-section-label paper-section-similar">Similar papers</span>
      {visiblePapers.map((paper, index) => (
        <article key={paper.paper_id} className={`similar-paper${!showAll && index === 2 ? " similar-paper-preview" : ""}`}>
          <strong>{paper.title}</strong>
          <span>{authorLine(paper.authors ?? [])}</span>
          <span>{publicationDate({ publicationDate: paper.publication_date ?? null, year: paper.year })}</span>
          {paper.arxiv_link || paper.s2_link ? (
            <div className="similar-paper-links">
              {paper.arxiv_link ? <ExternalLink href={paper.arxiv_link}>arXiv ↗</ExternalLink> : null}
              {paper.s2_link ? <ExternalLink href={paper.s2_link}>Semantic Scholar ↗</ExternalLink> : null}
            </div>
          ) : null}
        </article>
      ))}
      {papers.length > 2 ? (
        <div className={`similar-paper-reveal${showAll ? " similar-paper-reveal-expanded" : ""}`}>
          <button
            className="similar-paper-toggle"
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

function sectionLabelClass(label: string): string {
  return `paper-section-label paper-section-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
}

function fullAuthorList(authors: string[]): string {
  return authors.length > 0 ? authors.join(", ") : "Authors unavailable";
}

function PaperSourceActions({ paper }: { paper: PaperDetails }) {
  if (!paper.arxivLink && !paper.semanticScholarLink) {
    return null;
  }
  return (
    <footer className="paper-source-actions" aria-label="Original paper sources">
      <div>
        {paper.arxivLink ? <ExternalLink href={paper.arxivLink} className="paper-source-action">arXiv ↗</ExternalLink> : null}
        {paper.semanticScholarLink ? (
          <ExternalLink href={paper.semanticScholarLink} className="paper-source-action paper-source-action-secondary">
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
    <section className="node-list-section node-tag-list" aria-label={label}>
      <span className="metadata-label paper-section-label node-section-label">{label}</span>
      <p>{tags.join(" · ")}</p>
    </section>
  );
}

function QuestionList({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <section className="node-list-section node-question-list">
      <span className="metadata-label paper-section-label node-section-label">Open questions</span>
      <ul>
        {questions.map((question) => (
          <li key={question}>{question}</li>
        ))}
      </ul>
    </section>
  );
}
