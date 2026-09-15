/**
 * Today's agenda: every operational milestone for the slate, derived only from
 * persisted AISP state already in the shared cache (market coverage, decision
 * board, ledger, sport readiness). No request of its own.
 *
 * What is a fact and what is an expectation is kept explicit:
 *  - a published forecast, a stored quote, a recorded decision, a position and
 *    a settlement are FACTS with their recorded time;
 *  - a scheduled collection window and a decision window are SCHEDULE, fixed by
 *    policy (T-15 cutoff, 240-minute freshness, T-30..T-15 window);
 *  - a forecast not yet published and a settlement not yet recorded are
 *    EXPECTED, labelled as such, because their timing belongs to other owners
 *    (the model publisher; the governed final).
 */
import type { CoverageGame, DecisionItem, Ledger, SportCode } from './commandCenter'
import { asSport } from './commandCenter'

export type AgendaKind = 'forecast' | 'collection' | 'decision' | 'game' | 'position' | 'settlement'
export type AgendaStatus = 'done' | 'open' | 'scheduled' | 'expected' | 'missed'

export interface AgendaEvent {
  key: string
  kind: AgendaKind
  sport: SportCode
  gameId: string
  matchup: string
  /** The moment the event happens (or opens). */
  at: number
  /** For windows: when it closes. */
  until?: number
  status: AgendaStatus
  label: string
  detail: string
}

const MIN = 60_000
/** Publisher cadence is not fixed; an unpublished forecast is expected by T-60. */
const FORECAST_EXPECTED_BEFORE = 60 * MIN
/** Settlement follows the governed final; these are estimates of when a check will find one. */
const SETTLEMENT_ESTIMATE: Record<SportCode, number> = { MLB: 195 * MIN, NFL: 225 * MIN, NBA: 165 * MIN, CFB: 240 * MIN }

export function buildAgenda(games: CoverageGame[], decisions: DecisionItem[], ledger: Ledger | null, now: number): AgendaEvent[] {
  const events: AgendaEvent[] = []
  const decisionsByGame = new Map<string, DecisionItem[]>()
  for (const d of decisions) decisionsByGame.set(d.game_id, [...(decisionsByGame.get(d.game_id) ?? []), d])
  const positionsByGame = new Map<string, NonNullable<Ledger['sports'][number]['positions']>>()
  for (const s of ledger?.sports ?? []) for (const p of s.positions) positionsByGame.set(p.game_id, [...(positionsByGame.get(p.game_id) ?? []), p])

  for (const g of games) {
    const sport = asSport(g.league)
    if (!sport || !g.starts_at) continue
    const start = Date.parse(g.starts_at)
    const base = { sport, gameId: g.game_id, matchup: g.matchup ?? 'Game' }
    const status = g.status.toUpperCase()
    const final = status === 'FINAL' || status === 'COMPLETED'
    const started = now >= start

    // Forecast publication
    if (g.model?.created_at) {
      events.push({ ...base, key: `${g.game_id}:forecast`, kind: 'forecast', at: Date.parse(g.model.created_at), status: 'done',
        label: 'Forecast published', detail: g.model.engine_id.replace(/_/g, ' ') })
    } else {
      const at = start - FORECAST_EXPECTED_BEFORE
      events.push({ ...base, key: `${g.game_id}:forecast`, kind: 'forecast', at, status: started ? 'missed' : 'expected',
        label: started ? 'No forecast before first pitch' : 'Forecast expected', detail: started ? 'The controlling forecast was not published' : 'Final-pregame forecast expected by T-60 (publisher timing varies)' })
    }

    // Sportsbook collection window
    const cOpen = Date.parse(g.checkpoints.collection_opens_at)
    const cClose = Date.parse(g.checkpoints.decision_window_closes_at)
    const collected = g.quotes.length > 0 && g.freshest_observed_at != null && Date.parse(g.freshest_observed_at) >= cOpen
    events.push({ ...base, key: `${g.game_id}:collection`, kind: 'collection', at: cOpen, until: cClose,
      status: collected ? 'done' : now >= cClose ? 'missed' : now >= cOpen ? 'open' : 'scheduled',
      label: collected ? 'Market collected' : now >= cClose ? 'No price collected' : now >= cOpen ? 'Collection window open' : 'Market collection scheduled',
      detail: collected ? `${g.fresh_books || g.books} books · latest ${new Date(Date.parse(g.freshest_observed_at as string)).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })} ET` : 'Sportsbook prices for this game' })

    // Decision window
    const dOpen = Date.parse(g.checkpoints.decision_window_opens_at)
    const items = decisionsByGame.get(g.game_id) ?? []
    const finalDecision = items.find((i) => i.phase === 'FINAL_PREGAME')
    events.push({ ...base, key: `${g.game_id}:decision`, kind: 'decision', at: dOpen, until: cClose,
      status: finalDecision ? 'done' : now >= cClose ? 'missed' : now >= dOpen ? 'open' : 'scheduled',
      label: finalDecision
        ? (finalDecision.recommendation === 'QUALIFIED_BET' ? 'Qualified' : finalDecision.recommendation === 'NO_BET' ? 'Evaluated · no bet' : 'Evaluated')
        : now >= cClose ? 'Window closed · not evaluated' : now >= dOpen ? 'Decision window open' : 'Decision window',
      detail: finalDecision?.reason ?? 'T-30 to T-15 final-pregame evaluation' })

    // Game start
    events.push({ ...base, key: `${g.game_id}:game`, kind: 'game', at: start, status: final ? 'done' : started ? 'open' : 'scheduled',
      label: final ? 'Final' : started ? 'In progress' : 'First pitch / kickoff', detail: g.status.replace(/_/g, ' ').toLowerCase() })

    // Positions and settlement
    for (const p of positionsByGame.get(g.game_id) ?? []) {
      events.push({ ...base, key: `${p.position_id}:position`, kind: 'position', at: Date.parse(p.placed_at ?? g.checkpoints.decision_window_closes_at), status: 'done',
        label: `Position ${p.selection} ${p.entry.american != null ? (p.entry.american > 0 ? `+${p.entry.american}` : p.entry.american) : ''}`,
        detail: `stake $${(p.stake ?? 0).toFixed(2)} · ${p.entry.sportsbook?.replace(/\s*\(.*\)\s*$/, '') ?? ''}` })
      if (p.status === 'SETTLED' && p.settled_at) {
        events.push({ ...base, key: `${p.position_id}:settlement`, kind: 'settlement', at: Date.parse(p.settled_at), status: 'done',
          label: `Settled ${p.result ?? ''} ${(p.profit_loss ?? 0) >= 0 ? '+' : '−'}$${Math.abs(p.profit_loss ?? 0).toFixed(2)}`, detail: p.selection ?? '' })
      } else {
        events.push({ ...base, key: `${p.position_id}:settlement`, kind: 'settlement', at: start + SETTLEMENT_ESTIMATE[sport], status: 'expected',
          label: 'Settlement check (estimated)', detail: 'Settles once the governed final is recorded' })
      }
    }
  }
  return events.sort((a, b) => a.at - b.at)
}

export interface AttentionItem {
  event: AgendaEvent
  /** Games sharing this milestone at the same minute (a slate's 6:40 group). */
  matchups: string[]
  sports: SportCode[]
}

/**
 * The next milestones that need attention, soonest first. Games that share the
 * same kind and minute are one item ("Market collection · 4 games"), so a
 * 15-game slate does not fill the list with identical rows.
 */
export function nextAttention(events: AgendaEvent[], now: number, count = 3): AttentionItem[] {
  const pending = events
    .filter((e) => (e.status === 'open' && (e.until ?? e.at) >= now) || ((e.status === 'scheduled' || e.status === 'expected') && e.at >= now - 5 * MIN))
    .sort((a, b) => (a.status === 'open' ? now : a.at) - (b.status === 'open' ? now : b.at) || a.matchup.localeCompare(b.matchup))
  const groups = new Map<string, AttentionItem>()
  for (const e of pending) {
    const key = `${e.kind}|${e.status}|${Math.round(e.at / MIN)}`
    const group = groups.get(key)
    if (group) {
      group.matchups.push(e.matchup)
      if (!group.sports.includes(e.sport)) group.sports.push(e.sport)
    } else {
      if (groups.size >= count) continue
      groups.set(key, { event: e, matchups: [e.matchup], sports: [e.sport] })
    }
  }
  return [...groups.values()]
}

/** Completed milestones so far today, for the "done" tally. */
export function completedCount(events: AgendaEvent[]): number {
  return events.filter((e) => e.status === 'done').length
}

export const KIND_LABEL: Record<AgendaKind, string> = {
  forecast: 'Forecast', collection: 'Market collection', decision: 'Decision window',
  game: 'Game start', position: 'Position', settlement: 'Settlement',
}
