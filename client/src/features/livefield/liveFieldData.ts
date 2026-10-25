/**
 * AISP Live View (TEST MODE): data layer.
 *
 * Three kinds of reads, each labelled on the page by where it came from:
 *
 *   AISP (stored, governed)   /mission-control?date=   tonight's games and their stages (30 s)
 *                             /scoreboard?game_ids=    the observed score AISP settles from (30 s)
 *                             /probability/games/{id}/story   the pregame model vs the market
 *   Preview feed (free, public, proxied by the preview server, never stored)
 *                             /preview-api/mlb/live    MLB Stats API: diamond state, batter, pitcher,
 *                                                      defenders, last plays, live win probability (10 s)
 *   The Booth                 POST /preview-api/booth  one question -> a small language model that
 *                                                      explains, grounded on the context this page sends
 *
 * Nothing here places a bet, triggers a job or writes AISP data. The preview
 * endpoints exist only on the test-mode server; a production build shows the
 * AISP layer and says the field feed is not wired.
 */
import { useCallback, useMemo, useRef, useState } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { cacheKey } from '../../services/queryCache'
import { useMissionControl, type MissionGame } from '../mission/missionData'

export const FIELD_POLL_MS = 10_000
export const FIELD_STALE_MS = 8_000
export const STORY_STALE_MS = 5 * 60_000

export interface Person { id: number; name: string | null; headshot: string }
export type Base = 'first' | 'second' | 'third'
export type DefenseSlot = 'catcher' | 'first' | 'second' | 'third' | 'shortstop' | 'left' | 'center' | 'right'

export interface MlbPlay {
  atBatIndex: number | null
  inning: number | null
  half: 'top' | 'bottom' | null
  event: string | null
  description: string
  rbi: number | null
  awayScore: number | null
  homeScore: number | null
  isScoringPlay: boolean | null
  endTime: string | null
}

export interface WpPoint {
  atBatIndex: number | null
  inning: number | null
  half: 'top' | 'bottom' | null
  /** Home win probability 0–1 after this at-bat, as MLB publishes it. */
  home: number
  event: string | null
  awayScore: number | null
  homeScore: number | null
}

export interface BattingLine {
  season: { avg: string | null; obp: string | null; slg: string | null; ops: string | null; homeRuns: number | null; rbi: number | null; hits: number | null; atBats: number | null; strikeOuts: number | null; baseOnBalls: number | null; stolenBases: number | null }
  game: { summary: string | null; hits: number | null; atBats: number | null; runs: number | null; rbi: number | null; homeRuns: number | null; baseOnBalls: number | null; strikeOuts: number | null }
  position: string | null
  jersey: string | null
}

export interface PitchingLine {
  season: { era: string | null; whip: string | null; inningsPitched: string | null; strikeOuts: number | null; baseOnBalls: number | null; wins: number | null; losses: number | null; saves: number | null; homeRuns: number | null }
  game: { summary: string | null; inningsPitched: string | null; hits: number | null; runs: number | null; earnedRuns: number | null; baseOnBalls: number | null; strikeOuts: number | null; pitchesThrown: number | null; numberOfPitches: number | null; strikes: number | null; homeRuns: number | null }
  jersey: string | null
}


export interface RegularBatting { pa: number | null; k: number | null; bb: number | null; hbp: number | null; h: number | null; hr: number | null; doubles: number | null; triples: number | null; gidp: number | null; sb: number | null; cs: number | null; avg: string | null; obp: string | null; slg: string | null; ops: string | null; go_ao: string | null; games: number | null }
export interface RegularPitching { bf: number | null; k: number | null; bb: number | null; hbp: number | null; h: number | null; hr: number | null; go_ao: string | null; sb: number | null; cs: number | null; era: string | null; whip: string | null; k9: string | null; bb9: string | null; strike_pct: string | null; ip: string | null; gidp: number | null }
export interface RegularCatching { cs: number | null; sb_allowed: number | null; cs_pct: string | null; passed_ball: number | null; pickoffs: number | null }
export interface RegularLines { season: string; batter: RegularBatting | null; pitcher: RegularPitching | null; runners: Record<Base, RegularBatting | null>; catcher: RegularCatching | null; on_deck: RegularBatting | null }
export interface Zone { zone: string; value: string; temp: string }

export interface MlbLive {
  source: 'MLB_STATS_API'
  found: boolean
  reason?: string
  game_pk?: number
  retrieved_at?: string
  series?: string | null
  series_game?: number | null
  venue?: string | null
  weather?: { condition: string | null; temp: string | null; wind: string | null }
  status?: { abstract: string | null; detailed: string | null; inning: number | null; inning_ordinal: string | null; inning_state: string | null; is_top: boolean | null; scheduled_innings: number | null }
  score?: { away: number | null; home: number | null; away_hits: number | null; home_hits: number | null; away_errors: number | null; home_errors: number | null }
  count?: { balls: number | null; strikes: number | null; outs: number | null }
  runners?: Record<Base, Person | null>
  teams?: Record<'away' | 'home', { id: number | null; abbr: string | null; name: string | null; record: { wins: number; losses: number } | null; probable: Person | null }>
  innings?: { num: number; away: number | null; home: number | null }[]
  offense?: { batter: Person | null; on_deck: Person | null; in_hole: Person | null; batting_order: number | null; team_id: number | null; bat_side: string | null }
  defense?: { pitcher: Person | null; pitch_hand: string | null; team_id: number | null } & Record<DefenseSlot, Person | null>
  batter_stats?: BattingLine | null
  pitcher_stats?: PitchingLine | null
  regular?: RegularLines
  zones?: Zone[]
  at_bat?: AtBat | null
  last_hit?: HitEvent | null
  recent_plays?: MlbPlay[]
  win_probability?: WpPoint[]
}

/** The slice of /probability/games/{id}/story this page reads. */
export interface GameStory {
  schema_version: string
  game: { game_id: string; sport: string; status: string; starts_at: string; home: { abbreviation: string; name: string }; away: { abbreviation: string; name: string } }
  model_vs_market: { model_home: number | null; model_away: number | null; model_tie: number | null; model_home_two_way: number | null; model_source: string | null; market_home_no_vig: number | null; market_is_current: boolean; market_books: number }
  market: { price_state: string; consensus_no_vig_home: number | null; consensus_is_current: boolean; books: number; freshest_observed_at: string | null; best: { HOME: unknown; AWAY: unknown } }
  facts: { kind: string; text: string }[]
}

// ----------------------------------------------------------------- reads --

export function useLiveSlate(day: string) {
  return useMissionControl(day)
}

export function useStory(gameId: string | null) {
  const loader = useCallback(async (signal: AbortSignal) =>
    (gameId ? apiRequest<GameStory>(`/probability/games/${encodeURIComponent(gameId)}/story`, {}, signal) : null), [gameId])
  return useApiResource<GameStory | null>(loader, { key: cacheKey('lf-story', { gameId: gameId ?? '' }), staleTimeMs: 60_000, refreshIntervalMs: gameId ? 60_000 : undefined })
}

export const PREVIEW_ONLY = 'The field feed exists only on the test-mode preview server.'

async function fetchPreview<T>(path: string, signal: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { accept: 'application/json' } })
  const type = res.headers.get('content-type') ?? ''
  if (res.status === 404 || !type.includes('application/json')) throw new Error(PREVIEW_ONLY)
  const body = (await res.json()) as T
  if (!res.ok) throw new Error((body as { reason?: string; error?: string }).reason ?? (body as { error?: string }).error ?? `HTTP ${res.status}`)
  return body
}

export function useMlbLive(args: { date: string; home: string | null; away: string | null; enabled: boolean }) {
  const { date, home, away, enabled } = args
  const on = enabled && Boolean(home && away)
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!on) return null
    const q = new URLSearchParams({ date, home: home ?? '', away: away ?? '' })
    return fetchPreview<MlbLive>(`/preview-api/mlb/live?${q.toString()}`, signal)
  }, [on, date, home, away])
  return useApiResource<MlbLive | null>(loader, {
    key: cacheKey('lf-mlb', { date, home: home ?? '', away: away ?? '', on }),
    staleTimeMs: FIELD_STALE_MS,
    refreshIntervalMs: on ? FIELD_POLL_MS : undefined,
  })
}

// ----------------------------------------------------------------- booth --

export interface BoothAnswer { answer: string; model: string; latency_ms: number; usage: { input_tokens: number | null; output_tokens: number | null }; disclaimer: string }
export interface BoothTurn { id: number; question: string; answer: BoothAnswer | null; error: string | null; at: number }

export function useBooth(contextRef: { current: unknown }) {
  const [turns, setTurns] = useState<BoothTurn[]>([])
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)
  const ask = useCallback(async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    const id = ++seq.current
    setBusy(true)
    setTurns((prev) => [{ id, question: q, answer: null, error: null, at: Date.now() }, ...prev].slice(0, 6))
    try {
      const res = await fetch('/preview-api/booth', {
        method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({ question: q, context: contextRef.current }),
      })
      const type = res.headers.get('content-type') ?? ''
      if (!type.includes('application/json')) throw new Error('The Booth is only wired on the test-mode preview server.')
      const body = (await res.json()) as Partial<BoothAnswer> & { error?: string }
      if (!res.ok || !body.answer) throw new Error(body.error ?? `HTTP ${res.status}`)
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, answer: body as BoothAnswer } : t)))
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The Booth did not answer.'
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, error: message } : t)))
    } finally {
      setBusy(false)
    }
  }, [busy, contextRef])
  return { turns, busy, ask }
}

// ----------------------------------------------------------------- words --

export const SPORT_LABEL: Record<string, string> = { MLB: 'MLB', NFL: 'NFL', CFB: 'College Football', NHL: 'NHL', NBA: 'NBA' }

export function gameLabel(g: MissionGame): string {
  return `${g.away.abbr} @ ${g.home.abbr}`
}

export const pct = (p: number | null | undefined, digits = 0): string => (p == null || Number.isNaN(p) ? '—' : `${(p * 100).toFixed(digits)}%`)

export function etClock(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(d) + ' ET'
}

export function ageSeconds(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? null : Math.max(0, Math.round((now - t) / 1000))
}

/** The last meaningful swing: the largest single at-bat change in home win probability among the last `n` points. */
export function biggestSwing(points: readonly WpPoint[], n = 12): { from: WpPoint; to: WpPoint; delta: number } | null {
  if (points.length < 2) return null
  const start = Math.max(1, points.length - n)
  let best: { from: WpPoint; to: WpPoint; delta: number } | null = null
  for (let i = start; i < points.length; i += 1) {
    const delta = points[i].home - points[i - 1].home
    if (!best || Math.abs(delta) > Math.abs(best.delta)) best = { from: points[i - 1], to: points[i], delta }
  }
  return best
}

/** Ordered picks for the game list: live first, then upcoming by start, then finals. */
export function orderSlate(games: readonly MissionGame[]): MissionGame[] {
  const rank = (g: MissionGame) => (g.status === 'live' || g.phase === 'STARTED' ? 0 : g.status === 'scheduled' ? 1 : 2)
  return [...games].sort((a, b) => rank(a) - rank(b) || a.starts_at.localeCompare(b.starts_at) || a.matchup.localeCompare(b.matchup))
}

/** A stable, compact context for the Booth: numbers with their sources, no images, no long series. */
export function boothContext(input: {
  now: number
  game: MissionGame | null
  story: GameStory | null
  field: MlbLive | null
  scoreboard: { status?: string; status_detail?: string | null; away_score?: number | null; home_score?: number | null } | null
  football?: unknown
}): unknown {
  const { game, story, field, scoreboard } = input
  if (!game) return input.football ? { as_of: new Date(input.now).toISOString(), site: 'AISP Live View (research and paper only)', game_on_screen: 'a football game; everything you need is in football_feed', football_feed: input.football } : { note: 'no game focused' }
  const wp = field?.win_probability ?? []
  const tail = wp.slice(-8)
  return {
    as_of: new Date(input.now).toISOString(),
    site: 'AISP Live View (research and paper only)',
    game: { sport: game.sport, matchup: game.matchup, starts_at: game.starts_at, status: game.status, phase: game.phase, away: game.away, home: game.home },
    aisp_model_pregame: story?.model_vs_market ? {
      source: story.model_vs_market?.model_source,
      home_win_probability: story.model_vs_market?.model_home,
      away_win_probability: story.model_vs_market?.model_away,
    } : 'no stored AISP forecast for this game',
    sportsbook_market_pregame: story?.market ? {
      state: story.market.price_state,
      consensus_no_vig_home: story.market.consensus_no_vig_home,
      books: story.market.books,
      freshest_quote_at: story.market.freshest_observed_at,
    } : null,
    aisp_facts: story?.facts ?? [],
    aisp_scoreboard: scoreboard,
    football_feed: input.football ?? undefined,
    field_feed: field?.found ? {
      source: 'MLB Stats API (live, free public feed shown in test mode)',
      retrieved_at: field.retrieved_at,
      series: field.series, series_game: field.series_game, venue: field.venue, weather: field.weather,
      status: field.status, score: field.score, count: field.count,
      runners: field.runners ? Object.fromEntries(Object.entries(field.runners).map(([b, p]) => [b, p?.name ?? null])) : null,
      batter: field.offense?.batter?.name ?? null, bat_side: field.offense?.bat_side ?? null, batter_stats: field.batter_stats ?? null,
      pitcher: field.defense?.pitcher?.name ?? null, pitch_hand: field.defense?.pitch_hand ?? null, pitcher_stats: field.pitcher_stats ?? null,
      on_deck: field.offense?.on_deck?.name ?? null,
      probable_pitchers: field.teams ? { away: field.teams.away.probable?.name ?? null, home: field.teams.home.probable?.name ?? null } : null,
      recent_plays: (field.recent_plays ?? []).slice(-6).map((p) => ({ inning: p.inning, half: p.half, event: p.event, description: p.description, score: `${p.awayScore ?? '?'}-${p.homeScore ?? '?'}` })),
      live_home_win_probability_last_8_at_bats: tail.map((p) => ({ inning: p.inning, half: p.half, event: p.event, home: p.home })),
      live_home_win_probability_now: wp.length ? wp[wp.length - 1].home : null,
    } : (field?.reason ?? undefined),
  }
}

export function useLatest<T>(value: T) {
  const ref = useRef(value)
  ref.current = value
  return ref
}

export const emptyWp: WpPoint[] = []
export function useWpSeries(field: MlbLive | null): WpPoint[] {
  return useMemo(() => field?.win_probability ?? emptyWp, [field])
}

// ------------------------------------------------------- plate appearance --

export interface PaOutcomes { k: number; bb: number; single: number; xbh: number; hr: number; out: number; basis: string }
/** Approximate MLB per-plate-appearance rates, used only as the odds-ratio baseline. */
const LEAGUE = { k: 0.222, bb: 0.085, hr: 0.031, h: 0.235 }
const clamp = (x: number) => Math.min(0.98, Math.max(0.002, x))
const odds = (p: number) => clamp(p) / (1 - clamp(p))
const fromOdds = (o: number) => o / (1 + o)
const blend = (b: number, p: number, league: number) => fromOdds((odds(b) * odds(p)) / odds(league))

/**
 * What this plate appearance looks like from the two season lines: an
 * odds-ratio blend (batter × pitcher ÷ league) of strikeout, walk, home-run
 * and hit rates, hits split by the batter's own single/extra-base mix. An
 * estimate from public season lines, labelled as such; not an AISP model and
 * never a bet signal.
 */
export function paOutcomes(b: RegularBatting | null | undefined, p: RegularPitching | null | undefined): PaOutcomes | null {
  if (!b?.pa || !p?.bf || b.pa < 50 || p.bf < 50) return null
  const n = (v: number | null) => v ?? 0
  const bk = n(b.k) / b.pa, bbb = (n(b.bb) + n(b.hbp)) / b.pa, bhr = n(b.hr) / b.pa, bh = n(b.h) / b.pa
  const pk = n(p.k) / p.bf, pbb = (n(p.bb) + n(p.hbp)) / p.bf, phr = n(p.hr) / p.bf, ph = n(p.h) / p.bf
  const k = blend(bk, pk, LEAGUE.k)
  const bb = blend(bbb, pbb, LEAGUE.bb)
  const hr = blend(bhr, phr, LEAGUE.hr)
  const h = Math.max(hr, blend(bh, ph, LEAGUE.h))
  const nonHr = h - hr
  const xbhShare = (n(b.doubles) + n(b.triples)) / Math.max(1, n(b.h) - n(b.hr))
  const xbh = nonHr * xbhShare
  const single = nonHr - xbh
  let out = 1 - k - bb - hr - xbh - single
  let scale = 1
  if (out < 0.05) { scale = 0.95 / (k + bb + hr + xbh + single); out = 0.05 }
  return { k: k * scale, bb: bb * scale, single: single * scale, xbh: xbh * scale, hr: hr * scale, out, basis: `${b.pa} PA vs ${p.bf} BF, 2026 regular season` }
}

export const ratePct = (num: number | null | undefined, den: number | null | undefined, digits = 1): string =>
  num == null || !den ? '—' : `${((num / den) * 100).toFixed(digits)}%`

// ------------------------------------------------------ pitches and hits --

export interface PitchEvent { n: number | null; pX: number | null; pZ: number | null; szTop: number | null; szBot: number | null; speed: number | null; type: string | null; call: string | null; isStrike: boolean | null; isBall: boolean | null; inPlay: boolean | null; zone: number | null; balls: number | null; strikes: number | null; spin: number | null }
export interface AtBat { atBatIndex: number | null; inning: number | null; half: 'top' | 'bottom' | null; complete: boolean | null; batter: string | null; bat_side: string | null; event: string | null; description: string | null; is_out: boolean | null; pitches: PitchEvent[] }
export interface HitEvent { key: string; atBatIndex: number | null; inning: number | null; half: 'top' | 'bottom' | null; coordX: number | null; coordY: number | null; speed: number | null; angle: number | null; distance: number | null; trajectory: string | null; hardness: string | null; location: string | null; event: string | null; description: string | null; is_out: boolean; fielder: string | null; batter: string | null }

const SLOT_BY_ABBR: Record<string, DefenseSlot | 'pitcher'> = { P: 'pitcher', C: 'catcher', '1B': 'first', '2B': 'second', '3B': 'third', SS: 'shortstop', LF: 'left', CF: 'center', RF: 'right' }
const SLOT_BY_CODE: Record<string, DefenseSlot | 'pitcher'> = { '1': 'pitcher', '2': 'catcher', '3': 'first', '4': 'second', '5': 'third', '6': 'shortstop', '7': 'left', '8': 'center', '9': 'right' }

/** The fielder who handled the ball (credit first, scorer's location code second). */
export function hitSlot(h: HitEvent | null | undefined): DefenseSlot | 'pitcher' | null {
  if (!h) return null
  return (h.fielder ? SLOT_BY_ABBR[h.fielder] : undefined) ?? (h.location ? SLOT_BY_CODE[h.location] : undefined) ?? null
}

export function trajectoryWord(t: string | null | undefined): string {
  if (!t) return 'Ball in play'
  const w = t.replace(/_/g, ' ')
  return w.charAt(0).toUpperCase() + w.slice(1)
}

/** MLB's hit coordinates (about 2.5 ft per unit, home plate near 125.4, 198.3) to feet from home. */
function hitFeet(h: HitEvent): { x: number; y: number } | null {
  if (h.coordX == null || h.coordY == null) return null
  return { x: 2.5 * (h.coordX - 125.42), y: 2.5 * (198.27 - h.coordY) }
}

/** Spray angle in radians, 0 = straight to centre field, negative = left-field side. */
export function hitSprayAngle(h: HitEvent): number | null {
  const f = hitFeet(h)
  if (!f) return null
  return Math.max(-0.83, Math.min(0.83, Math.atan2(f.x, Math.max(1, f.y))))
}

const HOME = { x: 500, y: 620 }

/** Where the ball ended up on the drawn field (the field is stylised: infield to scale, outfield compressed). */
export function hitToSvg(h: HitEvent): { x: number; y: number; r: number } | null {
  const f = hitFeet(h)
  const ang = hitSprayAngle(h)
  if (!f || ang == null) return null
  const dist = Math.hypot(f.x, f.y)
  const r = Math.min(640, dist <= 130 ? dist * (240 / 130) : 240 + (dist - 130) * (380 / 270))
  return { x: HOME.x + r * Math.sin(ang), y: HOME.y - r * Math.cos(ang), r }
}

export interface HitFlight { d: string; trail: string; end: { x: number; y: number }; dur: number; peak: number }

export function hitFlight(h: HitEvent, fallback?: { x: number; y: number } | null): HitFlight | null {
  const end = hitToSvg(h) ?? (fallback ? { x: fallback.x, y: fallback.y, r: Math.hypot(fallback.x - HOME.x, fallback.y - HOME.y) } : null)
  if (!end) return null
  const traj = h.trajectory ?? ''
  const fly = /fly|popup|pop/.test(traj)
  const line = /line/.test(traj)
  const peak = fly ? Math.min(330, Math.max(70, (h.angle ?? 30) * 5 + end.r * 0.15)) : line ? Math.min(90, end.r * 0.12) : 0
  const ctrl = { x: (HOME.x + end.x) / 2, y: (HOME.y + end.y) / 2 - peak * 2 }
  const d = peak > 0 ? `M ${HOME.x} ${HOME.y} Q ${ctrl.x.toFixed(1)} ${ctrl.y.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}` : `M ${HOME.x} ${HOME.y} L ${end.x.toFixed(1)} ${end.y.toFixed(1)}`
  const dur = peak > 0 ? 0.9 + Math.min(1.1, (h.distance ?? end.r) / 350) : 0.6 + Math.min(0.5, (h.distance ?? 100) / 300)
  return { d, trail: d, end: { x: end.x, y: end.y }, dur: Number(dur.toFixed(2)), peak }
}
