/**
 * The shared control vocabulary from the design's component sheet — buttons,
 * inputs, chips and badges. One visual change stays one edit.
 */

const buttonBase =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-semibold transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed";

export const primaryActionClass = `${buttonBase} rounded-md border border-transparent bg-accent px-4 py-2 text-[13px] text-white enabled:hover:bg-accent-deep disabled:border-border-strong disabled:bg-border-strong disabled:text-white`;

export const secondaryActionClass = `${buttonBase} rounded-md border border-border bg-surface px-3.5 py-2 text-[13px] text-text-secondary enabled:hover:border-border-strong enabled:hover:text-text-primary disabled:text-text-muted`;

export const tintedActionClass = `${buttonBase} rounded-md border border-accent-border bg-accent-subtle px-3.5 py-2 text-[12.5px] text-accent-deep enabled:hover:bg-[#dcefe6] disabled:text-text-muted`;

export const ghostActionClass = `${buttonBase} rounded-md border border-transparent bg-transparent px-3 py-[7px] text-[12.5px] font-normal text-text-muted enabled:hover:text-text-primary disabled:text-border-strong`;

export const dangerActionClass = `${buttonBase} rounded-md border border-error bg-error px-3.5 py-2 text-[13px] text-white enabled:hover:bg-[#a83232] disabled:border-border-strong disabled:bg-border-strong`;

/** The smaller button used inside panel rows (history, proposals). */
export const compactActionClass = `${buttonBase} rounded-[6px] border border-border bg-surface px-[11px] py-1 text-[11px] text-text-primary enabled:hover:border-accent enabled:hover:text-accent-deep disabled:text-text-muted`;

/** The compact sibling of primaryActionClass, for a row's one main action. */
export const compactPrimaryActionClass = `${buttonBase} rounded-[6px] border border-transparent bg-accent px-3 py-1 text-[11px] text-white enabled:hover:bg-accent-deep disabled:bg-border-strong disabled:text-white`;

/** Bordered 30px affordance used where the control sits on white chrome. */
export const outlineIconButtonClass =
  "grid h-[30px] w-[30px] flex-none place-items-center rounded-[7px] border border-border bg-surface p-0 text-text-secondary transition-[background-color,border-color,color] duration-150 enabled:hover:bg-surface-subtle enabled:hover:text-text-primary disabled:cursor-not-allowed disabled:text-border-strong";

export const textInputClass =
  "w-full rounded-[9px] border-[1.5px] border-border-strong bg-surface px-[13px] py-[10px] text-sm text-text-primary outline-0 transition-[border-color,box-shadow] duration-150 placeholder:text-text-muted focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-subtle)]";

export const exampleChipClass =
  "rounded-[20px] border-0 bg-surface-subtle px-[10px] py-[3px] text-[11px] text-text-secondary transition-[background-color,color] duration-150 hover:bg-accent-subtle hover:text-accent-deep";
