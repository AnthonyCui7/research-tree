import type { PaperDetails, TreeNodeId, TreeNodeViewModel } from "../../lib/types";

type TreeNodeProps = {
  node: TreeNodeViewModel;
  selected: boolean;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function TreeNode({ node, selected, onSelectNode }: TreeNodeProps) {
  return (
    <button
      type="button"
      className={`tree-node tree-node-${node.kind}`}
      data-selected={selected}
      style={{
        left: node.position.x,
        top: node.position.y,
        width: node.size.width,
        minHeight: node.size.height,
      }}
      aria-pressed={selected}
      onClick={() => onSelectNode(node.id)}
    >
      {node.kind === "root" ? <RootNodeContent node={node} /> : null}
      {node.kind === "branch" ? <BranchNodeContent node={node} /> : null}
      {node.kind === "paper" ? <PaperNodeContent paper={node} /> : null}
    </button>
  );
}

function RootNodeContent({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
}) {
  return (
    <>
      <span className="node-kicker">Research topic</span>
      <strong className="root-node-title">{node.title}</strong>
      <span className="node-description">{node.overview}</span>
      <AnchorSummary paper={node.anchorPaper} label="Survey" />
      <span className="node-expand-hint">Expand</span>
    </>
  );
}

function BranchNodeContent({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "branch" }>;
}) {
  return (
    <>
      <span className="node-kicker">Research branch</span>
      <strong>{node.title}</strong>
      <span className="node-description">{node.description}</span>
      <AnchorSummary paper={node.anchorPaper} label="Branch survey" />
      <span className="node-expand-hint">Expand</span>
    </>
  );
}

function PaperNodeContent({ paper }: { paper: Extract<TreeNodeViewModel, { kind: "paper" }> }) {
  return (
    <>
      <strong>{paper.title}</strong>
      <span className="paper-authors">{authorLine(paper.authors)}</span>
      <span className="paper-date">{publicationDate(paper)}</span>
      <span className="paper-tldr">
        <b>TLDR</b>
        {paper.tldr || "Unavailable from Semantic Scholar."}
      </span>
      <span className="node-expand-hint">Expand</span>
    </>
  );
}

function AnchorSummary({ paper, label }: { paper: PaperDetails | null; label: string }) {
  if (!paper) {
    return null;
  }
  return (
    <span className="survey-anchor-summary">
      <span>{label}</span>
      <b>{paper.title}</b>
      <em>{paper.year ?? "n.d."}</em>
    </span>
  );
}

export function authorLine(authors: string[]): string {
  if (authors.length === 0) {
    return "Authors unavailable";
  }
  if (authors.length === 1) {
    return citedAuthorName(authors[0]);
  }
  if (authors.length === 2) {
    return `${citedAuthorName(authors[0])} & ${citedAuthorName(authors[1])}`;
  }
  return `${citedAuthorName(authors[0])} et al.`;
}

function citedAuthorName(author: string): string {
  const trimmed = author.trim();
  if (!trimmed) {
    return "Author";
  }
  if (trimmed.includes(",")) {
    return trimmed.split(",", 1)[0].trim() || trimmed;
  }
  return trimmed.split(/\s+/).at(-1) || trimmed;
}

export function publicationDate(paper: Pick<PaperDetails, "publicationDate" | "year">): string {
  if (!paper.publicationDate) {
    return paper.year ? `${paper.year}` : "Date unavailable";
  }
  const date = new Date(`${paper.publicationDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return paper.year ? `${paper.year}` : "Date unavailable";
  }
  return new Intl.DateTimeFormat("en", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}
