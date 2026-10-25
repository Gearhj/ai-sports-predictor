/**
 * College Football sportsbook quotes, as stored, and what they are compared with.
 *
 * THREE CLOCKS, NEVER ONE FOR ANOTHER
 *   quote clock   the book's own lastUpdatedAt, per side; the pair is as old as its OLDER leg
 *   fetched       when AISP sent the request (or bound a retained response after review)
 *   received      when the response arrived at AISP
 *
 * ONE MODEL CHANNEL
 *   Every comparison is against the joint score simulation: its win share for
 *   the moneyline, its own margin/total draws for spreads and totals. Ridge and
 *   Elo never appear in a comparison and nothing is averaged.
 *
 * CHECKPOINT MEANING
 *   NO_BET (priced, edge below policy) vs NO_PRICE (declined: no usable pair)
 *   vs BLOCKED (not evaluated) -- the server derives it from the recorded row.
 */

export interface QuoteClocks {
  quote_clock_basis: 'BOOK_LAST_UPDATED_AT_PER_SIDE'
  legs: Record<string, string | null>
  older_leg_at: string | null
  leg_skew_seconds: number | null
  quote_age_minutes: number | null
  fetched_at: string | null
  fetch_basis?: string
  received_at: string | null
  receipt_basis?: string | null
  fetch_age_minutes: number | null
  fresh: boolean
  usable_until: string | null
}

export interface CfbStoredQuote {
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL' | string
  line: number | null
  sportsbook: string | null
  sportsbook_key: string | null
  provider: string | null
  observed_at: string | null
  age_minutes: number | null
  fresh: boolean
  clocks: QuoteClocks
  provider_event_id?: string | null
  rebound_from_archive?: boolean
  sides: Record<string, { american: number | null; decimal: number | null }>
}

export interface CfbComparison {
  market: string
  line: number | null
  sportsbook: string | null
  provider: string | null
  current: boolean
  available: boolean
  reason?: string
  channel?: 'JOINT_PAIRED_RESIDUAL_BASELINE' | 'JOINT_SCORE_DISTRIBUTION'
  channel_label?: string
  /** Fractions; spread/total also carry PUSH and "<SIDE>_if_not_push". */
  model?: Record<string, number | null>
  no_vig?: Record<string, number> | null
  difference?: Record<string, number> | null
  overround?: number | null
  draws?: number
  labels?: Record<string, string>
  basis?: string
  policy_test?: boolean
  excluded_channels?: string[]
}

export interface CfbMarket {
  state: 'QUOTES_STORED' | 'STORED_REFERENCE_ONLY' | 'NO_QUOTE_ROWS'
  freshness_rule?: string
  quotes: CfbStoredQuote[]
  comparisons: CfbComparison[]
  coverage?: { reason: string; detail: string; reference_lines?: string } | null
  note: string
}

export type CheckpointClass = 'NO_BET' | 'NO_PRICE' | 'NO_ROOM' | 'BLOCKED' | 'QUALIFIED' | 'QUALIFIED_NOT_ACTIVATED' | 'UNKNOWN'

export interface CheckpointSemantics {
  class: CheckpointClass
  reason: string | null
  evaluated: boolean
  priced: boolean
  text: string
}

const ET = 'America/New_York'
const time = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' }) + ' ET'
}
const mins = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)} min`)
export const pctText = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
export const ptsText = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} pts`)
export const americanText = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)

/** "quote 3:41 PM ET (older leg; home 3:41, away 3:39) · 12 min old · fetched 3:50 PM ET · received 3:50 PM ET" */
export function clocksLine(c: QuoteClocks | null | undefined): string | null {
  if (!c) return null
  const legs = Object.entries(c.legs ?? {})
    .map(([side, at]) => `${side.toLowerCase()} ${time(at).replace(' ET', '')}`).join(', ')
  const parts = [
    `quote ${time(c.older_leg_at)} (older leg${legs ? `; ${legs}` : ''}) · ${mins(c.quote_age_minutes)} old`,
    `fetched ${time(c.fetched_at)}`,
    c.received_at ? `received ${time(c.received_at)}` : 'receipt time not recorded',
  ]
  return parts.join(' · ')
}

export function freshnessText(c: QuoteClocks | null | undefined): string {
  if (!c) return ''
  return c.fresh ? `usable until ${time(c.usable_until)} (older leg ≤ 240 min)` : 'older than 240 minutes · stored reference only'
}

const MARKET_NAME: Record<string, string> = { MONEYLINE: 'Moneyline', SPREAD: 'Spread', TOTAL: 'Total' }
export const marketName = (m: string) => MARKET_NAME[m] ?? m

/** One stored quote, as offered: "Home −7.5 −110 / Away +7.5 −110". */
export function quoteText(q: CfbStoredQuote, home: string, away: string): string {
  const s = q.sides
  if (q.market === 'TOTAL') return `Over ${q.line} ${americanText(s.OVER?.american)} / Under ${q.line} ${americanText(s.UNDER?.american)}`
  if (q.market === 'SPREAD' && q.line != null) {
    const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v)}`
    return `${home} ${signed(q.line)} ${americanText(s.HOME?.american)} / ${away} ${signed(-q.line)} ${americanText(s.AWAY?.american)}`
  }
  return `${home} ${americanText(s.HOME?.american)} / ${away} ${americanText(s.AWAY?.american)}`
}

const UNAVAILABLE: Record<string, string> = {
  NO_FORECAST: 'no forecast is bound',
  NO_VIG_UNAVAILABLE: 'the book did not price both sides',
  NO_JOINT: 'the joint probabilities are missing',
  LINE_MISSING: 'the book posted no line',
  FORECAST_PROVIDES_NO_SCORE_DISTRIBUTION: 'the forecast carries no joint score distribution for this market',
  MARKET_NOT_COMPARED: 'this market is not compared',
}

/** The comparison in words, labelled by its channel; never a Ridge or Elo number. */
export function comparisonText(c: CfbComparison, home: string, away: string): string {
  if (!c.available) return `Not compared: ${UNAVAILABLE[c.reason ?? ''] ?? (c.reason ?? 'unavailable').replace(/_/g, ' ').toLowerCase()}`
  const m = c.model ?? {}
  const nv = c.no_vig ?? {}
  const diff = c.difference ?? {}
  if (c.market === 'MONEYLINE') {
    return `Joint model ${home} ${pctText(m.HOME)} vs ${c.sportsbook ?? 'book'} no-vig ${pctText(nv.HOME)} (${ptsText(diff.HOME)}); ${away} ${pctText(m.AWAY)} vs ${pctText(nv.AWAY)} (${ptsText(diff.AWAY)})`
  }
  const [a, b] = c.market === 'TOTAL' ? ['OVER', 'UNDER'] : ['HOME', 'AWAY']
  const name = (side: string) => (side === 'HOME' ? `${home} covers` : side === 'AWAY' ? `${away} covers` : side === 'OVER' ? 'over' : 'under')
  const push = m.PUSH ? ` · push ${pctText(m.PUSH)}` : ''
  const cond = m[`${a}_if_not_push`]
  const market = c.no_vig
    ? ` · pushes excluded ${pctText(cond)} vs no-vig ${pctText(nv[a])} (${ptsText(diff[a] ?? null)})`
    : ' · the book priced one side only'
  return `Joint draws ${name(a)} ${pctText(m[a])}, ${name(b)} ${pctText(m[b])}${push}${market}`
}

/** A recorded CFB checkpoint in plain words: NO_BET, NO_PRICE and BLOCKED are three different results. */
export const CHECKPOINT_LABEL: Record<CheckpointClass, string> = {
  NO_BET: 'No bet · priced, edge below policy',
  NO_PRICE: 'Declined · no usable two-sided price',
  NO_ROOM: 'Declined · no room left in the shared $100',
  BLOCKED: 'Blocked · not evaluated',
  QUALIFIED: 'Qualified · position recorded',
  QUALIFIED_NOT_ACTIVATED: 'Qualified · activation control off',
  UNKNOWN: 'Recorded',
}

const NO_PRICE_DETAIL: Record<string, string> = {
  NO_TWO_SIDED_QUOTE: 'no same-book two-sided moneyline was stored',
  QUOTE_TOO_STALE: 'the stored pair’s older leg was more than 240 minutes old',
  NO_PERMITTED_PROVIDER: 'stored pairs came only from a provider the policy does not permit',
}

/** Value qualified, but the owner's shared $100 had no room: never a no-bet (AISP_SHARED_100_PRIORITY_MLB_NHL_V1). */
const NO_ROOM_DETAIL: Record<string, string> = {
  SHARED_ALLOWANCE_EXHAUSTED: 'the shared $100 for the day was fully used',
  SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS: 'the rest of the shared $100 was held for MLB/NHL games',
}

/** A recorded decision whose own reason says the shared allowance alone left no room (any sport). */
export const ALLOWANCE_DECLINE = /no room remains|shared \$100 for \S+ is fully entered|SHARED_ALLOWANCE_EXHAUSTED|SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS/

export function checkpointLabel(s: CheckpointSemantics | null | undefined): string | null {
  if (!s) return null
  if (s.class === 'NO_PRICE' && s.reason && NO_PRICE_DETAIL[s.reason]) return `Declined · ${NO_PRICE_DETAIL[s.reason]}`
  if (s.class === 'NO_ROOM' && s.reason && NO_ROOM_DETAIL[s.reason]) return `Declined · ${NO_ROOM_DETAIL[s.reason]}`
  return CHECKPOINT_LABEL[s.class] ?? CHECKPOINT_LABEL.UNKNOWN
}
