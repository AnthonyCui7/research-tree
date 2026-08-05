import type {
  AgentInterruptPayload,
  ProposedOperation,
  TreeViewModel,
} from "./types";

/**
 * Turning a proposal's operations into the rows the review card shows.
 *
 * Everything here is a pure function over loosely-typed backend payloads, so
 * the strict-mode guards (`noUncheckedIndexedAccess`, unknown-shaped dicts)
 * live in one place instead of leaking into JSX.
 */

export type ChipTone = "add" | "remove" | "neutral";

export type OperationChip = {
  /** Stable within one render of one proposal. */
  key: string;
  badge: string;
  tone: ChipTone;
  /** The bold part — a paper title, branch label, or path name. */
  name: string | null;
  /** The rest of the row, possibly including a "→ destination". */
  detail: string;
};

export type FieldChange = {
  label: string;
  before: string | null;
  after: string | null;
};

export type NameLookup = {
  branchLabel: (nodeId: string) => string | null;
  paperTitle: (paperId: string) => string | null;
};

/** Resolve display names from the tree the app already holds. */
export function nameLookupFromTree(tree: TreeViewModel | null): NameLookup {
  const branchLabels: Record<string, string> = {};
  const paperTitles: Record<string, string> = {};
  for (const node of tree?.nodes ?? []) {
    if (node.kind === "branch") {
      branchLabels[node.branchNodeId] = node.title;
      if (node.anchorPaper) paperTitles[node.anchorPaper.paperId] = node.anchorPaper.title;
    } else if (node.kind === "root") {
      branchLabels[node.id] = node.title;
      if (node.anchorPaper) paperTitles[node.anchorPaper.paperId] = node.anchorPaper.title;
    } else {
      paperTitles[node.paperId] = node.title;
    }
  }
  return {
    branchLabel: (nodeId) => branchLabels[nodeId] ?? null,
    paperTitle: (paperId) => paperTitles[paperId] ?? null,
  };
}

export function operationChips(
  operations: ProposedOperation[],
  names: NameLookup,
): OperationChip[] {
  return operations.map((operation, index) => ({
    key: `${index}:${operation.operation_type}`,
    ...describeOperation(operation, names),
  }));
}

function describeOperation(
  operation: ProposedOperation,
  names: NameLookup,
): Omit<OperationChip, "key"> {
  const targets = operation.target_ids ?? {};
  const before = operation.before ?? null;
  const after = operation.after ?? null;
  const paperId = asString(targets["paper_id"]);
  const branchId = asString(targets["branch_id"]);
  const pathId = asString(targets["path_id"]);

  switch (operation.operation_type) {
    case "promote_candidate_paper": {
      const name = asString(after?.["title"]) ?? paperId;
      const destination = branchNameFromCard(after, names);
      return {
        badge: "ADD",
        tone: "add",
        name,
        detail: destination ? `→ ${destination}` : "added to the workspace",
      };
    }
    case "demote_visible_paper":
      return {
        badge: "REMOVE",
        tone: "remove",
        name: asString(before?.["title"]) ?? paperId,
        detail: "removed from the workspace",
      };
    case "move_paper": {
      const toBranch = asString(targets["to_branch_id"]);
      const destination = toBranch ? names.branchLabel(toBranch) ?? toBranch : null;
      return {
        badge: "MOVE",
        tone: "neutral",
        name: paperId ? names.paperTitle(paperId) ?? paperId : null,
        detail: destination ? `→ ${destination}` : "moved to another branch",
      };
    }
    case "rename_branch": {
      const fromLabel =
        asString(before?.["label"]) ?? (branchId ? names.branchLabel(branchId) : null) ?? branchId;
      const toLabel = asString(after?.["label"]);
      return {
        badge: "RENAME",
        tone: "neutral",
        name: fromLabel,
        detail: toLabel ? `→ ${toLabel}` : "renamed",
      };
    }
    case "update_branch_details":
      return {
        badge: "EDIT",
        tone: "neutral",
        name: (branchId ? names.branchLabel(branchId) : null) ?? branchId,
        detail: `branch ${changedFieldsPhrase(before, after) ?? "details"}`,
      };
    case "split_branch": {
      const added = asStringArray(after?.["branch_ids"]);
      return {
        badge: "ADD",
        tone: "add",
        name: null,
        detail:
          added.length === 1
            ? "1 new branch in the tree"
            : `${added.length} new branches in the tree`,
      };
    }
    case "merge_branches": {
      const removed = asStringArray(before?.["branch_ids"]);
      const labels = removed
        .map((id) => names.branchLabel(id) ?? id)
        .filter(Boolean);
      return {
        badge: "MERGE",
        tone: "neutral",
        name: labels.length > 0 ? labels.join(", ") : null,
        detail:
          labels.length > 0 ? "merged away" : `${removed.length} branches merged away`,
      };
    }
    case "update_paper_card":
      return {
        badge: "EDIT",
        tone: "neutral",
        name:
          asString(after?.["title"]) ??
          asString(before?.["title"]) ??
          (paperId ? names.paperTitle(paperId) ?? paperId : null),
        detail: "paper details",
      };
    case "refresh_similar_papers":
      return {
        badge: "EDIT",
        tone: "neutral",
        name: paperId ? names.paperTitle(paperId) ?? paperId : null,
        detail: "similar-paper suggestions refreshed",
      };
    case "create_paper_path": {
      const destination = branchId ? names.branchLabel(branchId) ?? branchId : null;
      return {
        badge: "ADD",
        tone: "add",
        name: asString(after?.["label"]) ?? pathId,
        detail: destination ? `reading path → ${destination}` : "new reading path",
      };
    }
    case "remove_paper_path":
      return {
        badge: "REMOVE",
        tone: "remove",
        name: asString(before?.["label"]) ?? pathId,
        detail: "reading path removed",
      };
    case "update_reading_order":
      return {
        badge: "MOVE",
        tone: "neutral",
        name: null,
        detail: "reading order across the workspace",
      };
    case "update_root_overview":
      return {
        badge: "EDIT",
        tone: "neutral",
        name: null,
        detail: `topic overview — ${changedFieldsPhrase(before, after) ?? "updated"}`,
      };
    case "rename_workspace": {
      const toTitle = asString(after?.["title"]) ?? asString(after?.["topic"]);
      return {
        badge: "RENAME",
        tone: "neutral",
        name: asString(before?.["title"]) ?? asString(before?.["topic"]),
        detail: toTitle ? `→ ${toTitle}` : "workspace renamed",
      };
    }
    case "update_workspace_subtree": {
      if (branchId) {
        return {
          badge: "MOVE",
          tone: "neutral",
          name: names.branchLabel(branchId) ?? branchId,
          detail: "moved in the tree",
        };
      }
      if (pathId) {
        return {
          badge: "EDIT",
          tone: "neutral",
          name: asString(after?.["label"]) ?? asString(before?.["label"]) ?? pathId,
          detail: "reading path changed",
        };
      }
      const fields = asStringArray(targets["top_level_fields"]);
      if (fields.length > 0) {
        return {
          badge: "EDIT",
          tone: "neutral",
          name: null,
          detail: `workspace fields: ${fields.join(", ")}`,
        };
      }
      return { badge: "EDIT", tone: "neutral", name: null, detail: "branch structure changed" };
    }
    default:
      return {
        badge: "EDIT",
        tone: "neutral",
        name: null,
        detail: humanizeOperation(operation.operation_type),
      };
  }
}

/**
 * The previous card rendering, kept as the fallback for payloads that carry a
 * diff summary but no per-instance operations.
 */
export function fallbackChipsFromDiffSummary(
  diff: Record<string, unknown> | null,
): OperationChip[] {
  const types = diff?.["operation_types"];
  if (!Array.isArray(types)) return [];
  return types.map((value, index) => {
    const type = String(value);
    const known = FALLBACK_LABELS[type];
    return {
      key: `${index}:${type}`,
      badge: known?.badge ?? "EDIT",
      tone: known?.tone ?? "neutral",
      name: null,
      detail: known?.label ?? humanizeOperation(type),
    };
  });
}

const FALLBACK_LABELS: Record<string, { badge: string; tone: ChipTone; label: string }> = {
  promote_candidate_paper: { badge: "ADD", tone: "add", label: "Add papers to the workspace" },
  create_paper_path: { badge: "ADD", tone: "add", label: "Add a reading path" },
  split_branch: { badge: "ADD", tone: "add", label: "Add research branches" },
  demote_visible_paper: { badge: "REMOVE", tone: "remove", label: "Remove papers from the workspace" },
  remove_paper_path: { badge: "REMOVE", tone: "remove", label: "Remove a reading path" },
  merge_branches: { badge: "MERGE", tone: "neutral", label: "Merge research branches" },
  move_paper: { badge: "MOVE", tone: "neutral", label: "Move papers between branches" },
  update_reading_order: { badge: "MOVE", tone: "neutral", label: "Reorder the reading order" },
  rename_branch: { badge: "RENAME", tone: "neutral", label: "Rename a research branch" },
  rename_workspace: { badge: "RENAME", tone: "neutral", label: "Rename the workspace" },
  update_branch_details: { badge: "EDIT", tone: "neutral", label: "Update branch details" },
  update_paper_card: { badge: "EDIT", tone: "neutral", label: "Update paper details" },
  refresh_similar_papers: { badge: "EDIT", tone: "neutral", label: "Refresh similar-paper suggestions" },
  update_root_overview: { badge: "EDIT", tone: "neutral", label: "Update the topic overview" },
  update_workspace_subtree: { badge: "EDIT", tone: "neutral", label: "Restructure part of the tree" },
};

/**
 * The changed-field ledger the diff dialog renders: the shallow union of
 * before/after keys whose values differ, values summarized rather than
 * recursed into.
 */
export function operationFieldChanges(operation: ProposedOperation): FieldChange[] {
  const before = operation.before ?? {};
  const after = operation.after ?? {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const changes: FieldChange[] = [];
  for (const key of keys) {
    const beforeValue = before[key];
    const afterValue = after[key];
    if (JSON.stringify(beforeValue) === JSON.stringify(afterValue)) continue;
    changes.push({
      label: humanizeOperation(key),
      before: summarizeValue(beforeValue),
      after: summarizeValue(afterValue),
    });
  }
  return changes;
}

export function skepticNotes(payload: AgentInterruptPayload | null | undefined): string[] {
  return (payload?.skeptic_notes ?? []).map((note) => note.trim()).filter(Boolean);
}

export function operationsFromResult(payload: AgentInterruptPayload | null | undefined): ProposedOperation[] {
  const operations = payload?.proposed_operations;
  return Array.isArray(operations) ? operations : [];
}

/* ------------------------------------------------------------- helpers --- */

const MAX_VALUE_CHARACTERS = 280;

function summarizeValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return truncate(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const allShortStrings =
      value.length <= 8 && value.every((item) => typeof item === "string" && item.length <= 48);
    if (allShortStrings) return truncate(value.join(", ")) || "(empty)";
    return `${value.length} item${value.length === 1 ? "" : "s"}`;
  }
  if (typeof value === "object") {
    const size = Object.keys(value as Record<string, unknown>).length;
    return `${size} field${size === 1 ? "" : "s"}`;
  }
  return String(value);
}

function truncate(value: string): string {
  const trimmed = value.trim();
  return trimmed.length <= MAX_VALUE_CHARACTERS
    ? trimmed
    : `${trimmed.slice(0, MAX_VALUE_CHARACTERS)}…`;
}

function branchNameFromCard(
  card: Record<string, unknown> | null,
  names: NameLookup,
): string | null {
  const location = card?.["primary_tree_location"];
  if (typeof location !== "object" || location === null) return null;
  const nodeId = asString((location as Record<string, unknown>)["node_id"]);
  return nodeId ? names.branchLabel(nodeId) ?? nodeId : null;
}

function changedFieldsPhrase(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
): string | null {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])];
  if (keys.length === 0) return null;
  return keys.map((key) => key.replace(/_/g, " ")).join(", ");
}

export function humanizeOperation(type: string): string {
  const words = type.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
