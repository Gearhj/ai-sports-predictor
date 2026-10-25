/**
 * Game Night: one view model composed from read models the site already has.
 *
 *   /mission-control?date=          every game of the ET day and every open bet (30 s)
 *   /command-center/ledger          every paper position and the bankroll series (60 s)
 *   /command-center/decisions?date= the model and the same-book price pair at decision time (120 s)
 *   /scoreboard?game_ids=           observed scores for held games that have started (30 s)
 *   /probability/games/{id}/story   the focused game's stored draw tally (once, 30 min)
 *   /nhl/games/{source_game_id}     the focused NHL game's stored expected goals (once, 30 min)
 *
 * Every read is a GET of stored AISP data through the shared query cache;
 * polling pauses while the tab is hidden. Nothing here posts, calls a provider,
 * triggers a job or changes a bet. Joins are by position_id and game_id, never
 * by date: decisions group by slate date while Mission Control groups by start.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { useScoreboard } from '../../hooks/useScoreboard'
import { apiRequest } from '../../services/api'
import { cacheKey } from '../../services/queryCache'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import {
  asSport, decisionsKey, ledgerKey, loadDecisions, loadLedger, useDecisionBoard,
  type DecisionBoard, type DecisionItem, type Ledger, type LedgerPosition, type SportCode,
} from '../../services/commandCenter'
import {
  MISSION_POLL_MS, MISSION_STALE_MS, loadMission, missionKey, useMissionControl,
  type LiveStage, type MissionControl, type MissionGame, type SweatItem, type SweatState,
} from '../mission/missionData'
import type { SimModel } from '../simulator/simData'
import type { GameScoreboard } from '../../types/scoreboard'
import type { NhlGameDetail } from '../../types/nhlResearch'
import { decisionGap, normalizeResult, type BetResult, type GapReading } from './gap'
import { nhlRegulationGrid, sampleGrid, NHL_DRAWN_MAX } from './nhlGrid'
import { axisRange, expandShuffle, totalCount, type Side, type TallyCell } from './stormTally'

export const LEDGER_POLL_MS = 60_000
export const LEDGER_STALE_MS = 55_000
/** One distribution per focused game, kept for 30 minutes so a looping director never refetches it. */
export const FOCUS_STALE_MS = 30 * 60_000
/** Scores are read for held games that have started or start within this window. */
export const SCORE_LEAD_MS = 30 * 60_000
/** A bet settled this recently is a director priority. */
export const RECENT_SETTLEMENT_MS = 15 * 60_000

/** The ET hour (0–23) at `now`. */
export function etHour(now: number): number {
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(now)
  return Number(hour) % 24
}

/** Between midnight and 6 AM ET last night's late games still matter, so yesterday is read too. */
export const isEarlyHours = (now: number) => etHour(now) < 6

// ------------------------------------------------------------------ reads --

function useOptionalMission(day: string, enabled: boolean) {
  const loader = useCallback(async (signal: AbortSignal) => (enabled ? loadMission(day)(signal) : null), [day, enabled])
  return useApiResource<MissionControl | null>(loader, {
    key: enabled ? missionKey(day) : cacheKey('gn-idle', { slot: 'mission-yesterday' }),
    staleTimeMs: MISSION_STALE_MS,
    refreshIntervalMs: enabled ? MISSION_POLL_MS : undefined,
  })
}

/** The ledger, polled every 60 s through the shared `command-ledger` key. */
export function useGameNightLedger() {
  return useApiResource<Ledger>(loadLedger, { key: ledgerKey(), staleTimeMs: LEDGER_STALE_MS, refreshIntervalMs: LEDGER_POLL_MS })
}

/** Decision boards for slate dates other than today that hold a bet still on tonight's board. */
function useExtraDecisions(days: readonly string[]) {
  const key = [...new Set(days)].sort().join(',')
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!key) return { boards: [] as DecisionBoard[] }
    const boards = await Promise.all(key.split(',').map((d) => loadDecisions(d)(signal).catch(() => null)))
    return { boards: boards.filter((b): b is DecisionBoard => b != null) }
  }, [key])
  return useApiResource(loader, {
    key: cacheKey('gn-decisions-extra', { key }), staleTimeMs: 5 * 60_000, refreshIntervalMs: key ? 5 * 60_000 : undefined,
  })
}

/** One decision board, only once asked for (the Gap Watch "Yesterday" toggle). */
export function useOptionalDecisionBoard(day: string, enabled: boolean) {
  const loader = useCallback(async (signal: AbortSignal) => (enabled ? loadDecisions(day)(signal) : null), [day, enabled])
  return useApiResource<DecisionBoard | null>(loader, {
    key: enabled ? decisionsKey(day) : cacheKey('gn-idle', { slot: 'decisions-yesterday' }),
    staleTimeMs: 10 * 60_000,
  })
}

/** The focused NHL game's stored forecast, read once. */
export function useNhlFocus(sourceGameId: string) {
  const loader = useCallback((signal: AbortSignal) =>
    apiRequest<NhlGameDetail>(`/nhl/games/${encodeURIComponent(sourceGameId)}`, {}, signal), [sourceGameId])
  return useApiResource<NhlGameDetail>(loader, { key: cacheKey('gn-nhl', { sourceGameId }), staleTimeMs: FOCUS_STALE_MS })
}

// ------------------------------------------------------------------ joins --

export interface HeldBet {
  positionId: string
  gameId: string
  sourceGameId: string | null
  sport: SportCode
  matchup: string
  away: string
  home: string
  awayName: string | null
  homeName: string | null
  selection: string
  side: Side | null
  american: number | null
  sportsbook: string | null
  stake: number | null
  toWin: number | null
  startsAt: string
  slateDate: string | null
  /** From the ledger when it has the position, else from Mission Control. */
  open: boolean
  /** The server's own open-bet state (Mission Control sweating[]). */
  sweatState: SweatState | null
  live: LiveStage | null
  result: BetResult
  profit: number | null
  settledAt: string | null
  final: { away: number; home: number; source?: string | null } | null
  ledger: (LedgerPosition & { sport: SportCode }) | null
  decision: DecisionItem | null
  gap: GapReading | null
  score: GameScoreboard | null
  game: MissionGame | null
}

/** "ARI ML" / "Cleveland Guardians" / "AWAY" -> the side it backs, if it can be told. */
export function pickSideOf(selection: string | null | undefined, away: { abbr: string; name?: string | null }, home: { abbr: string; name?: string | null }): Side | null {
  const s = (selection ?? '').trim().toUpperCase()
  if (!s) return null
  if (s === 'AWAY' || s === 'HOME') return s
  const matches = (team: { abbr: string; name?: string | null }) => {
    const abbr = team.abbr.trim().toUpperCase()
    const name = (team.name ?? '').trim().toUpperCase()
    return (abbr && (s === abbr || s.startsWith(`${abbr} `))) || (name && (s === name || s.startsWith(`${name} `) || s.includes(name)))
  }
  const a = matches(away)
  const h = matches(home)
  if (a && !h) return 'AWAY'
  if (h && !a) return 'HOME'
  return null
}

const splitMatchup = (matchup: string | null | undefined): [string, string] => {
  const [a, h] = (matchup ?? '').split('@').map((x) => x.trim())
  return [a || 'Away', h || 'Home']
}

export function ledgerPositions(ledger: Ledger | null | undefined): Map<string, LedgerPosition & { sport: SportCode }> {
  const out = new Map<string, LedgerPosition & { sport: SportCode }>()
  for (const s of ledger?.sports ?? []) for (const p of s.positions ?? []) out.set(p.position_id, { ...p, sport: s.sport })
  return out
}

export function decisionsByPosition(boards: readonly (DecisionBoard | null | undefined)[]): Map<string, DecisionItem> {
  const out = new Map<string, DecisionItem>()
  for (const b of boards) for (const item of b?.items ?? []) if (item.position?.position_id) out.set(item.position.position_id, item)
  return out
}

/** Several decision boards as one (decision ids are unique across slates). */
export function mergeBoards(boards: readonly (DecisionBoard | null | undefined)[], date: string): DecisionBoard | null {
  const present = boards.filter((b): b is DecisionBoard => b != null)
  if (!present.length) return null
  const items = new Map<string, DecisionItem>()
  for (const b of present) for (const item of b.items) if (!items.has(item.decision_id)) items.set(item.decision_id, item)
  const generated = present.map((b) => b.generated_at).filter(Boolean).sort()[0] ?? present[0].generated_at
  return { ...present[0], date, generated_at: generated, items: [...items.values()] }
}

export function decisionsByGame(boards: readonly (DecisionBoard | null | undefined)[]): Map<string, DecisionItem> {
  const out = new Map<string, DecisionItem>()
  for (const b of boards) for (const item of b?.items ?? []) {
    const prev = out.get(item.game_id)
    if (!prev || (item.position && !prev.position)) out.set(item.game_id, item)
  }
  return out
}

/**
 * Every bet on tonight's board(s), held or settled: Mission Control games
 * whose bet stage carries a position, plus open bets (sweating[]) whose game
 * is not on the board. Settlement truth is the ledger's, when it has the row.
 */
export function heldBets(input: {
  missions: readonly (MissionControl | null | undefined)[]
  ledger: Ledger | null | undefined
  decisions: readonly (DecisionBoard | null | undefined)[]
  scores: Record<string, GameScoreboard>
}): HeldBet[] {
  const positions = ledgerPositions(input.ledger)
  const byPosition = decisionsByPosition(input.decisions)
  const out = new Map<string, HeldBet>()
  const sweatById = new Map<string, SweatItem>()
  for (const m of input.missions) for (const s of m?.sweating ?? []) sweatById.set(s.position_id, s)
  const gamesById = new Map<string, MissionGame>()
  for (const m of input.missions) for (const g of m?.games ?? []) if (!gamesById.has(g.game_id)) gamesById.set(g.game_id, g)

  const build = (positionId: string, game: MissionGame | null, sweat: SweatItem | null): HeldBet | null => {
    const bet = game?.stages?.bet
    const ledger = positions.get(positionId) ?? null
    const sport = asSport(game?.sport ?? sweat?.sport ?? ledger?.sport ?? null)
    if (!sport) return null
    const gameId = game?.game_id ?? sweat?.game_id ?? ledger?.game_id ?? ''
    const [awayAbbr, homeAbbr] = game ? [game.away.abbr, game.home.abbr] : splitMatchup(sweat?.matchup ?? ledger?.matchup)
    const away = { abbr: awayAbbr, name: game?.away.name ?? null }
    const home = { abbr: homeAbbr, name: game?.home.name ?? null }
    const decision = byPosition.get(positionId) ?? null
    const selection = sweat?.selection ?? bet?.side ?? ledger?.selection ?? decision?.selection ?? ''
    const result = ledger ? normalizeResult(ledger.status, ledger.result) : normalizeResult(bet?.status, game?.stages?.result?.outcome)
    const open = ledger ? String(ledger.status).toUpperCase() === 'OPEN' : (sweat != null || (bet?.status ?? 'OPEN') === 'OPEN')
    const resultStage = game?.stages?.result
    return {
      positionId, gameId, sourceGameId: game?.source_game_id ?? sweat?.source_game_id ?? null, sport,
      matchup: game?.matchup ?? sweat?.matchup ?? ledger?.matchup ?? `${awayAbbr} @ ${homeAbbr}`,
      away: awayAbbr, home: homeAbbr, awayName: away.name, homeName: home.name,
      selection, side: pickSideOf(selection, away, home),
      american: sweat?.american ?? bet?.american ?? ledger?.entry?.american ?? null,
      sportsbook: sweat?.sportsbook ?? bet?.sportsbook ?? ledger?.entry?.sportsbook ?? null,
      stake: sweat?.stake_usd ?? bet?.stake_usd ?? ledger?.stake ?? null,
      toWin: sweat?.to_win_usd ?? bet?.to_win_usd ?? null,
      startsAt: game?.starts_at ?? sweat?.starts_at ?? ledger?.starts_at ?? '',
      slateDate: sweat?.slate_date ?? null,
      open, sweatState: sweat?.state ?? null,
      live: (sweat?.live as LiveStage | null | undefined) ?? (game?.stages?.live as LiveStage | undefined) ?? null,
      result: open ? 'OPEN' : result,
      profit: ledger?.profit_loss ?? resultStage?.profit_usd ?? null,
      settledAt: ledger?.settled_at ?? resultStage?.settled_at ?? null,
      final: resultStage?.final ?? null,
      ledger, decision, gap: decision ? decisionGap(decision) : null,
      score: input.scores[gameId] ?? null,
      game,
    }
  }

  for (const game of gamesById.values()) {
    const positionId = game.stages?.bet?.position_id
    if (!positionId) continue
    const held = build(positionId, game, sweatById.get(positionId) ?? null)
    if (held) out.set(positionId, held)
  }
  for (const sweat of sweatById.values()) {
    if (out.has(sweat.position_id)) continue
    const held = build(sweat.position_id, gamesById.get(sweat.game_id) ?? null, sweat)
    if (held) out.set(sweat.position_id, held)
  }
  return [...out.values()]
}

/**
 * A ledger position as a held bet, for a game that is not on tonight's board
 * (a settlement moment for a game decided on another slate, say). Settlement
 * truth is the ledger's; there is no score and no live state.
 */
export function heldFromLedger(pos: LedgerPosition & { sport: SportCode }, decision: DecisionItem | null): HeldBet {
  const [away, home] = splitMatchup(pos.matchup)
  const open = String(pos.status).toUpperCase() === 'OPEN'
  return {
    positionId: pos.position_id, gameId: pos.game_id, sourceGameId: null, sport: pos.sport,
    matchup: pos.matchup ?? `${away} @ ${home}`, away, home, awayName: null, homeName: null,
    selection: pos.selection ?? '', side: pickSideOf(pos.selection, { abbr: away }, { abbr: home }),
    american: pos.entry?.american ?? null, sportsbook: pos.entry?.sportsbook ?? null, stake: pos.stake, toWin: null,
    startsAt: pos.starts_at ?? '', slateDate: null, open, sweatState: null, live: null,
    result: open ? 'OPEN' : normalizeResult(pos.status, pos.result), profit: pos.profit_loss, settledAt: pos.settled_at,
    final: null, ledger: pos, decision, gap: decision ? decisionGap(decision) : null, score: null, game: null,
  }
}

/** A focus target for a held bet that is not on the board (the stage reads its stored distribution by game id). */
export function targetFromHeld(held: HeldBet): FocusTarget {
  return {
    gameId: held.gameId, sport: held.sport, sourceGameId: held.sourceGameId, matchup: held.matchup,
    away: { abbr: held.away, name: held.awayName }, home: { abbr: held.home, name: held.homeName }, startsAt: held.startsAt || null,
    held, game: null, hasForecast: true,
  }
}

export type BugState = 'LIVE' | 'FINAL_AWAITING_SETTLEMENT' | 'STARTED_NO_LIVE_FEED' | 'VOID_AWAITING_SETTLEMENT' | 'UPCOMING' | 'SETTLED'

/**
 * What a held bet's score bug shows. The scoreboard is preferred over the
 * canonical game status, which can lag (BUF@LV still read "scheduled" at an
 * observed 33–30 final). An observed final with the position still open is
 * "awaiting settlement", never "settled".
 */
export function bugState(bet: HeldBet, now: number): BugState {
  if (!bet.open) return 'SETTLED'
  const sb = bet.score
  if (sb?.governed_final || (sb?.available && sb.is_final)) return 'FINAL_AWAITING_SETTLEMENT'
  if (sb?.available && sb.is_live) return 'LIVE'
  if (bet.sweatState === 'LIVE' && !(sb?.available)) return 'LIVE'
  if (bet.sweatState) return bet.sweatState
  const start = Date.parse(bet.startsAt)
  return Number.isFinite(start) && start <= now ? 'STARTED_NO_LIVE_FEED' : 'UPCOMING'
}

export const BUG_ORDER: Record<BugState, number> = {
  LIVE: 0, FINAL_AWAITING_SETTLEMENT: 1, STARTED_NO_LIVE_FEED: 2, VOID_AWAITING_SETTLEMENT: 3, UPCOMING: 4, SETTLED: 5,
}

/** A held bet's observed score, when the scoreboard (or the server's live block) has one. */
export function observedScore(bet: HeldBet): { away: number; home: number; final: boolean; source: string | null } | null {
  const sb = bet.score
  if (sb?.available && sb.away_score != null && sb.home_score != null) {
    return { away: sb.away_score, home: sb.home_score, final: Boolean(sb.is_final), source: sb.source?.name ?? null }
  }
  if (sb?.governed_final) return { away: sb.governed_final.away_score, home: sb.governed_final.home_score, final: true, source: sb.governed_final.evidence_source }
  const live = bet.live
  if (live?.available && live.away_score != null && live.home_score != null) {
    return { away: live.away_score, home: live.home_score, final: Boolean(live.is_final), source: null }
  }
  if (bet.final) return { away: bet.final.away, home: bet.final.home, final: true, source: bet.final.source ?? null }
  return null
}

// ---------------------------------------------------------------- focus --

export interface FocusTarget {
  gameId: string
  sport: string
  sourceGameId: string | null
  matchup: string
  away: { abbr: string; name: string | null }
  home: { abbr: string; name: string | null }
  startsAt: string | null
  held: HeldBet | null
  game: MissionGame | null
  hasForecast: boolean
}

/** Held games first (bet), then the other Mission Control games that have a stored forecast. */
export function focusTargets(missions: readonly (MissionControl | null | undefined)[], held: readonly HeldBet[]): FocusTarget[] {
  const out: FocusTarget[] = []
  const seen = new Set<string>()
  const byGame = new Map<string, HeldBet>()
  for (const h of held) if (!byGame.has(h.gameId)) byGame.set(h.gameId, h)
  const games: MissionGame[] = []
  for (const m of missions) for (const g of m?.games ?? []) games.push(g)
  const fromGame = (g: MissionGame, h: HeldBet | null): FocusTarget => ({
    gameId: g.game_id, sport: g.sport, sourceGameId: g.source_game_id ?? null, matchup: g.matchup,
    away: { abbr: g.away.abbr, name: g.away.name ?? null }, home: { abbr: g.home.abbr, name: g.home.name ?? null },
    startsAt: g.starts_at, held: h, game: g, hasForecast: g.stages?.forecast?.state === 'DONE',
  })
  for (const h of held) {
    if (seen.has(h.gameId) || !h.gameId) continue
    seen.add(h.gameId)
    const g = games.find((x) => x.game_id === h.gameId)
    out.push(g ? fromGame(g, h) : {
      gameId: h.gameId, sport: h.sport, sourceGameId: h.sourceGameId, matchup: h.matchup,
      away: { abbr: h.away, name: h.awayName }, home: { abbr: h.home, name: h.homeName }, startsAt: h.startsAt || null,
      held: h, game: null, hasForecast: true,
    })
  }
  for (const g of [...games].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))) {
    if (seen.has(g.game_id)) continue
    seen.add(g.game_id)
    out.push(fromGame(g, byGame.get(g.game_id) ?? null))
  }
  return out
}

/**
 * The director's running order, at most `limit` games, held games only:
 *   1. live games holding our bet, most recent score change first;
 *   2. bets settled in the last 15 minutes;
 *   3. open bets in play with no live feed, or final and awaiting settlement;
 *   4. the next held games to start;
 *   5. the most recently settled held games.
 * With no held game it falls back to the first game with a stored forecast.
 */
export function directorQueue(
  targets: readonly FocusTarget[], ctx: { now: number; lastScoreChange: ReadonlyMap<string, number> }, limit = 6,
): string[] {
  const held = targets.filter((t) => t.held)
  const state = (t: FocusTarget) => bugState(t.held!, ctx.now)
  const settledAt = (t: FocusTarget) => Date.parse(t.held?.settledAt ?? '') || 0
  const live = held.filter((t) => state(t) === 'LIVE')
    .sort((a, b) => (ctx.lastScoreChange.get(b.gameId) ?? 0) - (ctx.lastScoreChange.get(a.gameId) ?? 0))
  const recent = held.filter((t) => state(t) === 'SETTLED' && ctx.now - settledAt(t) <= RECENT_SETTLEMENT_MS)
    .sort((a, b) => settledAt(b) - settledAt(a))
  const inPlay = held.filter((t) => ['STARTED_NO_LIVE_FEED', 'FINAL_AWAITING_SETTLEMENT', 'VOID_AWAITING_SETTLEMENT'].includes(state(t)))
  const upcoming = held.filter((t) => state(t) === 'UPCOMING').sort((a, b) => Date.parse(a.startsAt ?? '') - Date.parse(b.startsAt ?? ''))
  const settled = held.filter((t) => state(t) === 'SETTLED').sort((a, b) => settledAt(b) - settledAt(a))
  const order: string[] = []
  for (const t of [...live, ...recent, ...inPlay, ...upcoming, ...settled]) if (!order.includes(t.gameId)) order.push(t.gameId)
  if (!order.length) {
    const first = targets.find((t) => t.hasForecast)
    return first ? [first.gameId] : []
  }
  return order.slice(0, limit)
}

// ---------------------------------------------------------------- storm --

export interface StormPopulation {
  /** exact: the stored multiset, shuffled. sampled: drawn with replacement from a derived distribution. */
  mode: 'exact' | 'sampled'
  drawCount: number
  window: { homeMax: number; awayMax: number }
  /** The replay for one seed: every draw as a cell index, and the tally those draws make. */
  draw: (seed: number) => { order: Int32Array; cells: TallyCell[] }
  nhl: { lambdaHome: number; lambdaAway: number; regulationTie: number; outside: number; maxError: number } | null
}

export interface StormSource {
  gameId: string
  sport: string
  away: { abbr: string; name: string }
  home: { abbr: string; name: string }
  population: StormPopulation | null
  unavailableReason: string | null
  /** The stored win split shown on the bar. */
  split: { away: number; tie: number; home: number }
  /** What `split` means ("regulation and overtime" for the NHL final channel). */
  splitBasis: string
  /** NHL only: the regulation three-way split the grid reproduces. */
  regulation: { away: number; tie: number; home: number } | null
  modelLabel: string
  forecastAt: string | null
  startsAt: string | null
  hoursBeforeStart: number | null
  development: boolean
  prospective: boolean
  /** The game's final as stored with the forecast's game (story) or the NHL result. */
  final: { away: number; home: number; label: string; source: string | null } | null
  /** The cell the crosshair lands on: the final, or for the NHL the regulation score. */
  strikeCell: { away: number; home: number } | null
  /**
   * MLB Gen 1 pools every score above `cap` into one "cap+1 or more" cell
   * (stored as cap + 1). Null when every cell is an exact score.
   */
  cap: number | null
}

/** A score as the grid labels it: "13+" for the pooled cell of a capped distribution. */
export function scoreLabel(value: number, cap: number | null | undefined): string {
  return cap != null && value > cap ? `${cap + 1}+` : String(value)
}

/** The cell a real score falls in: a score above the cap lands in the pooled cell. */
export function pooledCell(score: { away: number; home: number }, cap: number | null | undefined): { away: number; home: number } {
  if (cap == null) return score
  return { away: Math.min(score.away, cap + 1), home: Math.min(score.home, cap + 1) }
}

function hoursBetween(from: string | null | undefined, to: string | null | undefined): number | null {
  const a = from ? Date.parse(from) : NaN
  const b = to ? Date.parse(to) : NaN
  return Number.isFinite(a) && Number.isFinite(b) ? (b - a) / 3_600_000 : null
}

/** NFL / CFB / MLB: the exact stored tally from useSimModel. */
export function stormFromSim(gameId: string, model: SimModel): StormSource {
  const cells: TallyCell[] = model.cells
    .map((c) => ({ home: c.home, away: c.away, count: Math.round(c.p * model.draws) }))
    .filter((c) => c.count > 0)
  const drawCount = totalCount(cells)
  const horizon = (model.story?.channel as { horizon?: string } | undefined)?.horizon ?? ''
  const final = model.final ? { away: model.final.away, home: model.final.home, label: 'Final', source: 'AISP game record' } : null
  return {
    gameId, sport: model.sport, away: model.away, home: model.home,
    population: drawCount > 0 ? {
      mode: 'exact', drawCount, window: axisRange(cells),
      draw: (seed) => ({ order: expandShuffle(cells, seed), cells }),
      nhl: null,
    } : null,
    unavailableReason: drawCount > 0 ? null : 'No simulated draws are stored for this game.',
    split: { away: model.win.away, tie: model.win.tie, home: model.win.home },
    splitBasis: model.win.tie > 0 ? 'stored win / tie / win' : 'stored win split',
    regulation: null,
    modelLabel: model.modelLabel, forecastAt: model.forecastAt, startsAt: model.startsAt,
    hoursBeforeStart: model.hoursBeforeStart ?? hoursBetween(model.forecastAt, model.startsAt),
    development: model.development, prospective: /PROSPECTIVE/i.test(horizon),
    final, strikeCell: final ? pooledCell({ away: final.away, home: final.home }, model.cap) : null,
    cap: model.cap,
  }
}

/** NHL: a regulation grid derived from the stored expected goals, drawn only if it reproduces the stored probabilities. */
export function stormFromNhl(gameId: string, detail: NhlGameDetail): StormSource {
  const f = detail.forecast
  const away = { abbr: detail.away?.abbreviation ?? 'AWAY', name: detail.away?.name ?? 'Away' }
  const home = { abbr: detail.home?.abbreviation ?? 'HOME', name: detail.home?.name ?? 'Home' }
  const result = detail.result
  const final = result ? {
    away: result.final.away_score, home: result.final.home_score,
    label: result.status_label || 'Final', source: result.final.source ?? null,
  } : null
  const base = {
    gameId, sport: 'NHL', away, home, cap: null,
    modelLabel: f ? `${f.model_id.replace(/_/g, ' ')} v${f.model_version}` : 'NHL Gen 1',
    forecastAt: f?.clocks?.published_utc ?? f?.clocks?.created_utc ?? null,
    startsAt: detail.start_utc, development: false, prospective: false,
    hoursBeforeStart: hoursBetween(f?.clocks?.published_utc ?? f?.clocks?.created_utc, detail.start_utc),
    final,
    strikeCell: result ? { away: result.regulation.away_goals, home: result.regulation.home_goals } : null,
    split: { away: f?.probabilities.final.away ?? 0, tie: 0, home: f?.probabilities.final.home ?? 0 },
    splitBasis: 'final, overtime and shootout included',
    regulation: f?.probabilities.regulation ? { ...f.probabilities.regulation } : null,
  }
  if (!f) return { ...base, population: null, unavailableReason: 'No NHL forecast is stored for this game yet.' }
  const built = nhlRegulationGrid(f.goals.regulation, f.probabilities.regulation)
  if (!built.ok) return { ...base, population: null, unavailableReason: `Grid unavailable. ${built.reason}` }
  const grid = built.grid
  return {
    ...base,
    population: {
      mode: 'sampled', drawCount: 10_000, window: { homeMax: NHL_DRAWN_MAX, awayMax: NHL_DRAWN_MAX },
      draw: (seed) => sampleGrid(grid, 10_000, seed),
      nhl: {
        lambdaHome: f.goals.regulation!.home, lambdaAway: f.goals.regulation!.away,
        regulationTie: f.probabilities.regulation!.tie, outside: built.outside, maxError: built.check.maxError,
      },
    },
    unavailableReason: null,
  }
}

// ---------------------------------------------------------------- composer --

export interface ScoreChange { gameId: string; at: number; seq: number }

/**
 * Everything the wall reads, composed. `day` is today in ET; between midnight
 * and 6 AM yesterday's Mission Control and decisions are read as well.
 */
export function useGameNight(day: string, now: number, focusGameId: string | null = null) {
  const early = isEarlyHours(now)
  const yesterday = shiftDate(day, -1)
  const mission = useMissionControl(day)
  const missionPrev = useOptionalMission(yesterday, early)
  const ledger = useGameNightLedger()
  const decisions = useDecisionBoard(day)

  const missions = useMemo(() => [mission.data, early ? missionPrev.data : null], [mission.data, missionPrev.data, early])

  // Bets on tonight's board decided on another slate (an MLB game decided on 10-16, played 10-18).
  const extraDays = useMemo(() => {
    const days = new Set<string>()
    for (const m of missions) for (const s of m?.sweating ?? []) if (s.slate_date && s.slate_date !== day) days.add(s.slate_date)
    if (early) days.add(yesterday)
    days.delete(day)
    return [...days].sort()
  }, [missions, day, early, yesterday])
  const extra = useExtraDecisions(extraDays)
  const boards = useMemo(() => [decisions.data, ...(extra.data?.boards ?? [])], [decisions.data, extra.data])

  // Scores only for held games that have started or start within 30 minutes (plus a started focused game).
  const preliminary = useMemo(() => heldBets({ missions, ledger: ledger.data, decisions: boards, scores: {} }), [missions, ledger.data, boards])
  const scoreIds = useMemo(() => {
    const ids = new Set<string>()
    for (const h of preliminary) {
      const start = Date.parse(h.startsAt)
      if (h.gameId && h.open && Number.isFinite(start) && start - now <= SCORE_LEAD_MS) ids.add(h.gameId)
    }
    // A focused game without our bet still gets its live ring once it has started and is not over.
    if (focusGameId && !ids.has(focusGameId)) {
      for (const m of missions) {
        const g = m?.games.find((x) => x.game_id === focusGameId)
        const start = g ? Date.parse(g.starts_at) : NaN
        if (g && g.phase !== 'FINAL' && g.phase !== 'VOID' && Number.isFinite(start) && start <= now) { ids.add(focusGameId); break }
      }
    }
    return [...ids].sort()
    // `now` moves on the caller's half-minute clock, so a game entering its 30-minute window is picked up.
  }, [preliminary, now, focusGameId, missions])
  const scoreboard = useScoreboard({ gameIds: scoreIds, enabled: scoreIds.length > 0 })

  const held = useMemo(() => heldBets({ missions, ledger: ledger.data, decisions: boards, scores: scoreboard.games }),
    [missions, ledger.data, boards, scoreboard.games])
  const targets = useMemo(() => focusTargets(missions, held), [missions, held])

  // Score changes on held games: the director cuts to the one that just changed.
  const lastScores = useRef(new Map<string, string>())
  const [scoreChanges, setScoreChanges] = useState<{ map: ReadonlyMap<string, number>; latest: ScoreChange | null }>({ map: new Map(), latest: null })
  useEffect(() => {
    const games = scoreboard.games
    let latest: ScoreChange | null = null
    const next = new Map(scoreChanges.map)
    for (const [id, entry] of Object.entries(games)) {
      if (!entry?.available || entry.away_score == null || entry.home_score == null) continue
      const text = `${entry.away_score}-${entry.home_score}`
      const prev = lastScores.current.get(id)
      lastScores.current.set(id, text)
      if (prev != null && prev !== text) {
        const at = Date.now()
        next.set(id, at)
        latest = { gameId: id, at, seq: (scoreChanges.latest?.seq ?? 0) + 1 }
      }
    }
    if (latest) setScoreChanges({ map: next, latest })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to new scoreboard payloads only
  }, [scoreboard.games])

  const liveHeld = held.some((h) => h.open && h.score?.available && h.score.is_live)
  const onAir = liveHeld || (mission.data?.counts.live ?? 0) > 0

  return {
    day, early, yesterday,
    mission, missionPrev, ledger, decisions, extra, scoreboard,
    missions, boards, held, targets, scoreChanges, onAir, scoreIds,
  }
}

export type GameNightModel = ReturnType<typeof useGameNight>

/** Today's ET date. */
export const etToday = (now: number) => operationalToday(new Date(now))
