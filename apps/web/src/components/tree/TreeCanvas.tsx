import { TreeEdge } from "./TreeEdge";
import { TreeNode } from "./TreeNode";
import type { TreeNodeId, TreeViewModel } from "../../lib/types";

type TreeCanvasProps = {
  tree: TreeViewModel;
  selectedNodeId: TreeNodeId;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function TreeCanvas({ tree, selectedNodeId, onSelectNode }: TreeCanvasProps) {
  return (
    <div className="tree-canvas-shell">
      <div className="canvas-label-row">
        <span>Tree canvas</span>
        <span>{tree.paperCount} visible papers</span>
      </div>
      <div className="tree-canvas" tabIndex={-1}>
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
            {tree.edges.map((edge) => (
              <TreeEdge key={`${edge.from}-${edge.to}`} edge={edge} />
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
