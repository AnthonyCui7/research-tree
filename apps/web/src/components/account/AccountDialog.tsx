import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { useModalDialog } from "../../lib/modalDialog";
import { CloseIcon } from "../ui/icons";

type AccountDialogProps = {
  title: string;
  /** One line under the title saying what the screen is for. */
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
};

/**
 * The shell every account screen shares — settings, API keys, help, bug reports.
 * They differ only in what they hold, so the chrome, the dismissal and the
 * entrance and exit are written once here.
 */
export function AccountDialog({ title, subtitle, onClose, children, footer }: AccountDialogProps) {
  const { ref, closing, dismiss } = useModalDialog(DIALOG_EXIT_MS);

  return (
    <dialog
      ref={ref}
      className={cx(
        "fixed inset-0 m-0 grid h-full max-h-none w-full max-w-none place-items-center border-0 bg-transparent p-6 text-text-primary outline-none",
        closing ? "[&::backdrop]:animate-backdrop-exit" : "[&::backdrop]:animate-backdrop-enter",
      )}
      // Focusable, so a click on its own chrome cannot send focus to the inert
      // page behind and keys always arrive here. Never a tab stop, so no ring.
      tabIndex={-1}
      aria-labelledby="account-dialog-title"
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={(event) => {
        // The shell's shortcuts stop at a modal.
        event.stopPropagation();
        if (event.key !== "Escape") return;
        event.preventDefault();
        dismiss();
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      <section
        className={cx(
          "flex max-h-[min(620px,calc(100vh-48px))] w-[480px] max-w-full flex-col overflow-hidden rounded-[14px] bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
      >
        <header className="flex flex-none items-start gap-2 border-b border-hairline px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <h2
              className="m-0 text-[15px] font-bold tracking-[-0.01em] text-text-primary"
              id="account-dialog-title"
            >
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-0.5 mb-0 text-[12.5px] leading-[1.5] text-text-secondary">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
            type="button"
            onClick={dismiss}
            aria-label={`Close ${title}`}
            title="Close"
          >
            <CloseIcon className="h-3 w-3" />
          </button>
        </header>

        <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-5 pt-[18px] pb-5">
          {children}
        </div>

        {footer ? (
          <div className="flex flex-none items-center gap-2 border-t border-hairline bg-surface-muted px-5 py-3.5">
            {footer}
          </div>
        ) : null}
      </section>
    </dialog>
  );
}

/** A labelled block of one screen, so every screen reads the same way. */
export function AccountSection({
  title,
  detail,
  children,
}: {
  title: string;
  detail?: string;
  children?: ReactNode;
}) {
  return (
    <section className="mt-[22px] first:mt-0">
      <h3 className="m-0 text-[13.5px] font-bold text-text-primary">{title}</h3>
      {detail ? (
        <p className="mt-[3px] mb-0 text-[12.5px] leading-[1.55] text-text-secondary">{detail}</p>
      ) : null}
      {children ? <div className="mt-2.5">{children}</div> : null}
    </section>
  );
}

/** The strip that says a control is built but not yet connected to anything. */
export function AccountNotice({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 mb-0 rounded-lg border border-border bg-surface-subtle px-3.5 py-2.5 text-[12px] leading-[1.55] text-text-secondary">
      {children}
    </p>
  );
}
