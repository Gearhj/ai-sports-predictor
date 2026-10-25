/**
 * Active Wagers: the funded paper positions still in play, across every sport.
 *
 * ONE AUTHORITY FOR MONEY
 * -----------------------
 * Every dollar here comes from `/command-center/ledger` -- the same read model,
 * under the same cache key, that the $1,000 Experiment page draws. A position is
 * "active" exactly when the experiment page lists it under Open positions
 * (`status !== 'SETTLED'`), so the panel's count and stake at risk are the
 * page's own numbers and cannot drift from them.
 *
 * STATUS IS OBSERVED, NEVER GUESSED
 * ---------------------------------
 * Live / Upcoming / Awaiting settlement is decided from the shared scoreboard
 * (observed game state) and the decision board's `awaiting_settlement` flag.
 * Where neither has a reading for a game that has already started, the card
 * says so ("started · live status not available") instead of inventing one.
 *
 * WATCHING IS NOT MONEY
 * ---------------------
 * Decisions without a position are shown separately and never enter a total.
 */
import type { GameScoreboard } from '../types/scoreboard'
import type { DecisionItem, Ledger, LedgerPosition, SportCode } from './commandCenter'
import { SHADOW_ONLY_LABEL } from './decisionLead'
import { decimalFromAmerican } from './topPlay'

export type WagerGroup = 'LIVE' | 'UPCOMING' | 'AWAITING'

export const WAGER_GROUP_ORDER: WagerGroup[] = ['LIVE', 'UPCOMING', 'AWAITING']
export const WAGER_GROUP_LABEL: Record<WagerGroup, string> = {
  LIVE: 'Live',
  UPCOMING: 'Upcoming',
  AWAITING: 'Awaiting settlement',
}

export interface ActiveWager extends LedgerPosition {
  sport: SportCode
  group: WagerGroup
  /** Plain status line for the card. */
  statusText: string
  /** Total returned if it wins, stake included. Null when the entry price is unknown. */
  potentialPayout: number | null
  /** The decision that funded it, when the decision board holds it. */
  decision: DecisionItem | null
  /** Minutes between the quote's observation and the placement, when both are recorded. */
  quoteAgeAtPlacementMinutes: number | null
}

export interface WatchItem {
  decision_id: string
  sport: SportCode
  game_id: string
  matchup: string | null
  starts_at: string | null
  selection: string | null
  market: string
  american: number | null
  sportsbook: string | null
  label: string
  tone: 'good' | 'warn' | 'bad' | ''
}

export interface ActiveWagerSummary {
  openCount: number
  /** Sum of the open stakes (deduplicated). */
  stakeAtRisk: number
  /** The ledger's own combined open exposure, for reconciliation. */
  ledgerOpenExposure: number | null
  ledgerOpenCount: number | null
  /** True when the positions add up to the ledger's own totals. */
  reconciled: boolean
  /** Realized paper bankroll across the four accounts (ledger totals). */
  bankroll: number | null
  available: number | null
  /** Earliest scheduled start among upcoming wagers. */
  nextStart: string | null
}

const LIVE_STATES = new Set(['IN_PROGRESS', 'DELAYED', 'SUSPENDED'])
const OVER_STATES = new Set(['FINAL', 'POSTPONED', 'CANCELLED'])

const time = (iso: string | null | undefined): number => {
  if (!iso) return Number.POSITIVE_INFINITY
  const t = Date.parse(iso)
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
}

/** Ascending scheduled start; unknown starts last; position id breaks ties so the order is stable. */
export const byStart = <T extends { starts_at: string | null; position_id?: string; decision_id?: string }>(a: T, b: T) =>
  time(a.starts_at) - time(b.starts_at)
  || (a.position_id ?? a.decision_id ?? '').localeCompare(b.position_id ?? b.decision_id ?? '')

/** Every open position in the ledger, once per position id, tagged with its account. */
export function openPositions(ledger: Ledger | null): Array<LedgerPosition & { sport: SportCode }> {
  if (!ledger) return []
  const seen = new Map<string, LedgerPosition & { sport: SportCode }>()
  for (const account of ledger.sports ?? []) {
    for (const p of account.positions ?? []) {
      if (!p?.position_id || p.status === 'SETTLED') continue
      if (!seen.has(p.position_id)) seen.set(p.position_id, { ...p, sport: account.sport })
    }
  }
  return [...seen.values()]
}

export function potentialPayout(p: Pick<LedgerPosition, 'stake' | 'entry'>): number | null {
  if (p.stake == null || !Number.isFinite(p.stake)) return null
  const decimal = p.entry?.decimal ?? (p.entry?.american != null && p.entry.american !== 0 ? decimalFromAmerican(p.entry.american) : null)
  if (decimal == null || !Number.isFinite(decimal)) return null
  return Math.round(p.stake * decimal * 100) / 100
}

export function classify(
  p: Pick<LedgerPosition, 'starts_at'>,
  score: GameScoreboard | null | undefined,
  decision: DecisionItem | null | undefined,
  now: number,
): { group: WagerGroup; statusText: string } {
  const status = score?.available ? score.status ?? null : null
  if (decision?.position?.awaiting_settlement || score?.governed_final || score?.is_final || (status && OVER_STATES.has(status))) {
    const why = status === 'POSTPONED' ? 'Postponed' : status === 'CANCELLED' ? 'Cancelled' : 'Final'
    return { group: 'AWAITING', statusText: `${why} · settlement pending` }
  }
  if (score?.is_live || (status && LIVE_STATES.has(status))) {
    const detail = score?.status_detail || (status === 'DELAYED' ? 'Delayed' : status === 'SUSPENDED' ? 'Suspended' : 'In progress')
    return { group: 'LIVE', statusText: detail }
  }
  if (time(p.starts_at) > now) return { group: 'UPCOMING', statusText: 'Scheduled' }
  if (p.starts_at == null) return { group: 'UPCOMING', statusText: 'Start time not available' }
  return { group: 'LIVE', statusText: 'Started · live status not available' }
}

/** Index decision-board items by the position they funded. */
export function decisionsByPosition(items: DecisionItem[]): Map<string, DecisionItem> {
  const map = new Map<string, DecisionItem>()
  for (const item of items) if (item.position?.position_id) map.set(item.position.position_id, item)
  return map
}

export function activeWagers(
  ledger: Ledger | null,
  scores: Record<string, GameScoreboard | undefined>,
  decisions: Map<string, DecisionItem>,
  now: number,
): Record<WagerGroup, ActiveWager[]> {
  const groups: Record<WagerGroup, ActiveWager[]> = { LIVE: [], UPCOMING: [], AWAITING: [] }
  for (const p of openPositions(ledger)) {
    const decision = decisions.get(p.position_id) ?? null
    const { group, statusText } = classify(p, scores[p.game_id], decision, now)
    const placed = p.placed_at ? Date.parse(p.placed_at) : NaN
    const quoted = p.entry?.quote_observed_at ? Date.parse(p.entry.quote_observed_at) : NaN
    groups[group].push({
      ...p, group, statusText, decision,
      potentialPayout: potentialPayout(p),
      quoteAgeAtPlacementMinutes: Number.isFinite(placed) && Number.isFinite(quoted) ? Math.max(0, Math.round((placed - quoted) / 60_000)) : null,
    })
  }
  for (const g of WAGER_GROUP_ORDER) groups[g].sort(byStart)
  return groups
}

const round2 = (v: number) => Math.round(v * 100) / 100

export function summarize(ledger: Ledger | null, groups: Record<WagerGroup, ActiveWager[]>): ActiveWagerSummary {
  const all = WAGER_GROUP_ORDER.flatMap((g) => groups[g])
  const stakeAtRisk = round2(all.reduce((s, p) => s + (p.stake ?? 0), 0))
  const ledgerOpenExposure = ledger?.totals?.open_exposure ?? null
  const ledgerOpenCount = ledger?.totals?.open_count ?? null
  const reconciled = ledger != null
    && (ledgerOpenExposure == null || Math.abs(ledgerOpenExposure - stakeAtRisk) < 0.005)
    && (ledgerOpenCount == null || ledgerOpenCount === all.length)
  return {
    openCount: all.length,
    stakeAtRisk,
    ledgerOpenExposure,
    ledgerOpenCount,
    reconciled,
    bankroll: ledger?.totals?.realized_bankroll ?? null,
    available: ledger?.totals?.available_capital ?? null,
    nextStart: groups.UPCOMING.find((p) => p.starts_at)?.starts_at ?? null,
  }
}

const WATCH_LABEL: Record<string, { label: string; tone: WatchItem['tone'] }> = {
  QUALIFIED_BET: { label: 'Qualifies · not funded yet', tone: 'good' },
  NO_BET: { label: 'Declined so far', tone: '' },
  NOT_YET_EVALUATED: { label: 'Not yet evaluated', tone: '' },
}

/**
 * Upcoming decisions that hold no position: what AISP is watching. Never money.
 * A decision with a position is funded and belongs above, so it is excluded here.
 */
export function watchingItems(items: DecisionItem[], fundedPositionIds: Set<string>, now: number): WatchItem[] {
  return items
    .filter((i) => !i.position && ['READY', 'WATCHING', 'PASSED'].includes(i.group))
    .filter((i) => time(i.starts_at) > now)
    .filter((i) => !fundedPositionIds.has(i.position?.position_id ?? ''))
    .map((i) => {
      const final = i.group === 'PASSED'
      const base = WATCH_LABEL[i.recommendation] ?? { label: i.recommendation.replace(/_/g, ' ').toLowerCase(), tone: '' as const }
      return {
        decision_id: i.decision_id, sport: i.sport, game_id: i.game_id, matchup: i.matchup, starts_at: i.starts_at,
        selection: i.selection, market: i.market, american: i.american, sportsbook: i.sportsbook,
        // A funding-paused V18 qualifier is shadow only: never "not funded yet", never money.
        label: i.funding_pause ? SHADOW_ONLY_LABEL
          : i.group === 'READY' ? 'Qualified · not funded' : final ? 'Declined at final check' : base.label,
        tone: i.funding_pause ? '' : i.group === 'READY' ? 'good' : base.tone,
      }
    })
    .sort(byStart)
}

/** "2 h 05 min" style countdown; null once the time has passed or is unknown. */
export function countdown(iso: string | null | undefined, now: number): string | null {
  const t = time(iso)
  if (!Number.isFinite(t) || t <= now) return null
  const minutes = Math.round((t - now) / 60_000)
  if (minutes < 60) return `${minutes} min`
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`
  return `${Math.round(minutes / 1440)} days`
}

// ---------------------------------------------------------- persistence --

export const COLLAPSE_STORAGE_KEY = 'aisp.active-wagers.collapsed'

/** Guarded: private windows and blocked storage must not break the shell. */
export function readCollapsed(): boolean {
  try { return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1' } catch { return false }
}

export function writeCollapsed(collapsed: boolean): void {
  try { window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0') } catch { /* storage unavailable */ }
}
