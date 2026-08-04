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

export function RefreshIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" {...stroke(className)}>
      <path d="M12 7a5 5 0 1 1-1.6-3.7" />
      <path d="M12.2 1.6v2.9H9.3" />
    </svg>
  );
}

export function SparkIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M7 0.8 8.5 5.5 13.2 7 8.5 8.5 7 13.2 5.5 8.5 0.8 7 5.5 5.5Z" />
    </svg>
  );
}
