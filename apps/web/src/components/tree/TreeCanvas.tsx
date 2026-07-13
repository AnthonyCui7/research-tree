import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const zoomAnchorRef = useRef<{ x: number; y: number } | null>(null);
  const zoomPercent = Math.round(zoom * 100);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setZoom(1);
    zoomAnchorRef.current = null;
    canvas.scrollLeft = Math.max(0, tree.root.position.x - 20);
    canvas.scrollTop = Math.max(0, tree.root.position.y - 36);
  }, [tree.workspaceId]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const anchor = zoomAnchorRef.current;
    if (!canvas || !anchor) return;
    canvas.scrollLeft = Math.max(0, anchor.x * zoom - canvas.clientWidth / 2);
    canvas.scrollTop = Math.max(0, anchor.y * zoom - canvas.clientHeight / 2);
    zoomAnchorRef.current = null;
  }, [zoom]);

  function adjustZoom(amount: number) {
    const nextZoom = Math.min(1.4, Math.max(0.6, Number((zoom + amount).toFixed(2))));
    const canvas = canvasRef.current;
    if (!canvas || nextZoom === zoom) return;
    zoomAnchorRef.current = {
      x: (canvas.scrollLeft + canvas.clientWidth / 2) / zoom,
      y: (canvas.scrollTop + canvas.clientHeight / 2) / zoom,
    };
    setZoom(nextZoom);
  }

  return (
    <div className="tree-canvas-shell">
      <div className="canvas-label-row">
        <span>Workspace</span>
        <div className="canvas-controls">
          <span>
            {tree.branchCount} branches / {tree.paperCount} papers
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
