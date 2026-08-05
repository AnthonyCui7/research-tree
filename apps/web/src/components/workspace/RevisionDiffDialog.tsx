import { cx } from "../../lib/cx";
import { secondaryActionClass } from "../../lib/controlClasses";
import { AccountDialog } from "../account/AccountDialog";
import {
  operationFieldChanges,
  type ChipTone,
  type OperationChip,
} from "../../lib/proposedOperations";
import type { ProposedOperation } from "../../lib/types";

type RevisionDiffDialogProps = {
  operations: ProposedOperation[];
  /** Derived once by the card, so the dialog can never disagree with it. */
  chips: OperationChip[];
  onClose: () => void;
};

/**
 * The full proposal, one section per operation: the same chip header the card
 * shows, the model's rationale, and a changed-field ledger. A ledger, not a
 * text diff — values are summarized, never recursed into.
 */
export function RevisionDiffDialog({ operations, chips, onClose }: RevisionDiffDialogProps) {
  return (
    <AccountDialog
      title="Proposed revision"
      subtitle={`${operations.length} operation${operations.length === 1 ? "" : "s"} against the current version`}
      onClose={onClose}
      footer={
        <button className={cx(secondaryActionClass, "ml-auto")} type="button" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="grid gap-4">
        {operations.map((operation, index) => {
          const chip = chips[index];
          const changes = operationFieldChanges(operation);
          return (
            <section
              className="border-b border-hairline pb-4 last:border-b-0 last:pb-0"
              key={chip?.key ?? `${index}:${operation.operation_type}`}
            >
              <div className="flex gap-2 text-[12.5px] leading-[1.5]">
                {chip ? (
                  <span
                    className={cx(
                      "mt-px flex-none rounded-[5px] px-[7px] text-[10px] font-semibold uppercase",
                      badgeToneClass(chip.tone),
                    )}
                  >
                    {chip.badge}
                  </span>
                ) : null}
                <span className="min-w-0 text-text-primary">
                  {chip?.name ? <strong className="font-semibold">{chip.name}</strong> : null}
                  {chip?.name && chip.detail ? " " : null}
                  {chip?.detail}
                </span>
              </div>
              {operation.rationale ? (
                <p className="mt-1.5 mb-0 text-xs leading-[1.55] text-text-muted">
                  {operation.rationale}
                </p>
              ) : null}
              {changes.length > 0 ? (
                <dl className="mt-2.5 mb-0 grid gap-2">
                  {changes.map((change) => (
                    <div className="grid gap-0.5" key={change.label}>
                      <dt className="text-[10.5px] font-semibold tracking-[0.04em] text-text-muted uppercase">
                        {change.label}
                      </dt>
                      <dd className="m-0 grid gap-0.5 text-xs leading-[1.55]">
                        {change.before !== null ? (
                          <span className="text-text-muted line-through [overflow-wrap:anywhere]">
                            {change.before}
                          </span>
                        ) : null}
                        {change.after !== null ? (
                          <span className="text-text-primary [overflow-wrap:anywhere]">
                            {change.after}
                          </span>
                        ) : null}
                        {change.before === null && change.after === null ? (
                          <span className="text-text-muted">(cleared)</span>
                        ) : null}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </section>
          );
        })}
        {operations.length === 0 ? (
          <p className="m-0 text-xs text-text-secondary">
            This proposal carries no operation details to show.
          </p>
        ) : null}
      </div>
    </AccountDialog>
  );
}

function badgeToneClass(tone: ChipTone): string {
  if (tone === "add") return "bg-accent-subtle text-accent-deep";
  if (tone === "remove") return "bg-error-surface text-error";
  return "bg-surface-subtle text-text-secondary";
}
