import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { useModalDialog } from "../../lib/modalDialog";
import { iconButtonClass, plainNoticeClass } from "../../lib/controlClasses";
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
 * The shell every account screen shares — settings, API keys, help, bug reports
 * — and the revision diff. They differ only in what they hold, so the chrome,
 * the dismissal and the entrance and exit are written once here.
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
          "flex max-h-[min(640px,calc(100vh-48px))] w-[500px] max-w-full flex-col overflow-hidden rounded-2xl bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
      >
        <header className="flex flex-none items-start gap-3 p-6">
          <div className="min-w-0 flex-1">
            <h2
              className="m-0 text-17 font-semibold text-text-primary"
              id="account-dialog-title"
            >
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-1 mb-0 text-13 text-text-secondary">{subtitle}</p>
            ) : null}
          </div>
          {/* Centred on the title's line, with its glyph on the 24px edge the
              title and the body share. */}
          <button
            className={cx(iconButtonClass, "-mt-1 -mr-2.5")}
            type="button"
            onClick={dismiss}
            aria-label={`Close ${title}`}
            title="Close"
          >
            <CloseIcon className="size-3" />
          </button>
        </header>

        <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-6 pb-6">{children}</div>

        {footer ? (
          <div className="box-content flex h-16 flex-none items-center gap-2 border-t border-hairline px-6">
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
    <section className="mt-6 first:mt-0">
      <h3 className="m-0 text-14 font-semibold text-text-primary">{title}</h3>
      {detail ? (
        <p className="mt-1 mb-0 text-13 text-text-secondary">{detail}</p>
      ) : null}
      {children ? <div className="mt-3">{children}</div> : null}
    </section>
  );
}

/** The strip that says what a control will and will not do on this server. */
export function AccountNotice({ children }: { children: ReactNode }) {
  return <p className={cx(plainNoticeClass, "mt-3 mb-0")}>{children}</p>;
}
