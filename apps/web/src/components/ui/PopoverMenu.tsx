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
 *
 * Focus moves into the menu when it opens and the arrow keys walk its items,
 * so it works from the keyboard as it does from the mouse. Escape, Tab and
 * choosing an item hand focus back to the control that opened it, before the
 * item's action runs: a dialog that action opens then records that control as
 * the place to return to.
 */
export function PopoverMenu({ anchor, onClose, label, width = 216, children }: PopoverMenuProps) {
  const { closing, dismiss } = useDismissAnimation(onClose, DROPDOWN_EXIT_MS);
  const menuRef = useRef<HTMLDivElement>(null);
  // The control focused as the menu opened is the one that opened it.
  const [opener] = useState(() => document.activeElement);
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
    (enabledItems(menuRef.current)[0] ?? menuRef.current)?.focus();
  }, []);

  function close() {
    if (opener instanceof HTMLElement) opener.focus();
    dismiss();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    // Nothing here is for the shell behind the menu.
    event.stopPropagation();
    const items = enabledItems(menuRef.current);
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      items[event.key === "Home" ? 0 : items.length - 1]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      // Menus are not tabbed through: Tab leaves, from the opener onwards.
      close();
    }
  }

  return (
    <div className="fixed inset-0 z-menu" onMouseDown={dismiss} role="presentation">
      <div
        ref={menuRef}
        className={cx(
          "absolute overflow-hidden rounded-[11px] border border-border bg-surface shadow-popover outline-none",
          // The menu grows out of the edge it is pinned to.
          anchor.align === "right" ? "origin-top-right" : "origin-top-left",
          // Choosing an item starts the exit; a second click landing during it
          // must not choose again, or a double click sent an edit twice.
          closing ? "pointer-events-none animate-dropdown-exit" : "animate-dropdown-enter",
        )}
        style={{
          top,
          [anchor.align]: Math.min(anchor.x, Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN)),
          width,
        }}
        role="menu"
        aria-label={label}
        // Focusable so a click on a header or divider keeps focus, and keys,
        // in the menu; the items are where focus actually rests.
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        // Choosing an item dismisses the menu, so items carry their action
        // alone. Capturing means the exit starts before the action runs, and
        // scoping it to menu items leaves headers and footers inert.
        onClickCapture={(event) => {
          if ((event.target as HTMLElement).closest('[role="menuitem"]')) {
            close();
          }
        }}
      >
        {children}
      </div>
    </div>
  );
}

function enabledItems(menu: HTMLElement | null): HTMLElement[] {
  return Array.from(menu?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? []);
}

type MenuItemProps = {
  children: ReactNode;
  tone?: "default" | "danger";
  icon?: ReactNode;
  title?: string;
  /** Quiet value shown at the end of the row — a state, not a second action. */
  trailing?: ReactNode;
} & (
  | { onClick: () => void; disabled?: boolean; href?: never }
  | { href: string; onClick?: never; disabled?: never }
);

/** One row of a menu: an action, or a link that opens in a new tab. */
export function MenuItem({ children, tone = "default", icon, title, trailing, ...item }: MenuItemProps) {
  const className = cx(
    "flex w-full items-center gap-[9px] rounded-[6px] border-0 bg-transparent px-[9px] py-[7px] text-left text-[13px] no-underline transition-[background-color] duration-150",
    tone === "danger"
      ? "text-error hover:bg-error-surface focus-visible:bg-error-surface"
      : "text-text-primary hover:bg-surface-subtle focus-visible:bg-surface-subtle",
  );
  const content = (
    <>
      {icon ? <span className="flex-none text-text-muted">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing ? (
        <span className="flex-none font-mono text-[11.5px] text-text-muted">{trailing}</span>
      ) : null}
    </>
  );
  // Items are reached with the arrow keys, never with Tab.
  if (item.href !== undefined) {
    return (
      <a
        className={cx(className, "hover:no-underline")}
        href={item.href}
        target="_blank"
        rel="noreferrer"
        role="menuitem"
        tabIndex={-1}
        title={title}
      >
        {content}
      </a>
    );
  }
  return (
    <button
      className={cx(className, "disabled:cursor-not-allowed disabled:bg-transparent disabled:text-text-muted")}
      type="button"
      role="menuitem"
      tabIndex={-1}
      onClick={item.onClick}
      disabled={item.disabled}
      title={title}
    >
      {content}
    </button>
  );
}

export function MenuSection({ children }: { children: ReactNode }) {
  return <div className="border-b border-hairline-soft p-1.5 last:border-b-0">{children}</div>;
}
