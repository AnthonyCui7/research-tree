import type { CSSProperties, ReactNode } from "react";
import { cx } from "../../lib/cx";
import {
  beginPanelResize,
  clampResizablePanelWidth,
  resizablePanelMaxWidth,
  useViewportWidth,
} from "../../lib/resizablePanel";

export const RIGHT_PANEL_MIN_WIDTH = 320;
export const RIGHT_PANEL_DEFAULT_WIDTH = 396;

type RightPanelProps = {
  label: string;
  width: number;
  onWidth: (width: number) => void;
  sidebarCollapsed: boolean;
  /** True while the panel plays its exit animation on the way out. */
  closing?: boolean;
  /** The assistant sits on the warmer canvas; inspector and history on white. */
  tone?: "surface" | "agent";
  children: ReactNode;
};

/**
 * The docked right column shared by the inspector, the assistant, and version
 * history. It takes width from the canvas rather than floating over it, which is
 * what makes the drag handle on its left edge meaningful.
 */
export function RightPanel({
  label,
  width,
  onWidth,
  sidebarCollapsed,
  closing = false,
  tone = "surface",
  children,
}: RightPanelProps) {
  const viewportWidth = useViewportWidth();
  const maxWidth = resizablePanelMaxWidth(sidebarCollapsed, viewportWidth);
  const resolvedWidth = clampResizablePanelWidth(width, RIGHT_PANEL_MIN_WIDTH, maxWidth);

  return (
    <aside
      className={cx(
        "relative flex min-h-0 w-[var(--right-panel-width)] flex-none flex-col border-l border-border",
        closing ? "animate-interface-right-exit" : "animate-interface-right-enter",
        tone === "agent" ? "bg-agent-canvas" : "bg-surface",
        "max-[900px]:absolute max-[900px]:inset-y-0 max-[900px]:right-0 max-[900px]:z-panel max-[900px]:w-[min(420px,100%)] max-[900px]:shadow-panel-left",
      )}
      style={{ "--right-panel-width": `${resolvedWidth}px` } as CSSProperties}
      aria-label={label}
    >
      <button
        className="group absolute top-0 bottom-0 -left-[5px] z-[6] flex w-2.5 cursor-col-resize touch-none items-center justify-center border-0 bg-transparent p-0 transition-[background-color] duration-150 hover:bg-[linear-gradient(90deg,transparent_40%,var(--color-accent-border)_40%,var(--color-accent-border)_60%,transparent_60%)] focus-visible:bg-[linear-gradient(90deg,transparent_40%,var(--color-accent-border)_40%,var(--color-accent-border)_60%,transparent_60%)] max-[900px]:hidden"
        type="button"
        aria-label={`Resize ${label.toLowerCase()}`}
        title="Drag to resize"
        onPointerDown={(event) =>
          beginPanelResize(event, {
            startWidth: resolvedWidth,
            minWidth: RIGHT_PANEL_MIN_WIDTH,
            maxWidth,
            onWidth,
          })
        }
      >
        <span
          className="block h-[38px] w-1 rounded-[2px] bg-node-border transition-[background-color] duration-150 group-hover:bg-accent group-focus-visible:bg-accent"
          aria-hidden="true"
        />
      </button>
      {children}
    </aside>
  );
}

/** The 44px header a titled panel mode shares. */
export function PanelHeader({
  title,
  icon,
  onClose,
  closeLabel,
}: {
  title: string;
  icon?: ReactNode;
  onClose: () => void;
  closeLabel: string;
}) {
  return (
    <header className="flex h-11 flex-none items-center gap-2 border-b border-hairline px-4">
      {icon ? <span className="flex-none text-text-secondary">{icon}</span> : null}
      <h2 className="m-0 min-w-0 flex-1 truncate text-[13px] font-semibold text-text-primary">
        {title}
      </h2>
      <PanelClose onClose={onClose} label={closeLabel} />
    </header>
  );
}

/** The dismissal every panel mode shares, header or not. */
export function PanelClose({
  onClose,
  label,
  className,
}: {
  onClose: () => void;
  label: string;
  className?: string;
}) {
  return (
    <button
      className={cx(
        "grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary",
        className,
      )}
      type="button"
      onClick={onClose}
      aria-label={label}
      title="Close"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 12 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        className="h-3 w-3"
      >
        <path d="M2 2L10 10M10 2L2 10" />
      </svg>
    </button>
  );
}
