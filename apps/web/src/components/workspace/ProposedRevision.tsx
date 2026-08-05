import { useMemo, useState } from "react";
import { cx } from "../../lib/cx";
import { compactActionClass, compactPrimaryActionClass } from "../../lib/controlClasses";
import {
  fallbackChipsFromDiffSummary,
  nameLookupFromTree,
  operationChips,
  operationsFromResult,
  skepticNotes,
  type ChipTone,
  type OperationChip,
} from "../../lib/proposedOperations";
import { RevisionDiffDialog } from "./RevisionDiffDialog";
import type { AgentRunResult, TreeViewModel } from "../../lib/types";

const MAX_CHIP_ROWS = 8;

type ProposedRevisionProps = {
  result: AgentRunResult;
  /** Null while the workspace loads; chips then fall back to raw ids. */
  tree: TreeViewModel | null;
  busy: boolean;
  onDecide: (choice: "approve" | "reject") => Promise<void>;
  /** Set when the card was restored from a stored review after a reload. */
  restoredUserMessage?: string | null;
};

/**
 * The review card: one row per proposed operation, the skeptic's objections,
 * and the approve/reject decision. Operations come from the run's
 * interrupt_payload — the same object stored on the review record, so a card
 * restored after a reload renders identically.
 */
export function ProposedRevision({
  result,
  tree,
  busy,
  onDecide,
  restoredUserMessage,
}: ProposedRevisionProps) {
  const [diffOpen, setDiffOpen] = useState(false);
  const operations = operationsFromResult(result.interrupt_payload);
  const chips = useMemo(() => {
    if (operations.length > 0) {
      return operationChips(operations, nameLookupFromTree(tree));
    }
    return fallbackChipsFromDiffSummary(result.diff_summary);
  }, [operations, result.diff_summary, tree]);
  const notes = skepticNotes(result.interrupt_payload);
  const count = operations.length > 0 ? operations.length : operationCount(result.diff_summary);
  const paperDelta = paperCountDelta(result.diff_summary);
  const visibleChips = chips.slice(0, MAX_CHIP_ROWS);
  const hiddenChipCount = chips.length - visibleChips.length;

  return (
    <div className="overflow-hidden rounded-[11px] border border-border bg-surface">
      <div className="flex items-center gap-[7px] border-b border-hairline-soft px-[13px] py-2.5">
        <span className="h-[7px] w-[7px] flex-none rounded-full bg-accent" aria-hidden="true" />
        <span className="flex-1 text-xs font-semibold text-text-primary">Proposed revision</span>
        <span className="text-[10.5px] text-text-muted">
          {count === null ? "needs approval" : `${count} op${count === 1 ? "" : "s"} · needs approval`}
        </span>
      </div>
      <div className="flex flex-col gap-2 px-[13px] py-[11px]">
        {restoredUserMessage ? (
          <p className="m-0 text-[11.5px] leading-[1.5] text-text-muted">
            In response to: “{restoredUserMessage}”
          </p>
        ) : null}
        {visibleChips.length > 0 ? (
          visibleChips.map((chip) => (
            <div className="flex gap-2 text-xs leading-[1.5]" key={chip.key}>
              <span
                className={cx(
                  "mt-px flex-none rounded-[5px] px-[7px] text-[10px] font-semibold uppercase",
                  badgeToneClass(chip.tone),
                )}
              >
                {chip.badge}
              </span>
              <span className="min-w-0 truncate text-text-secondary" title={chipTitle(chip)}>
                {chip.name ? (
                  <strong className="font-semibold text-text-primary">{chip.name}</strong>
                ) : null}
                {chip.name && chip.detail ? " " : null}
                {chip.detail}
              </span>
            </div>
          ))
        ) : (
          <p className="m-0 text-xs leading-[1.5] text-text-secondary">
            A structural revision is ready for your review.
          </p>
        )}
        {hiddenChipCount > 0 ? (
          <p className="m-0 text-[11.5px] leading-[1.5] text-text-muted">
            +{hiddenChipCount} more — View diff shows all
          </p>
        ) : null}
        {paperDelta || notes.length > 0 ? (
          <div className="grid gap-1.5 border-t border-dashed border-hairline pt-2">
            {paperDelta ? (
              <p className="m-0 text-[11.5px] leading-[1.55] text-text-muted">
                Visible papers: {paperDelta.before} → {paperDelta.after}
              </p>
            ) : null}
            {notes.map((note, index) => (
              <p className="m-0 text-[11.5px] leading-[1.55] text-warning" key={`${index}:${note}`}>
                <strong className="font-semibold">Skeptic:</strong> {note}
              </p>
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-2 border-t border-hairline-soft bg-surface-muted px-[13px] py-2.5">
        <button
          className={compactPrimaryActionClass}
          type="button"
          disabled={busy}
          onClick={() => void onDecide("approve")}
        >
          Approve &amp; apply
        </button>
        <button
          className={compactActionClass}
          type="button"
          disabled={busy}
          onClick={() => void onDecide("reject")}
        >
          Reject
        </button>
        {operations.length > 0 ? (
          <button
            className="ml-auto border-0 bg-transparent p-0 text-[11px] text-text-muted transition-[color] duration-150 hover:text-accent-deep hover:underline"
            type="button"
            onClick={() => setDiffOpen(true)}
          >
            View diff
          </button>
        ) : null}
      </div>
      {diffOpen ? (
        <RevisionDiffDialog
          operations={operations}
          chips={chips}
          onClose={() => setDiffOpen(false)}
        />
      ) : null}
    </div>
  );
}

function badgeToneClass(tone: ChipTone): string {
  if (tone === "add") return "bg-accent-subtle text-accent-deep";
  if (tone === "remove") return "bg-error-surface text-error";
  return "bg-surface-subtle text-text-secondary";
}

function chipTitle(chip: OperationChip): string {
  return chip.name ? `${chip.name} ${chip.detail}` : chip.detail;
}

function operationCount(diff: Record<string, unknown> | null): number | null {
  const value = Number(diff?.operation_count ?? Number.NaN);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function paperCountDelta(
  diff: Record<string, unknown> | null,
): { before: number; after: number } | null {
  const before = Number(diff?.visible_paper_count_before ?? Number.NaN);
  const after = Number(diff?.visible_paper_count_after ?? Number.NaN);
  if (!Number.isFinite(before) || !Number.isFinite(after) || before === after) {
    return null;
  }
  return { before, after };
}
