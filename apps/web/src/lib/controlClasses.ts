/**
 * The shared control vocabulary: buttons, inputs, chips and labels. One visual
 * change stays one edit.
 */

const buttonBase =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed";

/** The one filled action on a surface. */
export const primaryActionClass = `${buttonBase} h-9 rounded-md border border-transparent bg-accent px-3.5 text-[13px] text-white enabled:hover:bg-accent-deep disabled:bg-border-strong`;

export const secondaryActionClass = `${buttonBase} h-9 rounded-md border border-border bg-surface px-3.5 text-[13px] text-text-primary enabled:hover:bg-surface-subtle disabled:text-text-muted`;

/** An action that should not compete with the one beside it. */
export const ghostActionClass = `${buttonBase} h-9 rounded-md border border-transparent bg-transparent px-3 text-[13px] text-text-secondary enabled:hover:bg-surface-subtle enabled:hover:text-text-primary disabled:text-border-strong`;

export const dangerActionClass = `${buttonBase} h-9 rounded-md border border-transparent bg-error px-3.5 text-[13px] text-white enabled:hover:bg-[#a83232] disabled:bg-border-strong`;

/** The smaller pair used inside panel rows and cards. */
export const compactPrimaryActionClass = `${buttonBase} h-7 rounded-[7px] border border-transparent bg-accent px-2.5 text-[12px] text-white enabled:hover:bg-accent-deep disabled:bg-border-strong`;

export const compactActionClass = `${buttonBase} h-7 rounded-[7px] border border-border bg-surface px-2.5 text-[12px] text-text-primary enabled:hover:bg-surface-subtle disabled:text-text-muted`;

/** A square icon control on white chrome. */
export const iconButtonClass =
  "grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 enabled:hover:bg-surface-subtle enabled:hover:text-text-primary aria-expanded:bg-surface-subtle aria-expanded:text-text-primary disabled:cursor-not-allowed disabled:text-border-strong";

export const textInputClass =
  "w-full rounded-md border border-border bg-surface px-3 py-2 text-[14px] text-text-primary outline-0 transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-subtle)] disabled:bg-surface-subtle disabled:text-text-secondary";

export const chipClass =
  "inline-flex items-center rounded-full bg-surface-subtle px-2.5 py-[3px] text-[12px] leading-[1.4] text-text-secondary";

/**
 * The canvas cards' kicker, reused wherever a section needs a label. The type
 * alone is exported for labels in another colour: `cx` joins classes without
 * resolving conflicts, so a colour appended to `kickerClass` would compete
 * with its own rather than replace it.
 */
export const kickerTypeClass =
  "text-[11px] font-semibold uppercase leading-[1.3] tracking-[0.06em]";

export const kickerClass = `${kickerTypeClass} text-text-muted`;

/** Inline notice strips: an error, a warning, or a plain note. */
export const errorNoticeClass =
  "rounded-lg border border-error-border bg-error-surface px-3.5 py-2.5 text-[12.5px] leading-[1.5] text-error";

export const warningNoticeClass =
  "rounded-lg border border-warning-border bg-warning-surface px-3.5 py-2.5 text-[12.5px] leading-[1.5] text-warning";

export const plainNoticeClass =
  "rounded-lg border border-hairline bg-surface-subtle px-3.5 py-2.5 text-[12.5px] leading-[1.5] text-text-secondary";
