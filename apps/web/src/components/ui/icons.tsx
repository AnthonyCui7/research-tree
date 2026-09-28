import type { ReactNode } from "react";

/**
 * The icon set: one 16px grid, 1.5px strokes with round caps and joins,
 * sized by the caller so one icon serves every control. The three zoom icons
 * below belong to the canvas and keep the canvas's own drawing.
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

/* ----------------------------------------------------------- navigation --- */

export function SidebarIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="2" y="2.75" width="12" height="10.5" rx="2.25" />
      <path d="M6.25 2.75v10.5" />
    </Icon>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 3.25v9.5M3.25 8h9.5" />
    </Icon>
  );
}

export function SearchIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="7.25" cy="7.25" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </Icon>
  );
}

export function ClockIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 5v3.25l2.1 1.3" />
    </Icon>
  );
}

export function ChatIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M13.5 9.75a1.5 1.5 0 0 1-1.5 1.5H6l-3.5 2.5V4a1.5 1.5 0 0 1 1.5-1.5h8A1.5 1.5 0 0 1 13.5 4Z" />
    </Icon>
  );
}

/** A root and two branches, drawn the way the canvas draws them. */
export function TreeIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="1.75" y="6.25" width="4" height="3.5" rx="1" />
      <rect x="10.25" y="2.25" width="4" height="3.5" rx="1" />
      <rect x="10.25" y="10.25" width="4" height="3.5" rx="1" />
      <path d="M5.75 8h1.5A1.5 1.5 0 0 0 8.75 6.5v-1A1.5 1.5 0 0 1 10.25 4M8.75 9.5v1A1.5 1.5 0 0 0 10.25 12" />
    </Icon>
  );
}

export function PaperIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M9.5 2H4.5A1.5 1.5 0 0 0 3 3.5v9A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V5.5Z" />
      <path d="M9.5 2v3.5H13M5.75 8.5h4.5M5.75 11h2.75" />
    </Icon>
  );
}

/* ----------------------------------------------------------------- state --- */

export function CloseIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m4 4 8 8M12 4l-8 8" />
    </Icon>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m3.25 8.5 3 3 6.5-7" />
    </Icon>
  );
}

export function WarningIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M6.85 2.85 1.8 11.6a1.33 1.33 0 0 0 1.15 2h10.1a1.33 1.33 0 0 0 1.15-2L9.15 2.85a1.33 1.33 0 0 0-2.3 0Z" />
      <path d="M8 6.25V9" />
      <circle cx="8" cy="11.25" r="0.6" fill="currentColor" stroke="none" />
    </Icon>
  );
}

/* -------------------------------------------------------------- actions --- */

export function ChevronDownIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m4.5 6.25 3.5 3.5 3.5-3.5" />
    </Icon>
  );
}

export function ChevronRightIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m6.25 4.5 3.5 3.5-3.5 3.5" />
    </Icon>
  );
}

export function ChevronUpDownIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m5 6.25 3-3 3 3M5 9.75l3 3 3-3" />
    </Icon>
  );
}

export function ArrowLeftIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M12.75 8h-9.5M7.25 4 3.25 8l4 4" />
    </Icon>
  );
}

export function ArrowRightIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3.25 8h9.5M8.75 4l4 4-4 4" />
    </Icon>
  );
}

export function SendIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 13V3M3.75 7.25 8 3l4.25 4.25" />
    </Icon>
  );
}

/** Leaves the app: a link that opens in a new tab. */
export function ExternalIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M5 11 11 5M5.75 5H11v5.25" />
    </Icon>
  );
}

export function DownloadIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 2.75v7.5M4.75 7 8 10.25 11.25 7M3 13.25h10" />
    </Icon>
  );
}

export function BookIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M8 4.5C6.75 3.4 5 3 2.5 3v9.5c2.5 0 4.25.4 5.5 1.5 1.25-1.1 3-1.5 5.5-1.5V3C11 3 9.25 3.4 8 4.5Z" />
      <path d="M8 4.5V14" />
    </Icon>
  );
}

export function TrashIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M2.75 4.25h10.5M6.25 4.25V3a.75.75 0 0 1 .75-.75h2a.75.75 0 0 1 .75.75v1.25M4 4.25l.6 8.15A1.25 1.25 0 0 0 5.85 13.5h4.3a1.25 1.25 0 0 0 1.25-1.1l.6-8.15M6.75 7v3.75M9.25 7v3.75" />
    </Icon>
  );
}

export function PencilIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M10.6 2.9a1.77 1.77 0 0 1 2.5 2.5L5.9 12.6l-3.4.9.9-3.4Z" />
      <path d="m9.5 4 2.5 2.5" />
    </Icon>
  );
}

export function EllipsisIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className={className}>
      <circle cx="3.5" cy="8" r="1.2" />
      <circle cx="8" cy="8" r="1.2" />
      <circle cx="12.5" cy="8" r="1.2" />
    </svg>
  );
}

/* -------------------------------------------------------------- account --- */

export function GearIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M6.6 2.25h2.8l.35 1.75 1.2.7 1.7-.6 1.4 2.4-1.35 1.2v1.4l1.35 1.2-1.4 2.4-1.7-.6-1.2.7-.35 1.75H6.6l-.35-1.75-1.2-.7-1.7.6-1.4-2.4 1.35-1.2V7.3L1.95 6.1l1.4-2.4 1.7.6 1.2-.7Z" />
      <circle cx="8" cy="8" r="2" />
    </Icon>
  );
}

export function KeyIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="5.25" cy="10.75" r="2.75" />
      <path d="m7.2 8.8 5.8-5.8M10.75 5.25l1.75 1.75M9.25 6.75l1.25 1.25" />
    </Icon>
  );
}

export function HelpIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M6.25 6.3a1.8 1.8 0 0 1 3.5.55c0 1.2-1.75 1.55-1.75 2.4" />
      <circle cx="8" cy="11.3" r="0.6" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function BugIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="5" y="5.5" width="6" height="8" rx="3" />
      <path d="M6.25 5.6V5a1.75 1.75 0 0 1 3.5 0v.6M2.5 9h2.5M11 9h2.5M3 12.75l2-1M13 12.75l-2-1M3 5.25l2 1.25M13 5.25l-2 1.25" />
    </Icon>
  );
}

export function SignOutIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M6.25 13.5h-2.5A1.25 1.25 0 0 1 2.5 12.25v-8.5A1.25 1.25 0 0 1 3.75 2.5h2.5M10.5 11l3-3-3-3M13.5 8H6" />
    </Icon>
  );
}

/* --------------------------------------------------------------- papers --- */

export function CalendarIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <rect x="2.5" y="3.25" width="11" height="10.25" rx="2" />
      <path d="M2.5 6.75h11M5.5 2v2.5M10.5 2v2.5" />
    </Icon>
  );
}

/** Where a paper appeared. */
export function VenueIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="m8 2.25 5.5 3H2.5Z" />
      <path d="M3.5 7v4.5M6.5 7v4.5M9.5 7v4.5M12.5 7v4.5M2.25 13.75h11.5" />
    </Icon>
  );
}

/** How often a paper is cited. */
export function QuoteIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3 11.75c1.75-.5 2.75-1.9 2.75-3.9V4.5H3V8h2.6M10 11.75c1.75-.5 2.75-1.9 2.75-3.9V4.5H10V8h2.6" />
    </Icon>
  );
}

export function GlobeIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M2.25 8h11.5M8 2.25c1.55 1.6 2.35 3.5 2.35 5.75S9.55 12.15 8 13.75C6.45 12.15 5.65 10.25 5.65 8S6.45 3.85 8 2.25Z" />
    </Icon>
  );
}

/** A stage of work: drafting, checking, auditing. */
export function StepIcon({ className }: IconProps) {
  return (
    <Icon className={className}>
      <path d="M3 4.25h7M3 8h10M3 11.75h5.5" />
    </Icon>
  );
}

/* --------------------------------------------------------------- canvas --- */

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
