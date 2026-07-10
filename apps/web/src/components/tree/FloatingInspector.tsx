import { useEffect, useState } from "react";
import type { PaperDetails, SimilarPaper, TreeNodeViewModel } from "../../lib/types";
import { authorLine, publicationDate } from "./TreeNode";

type FloatingInspectorProps = {
  node: TreeNodeViewModel | null;
  onClose: () => void;
};

export function FloatingInspector({ node, onClose }: FloatingInspectorProps) {
  const [visibleNode, setVisibleNode] = useState<TreeNodeViewModel | null>(node);
  const [isClosing, setIsClosing] = useState(false);

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

  if (!visibleNode) {
    return null;
  }

  return (
    <aside
      key={visibleNode.id}
      className="floating-inspector"
      data-state={isClosing ? "closing" : "open"}
      aria-label="Selected node details"
      onAnimationEnd={() => {
        if (isClosing) {
          setVisibleNode(null);
        }
      }}
    >
      <div className="inspector-heading">
        <div>
          <span className="eyebrow">{headingLabel(visibleNode.kind)}</span>
          <h2>{visibleNode.title}</h2>
        </div>
        <button className="inspector-close" type="button" onClick={onClose} aria-label="Close details">
          <span aria-hidden="true">×</span>
        </button>
      </div>

      <div className="inspector-content">
        {visibleNode.kind === "root" ? (
          <div className="inspector-stack">
            <p>{visibleNode.overview}</p>
            <PaperReferenceDetails paper={visibleNode.anchorPaper} showTitle />
            <MetadataRow label="Suggested direction" value={visibleNode.suggestedReadingDirection} />
            <TagList tags={visibleNode.keyTerms} />
            <QuestionList questions={visibleNode.openQuestions} />
          </div>
        ) : null}

        {visibleNode.kind === "branch" ? (
          <div className="inspector-stack">
            <p>{visibleNode.description}</p>
            <MetadataRow label="Why this branch matters" value={visibleNode.whyItMatters} />
            <PaperReferenceDetails paper={visibleNode.anchorPaper} showTitle />
            <TagList tags={visibleNode.tags} />
            <QuestionList questions={visibleNode.openQuestions} />
          </div>
        ) : null}

        {visibleNode.kind === "paper" ? <PaperLearningDetails paper={visibleNode} /> : null}
      </div>

      {visibleNode.kind === "paper" ? <PaperSourceActions paper={visibleNode} /> : null}
    </aside>
  );
}

function PaperLearningDetails({ paper }: { paper: PaperDetails }) {
  return (
    <div className="paper-learning-detail">
      <p className="paper-authors-full">{fullAuthorList(paper.authors)}</p>
      <PaperFacts paper={paper} />
      <LearningSection label="TLDR" value={paper.tldr || "Unavailable from Semantic Scholar."} primary />
      <LearningSection label="Why it matters" value={paper.importance || "Not yet explained for this workspace."} />
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
      {showTitle ? <h3>{paper.title}</h3> : null}
      <MetadataRow label="Authors" value={fullAuthorList(paper.authors)} />
      <PaperFacts paper={paper} />
      <TextSection label="TLDR" value={paper.tldr || "Unavailable from Semantic Scholar."} />
      <TextSection label="Why it matters" value={paper.importance || "Not yet explained for this workspace."} />
      {paper.abstract ? <TextSection label="Abstract" value={paper.abstract} /> : null}
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

function LearningSection({
  label,
  value,
  primary = false,
}: {
  label: string;
  value: string;
  primary?: boolean;
}) {
  return (
    <section className={`paper-learning-section${primary ? " paper-learning-section-primary" : ""}`}>
      <span className="metadata-label">{label}</span>
      <p>{value}</p>
    </section>
  );
}

function AbstractSection({ abstract }: { abstract: string }) {
  return (
    <section className="paper-abstract">
      <span className="metadata-label">Abstract</span>
      <p>{abstract}</p>
    </section>
  );
}

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  const [showAll, setShowAll] = useState(false);
  const visiblePapers = showAll ? papers : papers.slice(0, 2);
  const hiddenCount = papers.length - visiblePapers.length;

  return (
    <section className="similar-papers">
      <span className="similar-papers-heading metadata-label">Similar papers</span>
      {visiblePapers.map((paper) => (
        <article key={paper.paper_id} className="similar-paper">
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
        <button
          className="similar-paper-toggle"
          type="button"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "Show fewer papers" : `Show ${hiddenCount} more paper${hiddenCount === 1 ? "" : "s"}`}
        </button>
      ) : null}
    </section>
  );
}

function TextSection({ label, value }: { label: string; value: string }) {
  return (
    <section className="paper-text-section">
      <span className="metadata-label">{label}</span>
      <p>{value}</p>
    </section>
  );
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
        {paper.arxivLink ? <ExternalLink href={paper.arxivLink} className="paper-source-action">Open on arXiv ↗</ExternalLink> : null}
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
    return "Topic overview";
  }
  if (kind === "branch") {
    return "Research branch";
  }
  return "Paper";
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="metadata-row">
      <span>{label}</span>
      <p>{value}</p>
    </div>
  );
}

function TagList({ tags }: { tags: string[] }) {
  if (tags.length === 0) {
    return null;
  }
  return (
    <div className="tag-list" aria-label="Tags">
      {tags.map((tag) => (
        <span key={tag}>{tag}</span>
      ))}
    </div>
  );
}

function QuestionList({ questions }: { questions: string[] }) {
  if (questions.length === 0) {
    return null;
  }
  return (
    <div className="question-list">
      <span className="metadata-label">Open questions</span>
      <ul>
        {questions.map((question) => (
          <li key={question}>{question}</li>
        ))}
      </ul>
    </div>
  );
}
