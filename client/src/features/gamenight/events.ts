/**
 * Settlement events and bankroll landmarks, derived from the ledger's
 * `combined_series` (every FUNDING and SETTLEMENT point since 09-01).
 *
 * A "moment" fires ONLY when a ledger poll contains a SETTLEMENT point that the
 * page had not seen before. An observed ESPN final never fires one.
 *
 * Baseline rules, because the ledger key is persisted in browser storage:
 *   - a view restored from storage is never the baseline and never diffed;
 *   - the first fresh (restored === false) ledger becomes the baseline and
 *     fires nothing;
 *   - a new point whose settlement time is more than 15 minutes old when it is
 *     detected (the tab was hidden, say) goes to the replay reel, not live.
 *
 * Points are keyed `${position_id}|${at}`: two settlements can share a clock.
 */
import type { BankrollPoint, LedgerPosition, SportCode } from '../../services/commandCenter'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import type { GapReading } from './gap'

export const LIVE_WINDOW_MS = 15 * 60_000
export const STARTING_LEVEL = 5000
/** "Tonight" runs from 6 AM ET to 6 AM ET, so a game that settles after midnight still belongs to the night it was played. */
export const NIGHT_START_HOUR_ET = 6

const ET_HOUR = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' })
const etHourOf = (ms: number) => Number(ET_HOUR.format(ms)) % 24

/** `hour`:00 ET on the ET calendar day `day`, as epoch ms (DST-safe: ET is UTC−4 or −5). */
export function etDayAt(day: string, hour: number): number {
  const base = Date.parse(`${day}T${String(hour).padStart(2, '0')}:00:00Z`)
  for (const offset of [4, 5]) {
    const t = base + offset * 3_600_000
    if (operationalToday(new Date(t)) === day && etHourOf(t) === hour) return t
  }
  return base + 5 * 3_600_000
}

/** When tonight began: 6 AM ET today, or 6 AM ET yesterday before 6 AM. */
export function nightStartOf(now: number): number {
  const day = operationalToday(new Date(now))
  const start = etDayAt(day, NIGHT_START_HOUR_ET)
  return now >= start ? start : etDayAt(shiftDate(day, -1), NIGHT_START_HOUR_ET)
}

export const pointKey = (p: Pick<BankrollPoint, 'position_id' | 'at'>): string => `${p.position_id ?? ''}|${p.at ?? ''}`

const isSettlement = (p: BankrollPoint) => p.kind === 'SETTLEMENT'
const time = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN)

/** SETTLEMENT points in `next` that are not in `prev`. */
export function settlementEvents(prev: readonly BankrollPoint[], next: readonly BankrollPoint[]): BankrollPoint[] {
  const seen = new Set(prev.map(pointKey))
  return next.filter((p) => isSettlement(p) && !seen.has(pointKey(p)))
}

/**
 * Advance the settlement watch with one ledger read.
 * `baseline` is null until the first fresh read.
 */
export function watchLedger(
  baseline: ReadonlySet<string> | null, series: readonly BankrollPoint[], restored: boolean,
): { baseline: ReadonlySet<string> | null; fresh: BankrollPoint[] } {
  if (restored) return { baseline, fresh: [] }
  const keys = series.filter(isSettlement).map(pointKey)
  if (baseline == null) return { baseline: new Set(keys), fresh: [] }
  const fresh = series.filter((p) => isSettlement(p) && !baseline.has(pointKey(p)))
  if (!fresh.length) return { baseline, fresh }
  return { baseline: new Set([...baseline, ...keys]), fresh }
}

/** Live if detected within 15 minutes of its settlement time; otherwise it belongs to the replay reel. */
export function splitLiveLate(events: readonly BankrollPoint[], detectedAt: number, windowMs = LIVE_WINDOW_MS) {
  const live: BankrollPoint[] = []
  const late: BankrollPoint[] = []
  for (const e of events) {
    const at = time(e.at)
    if (Number.isFinite(at) && detectedAt - at <= windowMs) live.push(e)
    else late.push(e)
  }
  return { live, late }
}

export interface Landmark { bankroll: number; at: string | null; index: number }

const landmark = (series: readonly BankrollPoint[], index: number): Landmark =>
  ({ bankroll: series[index].bankroll, at: series[index].at, index })

/**
 * The all-time high (the first point that reached it) and the high that stood
 * before it: the best bankroll at any EARLIER settlement time, so a batch that
 * settles two bets on one clock counts as one run.
 */
export function allTimeHigh(series: readonly BankrollPoint[]): (Landmark & { previous: Landmark | null }) | null {
  if (!series.length) return null
  let best = 0
  series.forEach((p, i) => { if (p.bankroll > series[best].bankroll) best = i })
  const bestAt = time(series[best].at)
  let prev = -1
  series.forEach((p, i) => {
    if (i === best) return
    const at = time(p.at)
    const earlier = Number.isFinite(at) && Number.isFinite(bestAt) ? at < bestAt : i < best
    if (earlier && (prev < 0 || p.bankroll > series[prev].bankroll)) prev = i
  })
  return { ...landmark(series, best), previous: prev >= 0 ? landmark(series, prev) : null }
}

/** The best bankroll strictly before `index` in series order. */
export function highBefore(series: readonly BankrollPoint[], index: number): Landmark | null {
  let best = -1
  for (let i = 0; i < Math.min(index, series.length); i++) if (best < 0 || series[i].bankroll > series[best].bankroll) best = i
  return best >= 0 ? landmark(series, best) : null
}

/**
 * The record that stood before the settlement run `index` belongs to: the best
 * bankroll at any earlier settlement time (points sharing its clock are the
 * same run).
 */
export function recordBeforeRun(series: readonly BankrollPoint[], index: number): Landmark | null {
  const at = time(series[index]?.at)
  let best = -1
  for (let i = 0; i < Math.min(index, series.length); i++) {
    const t = time(series[i].at)
    if (Number.isFinite(at) && Number.isFinite(t) && t >= at) continue
    if (best < 0 || series[i].bankroll > series[best].bankroll) best = i
  }
  return best >= 0 ? landmark(series, best) : null
}

export function lowPoint(series: readonly BankrollPoint[]): Landmark | null {
  if (!series.length) return null
  let low = 0
  series.forEach((p, i) => { if (p.bankroll < series[low].bankroll) low = i })
  return landmark(series, low)
}

/**
 * The last point above `level` before the most recent point at or below it:
 * "first time above $5,000 since <that point>". Null when the series never
 * dipped to the level, or never stood above it before the dip.
 */
export function lastCrossingAbove(series: readonly BankrollPoint[], level = STARTING_LEVEL): Landmark | null {
  let dip = -1
  for (let i = series.length - 1; i >= 0; i--) if (series[i].bankroll <= level) { dip = i; break }
  if (dip < 0) return null
  for (let i = dip - 1; i >= 0; i--) if (series[i].bankroll > level) return landmark(series, i)
  return null
}

/** ROI over settled positions: Σ profit_loss ÷ Σ stake, with its sample size. */
export function sportRoi(positions: readonly Pick<LedgerPosition, 'status' | 'stake' | 'profit_loss'>[]): { roi: number | null; n: number; stake: number; profit: number } {
  let stake = 0, profit = 0, n = 0
  for (const p of positions) {
    if (String(p.status).toUpperCase() !== 'SETTLED' || p.profit_loss == null || p.stake == null) continue
    n += 1; stake += p.stake; profit += p.profit_loss
  }
  return { roi: stake > 0 ? profit / stake : null, n, stake: Math.round(stake * 100) / 100, profit: Math.round(profit * 100) / 100 }
}

/** SETTLEMENT points whose settlement time falls on `day` (ET). */
export function settledOnDay(series: readonly BankrollPoint[], day: string): BankrollPoint[] {
  return series.filter((p) => isSettlement(p) && p.at && operationalToday(new Date(p.at)) === day)
}

/** SETTLEMENT points settled at or after `startMs` (tonight's, from `nightStartOf`). */
export function settledSince(series: readonly BankrollPoint[], startMs: number): BankrollPoint[] {
  return series.filter((p) => isSettlement(p) && Number.isFinite(time(p.at)) && time(p.at) >= startMs)
}

/**
 * Split settlement points into settlement runs: points whose clocks are
 * within `gapMs` of the previous one belong to the same run (one pass of the
 * settlement owner).
 */
export function settlementRuns(points: readonly BankrollPoint[], gapMs = 120_000): BankrollPoint[][] {
  const runs: BankrollPoint[][] = []
  let last = NaN
  for (const p of points) {
    const t = time(p.at)
    if (!runs.length || !(Number.isFinite(t) && Number.isFinite(last) && t - last <= gapMs)) runs.push([])
    runs[runs.length - 1].push(p)
    last = t
  }
  return runs
}

// ------------------------------------------------------------------ moments --

export type MomentKind = 'WIN' | 'LOSS' | 'PUSH' | 'BATCH'

export interface Moment {
  id: string
  kind: MomentKind
  /** A replay of a real past settlement, always watermarked. */
  replay: boolean
  at: string | null
  sport: SportCode | null
  change: number
  bankroll: number
  positionId: string | null
  gameId: string | null
  matchup: string | null
  selection: string | null
  american: number | null
  sportsbook: string | null
  stake: number | null
  gap: GapReading | null
  /**
   * The run this settlement belongs to set a new all-time high. `bankroll` is
   * always the run's peak (the figure the headline and Heartbeat show);
   * `partOfRun` marks a win earlier in that run, before the peak was reached.
   */
  newHigh: { bankroll: number; previous: Landmark | null; partOfRun: boolean } | null
  batch: { count: number; net: number } | null
}

export interface MomentContext {
  series: readonly BankrollPoint[]
  positions: ReadonlyMap<string, LedgerPosition & { sport: SportCode }>
  gaps: ReadonlyMap<string, GapReading>
}

function kindOf(change: number, result: string | null | undefined): Exclude<MomentKind, 'BATCH'> {
  const r = (result ?? '').toUpperCase()
  if (r === 'WIN' || r === 'LOSS' || r === 'PUSH') return r
  return change > 0 ? 'WIN' : change < 0 ? 'LOSS' : 'PUSH'
}

/**
 * Lower-thirds for a set of settlement points, in settlement order. Three or
 * more in one run get a header card first.
 */
export function momentsFor(points: readonly BankrollPoint[], ctx: MomentContext, replay: boolean): Moment[] {
  const index = new Map(ctx.series.map((p, i) => [pointKey(p), i]))
  const ordered = [...points].sort((a, b) => (index.get(pointKey(a)) ?? 0) - (index.get(pointKey(b)) ?? 0))
  const out: Moment[] = []
  if (!ordered.length) return out

  // A run that settles two bets on one clock is one event: the new high it sets is its PEAK, against the record
  // that stood before the run. Only the card that reached the peak says "NEW ALL-TIME HIGH"; a win earlier in
  // the run says it was part of it; a loss never does.
  let peak = 0
  ordered.forEach((p, k) => { if (p.bankroll > ordered[peak].bankroll) peak = k })
  const first = index.get(pointKey(ordered[0])) ?? -1
  const beatsAll = first >= 0 ? highBefore(ctx.series, first) : null
  const setsHigh = beatsAll != null && ordered[peak].bankroll > beatsAll.bankroll
  const previous = setsHigh ? recordBeforeRun(ctx.series, first) : null
  const high = (partOfRun: boolean) => (setsHigh ? { bankroll: ordered[peak].bankroll, previous, partOfRun } : null)

  if (ordered.length >= 3) {
    const net = Math.round(ordered.reduce((s, p) => s + (p.change ?? 0), 0) * 100) / 100
    const last = ordered[ordered.length - 1]
    out.push({
      id: `batch|${pointKey(ordered[0])}|${ordered.length}|${replay ? 'r' : 'l'}`, kind: 'BATCH', replay, at: last.at, sport: null,
      change: net, bankroll: last.bankroll, positionId: null, gameId: null, matchup: null, selection: null, american: null,
      sportsbook: null, stake: null, gap: null, newHigh: high(true), batch: { count: ordered.length, net },
    })
  }
  ordered.forEach((p, k) => {
    const pos = p.position_id ? ctx.positions.get(p.position_id) : undefined
    const change = p.change ?? 0
    out.push({
      id: `${pointKey(p)}|${replay ? 'r' : 'l'}`,
      kind: kindOf(change, pos?.result), replay, at: p.at, sport: (p.sport ?? pos?.sport ?? null) as SportCode | null,
      change, bankroll: p.bankroll, positionId: p.position_id, gameId: pos?.game_id ?? null,
      matchup: pos?.matchup ?? null, selection: pos?.selection ?? null, american: pos?.entry?.american ?? null,
      sportsbook: pos?.entry?.sportsbook ?? null, stake: pos?.stake ?? null,
      gap: p.position_id ? ctx.gaps.get(p.position_id) ?? null : null,
      newHigh: k === peak ? high(false) : k < peak && change > 0 ? high(true) : null,
      batch: null,
    })
  })
  return out
}
