import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DROPDOWN_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { CheckIcon } from "./icons";

export type MenuAnchor = {
  /** Viewport coordinates of the edge the menu is pinned to. */
  x: number;
  y: number;
  align: "left" | "right";
  /** Opens upward from `y`, for a control at the foot of the screen. */
  above?: boolean;
};

/** Reads the anchor for a menu from the control that opened it. */
export function anchorFromEvent(
  element: HTMLElement,
  align: MenuAnchor["align"],
  above = false,
): MenuAnchor {
  const rect = element.getBoundingClientRect();
  return {
    x: align === "right" ? window.innerWidth - rect.right : rect.left,
    y: above ? rect.top - 6 : rect.bottom + 6,
    align,
    above,
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
    if (anchor.above) {
      setTop(Math.max(VIEWPORT_MARGIN, anchor.y - menu.offsetHeight));
      return;
    }
    const overflow = anchor.y + menu.offsetHeight + VIEWPORT_MARGIN - window.innerHeight;
    setTop(overflow > 0 ? Math.max(VIEWPORT_MARGIN, anchor.y - overflow) : anchor.y);
  }, [anchor.above, anchor.y, children]);

  // Focus starts on the first item, and goes back there when the body is
  // swapped in place (a list of destinations replacing the actions) and took
  // the focused item with it. Not while leaving: focus is on its way back to
  // the opener then.
  useEffect(() => {
    const menu = menuRef.current;
    if (closing || !menu || menu.contains(document.activeElement)) return;
    (enabledItems(menu)[0] ?? menu).focus();
  }, [children, closing]);

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
          "absolute overflow-hidden rounded-xl border border-border bg-surface shadow-popover outline-none",
          // The menu grows out of the corner it is pinned to.
          anchor.above
            ? anchor.align === "right"
              ? "origin-bottom-right"
              : "origin-bottom-left"
            : anchor.align === "right"
              ? "origin-top-right"
              : "origin-top-left",
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
        // scoping it to menu items leaves headers and footers inert. An item
        // that changes the menu's own body keeps it open.
        onClickCapture={(event) => {
          const item = (event.target as HTMLElement).closest(MENU_ITEM_SELECTOR);
          if (item && !item.hasAttribute("data-keeps-menu-open")) {
            close();
          }
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Plain items and one-of-several choices alike. */
const MENU_ITEM_SELECTOR = '[role="menuitem"], [role="menuitemradio"]';

function enabledItems(menu: HTMLElement | null): HTMLElement[] {
  return Array.from(menu?.querySelectorAll<HTMLElement>(MENU_ITEM_SELECTOR) ?? []).filter(
    (item) => !(item as HTMLButtonElement).disabled,
  );
}

type MenuItemProps = {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: "default" | "danger";
  icon?: ReactNode;
  title?: string;
  /** Quiet value shown at the end of the row — a state, not a second action. */
  trailing?: ReactNode;
  /** A second, quieter line under the label. */
  description?: string;
  /** Makes the row one choice of several, marked when it is the current one. */
  checked?: boolean;
  /** The row changes what the menu shows rather than choosing something. */
  keepsMenuOpen?: boolean;
};

/** One row of a menu. Rows are reached with the arrow keys, never with Tab. */
export function MenuItem({
  children,
  onClick,
  disabled,
  tone = "default",
  icon,
  title,
  trailing,
  description,
  checked,
  keepsMenuOpen = false,
}: MenuItemProps) {
  const choice = checked !== undefined;
  return (
    <button
      className={cx(
        "flex w-full items-center gap-2.5 rounded-md border-0 bg-transparent px-2.5 text-left text-13 transition-[background-color] duration-150 disabled:cursor-not-allowed disabled:bg-transparent disabled:text-text-muted",
        description ? "py-1.5" : "h-8",
        tone === "danger"
          ? "text-error hover:bg-error-surface focus-visible:bg-error-surface"
          : "text-text-primary hover:bg-surface-subtle focus-visible:bg-surface-subtle",
      )}
      type="button"
      role={choice ? "menuitemradio" : "menuitem"}
      aria-checked={choice ? checked : undefined}
      tabIndex={-1}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-keeps-menu-open={keepsMenuOpen || undefined}
    >
      {icon ? <span className="flex-none text-text-muted">{icon}</span> : null}
      <span className="min-w-0 flex-1">
        <span className="block truncate">{children}</span>
        {description ? (
          <span className="block truncate text-12 text-text-muted">{description}</span>
        ) : null}
      </span>
      {trailing ? (
        <span className="flex-none font-mono text-12 text-text-muted">{trailing}</span>
      ) : null}
      {checked ? <CheckIcon className="size-4 flex-none text-accent" /> : null}
    </button>
  );
}

export function MenuSection({ children }: { children: ReactNode }) {
  return <div className="border-b border-hairline p-1.5 last:border-b-0">{children}</div>;
}
