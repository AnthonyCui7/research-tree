import type { ReactNode } from "react";

/**
 * The icon set: one 16-unit grid, 1.5-unit strokes with round caps and joins,
 * sized by the caller: `size-4` for controls and rows, `size-3` for marks in
 * small indicators, `size-5` in feature tiles. Generated and checked by
 * `apps/web/scripts/icons.py`; change an icon there, not here. The three zoom
 * icons at the end belong to the canvas and keep the canvas's own drawing.
 */
type IconProps = {
  className?: string;
};

function Icon({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {children}
    </svg>
  );
}

export function SidebarIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="2.25" y="2.75" width="11.5" height="10.5" rx="2.25" />
      <path d="M6.25 2.75L6.25 13.25" />
    </Icon>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 3.25L8 12.75M3.25 8L12.75 8" />
    </Icon>
  );
}

export function SearchIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="7.25" cy="7.25" r="4.5" />
      <path d="M10.61 10.61L13.25 13.25" />
    </Icon>
  );
}

export function ClockIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 4.75L8 8L10.25 9.5" />
    </Icon>
  );
}

export function ChatIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M13.75 9.75A1.5 1.5 0 0 1 12.25 11.25L6 11.25L2.25 13.75L2.25 4.25A1.5 1.5 0 0 1 3.75 2.75L12.25 2.75A1.5 1.5 0 0 1 13.75 4.25Z" />
    </Icon>
  );
}

/** A root and two branches joined as the canvas joins them. */
export function TreeIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="1.75" y="5.75" width="4.5" height="4.5" rx="1" />
      <rect x="10.25" y="1.75" width="4" height="4" rx="1" />
      <rect x="10.25" y="10.25" width="4" height="4" rx="1" />
      <path d="M6.25 8C8.25 8 8.25 3.75 10.25 3.75M6.25 8C8.25 8 8.25 12.25 10.25 12.25" />
    </Icon>
  );
}

export function PaperIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M9.75 1.75L4.5 1.75A1.75 1.75 0 0 0 2.75 3.5L2.75 12.5A1.75 1.75 0 0 0 4.5 14.25L11.5 14.25A1.75 1.75 0 0 0 13.25 12.5L13.25 5.25Z" />
      <path d="M9.75 1.75L9.75 4.25A1 1 0 0 0 10.75 5.25L13.25 5.25" />
      <path d="M5.5 8.25L10.5 8.25M5.5 11.25L8.75 11.25" />
    </Icon>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M4 4L12 12M12 4L4 12" />
    </Icon>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3.25 8.5L6.25 11.5L12.75 4.5" />
    </Icon>
  );
}

export function WarningIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M9.09 3.68L13.45 11.38A1.25 1.25 0 0 1 12.36 13.25L3.64 13.25A1.25 1.25 0 0 1 2.55 11.38L6.91 3.68A1.25 1.25 0 0 1 9.09 3.68Z" />
      <path d="M8 6L8 8.75" />
      <circle cx="8" cy="11" r="0.8" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function ChevronDownIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M4.5 6.25L8 9.75L11.5 6.25" />
    </Icon>
  );
}

export function ChevronUpDownIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M5 6.25L8 3.25L11 6.25M5 9.75L8 12.75L11 9.75" />
    </Icon>
  );
}

export function ArrowLeftIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M12.75 8L3.25 8M7.25 4L3.25 8L7.25 12" />
    </Icon>
  );
}

export function ArrowRightIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3.25 8L12.75 8M8.75 4L12.75 8L8.75 12" />
    </Icon>
  );
}

export function SendIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 13L8 3M3.75 7.25L8 3L12.25 7.25" />
    </Icon>
  );
}

/** Leaves the app: a link that opens in a new tab. */
export function ExternalIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M4.75 11.25L11.25 4.75M6 4.75L11.25 4.75L11.25 10" />
    </Icon>
  );
}

export function DownloadIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 2.75L8 10.25M4.75 7L8 10.25L11.25 7M3 13.25L13 13.25" />
    </Icon>
  );
}

export function BookIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 4.5C6.75 3.4 5 3 2.25 3L2.25 12.5C5 12.5 6.75 12.9 8 14M8 4.5C9.25 3.4 11 3 13.75 3L13.75 12.5C11 12.5 9.25 12.9 8 14" />
      <path d="M8 4.5L8 14" />
    </Icon>
  );
}

export function TrashIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M2.75 4.25L13.25 4.25M6.25 4.25L6.25 3A0.75 0.75 0 0 1 7 2.25L9 2.25A0.75 0.75 0 0 1 9.75 3L9.75 4.25M4 4.25L4.62 12.65A1.25 1.25 0 0 0 5.87 13.75L10.13 13.75A1.25 1.25 0 0 0 11.38 12.65L12 4.25" />
      <path d="M6.75 7L6.75 10.75M9.25 7L9.25 10.75" />
    </Icon>
  );
}

export function PencilIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M10.6 2.9A1.77 1.77 0 0 1 13.1 5.4L5.9 12.6L2.5 13.5L3.4 10.1Z" />
      <path d="M9.5 4L12 6.5" />
    </Icon>
  );
}

export function EllipsisIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className={className}>
      <circle cx="3.5" cy="8" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="8" cy="8" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="12.5" cy="8" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function GearIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M6.77 3.41L6.91 1.84A6.25 6.25 0 0 1 9.09 1.84L9.23 3.41A4.75 4.75 0 0 1 10.38 3.89L11.58 2.88A6.25 6.25 0 0 1 13.12 4.42L12.11 5.62A4.75 4.75 0 0 1 12.59 6.77L14.16 6.91A6.25 6.25 0 0 1 14.16 9.09L12.59 9.23A4.75 4.75 0 0 1 12.11 10.38L13.12 11.58A6.25 6.25 0 0 1 11.58 13.12L10.38 12.11A4.75 4.75 0 0 1 9.23 12.59L9.09 14.16A6.25 6.25 0 0 1 6.91 14.16L6.77 12.59A4.75 4.75 0 0 1 5.63 12.11L4.42 13.12A6.25 6.25 0 0 1 2.88 11.58L3.89 10.38A4.75 4.75 0 0 1 3.41 9.23L1.84 9.09A6.25 6.25 0 0 1 1.84 6.91L3.41 6.77A4.75 4.75 0 0 1 3.89 5.63L2.88 4.42A6.25 6.25 0 0 1 4.42 2.88L5.62 3.89A4.75 4.75 0 0 1 6.77 3.41Z" />
      <circle cx="8" cy="8" r="2" />
    </Icon>
  );
}

export function KeyIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="5.25" cy="10.75" r="3" />
      <path d="M7.37 8.63L13.5 2.5M11.17 4.83L12.59 6.24M12.52 3.48L13.65 4.61" />
    </Icon>
  );
}

export function HelpIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M6.25 6.3A1.8 1.8 0 0 1 9.75 6.85C9.75 8.05 8 8.4 8 9.25" />
      <circle cx="8" cy="11.3" r="0.6" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function BugIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="5.25" y="5.75" width="5.5" height="8" rx="2.75" />
      <path d="M6.25 5.75L6.25 5A1.75 1.75 0 0 1 9.75 5L9.75 5.75M2.25 9.25L5.25 9.25M10.75 9.25L13.75 9.25M2.75 12.75L5.25 11.75M13.25 12.75L10.75 11.75M2.75 5.25L5.25 6.5M13.25 5.25L10.75 6.5" />
    </Icon>
  );
}

export function SignOutIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M6.25 13.75L3.5 13.75A1.25 1.25 0 0 1 2.25 12.5L2.25 3.5A1.25 1.25 0 0 1 3.5 2.25L6.25 2.25" />
      <path d="M10.5 11L13.5 8L10.5 5M13.5 8L6 8" />
    </Icon>
  );
}

export function CalendarIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="2.25" y="3.25" width="11.5" height="10.5" rx="2" />
      <path d="M2.25 6.75L13.75 6.75M5.25 1.75L5.25 4.75M10.75 1.75L10.75 4.75" />
    </Icon>
  );
}

/** Where a paper appeared. */
export function VenueIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 2.25L13.5 5.25L2.5 5.25Z" />
      <path d="M4.25 7.25L4.25 11.5M6.75 7.25L6.75 11.5M9.25 7.25L9.25 11.5M11.75 7.25L11.75 11.5M2.25 13.75L13.75 13.75" />
    </Icon>
  );
}

/** How often a paper is cited. */
export function QuoteIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="4.75" cy="10.25" r="2.25" fill="currentColor" stroke="none" />
      <path d="M3.25 10.25C3.25 7.25 3.5 5.25 6 4.25" />
      <circle cx="11.25" cy="10.25" r="2.25" fill="currentColor" stroke="none" />
      <path d="M9.75 10.25C9.75 7.25 10 5.25 12.5 4.25" />
    </Icon>
  );
}

export function GlobeIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M2.25 8L13.75 8M8 2.25C9.55 3.85 10.35 5.75 10.35 8C10.35 10.25 9.55 12.15 8 13.75C6.45 12.15 5.65 10.25 5.65 8C5.65 5.75 6.45 3.85 8 2.25Z" />
    </Icon>
  );
}

/** A stage of work: drafting, checking, auditing. */
export function StepIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3 4.25L10 4.25M3 8L13 8M3 11.75L8.5 11.75" />
    </Icon>
  );
}

export function ZoomOutIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" className={className}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M4 6H8" />
    </svg>
  );
}

export function ZoomInIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" className={className}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M6 4V8M4 6H8" />
    </svg>
  );
}

export function FitIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d="M1.5 4.5V1.5H4.5M9.5 1.5H12.5V4.5M12.5 9.5V12.5H9.5M4.5 12.5H1.5V9.5" />
    </svg>
  );
}
