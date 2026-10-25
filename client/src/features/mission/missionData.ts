/**
 * Mission Control: every game of one ET day as a pipeline it travels through.
 *
 *   FORECAST -> APPROVAL -> PRICES -> DECISION -> BET -> LIVE -> RESULT
 *
 * One read model, `GET /mission-control?date=YYYY-MM-DD`
 * (schema AISP_MISSION_CONTROL_V1). The server evaluates every stage against
 * a single clock (`generated_at`) and says, per stage, whether it is DONE,
 * WAITING (with the clock it is due), BLOCKED (with the reason in plain
 * English), NOT_APPLICABLE, or UNKNOWN (a read failed).
 *
 * This module is read-only. It never posts, never calls a provider and never
 * changes a betting decision: it only types the contract and turns its fields
 * into words and colours. Anything missing from a response is treated as
 * UNKNOWN, never promoted to DONE.
 */
import { useCallback, useSyncExternalStore } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { expectSchema } from '../../services/commandCenter'
import { cacheKey } from '../../services/queryCache'
import { countdown, etClock } from '../pulse/pulseData'

export { ageText, american, dayLabel, etClock, pct, usd } from '../pulse/pulseData'

export const MISSION_SCHEMA = 'AISP_MISSION_CONTROL_V1'

// ---------------------------------------------------------------- contract --

export type MissionSport = 'MLB' | 'NFL' | 'CFB' | 'NBA' | 'NHL'
/** Display order everywhere on the page. */
export const MISSION_SPORTS: MissionSport[] = ['MLB', 'NFL', 'CFB', 'NHL', 'NBA']

export type StageState = 'DONE' | 'WAITING' | 'BLOCKED' | 'NOT_APPLICABLE' | 'UNKNOWN'
export const STAGE_STATES: StageState[] = ['DONE', 'WAITING', 'BLOCKED', 'NOT_APPLICABLE', 'UNKNOWN']

/** Fields every stage block carries. */
export interface Stage {
  state: StageState
  /** The owning service's code, e.g. EXPIRED_LEASE, QUOTE_TOO_STALE. */
  code?: string | null
  /** Chip text, at most 32 characters ("Buys 7:45 PM", "Lease expired"). */
  label?: string | null
  /** One plain-English sentence. */
  detail?: string | null
  /** When a WAITING stage is due. */
  due_at?: string | null
  /** When a DONE stage happened. */
  at?: string | null
}

export interface ForecastStage extends Stage {
  made_at?: string | null
  made_at_basis?: 'PRODUCER_PUBLISHED' | 'AISP_IMPORT_TRANSACTION_START' | 'AISP_IN_PROCESS_GENERATED' | string | null
  source?: string | null
  engine_id?: string | null
  forecast_id?: string | null
  snapshot?: 'FINAL_PREGAME' | 'CURRENT' | string | null
  freshness?: 'FRESH' | 'AGING' | 'STALE' | null
  age_minutes?: number | null
  home_win_prob?: number | null
  away_win_prob?: number | null
  favorite?: { side: 'HOME' | 'AWAY'; team: string; prob: number } | null
}

export interface ApprovalStage extends Stage {
  route?: string | null
  eligible?: boolean | null
  valid_until?: string | null
  /** MLB only: whether the V18 funding pause (MLB_V18_FUNDING_PAUSE_V1) is switched on. */
  funding_pause_enabled?: boolean | null
}

export interface PriceQuote {
  american: number | null
  sportsbook?: string | null
  provider?: string | null
  observed_at?: string | null
}

export interface PricesStage extends Stage {
  best?: { home?: PriceQuote | null; away?: PriceQuote | null } | null
  books?: number | null
  fresh_books?: number | null
  quote_age_minutes?: number | null
  fresh_until?: string | null
  consensus_home?: number | null
  collector?: 'ON' | 'OFF' | 'UNKNOWN' | null
  permitted_sources?: string[] | null
  /** NFL only: whether The Odds API fallback lane is switched on. */
  fallback_odds_api?: boolean | null
}

export type DecisionClass = 'BET' | 'PASS' | 'NO_PRICE' | 'BLOCKED' | 'NOT_ACTIVATED' | 'PENDING' | 'NOT_RECORDED'

export interface DecisionStage extends Stage {
  class?: DecisionClass | null
  recommendation?: string | null
  reason_code?: string | null
  phase?: string | null
  recorded_at?: string | null
  decision_id?: string | null
  edge?: number | null
  model_probability?: number | null
  market_probability?: number | null
}

export interface BetStage extends Stage {
  position_id?: string | null
  side?: string | null
  american?: number | null
  sportsbook?: string | null
  stake_usd?: number | null
  to_win_usd?: number | null
  placed_at?: string | null
  status?: 'OPEN' | 'SETTLED' | 'VOID' | null
  display_status?: string | null
}

export interface LiveFields {
  available?: boolean | null
  is_live?: boolean | null
  is_final?: boolean | null
  away_score?: number | null
  home_score?: number | null
  period_label?: string | null
  detail?: string | null
  freshness?: 'FRESH' | 'DELAYED' | 'UNAVAILABLE' | null
  last_checked_at?: string | null
}

export interface LiveStage extends Stage, LiveFields {}

export interface ResultStage extends Stage {
  outcome?: 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | null
  profit_usd?: number | null
  final?: { away: number; home: number; source?: string | null; label?: string | null } | null
  awaiting_settlement?: boolean | null
  settled_at?: string | null
}

export interface Stages {
  forecast: ForecastStage
  approval: ApprovalStage
  prices: PricesStage
  decision: DecisionStage
  bet: BetStage
  live: LiveStage
  result: ResultStage
}
export type StageKey = keyof Stages
export const STAGE_KEYS: StageKey[] = ['forecast', 'approval', 'prices', 'decision', 'bet', 'live', 'result']

export type NextStage = 'FORECAST' | 'APPROVAL' | 'PRICES' | 'DECISION' | 'BET' | 'LIVE' | 'RESULT' | 'DONE'

export interface NextStep {
  stage: NextStage
  kind: 'WAIT' | 'BLOCKED' | 'WATCH' | 'DONE'
  at?: string | null
  /** "Decision in", "Puck drop", "Settlement". */
  label?: string | null
  /** "Decision window opens 7:30 PM ET". */
  text?: string | null
}

export interface MissionTeam { abbr: string; name?: string | null }

export interface MissionGame {
  game_id: string
  sport: MissionSport
  source_game_id?: string | null
  matchup: string
  away: MissionTeam
  home: MissionTeam
  starts_at: string
  status: 'scheduled' | 'live' | 'final' | 'postponed' | 'canceled' | string
  phase: 'PREGAME' | 'DECISION_WINDOW' | 'STARTED' | 'FINAL' | 'VOID' | string
  clocks?: {
    forecast_due_at?: string | null
    buy_opens_at?: string | null
    buy_closes_at?: string | null
    decision_opens_at?: string | null
    decision_closes_at?: string | null
  } | null
  /** Partial on purpose: a block the server did not send renders as UNKNOWN. */
  stages: Partial<Stages>
  next_step?: NextStep | null
  attention?: 'NONE' | 'WATCH' | 'BLOCKED' | null
}

export type SweatState = 'LIVE' | 'UPCOMING' | 'STARTED_NO_LIVE_FEED' | 'FINAL_AWAITING_SETTLEMENT'
  /** A postponed or canceled game whose open bet the settlement owner has not voided yet. */
  | 'VOID_AWAITING_SETTLEMENT'

export interface SweatItem {
  position_id: string
  game_id: string
  /** The sport's own game id (NHL ten-digit, CFBD), so the link works from any day's board. */
  source_game_id?: string | null
  sport: MissionSport
  matchup: string
  selection: string
  american: number | null
  sportsbook?: string | null
  stake_usd: number | null
  to_win_usd: number | null
  starts_at: string
  slate_date: string
  placed_at?: string | null
  state: SweatState
  /** The game's live stage block (its `detail` is the plain sentence). */
  live?: LiveFields | null
}

export type JobState = 'OK' | 'RUNNING' | 'FAILED' | 'STALE' | 'NEVER_RUN' | 'NOT_REGISTERED'
export type JobRole = 'FORECAST' | 'IMPORT' | 'PRICES' | 'DECISION' | 'LIVE' | 'RESULTS'

export interface HealthJob {
  key: string
  job_name: string
  scheduler_id?: string | null
  label?: string | null
  sport: MissionSport | 'ALL'
  role: JobRole
  registered: boolean
  /**
   * Whether settings say this job should be registered. A NOT_REGISTERED job
   * with `expected: false` is switched off on purpose, not a fault.
   */
  expected?: boolean | null
  state: JobState
  last_started_at?: string | null
  last_status?: 'succeeded' | 'failed' | 'running' | 'skipped' | 'queued' | null
  last_success_at?: string | null
  next_run_at?: string | null
  stale_after_minutes?: number | null
}

export type ProducerState = 'OK' | 'STALE' | 'MISSING' | 'EXPIRED' | 'FAILED' | 'NOT_APPLICABLE'

export interface HealthProducer {
  sport: MissionSport
  name: string
  state: ProducerState
  last_published_at?: string | null
  age_minutes?: number | null
  valid_until?: string | null
  detail?: string | null
}

export interface MissionHealth {
  overall: 'OK' | 'DEGRADED' | 'DOWN'
  issues: string[]
  scheduler: { state: 'RUNNING' | 'NOT_RUNNING' | 'DISABLED'; started_at?: string | null }
  database: { state: string }
  jobs: HealthJob[]
  producers: HealthProducer[]
}

/**
 * The shared $100 meter. `room_usd` is null when the allowance is not in
 * force; a failed read is state UNKNOWN with every number null.
 */
export interface MissionAllowance {
  state: 'ROOM' | 'FULL' | 'NOT_IN_FORCE' | 'UNKNOWN'
  in_force: boolean | null
  effective_from?: string | null
  limit_usd: number | null
  entered_usd: number | null
  room_usd: number | null
  entries: number | null
  ambiguous_entries: number | null
  slate_date: string
  basis?: string | null
  read_state?: 'OK' | 'READ_FAILED'
  error_type?: string | null
}

/** An unfunded or unreadable bankroll carries null numbers, never zeros. */
export interface MissionBankroll {
  sport: MissionSport
  label: string
  /** null when the account could not be read (read_state READ_FAILED). */
  funded: boolean | null
  starting_usd: number | null
  bankroll_usd: number | null
  available_usd: number | null
  open_exposure_usd: number | null
  profit_usd: number | null
  effective_on?: string | null
  read_state?: 'OK' | 'READ_FAILED'
  error_type?: string | null
}

export interface MissionSportSummary {
  sport: MissionSport
  label: string
  games: number
  read_state: 'OK' | 'READ_FAILED'
  error_type?: string | null
  owner_active?: boolean | null
  schedule_note?: string | null
  next_event_at?: string | null
  pending_games?: { source_game_id: string; starts_at: string; reason: string }[] | null
}

export interface MissionCounts {
  games: number
  live: number
  bets_open: number
  bets_settled: number
  blocked: number
  waiting: number
  staked_usd: number
  profit_usd: number
}

export interface MissionControl {
  schema_version: typeof MISSION_SCHEMA
  date: string
  timezone: string
  is_today: boolean
  window?: { local_date: string; timezone: string; start_utc: string; end_utc: string; description?: string } | null
  generated_at: string
  authority: string
  provider_calls_made: number
  refresh?: { client_poll_seconds: number; server_fresh_seconds: number; server_max_stale_seconds: number } | null
  health: MissionHealth
  allowance: MissionAllowance
  bankrolls: MissionBankroll[]
  sports: MissionSportSummary[]
  games: MissionGame[]
  /** null when the open-bets read failed (then `read_errors.sweating` names it): never an empty list. */
  sweating: SweatItem[] | null
  counts: MissionCounts
  /** Which shared reads failed, by error TYPE name only (never a message). */
  read_errors?: Record<string, string> | null
}

// ------------------------------------------------------------------ loading --

/** Poll cadence: the server keeps the body fresh for 30 s. */
export const MISSION_POLL_MS = 30_000
export const MISSION_STALE_MS = 25_000

export const missionKey = (day: string) => cacheKey('mission-control', { day })

/** Always sends the date, so each day has exactly one cache key. */
export const loadMission = (day: string) => async (signal?: AbortSignal) =>
  expectSchema(await apiRequest<MissionControl>(`/mission-control?date=${day}`, {}, signal), MISSION_SCHEMA)

export function useMissionControl(day: string) {
  const loader = useCallback((signal: AbortSignal) => loadMission(day)(signal), [day])
  return useApiResource(loader, { key: missionKey(day), staleTimeMs: MISSION_STALE_MS, refreshIntervalMs: MISSION_POLL_MS })
}

// ------------------------------------------------------------------- words --

export const STAGE_NAMES: Record<StageKey, string> = {
  forecast: 'Forecast', approval: 'Approval', prices: 'Prices', decision: 'Decision',
  bet: 'Bet', live: 'Live', result: 'Result',
}
/** Four-letter chip text on phones. */
export const STAGE_ABBR: Record<StageKey, string> = {
  forecast: 'FCST', approval: 'APPR', prices: 'PRCE', decision: 'DECN', bet: 'BET', live: 'LIVE', result: 'RSLT',
}
export const NEXT_STAGE_KEY: Record<NextStage, StageKey | null> = {
  FORECAST: 'forecast', APPROVAL: 'approval', PRICES: 'prices', DECISION: 'decision',
  BET: 'bet', LIVE: 'live', RESULT: 'result', DONE: null,
}

/** What the start of a game is called, per sport. */
export const KICK_WORD: Record<MissionSport, string> = {
  MLB: 'First pitch', NFL: 'Kickoff', CFB: 'Kickoff', NHL: 'Puck drop', NBA: 'Tip-off',
}

export const SPORT_LABEL: Record<MissionSport, string> = {
  MLB: 'MLB', NFL: 'NFL', CFB: 'College Football', NHL: 'NHL', NBA: 'NBA',
}

export type StageTone = 'done' | 'waiting' | 'blocked' | 'na' | 'unknown'

/** A state the client does not recognise is UNKNOWN; it can never read as done. */
export function normalizeState(value: unknown): StageState {
  return typeof value === 'string' && (STAGE_STATES as string[]).includes(value) ? value as StageState : 'UNKNOWN'
}

export function stageTone(state: unknown): StageTone {
  switch (normalizeState(state)) {
    case 'DONE': return 'done'
    case 'WAITING': return 'waiting'
    case 'BLOCKED': return 'blocked'
    case 'NOT_APPLICABLE': return 'na'
    default: return 'unknown'
  }
}

export const STATE_WORD: Record<StageState, string> = {
  DONE: 'done', WAITING: 'waiting', BLOCKED: 'blocked', NOT_APPLICABLE: 'not applicable', UNKNOWN: 'unknown',
}

/** The stage block, or an explicit UNKNOWN placeholder when the server sent none. */
export function stageOf<K extends StageKey>(game: MissionGame, key: K): Stages[K] {
  const raw = game.stages?.[key] as Stages[K] | undefined
  if (!raw || typeof raw !== 'object') {
    return { state: 'UNKNOWN', code: 'NOT_REPORTED', label: 'Not reported', detail: `AISP did not report the ${STAGE_NAMES[key].toLowerCase()} stage for this game.` } as Stages[K]
  }
  const state = normalizeState(raw.state)
  return state === raw.state ? raw : { ...raw, state }
}

/** Chip text: the server's label, else a plain fallback that never claims success it was not told. */
export function chipLabel(key: StageKey, stage: Stage): string {
  const label = (stage.label ?? '').trim()
  if (label) return label
  switch (normalizeState(stage.state)) {
    case 'DONE': return STAGE_NAMES[key]
    case 'WAITING': return stage.due_at ? `Due ${etClock(stage.due_at)}` : 'Waiting'
    case 'BLOCKED': return 'Blocked'
    case 'NOT_APPLICABLE': return 'n/a'
    default: return 'Unknown'
  }
}

/** "<Stage>: <state> — <detail>", the chip's accessible name. */
export function chipAria(key: StageKey, stage: Stage): string {
  const state = normalizeState(stage.state)
  const detail = (stage.detail ?? '').trim() || chipLabel(key, stage)
  return `${STAGE_NAMES[key]}: ${STATE_WORD[state]} — ${detail}`
}

// ------------------------------------------------------------------- clocks --

/**
 * One shared clock for every row. A single 1 s interval runs while anything
 * subscribes; each subscriber re-renders only when its own period rolls over,
 * so 60 rows counting down in minutes cost one timer and no extra renders.
 */
const tickListeners = new Set<() => void>()
let tickTimer: number | null = null

function subscribeTick(listener: () => void) {
  tickListeners.add(listener)
  if (tickTimer == null) tickTimer = window.setInterval(() => tickListeners.forEach((l) => l()), 1000)
  return () => {
    tickListeners.delete(listener)
    if (!tickListeners.size && tickTimer != null) { window.clearInterval(tickTimer); tickTimer = null }
  }
}

/** The current time, refreshed once per `periodMs` (floored to that period) from the shared clock. */
export function useTick(periodMs: number): number {
  const bucket = useSyncExternalStore(subscribeTick, () => Math.floor(Date.now() / periodMs), () => Math.floor(Date.now() / periodMs))
  return bucket * periodMs
}

/**
 * How often a next-step countdown must refresh: every second only while its
 * clock is less than eleven minutes away; every 15 s further out; every 30 s
 * once it is overdue (its "due N min ago" text changes slowly); hourly for a
 * step with no clock at all (nothing on it moves with time).
 */
export function tickPeriod(atMs: number, now: number): number {
  if (!Number.isFinite(atMs)) return 3_600_000
  const left = atMs - now
  if (left > 0 && left < 11 * 60_000) return 1000
  return left > 0 ? 15_000 : 30_000
}

/**
 * Time until `iso`, in words that suit a glance:
 * "45 s", "4:32" (under ten minutes, ticking), "12 min", "2 h 05 min", "2d 3h".
 * A clock that has passed returns `null`; the caller decides how to say "now".
 */
export function countdownText(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return null
  const ms = at - now
  if (ms <= 0) return null
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s} s`
  if (s < 600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  if (s < 24 * 3600) {
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return `${h} h ${String(m).padStart(2, '0')} min`
  }
  return countdown(iso, now)
}

/** How long ago `iso` was, compactly: "40 s", "12 min", "3 h", "2 d". */
export function sinceText(iso: string | number | null | undefined, now: number): string | null {
  if (iso == null) return null
  const at = typeof iso === 'number' ? iso : Date.parse(iso)
  if (!Number.isFinite(at)) return null
  const s = Math.max(0, Math.floor((now - at) / 1000))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  if (s < 48 * 3600) return `${Math.floor(s / 3600)} h`
  return `${Math.floor(s / 86400)} d`
}

/**
 * A span of minutes at a glance: "45 min", "5.5 h", "14 h", "6 d".
 * Used for ages (forecast, quote) that can run from minutes to days.
 */
export function minutesText(minutes: number | null | undefined): string | null {
  if (minutes == null || !Number.isFinite(minutes)) return null
  const m = Math.max(0, minutes)
  if (m < 90) return `${Math.round(m)} min`
  if (m < 600) return `${(m / 60).toFixed(1)} h`
  if (m < 48 * 60) return `${Math.round(m / 60)} h`
  return `${Math.round(m / 1440)} d`
}

/**
 * An ET clock for the detail panel: "Sat 7:45 PM ET" within three days of
 * `now`, "Oct 11, 1:12 AM ET" further away, where a weekday alone would read
 * as the coming one.
 */
export function whenText(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return null
  const near = Math.abs(at - now) < 3 * 86_400_000
  return `${etClock(iso, near ? { weekday: 'short', hour: 'numeric', minute: '2-digit' } : { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET`
}

/**
 * When what is on screen was computed: the older of the server's
 * `X-AISP-Generated-At` (the resource's `asOf`) and the body's own
 * `generated_at`. A response served without the header falls back to its
 * fetch time in the cache, so taking the older clock never overstates
 * freshness.
 */
export function freshnessClock(asOf: number | null | undefined, generatedAt: string | null | undefined): number | null {
  const clocks = [asOf ?? NaN, generatedAt ? Date.parse(generatedAt) : NaN].filter((t) => Number.isFinite(t))
  return clocks.length ? Math.min(...clocks) : null
}

/**
 * The row's headline for its next step, e.g. "Decision in 12 min",
 * "Puck drop in 1 h 05 min", "Blocked: lease expired", "Watching live".
 */
export function nextStepLine(step: NextStep | null | undefined, now: number): { headline: string; sub: string | null; overdue: boolean } {
  if (!step) return { headline: 'Next step not reported', sub: null, overdue: false }
  const text = (step.text ?? '').trim() || null
  const label = (step.label ?? '').trim()
  if (step.kind === 'BLOCKED') return { headline: label ? `Blocked · ${label}` : 'Blocked', sub: text, overdue: false }
  if (step.kind === 'WATCH') return { headline: label || 'Watching live', sub: text, overdue: false }
  if (step.kind === 'DONE') return { headline: label || text || 'Done', sub: label ? text : null, overdue: false }
  // WAIT
  const left = countdownText(step.at, now)
  const base = label || 'Next step'
  // No clock: say what is awaited without a dangling "in" ("Decision in" -> "Decision").
  if (!step.at) return { headline: base.replace(/\s+in$/i, ''), sub: text, overdue: false }
  if (left == null) {
    const late = sinceText(step.at, now)
    const stem = base.replace(/\s+in$/i, '')
    return { headline: late && late !== '0 s' ? `${stem} · due ${late} ago` : `${stem} · due now`, sub: text, overdue: true }
  }
  return { headline: /\bin$/i.test(base) ? `${base} ${left}` : `${base} in ${left}`, sub: text, overdue: false }
}

/** "4:10–4:50 PM ET", or "from 4:10 PM ET" when the server reports no closing clock (MLB). */
export function buyWindowText(opens: string | null | undefined, closes: string | null | undefined): string | null {
  if (!opens) return null
  return closes ? `${etClock(opens)}–${etClock(closes)} ET` : `from ${etClock(opens)} ET`
}

/**
 * The FORECAST chip's age suffix for a forecast the server does not call
 * FRESH ("6 d", "30 h"), so a stale forecast never reads as a plain tick.
 */
export function forecastAgeSuffix(stage: ForecastStage): string | null {
  if (!stage.freshness || stage.freshness === 'FRESH') return null
  return minutesText(stage.age_minutes) ?? stage.freshness.toLowerCase()
}

/**
 * A request that never reached the server (fetch rejected: offline, refused,
 * DNS, CORS) as opposed to one the server answered with an error. Only the
 * first means "backend unreachable".
 */
export function isNetworkFailure(error: string | null | undefined): boolean {
  return Boolean(error) && /failed to fetch|networkerror|network request failed|load failed|err_connection|net::|offline/i.test(error ?? '')
}

/** A short, readable team mark: a long name (College Football schools) gets no crest-sized mark at all. */
export function showsTeamMark(abbr: string | null | undefined): boolean {
  return Boolean(abbr) && (abbr ?? '').trim().length <= 4
}

// ------------------------------------------------------------ rows & filters --

export type SportFilter = 'ALL' | MissionSport

/** A `?sport=` value as a filter; anything unrecognised (or ALL) is All sports. */
export function asSportFilter(value: string | null | undefined): SportFilter {
  const upper = (value ?? '').toUpperCase()
  return (MISSION_SPORTS as string[]).includes(upper) ? upper as MissionSport : 'ALL'
}

/** The server serves today-1 .. today+3 (ET); anything else is refused with a 400. */
export const MISSION_DAYS_BACK = 1
export const MISSION_DAYS_AHEAD = 3

export function filterBySport<T extends { sport: string }>(rows: T[], sport: SportFilter): T[] {
  return sport === 'ALL' ? rows : rows.filter((r) => r.sport === sport)
}

/** A game that is on right now, by its own live block or phase. */
export function isLive(game: MissionGame): boolean {
  const live = game.stages?.live
  if (game.phase === 'FINAL' || game.phase === 'VOID') return false
  return Boolean(live?.is_live) || game.status === 'live'
}

function rank(game: MissionGame): number {
  if (isLive(game)) return 0
  if (game.phase === 'FINAL' || game.phase === 'VOID' || game.status === 'final'
    || game.status === 'postponed' || game.status === 'canceled') return 2
  return 1
}

const time = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : NaN
  return Number.isFinite(t) ? t : Number.POSITIVE_INFINITY
}

/** Live first, then by when the next step is due, then finished games. */
export function sortRows(games: MissionGame[]): MissionGame[] {
  return [...games].sort((a, b) => {
    const r = rank(a) - rank(b)
    if (r) return r
    if (rank(a) === 1) {
      const d = time(a.next_step?.at ?? a.starts_at) - time(b.next_step?.at ?? b.starts_at)
      if (d) return d
    }
    return time(a.starts_at) - time(b.starts_at) || a.matchup.localeCompare(b.matchup)
  })
}

/** How far along the pipeline a game is: the index of the last DONE stage, or -1. */
export function progressIndex(game: MissionGame): number {
  let last = -1
  STAGE_KEYS.forEach((key, i) => { if (normalizeState(game.stages?.[key]?.state) === 'DONE') last = i })
  return last
}

/** Per-sport game counts for the filter chips, in display order, only sports with games or a failed read. */
export function sportCounts(data: MissionControl | null): { sport: MissionSport; games: number; failed: boolean }[] {
  if (!data) return []
  const counts = new Map<MissionSport, number>()
  data.games.forEach((g) => counts.set(g.sport, (counts.get(g.sport) ?? 0) + 1))
  const failed = new Set(data.sports.filter((s) => s.read_state === 'READ_FAILED').map((s) => s.sport))
  return MISSION_SPORTS
    .filter((s) => (counts.get(s) ?? 0) > 0 || failed.has(s))
    .map((s) => ({ sport: s, games: counts.get(s) ?? 0, failed: failed.has(s) }))
}

/** Bankrolls in display order. A sport the server did not report is listed as missing, never as $0. */
export function bankrollStrip(data: MissionControl | null): (MissionBankroll | { sport: MissionSport; label: string; missing: true })[] {
  const by = new Map((data?.bankrolls ?? []).map((b) => [b.sport, b]))
  return MISSION_SPORTS.map((sport) => by.get(sport) ?? { sport, label: sport, missing: true as const })
}

/** Job states that count against health, worst first. */
export const JOB_TROUBLE: JobState[] = ['FAILED', 'STALE', 'NOT_REGISTERED']

/**
 * A job settings deliberately leave unregistered (a lane that is switched
 * off). Only `expected === false` counts: a job the server did not classify is
 * treated as expected, so a missing field can never hide a fault.
 */
export function isSwitchedOff(job: HealthJob): boolean {
  return job.state === 'NOT_REGISTERED' && job.expected === false
}

export function jobSummary(jobs: HealthJob[] | null | undefined) {
  const all = jobs ?? []
  const off = all.filter(isSwitchedOff).length
  const list = all.filter((j) => !isSwitchedOff(j))
  const count = (s: JobState) => list.filter((j) => j.state === s).length
  return {
    total: list.length, ok: count('OK') + count('RUNNING'), running: count('RUNNING'),
    failed: count('FAILED'), stale: count('STALE'), never: count('NEVER_RUN'), unregistered: count('NOT_REGISTERED'),
    off,
  }
}

/** The live block as the shared ScoreInline state. */
export function liveScoreState(live: LiveFields | null | undefined): {
  phase: 'LIVE' | 'FINAL' | 'PRE' | 'UNKNOWN'; away: number | null; home: number | null; detail: string
} {
  if (!live || !live.available || live.away_score == null || live.home_score == null) {
    return { phase: live?.is_final ? 'FINAL' : 'PRE', away: null, home: null, detail: live?.detail ?? '' }
  }
  if (live.is_final) return { phase: 'FINAL', away: live.away_score, home: live.home_score, detail: live.period_label || 'Final' }
  if (live.is_live) return { phase: 'LIVE', away: live.away_score, home: live.home_score, detail: live.period_label || live.detail || 'Live' }
  return { phase: 'PRE', away: null, home: null, detail: live.detail ?? '' }
}

export const SWEAT_WORDS: Record<SweatState, string> = {
  LIVE: 'Live',
  UPCOMING: 'Not started',
  STARTED_NO_LIVE_FEED: 'Started · no live feed',
  FINAL_AWAITING_SETTLEMENT: 'Final · settling',
  VOID_AWAITING_SETTLEMENT: 'Not played · voiding',
}
