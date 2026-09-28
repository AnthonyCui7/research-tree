import { cx } from "../../lib/cx";
import { kickerClass, secondaryActionClass } from "../../lib/controlClasses";
import { pluralize } from "../../lib/format";
import { AccountDialog } from "../account/AccountDialog";
import { OperationBadge } from "./ProposedRevision";
import { operationFieldChanges, type OperationChip } from "../../lib/proposedOperations";
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
      subtitle={`${pluralize(operations.length, "change")} to the current version`}
      onClose={onClose}
      footer={
        <button className={cx(secondaryActionClass, "ml-auto")} type="button" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="grid gap-5">
        {operations.map((operation, index) => {
          const chip = chips[index];
          const changes = operationFieldChanges(operation);
          return (
            <section
              className="border-b border-hairline pb-5 last:border-b-0 last:pb-0"
              key={chip?.key ?? `${index}:${operation.operation_type}`}
            >
              <div className="flex items-baseline gap-2 text-[13.5px] leading-[1.5]">
                {chip ? <OperationBadge chip={chip} /> : null}
                <span className="min-w-0 text-text-primary [overflow-wrap:anywhere]">
                  {chip?.name ? <strong className="font-semibold">{chip.name}</strong> : null}
                  {chip?.name && chip.detail ? " " : null}
                  {chip?.detail}
                </span>
              </div>
              {operation.rationale ? (
                <p className="mt-1.5 mb-0 text-[13px] leading-[1.55] text-text-secondary">
                  {operation.rationale}
                </p>
              ) : null}
              {changes.length > 0 ? (
                <dl className="mt-3 mb-0 grid gap-2.5">
                  {changes.map((change) => (
                    <div className="grid gap-0.5" key={change.label}>
                      <dt className={kickerClass}>{change.label}</dt>
                      <dd className="m-0 grid gap-0.5 text-[13px] leading-[1.55]">
                        {change.before !== null ? (
                          <span className="text-text-muted line-through [overflow-wrap:anywhere]">
                            {change.before}
                          </span>
                        ) : null}
                        {change.after !== null ? (
                          <span className="text-text-primary [overflow-wrap:anywhere]">{change.after}</span>
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
          <p className="m-0 text-[13px] text-text-secondary">
            This proposal carries no operation details to show.
          </p>
        ) : null}
      </div>
    </AccountDialog>
  );
}
