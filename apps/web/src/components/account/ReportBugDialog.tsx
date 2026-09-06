import { useState } from "react";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import { primaryActionClass, secondaryActionClass, textInputClass } from "../../lib/controlClasses";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { AccountDialog, AccountNotice, AccountSection } from "./AccountDialog";
import { CheckIcon } from "../ui/icons";

/** The parts of the product a report can be filed against. */
const AREAS = [
  { id: "workspace-build", label: "Building a workspace" },
  { id: "tree", label: "The tree canvas" },
  { id: "assistant", label: "The assistant" },
  { id: "history", label: "Version history" },
  { id: "general", label: "Something else" },
] as const;

/**
 * A report goes to the server, which writes it to its log and says plainly that
 * it kept no copy — there is no database yet. Sending it anyway is what makes
 * the path real: when a store exists, only the route's body changes.
 */
export function ReportBugDialog({ onClose }: { onClose: () => void }) {
  const [summary, setSummary] = useState("");
  const [details, setDetails] = useState("");
  const [area, setArea] = useState<string>("general");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!summary.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await repositoryWorkspaceGateway.reportBug(summary.trim(), details, area);
      setSent(result.detail);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <AccountDialog
        title="Report a bug"
        onClose={onClose}
        footer={
          <button className={cx(secondaryActionClass, "ml-auto")} type="button" onClick={onClose}>
            Done
          </button>
        }
      >
        <div className="grid justify-items-center px-4 py-12 text-center">
          <span
            className="grid h-9 w-9 place-items-center rounded-full bg-accent-subtle text-accent-deep"
            aria-hidden="true"
          >
            <CheckIcon className="h-4 w-4" />
          </span>
          <p className="mt-3 mb-0 text-[13px] leading-[1.6] text-text-primary">Report sent.</p>
          <p className="mt-1.5 mb-0 max-w-[42ch] text-[12.5px] leading-[1.6] text-text-muted">
            {sent}
          </p>
        </div>
      </AccountDialog>
    );
  }

  return (
    <AccountDialog
      title="Report a bug"
      onClose={onClose}
      footer={
        <button
          className={cx(primaryActionClass, "ml-auto")}
          type="submit"
          form="bug-report-form"
          disabled={busy || !summary.trim()}
        >
          {busy ? "Sending…" : "Send report"}
        </button>
      }
    >
      <form
        id="bug-report-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <AccountSection title="Where">
          <div className="flex flex-wrap gap-1.5">
            {AREAS.map((option) => (
              <button
                className={cx(
                  "rounded-[20px] border px-[11px] py-1 text-[12px] transition-[background-color,border-color,color] duration-150",
                  area === option.id
                    ? "border-accent-border bg-accent-subtle text-accent-deep"
                    : "border-border bg-surface text-text-secondary hover:border-border-strong hover:text-text-primary",
                )}
                key={option.id}
                type="button"
                aria-pressed={area === option.id}
                onClick={() => setArea(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </AccountSection>

        <AccountSection title="What happened">
          <input
            className={textInputClass}
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            placeholder="One line: what went wrong"
            maxLength={200}
            aria-label="Summary"
          />
          <textarea
            className={cx(textInputClass, "mt-2 min-h-[110px] resize-y")}
            value={details}
            onChange={(event) => setDetails(event.target.value)}
            placeholder="What you were doing, what you expected, and what happened instead."
            maxLength={8000}
            rows={4}
            aria-label="Details"
          />
        </AccountSection>

        <AccountNotice>Reports are written to the server log for now.</AccountNotice>

        {error ? (
          <p
            className="mt-3 mb-0 rounded-lg border border-error-border bg-error-surface px-3.5 py-3 text-xs leading-[1.5] text-error"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </form>
    </AccountDialog>
  );
}
