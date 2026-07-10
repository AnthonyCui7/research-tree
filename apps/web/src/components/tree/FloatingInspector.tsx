import type { PaperDetails, SimilarPaper, TreeNodeViewModel } from "../../lib/types";
import { authorLine, publicationDate } from "./TreeNode";

type FloatingInspectorProps = {
  node: TreeNodeViewModel | null;
  onClose: () => void;
};

export function FloatingInspector({ node, onClose }: FloatingInspectorProps) {
  if (!node) {
    return null;
  }

  return (
    <aside key={node.id} className="floating-inspector" aria-label="Selected node details">
      <div className="inspector-heading">
        <div>
          <span className="eyebrow">{headingLabel(node.kind)}</span>
          <h2>{node.title}</h2>
        </div>
        <button className="inspector-close" type="button" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </div>

      <div className="inspector-content">
        {node.kind === "root" ? (
          <div className="inspector-stack">
            <p>{node.overview}</p>
            <PaperDetailsSection paper={node.anchorPaper} showTitle />
            <MetadataRow label="Suggested direction" value={node.suggestedReadingDirection} />
            <TagList tags={node.keyTerms} />
            <QuestionList questions={node.openQuestions} />
          </div>
        ) : null}

        {node.kind === "branch" ? (
          <div className="inspector-stack">
            <p>{node.description}</p>
            <MetadataRow label="Why this branch matters" value={node.whyItMatters} />
            <PaperDetailsSection paper={node.anchorPaper} showTitle />
            <TagList tags={node.tags} />
            <QuestionList questions={node.openQuestions} />
          </div>
        ) : null}

        {node.kind === "paper" ? <PaperDetailsSection paper={node} showSimilarPapers /> : null}
      </div>

      {node.kind === "paper" ? <PaperLinks paper={node} /> : null}
    </aside>
  );
}

function PaperDetailsSection({
  paper,
  showTitle = false,
  showSimilarPapers = false,
}: {
  paper: PaperDetails | null;
  showTitle?: boolean;
  showSimilarPapers?: boolean;
}) {
  if (!paper) {
    return null;
  }
  return (
    <div className="paper-detail-stack">
      {showTitle ? <h3>{paper.title}</h3> : null}
      <MetadataRow label="Authors" value={fullAuthorList(paper.authors)} />
      <div className="paper-metadata-grid">
        <MetadataRow label="Published" value={publicationDate(paper)} />
        <MetadataRow label="Venue" value={paper.venue || "Unlisted"} />
        {paper.citationCount !== null ? (
          <MetadataRow label="Citations" value={paper.citationCount.toLocaleString()} />
        ) : null}
      </div>
      <TextSection label="TLDR" value={paper.tldr || "Unavailable from Semantic Scholar."} />
      <TextSection label="Importance" value={paper.importance || "Not yet explained for this workspace."} />
      {paper.abstract ? <TextSection label="Abstract" value={paper.abstract} /> : null}
      {showSimilarPapers && paper.similarPapers.length > 0 ? (
        <SimilarPapers papers={paper.similarPapers} />
      ) : null}
    </div>
  );
}

function SimilarPapers({ papers }: { papers: SimilarPaper[] }) {
  return (
    <section className="similar-papers">
      <span className="metadata-label">Similar papers</span>
      {papers.map((paper) => (
        <article key={paper.paper_id} className="similar-paper">
          <strong>{paper.title}</strong>
          <span>{authorLine(paper.authors ?? [])}</span>
          <span>{publicationDate({ publicationDate: paper.publication_date ?? null, year: paper.year })}</span>
          <div className="similar-paper-links">
            {paper.arxiv_link ? <ExternalLink href={paper.arxiv_link}>arXiv</ExternalLink> : null}
            {paper.s2_link ? <ExternalLink href={paper.s2_link}>Semantic Scholar</ExternalLink> : null}
          </div>
        </article>
      ))}
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

function PaperLinks({ paper }: { paper: PaperDetails }) {
  return (
    <footer className="paper-links" aria-label="Paper sources">
      {paper.arxivLink ? <ExternalLink href={paper.arxivLink}>arXiv</ExternalLink> : null}
      {paper.semanticScholarLink ? (
        <ExternalLink href={paper.semanticScholarLink}>Semantic Scholar</ExternalLink>
      ) : null}
    </footer>
  );
}

function ExternalLink({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
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
