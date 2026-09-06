import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DROPDOWN_EXIT_MS, useDismissAnimation } from "../../lib/animation";

export type MenuAnchor = {
  /** Viewport coordinates of the edge the menu is pinned to. */
  x: number;
  y: number;
  align: "left" | "right";
};

/** Reads the anchor for a menu from the control that opened it. */
export function anchorFromEvent(element: HTMLElement, align: MenuAnchor["align"]): MenuAnchor {
  const rect = element.getBoundingClientRect();
  return {
    x: align === "right" ? window.innerWidth - rect.right : rect.left,
    y: rect.bottom + 6,
    align,
  };
}

const VIEWPORT_MARGIN = 12;

type PopoverMenuProps = {
  anchor: MenuAnchor;
  onClose: () => void;
  label: string;
  width?: number;
  children: ReactNode;
};

/**
 * A menu pinned to the viewport rather than to its container, so it survives the
 * scrolling, clipped panels it is opened from. The full-screen layer beneath it
 * is what closes it on an outside click.
 */
export function PopoverMenu({ anchor, onClose, label, width = 216, children }: PopoverMenuProps) {
  const { closing, dismiss } = useDismissAnimation(onClose, DROPDOWN_EXIT_MS);
  const menuRef = useRef<HTMLDivElement>(null);
  // A menu opened near the bottom edge — a right-click low on the canvas — is
  // pulled up once its height is known, so its last item is not off screen.
  const [top, setTop] = useState(() => Math.min(anchor.y, window.innerHeight - VIEWPORT_MARGIN));

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const overflow = anchor.y + menu.offsetHeight + VIEWPORT_MARGIN - window.innerHeight;
    setTop(overflow > 0 ? Math.max(VIEWPORT_MARGIN, anchor.y - overflow) : anchor.y);
  }, [anchor.y, children]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        dismiss();
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dismiss]);

  return (
    <div className="fixed inset-0 z-menu" onMouseDown={dismiss} role="presentation">
      <div
        ref={menuRef}
        className={cx(
          "absolute overflow-hidden rounded-[11px] border border-border bg-surface shadow-popover",
          // The menu grows out of the edge it is pinned to.
          anchor.align === "right" ? "origin-top-right" : "origin-top-left",
          closing ? "animate-dropdown-exit" : "animate-dropdown-enter",
        )}
        style={{
          top,
          [anchor.align]: Math.min(anchor.x, Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN)),
          width,
        }}
        role="menu"
        aria-label={label}
        onMouseDown={(event) => event.stopPropagation()}
        // Choosing an item dismisses the menu, so items carry their action
        // alone. Capturing means the exit starts before the action runs, and
        // scoping it to menu items leaves headers and footers inert.
        onClickCapture={(event) => {
          if ((event.target as HTMLElement).closest('[role="menuitem"]')) {
            dismiss();
          }
        }}
      >
        {children}
      </div>
    </div>
  );
}

type MenuItemProps = {
  children: ReactNode;
  onClick: () => void;
  tone?: "default" | "danger";
  icon?: ReactNode;
  disabled?: boolean;
  title?: string;
  /** Quiet value shown at the end of the row — a state, not a second action. */
  trailing?: ReactNode;
};

export function MenuItem({
  children,
  onClick,
  tone = "default",
  icon,
  disabled,
  title,
  trailing,
}: MenuItemProps) {
  return (
    <button
      className={cx(
        "flex w-full items-center gap-[9px] rounded-[6px] border-0 bg-transparent px-[9px] py-[7px] text-left text-[13px] transition-[background-color] duration-150 disabled:cursor-not-allowed disabled:text-text-muted",
        tone === "danger"
          ? "text-error enabled:hover:bg-error-surface"
          : "text-text-primary enabled:hover:bg-surface-subtle",
      )}
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {icon ? <span className="flex-none text-text-muted">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing ? (
        <span className="flex-none font-mono text-[11.5px] text-text-muted">{trailing}</span>
      ) : null}
    </button>
  );
}

export function MenuSection({ children }: { children: ReactNode }) {
  return <div className="border-b border-hairline-soft p-1.5 last:border-b-0">{children}</div>;
}
