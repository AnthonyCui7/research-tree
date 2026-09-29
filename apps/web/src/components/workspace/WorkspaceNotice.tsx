import { cx } from "../../lib/cx";
import { secondaryActionClass } from "../../lib/controlClasses";

type WorkspaceNoticeProps = {
  title: string;
  detail?: string;
  tone: "error" | "loading";
  actionLabel?: string;
  onAction?: () => void;
};

/** Loading and failure states for the main area, centred where the canvas would be. */
export function WorkspaceNotice({
  title,
  detail,
  tone,
  actionLabel,
  onAction,
}: WorkspaceNoticeProps) {
  return (
    <div className="grid h-full place-items-center p-6">
      <div
        className="flex max-w-[400px] flex-col items-center text-center"
        role={tone === "error" ? "alert" : "status"}
      >
        {tone === "loading" ? (
          <span
            className="mb-3 h-5 w-5 animate-progress-spin rounded-full border-2 border-accent-subtle border-t-accent"
            aria-hidden="true"
          />
        ) : null}
        <h2
          className={cx(
            "m-0",
            tone === "error" ? "text-15 font-semibold text-error" : "text-13 font-medium text-text-muted",
          )}
        >
          {title}
        </h2>
        {detail ? (
          <p className="mt-1.5 mb-0 text-13 text-text-secondary">{detail}</p>
        ) : null}
        {actionLabel && onAction ? (
          <button className={cx(secondaryActionClass, "mt-4")} type="button" onClick={onAction}>
            {actionLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
