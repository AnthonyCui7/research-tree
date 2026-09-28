import { cx } from "../../lib/cx";
import { secondaryActionClass } from "../../lib/controlClasses";

type WorkspaceNoticeProps = {
  title: string;
  detail?: string;
  tone?: "neutral" | "error";
  actionLabel?: string;
  onAction?: () => void;
};

/** Loading and failure states for the main area, centred where the canvas would be. */
export function WorkspaceNotice({
  title,
  detail,
  tone = "neutral",
  actionLabel,
  onAction,
}: WorkspaceNoticeProps) {
  return (
    <div className="grid h-full place-items-center p-6">
      <div
        className="flex max-w-[400px] flex-col items-center text-center"
        role={tone === "error" ? "alert" : "status"}
      >
        <h2
          className={cx(
            "m-0 text-[15px] font-semibold tracking-[-0.01em]",
            tone === "error" ? "text-error" : "text-text-secondary",
          )}
        >
          {title}
        </h2>
        {detail ? (
          <p className="mt-1.5 mb-0 text-[13px] leading-[1.6] text-text-secondary">{detail}</p>
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
