import type { TreeNodeId, TreeNodeViewModel } from "../../lib/types";

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
      style={{ left: node.position.x, top: node.position.y }}
      aria-pressed={selected}
      onClick={() => onSelectNode(node.id)}
    >
      {node.kind === "root" ? (
        <>
          <span className="node-kicker">Root overview</span>
          <strong>{node.title}</strong>
          <span>{node.paperCount} representative papers</span>
        </>
      ) : null}

      {node.kind === "branch" ? (
        <>
          <span className="node-row">
            <strong>{node.title}</strong>
            <span className="count-badge">{node.paperCount}</span>
          </span>
          <span className="node-description">{node.description}</span>
          <span className="node-hover-detail">{node.whyItMatters}</span>
        </>
      ) : null}

      {node.kind === "paper" ? (
        <>
          <strong>{node.title}</strong>
          <span className="paper-meta">
            {node.year ?? "n.d."}
            {node.venue ? ` / ${node.venue}` : ""}
          </span>
          <span className="status-chip">{node.role}</span>
          <span className="node-hover-detail">{node.contribution}</span>
        </>
      ) : null}
    </button>
  );
}
