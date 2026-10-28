/**
 * THE SWEAT METER's arithmetic (owner 10-26, plan item #3). A heart rate for every live game, from the only
 * things a scoreboard tells us: the score and how much game is left.
 *
 *   progress  0..1 of regulation played (quarters, periods or half-innings)
 *   close     exp(−½ · (margin / spread)²), spread = max(k · √left, one score) — k is a typical swing over the
 *             rest of a game; "one score" (NBA 4, NFL/CFB 8, NHL 1, MLB 1.5) keeps a one-possession game tense
 *             to the last second, while a blowout reads calm
 *   sweat     close · (0.35 + 0.65 · progress^1.5), clamped 0..1
 *   bpm       60 + 110 · sweat (a sleepy 60 to a cardiac 170)
 *
 * It is a feel-meter for the broadcast, NOT a win probability and never a wager signal; the page says so.
 */
export type League = 'nba' | 'nhl' | 'nfl' | 'cfb' | 'mlb'

const PERIODS: Record<Exclude<League, 'mlb'>, { n: number; min: number }> = { nba: { n: 4, min: 12 }, nfl: { n: 4, min: 15 }, cfb: { n: 4, min: 15 }, nhl: { n: 3, min: 20 } }
const K: Record<League, number> = { nba: 11, nfl: 10, cfb: 12, nhl: 1.6, mlb: 2.2 }

function clockMinutes(clock: string | null | undefined): number | null {
  const m = /^(\d+):(\d{2})(?:\.\d+)?$/.exec((clock ?? '').trim())
  if (m) return Number(m[1]) + Number(m[2]) / 60
  const s = /^(\d+(?:\.\d+)?)$/.exec((clock ?? '').trim()) // "45.2" seconds in the last minute
  return s ? Number(s[1]) / 60 : null
}

/** Share of regulation played, 0..1. Overtime/extra innings count as 1 (as late as it gets). */
export function progressOf(lg: League, period: number | null | undefined, clock: string | null | undefined, detail: string | null | undefined): number {
  const p = Math.max(1, Number(period) || 1)
  if (lg === 'mlb') {
    const d = (detail ?? '').toLowerCase()
    const half = /bot|end/.test(d) ? 1 : /mid/.test(d) ? 0.9 : 0
    if (p > 9) return 1
    return Math.min(1, ((p - 1) * 2 + half + 0.5) / 18)
  }
  const { n, min } = PERIODS[lg]
  if (p > n) return 1
  const left = clockMinutes(clock)
  const intoPeriod = left == null ? min / 2 : Math.max(0, min - Math.min(min, left))
  if (/halftime/i.test(detail ?? '')) return 0.5
  if (/end of/i.test(detail ?? '')) return Math.min(1, (p * min) / (n * min))
  return Math.min(1, ((p - 1) * min + intoPeriod) / (n * min))
}

/** One possession / one score: a margin this size is never "safe", however late. */
const ONE_SCORE: Record<League, number> = { nba: 4, nfl: 8, cfb: 8, nhl: 1, mlb: 1.5 }

export function sweatOf(lg: League, margin: number, progress: number): number {
  const left = Math.max(0, 1 - progress)
  const spread = Math.max(K[lg] * Math.sqrt(left), ONE_SCORE[lg])
  const close = Math.exp(-((margin / spread) ** 2) / 2)
  return Math.max(0, Math.min(1, close * (0.35 + 0.65 * Math.pow(progress, 1.5))))
}
export const bpmOf = (sweat: number) => Math.round(60 + 110 * sweat)
export function tierOf(sweat: number): { label: string; tone: 'chill' | 'warm' | 'hot' | 'cardiac' } {
  if (sweat >= 0.75) return { label: 'CARDIAC', tone: 'cardiac' }
  if (sweat >= 0.5) return { label: 'SWEATING', tone: 'hot' }
  if (sweat >= 0.25) return { label: 'WARM', tone: 'warm' }
  return { label: 'CHILL', tone: 'chill' }
}

/**
 * One ECG beat as a function of phase 0..1 (P wave, QRS spike, T wave), scaled by amplitude.
 * Pure so the drawing can be tested and stays the same at any frame rate.
 */
export function ecg(phase: number, amp: number): number {
  const g = (mu: number, s: number, h: number) => h * Math.exp(-((phase - mu) ** 2) / (2 * s * s))
  return amp * (g(0.18, 0.025, 0.12) - g(0.36, 0.008, 0.18) + g(0.4, 0.012, 1) - g(0.44, 0.01, 0.32) + g(0.68, 0.045, 0.22))
}
