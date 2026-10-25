/**
 * Pulse home: one view model built from the read models the site already has.
 *
 *   /command-center/markets   (model + no-vig consensus + best price, per game, per day)
 *   /command-center/decisions (what the official policy did, per game)
 *   /command-center/ledger    (every paper position and the bankroll series)
 *   /house-divided?sport=ALL  (family competition standings)
 *   /scoreboard               (observed live / final scores)
 *
 * Nothing here calls a provider or writes. A "lean" is derived only from the
 * stored model probability against the stored consensus, and is always shown
 * with the freshness of both, never as an official pick.
 */
import { useCallback, useMemo } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import {
  expectSchema, type CoverageGame, type DecisionBoard, type DecisionItem, type Ledger, type LedgerPosition,
  type MarketCoverage, type SportCode, asSport,
} from '../../services/commandCenter'
import { houseDivided, type AllSportsBoard, type Board, type Standing } from '../../services/houseDivided'
import { shiftDate } from '../../services/operationalDate'

export interface ScoreEntry {
  game_id: string
  available: boolean
  status?: string
  status_detail?: string
  is_final?: boolean
  is_live?: boolean
  away_score?: number | null
  home_score?: number | null
  period?: { label: string; state: string | null } | null
  governed_final?: { away_score: number; home_score: number } | null
}

/**
 * STARTED: the start time has passed and no live or final score was observed yet -- the game is
 * (presumably) being played, but AISP has no live feed for it (College Football, NHL). It is never
 * shown as upcoming. STALE: the start time passed long ago (6 h) and still no score was observed.
 */
export type Phase = 'LIVE' | 'FINAL' | 'UPCOMING' | 'STARTED' | 'STALE'

/** A price older than this is history, not a market to compare against. */
export const MARKET_FRESH_HOURS = 36

export interface PulseGame {
  id: string
  sport: SportCode
  startsAt: string
  day: string
  away: { abbr: string; name: string }
  home: { abbr: string; name: string }
  phase: Phase
  score: { away: number; home: number; detail: string } | null
  /** Model two-way home probability (tie mass excluded). */
  modelHome: number | null
  modelCreatedAt: string | null
  engine: string | null
  /** No-vig consensus home probability and whether it is current. */
  marketHome: number | null
  marketCurrent: boolean
  marketObservedAt: string | null
  books: number
  bestAway: { american: number | null; book: string | null } | null
  bestHome: { american: number | null; book: string | null } | null
  /** The stored consensus is recent enough to compare against. */
  marketFresh: boolean
  /** Side the model rates above a FRESH market, and by how many points. */
  lean: { side: 'HOME' | 'AWAY'; gap: number; model: number; market: number } | null
  /**
   * Whether the paper policy could act on this game now. A model-vs-Vegas gap is not an edge the
   * policy can take when the forecast's eligibility lease is not current or the game was already
   * decided; the card says so instead of implying a bet exists.
   */
  bettable: { ok: boolean; label: string }
  /** The model's favourite on its own, for games whose price is not fresh yet. */
  favorite: { side: 'HOME' | 'AWAY'; prob: number } | null
  decision: DecisionItem | null
  position: LedgerPosition | null
  windowOpensAt: string | null
}

const toAbbr = (matchup: string | null, idx: 0 | 1) => (matchup ?? '').split('@')[idx]?.trim() || '—'

function scoreOf(entry: ScoreEntry | undefined): PulseGame['score'] {
  if (!entry) return null
  if (entry.available && entry.away_score != null && entry.home_score != null) {
    const detail = entry.is_final ? 'Final' : entry.period ? `${entry.period.state ?? ''} ${entry.period.label}`.trim() : entry.status_detail ?? ''
    return { away: entry.away_score, home: entry.home_score, detail }
  }
  if (entry.governed_final) return { away: entry.governed_final.away_score, home: entry.governed_final.home_score, detail: 'Final' }
  return null
}

export function buildGame(
  cov: CoverageGame, day: string, decision: DecisionItem | null, position: LedgerPosition | null, score: ScoreEntry | undefined,
): PulseGame | null {
  const sport = asSport(cov.league)
  if (!sport || !cov.starts_at) return null
  const away = { abbr: cov.away?.abbreviation ?? toAbbr(cov.matchup, 0), name: cov.away?.name ?? toAbbr(cov.matchup, 0) }
  const home = { abbr: cov.home?.abbreviation ?? toAbbr(cov.matchup, 1), name: cov.home?.name ?? toAbbr(cov.matchup, 1) }
  const s = scoreOf(score)
  const final = cov.status === 'FINAL' || score?.is_final || Boolean(score?.governed_final)
  const live = !final && Boolean(score?.is_live)
  const longPast = Date.parse(cov.starts_at) < Date.now() - 6 * 3600_000
  const started = Date.parse(cov.starts_at) <= Date.now()
  const phase: Phase = final ? 'FINAL' : live ? 'LIVE' : longPast ? 'STALE' : started ? 'STARTED' : 'UPCOMING'
  const modelHome = cov.model?.home_two_way ?? null
  const marketHome = cov.consensus_no_vig_home
  const marketFresh = cov.consensus_is_current || (cov.freshest_observed_at != null
    && Date.now() - Date.parse(cov.freshest_observed_at) < MARKET_FRESH_HOURS * 3600_000)
  const favorite: PulseGame['favorite'] = modelHome == null ? null
    : modelHome >= 0.5 ? { side: 'HOME', prob: modelHome } : { side: 'AWAY', prob: 1 - modelHome }
  let lean: PulseGame['lean'] = null
  if (modelHome != null && marketHome != null && marketFresh) {
    const diff = modelHome - marketHome
    lean = diff >= 0
      ? { side: 'HOME', gap: diff, model: modelHome, market: marketHome }
      : { side: 'AWAY', gap: -diff, model: 1 - modelHome, market: 1 - marketHome }
  }
  return {
    id: cov.game_id, sport, startsAt: cov.starts_at, day, away, home, phase, score: s,
    modelHome, modelCreatedAt: cov.model?.created_at ?? null, engine: cov.model?.engine_id ?? null,
    marketHome, marketCurrent: cov.consensus_is_current, marketObservedAt: cov.freshest_observed_at, books: cov.books, marketFresh, favorite,
    bestAway: cov.best?.AWAY ? { american: cov.best.AWAY.american, book: cov.best.AWAY.sportsbook } : null,
    bestHome: cov.best?.HOME ? { american: cov.best.HOME.american, book: cov.best.HOME.sportsbook } : null,
    lean, decision, position, windowOpensAt: cov.checkpoints?.decision_window_opens_at ?? null,
    bettable: bettableNow(cov, decision, position),
  }
}

const LEASE_LABEL: Record<string, string> = {
  ELIGIBILITY_EXPIRED: 'not bettable now · eligibility lease expired',
  ELIGIBILITY_NOT_CURRENT: 'not bettable now · forecast not currently eligible',
}

/** What the paper policy can do with this game now, from the server's own readings (never inferred from the gap). */
export function bettableNow(cov: CoverageGame, decision: DecisionItem | null, position: LedgerPosition | null): PulseGame['bettable'] {
  if (position) return { ok: false, label: 'bet placed' }
  if (decision && decision.phase === 'FINAL_PREGAME') {
    return decision.recommendation === 'QUALIFIED_BET'
      ? { ok: false, label: 'qualified at the checkpoint' }
      : { ok: false, label: 'decided · no bet' }
  }
  const lease = cov.model_status?.state
  if (lease && LEASE_LABEL[lease]) return { ok: false, label: LEASE_LABEL[lease] }
  const closes = cov.checkpoints?.decision_window_closes_at
  if (closes && Date.parse(closes) <= Date.now()) return { ok: false, label: 'decision window closed' }
  return { ok: true, label: 'decided 30–15 min before start' }
}

const loadCoverage = (day: string) => async (signal: AbortSignal) =>
  expectSchema(await apiRequest<MarketCoverage>(`/command-center/markets?date=${day}`, {}, signal), 'AISP_COMMAND_CENTER_MARKET_COVERAGE_V1')
const loadDecisions = (day: string) => async (signal: AbortSignal) =>
  expectSchema(await apiRequest<DecisionBoard>(`/command-center/decisions?date=${day}`, {}, signal), 'AISP_COMMAND_CENTER_DECISIONS_V1')

/** Markets + decisions for several days in one resource. */
function useDays(days: string[]) {
  const key = days.join(',')
  const loader = useCallback(async (signal: AbortSignal) => {
    const list = key.split(',')
    const [coverage, decisions] = await Promise.all([
      Promise.all(list.map((d) => loadCoverage(d)(signal).catch(() => null))),
      Promise.all(list.map((d) => loadDecisions(d)(signal).catch(() => null))),
    ])
    return { days: list, coverage, decisions }
  }, [key])
  return useApiResource(loader, { key: cacheKey('pulse-days', { key }), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 60_000 })
}

function useScores(ids: string[]) {
  const key = [...ids].sort().join(',')
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!key) return { games: {} as Record<string, ScoreEntry> }
    return apiRequest<{ games: Record<string, ScoreEntry> }>(`/scoreboard?game_ids=${key}`, {}, signal)
  }, [key])
  return useApiResource(loader, { key: cacheKey('pulse-scores', { key }), staleTimeMs: 30_000, refreshIntervalMs: 30_000 })
}

export function useLedgerData() {
  const loader = useCallback(async (signal: AbortSignal) =>
    expectSchema(await apiRequest<Ledger>('/command-center/ledger', {}, signal), 'AISP_COMMAND_CENTER_LEDGER_V1'), [])
  return useApiResource(loader, { key: cacheKey('command-ledger'), staleTimeMs: STALE_TIME.operational, refreshIntervalMs: 120_000 })
}

export function useStandings() {
  const loader = useCallback((signal: AbortSignal) => houseDivided.board('ALL', signal) as Promise<Board>, [])
  return useApiResource(loader, { key: cacheKey('pulse-house', {}), staleTimeMs: STALE_TIME.operational })
}

export function allPositions(ledger: Ledger | null): (LedgerPosition & { sport: SportCode })[] {
  if (!ledger) return []
  return ledger.sports.flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport })))
}

export function usePulse(today: string) {
  const days = useMemo(() => [shiftDate(today, -1), today, shiftDate(today, 1), shiftDate(today, 2)], [today])
  const daysRes = useDays(days)
  const ledgerRes = useLedgerData()
  const houseRes = useStandings()

  const positionsByGame = useMemo(() => {
    const map = new Map<string, LedgerPosition>()
    for (const p of allPositions(ledgerRes.data)) map.set(p.game_id, p)
    return map
  }, [ledgerRes.data])

  const ids = useMemo(() => {
    const out: string[] = []
    daysRes.data?.coverage.forEach((c) => c?.games.forEach((g) => out.push(g.game_id)))
    return out
  }, [daysRes.data])
  const scoresRes = useScores(ids)

  const games = useMemo<PulseGame[]>(() => {
    const data = daysRes.data
    if (!data) return []
    const decisionsByGame = new Map<string, DecisionItem>()
    data.decisions.forEach((b) => b?.items.forEach((d) => {
      // Prefer the decision that produced a position, then the latest.
      const prev = decisionsByGame.get(d.game_id)
      if (!prev || (d.position && !prev.position)) decisionsByGame.set(d.game_id, d)
    }))
    const seen = new Set<string>()
    const out: PulseGame[] = []
    data.coverage.forEach((c, i) => c?.games.forEach((g) => {
      if (seen.has(g.game_id)) return
      seen.add(g.game_id)
      const game = buildGame(g, data.days[i], decisionsByGame.get(g.game_id) ?? null, positionsByGame.get(g.game_id) ?? null,
        scoresRes.data?.games?.[g.game_id])
      if (game) out.push(game)
    }))
    return out.sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt))
  }, [daysRes.data, positionsByGame, scoresRes.data])

  const house = houseRes.data && houseRes.data.sport === 'ALL' ? (houseRes.data as AllSportsBoard) : null

  return {
    days, games, ledger: ledgerRes.data, standings: (house?.standings ?? []) as Standing[],
    loading: daysRes.loading, error: daysRes.error, updatedAt: daysRes.updatedAt,
  }
}

// ------------------------------------------------------------ formatting --

export const pct = (p: number | null | undefined, digits = 0) => p == null ? '—' : `${(p * 100).toFixed(digits)}%`
export const american = (n: number | null | undefined) => n == null ? '—' : n > 0 ? `+${n}` : `${n}`
export const usd = (n: number | null | undefined, signed = false) => {
  if (n == null || !Number.isFinite(n)) return '—'
  const s = Math.abs(n).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  return signed ? `${n > 0 ? '+' : n < 0 ? '−' : ''}${s}` : (n < 0 ? `−${s}` : s)
}

export function etClock(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }) {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', ...opts }).format(new Date(iso))
}

/** "Today", "Tomorrow", "Sun", "Yesterday" relative to an ET operational day. */
export function dayLabel(day: string, today: string) {
  if (day === today) return 'Today'
  if (day === shiftDate(today, 1)) return 'Tomorrow'
  if (day === shiftDate(today, -1)) return 'Yesterday'
  return new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'UTC' }).format(new Date(`${day}T12:00:00Z`))
}

export function ageText(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return 'unknown age'
  const m = Math.round((now - Date.parse(iso)) / 60_000)
  if (m < 60) return `${m} min old`
  if (m < 48 * 60) return `${Math.round(m / 60)} h old`
  return `${Math.round(m / 1440)} days old`
}

export function countdown(iso: string, now: number) {
  const s = Math.max(0, Math.floor((Date.parse(iso) - now) / 1000))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h > 23 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

/** Result of a settled position in plain words. */
export function resultOf(p: LedgerPosition): 'WIN' | 'LOSS' | 'PUSH' | 'OPEN' {
  const r = (p.result ?? '').toUpperCase()
  if (r === 'WIN' || r === 'LOSS' || r === 'PUSH') return r
  return 'OPEN'
}
