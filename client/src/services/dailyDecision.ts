/**
 * Derives the consumer-facing daily decision experience from persisted authority.
 *
 * Every function here is pure and takes `now` explicitly, so the grouping and
 * the countdowns are testable and never depend on a hidden clock.
 *
 * COUNTING UNIT. One `NormalizedSelection` is one *governed decision*: the
 * adapter emits one per (game x active experiment policy), so a single game
 * with two active policies produces two decisions that may legitimately
 * disagree. The four groups therefore partition decisions, never games, and
 * the UI must say so — collapsing them to one row per game would hide a real
 * disagreement between two governed policies.
 *
 * The single rule behind all of it: this module *translates* persisted state,
 * it never infers authority. In particular it must keep four situations apart
 * that a naive "is there a position?" check would collapse into one:
 *
 *   - not yet evaluated            (no governed decision exists)
 *   - blocked by missing evidence  (evaluation cannot complete)
 *   - evaluated and did not qualify (a real, final negative decision)
 *   - qualified                    (a final positive decision)
 *
 * Only the third is a "pass". Treating the first two as a pass would invent a
 * decision the platform never made.
 */
import type { NormalizedSelection } from '../types/multisport'

export type DecisionGroup = 'READY' | 'WATCHING' | 'PASSED' | 'CLOSED'

export type DecisionReasonCode =
  | 'READY_FUNDED'
  | 'READY_QUALIFIED_UNFUNDED'
  | 'WATCH_AWAITING_EVIDENCE'
  | 'WATCH_PROVISIONAL'
  | 'WATCH_WINDOW_CLOSED'
  | 'WATCH_NOT_EVALUATED'
  | 'WATCH_MARKET_UNSUPPORTED'
  | 'WATCH_RESEARCH_ONLY'
  // A decision with no sportsbook price cannot be evaluated at all, and a
  // decision priced by a quote too old to use is a different problem. Neither
  // is "under review": nothing is reviewing them.
  | 'WATCH_NO_MARKET_PRICE'
  | 'WATCH_MARKET_PRICE_STALE'
  | 'PASSED_NO_QUALIFYING_EDGE'
  | 'PASSED_POLICY_REJECTION'
  // Closed-for-decisions reasons. These are deliberately NOT interchangeable:
  // a postponed game is not a final game, and neither is a settled wager.
  | 'CLOSED_POSITION_SETTLED'
  | 'CLOSED_POSITION_VOID'
  | 'CLOSED_FINAL_SETTLEMENT_PENDING'
  | 'CLOSED_FINAL_NO_POSITION'
  | 'CLOSED_IN_PROGRESS'
  | 'CLOSED_POSTPONED'
  | 'CLOSED_CANCELED'

export interface DecisionCheckpoint {
  label: string
  at: string
}

export interface DerivedDecision {
  group: DecisionGroup
  reasonCode: DecisionReasonCode
  /** Short plain-language status shown as the card's second line. */
  status: string
  /** Optional supporting sentence. Never raw internal exception text. */
  detail: string | null
  /** Next known checkpoint, or null when no timestamp supports one. */
  checkpoint: DecisionCheckpoint | null
}

const SETTLED_POSITION_STATES = new Set(['SETTLED'])
const VOID_POSITION_STATES = new Set(['VOID'])
const COMPLETED_EVENT_STATES = new Set(['FINAL', 'COMPLETED', 'CLOSED'])
/** The event has started. No pregame decision can be taken or awaited. */
const IN_PROGRESS_EVENT_STATES = new Set(['LIVE', 'IN_PROGRESS', 'INPROGRESS', 'STARTED'])

function parse(at: string | null | undefined): number | null {
  if (!at) return null
  const value = Date.parse(at)
  return Number.isNaN(value) ? null : value
}

function isFuture(at: string | null | undefined, now: number): boolean {
  const value = parse(at)
  return value !== null && value > now
}

/**
 * The next meaningful moment we can *prove* from persisted timestamps.
 * Returns null rather than inventing a countdown when nothing is recorded.
 */
export function nextCheckpoint(
  selection: NormalizedSelection,
  now: number,
): DecisionCheckpoint | null {
  const candidates: DecisionCheckpoint[] = []
  if (isFuture(selection.next_scheduled_refresh_at, now)) {
    candidates.push({ label: 'Next forecast refresh', at: selection.next_scheduled_refresh_at! })
  }
  if (isFuture(selection.final_decision_cutoff, now)) {
    candidates.push({ label: 'Final decision cutoff', at: selection.final_decision_cutoff! })
  }
  if (isFuture(selection.starts_at, now)) {
    candidates.push({ label: 'Scheduled start', at: selection.starts_at! })
  }
  if (candidates.length === 0) return null
  return candidates.reduce((earliest, item) => (
    (parse(item.at) ?? Infinity) < (parse(earliest.at) ?? Infinity) ? item : earliest
  ))
}

/** True once the governed final-decision cutoff has passed. */
export function cutoffPassed(selection: NormalizedSelection, now: number): boolean {
  const cutoff = parse(selection.final_decision_cutoff)
  return cutoff !== null && cutoff <= now
}

function firstGate(selection: NormalizedSelection): string | null {
  const gate = selection.outstanding_gates.find((item) => item.trim().length > 0)
  return gate ? humanizeGate(gate) : null
}

/**
 * Turns an internal gate token into consumer language.
 *
 * Unknown tokens are lower-cased and de-underscored rather than passed through
 * verbatim, so an internal identifier never becomes the primary message. The
 * exact original stays available in the card's details area.
 */
export function humanizeGate(gate: string): string {
  const token = gate.trim().toUpperCase().replaceAll(' ', '_')
  const known: Record<string, string> = {
    NO_TWO_SIDED_QUOTE: 'Waiting for a complete two-sided sportsbook quote',
    MISSING_TWO_SIDED_QUOTE: 'Waiting for a complete two-sided sportsbook quote',
    INSUFFICIENT_BOOKS: 'Waiting for enough sportsbooks to report a price',
    NO_MARKET: 'No sportsbook price has been recorded yet',
    STALE_MARKET: 'The recorded sportsbook price is too old to use',
    STALE_ODDS: 'The recorded sportsbook price is too old to use',
    LINEUP_NOT_CONFIRMED: 'Starting lineup has not been confirmed',
    MISSING_LINEUP: 'Starting lineup has not been confirmed',
    LINEUP_MISSING: 'Starting lineup has not been confirmed',
    STARTER_NOT_CONFIRMED: 'Probable starter has not been confirmed',
    MISSING_STARTER: 'Probable starter has not been confirmed',
    NO_FORECAST: 'No forecast has been produced yet',
    FORECAST_STALE: 'The forecast is older than the policy allows',
    BELOW_THRESHOLD: 'Available price did not meet the policy threshold',
    EDGE_BELOW_THRESHOLD: 'Available price did not meet the policy threshold',
    INSUFFICIENT_EDGE: 'Available price did not meet the policy threshold',
    NO_CLOSING_LINE: 'Closing price was not captured',
    TIER_C: 'Evidence quality is below the policy tier',
    LOW_READINESS: 'Evidence readiness is below the policy threshold',
  }
  if (known[token]) return known[token]
  const spaced = gate.trim().replaceAll('_', ' ').toLowerCase()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function closed(
  reasonCode: DecisionReasonCode,
  status: string,
  detail: string | null,
): DerivedDecision {
  return { group: 'CLOSED', reasonCode, status, detail, checkpoint: null }
}

/**
 * Classifies one governed decision into a consumer group with plain language.
 *
 * Precedence, highest first — each step is a fact the later steps cannot
 * override, and each keeps its own distinct wording:
 *
 *   1. position voided        (ledger)
 *   2. position settled       (ledger)
 *   3. postponed / canceled   (event)  — NOT a final game, NOT a settled wager
 *   4. event final            (event)  — split by whether a position exists
 *   5. event in progress      (event)  — the pregame window is closed by definition
 *   6. final qualified decision         -> READY
 *   7. final negative decision          -> PASSED
 *   8. everything still open            -> WATCHING
 *
 * Steps 3-5 exist because a pregame decision cannot stay actionable once the
 * event has started: presenting a live game as READY, or as awaiting further
 * pregame evidence, would invite an action that is no longer possible.
 */
export function deriveDecision(selection: NormalizedSelection, now: number): DerivedDecision {
  const checkpoint = nextCheckpoint(selection, now)
  const positionStatus = (selection.position_status ?? '').toUpperCase()
  const eventStatus = (selection.event_status ?? '').toUpperCase()
  const hasPosition = Boolean(selection.official_position_id)

  if (VOID_POSITION_STATES.has(positionStatus)) {
    return closed('CLOSED_POSITION_VOID', 'Virtual position voided', 'The position was voided in the experiment ledger.')
  }
  if (SETTLED_POSITION_STATES.has(positionStatus)) {
    const result = (selection.position_result ?? '').toUpperCase()
    const outcome = result === 'WIN' ? 'won' : result === 'LOSS' ? 'lost' : result === 'PUSH' ? 'pushed' : null
    return closed(
      'CLOSED_POSITION_SETTLED',
      outcome ? `Final — virtual position settled, ${outcome}` : 'Final — virtual position settled',
      'Recorded in the experiment ledger.',
    )
  }
  if (eventStatus === 'POSTPONED') {
    return closed(
      'CLOSED_POSTPONED',
      'Postponed',
      hasPosition
        ? 'The game was postponed. The virtual position has not been settled.'
        : 'The game was postponed. No result is recorded.',
    )
  }
  if (eventStatus === 'CANCELED' || eventStatus === 'CANCELLED') {
    return closed(
      'CLOSED_CANCELED',
      'Canceled',
      hasPosition
        ? 'The game was canceled. The virtual position has not been settled.'
        : 'The game was canceled. No result is recorded.',
    )
  }
  if (COMPLETED_EVENT_STATES.has(eventStatus)) {
    return hasPosition
      ? closed('CLOSED_FINAL_SETTLEMENT_PENDING', 'Final — settlement pending', 'The virtual position has not been settled yet.')
      : closed('CLOSED_FINAL_NO_POSITION', 'Final — no position was taken', null)
  }
  if (IN_PROGRESS_EVENT_STATES.has(eventStatus)) {
    return closed(
      'CLOSED_IN_PROGRESS',
      'In progress — pregame decision window closed',
      hasPosition
        ? 'A virtual position is open and will settle after the game.'
        : 'No further pregame decision is possible for this game.',
    )
  }

  if (selection.decision_state === 'FINAL_QUALIFIED') {
    if (selection.funding_state === 'OFFICIAL_VIRTUAL_POSITION' && hasPosition) {
      return {
        group: 'READY',
        reasonCode: 'READY_FUNDED',
        status: 'Qualified virtual position recorded before the cutoff',
        detail: null,
        checkpoint,
      }
    }
    return {
      group: 'READY',
      reasonCode: 'READY_QUALIFIED_UNFUNDED',
      status: 'Qualified under policy — no virtual position was funded',
      detail: 'A qualification is not a position. Nothing was staked.',
      checkpoint,
    }
  }

  if (selection.decision_state === 'FINAL_NO_BET') {
    return {
      group: 'PASSED',
      reasonCode: 'PASSED_NO_QUALIFYING_EDGE',
      status: 'Evaluated — available price did not meet the policy threshold',
      detail: firstGate(selection),
      checkpoint: null,
    }
  }
  if (selection.decision_state === 'PASS') {
    return {
      group: 'PASSED',
      reasonCode: 'PASSED_POLICY_REJECTION',
      status: 'Evaluated — did not qualify under the experiment policy',
      detail: firstGate(selection),
      checkpoint: null,
    }
  }

  if (selection.funding_state === 'MARKET_NOT_SUPPORTED') {
    return {
      group: 'WATCHING',
      reasonCode: 'WATCH_MARKET_UNSUPPORTED',
      status: 'Not evaluated — outside this experiment’s funded market',
      detail: 'The experiment funds the full-game moneyline only.',
      checkpoint,
    }
  }
  if (selection.decision_state === 'AWAITING_FINAL_EVIDENCE') {
    if (selection.market_evidence_state === 'QUOTE_MISSING') {
      return {
        group: 'WATCHING',
        reasonCode: 'WATCH_NO_MARKET_PRICE',
        status: 'Cannot be evaluated yet — no sportsbook price is available',
        detail: 'A decision needs a complete two-sided quote before it can be priced.',
        checkpoint,
      }
    }
    if (selection.market_evidence_state === 'QUOTE_STALE') {
      return {
        group: 'WATCHING',
        reasonCode: 'WATCH_MARKET_PRICE_STALE',
        status: 'Cannot be evaluated yet — the latest sportsbook price is too old to use',
        detail: 'A quote exists but falls outside the policy freshness window.',
        checkpoint,
      }
    }
    return {
      group: 'WATCHING',
      reasonCode: 'WATCH_AWAITING_EVIDENCE',
      status: firstGate(selection) ?? 'Waiting for complete final evidence',
      detail: selection.final_decision_cutoff ? null : 'No decision cutoff is recorded for this game.',
      checkpoint,
    }
  }
  if (selection.decision_state === 'PROVISIONAL_EDGE' || selection.decision_state === 'WATCH') {
    if (selection.funding_state === 'RESEARCH_ONLY') {
      return {
        group: 'WATCHING',
        reasonCode: 'WATCH_RESEARCH_ONLY',
        status: 'Research shadow — no funded decision is possible',
        detail: firstGate(selection),
        checkpoint,
      }
    }
    return {
      group: 'WATCHING',
      reasonCode: 'WATCH_PROVISIONAL',
      status: 'Forecast is ready; the final decision window has not closed',
      detail: firstGate(selection),
      checkpoint,
    }
  }

  // NOT_EVALUATED means two different things. Keep them apart.
  if (cutoffPassed(selection, now)) {
    return {
      group: 'WATCHING',
      reasonCode: 'WATCH_WINDOW_CLOSED',
      status: 'Decision window closed without a final recorded decision',
      detail: 'No cutoff-safe decision was persisted, so no decision is claimed.',
      checkpoint: null,
    }
  }
  return {
    group: 'WATCHING',
    reasonCode: selection.funding_state === 'RESEARCH_ONLY' ? 'WATCH_RESEARCH_ONLY' : 'WATCH_NOT_EVALUATED',
    status: selection.funding_state === 'RESEARCH_ONLY'
      ? 'Research shadow — no funded decision is possible'
      : 'Not yet evaluated for today’s slate',
    detail: firstGate(selection),
    checkpoint,
  }
}

/**
 * Fair American odds implied by the displayed model probability.
 * Returns null outside (0, 100) so an impossible price is never rendered.
 */
export function fairAmericanOdds(probabilityPercent: number | null): number | null {
  if (probabilityPercent == null) return null
  const p = probabilityPercent / 100
  if (!Number.isFinite(p) || p <= 0 || p >= 1) return null
  return p >= 0.5 ? -Math.round((100 * p) / (1 - p)) : Math.round((100 * (1 - p)) / p)
}

export interface EdgeDisplay {
  label: string
  valuePercent: number
}

/**
 * An edge is shown ONLY when both probabilities behind it are available.
 * A missing sportsbook probability must never surface as 0%.
 */
export function edgeDisplay(selection: NormalizedSelection): EdgeDisplay | null {
  if (selection.model_probability == null || selection.no_vig_probability == null) return null
  if (selection.adjusted_edge != null) {
    return { label: 'Policy-adjusted edge', valuePercent: selection.adjusted_edge }
  }
  return {
    label: 'Raw edge',
    valuePercent: selection.model_probability - selection.no_vig_probability,
  }
}

export const GROUP_ORDER: DecisionGroup[] = ['READY', 'WATCHING', 'PASSED', 'CLOSED']

export interface GroupedGame {
  selection: NormalizedSelection
  decision: DerivedDecision
}

/** Groups decisions, preserving each sport's own governed ordering. */
export function groupSelections(
  selections: NormalizedSelection[],
  now: number,
): Record<DecisionGroup, GroupedGame[]> {
  const grouped: Record<DecisionGroup, GroupedGame[]> = {
    READY: [], WATCHING: [], PASSED: [], CLOSED: [],
  }
  selections.forEach((selection) => {
    const decision = deriveDecision(selection, now)
    grouped[decision.group].push({ selection, decision })
  })
  return grouped
}

/** Distinct games represented by a set of decisions. */
export function distinctGameCount(items: { selection: NormalizedSelection }[]): number {
  return new Set(items.map((item) => item.selection.event_id)).size
}

/**
 * The soonest checkpoint across every watching decision — the slate's own
 * "next meaningful moment". Null when nothing is scheduled.
 */
export function slateNextCheckpoint(
  watching: GroupedGame[],
): (DecisionCheckpoint & { eventLabel: string }) | null {
  const points = watching
    .filter((item) => item.decision.checkpoint !== null)
    .map((item) => ({ ...item.decision.checkpoint!, eventLabel: item.selection.event_label }))
  if (points.length === 0) return null
  return points.reduce((earliest, item) => (
    (parse(item.at) ?? Infinity) < (parse(earliest.at) ?? Infinity) ? item : earliest
  ))
}

/**
 * The honest one-line slate message.
 *
 * It speaks in decisions, matching the group counts exactly, so the sentence
 * and the tiles can never disagree. It never implies an edge, a profit, or a
 * recommendation.
 */
export function slateMessage(
  counts: Record<DecisionGroup, number>,
  readyFunded = 0,
  /** Watching decisions that hold no usable sportsbook price. */
  marketBlocked = 0,
): string {
  const { READY, WATCHING, PASSED, CLOSED } = counts
  const total = READY + WATCHING + PASSED + CLOSED
  if (total === 0) return 'No governed slate is persisted for this date.'

  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

  if (READY === 0 && WATCHING === 0) {
    const parts: string[] = []
    if (CLOSED > 0) parts.push(`${plural(CLOSED, 'decision is', 'decisions are')} closed`)
    if (PASSED > 0) parts.push(`${plural(PASSED, 'decision', 'decisions')} passed`)
    return `Nothing is left to decide today: ${parts.join(' and ')}.`
  }
  if (READY === 0) {
    // "Under review" implies something is progressing. When a decision has no
    // usable price, nothing is reviewing it, so say what is actually true.
    if (marketBlocked >= WATCHING && WATCHING > 0) {
      return (
        `No qualified decisions yet. ${plural(WATCHING, 'decision', 'decisions')} `
        + `${WATCHING === 1 ? 'cannot' : 'cannot'} be evaluated against a sportsbook price yet.`
      )
    }
    if (marketBlocked > 0) {
      return (
        `No qualified decisions yet. ${plural(WATCHING, 'decision is', 'decisions are')} open, `
        + `${marketBlocked} of them awaiting a usable sportsbook price.`
      )
    }
    return `No qualified decisions yet. ${plural(WATCHING, 'decision is', 'decisions are')} still under review.`
  }
  const ready = readyFunded === READY
    ? `${plural(READY, 'virtual position is', 'virtual positions are')} ready`
    : `${plural(READY, 'qualified decision is', 'qualified decisions are')} ready`
  const tail = WATCHING > 0 ? ` ${plural(WATCHING, 'decision remains', 'decisions remain')} under review.` : ''
  return `${ready}.${tail}`
}
