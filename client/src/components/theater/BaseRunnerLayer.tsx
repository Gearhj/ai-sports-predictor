/**
 * Anonymous runner tokens on the base paths.
 *
 * Runner identity is not persisted, so tokens carry no names — only the
 * batting team's colour. Positions come from the persisted base mask, and
 * movement between two states is resolved by one deterministic rule in
 * `inferRunnerMoves`. A token glides along the base path because CSS
 * interpolates its transform; the endpoints are always persisted state.
 */
import { basePoint } from './BallparkField'
import type { RunnerMove } from '../../services/replayModel'

export function BaseRunnerLayer({ moves, accent, animate }: {
  /** Resolved movement for the plate appearance now being shown. */
  moves: RunnerMove[]
  accent: string
  animate: boolean
}) {
  return (
    <g className="bp-runners" aria-hidden="true">
      {moves.map((move) => {
        // A runner who left the bases without scoring simply stops being drawn.
        if (move.to === null) return null
        const point = basePoint(move.to)
        return (
          <g
            key={move.id}
            className={`bp-runner${move.scored ? ' bp-runner--scored' : ''}${animate ? '' : ' bp-runner--static'}`}
            style={{ transform: `translate(${point.x}px, ${point.y}px)` }}
          >
            <circle r="13" className="bp-runner__halo" style={{ fill: accent }} />
            <circle r="8.5" className="bp-runner__body" style={{ fill: accent }} />
          </g>
        )
      })}
    </g>
  )
}

/**
 * Highlight ring on each occupied base.
 *
 * Separate from the tokens so occupancy stays legible even mid-transition,
 * and so a static (reduced-motion) render still communicates the base state.
 */
export function BaseOccupancyLayer({ mask, accent }: { mask: number; accent: string }) {
  const bases: Array<[number, 1 | 2 | 3]> = [[1, 1], [2, 2], [4, 3]]
  return (
    <g className="bp-occupied" aria-hidden="true">
      {bases.map(([bit, base]) => {
        if ((mask & bit) === 0) return null
        const point = basePoint(base)
        return (
          <rect
            key={base}
            x={point.x - 13}
            y={point.y - 13}
            width="26"
            height="26"
            rx="3"
            transform={`rotate(45 ${point.x} ${point.y})`}
            style={{ stroke: accent }}
          />
        )
      })}
    </g>
  )
}
