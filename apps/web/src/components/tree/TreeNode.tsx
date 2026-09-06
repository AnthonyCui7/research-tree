import type { PaperDetails, PathLabelViewModel, TreeNodeId, TreeNodeViewModel } from "../../lib/types";
import { cx } from "../../lib/cx";

type TreeNodeProps = {
  node: TreeNodeViewModel;
  /** Render for height measurement only: fixed width, natural height, no position. */
  measure?: boolean;
  /** Required for interactive nodes; a measured node is never interactive. */
  selected?: boolean;
  onSelectNode?: (nodeId: TreeNodeId) => void;
};

export function TreeNode({ node, selected = false, onSelectNode, measure = false }: TreeNodeProps) {
  const family = node.kind === "root" ? null : familyTone(node.family);

  return (
    <button
      type="button"
      className={cx(
        "absolute z-[1] flex flex-col items-start rounded-md border text-left text-text-primary transition-[border-color,box-shadow,transform] duration-150 enabled:hover:-translate-y-px enabled:hover:border-accent aria-pressed:border-accent aria-pressed:shadow-[0_0_0_2.5px_var(--color-accent-border)]",
        nodePadding[node.kind],
        node.kind === "root" ? rootTone.border : family?.border,
        node.kind === "root" ? rootTone.root : node.kind === "branch" ? family?.branch : family?.paper,
      )}
      style={
        measure
          ? { left: 0, top: 0, width: node.size.width }
          : {
              left: node.position.x,
              top: node.position.y,
              width: node.size.width,
              height: node.size.height,
            }
      }
      data-measure-id={measure ? node.id : undefined}
      tabIndex={measure ? -1 : undefined}
      aria-pressed={selected}
      onClick={() => onSelectNode?.(node.id)}
    >
      {node.kind === "root" ? <RootNodeContent node={node} /> : null}
      {node.kind === "branch" ? <BranchNodeContent node={node} /> : null}
      {node.kind === "paper" ? <PaperNodeContent paper={node} /> : null}
    </button>
  );
}

function RootNodeContent({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "root" }>;
}) {
  return (
    <>
      <span className={kickerClass}>Research topic</span>
      <strong className="mt-[5px] [overflow-wrap:anywhere] text-[21px] font-bold leading-[1.12] tracking-normal">{node.title}</strong>
      <span className="mt-[7px] text-xs leading-[1.5] text-text-secondary">{node.overview}</span>
      <AnchorSummary paper={node.anchorPaper} label="Root survey" />
    </>
  );
}

function BranchNodeContent({
  node,
}: {
  node: Extract<TreeNodeViewModel, { kind: "branch" }>;
}) {
  return (
    <>
      <span className={kickerClass}>Research branch</span>
      <strong className="mt-1.5 [overflow-wrap:anywhere] text-sm font-bold leading-[1.35] tracking-normal">{node.title}</strong>
      <span className={cx("mt-1.5 text-xs leading-[1.5] text-text-secondary", clampThreeLines)}>
        {node.description}
      </span>
      <AnchorSummary paper={node.anchorPaper} label="Branch survey" />
    </>
  );
}

function PaperNodeContent({ paper }: { paper: Extract<TreeNodeViewModel, { kind: "paper" }> }) {
  return (
    <>
      <strong className="[overflow-wrap:anywhere] text-sm font-bold leading-[1.35] tracking-normal">{paper.title}</strong>
      <span className="mt-[5px] text-[11px] leading-[1.35] text-text-muted">
        {paperMetaLine(paper)}
      </span>
      <span className={cx("mt-[7px] text-xs leading-[1.5] text-text-secondary", clampThreeLines)}>
        <b className="mr-1 text-[11px] text-text-primary">TLDR</b>
        {paper.tldr || "Unavailable"}
      </span>
    </>
  );
}

function AnchorSummary({ paper, label }: { paper: PaperDetails | null; label: string }) {
  if (!paper) {
    return null;
  }
  return (
    <span className="mt-2.5 grid w-full gap-[3px] border-t border-[color-mix(in_srgb,var(--color-border)_80%,transparent)] pt-[9px] text-[11px] leading-[1.35] text-text-secondary">
      <span className={kickerClass}>{label}</span>
      <b className="font-semibold text-text-primary">{paper.title}</b>
      <em className="text-[10.5px] not-italic text-text-muted">{paperMetaLine(paper)}</em>
    </span>
  );
}

/**
 * The caption over a timeline row: the reading path's label, set like the
 * cards' eyebrows so it reads as the row's heading rather than another card.
 */
export function PathCaption({ label }: { label: PathLabelViewModel }) {
  return (
    <span
      className={cx("absolute z-[1] block truncate", kickerClass)}
      style={{ left: label.position.x, top: label.position.y, maxWidth: label.width }}
      title={label.text}
    >
      {label.text}
    </span>
  );
}

/** The design's card kicker: uppercase, letter-spaced, one line. */
export const kickerClass =
  "text-[10.5px] font-semibold uppercase leading-[1.3] tracking-[0.05em] text-text-muted";

/** Card padding per kind, matching the design's tighter rhythm on smaller cards. */
const nodePadding = {
  root: "px-[18px] py-4",
  branch: "px-4 py-3.5",
  paper: "px-[15px] py-[13px]",
} as const;

/**
 * Body copy is clamped so one long abstract cannot stretch a card far past its
 * siblings — the full text lives in the inspector, which is where it is read.
 */
const clampThreeLines = "line-clamp-3";

/**
 * The root is the one card that is not a branch or a paper, so it carries the
 * accent rather than a branch family colour — a lighter wash than the selected
 * state, which still has to read as a change on top of it.
 */
const rootTone = {
  border: "border-accent-border",
  root: "bg-accent-wash",
};

const familyTones = [
  { border: "border-[#c7d4d0]", branch: "bg-[#eef3f2]", paper: "bg-[#fafcfb]" },
  { border: "border-[#d7d0c4]", branch: "bg-[#f3f1ec]", paper: "bg-[#fcfbf9]" },
  { border: "border-[#c7d2db]", branch: "bg-[#eff3f6]", paper: "bg-[#fafcfd]" },
  { border: "border-[#d7ccd9]", branch: "bg-[#f3f0f4]", paper: "bg-[#fcfbfd]" },
  { border: "border-[#d8d3c9]", branch: "bg-[#f3f2ee]", paper: "bg-[#fcfcfa]" },
  { border: "border-[#c5d6d9]", branch: "bg-[#eef4f5]", paper: "bg-[#f9fcfc]" },
  { border: "border-[#d1d8c5]", branch: "bg-[#f2f3ee]", paper: "bg-[#fbfcf9]" },
  { border: "border-[#dacdca]", branch: "bg-[#f4f1f0]", paper: "bg-[#fdfbfb]" },
  { border: "border-[#c7ced3]", branch: "bg-surface-subtle", paper: "bg-[#fafbfc]" },
];

function familyTone(family: number | "group" | null | undefined) {
  if (family === "group") {
    return familyTones[8];
  }
  if (typeof family === "number") {
    return familyTones[family % 8];
  }
  return familyTones[8];
}

/** The design's card meta line: `Radford et al. · 12 Feb 2019`. Surveys use it too. */
function paperMetaLine(paper: Pick<PaperDetails, "authors" | "publicationDate" | "year">): string {
  return joinMeta(authorLine(paper.authors), publicationDate(paper));
}

function joinMeta(authors: string, date: string): string {
  if (authors === "Authors unavailable") {
    return date;
  }
  return `${authors} · ${date}`;
}

export function authorLine(authors: string[]): string {
  const [first, second] = authors;
  if (first === undefined) {
    return "Authors unavailable";
  }
  if (second === undefined) {
    return citedAuthorName(first);
  }
  if (authors.length === 2) {
    return `${citedAuthorName(first)} & ${citedAuthorName(second)}`;
  }
  return `${citedAuthorName(first)} et al.`;
}

function citedAuthorName(author: string): string {
  const trimmed = author.trim();
  if (!trimmed) {
    return "Author";
  }
  if (trimmed.includes(",")) {
    return trimmed.split(",", 1)[0]?.trim() || trimmed;
  }
  return trimmed.split(/\s+/).at(-1) || trimmed;
}

export function publicationDate(paper: Pick<PaperDetails, "publicationDate" | "year">): string {
  if (!paper.publicationDate) {
    return paper.year ? `${paper.year}` : "Date unavailable";
  }
  const date = new Date(`${paper.publicationDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return paper.year ? `${paper.year}` : "Date unavailable";
  }
  return new Intl.DateTimeFormat("en", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}
