import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TreeEdge } from "./TreeEdge";
import { TreeNode } from "./TreeNode";
import { normalizeWorkspaceForTree, type MeasuredNodeHeights } from "../../lib/workspaceAdapter";
import type { TreeNodeId, TreeViewModel, WorkspaceDocument } from "../../lib/types";

type TreeCanvasProps = {
  tree: TreeViewModel;
  workspace: WorkspaceDocument | null;
  selectedNodeId: TreeNodeId | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function TreeCanvas({ tree, workspace, selectedNodeId, onSelectNode }: TreeCanvasProps) {
  const [zoom, setZoom] = useState(1);
  const canvasRef = useRef<HTMLDivElement>(null);
  const measureLayerRef = useRef<HTMLDivElement>(null);
  const zoomAnchorRef = useRef<{ x: number; y: number } | null>(null);
  const [measuredHeights, setMeasuredHeights] = useState<{
    treeKey: string;
    heights: MeasuredNodeHeights;
  } | null>(null);
  const [fontsReady, setFontsReady] = useState(false);
  const remeasuredForFontsRef = useRef(false);
  const zoomPercent = Math.round(zoom * 100);
  const treeKey = `${tree.workspaceId}:${tree.currentVersionHash ?? ""}`;
  const measured = measuredHeights?.treeKey === treeKey;

  // Cards are rendered once into a hidden layer and measured from the DOM, so
  // layout heights always match the real CSS — no typography mirror to drift.
  // `measured` is a dependency because the layer only exists while it is false:
  // discarding heights is what remounts the layer and asks for a fresh pass.
  useLayoutEffect(() => {
    const layer = measureLayerRef.current;
    if (!layer) {
      return;
    }
    const heights: MeasuredNodeHeights = {};
    layer.querySelectorAll<HTMLElement>("[data-measure-id]").forEach((element) => {
      heights[element.dataset.measureId as string] = Math.ceil(
        element.getBoundingClientRect().height,
      );
    });
    setMeasuredHeights({ treeKey, heights });
  }, [treeKey, tree, measured]);

  useEffect(() => {
    let active = true;
    void document.fonts?.ready?.then(() => {
      if (active) {
        setFontsReady(true);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  // Inter is loaded with `display=swap`, so a cold load measures every card
  // against the fallback face. Throwing that pass away once the real face
  // arrives re-measures against the metrics that actually paint; the ref keeps
  // it to a single extra pass.
  useEffect(() => {
    if (!fontsReady || remeasuredForFontsRef.current) {
      return;
    }
    remeasuredForFontsRef.current = true;
    setMeasuredHeights(null);
  }, [fontsReady]);

  const displayTree = useMemo(() => {
    if (!workspace || !measured || !measuredHeights) {
      return tree;
    }
    return normalizeWorkspaceForTree(workspace, measuredHeights.heights);
  }, [tree, workspace, measured, measuredHeights]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !measured) return;
    setZoom(1);
    zoomAnchorRef.current = null;
    canvas.scrollLeft = Math.max(0, displayTree.root.position.x - 20);
    canvas.scrollTop = Math.max(0, displayTree.root.position.y - 36);
    // displayTree is intentionally read but not tracked: re-centering belongs to
    // a workspace switch and to the first measured layout, not to every relayout.
  }, [tree.workspaceId, measured]);

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
    <div className="grid h-full grid-rows-[48px_minmax(0,1fr)] max-[720px]:grid-rows-[46px_minmax(0,1fr)]">
      <div className="flex min-w-0 items-center justify-between gap-4 px-6 text-xs text-text-secondary max-[720px]:px-3">
        <span className="font-semibold text-text-primary max-[520px]:hidden">Workspace</span>
        <div className="flex items-center gap-3.5 max-[720px]:gap-2">
          <span className="whitespace-nowrap max-[720px]:hidden">
            {displayTree.branchCount} branches / {displayTree.paperCount} papers
          </span>
          <div className="grid grid-cols-[28px_minmax(42px,auto)_28px] items-center overflow-hidden rounded-sm border border-border bg-surface" aria-label="Canvas zoom">
            <button
              className="grid h-7 w-7 place-items-center border-0 border-r border-border bg-transparent p-0 text-base leading-none text-text-secondary transition-[background-color,color] duration-150 enabled:hover:bg-accent-subtle enabled:hover:text-accent-deep disabled:cursor-not-allowed disabled:text-border-strong"
              type="button"
              onClick={() => adjustZoom(-0.1)}
              disabled={zoom <= 0.6}
              aria-label="Zoom out"
            >
              −
            </button>
            <output className="px-[7px] text-center text-[11px] font-semibold text-text-primary" aria-live="polite">{zoomPercent}%</output>
            <button
              className="grid h-7 w-7 place-items-center border-0 border-l border-border bg-transparent p-0 text-base leading-none text-text-secondary transition-[background-color,color] duration-150 enabled:hover:bg-accent-subtle enabled:hover:text-accent-deep disabled:cursor-not-allowed disabled:text-border-strong"
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
      <div ref={canvasRef} className="scrollbar-rt relative mx-6 mb-6 min-h-0 min-w-0 overflow-auto border border-border bg-surface max-[720px]:mx-3 max-[720px]:mb-3" aria-label="Research topic tree">
        {!measured ? (
          <div
            ref={measureLayerRef}
            className="pointer-events-none invisible absolute inset-0 overflow-hidden"
            aria-hidden="true"
          >
            {tree.nodes.map((node) => (
              <TreeNode key={node.id} node={node} measure />
            ))}
          </div>
        ) : null}
        <div
          className="relative min-h-full min-w-full"
          style={{ width: displayTree.canvas.width * zoom, height: displayTree.canvas.height * zoom }}
        >
          <div
            className="tree-grid-bg relative origin-top-left will-change-transform"
            style={{
              width: displayTree.canvas.width,
              height: displayTree.canvas.height,
              transform: `scale(${zoom})`,
            }}
          >
            <svg
              className="pointer-events-none absolute inset-0 z-0"
              width={displayTree.canvas.width}
              height={displayTree.canvas.height}
              aria-hidden="true"
            >
              {displayTree.edges.map((edge, index) => (
                <TreeEdge key={`${edge.from.x}:${edge.from.y}:${edge.to.x}:${edge.to.y}:${index}`} edge={edge} />
              ))}
            </svg>
            {displayTree.nodes.map((node) => (
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
