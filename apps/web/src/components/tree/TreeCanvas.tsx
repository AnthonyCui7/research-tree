import { useRef, useState } from "react";
import { TreeEdge } from "./TreeEdge";
import { TreeNode } from "./TreeNode";
import type { TreeNodeId, TreeViewModel } from "../../lib/types";

type TreeCanvasProps = {
  tree: TreeViewModel;
  selectedNodeId: TreeNodeId | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function TreeCanvas({ tree, selectedNodeId, onSelectNode }: TreeCanvasProps) {
  const [zoom, setZoom] = useState(1);
  const canvasRef = useRef<HTMLDivElement>(null);
  const zoomPercent = Math.round(zoom * 100);

  function adjustZoom(amount: number) {
    const nextZoom = Math.min(1.4, Math.max(0.6, Number((zoom + amount).toFixed(2))));
    if (nextZoom === zoom) {
      return;
    }

    const canvas = canvasRef.current;
    const centerX = canvas ? canvas.scrollLeft + canvas.clientWidth / 2 : 0;
    const centerY = canvas ? canvas.scrollTop + canvas.clientHeight / 2 : 0;
    const scale = nextZoom / zoom;

    setZoom(nextZoom);
    requestAnimationFrame(() => {
      if (!canvas) {
        return;
      }
      canvas.scrollLeft = Math.max(0, centerX * scale - canvas.clientWidth / 2);
      canvas.scrollTop = Math.max(0, centerY * scale - canvas.clientHeight / 2);
    });
  }

  return (
    <div className="tree-canvas-shell">
      <div className="canvas-label-row">
        <span>Research tree</span>
        <div className="canvas-controls">
          <span>
            {tree.branchCount} branches / {tree.pathCount} reading paths / {tree.paperCount} papers
          </span>
          <div className="canvas-zoom" aria-label="Canvas zoom">
            <button
              type="button"
              onClick={() => adjustZoom(-0.1)}
              disabled={zoom <= 0.6}
              aria-label="Zoom out"
            >
              −
            </button>
            <output aria-live="polite">{zoomPercent}%</output>
            <button
              type="button"
              onClick={() => adjustZoom(0.1)}
              disabled={zoom >= 1.4}
              aria-label="Zoom in"
            >
              +
            </button>
          </div>
        </div>
      </div>
      <div ref={canvasRef} className="tree-canvas" aria-label="Research topic tree">
        <div
          className="tree-canvas-viewport"
          style={{ width: tree.canvas.width * zoom, height: tree.canvas.height * zoom }}
        >
          <div
            className="tree-plane"
            style={{
              width: tree.canvas.width,
              height: tree.canvas.height,
              transform: `scale(${zoom})`,
            }}
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
    </div>
  );
}
