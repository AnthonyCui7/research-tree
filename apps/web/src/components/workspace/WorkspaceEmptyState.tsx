import { cx } from "../../lib/cx";
import { primaryActionClass } from "../../lib/controlClasses";

type WorkspaceEmptyStateProps = {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
  actionLabel?: string;
  onAction?: () => void;
};

export function WorkspaceEmptyState({
  title,
  detail,
  tone = "neutral",
  actionLabel,
  onAction,
}: WorkspaceEmptyStateProps) {
  return (
    <div className="grid h-full place-items-center p-6">
      <div className="w-full max-w-[440px] rounded-md border border-border bg-surface p-6">
          <h2 className={cx("mt-0 mb-1.5 text-[17px] tracking-normal", tone === "error" && "text-error")}>{title}</h2>
          <p className="m-0 leading-normal text-text-secondary">{detail}</p>
        {actionLabel && onAction ? <button className={cx(primaryActionClass, "mt-[18px]")} type="button" onClick={onAction}>{actionLabel}</button> : null}
      </div>
    </div>
  );
}
