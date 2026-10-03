import { useState } from "react";
import { messageFrom } from "../../lib/apiError";
import { cx } from "../../lib/cx";
import {
  errorNoticeClass,
  pillClass,
  primaryActionClass,
  secondaryActionClass,
  textAreaClass,
  textInputClass,
} from "../../lib/controlClasses";
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
 * it kept no copy — there is no bug_reports table yet. Sending it anyway is what makes
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
        {/* Centred between the title and the footer's rule: the header leaves
            32px and the body 16px, so 16px more below evens them. */}
        <div className="grid justify-items-center px-4 pt-8 pb-12 text-center">
          <span
            className="grid h-10 w-10 place-items-center rounded-full bg-accent-subtle text-accent-deep"
            aria-hidden="true"
          >
            <CheckIcon className="size-4" />
          </span>
          <p className="mt-4 mb-0 text-14 font-medium text-text-primary text-trim">Report sent.</p>
          <p className="mt-2 mb-0 max-w-[42ch] text-13 text-text-muted text-trim">
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
          <div className="flex flex-wrap gap-2">
            {AREAS.map((option) => (
              <button
                className={cx(
                  pillClass,
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
            className={cx(textAreaClass, "mt-2 resize-y")}
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
          <p className={cx(errorNoticeClass, "mt-2 mb-0")} role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </AccountDialog>
  );
}
