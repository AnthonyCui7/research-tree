/** Button styles shared across panels, so one visual change stays one edit. */

export const primaryActionClass =
  "min-h-[34px] rounded-sm border border-accent bg-accent px-[11px] py-[7px] text-xs font-semibold text-surface transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:-translate-y-px enabled:hover:border-accent-deep enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:border-border disabled:bg-surface-subtle disabled:text-text-muted disabled:transform-none";

export const secondaryActionClass =
  "min-h-[34px] rounded-sm border border-border-strong bg-surface px-2.5 py-[7px] text-xs font-semibold text-text-primary enabled:hover:border-accent enabled:hover:text-accent-deep disabled:cursor-not-allowed disabled:text-text-muted";

export const iconButtonClass =
  "grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:bg-surface-subtle enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px] max-[720px]:h-10 max-[720px]:w-10";
