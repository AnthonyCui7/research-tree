/**
 * The shared control vocabulary: buttons, inputs, chips and labels. One visual
 * change stays one edit.
 *
 * Buttons come in three heights: 28px inside cards and rows, 32px in bars,
 * panels and dialogs, 36px for a page's own actions. Every size keeps the
 * same proportion, side padding a little wider than the space above the
 * capitals, with 12px, 13px and 14px medium text.
 */

const buttonBase =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap border font-medium transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed";

const compactButton = `${buttonBase} h-7 rounded-sm px-2.5 text-12`;
const button = `${buttonBase} h-8 rounded-md px-3 text-13`;
const largeButton = `${buttonBase} h-9 rounded-md px-3.5 text-14`;

/** The one filled action on a surface. */
const primaryTone =
  "border-transparent bg-accent text-white enabled:hover:bg-accent-deep disabled:bg-border-strong";
const secondaryTone =
  "border-border bg-surface text-text-primary enabled:hover:bg-surface-subtle disabled:text-text-muted";
/** An action that should not compete with the one beside it. */
const ghostTone =
  "border-transparent bg-transparent text-text-secondary enabled:hover:bg-surface-subtle enabled:hover:text-text-primary disabled:text-border-strong";
const dangerTone =
  "border-transparent bg-error text-white enabled:hover:bg-[#a83232] disabled:bg-border-strong";

export const primaryActionClass = `${button} ${primaryTone}`;
export const secondaryActionClass = `${button} ${secondaryTone}`;
export const dangerActionClass = `${button} ${dangerTone}`;

export const largePrimaryActionClass = `${largeButton} ${primaryTone}`;
export const largeSecondaryActionClass = `${largeButton} ${secondaryTone}`;
export const largeGhostActionClass = `${largeButton} ${ghostTone}`;

export const compactPrimaryActionClass = `${compactButton} ${primaryTone}`;
export const compactActionClass = `${compactButton} ${secondaryTone}`;

/** A square icon control on white chrome. */
export const iconButtonClass =
  "grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 enabled:hover:bg-surface-subtle enabled:hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary disabled:cursor-not-allowed disabled:text-border-strong";

/**
 * The 24px icon control that sits inside a row, a toast or a notice, pulled
 * 2px into the padding around it so it fits a 20px line. Callers add the
 * colours, which follow the surface it sits on.
 */
export const inlineIconButtonClass =
  "-my-0.5 grid h-6 w-6 flex-none place-items-center rounded-sm border-0 bg-transparent p-0 transition-[background-color,color,opacity] duration-150";

const fieldBase =
  "w-full rounded-md border border-border bg-surface px-3 text-14 text-text-primary outline-0 transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-subtle)] disabled:bg-surface-subtle disabled:text-text-secondary";

/** A one-line field, a step taller than a button because it holds 14px text. */
export const textInputClass = `${fieldBase} h-9`;

export const textAreaClass = `${fieldBase} py-2 leading-6`;

/** A 32px pill: a choice among several, or a suggestion to fill a field with. Callers add the colours. */
export const pillClass =
  "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-13 transition-[background-color,border-color,color] duration-150";

export const chipClass =
  "inline-flex h-6 items-center rounded-full bg-surface-subtle px-2.5 text-12 text-text-secondary";

/** A status label, such as "Current" or "Needs approval", as tall as a 20px line. Callers add the colours. */
export const badgeClass =
  "inline-flex h-5 flex-none items-center rounded-full px-2 text-11 font-semibold";

/** A key the reader presses, printed as a keycap. */
export const keycapClass =
  "inline-flex h-5 min-w-5 flex-none items-center justify-center rounded-xs border border-border bg-surface px-1 font-sans text-11 text-text-muted";

/**
 * The section label, small and uppercase after the canvas cards' kicker. The
 * type alone is exported for labels in another colour: `cx` joins classes without
 * resolving conflicts, so a colour appended to `kickerClass` would compete
 * with its own rather than replace it.
 */
export const kickerTypeClass = "text-11 font-semibold uppercase tracking-[0.06em]";

export const kickerClass = `${kickerTypeClass} text-text-muted`;

/** Inline notice strips: an error, a warning, or a plain note. */
export const errorNoticeClass =
  "rounded-xl border border-error-border bg-error-surface px-4 py-3 text-13 text-error";

export const warningNoticeClass =
  "rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-13 text-warning";

export const plainNoticeClass =
  "rounded-xl border border-hairline bg-surface-subtle px-4 py-3 text-13 text-text-secondary";
