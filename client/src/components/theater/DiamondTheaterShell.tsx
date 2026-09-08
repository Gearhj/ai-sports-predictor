/**
 * The V18 Monte Carlo Diamond Theater.
 *
 * Owns replay state and composes the scorebug, ballpark, runner layer, outcome
 * overlay, population overlays, feed and transport into one surface. It runs no
 * simulation: every frame is a persisted plate appearance, and the only thing
 * computed here is which one to show.
 *
 * Two kinds of number share the screen and must never be confused:
 *   - FIXED population conclusions across all simulated games (flat plates)
 *   - CHANGING state of the one representative path (the bright scorebug)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BallparkField, FIELD } from './BallparkField'
import { BaseOccupancyLayer, BaseRunnerLayer } from './BaseRunnerLayer'
import { BroadcastScorebug } from './BroadcastScorebug'
import { FinalOverlay, PlayOutcomeOverlay } from './PlayOutcomeOverlay'
import { PopulationStatOverlay } from './PopulationStatOverlay'
import { ReplayTimeline, type Speed } from './ReplayTimeline'
import { SimulationEventFeed } from './SimulationEventFeed'
import { inferRunnerMoves, replayStatus } from '../../services/replayModel'
import type { ShadowForecastView, ShadowPath } from '../../types/shadow'

/**
 * Milliseconds per plate appearance at 1x.
 *
 * 74 plate appearances review in about 27 seconds — fast enough to feel like a
 * game, slow enough to read each outcome.
 */
export const BASE_STEP_MS = 360

const ACCENT_AWAY = '#4fb3ff'
const ACCENT_HOME = '#ffb454'

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function DiamondTheaterShell({ path, shadow, awayTeam, homeTeam }: {
  path: ShadowPath
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
}) {
  const events = useMemo(() => path.paths[0] ?? [], [path])
  const reduced = useMemo(prefersReducedMotion, [])
  const [index, setIndex] = useState(() => (reduced ? Math.max(0, events.length - 1) : 0))
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const [theater, setTheater] = useState(false)
  const timer = useRef<number | null>(null)
  const surface = useRef<HTMLDivElement | null>(null)

  const last = events.length - 1
  const current = events[Math.min(index, Math.max(0, last))]

  // -- transport ----------------------------------------------------------
  useEffect(() => {
    if (!playing || events.length === 0) return
    if (index >= last) { setPlaying(false); return }
    timer.current = window.setTimeout(
      () => setIndex((value) => Math.min(value + 1, last)),
      BASE_STEP_MS / speed,
    )
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [playing, index, last, speed, events.length])

  const seek = useCallback((next: number) => {
    setPlaying(false)
    setIndex(Math.min(Math.max(next, 0), last))
  }, [last])
  const step = useCallback((delta: number) => seek(index + delta), [index, seek])
  const restart = useCallback(() => { setPlaying(false); setIndex(0) }, [])

  // -- keyboard -----------------------------------------------------------
  useEffect(() => {
    function onKey(keyEvent: KeyboardEvent) {
      const target = keyEvent.target as HTMLElement | null
      // Never hijack a key the user is aiming at a control.
      if (target && ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(target.tagName)) return
      if (keyEvent.key === ' ' || keyEvent.key === 'Spacebar') {
        keyEvent.preventDefault()
        setPlaying((value) => !value)
      } else if (keyEvent.key === 'ArrowRight') {
        keyEvent.preventDefault(); step(1)
      } else if (keyEvent.key === 'ArrowLeft') {
        keyEvent.preventDefault(); step(-1)
      } else if (keyEvent.key === 'Home') {
        keyEvent.preventDefault(); restart()
      } else if (keyEvent.key === 'Escape' && theater) {
        setTheater(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, restart, theater])

  if (events.length === 0) {
    return (
      <p className="theater-empty" role="status">
        No sequential path is persisted for this research-shadow forecast, so there
        is nothing to replay. Nothing has been reconstructed in its place.
      </p>
    )
  }

  const battingAccent = current.half === 'TOP' ? ACCENT_AWAY : ACCENT_HOME
  const moves = inferRunnerMoves(current)
  const status = replayStatus(events, index)
  const complete = index >= last
  const starter = shadow.sides?.[current.half === 'TOP' ? 'HOME' : 'AWAY']?.starter ?? null
  const pitcher = current.pitcherState === 'STARTER' && starter
    ? { label: starter, sub: 'starter' }
    : { label: 'Team bullpen', sub: 'governed pitcher state' }

  return (
    <div
      ref={surface}
      className={`theater${theater ? ' theater--full' : ''}`}
      data-testid="diamond-theater"
    >
      <div className="theater__stage">
        <BroadcastScorebug
          event={current}
          awayTeam={awayTeam}
          homeTeam={homeTeam}
          accentAway={ACCENT_AWAY}
          accentHome={ACCENT_HOME}
          status={status}
          index={index}
          total={events.length}
          pitcher={pitcher}
        />

        <div className="theater__park">
          <svg className="theater__field" viewBox={FIELD.frame} preserveAspectRatio={theater ? 'xMidYMid meet' : 'xMidYMid slice'} role="img" aria-label={
            `${current.half === 'TOP' ? 'Top' : 'Bottom'} of inning ${current.inning}, `
            + `${current.outsAfter} out. `
            + (moves.filter((move) => move.to !== null && move.to < 4).length
              ? `${moves.filter((move) => move.to !== null && move.to < 4).length} runner(s) on base.`
              : 'Bases empty.')
          }>
            <BallparkField accentAway={ACCENT_AWAY} accentHome={ACCENT_HOME} />
            <BaseOccupancyLayer mask={current.baseMaskAfter} accent={battingAccent} />
            <BaseRunnerLayer moves={moves} accent={battingAccent} animate={!reduced} />
          </svg>

          <PopulationStatOverlay
            population={shadow.population}
            awayTeam={awayTeam}
            homeTeam={homeTeam}
            accentAway={ACCENT_AWAY}
            accentHome={ACCENT_HOME}
          />

          <PlayOutcomeOverlay event={current} accent={battingAccent} />

          {complete && (
            <FinalOverlay
              awayTeam={awayTeam}
              homeTeam={homeTeam}
              awayScore={current.awayScoreAfter}
              homeScore={current.homeScoreAfter}
              innings={current.inning}
            />
          )}

        </div>

        <p className="theater__provenance">
          Representative path selected from{' '}
          {shadow.simulation_count?.toLocaleString() ?? '—'} completed V18 simulations.
          This is one simulated game path, not a prediction of the exact play sequence.
        </p>

        <ReplayTimeline
          events={events}
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
      </div>

      <SimulationEventFeed
        events={events}
        index={index}
        awayTeam={awayTeam}
        homeTeam={homeTeam}
      />

      {theater && (
        <button type="button" className="theater__exit" onClick={() => setTheater(false)}>
          Exit theater mode <kbd>Esc</kbd>
        </button>
      )}
    </div>
  )
}
