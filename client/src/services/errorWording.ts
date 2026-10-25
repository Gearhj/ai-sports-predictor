/**
 * One sign convention for point errors, worded the same way everywhere.
 *
 * Every stored error is PREDICTED MINUS ACTUAL (server `PointErrors.signed`):
 *   * negative -> the forecast was too LOW  -> "underestimate"
 *   * positive -> the forecast was too HIGH -> "overestimate"
 * Margin is home minus away, so for a margin the words describe the home side's
 * predicted edge, not "good" or "bad".
 *
 * Colour follows the SIZE of the miss, never its sign: a +26.6 margin miss is a
 * large miss whatever its direction and must never read as a success.
 */

export type ErrorTone = 'small' | 'medium' | 'large'

/** Thresholds per quantity and sport, in points (runs for MLB). */
const THRESHOLDS: Record<string, { small: number; large: number }> = {
  'NFL:margin': { small: 3.5, large: 10 },
  'NFL:total': { small: 3.5, large: 10 },
  'NFL:score': { small: 3.5, large: 7 },
  'CFB:margin': { small: 4, large: 14 },
  'CFB:total': { small: 4, large: 14 },
  'CFB:score': { small: 4, large: 10 },
  'MLB:margin': { small: 1, large: 3 },
  'MLB:total': { small: 1, large: 3 },
  'MLB:score': { small: 1, large: 2.5 },
}

export function errorTone(error: number | null | undefined, kind: 'margin' | 'total' | 'score', sport = 'NFL'): ErrorTone | null {
  if (error == null || !Number.isFinite(error)) return null
  const limits = THRESHOLDS[`${sport}:${kind}`] ?? THRESHOLDS[`NFL:${kind}`]
  const size = Math.abs(error)
  if (size <= limits.small) return 'small'
  if (size >= limits.large) return 'large'
  return 'medium'
}

export function errorDirection(error: number | null | undefined): 'over' | 'under' | 'exact' | null {
  if (error == null || !Number.isFinite(error)) return null
  if (Math.abs(error) < 0.005) return 'exact'
  return error > 0 ? 'over' : 'under'
}

/** "underestimate by 5.5" / "overestimate by 19.7" / "exact". */
export function describeError(error: number | null | undefined, digits = 1): string {
  const direction = errorDirection(error)
  if (direction == null) return '—'
  if (direction === 'exact') return 'exact'
  return `${direction === 'over' ? 'overestimate' : 'underestimate'} by ${Math.abs(error as number).toFixed(digits)}`
}

/** Signed number with a real minus sign: "+26.6", "−5.5", "0.0". */
export function signedNumber(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const text = Math.abs(value).toFixed(digits)
  return Number(text) === 0 ? text : `${value > 0 ? '+' : '−'}${text}`
}

/** A chart domain that always contains zero and every value, padded. */
export function domainWithZero(values: number[], minimumSpan = 10): [number, number] {
  const finite = values.filter((v) => Number.isFinite(v))
  let lo = Math.min(0, ...finite)
  let hi = Math.max(0, ...finite)
  if (hi - lo < minimumSpan) {
    const pad = (minimumSpan - (hi - lo)) / 2
    lo -= pad
    hi += pad
  }
  const pad = (hi - lo) * 0.08
  return [lo - pad, hi + pad]
}
