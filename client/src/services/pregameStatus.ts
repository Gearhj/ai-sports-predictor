/**
 * A game's market and its model, stated independently.
 *
 * "Awaiting first forecast and quote" merged two questions and was wrong about
 * one of them whenever fresh quotes were stored. The server now answers each
 * from its own evidence (services/pregame_status.py); this turns those answers
 * into card lines. A publication time is always a TARGET, never a promise.
 */
import type { CheckpointSemantics } from './cfbMarket'

export type MarketState =
  | 'ELIGIBLE_THROUGH_WINDOW' | 'EXPIRES_DURING_WINDOW' | 'EXPIRES_BEFORE_WINDOW'
  | 'STORED_REFERENCE_ONLY' | 'NO_TWO_SIDED_QUOTE' | 'WINDOW_CLOSED'
  // College Football with no stored quote: whether any collector will fetch one.
  | 'COLLECTION_INACTIVE_NO_ALLOCATION' | 'NO_QUOTES_YET' | 'COLLECTION_STATE_UNKNOWN'

/** College Football's price lane, as the server read it from the capacity gate. */
export interface CollectionLane {
  lane: string | null
  /** false: nothing will fetch a price; null: the gate could not be read. */
  active: boolean | null
  lane_enabled: boolean | null
  allocation_state: string | null
  objects_allowed: number
  spent_on_lane: number | null
  explanation: string | null
  /** The Odds API lane (CFB since 2026-10-03, NHL): who prices the game and this game's buy window. */
  provider?: string | null
  opens_at?: string | null
  closes_at?: string | null
}

/** The server's state for a switched-on The Odds API CFB lane (one request per kickoff cluster, T-75..T-35). */
export const ODDS_API_LANE_ACTIVE = 'ODDS_API_LANE_ACTIVE'

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
  /** College Football only. */
  quotes_stored?: number
  collection?: CollectionLane
}

export type ModelState =
  | 'PUBLISHED' | 'PUBLISHED_NOT_IMPORTED' | 'SCHEDULED' | 'WAITING_ON_INPUTS' | 'OVERDUE' | 'MISSED' | 'NOT_PUBLISHED'
  // College Football: the producer's discovery lease on the forecast.
  | 'ELIGIBILITY_CURRENT' | 'ELIGIBILITY_EXPIRED' | 'ELIGIBILITY_NOT_CURRENT'

/** One discovery reading of a CFB forecast's lease (present, or as a checkpoint recorded it). */
export interface LeaseReading {
  state: string
  lease_valid_until?: string | null
  manifest_sha256?: string
  /** The producer's own manifest creation time: its last publication. */
  manifest_created_utc?: string | null
  producer_status?: string | null
}

/** A recorded CFB paper checkpoint, exactly as written. */
export interface RecordedCheckpoint {
  decision_id: string
  recorded_at: string | null
  recommendation: string
  reason: string | null
  detail: string | null
  discovery?: LeaseReading | null
  /** NO_BET (priced, edge below policy) vs NO_PRICE (declined) vs BLOCKED (not evaluated). */
  semantics?: CheckpointSemantics | null
}

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
  /** shadow_forecasts.created_at: the START of AISP's import transaction (not insert, commit or publication). */
  import_transaction_started_at?: string | null
  /** Older payloads carried the same value under this name. */
  published_at?: string | null
  schedule: {
    input_checks_from: string | null
    publication_target: string | null
    window_opens_at: string
    window_closes_at: string
    target_is_guaranteed: false
  }
  producer: ProducerReceipt | null
  /** College Football only: the lease the state was read from, the present one, and the checkpoint. */
  basis?: 'RECORDED_AT_CHECKPOINT' | 'PRESENT_DISCOVERY' | null
  eligibility?: LeaseReading | null
  eligibility_now?: LeaseReading | null
  checkpoint?: RecordedCheckpoint | null
  renewed_since_checkpoint?: boolean
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
    // College Football with no stored quote. Never "waiting": say whether anything will fetch one.
    case 'COLLECTION_INACTIVE_NO_ALLOCATION':
      return { tone: 'neutral', label: 'No sportsbook coverage',
        detail: `CFB price collection is not active (no allocation)${laneWhy(m.collection)} · no quote is stored for this game` }
    case 'NO_QUOTES_YET': {
      const c = m.collection
      if (c?.allocation_state === ODDS_API_LANE_ACTIVE) {
        // The Odds API lane prices each game once in its own window; after it, a missing quote means
        // the provider did not list the game or its team names did not bind -- not "waiting".
        const window = c.opens_at && c.closes_at ? ` (${t(c.opens_at)}–${t(c.closes_at)} ET)` : ''
        const passed = c.closes_at != null && Date.parse(c.closes_at) <= Date.now()
        return passed
          ? { tone: 'bad', label: 'No quote bound',
            detail: `The Odds API collection window${window} has passed with no quote bound to this game: the provider did not list it, or its team names or kickoff did not match` }
          : { tone: 'watch', label: 'No quotes yet',
            detail: `The Odds API prices this game once, 75–35 minutes before kickoff${window}; no quote is stored yet` }
      }
      return { tone: 'watch', label: 'No quotes yet',
        detail: `CFB price collection is active (${c?.objects_allowed ?? 0} objects allowed); no quote is stored for this game yet` }
    }
    case 'COLLECTION_STATE_UNKNOWN':
      return { tone: 'neutral', label: 'Sportsbook coverage unknown',
        detail: 'The CFB price-collection gate could not be read · no quote is stored for this game' }
  }
}

const ALLOCATION_TEXT: Record<string, string> = {
  NO_UNCOMMITTED_SGO_CAPACITY: 'no uncommitted SportsGameOdds capacity is left after MLB/NFL',
  LANE_DISABLED: 'the CFB lane is switched off',
  AUTHORIZATION_EXPIRED: 'the CFB lane authorization has expired',
  ASSIGNMENT_CAP_REACHED: 'the CFB lane has used its object cap',
}

/** Why the CFB lane grants nothing, from the server's reading of the gate. */
function laneWhy(c: CollectionLane | undefined): string {
  if (!c) return ''
  const parts: string[] = []
  if (c.allocation_state && c.allocation_state !== 'CAPACITY_AVAILABLE' && c.allocation_state !== 'PRICE_STATE_UNAVAILABLE') {
    parts.push(ALLOCATION_TEXT[c.allocation_state] ?? c.allocation_state.replace(/_/g, ' ').toLowerCase())
  }
  if (c.lane_enabled === false && c.allocation_state !== 'LANE_DISABLED') parts.push('the CFB lane is not switched on')
  if (c.spent_on_lane != null) parts.push(`${c.spent_on_lane} object${c.spent_on_lane === 1 ? '' : 's'} spent on it`)
  return parts.length ? `: ${parts.join('; ')}` : ''
}

const LEASE_TEXT: Record<string, string> = {
  HISTORICAL: 'the producer lists this forecast as historical, not currently eligible',
  NOT_IN_CURRENT_DISCOVERY: "the producer's newest manifest does not list this forecast",
  REJECTED: 'AISP could not verify this package in the newest manifest',
  NO_VERIFIED_DISCOVERY: 'AISP has verified no discovery manifest',
}

function checkpointText(c: RecordedCheckpoint | null | undefined): string {
  if (!c) return ''
  // A priced no-bet, a declined no-price and a block are three different results.
  const kind = c.semantics?.class
  const verb = kind === 'NO_BET' ? 'evaluated at a usable price and found no bet'
    : kind === 'NO_PRICE' ? 'declined: no usable two-sided price'
      : kind === 'NO_ROOM' ? 'declined for budget: the value qualified, but the shared $100 had no room left'
      : kind === 'BLOCKED' || c.recommendation === 'BLOCKED' ? 'was blocked'
        : kind === 'QUALIFIED_NOT_ACTIVATED' ? 'qualified, but the activation control was off'
          : c.recommendation === 'DECLINED' ? 'declined'
            : c.recommendation === 'QUALIFIED_BET' ? 'qualified' : `recorded ${c.recommendation.toLowerCase()}`
  return `the ${t(c.recorded_at)} ET checkpoint ${verb}${c.reason ? ` (${c.reason})` : ''}`
}

/** What the publisher has done since: the present lease, told apart from the recorded one. */
function renewalText(m: ModelStatus): string {
  const now = m.eligibility_now
  if (!now) return ''
  const manifest = now.manifest_created_utc ? ` (newest manifest ${t(now.manifest_created_utc)} ET)` : ''
  if (m.renewed_since_checkpoint) {
    return `renewed after the checkpoint: current until ${t(now.lease_valid_until)} ET${manifest}; the recorded checkpoint stands`
  }
  if (now.state === 'EXPIRED_LEASE') {
    const same = !m.eligibility?.lease_valid_until || now.lease_valid_until === m.eligibility.lease_valid_until
      || Date.parse(now.lease_valid_until ?? '') === Date.parse(m.eligibility.lease_valid_until)
    return same ? `the publisher has not renewed it${manifest}` : `its newest lease also expired, at ${t(now.lease_valid_until)} ET${manifest}`
  }
  if (now.state === 'CURRENT') return `lease current until ${t(now.lease_valid_until)} ET${manifest}`
  return `now: ${LEASE_TEXT[now.state] ?? now.state.replace(/_/g, ' ').toLowerCase()}${manifest}`
}

/** College Football: the producer's eligibility lease, never the price. */
function eligibilityLine(m: ModelStatus): StatusLine | null {
  const lease = m.eligibility
  const recorded = m.basis === 'RECORDED_AT_CHECKPOINT'
  const checkpoint = checkpointText(m.checkpoint)
    || `the ${t(m.schedule.window_opens_at)} ET checkpoint needs a current lease`
  const join = (...parts: string[]) => parts.filter(Boolean).join('; ')
  switch (m.state) {
    case 'ELIGIBILITY_EXPIRED':
      return { tone: 'bad', label: 'Forecast eligibility expired',
        detail: join(`Forecast eligibility expired at ${t(lease?.lease_valid_until)} ET`, renewalText(m), checkpoint) }
    case 'ELIGIBILITY_CURRENT':
      return { tone: 'good', label: 'Forecast eligible',
        detail: recorded
          ? join(`Lease current at the checkpoint (valid until ${t(lease?.lease_valid_until)} ET)`, checkpoint)
          : join(`Publisher lease current until ${t(lease?.lease_valid_until)} ET${lease?.manifest_created_utc ? ` (manifest ${t(lease.manifest_created_utc)} ET)` : ''}`, checkpoint) }
    case 'ELIGIBILITY_NOT_CURRENT': {
      const why = lease ? LEASE_TEXT[lease.state] ?? lease.state.replace(/_/g, ' ').toLowerCase() : 'no discovery reading'
      return { tone: 'bad', label: 'Forecast not currently eligible',
        detail: join(`Forecast not eligible: ${why}`, recorded ? renewalText(m) : '', checkpoint) }
    }
    default:
      return null
  }
}

/** The producer checks every five minutes from T-60; after that, a receipt older than this is itself news. */
const RECEIPT_STALE_MINUTES = 20

function producerText(p: ProducerReceipt | null, fastChecksFrom: string | null, now: number): string {
  if (!p) return ''
  const age = p.receipt_age_minutes != null ? Math.round(p.receipt_age_minutes) : null
  const fast = fastChecksFrom != null && now >= Date.parse(fastChecksFrom)
  const stale = fast && age != null && age > RECEIPT_STALE_MINUTES
  const when = stale
    ? `No producer receipt since ${t(p.receipt_generated_at)} (${age} min)`
    : `Producer receipt ${t(p.receipt_generated_at)}${age != null ? ` (${age} min ago)` : ''}`
  if (p.game_status === 'NOT_IN_RECEIPT') return `${when}: this game is not listed`
  const why = p.blocker_text.length ? p.blocker_text.join('; ') : p.reason_text ?? p.reason ?? p.game_status.toLowerCase()
  return stale ? `${when}; its last reason for this game: ${why}` : `${when}: ${why}`
}

export function modelLine(m: ModelStatus | null | undefined, engineName = 'V18', now = Date.now()): StatusLine | null {
  if (!m) return null
  const s = m.schedule
  const plan = s.publication_target
    ? `input checks from ${t(s.input_checks_from)} · publication target ${t(s.publication_target)} (a target, not a guaranteed arrival) · decision window ${t(s.window_opens_at)}–${t(s.window_closes_at)} ET`
    : `decision window ${t(s.window_opens_at)}–${t(s.window_closes_at)} ET`
  const producer = producerText(m.producer, s.input_checks_from, now)
  const join = (...parts: string[]) => parts.filter(Boolean).join(' · ')
  switch (m.state) {
    case 'PUBLISHED':
      // shadow_forecasts.created_at is AISP's IMPORT time, not the producer's
      // publication or creation time; say which clock this is.
      return { tone: 'good', label: `${engineName} forecast in AISP`,
        detail: `AISP import transaction began ${t(m.import_transaction_started_at ?? m.published_at)} ET (the row is usable once that transaction commits; the exact insert and commit time is not recorded)` }
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
    case 'ELIGIBILITY_CURRENT':
    case 'ELIGIBILITY_EXPIRED':
    case 'ELIGIBILITY_NOT_CURRENT':
      return eligibilityLine(m)
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
