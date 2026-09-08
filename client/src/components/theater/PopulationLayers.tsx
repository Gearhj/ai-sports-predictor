/**
 * The population density layers and the pure logic behind them.
 *
 * Everything drawn here comes from the compact grids the population supplement
 * supplies: a 32x32 sparse contact grid, a sparse quadratic ball-path grid and
 * anonymous runner-segment counts. Nothing is expanded into per-play geometry,
 * and no cell, path or segment the artifact does not carry is invented.
 *
 * The one presentation-only step is DISPLAY_INTERPOLATION: the completed
 * distribution is revealed in proportion to the frames accumulated so far, so
 * the animation strengthens as games are added. It is labelled as such in the
 * view and never described as a per-frame measurement.
 */
import { BASE_POINTS, project } from './GeometryDiamond'
import type {
  ContactDensityLayer, PathDensityLayer, PopulationLayersPayload,
} from '../../types/shadow'

export interface DensityGrid {
  width: number
  height: number
  xRange?: [number, number]
  yRange?: [number, number]
}

/** A grid cell's normalized-diamond centre and size, in SVG units. */
export function cellBox(index: number, grid: DensityGrid) {
  const [x0, x1] = grid.xRange ?? [-1, 1]
  const [y0, y1] = grid.yRange ?? [0, 1]
  const row = Math.floor(index / grid.width)
  const column = index % grid.width
  const spanX = (x1 - x0) / grid.width
  const spanY = (y1 - y0) / grid.height
  const lowLeft = project({ x: x0 + column * spanX, y: y0 + row * spanY })
  const highRight = project({ x: x0 + (column + 1) * spanX, y: y0 + (row + 1) * spanY })
  return {
    x: Math.min(lowLeft.x, highRight.x),
    y: Math.min(lowLeft.y, highRight.y),
    width: Math.abs(highRight.x - lowLeft.x),
    height: Math.abs(highRight.y - lowLeft.y),
    centre: project({
      x: x0 + (column + 0.5) * spanX,
      y: y0 + (row + 0.5) * spanY,
    }),
  }
}

/** How much of the completed population the current frame represents. */
export function revealFraction(cumulative: number, total: number): number {
  if (!total) return 0
  return Math.min(1, Math.max(0, cumulative / total))
}

/**
 * Cell intensity: the artifact's own normalization (count / layer maximum),
 * eased for legibility and scaled by how much of the population is revealed.
 */
export function cellIntensity(
  count: number, maximum: number, reveal: number,
): number {
  if (maximum <= 0) return 0
  const normalized = Math.min(1, count / maximum)
  return Math.pow(normalized, 0.45) * (0.12 + 0.88 * reveal)
}

export function ContactDensityLayerView({ layer, grid, reveal, scoring }: {
  layer: ContactDensityLayer
  grid: DensityGrid
  reveal: number
  scoring?: boolean
}) {
  return (
    <g className={`pop-contact${scoring ? ' pop-contact--scoring' : ''}`} aria-hidden="true">
      {layer.sparseRawCounts.map(([index, count]) => {
        const box = cellBox(index, grid)
        const intensity = cellIntensity(count, layer.maximumCellCount, reveal)
        if (intensity <= 0.008) return null
        // Cells are drawn slightly overlapping so a sparse grid reads as a
        // density field rather than as detached squares. The count itself is
        // untouched; only the painted footprint is eased.
        const bleed = 0.18
        return (
          <rect
            key={`c${index}`}
            x={box.x - box.width * bleed}
            y={box.y - box.height * bleed}
            width={box.width * (1 + bleed * 2)}
            height={box.height * (1 + bleed * 2)}
            rx={Math.min(box.width, box.height) * 0.35}
            style={{ opacity: intensity }}
          />
        )
      })}
    </g>
  )
}

/** Decode one sparse ball-path tuple into its three grid cells. */
export function decodePathTuple(
  tuple: [number, number, number], grid: DensityGrid,
) {
  const cells = grid.width * grid.height
  const origin = Math.floor(tuple[0] / cells)
  const control = tuple[0] % cells
  return { origin, control, destination: tuple[1], count: tuple[2] }
}

export function pathDensityD(
  tuple: [number, number, number], grid: DensityGrid,
): string {
  const { origin, control, destination } = decodePathTuple(tuple, grid)
  const from = cellBox(origin, grid).centre
  const via = cellBox(control, grid).centre
  const to = cellBox(destination, grid).centre
  return `M${from.x} ${from.y} Q${via.x} ${via.y} ${to.x} ${to.y}`
}

export function BallPathDensityLayerView({ layer, grid, reveal }: {
  layer: PathDensityLayer
  grid: DensityGrid
  reveal: number
}) {
  const maximum = layer.reduce((top, tuple) => Math.max(top, tuple[2]), 0)
  return (
    <g className="pop-paths" aria-hidden="true">
      {layer.map((tuple, position) => {
        const intensity = cellIntensity(tuple[2], maximum, reveal)
        if (intensity <= 0.01) return null
        return (
          <path
            key={`p${position}`}
            d={pathDensityD(tuple, grid)}
            style={{ opacity: intensity * 0.75, strokeWidth: 1 + intensity * 2.6 }}
          />
        )
      })}
    </g>
  )
}

const SEGMENT_ENDS: Record<string, [string, string]> = {
  BATTER_BOX_TO_FIRST: ['HOME', 'FIRST'],
  BATTER_BOX_TO_SECOND: ['HOME', 'SECOND'],
  BATTER_BOX_TO_THIRD: ['HOME', 'THIRD'],
  BATTER_BOX_TO_HOME: ['HOME', 'HOME'],
  FIRST_TO_SECOND: ['FIRST', 'SECOND'],
  FIRST_TO_THIRD: ['FIRST', 'THIRD'],
  FIRST_TO_HOME: ['FIRST', 'HOME'],
  SECOND_TO_THIRD: ['SECOND', 'THIRD'],
  SECOND_TO_HOME: ['SECOND', 'HOME'],
  THIRD_TO_HOME: ['THIRD', 'HOME'],
}

/**
 * The base-path arc for one anonymous traffic segment.
 *
 * Only advancing segments have a route. `*_TO_OUT`, `*_TO_ITSELF` and stranded
 * segments are counts without a path, so they are reported in the legend
 * rather than drawn as movement that did not happen.
 */
export function segmentPathD(segment: string): string | null {
  const ends = SEGMENT_ENDS[segment]
  if (!ends) return null
  const [fromKey, toKey] = ends
  if (fromKey === toKey) return null
  const from = BASE_POINTS[fromKey]
  const to = BASE_POINTS[toKey]
  if (!from || !to) return null
  const midX = (from.x + to.x) / 2
  const midY = (from.y + to.y) / 2
  const mound = project({ x: 0, y: 0.2121 })
  return `M${from.x} ${from.y} Q${midX + (midX - mound.x) * 0.22} ${
    midY + (midY - mound.y) * 0.22} ${to.x} ${to.y}`
}

export function RunnerTrafficLayerView({ segments, reveal }: {
  segments: Record<string, number>
  reveal: number
}) {
  const drawable = Object.entries(segments).filter(([name]) => segmentPathD(name))
  const maximum = drawable.reduce((top, [, count]) => Math.max(top, count), 0)
  return (
    <g className="pop-runners" aria-hidden="true">
      {drawable.map(([name, count]) => {
        const intensity = cellIntensity(count, maximum, reveal)
        return (
          <path
            key={name}
            d={segmentPathD(name)!}
            style={{ opacity: 0.16 + intensity * 0.7, strokeWidth: 1.5 + intensity * 9 }}
          />
        )
      })}
    </g>
  )
}

/**
 * The filters the artifact actually carries.
 *
 * Each entry names one supplied one-dimensional layer. Nothing is combined:
 * there is no client-side cube, and a filter with no layer is not offered.
 */
export const POPULATION_FILTERS: Array<[string, string]> = [
  ['ALL', 'All plays'],
  ['TEAM_HOME', 'Home batting'],
  ['TEAM_AWAY', 'Away batting'],
  ['HITS', 'Hits'],
  ['BATTED_BALL_OUTS', 'Outs'],
  ['OUTCOME_SINGLE', 'Singles'],
  ['OUTCOME_DOUBLE', 'Doubles'],
  ['OUTCOME_TRIPLE', 'Triples'],
  ['OUTCOME_HOME_RUN', 'Home runs'],
  ['BATTED_BALL_GROUND_BALL', 'Ground balls'],
  ['BATTED_BALL_LINE_DRIVE', 'Line drives'],
  ['BATTED_BALL_FLY_BALL', 'Fly balls'],
  ['BATTED_BALL_POPUP', 'Popups'],
  ['SCORING', 'Scoring plays'],
  ['RESULT_HOME_WIN', 'Home-win simulations'],
  ['RESULT_AWAY_WIN', 'Away-win simulations'],
  ['RESULT_REGULATION', 'Regulation games'],
  ['RESULT_EXTRA_INNINGS', 'Extra-inning games'],
  ['RESULT_ONE_RUN', 'One-run games'],
]

/** The offered filters, narrowed to those the loaded artifact supports. */
export function availableFilters(
  layers: PopulationLayersPayload | null,
): Array<[string, string]> {
  if (!layers) return [POPULATION_FILTERS[0]]
  const contact = layers.contact_density.layers
  const paths = layers.path_density.layers
  return POPULATION_FILTERS.filter(([key]) => key in contact || key in paths)
}
