/**
 * One score-distribution shape for both sports.
 *
 *   NFL (and MLB V18 when bound): /probability/games/{id}/story population
 *   MLB fallback:                 /games/{id}/simulation joint_score_distribution (Gen 1, development)
 *
 * The browser never models anything. It only re-reads stored draws: sums cells
 * for a line, or samples cells in proportion to their stored counts for the
 * "play it out" animation.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import type { GameStory } from '../../services/gameStory'

export interface Cell { home: number; away: number; p: number }

export interface SimModel {
  sport: 'NFL' | 'MLB' | string
  home: { abbr: string; name: string }
  away: { abbr: string; name: string }
  startsAt: string | null
  final: { home: number; away: number } | null
  cells: Cell[]
  draws: number
  /** Scores above this were pooled by the producer ("12+"). */
  cap: number | null
  win: { home: number; away: number; tie: number }
  expected: { home: number; away: number }
  modelLabel: string
  development: boolean
  forecastAt: string | null
  hoursBeforeStart: number | null
  market: { homeNoVig: number | null; current: boolean; books: number; observedAt: string | null } | null
  bookLines: { spreadHome: number | null; total: number | null; fresh: boolean }
  story: GameStory | null
}

interface MlbSim {
  simulation_available: boolean
  identity?: { generated_at?: string; model_name?: string; model_version?: string; development_model?: boolean }
  inputs?: { simulations?: number }
  simulation_win_probability?: { home: number; away: number }
  means?: { home_runs: number; away_runs: number }
  joint_score_distribution?: { home_labels: string[]; away_labels: string[]; counts: number[][]; cap: number }
  unavailable_reason?: string | null
}

const label = (s: string, cap: number) => (s.startsWith('>') ? cap + 1 : Number(s))

function fromStory(story: GameStory): SimModel | null {
  const pop = story.population
  const ch = story.channel as unknown as { binding?: { modelId?: string }; winner?: { home: number; away: number; tie: number }; expected_score?: { home: number; away: number }; forecast_created_at?: string; horizon?: string; controlling_revision?: { hours_before_kickoff?: number } }
  if (!pop || !story.game.home || !story.game.away) return null
  const draws = pop.draw_count
  const cells = pop.cells.map((c) => ({ home: c.home, away: c.away, p: c.count / draws }))
  const win = ch.winner ?? (() => {
    let h = 0, a = 0, t = 0
    for (const c of cells) { if (c.home > c.away) h += c.p; else if (c.away > c.home) a += c.p; else t += c.p }
    return { home: h, away: a, tie: t }
  })()
  const exp = ch.expected_score ?? {
    home: cells.reduce((s, c) => s + c.home * c.p, 0), away: cells.reduce((s, c) => s + c.away * c.p, 0),
  }
  const model = ch.binding?.modelId ?? 'Model'
  return {
    sport: story.game.sport, home: { abbr: story.game.home.abbreviation, name: story.game.home.name },
    away: { abbr: story.game.away.abbreviation, name: story.game.away.name }, startsAt: story.game.starts_at,
    final: story.game.final, cells, draws, cap: null, win, expected: exp,
    modelLabel: `${model.replace(/_/g, ' ')}${ch.horizon ? ` · ${ch.horizon}` : ''}`, development: false,
    forecastAt: ch.forecast_created_at ?? null, hoursBeforeStart: ch.controlling_revision?.hours_before_kickoff ?? null,
    market: marketOf(story), bookLines: linesOf(story), story,
  }
}

function marketOf(story: GameStory | null): SimModel['market'] {
  if (!story?.market) return null
  return { homeNoVig: story.market.consensus_no_vig_home, current: story.market.consensus_is_current, books: story.market.books, observedAt: story.market.freshest_observed_at }
}
function linesOf(story: GameStory | null): SimModel['bookLines'] {
  return {
    spreadHome: story?.lines?.spread?.line ?? null,
    total: story?.lines?.total?.line ?? null,
    fresh: story?.lines?.from === 'CURRENT_QUOTES',
  }
}

function fromMlb(sim: MlbSim, story: GameStory | null, gameId: string): SimModel | null {
  const j = sim.joint_score_distribution
  if (!sim.simulation_available || !j) return null
  const total = j.counts.flat().reduce((s, n) => s + n, 0)
  const cells: Cell[] = []
  j.counts.forEach((row, hi) => row.forEach((n, ai) => {
    if (n > 0) cells.push({ home: label(j.home_labels[hi], j.cap), away: label(j.away_labels[ai], j.cap), p: n / total })
  }))
  const g = story?.game
  const generated = sim.identity?.generated_at ?? null
  return {
    sport: 'MLB',
    home: { abbr: g?.home?.abbreviation ?? 'HOME', name: g?.home?.name ?? 'Home' },
    away: { abbr: g?.away?.abbreviation ?? 'AWAY', name: g?.away?.name ?? 'Away' },
    startsAt: g?.starts_at ?? null, final: g?.final ?? null, cells, draws: total, cap: j.cap,
    win: { home: sim.simulation_win_probability?.home ?? 0, away: sim.simulation_win_probability?.away ?? 0, tie: 0 },
    expected: { home: sim.means?.home_runs ?? 0, away: sim.means?.away_runs ?? 0 },
    modelLabel: `MLB Gen 1 ${sim.identity?.model_version ? `(${sim.identity.model_version.split('-')[0]})` : ''}`.trim(),
    development: Boolean(sim.identity?.development_model),
    forecastAt: generated,
    hoursBeforeStart: generated && g?.starts_at ? (Date.parse(g.starts_at) - Date.parse(generated)) / 3600_000 : null,
    market: marketOf(story), bookLines: linesOf(story), story,
  }
  void gameId
}

/**
 * `staleTimeMs` defaults to the one-game cadence. A caller that loops over the
 * same few games (the Game Night director) passes a longer one so a stored
 * distribution, which does not change, is not refetched on every cut.
 *
 * `sport`, when the caller already knows it, keeps the MLB Gen 1 fallback for
 * MLB: for any other sport a failed story read is reported as a failed read
 * (and retried later) instead of asking `/games/{id}/simulation`, which
 * re-runs a Monte Carlo on the server for every GET. Without it the behaviour
 * is unchanged.
 */
export function useSimModel(gameId: string, opts: { staleTimeMs?: number; sport?: string } = {}) {
  const staleTimeMs = opts.staleTimeMs ?? STALE_TIME.game
  const knownSport = opts.sport ?? null
  const loader = useCallback(async (signal: AbortSignal) => {
    let storyError: unknown = null
    const story = await apiRequest<GameStory>(`/probability/games/${encodeURIComponent(gameId)}/story`, {}, signal).catch((e: unknown) => { storyError = e; return null })
    const fromS = story ? fromStory(story) : null
    if (fromS) return { model: fromS, reason: null as string | null }
    if (!story && knownSport != null && knownSport !== 'MLB') throw storyError ?? new Error('The stored distribution could not be read.')
    if (!story || story.game.sport === 'MLB') {
      const sim = await apiRequest<MlbSim>(`/games/${encodeURIComponent(gameId)}/simulation`, {}, signal).catch(() => null)
      const m = sim ? fromMlb(sim, story, gameId) : null
      if (m) return { model: m, reason: null }
      return { model: null, reason: sim?.unavailable_reason ?? 'No simulation is stored for this game yet.', story }
    }
    return { model: null, reason: (story.channel as { error?: { message?: string } }).error?.message ?? 'No simulation is stored for this game yet.', story }
  }, [gameId, knownSport])
  return useApiResource(loader, { key: cacheKey('sim-model', { gameId }), staleTimeMs })
}

// ------------------------------------------------------------ math helpers --

export function distribution(cells: Cell[], kind: 'margin' | 'total') {
  const map = new Map<number, number>()
  for (const c of cells) {
    const v = kind === 'margin' ? c.home - c.away : c.home + c.away
    map.set(v, (map.get(v) ?? 0) + c.p)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([value, p]) => ({ value, p }))
}

/**
 * Probability that a side wins, pushes or loses at a line.
 *  spread: line is attached to HOME (home covers when margin + line > 0)
 *  total:  OVER wins when total > line
 */
export function lineOutcome(cells: Cell[], kind: 'spread' | 'total', line: number) {
  let win = 0, push = 0, loss = 0
  for (const c of cells) {
    const x = kind === 'spread' ? (c.home - c.away) + line : (c.home + c.away) - line
    if (x > 0) win += c.p; else if (x < 0) loss += c.p; else push += c.p
  }
  return { win, push, loss }
}

/** Fair American odds for a win probability, treating pushes as returned stakes. */
export function fairAmerican(win: number, loss: number): number | null {
  const p = win + loss > 0 ? win / (win + loss) : 0
  if (!(p > 0 && p < 1)) return null
  return p >= 0.5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p)
}

/** Sampler over cells in proportion to stored probability. */
export function makeSampler(cells: Cell[]) {
  const cum: number[] = []
  let s = 0
  for (const c of cells) { s += c.p; cum.push(s) }
  return () => {
    const r = Math.random() * s
    let lo = 0, hi = cum.length - 1
    while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] < r) lo = mid + 1; else hi = mid }
    return cells[lo]
  }
}
