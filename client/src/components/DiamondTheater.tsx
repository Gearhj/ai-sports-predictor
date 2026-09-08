/**
 * The Diamond Theater — 20,000 completed outcomes shown together.
 *
 * INTERACTION MODEL
 * -----------------
 * The simulation is already finished and verified before this component
 * renders. The field therefore shows the whole population at once; the optional
 * build animation is a ~1.7s reveal of an existing result, not a computation
 * and not a 20,000-step playlist. Nothing here advances one draw at a time.
 *
 * HONESTY BOUNDARY
 * ----------------
 * Gen 1 draws final scores directly and models no plate appearances, base-out
 * states, batters or pitchers. A trace is an encoding of ONE FINAL SCORE — not
 * a ball flight, a hit or a baserunner path — and the legend says so. Bases are
 * never populated with invented runners.
 */
import { Info, RotateCcw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BaseballFieldSvg } from './BaseballField'
import {
  FIELD,
  FILTER_OVERLAP_NOTE,
  traceFilters,
  buildDurationMs,
  buildTraceField,
  filterThresholds,
  matchesFilter,
  subsetStats,
  type Trace,
  type TraceFilter,
} from '../services/diamondTraces'
import type { GameSimulationData } from '../types/simulation'

const SPEEDS = [1, 2] as const
const HOME_COLOUR = [126, 212, 205] as const   // teal — home team
const AWAY_COLOUR = [232, 168, 96] as const    // amber — away team

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false
  ))
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(query.matches)
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])
  return reduced
}

function rgba(colour: readonly number[], alpha: number): string {
  return `rgba(${colour[0]}, ${colour[1]}, ${colour[2]}, ${alpha})`
}

/**
 * Traces per batched Path2D.
 *
 * This number is the difference between a picture and a distribution. A single
 * Path2D is composited ONCE, so traces inside the same path do not accumulate
 * alpha and no density forms — measured max alpha was 19/255 with everything in
 * one path. Stroking in small batches lets repeated scores build up, which is
 * what makes frequency visible, while keeping the call count in the hundreds
 * rather than at 20,000.
 */
const BATCH = 40

/**
 * Strokes a slice of traces in small per-team batches so that overlapping
 * outcomes accumulate opacity and frequent exact scores form density bands.
 */
function strokeSlice(
  ctx: CanvasRenderingContext2D,
  traces: Trace[],
  from: number,
  to: number,
  scale: number,
  alpha: number,
  predicate?: (trace: Trace) => boolean,
) {
  ctx.lineWidth = Math.max(0.6, scale * 0.75)
  ctx.lineCap = 'round'
  const hx = FIELD.home.x * scale
  const hy = FIELD.home.y * scale

  let home = new Path2D()
  let away = new Path2D()
  let inBatch = 0

  const flush = () => {
    if (!inBatch) return
    ctx.strokeStyle = rgba(AWAY_COLOUR, alpha)
    ctx.stroke(away)
    ctx.strokeStyle = rgba(HOME_COLOUR, alpha)
    ctx.stroke(home)
    home = new Path2D()
    away = new Path2D()
    inBatch = 0
  }

  for (let i = from; i < to; i++) {
    const t = traces[i]
    if (predicate && !predicate(t)) continue
    const path = t.winner === 'home' ? home : away
    path.moveTo(hx, hy)
    path.quadraticCurveTo(t.cx * scale, t.cy * scale, t.x * scale, t.y * scale)
    if (++inBatch >= BATCH) flush()
  }
  flush()
}

export function DiamondTheater({
  simulation, homeTeam, awayTeam,
}: {
  simulation: GameSimulationData
  homeTeam: string
  awayTeam: string
}) {
  const reducedMotion = usePrefersReducedMotion()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const frameRef = useRef<number | null>(null)
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1)
  const [filter, setFilter] = useState<TraceFilter>('all')
  const [built, setBuilt] = useState(0)
  const [replayToken, setReplayToken] = useState(0)
  const [filterMs, setFilterMs] = useState<number | null>(null)

  const joint = simulation.joint_score_distribution
  const field = useMemo(
    () => (joint
      ? buildTraceField(joint.probabilities, joint.counts, 30_000, joint.extra_inning_counts)
      : null),
    [joint],
  )
  const thresholds = useMemo(
    () => (field ? filterThresholds(field.traces) : null),
    [field],
  )
  const stats = useMemo(
    () => (field && thresholds ? subsetStats(field.traces, filter, thresholds) : null),
    [field, thresholds, filter],
  )
  // Counted once per population, not once per render. Recomputing all seven
  // subsets inside the render body cost 140,000 iterations on every animation
  // frame and pushed the 1x build past four seconds.
  const filterCounts = useMemo(() => {
    if (!field || !thresholds) return {} as Record<string, number>
    const result: Record<string, number> = {}
    for (const definition of traceFilters(homeTeam, awayTeam, thresholds)) {
      result[definition.key] = subsetStats(field.traces, definition.key, thresholds).count
    }
    return result
  }, [field, thresholds, homeTeam, awayTeam])

  /** Sizes the backing store to device pixels and returns the field scale. */
  const prepare = useCallback((): { ctx: CanvasRenderingContext2D; scale: number } | null => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return null
    const rect = wrap.getBoundingClientRect()
    if (rect.width < 2) return null
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const cssHeight = rect.width * (FIELD.height / FIELD.width)
    // Only the backing store is sized here. The element's CSS size is 100% of
    // the field wrapper, so a shrinking viewport can never leave a stale wide
    // canvas sticking out of the page while a redraw is pending.
    canvas.width = Math.round(rect.width * dpr)
    canvas.height = Math.round(cssHeight * dpr)
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, rect.width, cssHeight)
    return { ctx, scale: rect.width / FIELD.width }
  }, [])

  /** Full redraw: dimmed context first, then the selected subset on top. */
  const redraw = useCallback(() => {
    if (!field || !thresholds) return
    const prepared = prepare()
    if (!prepared) return
    const { ctx, scale } = prepared
    const n = field.traces.length
    if (filter === 'all') {
      strokeSlice(ctx, field.traces, 0, n, scale, 0.042)
      return
    }
    // Non-matching outcomes stay visible but recede, keeping the subset in
    // the context of the whole population.
    strokeSlice(ctx, field.traces, 0, n, scale, 0.014,
      (t) => !matchesFilter(t, filter, thresholds))
    strokeSlice(ctx, field.traces, 0, n, scale, 0.10,
      (t) => matchesFilter(t, filter, thresholds))
  }, [field, thresholds, filter, prepare])

  // Build (or immediately render) the field.
  useEffect(() => {
    if (!field) return
    if (frameRef.current) cancelAnimationFrame(frameRef.current)
    const duration = buildDurationMs(speed, reducedMotion)
    const total = field.traces.length

    // requestAnimationFrame does not fire in a hidden tab. Without this the
    // field would sit empty at 0 / 20,000 until the tab is looked at, so a
    // hidden document renders the completed field straight away.
    const hidden = typeof document !== 'undefined' && document.hidden
    if (duration === 0 || hidden) {
      setBuilt(field.totalOutcomes)
      redraw()
      return
    }

    const prepared = prepare()
    if (!prepared) return
    const { ctx, scale } = prepared
    const start = performance.now()
    let drawn = 0
    let lastReported = 0

    const tick = () => {
      const elapsed = performance.now() - start
      const progress = Math.min(elapsed / duration, 1)
      // Ease-out so the field fills quickly then settles.
      const target = Math.round(total * (1 - Math.pow(1 - progress, 2)))
      if (target > drawn) {
        strokeSlice(ctx, field.traces, drawn, target, scale, 0.042)
        drawn = target
        // Re-render the counter in steps, not every frame: each React render
        // re-evaluates the overlays, and doing that 60x/second is wasted work.
        const shown = Math.round((drawn / total) * field.totalOutcomes)
        if (shown - lastReported >= field.totalOutcomes / 25 || drawn === total) {
          lastReported = shown
          setBuilt(shown)
        }
      }
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick)
      } else {
        frameRef.current = null
        setBuilt(field.totalOutcomes)
        // Settle on the filtered view if one is active.
        if (filter !== 'all') redraw()
      }
    }
    frameRef.current = requestAnimationFrame(tick)

    // Safety net: if frames stop arriving (tab hidden mid-build, throttling),
    // finish the field rather than leaving a partial population on screen.
    const safety = window.setTimeout(() => {
      if (drawn < total) {
        strokeSlice(ctx, field.traces, drawn, total, scale, 0.042)
        drawn = total
        setBuilt(field.totalOutcomes)
        if (frameRef.current) { cancelAnimationFrame(frameRef.current); frameRef.current = null }
        if (filter !== 'all') redraw()
      }
    }, duration + 1200)

    return () => {
      window.clearTimeout(safety)
      if (frameRef.current) cancelAnimationFrame(frameRef.current)
    }
    // `filter` intentionally excluded: changing it must not restart the build.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field, speed, reducedMotion, replayToken, prepare, redraw])

  // Filter changes redraw the existing population — never a rebuild.
  useEffect(() => {
    if (!field || built < field.totalOutcomes) return
    const start = performance.now()
    redraw()
    setFilterMs(Math.round(performance.now() - start))
  }, [filter, field, built, redraw])

  // Keep the field crisp when the container resizes.
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined' || !wrapRef.current) return
    const observer = new ResizeObserver(() => { if (!frameRef.current) redraw() })
    observer.observe(wrapRef.current)
    return () => observer.disconnect()
  }, [redraw])

  if (!field || !stats || !thresholds || field.totalOutcomes === 0) {
    return (
      <section className="diamond-theater" aria-labelledby="theater-heading">
        <h2 id="theater-heading">Diamond Theater</h2>
        <p className="evidence-unavailable" role="status">
          No joint outcome distribution is available for this forecast, so the outcome field
          cannot be drawn.
        </p>
      </section>
    )
  }

  const complete = built >= field.totalOutcomes
  const filters = traceFilters(homeTeam, awayTeam, thresholds)
  const activeFilter = filters.find((f) => f.key === filter)!
  const pct = (value: number) => `${(value * 100).toFixed(1)}%`

  return (
    <section className="diamond-theater" aria-labelledby="theater-heading">
      <div className="theater-bar">
        <div>
          <h2 id="theater-heading">Diamond Theater</h2>
          <p className="theater-bar__sub">
            {field.totalOutcomes.toLocaleString()} completed game outcomes visualized together.
          </p>
        </div>
        <span className="theater-bar__verified">
          <ShieldCheck size={13} aria-hidden="true" />
          Simulation completed and verified before rendering
        </span>
      </div>

      <div className="field-stage">
        <div className="field-wrap" ref={wrapRef}>
          <BaseballFieldSvg homeAccent={rgba(HOME_COLOUR, 1)} awayAccent={rgba(AWAY_COLOUR, 1)} />
          <canvas ref={canvasRef} className="field-canvas" aria-hidden="true" />
        </div>

        {/* Statistics positioned around the field on desktop; below it on
            phones, where overlays would bury the park. */}
        <div className="field-stats field-stats-mobile">
          <div className="field-stat field-stat--away">
            <span className="field-stat__team" style={{ color: rgba(AWAY_COLOUR, 1) }}>{awayTeam}</span>
            <strong>{pct(1 - simulation.official_win_probability.home)}</strong>
            <small>win probability</small>
            <small className="field-stat__second">{simulation.means?.away_runs.toFixed(2)} expected runs</small>
          </div>

          <div className="field-stat field-stat--home">
            <span className="field-stat__team" style={{ color: rgba(HOME_COLOUR, 1) }}>{homeTeam}</span>
            <strong>{pct(simulation.official_win_probability.home)}</strong>
            <small>win probability</small>
            <small className="field-stat__second">{simulation.means?.home_runs.toFixed(2)} expected runs</small>
          </div>

          <div className="field-stat field-stat--centre">
            <small>Most likely score</small>
            <strong>
              {stats.modalScore
                ? `${awayTeam} ${stats.modalScore.away} — ${homeTeam} ${stats.modalScore.home}`
                : 'Not available'}
            </strong>
            <small className="field-stat__second">
              {simulation.means?.total_runs.toFixed(2)} expected total runs
            </small>
          </div>

          <div className="field-stat field-stat--mound">
            <strong>{pct(simulation.extra_inning_probability ?? 0)}</strong>
            <small>extra innings</small>
          </div>

          <div className="field-stat field-stat--plate">
            <strong className="field-stat__counter">
              {built.toLocaleString()}<span> / {field.totalOutcomes.toLocaleString()}</span>
            </strong>
            <small>simulated outcomes {complete ? 'shown' : 'drawing…'}</small>
            <small className="field-stat__second">
              ±{((simulation.sampling_uncertainty?.standard_error ?? 0) * 196).toFixed(2)} pts sampling error
              {simulation.replay_validation?.exact && ' · replay verified'}
            </small>
          </div>
        </div>
      </div>

      <div className="field-legend" aria-label="How to read the field">
        <span><i className="legend-swatch" style={{ background: rgba(AWAY_COLOUR, 0.9) }} /> {awayTeam} wins — traces bend toward left field</span>
        <span><i className="legend-swatch" style={{ background: rgba(HOME_COLOUR, 0.9) }} /> {homeTeam} wins — traces bend toward right field</span>
        <span>Depth = total runs</span>
        <span>Angle = run differential</span>
        <span>Density = how often that exact score occurred</span>
      </div>

      <div className="theater-controls" role="group" aria-label="Field controls">
        <button type="button" onClick={() => { setBuilt(0); setReplayToken((v) => v + 1) }}>
          <RotateCcw size={15} aria-hidden="true" /> <span>Replay build</span>
        </button>
        <label className="theater-controls__speed">
          <span>Speed</span>
          <select
            value={speed}
            disabled={reducedMotion}
            onChange={(event) => {
              setSpeed(Number(event.target.value) as (typeof SPEEDS)[number])
              setBuilt(0); setReplayToken((v) => v + 1)
            }}
          >
            {SPEEDS.map((value) => <option key={value} value={value}>{value}×</option>)}
          </select>
        </label>
        {filterMs != null && complete && (
          <span className="theater-controls__timing">Filter redraw {filterMs} ms</span>
        )}
      </div>

      {reducedMotion && (
        <p className="theater-reduced-motion" role="status">
          Reduced motion is on, so the completed field is rendered immediately without a build
          animation.
        </p>
      )}

      <div className="filter-row" role="group" aria-label="Filter outcomes">
        {filters.map((definition) => {
          const count = filterCounts[definition.key] ?? 0
          return (
            <button
              key={definition.key}
              type="button"
              className={definition.key === filter ? 'is-selected' : ''}
              aria-pressed={definition.key === filter}
              title={definition.description}
              onClick={() => setFilter(definition.key)}
            >
              {definition.label}
              <small>{count.toLocaleString()}</small>
            </button>
          )
        })}
      </div>

      <p className="filter-summary" role="status">
        <strong>{activeFilter.label}:</strong> {stats.count.toLocaleString()} of{' '}
        {field.totalOutcomes.toLocaleString()} outcomes ({pct(stats.share)}).{' '}
        {stats.count > 0 && (
          <>
            {homeTeam} wins {pct(stats.homeWinShare)} of this subset, averaging{' '}
            {stats.meanTotalRuns.toFixed(2)} total runs.
          </>
        )}
        <em>{FILTER_OVERLAP_NOTE}</em>
        {filter === 'extra_innings' && (
          <em>
            Taken from the simulator&rsquo;s own regulation-tie mask, not inferred from a
            one-run margin.
          </em>
        )}
      </p>

      <p className="theater-note">
        <Info size={14} aria-hidden="true" />
        <span>
          Each trace is one completed simulated game <strong>final score</strong> — not a ball
          flight, hit or baserunner path. {simulation.play_by_play.reason}{' '}
          {field.exhaustive
            ? `All ${field.totalOutcomes.toLocaleString()} outcomes are drawn individually (${field.renderedPrimitives.toLocaleString()} curves).`
            : `${field.renderedPrimitives.toLocaleString()} frequency-weighted curves represent ${field.totalOutcomes.toLocaleString()} outcomes.`}
          {' '}The animation reveals a result that was already computed and verified; it performs
          no simulation.
        </span>
      </p>
    </section>
  )
}
