/**
 * Branch colours for chrome outside the canvas — search results, the build
 * illustration — so a branch reads the same colour everywhere.
 *
 * These mirror the family tones in `TreeNode`, which owns the canvas palette
 * and is deliberately left untouched.
 */
export const FAMILY_TONES: readonly { branch: string; border: string }[] = [
  { border: "#c7d4d0", branch: "#eef3f2" },
  { border: "#d7d0c4", branch: "#f3f1ec" },
  { border: "#c7d2db", branch: "#eff3f6" },
  { border: "#d7ccd9", branch: "#f3f0f4" },
  { border: "#d8d3c9", branch: "#f3f2ee" },
  { border: "#c5d6d9", branch: "#eef4f5" },
  { border: "#d1d8c5", branch: "#f2f3ee" },
  { border: "#dacdca", branch: "#f4f1f0" },
];

const GROUP_TINT = "#f1f3f4";

export function branchTint(family: number | "group" | null | undefined): string {
  if (typeof family === "number") {
    return FAMILY_TONES[family % FAMILY_TONES.length]?.branch ?? GROUP_TINT;
  }
  return GROUP_TINT;
}
