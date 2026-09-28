import type { CSSProperties, ReactNode } from "react";
import { cx } from "../../lib/cx";
import { iconButtonClass } from "../../lib/controlClasses";
import { CloseIcon } from "../ui/icons";
import {
  beginPanelResize,
  clampResizablePanelWidth,
  resizablePanelMaxWidth,
  useViewportWidth,
} from "../../lib/resizablePanel";

export const RIGHT_PANEL_MIN_WIDTH = 320;
export const RIGHT_PANEL_DEFAULT_WIDTH = 420;

type RightPanelProps = {
  label: string;
  width: number;
  onWidth: (width: number) => void;
  sidebarCollapsed: boolean;
  /** True while the panel plays its exit animation on the way out. */
  closing?: boolean;
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
  children,
}: RightPanelProps) {
  const viewportWidth = useViewportWidth();
  const maxWidth = resizablePanelMaxWidth(sidebarCollapsed, viewportWidth);
  const resolvedWidth = clampResizablePanelWidth(width, RIGHT_PANEL_MIN_WIDTH, maxWidth);

  return (
    <aside
      className={cx(
        "relative flex min-h-0 w-[var(--right-panel-width)] flex-none flex-col border-l border-hairline bg-surface",
        closing ? "animate-interface-right-exit" : "animate-interface-right-enter",
        "max-[900px]:absolute max-[900px]:inset-y-0 max-[900px]:right-0 max-[900px]:z-panel max-[900px]:w-[min(440px,100%)] max-[900px]:shadow-panel-left",
      )}
      style={{ "--right-panel-width": `${resolvedWidth}px` } as CSSProperties}
      aria-label={label}
    >
      <button
        className="group absolute top-0 bottom-0 -left-[5px] z-[6] flex w-2.5 cursor-col-resize touch-none justify-center border-0 bg-transparent p-0 max-[900px]:hidden"
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
          className="block h-full w-0.5 bg-transparent transition-[background-color] duration-150 group-hover:bg-accent-border group-focus-visible:bg-accent"
          aria-hidden="true"
        />
      </button>
      {children}
    </aside>
  );
}

/** The header every panel mode shares: what it shows, its actions, and a close. */
export function PanelHeader({
  title,
  actions,
  onClose,
  closeLabel,
}: {
  title: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  closeLabel: string;
}) {
  return (
    <header className="flex h-12 flex-none items-center gap-1 border-b border-hairline pr-2 pl-5">
      <h2 className="m-0 min-w-0 flex-1 truncate text-[13.5px] font-semibold text-text-primary">
        {title}
      </h2>
      {actions}
      <button className={iconButtonClass} type="button" onClick={onClose} aria-label={closeLabel} title="Close">
        <CloseIcon className="h-3 w-3" />
      </button>
    </header>
  );
}
