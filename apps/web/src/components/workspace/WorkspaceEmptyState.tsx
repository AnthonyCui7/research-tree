import { useState } from "react";
import { cx } from "../../lib/cx";
import { primaryActionClass, secondaryActionClass } from "../../lib/controlClasses";
import { ArrowRightIcon } from "../ui/icons";

type WorkspaceEmptyStateProps = {
  onCreate: (topic: string) => void;
  disabled?: boolean;
};

/**
 * First launch: the creator is the empty state. No decorative filler beyond a
 * miniature of the thing being offered.
 */
export function WorkspaceEmptyState({ onCreate, disabled = false }: WorkspaceEmptyStateProps) {
  const [topic, setTopic] = useState("");

  return (
    <div className="dot-field-bg grid h-full place-items-center overflow-y-auto p-6">
      <div className="flex w-full max-w-[560px] flex-col items-center">
        <TreeSketch />
        <h2 className="mt-[22px] mb-0 text-[19px] font-bold tracking-[-0.015em] text-text-primary">
          Map your first field
        </h2>
        <p className="mt-2 mb-0 max-w-[46ch] text-center text-[13px] leading-[1.62] text-text-secondary">
          Give Research Tree a topic and it drafts an editable map of the literature: branches, key
          papers, and reading paths.
        </p>
        <form
          className="mt-5 flex w-[min(440px,100%)] items-center gap-2 rounded-[11px] border-[1.5px] border-border-strong bg-surface py-[9px] pr-2 pl-3.5 transition-[border-color,box-shadow] duration-150 focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--color-accent-subtle)]"
          onSubmit={(event) => {
            event.preventDefault();
            if (topic.trim()) onCreate(topic.trim());
          }}
        >
          <input
            className="min-w-0 flex-1 border-0 bg-transparent p-0 text-[13.5px] text-text-primary outline-0 placeholder:text-text-muted"
            type="text"
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="e.g. Speculative decoding"
            maxLength={240}
            aria-label="Research topic"
            disabled={disabled}
          />
          <button
            className={cx(primaryActionClass, "flex-none px-3.5 py-[7px] text-[12.5px]")}
            type="submit"
            disabled={disabled || !topic.trim()}
          >
            Map it
            <ArrowRightIcon className="h-3 w-3" />
          </button>
        </form>
        <p className="mt-4 mb-0 text-[11px] text-text-muted">You confirm the topic before anything runs.</p>
      </div>
    </div>
  );
}

function TreeSketch() {
  return (
    <div className="flex items-center gap-2.5 opacity-90" aria-hidden="true">
      <span className="h-10 w-14 rounded-md border border-node-border bg-surface" />
      <svg width="34" height="40" viewBox="0 0 34 40" fill="none">
        <path
          d="M0 20 C14 20 14 6 28 6 M0 20 C14 20 14 34 28 34"
          stroke="var(--color-edge-line)"
          strokeWidth="1.2"
        />
      </svg>
      <span className="flex flex-col gap-2">
        <span className="h-[26px] w-14 rounded-[7px] border border-[#d5dfd4] bg-[#edf2ec]" />
        <span className="h-[26px] w-14 rounded-[7px] border border-[#e3dcc8] bg-[#f5f1e7]" />
      </span>
    </div>
  );
}

type WorkspaceNoticeProps = {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
  actionLabel?: string;
  onAction?: () => void;
};

/** Loading and failure states for the canvas area. */
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
        className={cx(
          "w-full max-w-[420px] rounded-xl border bg-surface px-5 py-[18px] shadow-node",
          tone === "error" ? "border-error-border" : "border-border",
        )}
        role={tone === "error" ? "alert" : "status"}
      >
        <h2
          className={cx(
            "m-0 text-[15px] font-bold tracking-[-0.01em]",
            tone === "error" ? "text-error" : "text-text-primary",
          )}
        >
          {title}
        </h2>
        <p className="mt-1.5 mb-0 text-[13px] leading-[1.6] text-text-secondary">{detail}</p>
        {actionLabel && onAction ? (
          <button className={cx(secondaryActionClass, "mt-3.5")} type="button" onClick={onAction}>
            {actionLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
