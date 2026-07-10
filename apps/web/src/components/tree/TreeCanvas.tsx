import { TreeEdge } from "./TreeEdge";
import { TreeNode } from "./TreeNode";
import type { TreeNodeId, TreeViewModel } from "../../lib/types";

type TreeCanvasProps = {
  tree: TreeViewModel;
  selectedNodeId: TreeNodeId | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function TreeCanvas({ tree, selectedNodeId, onSelectNode }: TreeCanvasProps) {
  return (
    <div className="tree-canvas-shell">
      <div className="canvas-label-row">
        <span>Research tree</span>
        <span>
          {tree.branchCount} branches / {tree.pathCount} reading paths / {tree.paperCount} papers
        </span>
      </div>
      <div className="tree-canvas" aria-label="Research topic tree">
        <div
          className="tree-plane"
          style={{ width: tree.canvas.width, height: tree.canvas.height }}
        >
          <svg
            className="tree-edges"
            width={tree.canvas.width}
            height={tree.canvas.height}
            aria-hidden="true"
          >
            {tree.edges.map((edge, index) => (
              <TreeEdge key={`${edge.from.x}:${edge.from.y}:${edge.to.x}:${edge.to.y}:${index}`} edge={edge} />
            ))}
          </svg>
          {tree.nodes.map((node) => (
            <TreeNode
              key={node.id}
              node={node}
              selected={node.id === selectedNodeId}
              onSelectNode={onSelectNode}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
