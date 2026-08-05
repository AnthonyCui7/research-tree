import { useEffect, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

/** Width of the sidebar at desktop widths, subtracted from a panel's ceiling. */
export const DESKTOP_SIDEBAR_WIDTH = 250;

/** Below this width the panel is docked full-height over the canvas, not resizable. */
export const RESIZE_DISABLED_BELOW = 900;

/** A panel may be dragged over the whole canvas, stopping at the sidebar. */
export function resizablePanelMaxWidth(
  sidebarCollapsed: boolean,
  viewportWidth: number = window.innerWidth,
): number {
  return Math.max(0, viewportWidth - (sidebarCollapsed ? 0 : DESKTOP_SIDEBAR_WIDTH));
}

export function clampResizablePanelWidth(
  width: number,
  minWidth: number,
  maxWidth: number,
): number {
  return Math.min(maxWidth, Math.max(Math.min(minWidth, maxWidth), width));
}

/**
 * Drag a right-docked panel's left edge.
 *
 * Pointer capture is what makes releasing outside the window safe: without it
 * the pointerup never arrives and the page keeps a resize cursor and disabled
 * text selection for the rest of the session.
 */
export function beginPanelResize(
  event: ReactPointerEvent<HTMLElement>,
  options: {
    startWidth: number;
    minWidth: number;
    maxWidth: number;
    onWidth: (width: number) => void;
  },
): void {
  if (window.innerWidth <= RESIZE_DISABLED_BELOW) return;
  event.preventDefault();

  const handle = event.currentTarget;
  const startX = event.clientX;
  const { startWidth, minWidth, maxWidth, onWidth } = options;
  const initialWidth = clampResizablePanelWidth(startWidth, minWidth, maxWidth);

  handle.setPointerCapture?.(event.pointerId);
  document.body.style.cursor = "ew-resize";
  document.body.style.userSelect = "none";

  const onPointerMove = (moveEvent: PointerEvent) => {
    onWidth(
      clampResizablePanelWidth(initialWidth + startX - moveEvent.clientX, minWidth, maxWidth),
    );
  };
  const finish = () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    handle.releasePointerCapture?.(event.pointerId);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
  };

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
}

/**
 * Tracks the viewport width.
 *
 * A panel's maximum width depends on it, so reading it once at render leaves
 * the panel overflowing a window the user has since made narrower.
 */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}
