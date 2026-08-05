/**
 * The icon vocabulary from the Research Tree design: 1.3–1.6px strokes on
 * square viewBoxes, sized by the caller so one icon serves every control.
 */
type IconProps = {
  className?: string;
};

function stroke(className?: string) {
  return {
    "aria-hidden": true as const,
    fill: "none",
    stroke: "currentColor",
    className,
  };
}

export function SidebarIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.4" {...stroke(className)}>
      <rect x="1.5" y="2" width="11" height="10" rx="2" />
      <path d="M5.5 2V12" />
    </svg>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" strokeWidth="1.6" strokeLinecap="round" {...stroke(className)}>
      <path d="M6 1.5V10.5M1.5 6H10.5" />
    </svg>
  );
}

export function SearchIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" {...stroke(className)}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5" />
    </svg>
  );
}

export function ClockIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.4" strokeLinecap="round" {...stroke(className)}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8L10.5 9.8" />
    </svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" strokeWidth="1.5" strokeLinecap="round" {...stroke(className)}>
      <path d="M2 2L10 10M10 2L2 10" />
    </svg>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M2.5 7.5L5.5 10.5L11.5 3.5" />
    </svg>
  );
}

export function ChatIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" strokeLinejoin="round" {...stroke(className)}>
      <path d="M3 4.5A1.5 1.5 0 0 1 4.5 3H15.5A1.5 1.5 0 0 1 17 4.5V12A1.5 1.5 0 0 1 15.5 13.5H8L4.5 17V13.5A1.5 1.5 0 0 1 3 12Z" />
    </svg>
  );
}

export function ZoomOutIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" {...stroke(className)}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M4 6H8" />
    </svg>
  );
}

export function ZoomInIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" {...stroke(className)}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M6 4V8M4 6H8" />
    </svg>
  );
}

export function FitIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M1.5 4.5V1.5H4.5M9.5 1.5H12.5V4.5M12.5 9.5V12.5H9.5M4.5 12.5H1.5V9.5" />
    </svg>
  );
}

export function ChevronDownIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="m4 6 4 4 4-4" />
    </svg>
  );
}

export function SendIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M6 10V2M2.5 5.5L6 2L9.5 5.5" />
    </svg>
  );
}

export function TrashIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M1.5 3H10.5M4.5 3V1.8H7.5V3M2.5 3L3.2 10.5H8.8L9.5 3M4.8 5V8.5M7.2 5V8.5" />
    </svg>
  );
}

export function EllipsisIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="currentColor" aria-hidden="true" className={className}>
      <circle cx="3" cy="7" r="1.15" />
      <circle cx="7" cy="7" r="1.15" />
      <circle cx="11" cy="7" r="1.15" />
    </svg>
  );
}

export function ArrowLeftIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M12 7H2M5.5 3.5L2 7l3.5 3.5" />
    </svg>
  );
}

export function ArrowRightIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M2 7h10M8.5 3.5 12 7l-3.5 3.5" />
    </svg>
  );
}

export function WarningIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.4" strokeLinecap="round" {...stroke(className)}>
      <path d="M7 1.8 12.8 12H1.2L7 1.8Z" />
      <path d="M7 5.6v3.1" />
      <circle cx="7" cy="10.3" r="0.55" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function GearIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <circle cx="8" cy="8" r="2.1" />
      <path d="M8 1.4v1.8M8 12.8v1.8M1.4 8h1.8M12.8 8h1.8M3.3 3.3l1.3 1.3M11.4 11.4l1.3 1.3M12.7 3.3l-1.3 1.3M4.6 11.4l-1.3 1.3" />
    </svg>
  );
}

export function KeyIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <circle cx="5" cy="5" r="3" />
      <path d="M7.2 7.2 13.5 13.5M11.4 11.4l-1.3 1.3M13 13l-1.3 1.3" />
    </svg>
  );
}

export function HelpIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <circle cx="8" cy="8" r="6" />
      <path d="M6.3 6.2a1.75 1.75 0 1 1 2.1 2.1c-.3.1-.4.4-.4.7v.4" />
      <circle cx="8" cy="11.5" r="0.55" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function BugIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <rect x="5" y="5" width="6" height="8" rx="3" />
      <path d="M6.3 3.6 5.2 2.5M9.7 3.6l1.1-1.1M5 7.5H2.4M11 7.5h2.6M5.2 10.7 3 12M10.8 10.7 13 12" />
    </svg>
  );
}

export function SignOutIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M6.5 2.5H3.6A1.1 1.1 0 0 0 2.5 3.6v8.8a1.1 1.1 0 0 0 1.1 1.1h2.9" />
      <path d="M10.5 5.5 13.5 8l-3 2.5M13.5 8H6.2" />
    </svg>
  );
}
