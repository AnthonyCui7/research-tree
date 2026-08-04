const TIME = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" });
const SHORT_DATE = new Intl.DateTimeFormat("en", { month: "short", day: "numeric" });
const LONG_DATE = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" });
const DATE_TIME = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** Today reads as a clock time; anything older reads as a date. */
export function relativeTimestamp(value: string | null | undefined): string | null {
  const date = parseDate(value);
  if (!date) {
    return null;
  }
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return TIME.format(date);
  }
  return date.getFullYear() === now.getFullYear() ? SHORT_DATE.format(date) : LONG_DATE.format(date);
}

export function dateTimeLabel(value: string | null | undefined): string {
  const date = parseDate(value);
  return date ? DATE_TIME.format(date) : "Unknown date";
}

export function longDateLabel(value: string | null | undefined): string | null {
  const date = parseDate(value);
  return date ? LONG_DATE.format(date) : null;
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
