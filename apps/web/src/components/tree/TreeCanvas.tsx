import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TreeEdge } from "./TreeEdge";
import { TreeNode } from "./TreeNode";
import { normalizeWorkspaceForTree, type MeasuredNodeHeights } from "../../lib/workspaceAdapter";
import { cx } from "../../lib/cx";
import { FitIcon, ZoomInIcon, ZoomOutIcon } from "../ui/icons";
import type { TreeNodeId, TreeViewModel, WorkspaceDocument } from "../../lib/types";

type TreeCanvasProps = {
  tree: TreeViewModel;
  workspace: WorkspaceDocument | null;
  selectedNodeId: TreeNodeId | null;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 1.5;

/** Where a workspace was left: what the canvas restores when it is reopened. */
type Viewport = { zoom: number; scrollLeft: number; scrollTop: number };

/**
 * Viewports outlive the canvas component, which unmounts whenever a workspace
 * finishes loading or the panel layout changes. Keying by workspace is what
 * makes leaving one workspace and coming back land on the same place.
 */
const viewports = new Map<string, Viewport>();

export function TreeCanvas({ tree, workspace, selectedNodeId, onSelectNode }: TreeCanvasProps) {
  const [zoom, setZoom] = useState(() => viewports.get(tree.workspaceId)?.zoom ?? 1);
  const canvasRef = useRef<HTMLDivElement>(null);
  const measureLayerRef = useRef<HTMLDivElement>(null);
  const zoomAnchorRef = useRef<{ x: number; y: number } | null>(null);
  // The viewport fitting replaced, kept so pressing fit again puts it back. It
  // is state rather than a ref because the button says which way it will go.
  const [beforeFit, setBeforeFit] = useState<Viewport | null>(null);
  const restoreScrollRef = useRef<Viewport | null>(null);
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

  // Opening a workspace restores where it was left, and only falls back to the
  // root when it has never been opened.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !measured) return;
    zoomAnchorRef.current = null;
    setBeforeFit(null);
    const saved = viewports.get(tree.workspaceId);
    if (saved) {
      setZoom(saved.zoom);
      canvas.scrollLeft = saved.scrollLeft;
      canvas.scrollTop = saved.scrollTop;
      return;
    }
    setZoom(1);
    canvas.scrollLeft = Math.max(0, displayTree.root.position.x - 56);
    canvas.scrollTop = Math.max(0, displayTree.root.position.y - 36);
    // displayTree is intentionally read but not tracked: re-centering belongs to
    // a workspace switch and to the first measured layout, not to every relayout.
  }, [tree.workspaceId, measured]);

  // Every pan and zoom is recorded, including the ones the canvas makes itself
  // when it brings a searched-for node into view.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !measured) return;
    const workspaceId = tree.workspaceId;
    function remember() {
      viewports.set(workspaceId, {
        zoom,
        scrollLeft: canvas!.scrollLeft,
        scrollTop: canvas!.scrollTop,
      });
    }
    remember();
    canvas.addEventListener("scroll", remember, { passive: true });
    return () => canvas.removeEventListener("scroll", remember);
  }, [measured, tree.workspaceId, zoom]);

  // Search opens a node that may be far off screen. Bringing it into view only
  // when it is actually outside keeps clicking a visible card from moving the
  // canvas under the pointer.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !selectedNodeId || !measured) return;
    const node = displayTree.nodesById[selectedNodeId];
    if (!node) return;
    const left = node.position.x * zoom;
    const top = node.position.y * zoom;
    const right = left + node.size.width * zoom;
    const bottom = top + node.size.height * zoom;
    const visible =
      left >= canvas.scrollLeft &&
      right <= canvas.scrollLeft + canvas.clientWidth &&
      top >= canvas.scrollTop &&
      bottom <= canvas.scrollTop + canvas.clientHeight;
    if (visible) return;
    canvas.scrollTo({
      left: Math.max(0, left - (canvas.clientWidth - node.size.width * zoom) / 2),
      top: Math.max(0, top - (canvas.clientHeight - node.size.height * zoom) / 2),
      behavior: "smooth",
    });
  }, [selectedNodeId, displayTree, measured, zoom]);

  // Scroll can only be corrected once the canvas has been laid out at the new
  // scale: either back to a remembered position, or around the point that was
  // centred before the zoom.
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const restore = restoreScrollRef.current;
    if (restore) {
      restoreScrollRef.current = null;
      canvas.scrollLeft = restore.scrollLeft;
      canvas.scrollTop = restore.scrollTop;
      return;
    }
    const anchor = zoomAnchorRef.current;
    if (!anchor) return;
    canvas.scrollLeft = Math.max(0, anchor.x * zoom - canvas.clientWidth / 2);
    canvas.scrollTop = Math.max(0, anchor.y * zoom - canvas.clientHeight / 2);
    zoomAnchorRef.current = null;
  }, [zoom]);

  const applyZoom = useCallback((nextZoom: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Any deliberate zoom retires the fit toggle: what it would return to is no
    // longer where the reader was.
    setBeforeFit(null);
    setZoom((current) => {
      const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(nextZoom.toFixed(2))));
      if (clamped === current) return current;
      zoomAnchorRef.current = {
        x: (canvas.scrollLeft + canvas.clientWidth / 2) / current,
        y: (canvas.scrollTop + canvas.clientHeight / 2) / current,
      };
      return clamped;
    });
  }, []);

  /**
   * Fit, then unfit. Pulling all the way out is how you get your bearings, and
   * what you want next is almost always the corner you were reading — so the
   * second press restores the exact zoom and scroll the first one replaced.
   */
  function fitToView() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (beforeFit) {
      setBeforeFit(null);
      zoomAnchorRef.current = null;
      restoreScrollRef.current = beforeFit;
      setZoom(beforeFit.zoom);
      return;
    }
    const scale = Math.min(
      canvas.clientWidth / displayTree.canvas.width,
      canvas.clientHeight / displayTree.canvas.height,
    );
    const fitted = Math.min(1, Math.max(MIN_ZOOM, Number(scale.toFixed(2))));
    // Already showing everything: there is nothing to fit, and nothing to
    // return to either.
    if (fitted === zoom) return;
    const before = { zoom, scrollLeft: canvas.scrollLeft, scrollTop: canvas.scrollTop };
    // `applyZoom` clears the toggle, so the record is kept after it, not before.
    applyZoom(fitted);
    setBeforeFit(before);
  }

  return (
    <div className="relative h-full min-w-0" aria-label="Research topic tree">
      <div
        ref={canvasRef}
        className="scrollbar-rt tree-grid-bg relative h-full min-h-0 min-w-0 overflow-auto bg-surface"
      >
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
            className="relative origin-top-left will-change-transform"
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
                <TreeEdge
                  key={`${edge.from.x}:${edge.from.y}:${edge.to.x}:${edge.to.y}:${index}`}
                  edge={edge}
                />
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

      <div
        className="absolute right-4 bottom-4 flex items-center overflow-hidden rounded-[9px] border border-border bg-surface shadow-control"
        aria-label="Canvas zoom"
      >
        <ZoomButton label="Zoom out" onClick={() => applyZoom(zoom - 0.1)} disabled={zoom <= MIN_ZOOM}>
          <ZoomOutIcon className="h-3.5 w-3.5" />
        </ZoomButton>
        <button
          className="h-8 w-[46px] border-0 bg-transparent text-[11.5px] font-semibold text-text-primary transition-[background-color] duration-150 hover:bg-surface-subtle"
          type="button"
          onClick={() => applyZoom(1)}
          title="Reset zoom"
          aria-label="Reset zoom"
        >
          <output aria-live="polite">{zoomPercent}%</output>
        </button>
        <ZoomButton label="Zoom in" onClick={() => applyZoom(zoom + 0.1)} disabled={zoom >= MAX_ZOOM}>
          <ZoomInIcon className="h-3.5 w-3.5" />
        </ZoomButton>
        <span className="h-[18px] w-px flex-none bg-border" aria-hidden="true" />
        <ZoomButton
          label={beforeFit ? "Back to where you were" : "Fit to view"}
          onClick={fitToView}
        >
          <FitIcon
            className={cx(
              "h-[13px] w-[13px] transition-transform duration-200 ease-research",
              beforeFit && "rotate-45",
            )}
          />
        </ZoomButton>
      </div>
    </div>
  );
}

function ZoomButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      className="grid h-8 w-8 place-items-center border-0 bg-transparent p-0 text-text-secondary transition-[background-color,color] duration-150 enabled:hover:bg-surface-subtle enabled:hover:text-text-primary disabled:cursor-not-allowed disabled:text-border-strong"
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}
