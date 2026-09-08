/**
 * The V18 Play Geometry view.
 *
 * One representative simulated game, drawn as accumulating ball and runner
 * paths. It is a second visualisation of the same persisted path the Broadcast
 * Replay shows -- not a replacement for it, and not a picture of the 5,000-game
 * population.
 *
 * Honesty contract enforced here:
 *   PERSISTED_V18_STATE      outcome, outs, base masks, runs, scores
 *   MODELLED_GEOMETRY        batted-ball type, zone, depth, the drawn path
 *   HISTORICAL_RUNNER_TEMPLATE / DETERMINISTIC_LEGAL_BACKOFF  runner movement
 *   DISPLAY_INTERPOLATION    animation timing only
 * Nothing here executes the model or alters any forecast.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Eye, EyeOff, RotateCcw } from 'lucide-react'
import { GEOMETRY_VIEWBOX, GeometryDiamond } from './GeometryDiamond'
import {
  BallPathLayer,
  RunnerPathLayer,
  visibleEvents,
  type GeometryScope,
  type LayerToggles,
} from './GeometryPathLayers'
import { BroadcastScorebug } from './BroadcastScorebug'
import { ReplayTimeline, type Speed } from './ReplayTimeline'
import { replayStatus } from '../../services/replayModel'
import type { GeometryEvent, PlayGeometryPayload, ShadowForecastView } from '../../types/shadow'
import type { ShadowPathEvent } from '../../types/shadow'

/** Milliseconds per plate appearance at 1x, matching the Broadcast Replay. */
export const GEOMETRY_STEP_MS = 360

const ACCENT_AWAY = '#4fb3ff'
const ACCENT_HOME = '#ffb454'

const SCOPES: Array<[GeometryScope, string, string]> = [
  ['PLAY', 'Current play', 'Only the active plate appearance'],
  ['INNING', 'Current inning', 'Every completed play in this half-inning'],
  ['GAME', 'Full game', 'Every completed play so far'],
]

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** The scorebug reads persisted state, which the geometry event also carries. */
function asPathEvent(event: GeometryEvent): ShadowPathEvent {
  return {
    pa: event.pa, inning: event.inning, half: event.half,
    battingOrderPosition: event.battingOrderPosition, outcome: event.outcome,
    outsBefore: event.outsBefore, outsAfter: event.outsAfter,
    baseMaskBefore: event.baseMaskBefore, baseMaskAfter: event.baseMaskAfter,
    runsScored: event.runsScored, homeScoreAfter: event.homeScoreAfter,
    awayScoreAfter: event.awayScoreAfter, pitcherState: event.pitcherState,
    pitchingChange: event.pitchingChange,
  }
}

export function PlayGeometryView({ geometry, shadow, awayTeam, homeTeam }: {
  geometry: PlayGeometryPayload
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
}) {
  const events = geometry.events
  const reduced = useMemo(prefersReducedMotion, [])
  const [index, setIndex] = useState(() => (reduced ? Math.max(0, events.length - 1) : 0))
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const [theater, setTheater] = useState(false)
  const [scope, setScope] = useState<GeometryScope>('GAME')
  const [toggles, setToggles] = useState<LayerToggles>({
    ball: true, runners: true, scoringOnly: false,
  })
  const [persistence, setPersistence] = useState(14)
  const timer = useRef<number | null>(null)

  const last = events.length - 1
  const current = events[Math.min(index, Math.max(0, last))]

  useEffect(() => {
    if (!playing || events.length === 0) return
    if (index >= last) { setPlaying(false); return }
    timer.current = window.setTimeout(
      () => setIndex((value) => Math.min(value + 1, last)),
      GEOMETRY_STEP_MS / speed,
    )
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [playing, index, last, speed, events.length])

  const seek = useCallback((next: number) => {
    setPlaying(false)
    setIndex(Math.min(Math.max(next, 0), last))
  }, [last])
  const step = useCallback((delta: number) => seek(index + delta), [index, seek])
  const restart = useCallback(() => { setPlaying(false); setIndex(0) }, [])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const tag = target?.tagName ?? ''
      // Escape always leaves theater mode, whatever holds focus. Typing
      // controls keep their own keys, and a focused button keeps its Space.
      if (event.key === 'Escape') { if (theater) setTheater(false); return }
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return
      if (tag === 'BUTTON' && (event.key === ' ' || event.key === 'Spacebar')) return
      if (event.key === ' ' || event.key === 'Spacebar') {
        event.preventDefault(); setPlaying((value) => !value)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault(); step(1)
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault(); step(-1)
      } else if (event.key === 'Home') {
        event.preventDefault(); restart()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, restart, theater])

  if (events.length === 0) {
    return (
      <p className="theater-empty" role="status">
        The registered supplement carries no renderer-ready events, so there is
        no geometry to draw. Nothing has been synthesised in its place.
      </p>
    )
  }

  const shown = visibleEvents(events, index, scope)
  const drawnBallPaths = shown.filter(
    (event) => event.ballTrace && (!toggles.scoringOnly || event.runsScored > 0),
  ).length
  const drawnRunnerPaths = shown.reduce(
    (total, event) => total
      + (toggles.scoringOnly && event.runsScored === 0 ? 0 : event.runnerMovements.length),
    0,
  )
  const pathEvent = asPathEvent(current)
  const starter = shadow.sides?.[current.half === 'TOP' ? 'HOME' : 'AWAY']?.starter ?? null
  const pitcher = current.pitcherState === 'STARTER' && starter
    ? { label: starter, sub: 'starter' }
    : { label: 'Team bullpen', sub: 'governed pitcher state' }

  return (
    <div className={`geometry${theater ? ' geometry--full' : ''}`} data-testid="play-geometry">
      <div className="geometry__stage">
        <BroadcastScorebug
          event={pathEvent}
          awayTeam={awayTeam}
          homeTeam={homeTeam}
          accentAway={ACCENT_AWAY}
          accentHome={ACCENT_HOME}
          status={replayStatus(events.map(asPathEvent), index)}
          index={index}
          total={events.length}
          pitcher={pitcher}
        />

        <div className="geometry__field-wrap">
          <svg className="geometry__field" viewBox={GEOMETRY_VIEWBOX}
            preserveAspectRatio="xMidYMid meet" role="img"
            aria-label={
              `Play geometry, ${SCOPES.find(([key]) => key === scope)?.[1]}: `
              + `${drawnBallPaths} ball path(s) and ${drawnRunnerPaths} runner movement(s) `
              + `through plate appearance ${current.pa}.`
            }>
            <GeometryDiamond accentAway={ACCENT_AWAY} accentHome={ACCENT_HOME} />
            <BallPathLayer
              events={shown} current={current} toggles={toggles}
              persistence={persistence} animate={!reduced}
            />
            <RunnerPathLayer
              events={shown} current={current} toggles={toggles}
              persistence={persistence} accentAway={ACCENT_AWAY} accentHome={ACCENT_HOME}
              animate={!reduced}
            />
          </svg>

          <div className="geometry__legend" aria-hidden="true">
            <span><i className="geometry__swatch geometry__swatch--ball" /> Ball path</span>
            <span><i className="geometry__swatch" style={{ background: ACCENT_AWAY }} /> {awayTeam} runners</span>
            <span><i className="geometry__swatch" style={{ background: ACCENT_HOME }} /> {homeTeam} runners</span>
            <span className="geometry__count">
              {drawnBallPaths} ball · {drawnRunnerPaths} runner
            </span>
          </div>
        </div>

        <div className="geometry__scopes" role="group" aria-label="Geometry scope">
          {SCOPES.map(([key, label, hint]) => (
            <button
              key={key}
              type="button"
              className={scope === key ? 'is-active' : ''}
              aria-pressed={scope === key}
              title={hint}
              aria-label={label}
              onClick={() => setScope(key)}
            >
              {label}
            </button>
          ))}
          <span className="geometry__layer-toggles" role="group" aria-label="Path layers">
            <button type="button" aria-pressed={toggles.ball}
              className={toggles.ball ? 'is-active' : ''}
              onClick={() => setToggles((value) => ({ ...value, ball: !value.ball }))}>
              {toggles.ball ? <Eye size={13} aria-hidden="true" /> : <EyeOff size={13} aria-hidden="true" />}
              Ball paths
            </button>
            <button type="button" aria-pressed={toggles.runners}
              className={toggles.runners ? 'is-active' : ''}
              onClick={() => setToggles((value) => ({ ...value, runners: !value.runners }))}>
              {toggles.runners ? <Eye size={13} aria-hidden="true" /> : <EyeOff size={13} aria-hidden="true" />}
              Runner paths
            </button>
            <button type="button" aria-pressed={toggles.scoringOnly}
              className={toggles.scoringOnly ? 'is-active' : ''}
              onClick={() => setToggles((value) => ({ ...value, scoringOnly: !value.scoringOnly }))}>
              Scoring plays only
            </button>
            <button type="button" onClick={restart} title="Clear the accumulated paths">
              <RotateCcw size={13} aria-hidden="true" /> Clear
            </button>
          </span>
          <label className="geometry__persistence">
            <span>Trail persistence</span>
            <input
              type="range" min={4} max={40} step={1} value={persistence}
              onChange={(event) => setPersistence(Number(event.target.value))}
              aria-label="Trail persistence"
            />
          </label>
        </div>

        <ReplayTimeline
          events={events.map(asPathEvent)}
          index={index}
          playing={playing}
          speed={speed}
          theater={theater}
          onSeek={seek}
          onStep={step}
          onPlayToggle={() => setPlaying((value) => !value)}
          onRestart={restart}
          onSpeed={setSpeed}
          onTheater={() => setTheater((value) => !value)}
        />

        <p className="geometry__provenance">
          One representative simulated game from {shadow.simulation_count?.toLocaleString() ?? '—'} completed
          V18 simulations — not a prediction of the exact play sequence. Modelled visual
          trajectory from simulated geometry, not a measured ball flight. Runner identities
          are anonymous.
        </p>
      </div>

      <GeometryInfoPanel event={current} geometry={geometry} pitcher={pitcher} />

      {theater && (
        <button type="button" className="theater__exit" onClick={() => setTheater(false)}>
          Exit theater mode <kbd>Esc</kbd>
        </button>
      )}
    </div>
  )
}


/**
 * The supplement's own depth vocabulary. `UNMODIFIED` means the zone centroid
 * carried no depth modifier, which is a stated absence rather than a depth.
 */
function depthLabel(depthClass: string | null | undefined): string {
  if (!depthClass) return 'depth not modelled'
  if (depthClass === 'UNMODIFIED') return 'no depth modifier'
  return depthClass.replaceAll('_', ' ').toLowerCase()
}

function value(item: unknown): string {
  return item === null || item === undefined || item === '' ? 'Not available' : String(item)
}

function GeometryInfoPanel({ event, geometry, pitcher }: {
  event: GeometryEvent
  geometry: PlayGeometryPayload
  pitcher: { label: string; sub: string }
}) {
  const shape = event.geometry
  const bases = (mask: number) => {
    const names = [
      (mask & 1) === 1 && 'first',
      (mask & 2) === 2 && 'second',
      (mask & 4) === 4 && 'third',
    ].filter(Boolean)
    return names.length ? names.join(', ') : 'empty'
  }

  return (
    <aside className="geometry__panel" aria-label="Active plate appearance">
      <header>
        <span className="geometry__panel-title">Plate appearance {event.pa}</span>
        <span className="geometry__panel-sub">
          {event.half === 'TOP' ? 'Top' : 'Bottom'} {event.inning} · {event.battingTeam === 'AWAY' ? 'away' : 'home'} batting
        </span>
      </header>

      <dl className="geometry__facts">
        <div><dt>Outcome</dt><dd>{event.outcome.replaceAll('_', ' ').toLowerCase()}</dd></div>
        <div><dt>Batting order</dt><dd>#{event.battingOrderPosition}</dd></div>
        <div><dt>Pitcher</dt><dd>{pitcher.label} <small>{pitcher.sub}</small></dd></div>
        <div><dt>Outs</dt><dd>{event.outsBefore} → {event.outsAfter}</dd></div>
        <div><dt>Bases</dt><dd>{bases(event.baseMaskBefore)} → {bases(event.baseMaskAfter)}</dd></div>
        <div><dt>Runs on this play</dt><dd>{event.runsScored}</dd></div>
        <div><dt>Score after</dt><dd>{event.awayScoreAfter} – {event.homeScoreAfter}</dd></div>
        <div>
          <dt>Batted-ball type</dt>
          <dd>
            {shape.contactOccurred
              ? value(shape.battedBallType?.replaceAll('_', ' ').toLowerCase())
              : 'No contact'}
          </dd>
        </div>
        <div>
          <dt>Zone &amp; depth</dt>
          <dd>
            {shape.contactOccurred && shape.locationZone
              ? `${shape.locationZone} · ${depthLabel(shape.landingOrFieldingPoint?.depthClass)}`
              : 'Not available'}
          </dd>
        </div>
      </dl>

      <div className="geometry__runners">
        <h4>Runner movements <small>anonymous</small></h4>
        {event.runnerMovements.length === 0
          ? <p className="geometry__none">No runner movement is recorded for this play.</p>
          : (
            <ul>
              {event.runnerMovements.map((movement) => (
                <li key={`${movement.anonymousRunnerId}-${movement.from}`}
                  className={movement.scored ? 'is-scored' : movement.out ? 'is-out' : ''}>
                  <span className="geometry__runner-id">{movement.anonymousRunnerId.slice(-2)}</span>
                  <span>
                    {movement.from.replaceAll('_', ' ').toLowerCase()} →{' '}
                    {movement.to.toLowerCase()}
                    <small>{movement.movementReason.replaceAll('_', ' ').toLowerCase()}</small>
                  </span>
                </li>
              ))}
            </ul>
          )}
      </div>

      <details className="geometry__technical">
        <summary>Technical evidence</summary>
        <dl>
          <div><dt>Geometry authority</dt><dd>{value(shape.geometryAuthority)}</dd></div>
          <div><dt>Geometry backoff level</dt><dd>{value(shape.geometryBackoffLevel)}</dd></div>
          <div><dt>Runner template authority</dt><dd>{value(event.runnerBackoffLevel)}</dd></div>
          <div>
            <dt>Runner movement authority</dt>
            <dd>
              {event.runnerMovements.some((m) => m.authority === 'DETERMINISTIC_LEGAL_BACKOFF')
                ? 'Deterministic legal backoff'
                : 'Fitted historical template'}
            </dd>
          </div>
          <div><dt>Exit velocity</dt><dd className="unavailable">Not available</dd></div>
          <div><dt>Launch angle</dt><dd className="unavailable">Not available</dd></div>
          <div><dt>Spray angle</dt><dd className="unavailable">Not available</dd></div>
          <div><dt>Projected distance</dt><dd className="unavailable">Not available</dd></div>
          <div><dt>Geometry model</dt><dd>{value(geometry.geometry_model_version)}</dd></div>
          <div><dt>Runner model</dt><dd>{value(geometry.runner_model_version)}</dd></div>
          <div><dt>Supplement</dt><dd className="mono">{value(geometry.supplement_id)}</dd></div>
          <div><dt>Sampling seed</dt><dd className="mono">{value(shape.geometrySamplingSeed)}</dd></div>
        </dl>
        <p className="geometry__note">
          Exit velocity, launch angle, spray angle and distance are not registered in
          any MLB authority and are never estimated. The drawn point is a Retrosheet
          location-zone centroid. Animation timing is presentation sequencing, not a
          measured timestamp, and nothing in this browser executes or alters the model.
        </p>
      </details>
    </aside>
  )
}

/**
 * The Simulation Population view.
 *
 * Honest evidence-pending state. It renders the population facts AISP actually
 * holds and refuses to draw density, accumulation frames or representative
 * paths from the one available trace. When a checksum-verified aggregate is
 * registered, this panel is replaced without the page changing shape.
 */
export function SimulationPopulationView({ shadow, geometry }: {
  shadow: ShadowForecastView
  geometry: PlayGeometryPayload | null
}) {
  const population = shadow.population
  const slot = geometry?.population
  const available = Boolean(slot?.populationGeometryAvailable)

  return (
    <section className="population-pending" aria-labelledby="population-pending-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">SIMULATION POPULATION</span>
          <h2 id="population-pending-heading">
            {population?.simulation_count?.toLocaleString() ?? '—'} completed simulations
          </h2>
        </div>
      </div>

      <p className="population-pending__lead">
        Population summary available for{' '}
        {population?.simulation_count?.toLocaleString() ?? '—'} simulations. Detailed
        population geometry is awaiting a checksum-verified Model Studio supplement.
      </p>

      <dl className="population-pending__facts">
        <div><dt>Home win</dt><dd>{population?.home_win_probability?.toFixed(1) ?? 'Not available'}%</dd></div>
        <div><dt>Away win</dt><dd>{population?.away_win_probability?.toFixed(1) ?? 'Not available'}%</dd></div>
        <div><dt>Expected total runs</dt><dd>{population?.expected_total_runs?.toFixed(2) ?? 'Not available'}</dd></div>
        <div>
          <dt>Representative path</dt>
          <dd>
            {geometry
              ? `1 of ${population?.simulation_count?.toLocaleString() ?? '—'} · ${geometry.plate_appearances} plate appearances`
              : 'Not available'}
          </dd>
        </div>
      </dl>

      {!available && (
        <div className="population-pending__slot" role="status">
          <strong>Population geometry is not yet registered.</strong>
          <p>
            Aggregate contact density, ball-path density, runner traffic and deterministic
            accumulation frames require a separate checksum-verified Model Studio artifact.
            AISP will not synthesise them from the single representative path, and nothing
            here is derived by repeating that one game.
          </p>
          <p className="population-pending__status">
            Status: {slot?.populationGeometryStatus
              ?? 'AWAITING_CHECKSUM_VERIFIED_MODEL_STUDIO_SUPPLEMENT'}
          </p>
        </div>
      )}
    </section>
  )
}
