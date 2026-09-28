import { useMemo, useState } from "react";
import { cx } from "../../lib/cx";
import { compactActionClass, compactPrimaryActionClass } from "../../lib/controlClasses";
import { pluralize } from "../../lib/format";
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
    <div className="overflow-hidden rounded-2xl border border-border bg-surface">
      <div className="flex items-baseline gap-2 px-4 pt-3.5 pb-2">
        <span className="text-[13.5px] font-semibold text-text-primary">Proposed revision</span>
        {count !== null ? (
          <span className="text-[12px] text-text-muted">{pluralize(count, "change")}</span>
        ) : null}
        <span className="ml-auto flex-none rounded-full bg-accent-subtle px-2 py-px text-[11px] font-semibold text-accent-deep">
          Needs approval
        </span>
      </div>
      <div className="grid gap-2 px-4 pb-3.5">
        {restoredUserMessage ? (
          <p className="m-0 text-[12.5px] leading-[1.5] text-text-muted">
            In response to: “{restoredUserMessage}”
          </p>
        ) : null}
        {visibleChips.length > 0 ? (
          visibleChips.map((chip) => (
            <div className="flex items-baseline gap-2 text-[13px] leading-[1.5]" key={chip.key}>
              <OperationBadge chip={chip} />
              <span className="min-w-0 truncate text-text-secondary" title={chipTitle(chip)}>
                {chip.name ? <strong className="font-medium text-text-primary">{chip.name}</strong> : null}
                {chip.name && chip.detail ? " " : null}
                {chip.detail}
              </span>
            </div>
          ))
        ) : (
          <p className="m-0 text-[13px] leading-[1.5] text-text-secondary">
            A structural revision is ready for your review.
          </p>
        )}
        {hiddenChipCount > 0 ? (
          <p className="m-0 text-[12.5px] leading-[1.5] text-text-muted">
            +{hiddenChipCount} more in the diff
          </p>
        ) : null}
        {paperDelta || notes.length > 0 ? (
          <div className="mt-1 grid gap-1.5 border-t border-hairline pt-2.5">
            {paperDelta ? (
              <p className="m-0 text-[12.5px] leading-[1.55] text-text-muted">
                Visible papers: {paperDelta.before} → {paperDelta.after}
              </p>
            ) : null}
            {notes.map((note, index) => (
              <p className="m-0 text-[12.5px] leading-[1.55] text-warning" key={`${index}:${note}`}>
                <strong className="font-semibold">Skeptic:</strong> {note}
              </p>
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-2 border-t border-hairline px-4 py-2.5">
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
            className="ml-auto border-0 bg-transparent p-0 text-[12.5px] font-medium text-text-secondary transition-[color] duration-150 hover:text-accent-deep hover:underline"
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

/** ADD, REMOVE, MOVE…: tinted by whether the change adds, removes, or rearranges. */
export function OperationBadge({ chip }: { chip: OperationChip }) {
  return (
    <span
      className={cx(
        "flex-none rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold tracking-[0.02em] uppercase",
        BADGE_TONE[chip.tone],
      )}
    >
      {chip.badge}
    </span>
  );
}

const BADGE_TONE: Record<ChipTone, string> = {
  add: "bg-accent-subtle text-accent-deep",
  remove: "bg-error-surface text-error",
  neutral: "bg-surface-subtle text-text-secondary",
};

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
