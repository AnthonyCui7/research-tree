import type { TreeNodeViewModel } from "../../lib/types";

type FloatingInspectorProps = {
  node: TreeNodeViewModel | null;
};

export function FloatingInspector({ node }: FloatingInspectorProps) {
  if (!node) {
    return null;
  }

  return (
    <aside className="floating-inspector" aria-label="Selected node inspector">
      <div className="inspector-heading">
        <span className="eyebrow">{node.kind}</span>
        <h2>{node.title}</h2>
      </div>

      {node.kind === "root" ? (
        <div className="inspector-stack">
          <p>{node.overview}</p>
          <MetadataRow label="Survey type" value={node.surveyType} />
          <MetadataRow label="Reading direction" value={node.suggestedReadingDirection} />
          <TagList tags={node.keyTerms} />
          <QuestionList questions={node.openQuestions} />
        </div>
      ) : null}

      {node.kind === "branch" ? (
        <div className="inspector-stack">
          <p>{node.description}</p>
          <MetadataRow label="Why it matters" value={node.whyItMatters} />
          <MetadataRow label="Paper count" value={`${node.paperCount}`} />
          <TagList tags={node.tags} />
          <QuestionList questions={node.openQuestions} />
        </div>
      ) : null}

      {node.kind === "paper" ? (
        <div className="inspector-stack">
          <MetadataRow label="Year" value={node.year ? `${node.year}` : "Unknown"} />
          <MetadataRow label="Venue" value={node.venue || "Unlisted"} />
          <MetadataRow label="Role" value={node.role} />
          <MetadataRow label="Read status" value={node.readingStatus} />
          <MetadataRow label="Contribution" value={node.contribution} />
          <p className="abstract-preview">{node.abstractPreview}</p>
          <MetadataRow label="Similar papers" value={`${node.similarPaperCount}`} />
        </div>
      ) : null}
    </aside>
  );
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
