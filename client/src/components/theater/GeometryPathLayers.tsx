/**
 * The ball and runner path layers, and the scope rules that accumulate them.
 *
 * Every path drawn here comes from the supplement. Nothing is interpolated into
 * existence: a play with no `ballTrace` draws no ball path, and a runner path
 * exists only for a declared `runnerMovements` entry.
 */
import { BASE_POINTS, project } from './GeometryDiamond'
import type { BallTrace, GeometryEvent, RunnerMovement } from '../../types/shadow'

export type GeometryScope = 'PLAY' | 'INNING' | 'GAME'

export interface LayerToggles {
  ball: boolean
  runners: boolean
  scoringOnly: boolean
}

/**
 * The events whose paths are visible at `index` under one scope.
 *
 * Never returns anything after `index`: scrubbing backward removes future
 * paths, and the accumulation only ever grows forward.
 */
export function visibleEvents(
  events: GeometryEvent[],
  index: number,
  scope: GeometryScope,
): GeometryEvent[] {
  if (events.length === 0) return []
  const position = Math.min(Math.max(index, 0), events.length - 1)
  const current = events[position]
  const upto = events.slice(0, position + 1)

  if (scope === 'PLAY') return [current]
  if (scope === 'INNING') {
    return upto.filter(
      (event) => event.inning === current.inning && event.half === current.half,
    )
  }
  return upto
}

/** Age-based opacity: the active play is brightest, older paths recede. */
export function trailOpacity(
  event: GeometryEvent,
  current: GeometryEvent,
  persistence: number,
): number {
  if (event.pa === current.pa) return 1
  const age = current.pa - event.pa
  // `persistence` widens or narrows how long a trail stays legible.
  const decayed = Math.exp(-age / Math.max(persistence, 1))
  return Math.max(0.06, Math.min(0.62, decayed * 0.62))
}

/** A quadratic path through the supplement's own control point. */
export function ballPathD(trace: BallTrace): string {
  const start = project(trace.start)
  const end = project(trace.end)
  const control = trace.control[0]
  if (!control) return `M${start.x} ${start.y} L${end.x} ${end.y}`
  const mid = project(control)
  // `lift` is a normalized drawing aid the supplement supplies; it carries no
  // physical unit and is only used to bow the curve.
  const bow = control.lift * 620
  return `M${start.x} ${start.y} Q${mid.x} ${mid.y - bow} ${end.x} ${end.y}`
}

/** The base-path arc a runner travels, bowed slightly to follow the diamond. */
export function runnerPathD(movement: RunnerMovement): string | null {
  const from = BASE_POINTS[movement.from]
  const to = BASE_POINTS[movement.to === 'OUT' ? movement.from : movement.to]
  if (!from || !to) return null
  if (movement.from === movement.to) return null
  if (movement.to === 'OUT') {
    // A retired runner is shown as a short stub from their base, never as a
    // claimed route to somewhere the record does not place them.
    return `M${from.x} ${from.y} l0 -26`
  }
  // Bow away from the pitcher's mound so first-to-third reads as a base path.
  const midX = (from.x + to.x) / 2
  const midY = (from.y + to.y) / 2
  const centre = project({ x: 0, y: 0.2121 })
  const bowX = midX + (midX - centre.x) * 0.22
  const bowY = midY + (midY - centre.y) * 0.22
  return `M${from.x} ${from.y} Q${bowX} ${bowY} ${to.x} ${to.y}`
}

export function BallPathLayer({ events, current, toggles, persistence, animate }: {
  events: GeometryEvent[]
  current: GeometryEvent
  toggles: LayerToggles
  persistence: number
  animate: boolean
}) {
  if (!toggles.ball) return null
  return (
    <g className="pg-ball-layer" aria-hidden="true">
      {events.map((event) => {
        // Only a supplied trace is ever drawn.
        if (!event.ballTrace) return null
        if (toggles.scoringOnly && event.runsScored === 0) return null
        const active = event.pa === current.pa
        const opacity = trailOpacity(event, current, persistence)
        const scoring = event.runsScored > 0
        const homer = event.outcome === 'HOME_RUN'
        return (
          <path
            key={`ball-${event.pa}`}
            d={ballPathD(event.ballTrace)}
            className={[
              'pg-ball',
              `pg-ball--${event.ballTrace.traceStyle.toLowerCase()}`,
              active ? 'pg-ball--active' : '',
              scoring ? 'pg-ball--scoring' : '',
              homer ? 'pg-ball--homer' : '',
              active && animate ? 'pg-ball--draw' : '',
              event.ballTrace.fairTerritory ? '' : 'pg-ball--foul',
            ].filter(Boolean).join(' ')}
            style={{ opacity }}
          />
        )
      })}
      {/* Endpoint marks, subtle, only for the paths that are drawn. */}
      {events.map((event) => {
        if (!event.ballTrace) return null
        if (toggles.scoringOnly && event.runsScored === 0) return null
        const end = project(event.ballTrace.end)
        const active = event.pa === current.pa
        return (
          <circle
            key={`end-${event.pa}`}
            cx={end.x}
            cy={end.y}
            r={active ? 5.5 : 3}
            className={`pg-ball-end${active ? ' pg-ball-end--active' : ''}`}
            style={{ opacity: trailOpacity(event, current, persistence) }}
          />
        )
      })}
    </g>
  )
}

export function RunnerPathLayer({
  events, current, toggles, persistence, accentAway, accentHome, animate,
}: {
  events: GeometryEvent[]
  current: GeometryEvent
  toggles: LayerToggles
  persistence: number
  accentAway: string
  accentHome: string
  animate: boolean
}) {
  if (!toggles.runners) return null
  return (
    <g className="pg-runner-layer" aria-hidden="true">
      {events.map((event) => {
        if (toggles.scoringOnly && event.runsScored === 0) return null
        const accent = event.battingTeam === 'AWAY' ? accentAway : accentHome
        const active = event.pa === current.pa
        const opacity = trailOpacity(event, current, persistence)
        return event.runnerMovements.map((movement) => {
          const path = runnerPathD(movement)
          if (!path) return null
          return (
            <path
              key={`run-${event.pa}-${movement.anonymousRunnerId}-${movement.from}`}
              d={path}
              className={[
                'pg-runner',
                active ? 'pg-runner--active' : '',
                movement.scored ? 'pg-runner--scored' : '',
                movement.out ? 'pg-runner--out' : '',
                active && animate ? 'pg-runner--draw' : '',
              ].filter(Boolean).join(' ')}
              style={{ stroke: accent, opacity }}
            />
          )
        })
      })}

      {/* Anonymous tokens for the active play only, so the field stays readable. */}
      {current.runnerMovements.map((movement) => {
        const destination = BASE_POINTS[movement.to === 'OUT' ? movement.from : movement.to]
        if (!destination) return null
        const accent = current.battingTeam === 'AWAY' ? accentAway : accentHome
        return (
          <circle
            key={`token-${movement.anonymousRunnerId}-${movement.from}`}
            cx={destination.x}
            cy={destination.y}
            r={movement.out ? 4.5 : 7}
            className={[
              'pg-token',
              movement.scored ? 'pg-token--scored' : '',
              movement.out ? 'pg-token--out' : '',
            ].filter(Boolean).join(' ')}
            style={{ fill: accent }}
          />
        )
      })}
    </g>
  )
}
