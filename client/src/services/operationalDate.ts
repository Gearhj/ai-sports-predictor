/**
 * Central client-side operational-date authority.
 *
 * Every daily AISP surface must resolve "today" in the configured display
 * timezone (America/New_York unless the backend says otherwise) — never with
 * `new Date().toISOString().slice(0, 10)`, which is the UTC calendar date and
 * rolls to tomorrow at 8 PM Eastern.
 */

export const DEFAULT_DISPLAY_TIMEZONE = 'America/New_York'

/** YYYY-MM-DD for the given instant in the display timezone. */
export function operationalToday(now: Date = new Date(), timeZone: string = DEFAULT_DISPLAY_TIMEZONE): string {
  // en-CA formats as YYYY-MM-DD; the timeZone option does the conversion.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
}

/** Shift a YYYY-MM-DD string by whole days (calendar arithmetic, no timezone). */
export function shiftDate(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const shifted = new Date(Date.UTC(y, m - 1, d + days))
  return shifted.toISOString().slice(0, 10)
}

export function isValidIsoDate(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [y, m, d] = value.split('-').map(Number)
  const probe = new Date(Date.UTC(y, m - 1, d))
  return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d
}

/** Resolve the operating date: an explicit valid URL value wins, else today (ET). */
export function resolveOperationalDate(urlValue: string | null | undefined, now: Date = new Date()): string {
  return isValidIsoDate(urlValue) ? urlValue : operationalToday(now)
}
