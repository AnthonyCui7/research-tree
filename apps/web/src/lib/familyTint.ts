/**
 * Branch tints used by chrome outside the canvas — search results, the root
 * inspector's branch list — so a branch reads the same colour everywhere.
 *
 * These mirror the branch card backgrounds in `TreeNode`, which owns the canvas
 * palette and is deliberately left untouched.
 */
const BRANCH_TINTS = [
  "#eef3f2",
  "#f3f1ec",
  "#eff3f6",
  "#f3f0f4",
  "#f3f2ee",
  "#eef4f5",
  "#f2f3ee",
  "#f4f1f0",
];

const GROUP_TINT = "#f1f3f4";

export function branchTint(family: number | "group" | null | undefined): string {
  if (typeof family === "number") {
    return BRANCH_TINTS[family % BRANCH_TINTS.length] ?? GROUP_TINT;
  }
  return GROUP_TINT;
}
