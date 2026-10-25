/** NHL Live View maths: rink events in one frame (home attacks right), danger, and AISP's simple live win estimate. */
import { elapsedAt, type ArenaFeed, type ArenaPlay } from './arenaData'

export type RinkKind = 'goal' | 'shot' | 'miss' | 'block' | 'hit' | 'faceoff' | 'give' | 'take' | 'penalty'
export type RinkEvent = ArenaPlay & { i: number; nx: number; ny: number; shooter: string | null; danger: 'high' | 'med' | 'low'; kind: RinkKind; t: number }

const KIND: Record<string, RinkKind> = { Goal: 'goal', Shot: 'shot', Missed: 'miss', Blocked: 'block', Hit: 'hit', 'Face Off': 'faceoff', Giveaway: 'give', Takeaway: 'take' }
export const isAttempt = (k: RinkKind) => k === 'goal' || k === 'shot' || k === 'miss' || k === 'block'
const PENALTY = /slash|trip|hook|hold|interfer|high.?stick|rough|cross|delay|boarding|charging|elbow|fight|unsportsman|too many|penalty|misconduct/i

/** Every play with a rink spot, flipped so the home team always attacks the right-hand net. */
export function rinkEvents(feed: ArenaFeed): RinkEvent[] {
  const { home, away } = feed.teams
  const dir = new Map<number, number>()
  for (const per of Array.from(new Set(feed.plays.map((p) => p.period ?? 0)))) {
    const shots = (id: string, s: number) => feed.plays.filter((p) => p.period === per && p.team === id && p.coord && /^(Shot|Goal|Missed)$/.test(p.type ?? '')).map((p) => s * p.coord!.x)
    dir.set(per, [...shots(home.id, 1), ...shots(away.id, -1)].reduce((a, b) => a + b, 0) >= 0 ? 1 : -1)
  }
  const out: RinkEvent[] = []
  feed.plays.forEach((p) => {
    if (!p.coord) return
    const kind: RinkKind | undefined = KIND[p.type ?? ''] ?? (PENALTY.test(p.type ?? '') ? 'penalty' : undefined)
    if (!kind) return
    const f = dir.get(p.period ?? 0) ?? 1
    const nx = p.coord.x * f; const ny = p.coord.y * f
    const shooter = kind === 'block' ? (p.team === home.id ? away.id : home.id) : p.team
    const dist = Math.hypot((shooter === home.id ? 89 : -89) - nx, ny)
    const danger = dist < 22 && Math.abs(ny) < 14 ? 'high' : dist < 40 ? 'med' : 'low'
    out.push({ ...p, i: out.length, nx, ny, shooter, danger, kind, t: elapsedAt('nhl', p.period, p.clock) })
  })
  return out
}

/** The run of play that produced a goal: from the last faceoff before it (same period), at most ten events. */
export function goalSequence(events: RinkEvent[], goal: RinkEvent): RinkEvent[] {
  let start = goal.i
  while (start > 0 && goal.i - start < 9 && events[start - 1].period === goal.period) {
    start--
    if (events[start].kind === 'faceoff') break
  }
  return events.slice(start, goal.i + 1)
}

// ---- live win estimate: two Poisson scoring rates fitted to the pregame price, run down the clock ----
const GOALS_PER_60 = 6.2
const pois = (lambda: number) => { const out: number[] = []; let p = Math.exp(-lambda); for (let k = 0; k <= 14; k++) { out.push(p); p = (p * lambda) / (k + 1) } return out }
function chance(lh: number, la: number, lead: number, ot: number): number {
  const ph = pois(lh); const pa = pois(la); let win = 0; let tie = 0
  for (let h = 0; h < ph.length; h++) for (let a = 0; a < pa.length; a++) { const d = lead + h - a; const p = ph[h] * pa[a]; if (d > 0) win += p; else if (d === 0) tie += p }
  return win + tie * ot
}
/** Home's share of the scoring that reproduces the pregame home win probability. */
export function fitShare(prior: number): number {
  let lo = 0.2; let hi = 0.8
  for (let k = 0; k < 40; k++) { const s = (lo + hi) / 2; if (chance(GOALS_PER_60 * s, GOALS_PER_60 * (1 - s), 0, s) < prior) lo = s; else hi = s }
  return (lo + hi) / 2
}
/** Home win chance at `t` seconds with home leading by `lead` goals. Overtime = next goal wins (shootout: a coin). */
export function liveHome(share: number, t: number, lead: number, period: number | null, final: boolean): number {
  if (final) return lead > 0 ? 1 : lead < 0 ? 0 : 0.5
  if ((period ?? 1) >= 5) return lead > 0 ? 1 : lead < 0 ? 0 : 0.5
  if ((period ?? 1) === 4) return lead > 0 ? 1 : lead < 0 ? 0 : share
  const left = Math.max(0, 3600 - t) / 3600
  return chance(GOALS_PER_60 * share * left, GOALS_PER_60 * (1 - share) * left, lead, share)
}

export interface WinPoint { t: number; p: number; goal?: RinkEvent }
/** The estimate after every goal and every minute of play, ending at the current clock. */
export function winSeries(feed: ArenaFeed, events: RinkEvent[], prior: number): WinPoint[] {
  const share = fitShare(prior)
  const { home } = feed.teams
  const goals = events.filter((e) => e.kind === 'goal')
  const final = feed.status.state === 'post'
  const now = final ? Math.max(3600, ...events.map((e) => e.t)) : feed.status.state === 'in' ? elapsedAt('nhl', feed.status.period, feed.status.clock) : 0
  const leadAt = (t: number) => goals.filter((g) => g.t <= t).reduce((s, g) => s + (g.shooter === home.id ? 1 : -1), 0)
  const perAt = (t: number) => (t < 1200 ? 1 : t < 2400 ? 2 : t < 3600 ? 3 : 4)
  const out: WinPoint[] = [{ t: 0, p: prior }]
  for (let t = 60; t < Math.min(now, 3600); t += 60) out.push({ t, p: liveHome(share, t, leadAt(t), perAt(t), false) })
  for (const g of goals) {
    out.push({ t: g.t - 1, p: liveHome(share, g.t - 1, leadAt(g.t - 1), g.period, false) })
    out.push({ t: g.t, p: liveHome(share, g.t, leadAt(g.t), g.period, false), goal: g })
  }
  out.push({ t: now, p: liveHome(share, now, leadAt(now), feed.status.period, final) })
  return out.sort((a, b) => a.t - b.t || (a.goal ? 1 : 0) - (b.goal ? 1 : 0))
}
