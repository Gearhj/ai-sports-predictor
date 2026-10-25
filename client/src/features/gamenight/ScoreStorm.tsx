/**
 * Act I, "The 10,000": the focused game's stored draws fly onto a score grid
 * one at a time until the picture IS the model's stored tally. Then the
 * market's same-book price drops onto the win bar and, once a final is known,
 * crosshairs land on the real score.
 *
 * Honesty boundary, stated on the stage itself:
 *   - NFL / CFB / MLB replay the EXACT stored multiset in a shuffled order. The
 *     order is ours; the finished grid is exactly the model's.
 *   - NHL has no stored grid: its regulation grid is derived from the stored
 *     expected goals (and only drawn if it reproduces the stored regulation
 *     probabilities), and the replay SAMPLES 10,000 draws from it.
 *   - Live mode filters pregame draws by the score so far. It ignores the clock
 *     and is never a live win probability; no conditional percentage is shown.
 *   - Ticker lines are simulated draws, never play-by-play.
 *   - The glow, comet trails and landing flashes are decoration on a separate
 *     layer: every cell's colour is computed from its exact count alone.
 *
 * Loads only the focused game: one request, no polling, no prefetch.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Maximize, Pause, Play, Shuffle } from 'lucide-react'
import { ChartTable } from '../../components/command/chartKit'
import { useSimModel } from '../simulator/simData'
import { pct, usd, american as fmtAmerican } from '../mission/missionData'
import {
  FOCUS_STALE_MS, pooledCell, scoreLabel, stormFromNhl, stormFromSim, useNhlFocus,
  type FocusTarget, type HeldBet, type StormSource,
} from './gameNightData'
import { basisCaption, breakEven, gapText, isDisplayWarning, type GapReading } from './gap'
import {
  SLOW_DRAWS, STAGE_RGB, axisRange, cellColor, cellIndexOf, funnelHalfWidth, isReachable, launchedBy, maxCount, mulberry32, rankOf,
  reachableCount, tickStep, toneOf, topScores, winsFor, type Side, type TallyCell,
} from './stormTally'
import { canDraw2d, context2d, devicePixelRatioCapped, hasRaf, useAutoScroll, useBox, useFullscreenElement, useOnScreen, usePageVisible } from './gnHooks'
import { etShort } from './TopBar'

const MAX_PARTICLES = 400
const MAX_FLASHES = 500
type Speed = 1 | 4 | 'instant'

export interface StormContext {
  target: FocusTarget
  bet: HeldBet | null
  gap: GapReading | null
  /** The decision's selection when the focused game has a decision (with or without a bet). */
  selection: string | null
  decisionAt: string | null
  live: { away: number; home: number; detail: string } | null
  observedFinal: { away: number; home: number; source: string | null } | null
  reducedMotion: boolean
  tv: boolean
  /** A settlement moment cut here: replay at 4× so the crosshair lands while the lower-third is on screen. */
  fast?: boolean
  /** Where the focused distribution came from, for the methods table. */
  onInfo?: (info: StormInfo) => void
}

export interface StormInfo {
  gameId: string
  endpoint: string
  asOf: number | null
  cells: number | null
  draws: number | null
  mode: 'exact' | 'sampled' | null
  nhlCheck: { ok: boolean; maxError: number | null } | null
}

const intFmt = new Intl.NumberFormat('en-US')
const n0 = (v: number) => intFmt.format(v)
const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n0(n)}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`
}

/** A stable seed per game, so the first replay of a game is the same for everyone. */
export function seedOf(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

// --------------------------------------------------------------- loaders --

/** Chooses the loader by sport: NHL reads its game record; every other sport the stored story. */
export function StormStage(props: StormContext) {
  const { target } = props
  return (
    <section className="gn-storm" aria-labelledby="gn-storm-title">
      <StageHeader target={target} bet={props.bet} />
      {target.sport === 'NHL'
        ? <NhlFocus {...props} />
        : <SimFocus {...props} />}
    </section>
  )
}

function StageHeader({ target, bet }: { target: FocusTarget; bet: HeldBet | null }) {
  return (
    <header className="gn-storm__head">
      <p className="gn-kicker"><span className={`gn-tag gn-tag--${target.sport.toLowerCase()}`}>{target.sport}</span> Act I · The 10,000</p>
      <h2 id="gn-storm-title">
        {target.away.abbr} <em>@</em> {target.home.abbr}
        {bet
          ? <span className="gn-storm__pick"> · our pick <b>{bet.selection} {fmtAmerican(bet.american)}</b>{bet.sportsbook ? ` · ${bet.sportsbook}` : ''}{bet.stake != null ? ` · ${usd(bet.stake).replace('.00', '')}` : ''}</span>
          : <span className="gn-storm__pick gn-storm__pick--none"> · no bet on this game</span>}
      </h2>
      {bet?.slateDate && target.startsAt && bet.slateDate !== etDate(target.startsAt) && (
        <p className="gn-storm__note">Decided on the {bet.slateDate} slate.</p>
      )}
    </header>
  )
}

const etDate = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(iso))

function SimFocus(props: StormContext) {
  // The sport is known here, so a failed story read for anything but MLB is a failed read, not a server re-simulation.
  const res = useSimModel(props.target.gameId, { staleTimeMs: FOCUS_STALE_MS, sport: props.target.sport })
  const model = res.data?.model ?? null
  const source = useMemo(() => (model ? stormFromSim(props.target.gameId, model) : null), [model, props.target.gameId])
  const { onInfo } = props
  useEffect(() => {
    if (!res.data || !onInfo) return
    onInfo({
      gameId: props.target.gameId,
      endpoint: model?.development ? `/games/${props.target.gameId}/simulation` : `/probability/games/${props.target.gameId}/story`,
      asOf: res.asOf, cells: source?.population ? model?.cells.length ?? null : null,
      draws: source?.population?.drawCount ?? null, mode: source?.population ? 'exact' : null, nhlCheck: null,
    })
  }, [res.data, res.asOf, source, model, onInfo, props.target.gameId])
  if (!res.data) return <StageLoading loading={res.loading} error={res.error} retry={res.refresh} />
  if (!source) return <SplitOnly target={props.target} reason={res.data.reason ?? 'No simulation is stored for this game yet.'} />
  return <ScoreStorm source={source} {...props} />
}

function NhlFocus(props: StormContext) {
  const id = props.target.sourceGameId ?? props.target.gameId
  const res = useNhlFocus(id)
  const source = useMemo(() => (res.data ? stormFromNhl(props.target.gameId, res.data) : null), [res.data, props.target.gameId])
  const { onInfo } = props
  useEffect(() => {
    if (!res.data || !source || !onInfo) return
    const nhl = source.population?.nhl ?? null
    onInfo({
      gameId: props.target.gameId, endpoint: `/nhl/games/${id}`, asOf: res.asOf,
      cells: source.population ? (source.population.window.homeMax + 1) * (source.population.window.awayMax + 1) : null,
      draws: source.population?.drawCount ?? null, mode: source.population ? 'sampled' : null,
      nhlCheck: { ok: Boolean(nhl), maxError: nhl?.maxError ?? null },
    })
  }, [res.data, res.asOf, source, onInfo, props.target.gameId, id])
  if (!res.data || !source) return <StageLoading loading={res.loading} error={res.error} retry={res.refresh} />
  return <ScoreStorm source={source} {...props} />
}

function StageLoading({ loading, error, retry }: { loading: boolean; error: string | null; retry: () => void }) {
  if (error && !loading) {
    return (
      <div className="gn-storm__empty" role="alert">
        <strong>The stored distribution could not be read.</strong>
        <p>{error}</p>
        <button type="button" className="gn-btn" onClick={retry}>Try again</button>
      </div>
    )
  }
  return <div className="gn-storm__empty" aria-busy="true"><span className="gn-skel" /><p>Reading the stored distribution…</p></div>
}

/** No population: the honest empty state, with the probability split only. */
function SplitOnly({ target, reason }: { target: FocusTarget; reason: string }) {
  const f = target.game?.stages?.forecast
  return (
    <div className="gn-storm__empty">
      <strong>No stored draws to replay for this game.</strong>
      <p>{reason}</p>
      {f?.home_win_prob != null && f?.away_win_prob != null && (
        <p className="gn-storm__splitonly">Stored forecast: {target.away.abbr} {pct(f.away_win_prob, 1)} · {target.home.abbr} {pct(f.home_win_prob, 1)}</p>
      )}
    </div>
  )
}

// ------------------------------------------------------------- geometry --

interface Geo {
  size: number
  left: number; top: number; plotW: number; plotH: number
  cw: number; ch: number
  homeMax: number; awayMax: number
}

function geometry(size: number, ext: { homeMax: number; awayMax: number }): Geo {
  const left = 30, right = 8, top = 24, bottom = 28
  const plotW = Math.max(40, size - left - right)
  const plotH = Math.max(40, size - top - bottom)
  return {
    size, left, top, plotW, plotH,
    cw: plotW / (ext.awayMax + 1), ch: plotH / (ext.homeMax + 1),
    homeMax: ext.homeMax, awayMax: ext.awayMax,
  }
}

function cellRect(g: Geo, away: number, home: number) {
  const x0 = Math.round(g.left + away * g.cw)
  const x1 = Math.round(g.left + (away + 1) * g.cw)
  const y0 = Math.round(g.top + (g.homeMax - home) * g.ch)
  const y1 = Math.round(g.top + (g.homeMax - home + 1) * g.ch)
  return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) }
}

const centerOf = (g: Geo, away: number, home: number) => ({
  x: g.left + (away + 0.5) * g.cw, y: g.top + (g.homeMax - home + 0.5) * g.ch,
})

const GROUND = 'rgb(12,24,41)'
const STAGE_BG = '#07111f'

/** Ticks for one axis, with the pooled "cap+1 or more" cell always labelled. */
function ticksFor(max: number, cap: number | null): number[] {
  const step = tickStep(max)
  const out: number[] = []
  for (let v = 0; v <= max; v += step) out.push(v)
  if (cap != null && max === cap + 1 && !out.includes(max)) {
    // Make room for the pooled label: drop a regular tick that would collide with it.
    if (out.length && max - out[out.length - 1] < Math.max(2, step / 2)) out.pop()
    out.push(max)
  }
  return out
}

// ------------------------------------------------------------ the storm --

interface StormProps extends StormContext { source: StormSource }

interface Particle { idx: number; start: number; dur: number; sx: number }
interface Flash { idx: number; t: number }

function ScoreStormImpl(props: StormProps) {
  const { source, bet, gap, live, reducedMotion, tv } = props
  const pop = source.population
  const cap = source.cap
  const awayTeam = source.away.abbr
  const homeTeam = source.home.abbr
  const label = useCallback((v: number) => scoreLabel(v, cap), [cap])

  const [seed, setSeed] = useState(() => seedOf(source.gameId))
  const replay = useMemo(() => (pop ? pop.draw(seed) : null), [pop, seed])
  const cells: TallyCell[] = useMemo(() => replay?.cells ?? [], [replay])
  const order = replay?.order ?? null
  const total = order?.length ?? 0
  const extent = useMemo(() => pop?.window ?? axisRange(cells), [pop, cells])
  const maxC = useMemo(() => maxCount(cells.filter((c) => c.home <= extent.homeMax && c.away <= extent.awayMax)), [cells, extent])

  // The side the grid is tinted for: our pick, else the model's favourite.
  const tintSide: Side = bet?.side ?? (source.split.home >= source.split.away ? 'HOME' : 'AWAY')
  const tintTeam = tintSide === 'HOME' ? homeTeam : awayTeam
  const otherTeam = tintSide === 'HOME' ? awayTeam : homeTeam

  // Prefix wins over the replay order: the running meter reads them at any draw count.
  const prefixWins = useMemo(() => {
    const out = new Int32Array(total + 1)
    if (!order) return out
    for (let i = 0; i < total; i++) out[i + 1] = out[i] + (winsFor(cells[order[i]], tintSide) ? 1 : 0)
    return out
  }, [order, total, cells, tintSide])
  const totalWins = prefixWins[total] ?? 0
  const share = total ? totalWins / total : 0

  // The square. On a page it follows the column's width (≤ 540 px). On the TV wall it is sized from the stage
  // body's measured box: as tall as the row allows, never more than 60% of the stage's width; the readouts take
  // the rest of the width.
  const gridBox = useBox<HTMLDivElement>({ width: 480, height: 480 })
  const bodyBox = useBox<HTMLDivElement>({ width: 900, height: 480 })
  const gridRef = useRef<HTMLCanvasElement | null>(null)
  const fxRef = useRef<HTMLCanvasElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  // "Fullscreen stage": the square may grow to the screen's height, not the page's 540 px.
  const fullscreenEl = useFullscreenElement()
  const stageFullscreen = fullscreenEl != null && typeof document !== 'undefined' && fullscreenEl !== document.documentElement
    && stageRef.current != null && fullscreenEl.contains(stageRef.current)
  const pageMax = stageFullscreen && typeof window !== 'undefined' ? Math.max(540, Math.min(900, window.innerHeight - 220)) : 540
  // On the wall the legend sits with the readouts, so the square can take the stage body's full height.
  const tvSide = Math.floor(Math.min(bodyBox.height, bodyBox.width * 0.6, 900))
  const size = tv
    ? Math.max(180, tvSide)
    : Math.max(200, Math.floor(Math.min(gridBox.width, pageMax)))
  const geo = useMemo(() => geometry(size, extent), [size, extent])

  const sideRef = useRef<HTMLDivElement | null>(null)
  const [canvasOk] = useState(canDraw2d)

  const visible = usePageVisible()
  const onScreen = useOnScreen(stageRef)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState<Speed>(props.fast ? 4 : 1)
  const [phase, setPhase] = useState<'rain' | 'done'>('rain')
  const [landed, setLanded] = useState(0)
  const [ticker, setTicker] = useState<{ n: number; away: number; home: number }[]>([])
  const [announce, setAnnounce] = useState('')
  const instant = reducedMotion || speed === 'instant' || !canvasOk || !hasRaf()
  const done = phase === 'done'
  useAutoScroll(sideRef, tv && done, { reduced: reducedMotion, everyMs: 9_000, itemSelector: ':scope > *' })

  const liveCell = live && source.population ? pooledCell({ away: live.away, home: live.home }, cap) : null
  const liveRef = useRef(liveCell)
  liveRef.current = liveCell

  const sim = useRef({ t: 0, launched: 0, landed: 0, counts: new Int32Array(0), particles: [] as Particle[], flashes: [] as Flash[], rand: mulberry32(1), lastUi: 0, milestone: 0 })

  // ------------------------------------------------------------- drawing --
  const drawCell = useCallback((ctx: CanvasRenderingContext2D, i: number, count: number) => {
    const c = cells[i]
    if (!c || c.home > geo.homeMax || c.away > geo.awayMax) return
    const r = cellRect(geo, c.away, c.home)
    ctx.globalAlpha = 1
    ctx.fillStyle = GROUND
    ctx.fillRect(r.x, r.y, r.w, r.h)
    if (count <= 0) return
    const dim = !isReachable(c, liveRef.current)
    ctx.globalAlpha = dim ? 0.2 : 1
    ctx.fillStyle = cellColor(count, maxC, toneOf(c, tintSide))
    ctx.fillRect(r.x, r.y, r.w, r.h)
    ctx.globalAlpha = 1
  }, [cells, geo, maxC, tintSide])

  const drawBase = useCallback((ctx: CanvasRenderingContext2D) => {
    const dpr = devicePixelRatioCapped()
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'source-over'
    ctx.fillStyle = STAGE_BG
    ctx.fillRect(0, 0, geo.size, geo.size)
    ctx.fillStyle = GROUND
    ctx.fillRect(geo.left, geo.top, geo.plotW, geo.plotH)
    // Faint tie diagonal: the scores where a game would be level.
    ctx.strokeStyle = 'rgba(243,194,87,.18)'
    ctx.lineWidth = 1
    ctx.beginPath()
    const diag = Math.min(geo.homeMax, geo.awayMax)
    const a = centerOf(geo, 0, 0)
    const b = centerOf(geo, diag, diag)
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
    // Axes.
    ctx.fillStyle = '#8ea3bb'
    ctx.font = `${geo.size >= 420 ? 11 : 10}px ui-monospace, SFMono-Regular, Consolas, monospace`
    ctx.textBaseline = 'top'
    ctx.textAlign = 'center'
    for (const v of ticksFor(geo.awayMax, cap)) { const p = centerOf(geo, v, 0); ctx.fillText(label(v), p.x, geo.top + geo.plotH + 4) }
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    for (const v of ticksFor(geo.homeMax, cap)) { const p = centerOf(geo, 0, v); ctx.fillText(label(v), geo.left - 5, p.y) }
    ctx.fillStyle = '#c9d6e3'
    ctx.font = '700 10.5px Inter, "Segoe UI", Arial, sans-serif'
    ctx.textAlign = 'right'
    ctx.textBaseline = 'top'
    ctx.fillText(`${awayTeam} score →`, geo.left + geo.plotW, geo.top + geo.plotH + 15)
    ctx.save()
    ctx.translate(9, geo.top + geo.plotH)
    ctx.rotate(-Math.PI / 2)
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    ctx.fillText(`${homeTeam} score →`, 0, 0)
    ctx.restore()
    // The emitter: every draw leaves from the model.
    const grad = ctx.createLinearGradient(geo.left, 0, geo.left + geo.plotW, 0)
    grad.addColorStop(0, 'rgba(63,216,196,.15)')
    grad.addColorStop(0.5, 'rgba(63,216,196,.6)')
    grad.addColorStop(1, 'rgba(63,216,196,.15)')
    ctx.fillStyle = grad
    ctx.fillRect(geo.left + 40, 6, geo.plotW - 40, 6)
    ctx.fillStyle = '#8fe3d8'
    ctx.font = '800 9.5px Inter, "Segoe UI", Arial, sans-serif'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'middle'
    ctx.fillText('MODEL', geo.left, 9)
  }, [geo, awayTeam, homeTeam, cap, label])

  const sizeCanvases = useCallback(() => {
    const dpr = devicePixelRatioCapped()
    for (const canvas of [gridRef.current, fxRef.current]) {
      if (!canvas) continue
      const px = Math.round(geo.size * dpr)
      if (canvas.width !== px) { canvas.width = px; canvas.height = px }
      canvas.style.width = `${geo.size}px`
      canvas.style.height = `${geo.size}px`
    }
  }, [geo.size])

  const redrawAll = useCallback(() => {
    const ctx = context2d(gridRef.current)
    if (!ctx) return
    sizeCanvases()
    drawBase(ctx)
    const counts = sim.current.counts
    for (let i = 0; i < cells.length; i++) if (counts[i] > 0) drawCell(ctx, i, counts[i])
  }, [sizeCanvases, drawBase, drawCell, cells.length])

  /**
   * The finished grid's glow, on the effects layer only: a soft halo behind the
   * most-drawn cells (≥ 40% of the busiest). Cell colours underneath are
   * untouched, so the grid still reads as the exact tally.
   */
  const drawBloom = useCallback(() => {
    const fx = context2d(fxRef.current)
    if (!fx) return
    const dpr = devicePixelRatioCapped()
    fx.setTransform(dpr, 0, 0, dpr, 0, 0)
    fx.globalCompositeOperation = 'source-over'
    fx.clearRect(0, 0, geo.size, geo.size)
    if (!maxC) return
    fx.globalCompositeOperation = 'lighter'
    const radius = Math.max(6, Math.max(geo.cw, geo.ch) * 2.2)
    for (const c of cells) {
      if (c.count < maxC * 0.4 || c.home > geo.homeMax || c.away > geo.awayMax || !isReachable(c, liveRef.current)) continue
      const p = centerOf(geo, c.away, c.home)
      const [r, g, b] = STAGE_RGB[toneOf(c, tintSide)]
      const strength = 0.1 + 0.16 * (c.count / maxC)
      const grad = fx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius)
      grad.addColorStop(0, `rgba(${r},${g},${b},${strength.toFixed(3)})`)
      grad.addColorStop(1, `rgba(${r},${g},${b},0)`)
      fx.fillStyle = grad
      fx.fillRect(p.x - radius, p.y - radius, radius * 2, radius * 2)
    }
    fx.globalCompositeOperation = 'source-over'
  }, [cells, geo, maxC, tintSide])

  const finishNow = useCallback(() => {
    const s = sim.current
    if (!order) return
    for (let i = 0; i < cells.length; i++) s.counts[i] = cells[i].count
    s.launched = total
    s.landed = total
    s.particles = []
    s.flashes = []
    redrawAll()
    drawBloom()
    setLanded(total)
    setTicker(Array.from({ length: Math.min(SLOW_DRAWS, total) }, (_, k) => ({ n: k + 1, away: cells[order[k]].away, home: cells[order[k]].home })))
    setPhase('done')
  }, [order, cells, total, redrawAll, drawBloom])

  // A new replay (game, seed) starts from an empty grid.
  useEffect(() => {
    sim.current = {
      t: 0, launched: 0, landed: 0, counts: new Int32Array(cells.length), particles: [], flashes: [],
      rand: mulberry32(seed ^ 0x9e3779b9), lastUi: 0, milestone: 0,
    }
    setLanded(0)
    setTicker([])
    setPhase('rain')
    setAnnounce('')
    if (canvasOk) {
      redrawAll()
      const fx = context2d(fxRef.current)
      if (fx) { fx.setTransform(1, 0, 0, 1, 0, 0); fx.clearRect(0, 0, fx.canvas.width, fx.canvas.height) }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the replay itself changes
  }, [replay])

  // Instant, reduced motion, or no canvas: the finished tally in one frame.
  useEffect(() => {
    if (instant && phase === 'rain' && order) finishNow()
  }, [instant, phase, order, finishNow])

  // Resizes and live-score changes redraw from the exact current counts (and the glow, once finished).
  const liveKey = liveCell ? `${liveCell.away}-${liveCell.home}` : ''
  useEffect(() => {
    if (!canvasOk) return
    redrawAll()
    if (phase === 'done') drawBloom()
  }, [canvasOk, redrawAll, drawBloom, liveKey, phase])

  const running = playing && visible && onScreen && phase === 'rain' && !instant && Boolean(order)
  const speedFactor = speed === 4 ? 4 : 1

  useEffect(() => {
    if (!running || !order) return
    const grid = context2d(gridRef.current)
    const fx = context2d(fxRef.current)
    if (!grid || !fx) return
    let raf = 0
    let last: number | null = null
    const s = sim.current
    const pendingTicker: { n: number; away: number; home: number }[] = []
    const land = (idx: number) => {
      s.counts[idx] += 1
      s.landed += 1
      drawCell(grid, idx, s.counts[idx])
      s.flashes.push({ idx, t: s.t })
      if (s.flashes.length > MAX_FLASHES) s.flashes.splice(0, s.flashes.length - MAX_FLASHES)
      if (s.landed <= SLOW_DRAWS) pendingTicker.push({ n: s.landed, away: cells[idx].away, home: cells[idx].home })
    }
    const dpr = devicePixelRatioCapped()
    const head = geo.size >= 420 ? 5 : 4
    const pathAt = (p: Particle, u: number) => {
      const e = 1 - Math.pow(1 - Math.max(0, Math.min(1, u)), 3)
      const c = cells[p.idx]
      const to = centerOf(geo, c.away, c.home)
      const fromX = geo.left + 40 + p.sx * (geo.plotW - 40)
      const fromY = 10
      const cx = (fromX + to.x) / 2
      const cy = Math.min(fromY, to.y) - 24
      return {
        x: (1 - e) * (1 - e) * fromX + 2 * (1 - e) * e * cx + e * e * to.x,
        y: (1 - e) * (1 - e) * fromY + 2 * (1 - e) * e * cy + e * e * to.y,
      }
    }
    const frame = (ts: number) => {
      // Capped, so a frame after a stall (or on a slow TV) never jumps the replay by more than a quarter second.
      const dt = last == null ? 16 : Math.min(250, ts - last)
      last = ts
      s.t += (dt / 1000) * speedFactor
      const target = launchedBy(s.t, total)
      while (s.launched < target) {
        const idx = order[s.launched]
        s.launched += 1
        const c = cells[idx]
        const inWindow = c.home <= geo.homeMax && c.away <= geo.awayMax
        if (inWindow && s.particles.length < MAX_PARTICLES) s.particles.push({ idx, start: s.t, dur: 0.45 + s.rand() * 0.25, sx: s.rand() })
        else land(idx)
      }
      let w = 0
      for (const p of s.particles) {
        if (s.t - p.start >= p.dur) land(p.idx)
        else s.particles[w++] = p
      }
      s.particles.length = w

      fx.setTransform(dpr, 0, 0, dpr, 0, 0)
      fx.globalCompositeOperation = 'source-over'
      fx.clearRect(0, 0, geo.size, geo.size)
      fx.globalCompositeOperation = 'lighter'
      // Landing flashes: a cell lights up for a quarter second when a draw lands in it.
      let f = 0
      for (const fl of s.flashes) {
        const age = s.t - fl.t
        if (age > 0.25) continue
        s.flashes[f++] = fl
        const c = cells[fl.idx]
        const r = cellRect(geo, c.away, c.home)
        fx.globalAlpha = 0.55 * (1 - age / 0.25)
        fx.fillStyle = '#e8fffb'
        fx.fillRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2)
      }
      s.flashes.length = f
      // Comets: a bright head with a short trail on an ease-out arc from the emitter into the cell.
      fx.lineCap = 'round'
      for (const p of s.particles) {
        const u = (s.t - p.start) / p.dur
        const now = pathAt(p, u)
        const tail = pathAt(p, u - 0.16)
        fx.globalAlpha = 0.5
        fx.strokeStyle = '#3fd8c4'
        fx.lineWidth = head - 1.5
        fx.beginPath(); fx.moveTo(tail.x, tail.y); fx.lineTo(now.x, now.y); fx.stroke()
        fx.globalAlpha = 1
        fx.fillStyle = '#e8fffb'
        fx.fillRect(now.x - head / 2, now.y - head / 2, head, head)
      }
      fx.globalAlpha = 1
      fx.globalCompositeOperation = 'source-over'

      if (ts - s.lastUi > 100 || s.landed >= total) {
        s.lastUi = ts
        setLanded(s.landed)
        if (pendingTicker.length) { const add = pendingTicker.splice(0); setTicker((prev) => [...prev, ...add].slice(-SLOW_DRAWS)) }
      }
      if (s.landed >= total && s.particles.length === 0) {
        s.flashes = []
        drawBloom()
        setLanded(total)
        setPhase('done')
        return
      }
      raf = window.requestAnimationFrame(frame)
    }
    raf = window.requestAnimationFrame(frame)
    return () => window.cancelAnimationFrame(raf)
  }, [running, order, cells, total, geo, speedFactor, drawCell, drawBloom])

  // Milestones for assistive technology: 25 / 50 / 75 / 100 % and the final, once each.
  useEffect(() => {
    if (!total) return
    const quarter = Math.floor((landed / total) * 4)
    if (quarter > sim.current.milestone) {
      sim.current.milestone = quarter
      setAnnounce(`${n0(landed)} of ${n0(total)} draws landed${quarter === 4 ? '. The grid now equals the model’s tally.' : '.'}`)
    }
  }, [landed, total])

  // ------------------------------------------------------------- facts --
  const realFinal = source.final
    ? { away: source.final.away, home: source.final.home }
    : source.sport !== 'NHL' && props.observedFinal ? { away: props.observedFinal.away, home: props.observedFinal.home } : null
  const strike = source.strikeCell ?? (source.sport !== 'NHL' && realFinal ? pooledCell(realFinal, cap) : null)
  const strikeIndex = strike ? cellIndexOf(cells, strike.home, strike.away) : -1
  const strikeCount = strikeIndex >= 0 ? cells[strikeIndex].count : 0
  const strikeRank = strikeIndex >= 0 && pop?.mode === 'exact' ? rankOf(cells, strikeIndex) : null
  const pooledStrike = Boolean(strike && cap != null && (strike.away > cap || strike.home > cap))
  const top = useMemo(() => topScores(cells, 3), [cells])
  const top20 = useMemo(() => topScores(cells, 20), [cells])
  const reachable = liveCell ? reachableCount(cells, liveCell) : null
  const sampled = pop?.mode === 'sampled'
  const regulationWord = sampled ? ' in regulation' : ''
  const observedOnly = !source.strikeCell && props.observedFinal ? props.observedFinal : null

  const summary = `Score grid for ${awayTeam} at ${homeTeam}: ${sampled ? `${n0(total)} draws sampled from the model's derived regulation distribution` : `the model's ${n0(total)} stored pregame draws`}. `
    + (top[0] ? `Most drawn score ${awayTeam} ${label(top[0].away)}, ${homeTeam} ${label(top[0].home)} (${n0(top[0].count)} draws). ` : '')
    + `${tintTeam} won${regulationWord} in ${n0(totalWins)} of them (${pct(share, 1)}).`

  const capNote = cap != null ? ` Scores above ${cap} runs are pooled by the model into one "${cap + 1}+" cell.` : ''
  const caption = sampled && pop?.nhl
    ? `Replaying ${n0(total)} draws sampled from the model's stored distribution: a regulation-only grid derived from its stored expected goals (${pop.nhl.lambdaAway.toFixed(2)} for ${awayTeam} vs ${pop.nhl.lambdaHome.toFixed(2)} for ${homeTeam}). ${pct(pop.nhl.regulationTie, 1)} of the model's chances end tied after 60 minutes and go to overtime or a shootout, which it does not model as scores. The win % on the bar includes overtime. Scores above ${extent.homeMax} goals (${(pop.nhl.outside * 100).toFixed(3)}% of the grid) are counted but not drawn.`
    : `Replaying the model's ${n0(total)} stored pregame draws in a shuffled order. AISP stores how many draws landed on each score, not the order. The order is ours; the finished grid is exactly the model's.${capNote}`

  const canFullscreen = typeof document !== 'undefined' && Boolean(document.fullscreenEnabled) && typeof HTMLElement !== 'undefined' && 'requestFullscreen' in HTMLElement.prototype
  // The reel-and-stage column goes fullscreen, never the stage itself: a director or settlement cut remounts the
  // stage, and a removed fullscreen element drops the browser out of fullscreen.
  const fullscreenStage = () => {
    const node = (stageRef.current?.closest('.gn-act1') ?? stageRef.current?.closest('.gn-storm')) as HTMLElement | null
    if (!node) return
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {})
    else void node.requestFullscreen?.().catch(() => {})
  }

  if (!pop) {
    return (
      <div className="gn-storm__body">
        <div className="gn-storm__empty">
          <strong>{source.sport === 'NHL' ? 'Regulation grid unavailable.' : 'No stored draws to replay.'}</strong>
          <p>{source.unavailableReason}</p>
        </div>
        <div className="gn-storm__side">
          <SplitBar source={source} tintSide={tintSide} gap={gap} bet={bet} selection={props.selection} done decisionAt={props.decisionAt} />
          <ForecastLine source={source} />
        </div>
      </div>
    )
  }

  // Always shown with the grid. On the TV wall they run the full width under the stage so the square keeps its height.
  const captions = (
    <>
      <p className="gn-caption">{caption}</p>
      {liveCell && (
        <p className="gn-caption gn-caption--live">
          Pregame model only. AISP has no in-game model; this filters the pregame draws by the score so far and ignores the clock. It is not a live win probability.
        </p>
      )}
    </>
  )

  const legend = <Legend tintTeam={tintTeam} otherTeam={otherTeam} picked={Boolean(bet?.side)} sport={source.sport} />
  const table = (reducedMotion || !canvasOk) ? (
    <ChartTable caption={`${awayTeam} @ ${homeTeam}: the 20 most-drawn ${sampled ? 'regulation ' : ''}scores of ${n0(total)} ${sampled ? 'sampled' : 'stored'} draws`}>
      <thead><tr><th scope="col">{awayTeam}</th><th scope="col">{homeTeam}</th><th scope="col">Draws</th><th scope="col">Share</th></tr></thead>
      <tbody>
        {top20.map((c) => (
          <tr key={`${c.away}-${c.home}`}><td>{label(c.away)}</td><td>{label(c.home)}</td><td>{n0(c.count)}</td><td>{pct(c.count / Math.max(1, total), 2)}</td></tr>
        ))}
      </tbody>
    </ChartTable>
  ) : null

  const finalCards = done && strike ? (
    <FinalCards strike={strike} realFinal={realFinal} pooled={pooledStrike} count={strikeCount} rank={strikeRank} cellsCount={cells.length} total={total}
      team={tintTeam} wins={totalWins} share={share} bet={bet} source={source} sampled={sampled} observed={observedOnly} />
  ) : null

  return (
    <>
    <div className="gn-storm__body" ref={bodyBox.ref} style={tv ? { gridTemplateColumns: `${size}px minmax(0, 1fr)` } : undefined}>
      <div className="gn-storm__gridcol">
        <div className="gn-storm__canvaswrap" ref={gridBox.ref}>
          <div className="gn-storm__canvas" ref={stageRef} style={canvasOk ? { width: size, height: size } : undefined}>
            {canvasOk ? (
              <>
                <canvas ref={gridRef} className="gn-storm__grid" role="img" aria-label={summary} />
                <canvas ref={fxRef} className="gn-storm__fx" aria-hidden="true" />
                <StrikeOverlay geo={geo} strike={done && strike ? strike : null} live={liveCell} reducedMotion={reducedMotion} />
                {done && strike && (
                  <StrikeCallout geo={geo} strike={strike} realFinal={realFinal} away={awayTeam} home={homeTeam} count={strikeCount}
                    total={total} rank={strikeRank} cellsCount={cells.length} sampled={sampled} pooled={pooledStrike}
                    regulation={source.sport === 'NHL'} observed={Boolean(observedOnly)} reducedMotion={reducedMotion} label={label} />
                )}
              </>
            ) : (
              <p className="gn-storm__nocanvas" role="img" aria-label={summary}>{summary}</p>
            )}
          </div>
        </div>
        {!tv && legend}
        <div className="gn-storm__controls" role="group" aria-label="Replay controls">
          <button type="button" className="gn-btn" onClick={() => setPlaying((p) => !p)} disabled={done || instant} aria-label={playing ? 'Pause the replay' : 'Play the replay'}>
            {playing && !done ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}<span>{playing && !done ? 'Pause' : 'Play'}</span>
          </button>
          <span className="gn-seg" role="group" aria-label="Replay speed">
            {([1, 4, 'instant'] as Speed[]).map((s) => (
              <button key={String(s)} type="button" className={`gn-seg__btn ${speed === s ? 'is-on' : ''}`} aria-pressed={speed === s} onClick={() => setSpeed(s)}>
                {s === 'instant' ? 'Instant' : `${s}×`}
              </button>
            ))}
          </span>
          <button type="button" className="gn-btn" onClick={() => { setPlaying(true); setSeed((x) => (x * 1103515245 + 12345) >>> 0) }} aria-label="Replay with a new shuffle">
            <Shuffle size={15} aria-hidden="true" /><span>Replay</span>
          </button>
          {canFullscreen && (
            <button type="button" className="gn-btn" onClick={fullscreenStage} aria-label="Fullscreen stage">
              <Maximize size={15} aria-hidden="true" /><span>Fullscreen stage</span>
            </button>
          )}
        </div>
        {!tv && captions}
        {!tv && table}
      </div>

      <div className={`gn-storm__side ${done ? 'is-done' : ''}`} ref={sideRef}>
        {/* Once the rain is over the payoff leads: the real final against the model, then the market. */}
        {finalCards}
        {!done && !reducedMotion && <Ticker lines={ticker} away={awayTeam} home={homeTeam} total={total} sampled={sampled} label={label} />}
        <SplitBar source={source} tintSide={tintSide} gap={gap} bet={bet} selection={props.selection} done={done} decisionAt={props.decisionAt} />
        {tv && legend}
        {liveCell && reachable != null && (
          <p className="gn-live-count" role="status">
            Finals still reachable from {awayTeam} {live!.away} – {live!.home} {homeTeam}: <b>{n0(reachable)}</b> of {n0(total)} pregame draws
            {props.live?.detail ? <small> · {props.live.detail}</small> : null}
          </p>
        )}
        <Meter landed={landed} total={total} prefixWins={prefixWins} p={share} team={tintTeam} regulation={sampled} done={done} />
        <div className="gn-facts">
          <h3>Most drawn {sampled ? 'regulation ' : ''}scores</h3>
          <ol className="gn-top">
            {top.map((c) => <li key={`${c.away}-${c.home}`}><b>{awayTeam} {label(c.away)} – {label(c.home)} {homeTeam}</b><span>{n0(c.count)} draws</span></li>)}
          </ol>
        </div>
        {done && !strike && source.sport === 'NHL' && props.observedFinal && (
          <p className="gn-caption">Observed final {awayTeam} {props.observedFinal.away} – {props.observedFinal.home} {homeTeam}. The regulation score is not in AISP yet, so no cell is marked.</p>
        )}
        <ForecastLine source={source} />
        {tv && table}
      </div>
      <p className="gn-sr" aria-live="polite">{announce}</p>
    </div>
    {tv && <div className="gn-storm__captions">{captions}</div>}
    </>
  )
}

export const ScoreStorm = memo(ScoreStormImpl)

// ------------------------------------------------------------ overlays --

function StrikeOverlay({ geo, strike, live, reducedMotion }: { geo: Geo; strike: { away: number; home: number } | null; live: { away: number; home: number } | null; reducedMotion: boolean }) {
  const inWindow = (c: { away: number; home: number }) => c.away <= geo.awayMax && c.home <= geo.homeMax && c.away >= 0 && c.home >= 0
  const s = strike && inWindow(strike) ? centerOf(geo, strike.away, strike.home) : null
  const l = live && inWindow(live) ? centerOf(geo, live.away, live.home) : null
  if (!s && !l) return null
  const r = Math.max(10, Math.min(geo.cw, geo.ch) * 2.6)
  return (
    <svg className={`gn-strike ${reducedMotion ? 'is-static' : ''}`} width={geo.size} height={geo.size} viewBox={`0 0 ${geo.size} ${geo.size}`} aria-hidden="true">
      {s && (
        <g className="gn-strike__final">
          <line className="gn-strike__glow gn-strike__beam--x" x1={geo.left} y1={s.y} x2={s.x} y2={s.y} />
          <line className="gn-strike__glow gn-strike__beam--y" x1={s.x} y1={geo.top + geo.plotH} x2={s.x} y2={s.y} />
          <line className="gn-strike__beam gn-strike__beam--x" x1={geo.left} y1={s.y} x2={s.x} y2={s.y} />
          <line className="gn-strike__beam gn-strike__beam--y" x1={s.x} y1={geo.top + geo.plotH} x2={s.x} y2={s.y} />
          <rect className="gn-strike__cell" x={s.x - geo.cw / 2 - 2} y={s.y - geo.ch / 2 - 2} width={geo.cw + 4} height={geo.ch + 4} />
          {[0, 1, 2].map((k) => <circle key={k} className="gn-strike__ring" style={{ animationDelay: `${0.7 + k * 0.4}s` }} cx={s.x} cy={s.y} r={r} />)}
        </g>
      )}
      {l && (
        <g className="gn-strike__live">
          <circle className="gn-strike__livering" cx={l.x} cy={l.y} r={r} />
          <rect className="gn-strike__cell gn-strike__cell--live" x={l.x - geo.cw / 2 - 1.5} y={l.y - geo.ch / 2 - 1.5} width={geo.cw + 3} height={geo.ch + 3} />
        </g>
      )}
    </svg>
  )
}

/** The payoff written on the grid itself, beside the cell the crosshairs hit. */
function StrikeCallout({ geo, strike, realFinal, away, home, count, total, rank, cellsCount, sampled, pooled, regulation, observed, reducedMotion, label }: {
  geo: Geo; strike: { away: number; home: number }; realFinal: { away: number; home: number } | null; away: string; home: string
  count: number; total: number; rank: number | null; cellsCount: number; sampled: boolean; pooled: boolean; regulation: boolean
  observed: boolean; reducedMotion: boolean; label: (v: number) => string
}) {
  if (strike.away > geo.awayMax || strike.home > geo.homeMax) return null
  const c = centerOf(geo, strike.away, strike.home)
  const right = c.x < geo.size * 0.55
  const below = c.y < geo.size * 0.32
  const shown = !regulation && realFinal ? realFinal : strike
  const kicker = regulation ? 'REGULATION' : observed ? 'OBSERVED FINAL' : 'FINAL'
  const style: React.CSSProperties = {
    left: right ? c.x + Math.max(14, geo.cw * 1.4) : undefined,
    right: right ? undefined : geo.size - c.x + Math.max(14, geo.cw * 1.4),
    top: below ? c.y + Math.max(10, geo.ch) : undefined,
    bottom: below ? undefined : geo.size - c.y + Math.max(10, geo.ch),
  }
  return (
    <div className={`gn-callout ${right ? 'is-right' : 'is-left'} ${reducedMotion ? 'is-static' : ''} ${geo.size < 300 ? 'is-small' : ''}`} style={style} aria-hidden="true">
      <span className="gn-callout__kicker">{kicker}</span>
      <b className="gn-callout__score">{away} {shown.away} – {shown.home} {home}</b>
      <span className="gn-callout__line">
        {count > 0
          ? <>{sampled ? 'sampled draws' : 'the model put'} <b>{n0(count)}</b> of {n0(total)}{sampled ? '' : ' draws'} {pooled ? `in the pooled ${label(strike.away)}–${label(strike.home)} cell` : 'here'}</>
          : <>the model never drew this score</>}
      </span>
      {rank != null && <span className="gn-callout__rank">rank {n0(rank)} of {n0(cellsCount)} scores</span>}
    </div>
  )
}

function Legend({ tintTeam, otherTeam, picked, sport }: { tintTeam: string; otherTeam: string; picked: boolean; sport: string }) {
  return (
    <ul className="gn-legend" aria-label="Grid colours">
      <li><i className="gn-sw gn-sw--pick" />{tintTeam} wins{picked ? ' (our pick)' : ' (model favourite)'}</li>
      <li><i className="gn-sw gn-sw--fade" />{otherTeam} wins</li>
      <li><i className="gn-sw gn-sw--tie" />{sport === 'NHL' ? 'Tied after regulation (overtime)' : sport === 'NFL' ? 'Tie (a push)' : 'Tie'}</li>
      <li className="gn-legend__scale">brighter = more draws (log scale)</li>
    </ul>
  )
}

const Ticker = memo(function Ticker({ lines, away, home, total, sampled, label }: {
  lines: { n: number; away: number; home: number }[]; away: string; home: string; total: number; sampled: boolean; label: (v: number) => string
}) {
  const shown = lines.slice(-6).reverse()
  return (
    <div className="gn-ticker" aria-hidden="true">
      <h3>First {SLOW_DRAWS} {sampled ? 'sampled' : 'simulated'} draws <small>(not play-by-play)</small></h3>
      <ol>
        {shown.map((l) => (
          <li key={l.n}><span className="gn-ticker__n">#{l.n}<small>/{n0(total)}</small></span> {away} {label(l.away)} – {label(l.home)} {home} <em>sim</em></li>
        ))}
        {!shown.length && <li className="gn-ticker__wait">The first draw is on its way…</li>}
      </ol>
    </div>
  )
})

/** The share of draws so far won by the tinted side, inside the ±1.96·√(p(1−p)/n) funnel. */
const Meter = memo(function Meter({ landed, total, prefixWins, p, team, regulation, done }: {
  landed: number; total: number; prefixWins: Int32Array; p: number; team: string; regulation: boolean; done: boolean
}) {
  const W = 300, H = 92, padL = 30, padR = 6, padT = 6, padB = 16
  const pw = W - padL - padR, ph = H - padT - padB
  const lx = (n: number) => padL + (total > 1 ? (Math.log(Math.max(1, n)) / Math.log(total)) * pw : 0)
  const ly = (v: number) => padT + (1 - Math.max(0, Math.min(1, v))) * ph
  const pts = useMemo(() => {
    const out: number[] = []
    for (let k = 0; k <= 60; k++) out.push(Math.max(1, Math.round(Math.pow(Math.max(2, total), k / 60))))
    return [...new Set(out)].filter((n) => n <= total)
  }, [total])
  const band = pts.map((n) => [lx(n), ly(p + funnelHalfWidth(p, n)), ly(p - funnelHalfWidth(p, n))] as const)
  const upper = band.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const lower = [...band].reverse().map(([x, , y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const run = pts.filter((n) => n <= landed).map((n) => `${lx(n).toFixed(1)},${ly(prefixWins[n] / n).toFixed(1)}`)
  if (landed > 0 && !pts.includes(landed)) run.push(`${lx(landed).toFixed(1)},${ly(prefixWins[landed] / landed).toFixed(1)}`)
  const current = landed > 0 ? prefixWins[landed] / landed : null
  return (
    <div className="gn-meter">
      <h3>{team} won{regulation ? ' in regulation' : ''}{done ? '' : ' so far'}</h3>
      <p className="gn-meter__read">
        {done
          ? <><b>{pct(p, 1)}</b> of all {n0(total)} draws</>
          : <><b>{current == null ? '—' : pct(current, 1)}</b> of {n0(landed)} draws <span>→ {pct(p, 1)} of all {n0(total)}</span></>}
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} className="gn-meter__svg" role="img" aria-label={`Running share of draws won by ${team}, converging on ${pct(p, 1)}`}>
        <polygon className="gn-meter__band" points={`${upper} ${lower}`} />
        <line className="gn-meter__p" x1={padL} x2={W - padR} y1={ly(p)} y2={ly(p)} />
        {run.length > 1 && <polyline className="gn-meter__line" points={run.join(' ')} />}
        <text x={padL - 4} y={ly(1) + 3} className="gn-meter__tick" textAnchor="end">100%</text>
        <text x={padL - 4} y={ly(0) + 3} className="gn-meter__tick" textAnchor="end">0%</text>
        <text x={padL} y={H - 3} className="gn-meter__tick">1 draw</text>
        <text x={W - padR} y={H - 3} className="gn-meter__tick" textAnchor="end">{n0(total)}</text>
      </svg>
      <p className="gn-meter__cap">That wobble is luck: the band is where 95% of runs this long land.</p>
    </div>
  )
})

/** The stored win split, the model's two-way number for the decision's side, and the market's same-book number dropped beside it. */
function SplitBar({ source, tintSide, gap, bet, selection, done, decisionAt }: {
  source: StormSource; tintSide: Side; gap: GapReading | null; bet: HeldBet | null; selection: string | null; done: boolean; decisionAt: string | null
}) {
  const { away, tie, home } = source.split
  const awayTeam = source.away.abbr
  const homeTeam = source.home.abbr
  const sum = away + tie + home || 1
  // The gap is on the decision's selected side; for a held bet that is our pick.
  const gapSide: Side | null = bet?.side ?? null
  const gapTeam = gapSide === 'HOME' ? homeTeam : gapSide === 'AWAY' ? awayTeam : (bet?.selection || selection || 'the decision’s side').replace(/\s+ML$/i, '')
  // On one game, a gap of more than 10 points either way is the model warning.
  const warning = gap ? isDisplayWarning(gap.gap) : false
  const freshness = gap?.freshAtDecision === false ? ' · stale at decision' : gap?.freshAtDecision == null ? ' · freshness at decision not recorded' : ''
  const basis = basisCaption(gap)
  return (
    <div className="gn-split">
      <h3>Model split <small>({source.splitBasis})</small></h3>
      <div className="gn-split__bar" role="img" aria-label={`${awayTeam} ${pct(away, 1)}${tie > 0 ? `, tie ${pct(tie, 1)}` : ''}, ${homeTeam} ${pct(home, 1)}`}>
        <span className={`gn-split__seg gn-split__seg--${tintSide === 'AWAY' ? 'pick' : 'fade'}`} style={{ width: `${(away / sum) * 100}%` }} />
        {tie > 0 && <span className="gn-split__seg gn-split__seg--tie" style={{ width: `${(tie / sum) * 100}%` }} />}
        <span className={`gn-split__seg gn-split__seg--${tintSide === 'HOME' ? 'pick' : 'fade'}`} style={{ width: `${(home / sum) * 100}%` }} />
      </div>
      <p className="gn-split__labels"><span>{awayTeam} {pct(away, 1)}</span>{tie > 0 && <span>tie {pct(tie, 1)}</span>}<span>{homeTeam} {pct(home, 1)}</span></p>
      {source.regulation && (
        <p className="gn-split__reg">Regulation: {awayTeam} {pct(source.regulation.away, 1)} · tied {pct(source.regulation.tie, 1)} · {homeTeam} {pct(source.regulation.home, 1)}</p>
      )}
      {gap ? (
        <div className={`gn-market ${done ? 'is-dropped' : ''}`}>
          <p className="gn-market__title">{gapTeam}: model vs market <small>({gap.basis === 'NON_TIE_DERIVED' || gap.sport !== 'NFL' ? 'two-way' : 'raw model'}, at decision)</small></p>
          <div className="gn-market__track" role="img" aria-label={`Model ${pct(gap.model, 1)} against the market's ${pct(gap.noVig, 1)} for ${gapTeam}`}>
            <span className="gn-market__fill" style={{ width: `${gap.model * 100}%` }} />
            <span className="gn-market__mark gn-market__mark--model" style={{ left: `${gap.model * 100}%` }}><b>MODEL {pct(gap.model, 1)}</b></span>
            <span className="gn-market__mark gn-market__mark--market" style={{ left: `${gap.noVig * 100}%` }}><b>MARKET {pct(gap.noVig, 1)}</b></span>
          </div>
          <p className="gn-market__gap">
            <span className={`gn-gap-pill ${warning ? 'is-warning' : ''}`}>{gapText(gap.gap)}{warning ? ' · MODEL WARNING' : ''}</span>
            {warning && <span className="gn-market__warn">Model warning: the model is {Math.abs(gap.gap).toFixed(1)} pts from the market. A gap this big is more often model error than edge.</span>}
          </p>
          <p className="gn-caption">
            Market = {gap.sportsbook ?? 'the book'}&apos;s two prices when AISP decided{decisionAt ? ` (${etShort(Date.parse(decisionAt))} ET)` : ''}, vig removed{freshness}.
            {basis ? ` ${basis}` : ''}
          </p>
        </div>
      ) : (
        <p className="gn-caption">No decision-time price for this game.</p>
      )}
    </div>
  )
}

function ForecastLine({ source }: { source: StormSource }) {
  const made = source.forecastAt ? Date.parse(source.forecastAt) : NaN
  const start = source.startsAt ? Date.parse(source.startsAt) : NaN
  const kick = source.sport === 'NHL' ? 'puck drop' : source.sport === 'MLB' ? 'first pitch' : 'kickoff'
  const madeText = Number.isFinite(made)
    ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(made) + ' ET'
    : 'at an unrecorded time'
  const lead = source.hoursBeforeStart != null && Number.isFinite(source.hoursBeforeStart)
    ? source.hoursBeforeStart >= 48 ? `${Math.round(source.hoursBeforeStart / 24)} days before ${kick}` : `${source.hoursBeforeStart.toFixed(1)} h before ${kick}`
    : null
  const days = Number.isFinite(made) && Number.isFinite(start) ? Math.round((start - made) / 86_400_000) : 0
  return (
    <p className="gn-forecast">
      Forecast made {madeText} · {source.modelLabel}{lead ? ` · ${lead}` : ''}
      {source.development ? ' · development model' : ''}
      {source.prospective && days >= 1 && Number.isFinite(start)
        ? ` · forecast made ${etMonthDay(made)} for a ${etMonthDay(start)} game (prospective)` : ''}
    </p>
  )
}

const etMonthDay = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: '2-digit', day: '2-digit' }).format(ms).replace('/', '-')

function FinalCards({ strike, realFinal, pooled, count, rank, cellsCount, total, team, wins, share, bet, source, sampled, observed }: {
  strike: { away: number; home: number }; realFinal: { away: number; home: number } | null; pooled: boolean
  count: number; rank: number | null; cellsCount: number; total: number
  team: string; wins: number; share: number; bet: HeldBet | null; source: StormSource; sampled: boolean
  observed: { away: number; home: number; source: string | null } | null
}) {
  const awayTeam = source.away.abbr
  const homeTeam = source.home.abbr
  const be = bet ? breakEven(bet.american) : null
  const shown = realFinal ?? strike
  const label = source.sport === 'NHL' && source.final && (source.final.away !== strike.away || source.final.home !== strike.home)
    ? `Regulation ended ${awayTeam} ${strike.away} – ${strike.home} ${homeTeam}; ${source.final.label} ${source.final.away}–${source.final.home}`
    : `${observed ? 'Observed final' : 'Final'} ${awayTeam} ${shown.away} – ${shown.home} ${homeTeam}`
  const settled = bet && !bet.open
  const cap = source.cap
  return (
    <div className="gn-final" role="group" aria-label="The real final against the model">
      <p className="gn-final__score">{label}</p>
      <ul>
        <li><small>{pooled && cap != null ? `Draws in the pooled ${cap + 1}+ cell` : `Draws on this exact ${sampled ? 'regulation ' : ''}score`}</small><b>{n0(count)} of {n0(total)}</b>
          {rank != null ? <em>{ordinal(rank)} most likely of {n0(cellsCount)} scores</em> : count === 0 && !pooled ? <em>the model never drew it</em> : null}</li>
        <li><small>Draws where {team} won{sampled ? ' in regulation' : ''}</small><b>{n0(wins)}</b><em>{pct(share, 1)}</em></li>
        {be != null && <li><small>Price needed to break even</small><b>{pct(be, 1)}</b><em>at {fmtAmerican(bet!.american)}</em></li>}
        {bet && (
          settled
            ? <li className={`gn-final__result gn-final__result--${bet.result.toLowerCase()}`}><small>Result</small><b>{bet.result === 'WIN' ? 'W' : bet.result === 'LOSS' ? 'L' : bet.result === 'PUSH' ? 'P' : bet.result} {usd(bet.profit, true)}</b>
              <em>{bet.settledAt ? `settled ${etShort(Date.parse(bet.settledAt))} ET` : 'settled'}</em></li>
            : <li className="gn-final__result gn-final__result--open"><small>Result</small><b>Observed final{observed?.source ? ` (${observed.source.startsWith('ESPN') ? 'ESPN' : observed.source})` : ''}</b><em>not yet settled</em></li>
        )}
      </ul>
    </div>
  )
}
