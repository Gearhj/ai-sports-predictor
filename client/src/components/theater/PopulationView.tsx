/**
 * The V18 Simulation Population view.
 *
 * Deterministic aggregation over 5,000 complete simulated games, animated as
 * the artifact's own 100 accumulation frames. It is not a replay: no inning,
 * outs, bases or single-game scorebug is ever attached to this animation.
 *
 * Honesty contract enforced here:
 *   PERSISTED_V18_STATE      the accepted population metrics
 *   POPULATION_AGGREGATION   frame counts, density grids, segment traffic
 *   MODELLED_GEOMETRY        every drawn cell and path, Retrosheet-derived
 *   HISTORICAL_RUNNER_TEMPLATE / DETERMINISTIC_LEGAL_BACKOFF  runner traffic
 *   DISPLAY_INTERPOLATION    revealing the completed distribution over frames
 * Nothing here executes the model, and V18 remains a research shadow with no
 * wagering authority.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Eye, EyeOff, FastForward, Pause, Play, RotateCcw } from 'lucide-react'
import { GEOMETRY_VIEWBOX, GeometryDiamond } from './GeometryDiamond'
import {
  BallPathDensityLayerView,
  ContactDensityLayerView,
  RunnerTrafficLayerView,
  availableFilters,
  revealFraction,
} from './PopulationLayers'
import { PlayGeometryView } from './PlayGeometryView'
import { endpoints } from '../../services/api'
import type {
  PlayGeometryPayload,
  PopulationLayersPayload,
  PopulationPayload,
  PopulationRepresentativesPayload,
  RepresentativeSelector,
  ShadowForecastView,
} from '../../types/shadow'

/** 100 frames at 120 ms is a 12 s pass at 1x, inside the artifact's 10-15 s. */
export const POPULATION_FRAME_MS = 120

const ACCENT_AWAY = '#4fb3ff'
const ACCENT_HOME = '#ffb454'

type Speed = 0.5 | 1 | 2 | 4
const SPEEDS: Speed[] = [0.5, 1, 2, 4]

const SELECTOR_LABELS: Array<[RepresentativeSelector, string]> = [
  ['TYPICAL', 'Typical'],
  ['HOME_WIN', 'Home win'],
  ['AWAY_WIN', 'Away win'],
  ['HIGH_SCORING', 'High scoring'],
  ['LOW_SCORING', 'Low scoring'],
  ['EXTRA_INNINGS', 'Extra innings'],
]

interface Toggles {
  contact: boolean
  paths: boolean
  runners: boolean
  scoring: boolean
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function integer(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Not available' : value.toLocaleString()
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Not available' : `${(value * 100).toFixed(1)}%`
}

function decimal(value: number | null | undefined, places = 2): string {
  return value === null || value === undefined ? 'Not available' : value.toFixed(places)
}

/**
 * One representative game, shaped for the existing Play Geometry renderer.
 *
 * Only supplement-supplied values are carried across; the counts are read off
 * the same events that are drawn.
 */
export function representativePayload(
  representatives: PopulationRepresentativesPayload,
  selector: RepresentativeSelector,
  population: PopulationPayload,
  gameId: string,
): PlayGeometryPayload | null {
  const chosen = representatives.selectors?.[selector]
  const path = representatives.paths.find((item) => item.pathDigest === chosen?.pathDigest)
  if (!chosen || !path) return null
  const events = path.events
  return {
    schema_version: 'AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1',
    forecast_id: representatives.forecast_id,
    game_id: gameId,
    engine_id: 'MLB_V18_WHOLE_GAME',
    supplement_id: representatives.supplement_id,
    supplement_sha256: representatives.supplement_sha256,
    parent_bundle_id: '',
    parent_bundle_sha256: representatives.parent_bundle_sha256,
    coordinate_system: { name: 'ASP_NORMALIZED_DIAMOND_V1', origin: 'HOME_PLATE' },
    geometry_model_version: events[0]?.geometry?.geometryModelVersion ?? 'Not recorded',
    runner_model_version: events[0]?.runnerModelVersion ?? 'Not recorded',
    registered_at: '',
    backoff_summary: path.backoffSummary,
    limitations: null,
    wagering_authority: false,
    promotion_status: 'NOT_PROMOTED',
    execution_allowed_in_aisp: false,
    display_authority: 'RESEARCH_SHADOW_PLAY_GEOMETRY',
    plate_appearances: events.length,
    contact_plays: events.filter((event) => event.geometry?.contactOccurred).length,
    ball_traces: events.filter((event) => event.ballTrace).length,
    runner_movements: events.reduce(
      (total, event) => total + (event.runnerMovements?.length ?? 0), 0,
    ),
    events,
    population: {
      simulationCount: population.simulation_count,
      populationGeometryAvailable: true,
      populationGeometryStatus: 'REGISTERED',
      populationSupplementId: population.supplement_id,
      populationSupplementSha256: population.supplement_sha256,
    },
    notes: representatives.notes,
  }
}

export function PopulationView({ forecastId, shadow, awayTeam, homeTeam, gameId }: {
  forecastId: string
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
  gameId: string
}) {
  const reduced = useMemo(prefersReducedMotion, [])
  const [population, setPopulation] = useState<PopulationPayload | null>(null)
  const [layers, setLayers] = useState<PopulationLayersPayload | null>(null)
  const [representatives, setRepresentatives] = useState<PopulationRepresentativesPayload | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading')
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const [filter, setFilter] = useState('ALL')
  const [selected, setSelected] = useState<RepresentativeSelector | null>(null)
  const [toggles, setToggles] = useState<Toggles>({
    contact: true, paths: true, runners: true, scoring: true,
  })
  const timer = useRef<number | null>(null)

  // Stage one: identity, accepted summary and the accumulation frames.
  useEffect(() => {
    const controller = new AbortController()
    let live = true
    endpoints
      .shadowForecastPopulation(forecastId, controller.signal)
      .then((payload) => {
        if (!live) return
        // Fail closed: only a payload that declares the research-shadow
        // display authority, no execution and no wagering authority is drawn.
        const usable = payload?.schema_version === 'AISP_MLB_V18_POPULATION_VIEW_V1'
          && payload.display_authority === 'RESEARCH_SHADOW_POPULATION_GEOMETRY'
          && payload.wagering_authority === false
          && payload.execution_allowed_in_aisp === false
          && Array.isArray(payload.frames) && payload.frames.length > 0
        setPopulation(usable ? payload : null)
        setState(usable ? 'ready' : 'unavailable')
        if (usable) {
          setIndex(reduced ? payload.frames.length - 1 : 0)
          setPlaying(!reduced)
        }
      })
      .catch(() => { if (live) { setPopulation(null); setState('unavailable') } })
    return () => { live = false; controller.abort() }
  }, [forecastId, reduced])

  // Stage two: the density layers, once the frames are on screen.
  useEffect(() => {
    if (!population) return
    const controller = new AbortController()
    let live = true
    endpoints
      .shadowForecastPopulationLayers(forecastId, controller.signal)
      .then((payload) => {
        if (!live) return
        setLayers(
          payload?.schema_version === 'AISP_MLB_V18_POPULATION_LAYERS_V1' ? payload : null,
        )
      })
      .catch(() => { if (live) setLayers(null) })
    return () => { live = false; controller.abort() }
  }, [forecastId, population])

  // Stage three: the representative games, only when one is asked for.
  useEffect(() => {
    if (!selected || representatives) return
    const controller = new AbortController()
    let live = true
    endpoints
      .shadowForecastPopulationRepresentatives(forecastId, controller.signal)
      .then((payload) => {
        if (!live) return
        setRepresentatives(
          payload?.schema_version === 'AISP_MLB_V18_POPULATION_REPRESENTATIVES_V1'
            ? payload : null,
        )
      })
      .catch(() => { if (live) setRepresentatives(null) })
    return () => { live = false; controller.abort() }
  }, [forecastId, selected, representatives])

  const frames = population?.frames ?? []
  const last = frames.length - 1

  useEffect(() => {
    if (!playing || frames.length === 0 || selected) return
    if (index >= last) { setPlaying(false); return }
    timer.current = window.setTimeout(
      () => setIndex((value) => Math.min(value + 1, last)),
      POPULATION_FRAME_MS / speed,
    )
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [playing, index, last, speed, frames.length, selected])

  const seek = useCallback((next: number) => {
    setPlaying(false)
    setIndex(Math.min(Math.max(next, 0), Math.max(last, 0)))
  }, [last])

  if (state === 'loading') {
    return <p className="theater-empty" role="status">Loading the accepted population…</p>
  }
  if (state === 'unavailable' || !population) {
    return (
      <p className="theater-empty" role="status">
        No checksum-verified population supplement is registered for this forecast,
        so there is no aggregate to draw. The Broadcast Replay remains available
        and nothing has been synthesised here.
      </p>
    )
  }

  const frame = frames[Math.min(index, last)]
  const reveal = revealFraction(frame.cumulativeSimulationCount, population.simulation_count)
  const filters = availableFilters(layers)
  const contactLayer = layers?.contact_density.layers[filter]
    ?? layers?.contact_density.layers.ALL
  const pathLayer = layers?.path_density.layers[filter] ?? layers?.path_density.layers.ALL
  const scoringLayer = layers?.contact_density.layers.SCORING
  const runnerDimension = filter === 'ALL'
    ? layers?.runner_traffic.allSegments
    : layers?.runner_traffic.dimensions?.[filter]

  if (selected) {
    const chosen = population.selectors?.[selected]
    const payload = representatives
      ? representativePayload(representatives, selected, population, gameId)
      : null
    return (
      <section className="population" aria-labelledby="population-representative-heading">
        <div className="population__representative-bar">
          <button type="button" className="population__back" onClick={() => setSelected(null)}>
            <ChevronLeft size={14} aria-hidden="true" /> Back to the 5,000-game population
          </button>
          <p id="population-representative-heading">
            <strong>Representative simulated game</strong> — one selected simulation out of{' '}
            {integer(population.simulation_count)}, not a summary of them and not the
            expected exact play sequence.
          </p>
        </div>

        {chosen && (
          <dl className="population__representative-facts">
            <div><dt>Selector</dt><dd>{SELECTOR_LABELS.find(([key]) => key === selected)?.[1]}</dd></div>
            <div><dt>Simulation index</dt><dd>{chosen.simulationIndex}</dd></div>
            <div><dt>Final</dt><dd>
              {awayTeam} {chosen.selectionInputs.awayRuns} – {chosen.selectionInputs.homeRuns} {homeTeam}
              {chosen.selectionInputs.innings > 9 ? ` (${chosen.selectionInputs.innings})` : ''}
            </dd></div>
            <div><dt>Candidates</dt><dd>{integer(chosen.candidateCount)}</dd></div>
            <div className="population__definition"><dt>Selection rule</dt><dd>{chosen.definition}</dd></div>
          </dl>
        )}

        {payload
          ? (
            <PlayGeometryView
              geometry={payload}
              shadow={shadow}
              awayTeam={awayTeam}
              homeTeam={homeTeam}
            />
          )
          : (
            <p className="theater-empty" role="status">
              Loading the selected representative simulation…
            </p>
          )}
      </section>
    )
  }

  return (
    <section className="population" aria-labelledby="population-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">SIMULATION POPULATION</span>
          <h2 id="population-heading">
            {integer(frame.cumulativeSimulationCount)} of {integer(population.simulation_count)} simulations
          </h2>
        </div>
        <p className="population__lead">
          Compressed accumulation across {integer(population.simulation_count)} complete V18
          simulations. Each animation step adds {population.frame_size} games; it is not one
          continuous baseball game.
        </p>
      </div>

      <div className="population__stage">
        <div className="population__field-wrap">
          <svg className="population__field" viewBox={GEOMETRY_VIEWBOX}
            preserveAspectRatio="xMidYMid meet" role="img"
            aria-label={
              `Population geometry after ${frame.cumulativeSimulationCount} of `
              + `${population.simulation_count} simulations: `
              + `${frame.cumulativeContactPlayCount} contact plays, `
              + `${frame.cumulativeRunnerPathTraffic} runner segments, `
              + `${frame.cumulativeScoringPlayCount} scoring plays.`
            }>
            <defs>
              {/* Softens the sparse grid into a heat field. Presentation only. */}
              <filter id="pop-heat" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="7" />
              </filter>
            </defs>
            <GeometryDiamond accentAway={ACCENT_AWAY} accentHome={ACCENT_HOME} />
            {toggles.runners && runnerDimension && (
              <RunnerTrafficLayerView segments={runnerDimension} reveal={reveal} />
            )}
            {toggles.paths && pathLayer && layers && (
              <BallPathDensityLayerView
                layer={pathLayer} grid={layers.path_density.grid} reveal={reveal}
              />
            )}
            {toggles.contact && contactLayer && layers && (
              <ContactDensityLayerView
                layer={contactLayer} grid={layers.contact_density.grid} reveal={reveal}
              />
            )}
            {toggles.scoring && scoringLayer && layers && (
              <ContactDensityLayerView
                layer={scoringLayer} grid={layers.contact_density.grid}
                reveal={reveal} scoring
              />
            )}
          </svg>

          <div className="population__legend">
            <span><i className="population__swatch population__swatch--contact" /> Contact density</span>
            <span><i className="population__swatch population__swatch--path" /> Ball-path traffic</span>
            <span><i className="population__swatch population__swatch--runner" /> Runner traffic</span>
            <span><i className="population__swatch population__swatch--scoring" /> Scoring plays</span>
            <span className="population__legend-note">
              Brighter means a higher share of that layer's busiest cell.
            </span>
          </div>

          {!layers && (
            <p className="population__loading" role="status">Loading density layers…</p>
          )}
        </div>

        <aside className="population__metrics" aria-label="Population convergence">
          <div className="population__metric population__metric--wide">
            <span className="population__metric-label">Simulations</span>
            <span className="population__metric-value">
              {integer(frame.cumulativeSimulationCount)}
              <small> of {integer(population.simulation_count)}</small>
            </span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">{homeTeam} win</span>
            <span className="population__metric-value" style={{ color: ACCENT_HOME }}>
              {percent(frame.cumulativeHomeWinProbability)}
            </span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">{awayTeam} win</span>
            <span className="population__metric-value" style={{ color: ACCENT_AWAY }}>
              {percent(frame.cumulativeAwayWinProbability)}
            </span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">Expected {homeTeam} runs</span>
            <span className="population__metric-value">{decimal(frame.cumulativeExpectedHomeRuns)}</span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">Expected {awayTeam} runs</span>
            <span className="population__metric-value">{decimal(frame.cumulativeExpectedAwayRuns)}</span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">Expected total</span>
            <span className="population__metric-value">{decimal(frame.cumulativeExpectedTotal)}</span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">Run differential</span>
            <span className="population__metric-value">{decimal(frame.cumulativeRunDifferential)}</span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">One-run games</span>
            <span className="population__metric-value">{percent(frame.cumulativeOneRunGameRate)}</span>
          </div>
          <div className="population__metric">
            <span className="population__metric-label">Extra innings</span>
            <span className="population__metric-value">{percent(frame.cumulativeExtraInningRate)}</span>
          </div>

          <dl className="population__counts">
            <div><dt>Contact plays</dt><dd>{integer(frame.cumulativeContactPlayCount)}</dd></div>
            <div><dt>Ball-path density</dt><dd>{integer(frame.cumulativeGeometryDensity)}</dd></div>
            <div><dt>Runner segments</dt><dd>{integer(frame.cumulativeRunnerPathTraffic)}</dd></div>
            <div><dt>Scoring plays</dt><dd>{integer(frame.cumulativeScoringPlayCount)}</dd></div>
          </dl>
        </aside>
      </div>

      <div className="population__controls">
        <div className="population__layer-toggles" role="group" aria-label="Population layers">
          {([
            ['contact', 'Contact density'],
            ['paths', 'Ball paths'],
            ['runners', 'Runner traffic'],
            ['scoring', 'Scoring plays'],
          ] as Array<[keyof Toggles, string]>).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-label={label}
              aria-pressed={toggles[key]}
              className={toggles[key] ? 'is-active' : ''}
              onClick={() => setToggles((value) => ({ ...value, [key]: !value[key] }))}
            >
              {toggles[key] ? <Eye size={13} aria-hidden="true" /> : <EyeOff size={13} aria-hidden="true" />}
              {label}
            </button>
          ))}
          <label className="population__filter">
            <span>Filter</span>
            <select value={filter} onChange={(event) => setFilter(event.target.value)}
              aria-label="Population filter">
              {filters.map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="population__transport">
          <button type="button" aria-label="Previous frame" onClick={() => seek(index - 1)}>
            <ChevronLeft size={15} aria-hidden="true" />
          </button>
          <button type="button" className="population__play"
            aria-label={playing ? 'Pause' : 'Play'}
            onClick={() => setPlaying((value) => !value)}>
            {playing
              ? <><Pause size={14} aria-hidden="true" /> Pause</>
              : <><Play size={14} aria-hidden="true" /> Play</>}
          </button>
          <button type="button" aria-label="Next frame" onClick={() => seek(index + 1)}>
            <ChevronRight size={15} aria-hidden="true" />
          </button>
          <button type="button" aria-label="Restart" onClick={() => { setPlaying(false); setIndex(0) }}>
            <RotateCcw size={14} aria-hidden="true" />
          </button>

          <input
            type="range" min={0} max={Math.max(last, 0)} step={1} value={index}
            aria-label="Accumulation frame"
            onChange={(event) => seek(Number(event.target.value))}
          />

          <span className="population__frame-count">
            Frame {frame.frameIndex + 1} of {population.frame_count}
          </span>

          {SPEEDS.map((option) => (
            <button
              key={option}
              type="button"
              aria-label={`${option}x speed`}
              aria-pressed={speed === option}
              className={speed === option ? 'is-active' : ''}
              onClick={() => setSpeed(option)}
            >
              {option}×
            </button>
          ))}

          <button type="button" className="population__jump" onClick={() => seek(last)}>
            <FastForward size={13} aria-hidden="true" /> Jump to all {integer(population.simulation_count)}
          </button>
        </div>
      </div>

      <div className="population__representatives" role="group" aria-label="Representative simulated games">
        <h3>Representative simulated games</h3>
        <p>
          Population mode summarizes {integer(population.simulation_count)} games. Selecting one
          below leaves that summary and shows a single simulated game, drawn with the same
          Play Geometry renderer. A representative path is not the expected exact game sequence.
        </p>
        <div className="population__representative-buttons">
          {SELECTOR_LABELS.map(([key, label]) => {
            const chosen = population.selectors?.[key]
            return (
              <button key={key} type="button" onClick={() => setSelected(key)}
                disabled={!chosen}>
                <strong>{label}</strong>
                {chosen && (
                  <small>
                    #{chosen.simulationIndex} · {chosen.selectionInputs.awayRuns}–
                    {chosen.selectionInputs.homeRuns} · {chosen.selectionInputs.innings} inn
                  </small>
                )}
              </button>
            )
          })}
        </div>
      </div>

      <p className="population__provenance">
        Deterministic aggregation over {integer(population.simulation_count)} complete V18
        simulations ({integer(population.counts.plateAppearances)} plate appearances,{' '}
        {integer(population.counts.contactPlays)} geometry-bearing contact plays,{' '}
        {integer(population.counts.runnerSegments)} anonymous runner segments). Density is the
        completed distribution, revealed in proportion to the frames accumulated — display
        interpolation, not a per-frame measurement. Geometry is modelled from Retrosheet zone
        evidence, not measured Statcast tracking. Runner identities are anonymous. V18 is a
        research shadow with no wagering authority, and nothing in AISP runs the model.
      </p>

      <details className="population__technical">
        <summary>Population evidence</summary>
        <dl>
          <div><dt>Supplement</dt><dd className="mono">{population.supplement_id}</dd></div>
          <div><dt>Supplement checksum</dt><dd className="mono">{population.supplement_sha256}</dd></div>
          <div><dt>Parent V18 bundle</dt><dd className="mono">{population.parent_bundle_sha256}</dd></div>
          <div><dt>Parent play geometry</dt><dd className="mono">{population.play_geometry_sha256}</dd></div>
          <div><dt>Population digest</dt><dd className="mono">{population.population_digest}</dd></div>
          <div><dt>Execution authority</dt><dd className="mono">{population.execution_authority_sha256}</dd></div>
          <div><dt>Master seed</dt><dd>{population.master_seed}</dd></div>
          <div><dt>Frames</dt><dd>{population.frame_count} × {population.frame_size} simulations</dd></div>
          <div><dt>Deterministic replay</dt><dd>{population.deterministic_replay ? 'Verified' : 'Not verified'}</dd></div>
          <div><dt>Illegal states / nonterminations</dt><dd>
            {population.illegal_state_count} / {population.nontermination_count}
          </dd></div>
          <div><dt>Wagering authority</dt><dd>None — research shadow only</dd></div>
        </dl>
        <p className="population__note">
          Exit velocity, launch angle, spray angle, measured landing coordinates, physical
          flight timing and named runner identity are not registered anywhere in this
          artifact and are never estimated. Filters are the bounded one-dimensional layers
          the supplement carries; no combined cube is constructed in the browser.
        </p>
      </details>
    </section>
  )
}
