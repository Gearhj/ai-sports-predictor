/**
 * Derives the consumer-facing daily decision experience from persisted authority.
 *
 * Every function here is pure and takes `now` explicitly, so the grouping and
 * the countdowns are testable and never depend on a hidden clock.
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

export type DecisionGroup = 'READY' | 'WATCHING' | 'PASSED' | 'FINAL'

export type DecisionReasonCode =
  | 'READY_FUNDED'
  | 'READY_QUALIFIED_UNFUNDED'
  | 'WATCH_AWAITING_EVIDENCE'
  | 'WATCH_PROVISIONAL'
  | 'WATCH_WINDOW_CLOSED'
  | 'WATCH_NOT_EVALUATED'
  | 'WATCH_MARKET_UNSUPPORTED'
  | 'WATCH_RESEARCH_ONLY'
  | 'PASSED_NO_QUALIFYING_EDGE'
  | 'PASSED_POLICY_REJECTION'
  | 'FINAL_POSITION_SETTLED'
  | 'FINAL_POSITION_VOID'
  | 'FINAL_AWAITING_SETTLEMENT'
  | 'FINAL_POSTPONED'
  | 'FINAL_CANCELED'

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

function final(
  reasonCode: DecisionReasonCode,
  status: string,
  detail: string | null,
): DerivedDecision {
  return { group: 'FINAL', reasonCode, status, detail, checkpoint: null }
}

/**
 * Classifies one selection into a consumer group with a plain-language reason.
 *
 * Precedence is deliberate: completion outranks decision state, because a
 * settled position or a finished game is the last word regardless of what the
 * pregame decision was.
 */
export function deriveDecision(selection: NormalizedSelection, now: number): DerivedDecision {
  const checkpoint = nextCheckpoint(selection, now)
  const positionStatus = (selection.position_status ?? '').toUpperCase()
  const eventStatus = (selection.event_status ?? '').toUpperCase()

  // 1. Completion, from persisted ledger/event state only.
  if (VOID_POSITION_STATES.has(positionStatus)) {
    return final('FINAL_POSITION_VOID', 'Virtual position voided', 'The position was voided in the experiment ledger.')
  }
  if (SETTLED_POSITION_STATES.has(positionStatus)) {
    const result = (selection.position_result ?? '').toUpperCase()
    const outcome = result === 'WIN' ? 'won' : result === 'LOSS' ? 'lost' : result === 'PUSH' ? 'pushed' : null
    return final(
      'FINAL_POSITION_SETTLED',
      outcome ? `Virtual position settled — ${outcome}` : 'Virtual position settled',
      'Recorded in the experiment ledger.',
    )
  }
  if (eventStatus === 'POSTPONED') {
    return final('FINAL_POSTPONED', 'Game postponed', 'No decision is carried forward automatically.')
  }
  if (eventStatus === 'CANCELED' || eventStatus === 'CANCELLED') {
    return final('FINAL_CANCELED', 'Game canceled', null)
  }
  if (COMPLETED_EVENT_STATES.has(eventStatus)) {
    return final(
      'FINAL_AWAITING_SETTLEMENT',
      selection.official_position_id ? 'Game completed; settlement pending' : 'Game completed',
      selection.official_position_id ? 'The virtual position has not been settled yet.' : null,
    )
  }

  // 2. A final positive decision.
  if (selection.decision_state === 'FINAL_QUALIFIED') {
    if (selection.funding_state === 'OFFICIAL_VIRTUAL_POSITION' && selection.official_position_id) {
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

  // 3. A final negative decision. Only these are a genuine "pass".
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

  // 4. Everything still open. None of these is a pass.
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

export const GROUP_ORDER: DecisionGroup[] = ['READY', 'WATCHING', 'PASSED', 'FINAL']

export interface GroupedGame {
  selection: NormalizedSelection
  decision: DerivedDecision
}

/** Groups selections, preserving each sport's own governed ordering. */
export function groupSelections(
  selections: NormalizedSelection[],
  now: number,
): Record<DecisionGroup, GroupedGame[]> {
  const grouped: Record<DecisionGroup, GroupedGame[]> = {
    READY: [], WATCHING: [], PASSED: [], FINAL: [],
  }
  selections.forEach((selection) => {
    const decision = deriveDecision(selection, now)
    grouped[decision.group].push({ selection, decision })
  })
  return grouped
}

/**
 * The soonest checkpoint across every watching game — the slate's own
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
 * The honest one-line slate message. Describes what the day actually is; it
 * never implies an edge, a profit, or a recommendation.
 */
export function slateMessage(counts: Record<DecisionGroup, number>): string {
  const { READY, WATCHING, PASSED, FINAL } = counts
  const total = READY + WATCHING + PASSED + FINAL
  if (total === 0) return 'No governed slate is persisted for this date.'

  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

  if (READY === 0 && WATCHING === 0 && total > 0) {
    const parts: string[] = []
    if (FINAL > 0) parts.push(`${plural(FINAL, 'game is', 'games are')} complete`)
    if (PASSED > 0) parts.push(`${plural(PASSED, 'game', 'games')} passed`)
    return `Today’s slate is complete: ${parts.join(' and ')}.`
  }
  if (READY === 0) {
    return `No qualified positions yet. ${plural(WATCHING, 'game is', 'games are')} still under review.`
  }
  const tail = WATCHING > 0 ? ` ${plural(WATCHING, 'game remains', 'games remain')} under review.` : ''
  return `${plural(READY, 'qualified position is', 'qualified positions are')} ready.${tail}`
}
