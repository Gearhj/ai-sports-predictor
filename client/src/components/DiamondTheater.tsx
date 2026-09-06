/**
 * The Diamond Theater.
 *
 * IMPORTANT HONESTY BOUNDARY
 * --------------------------
 * The official Gen 1 simulator draws final scores directly from two Poisson
 * distributions. It models no innings, plate appearances, base-out states,
 * batters or pitchers, so there is no play sequence to animate. This component
 * therefore does NOT animate a fake game. It steps through *real* representative
 * outcomes selected from the official simulation's own draws, and animates the
 * simulated-score distribution accumulating toward the published aggregate.
 *
 * Bases are rendered as an inert diamond motif — never populated with invented
 * runners — and the play-by-play state is reported explicitly.
 */
import { ChevronLeft, ChevronRight, Info, Pause, Play, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { OUTCOME_LABELS } from '../services/simulationStory'
import type { GameSimulationData, RepresentativeOutcome } from '../types/simulation'

const SPEEDS = [0.5, 1, 2] as const

function usePrefersReducedMotion(): boolean {
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

export function DiamondTheater({
  simulation, homeTeam, awayTeam,
}: {
  simulation: GameSimulationData
  homeTeam: string
  awayTeam: string
}) {
  const outcomes = useMemo(
    () => (simulation.representative_outcomes ?? []).filter((o) => o.available),
    [simulation],
  )
  const reducedMotion = usePrefersReducedMotion()
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1)
  const timer = useRef<number | null>(null)

  const current: RepresentativeOutcome | undefined = outcomes[index]

  const step = useCallback((delta: number) => {
    setIndex((value) => {
      if (outcomes.length === 0) return 0
      return (value + delta + outcomes.length) % outcomes.length
    })
  }, [outcomes.length])

  useEffect(() => {
    if (!playing || reducedMotion || outcomes.length === 0) return
    timer.current = window.setInterval(() => step(1), 2600 / speed)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [playing, speed, step, reducedMotion, outcomes.length])

  // Reduced motion must stop motion, not merely slow it.
  useEffect(() => { if (reducedMotion) setPlaying(false) }, [reducedMotion])

  if (outcomes.length === 0 || !current) {
    return (
      <section className="diamond-theater" aria-labelledby="theater-heading">
        <h2 id="theater-heading">Diamond Theater</h2>
        <p className="evidence-unavailable" role="status">
          No representative simulated outcome is available for this forecast.
        </p>
      </section>
    )
  }

  const winnerIsHome = current.winner === 'home'
  const winnerName = winnerIsHome ? homeTeam : awayTeam

  return (
    <section className="diamond-theater" aria-labelledby="theater-heading">
      <div className="diamond-theater__head">
        <div>
          <h2 id="theater-heading">Diamond Theater</h2>
          <p className="diamond-theater__sub">
            Representative simulated path—not a prediction of the exact game sequence.
          </p>
        </div>
        <span className="diamond-theater__draw">
          Simulation draw #{current.draw_index?.toLocaleString()} of{' '}
          {simulation.inputs?.simulations.toLocaleString()}
        </span>
      </div>

      {/* Broadcast-style scorebug: every value below is a real simulated result. */}
      <div className="scorebug" role="group" aria-label="Simulated final score">
        <div className={`scorebug__team ${!winnerIsHome ? 'scorebug__team--winner' : ''}`}>
          <span className="scorebug__label">{awayTeam}</span>
          <span className="scorebug__runs">{current.away_runs}</span>
        </div>
        <div className="scorebug__middle">
          <span className="scorebug__state">
            {current.went_extra_innings ? 'Final / extra innings' : 'Final / 9'}
          </span>
          <span className="scorebug__total">{current.total_runs} total runs</span>
        </div>
        <div className={`scorebug__team ${winnerIsHome ? 'scorebug__team--winner' : ''}`}>
          <span className="scorebug__label">{homeTeam}</span>
          <span className="scorebug__runs">{current.home_runs}</span>
        </div>
      </div>

      <div className="diamond-theater__body">
        <DiamondFigure wentExtra={Boolean(current.went_extra_innings)} />
        <dl className="diamond-theater__facts">
          <div>
            <dt>Selection rule</dt>
            <dd>{current.criterion}</dd>
          </div>
          <div>
            <dt>Winner in this simulation</dt>
            <dd>{winnerName}</dd>
          </div>
          <div>
            <dt>How often this exact score occurs</dt>
            <dd>
              {(current.share_of_simulations * 100).toFixed(2)}% of all simulations
              <small> · {(current.share_within_criterion * 100).toFixed(1)}% within this selection</small>
            </dd>
          </div>
          <div>
            <dt>Extra innings in this simulation</dt>
            <dd>{current.went_extra_innings ? 'Yes' : 'No'}</dd>
          </div>
        </dl>
      </div>

      <div className="theater-controls" role="group" aria-label="Playback controls">
        <button type="button" onClick={() => step(-1)} aria-label="Previous outcome">
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setPlaying((value) => !value)}
          disabled={reducedMotion}
          aria-label={playing ? 'Pause' : 'Play'}
          aria-pressed={playing}
        >
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          <span>{playing ? 'Pause' : 'Play'}</span>
        </button>
        <button type="button" onClick={() => step(1)} aria-label="Next outcome">
          <ChevronRight size={16} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => { setIndex(0); setPlaying(false) }} aria-label="Restart">
          <RotateCcw size={16} aria-hidden="true" /> <span>Restart</span>
        </button>
        <label className="theater-controls__speed">
          <span>Speed</span>
          <select
            value={speed}
            disabled={reducedMotion}
            onChange={(event) => setSpeed(Number(event.target.value) as (typeof SPEEDS)[number])}
          >
            {SPEEDS.map((value) => <option key={value} value={value}>{value}×</option>)}
          </select>
        </label>
      </div>

      {reducedMotion && (
        <p className="theater-reduced-motion" role="status">
          Reduced motion is on, so automatic playback is disabled. Use the step controls to move
          between outcomes.
        </p>
      )}

      <div className="outcome-picker" role="group" aria-label="Representative outcome">
        {outcomes.map((outcome, position) => (
          <button
            key={outcome.key}
            type="button"
            className={position === index ? 'is-selected' : ''}
            aria-pressed={position === index}
            onClick={() => { setIndex(position); setPlaying(false) }}
          >
            {OUTCOME_LABELS[outcome.key]}
            <small>{outcome.away_runs}–{outcome.home_runs}</small>
          </button>
        ))}
      </div>

      <p className="theater-note">
        <Info size={14} aria-hidden="true" />
        <span>{simulation.play_by_play.reason} {simulation.play_by_play.what_is_shown_instead}</span>
      </p>
    </section>
  )
}

/**
 * An inert diamond motif. Bases are never populated: the model has no base-out
 * state, so drawing runners would invent evidence.
 */
function DiamondFigure({ wentExtra }: { wentExtra: boolean }) {
  return (
    <figure className="diamond-figure">
      <svg viewBox="0 0 200 180" role="img" aria-label="Baseball diamond graphic. Base occupancy is not modelled by this simulator.">
        <defs>
          <linearGradient id="dt-turf" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#12243b" />
            <stop offset="100%" stopColor="#0b1729" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="200" height="180" fill="url(#dt-turf)" rx="8" />
        <polygon points="100,148 152,96 100,44 48,96" fill="none" stroke="#2f5470" strokeWidth="1.5" />
        <circle cx="100" cy="96" r="13" fill="none" stroke="#2f5470" strokeWidth="1.5" />
        {[[100, 148], [152, 96], [100, 44], [48, 96]].map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x - 5} y={y - 5} width="10" height="10"
                transform={`rotate(45 ${x} ${y})`} fill="#16304d" stroke="#3f6c8d" strokeWidth="1.2" />
        ))}
        <text x="100" y="170" textAnchor="middle" fill="#9fbccd" fontSize="11">
          {wentExtra ? 'Resolved in extra innings' : 'Resolved in regulation'}
        </text>
      </svg>
      <figcaption>
        Base occupancy, outs and batters are not modelled by the official simulator and are
        deliberately not shown.
      </figcaption>
    </figure>
  )
}
