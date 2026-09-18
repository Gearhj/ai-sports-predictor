/**
 * A game's market and its model, stated independently.
 *
 * "Awaiting first forecast and quote" merged two questions and was wrong about
 * one of them whenever fresh quotes were stored. The server now answers each
 * from its own evidence (services/pregame_status.py); this turns those answers
 * into card lines. A publication time is always a TARGET, never a promise.
 */

export type MarketState =
  | 'ELIGIBLE_THROUGH_WINDOW' | 'EXPIRES_DURING_WINDOW' | 'EXPIRES_BEFORE_WINDOW'
  | 'STORED_REFERENCE_ONLY' | 'NO_TWO_SIDED_QUOTE' | 'WINDOW_CLOSED'

export interface MarketStatus {
  state: MarketState
  rule: string
  books_two_sided: number
  books_fresh_now: number
  books_fresh_through_window: number
  oldest_leg_observed_at: string | null
  newest_leg_observed_at: string | null
  oldest_leg_age_minutes: number | null
  fresh_until: string | null
  earliest_expiry: string | null
  window_opens_at: string
  window_closes_at: string
}

export type ModelState =
  | 'PUBLISHED' | 'PUBLISHED_NOT_IMPORTED' | 'SCHEDULED' | 'WAITING_ON_INPUTS' | 'OVERDUE' | 'MISSED' | 'NOT_PUBLISHED'

export interface ProducerReceipt {
  receipt_sha256: string | null
  receipt_generated_at: string | null
  receipt_age_minutes: number | null
  source: string
  game_status: string
  reason: string | null
  reason_text: string | null
  blockers: string[]
  blocker_text: string[]
  checked_as_of?: string | null
}

export interface ModelStatus {
  state: ModelState
  engine_id?: string | null
  published_at?: string | null
  schedule: {
    input_checks_from: string | null
    publication_target: string | null
    window_opens_at: string
    window_closes_at: string
    target_is_guaranteed: false
  }
  producer: ProducerReceipt | null
}

export type Tone = 'good' | 'watch' | 'bad' | 'neutral'
export interface StatusLine { label: string; detail: string; tone: Tone }

const t = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })
}
const books = (n: number) => `${n} book${n === 1 ? '' : 's'}`

export function marketLine(m: MarketStatus | null | undefined): StatusLine | null {
  if (!m) return null
  const window = `the ${t(m.window_opens_at)}–${t(m.window_closes_at)} ET decision window`
  const age = m.oldest_leg_age_minutes != null ? ` (${Math.round(m.oldest_leg_age_minutes)} min old)` : ''
  const legs = `oldest leg ${t(m.oldest_leg_observed_at)}${age}, newest ${t(m.newest_leg_observed_at)}`
  switch (m.state) {
    case 'ELIGIBLE_THROUGH_WINDOW':
      return { tone: 'good', label: 'Market ready',
        detail: `${books(m.books_fresh_through_window)} with a fresh same-book two-sided moneyline through ${window} · ${legs} · usable until ${t(m.fresh_until)} (older leg ≤ 240 min)` }
    case 'EXPIRES_DURING_WINDOW':
      return { tone: 'watch', label: 'Market expires in window',
        detail: `${books(m.books_fresh_now)} two-sided now, but the newest pair is usable only until ${t(m.fresh_until)}, before ${t(m.window_closes_at)} · ${legs}` }
    case 'EXPIRES_BEFORE_WINDOW':
      return { tone: 'watch', label: 'Market expires before window',
        detail: `${books(m.books_fresh_now)} two-sided now, usable only until ${t(m.fresh_until)}, before ${window} opens · ${legs}` }
    case 'STORED_REFERENCE_ONLY':
      return { tone: 'bad', label: 'Stored reference only',
        detail: `${books(m.books_two_sided)} stored, every pair older than 240 minutes · not usable for a decision` }
    case 'NO_TWO_SIDED_QUOTE':
      return { tone: 'neutral', label: 'No two-sided quote',
        detail: 'No same-book pair with both sides is stored for this game yet' }
    case 'WINDOW_CLOSED':
      return { tone: 'neutral', label: 'Decision window closed',
        detail: `${books(m.books_fresh_now)} two-sided · ${legs}` }
  }
}

function producerText(p: ProducerReceipt | null): string {
  if (!p) return ''
  const when = `Producer receipt ${t(p.receipt_generated_at)}${p.receipt_age_minutes != null ? ` (${Math.round(p.receipt_age_minutes)} min ago)` : ''}`
  if (p.game_status === 'NOT_IN_RECEIPT') return `${when}: this game is not listed`
  const why = p.blocker_text.length ? p.blocker_text.join('; ') : p.reason_text ?? p.reason ?? p.game_status.toLowerCase()
  return `${when}: ${why}`
}

export function modelLine(m: ModelStatus | null | undefined, engineName = 'V18'): StatusLine | null {
  if (!m) return null
  const s = m.schedule
  const plan = s.publication_target
    ? `input checks from ${t(s.input_checks_from)} · publication target ${t(s.publication_target)} (a target, not a guaranteed arrival) · decision window ${t(s.window_opens_at)}–${t(s.window_closes_at)} ET`
    : `decision window ${t(s.window_opens_at)}–${t(s.window_closes_at)} ET`
  const producer = producerText(m.producer)
  const join = (...parts: string[]) => parts.filter(Boolean).join(' · ')
  switch (m.state) {
    case 'PUBLISHED':
      return { tone: 'good', label: `${engineName} forecast published`, detail: `Published ${t(m.published_at)} ET` }
    case 'PUBLISHED_NOT_IMPORTED':
      return { tone: 'watch', label: 'Published, import pending',
        detail: join(`The producer lists a published ${engineName} forecast that AISP has not bound yet (imports run every 5 minutes)`, producer) }
    case 'SCHEDULED':
      return { tone: 'neutral', label: `${engineName} due by ${t(s.publication_target)} (target)`, detail: join(plan, producer) }
    case 'WAITING_ON_INPUTS':
      return { tone: 'watch', label: `${engineName} waiting on inputs`, detail: join(plan, producer) }
    case 'OVERDUE':
      return { tone: 'bad', label: `${engineName} overdue`,
        detail: join(`Publication target ${t(s.publication_target)} passed with no ${engineName} forecast · decision window ${t(s.window_opens_at)}–${t(s.window_closes_at)} ET`, producer) }
    case 'MISSED':
      return { tone: 'bad', label: `No ${engineName} forecast by the deadline`,
        detail: join(`No forecast arrived before the ${t(s.window_closes_at)} ET decision deadline`, producer) }
    case 'NOT_PUBLISHED':
      return { tone: 'neutral', label: 'No forecast bound', detail: plan }
  }
}

/** One short phrase for a dense row; the full line belongs in its title. */
export function marketShort(m: MarketStatus | null | undefined): string | null {
  if (!m) return null
  if (m.state === 'ELIGIBLE_THROUGH_WINDOW' || m.state === 'EXPIRES_DURING_WINDOW' || m.state === 'EXPIRES_BEFORE_WINDOW') {
    return `${books(m.books_fresh_now)} current · usable until ${t(m.fresh_until)}`
  }
  return marketLine(m)?.label ?? null
}

/** When the controlling forecast is expected: the T-45 publication target, never a promise. */
export function publicationTarget(m: ModelStatus | null | undefined): string | null {
  return m?.schedule.publication_target ?? null
}
