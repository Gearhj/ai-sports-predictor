const isIsoLike = (value: string) => /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)

export function formatTime(value: string): string {
  if (!isIsoLike(value)) return value
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date)
}

export function formatDate(value: string): string {
  if (!isIsoLike(value)) return value
  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  const date = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]), 12)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

export function formatTimestamp(value: string): string {
  if (!isIsoLike(value)) return value
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date)
}

export function formatCompactTimestamp(value: string): string {
  if (!isIsoLike(value)) return value
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

export function timeSortValue(value: string): number {
  if (isIsoLike(value)) {
    const parsed = new Date(value).getTime()
    return Number.isNaN(parsed) ? Number.MAX_SAFE_INTEGER : parsed
  }
  const match = value.match(/(\d{1,2}):(\d{2})\s*(AM|PM)/i)
  if (!match) return Number.MAX_SAFE_INTEGER
  const hour = (Number(match[1]) % 12) + (match[3].toUpperCase() === 'PM' ? 12 : 0)
  return hour * 60 + Number(match[2])
}

export function recencySortValue(value: string, now = Date.now()): number {
  if (isIsoLike(value)) {
    const parsed = new Date(value).getTime()
    return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed
  }
  const relative = value.match(/^(\d+)\s+(?:min|minute)s?\s+ago$/i)
  if (relative) return now - Number(relative[1]) * 60_000
  if (/^just now$/i.test(value)) return now
  return Number.NEGATIVE_INFINITY
}
