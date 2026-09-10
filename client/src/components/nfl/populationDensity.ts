/**
 * The shape of the WHOLE population, behind the handful of paths that exist.
 *
 * WHAT THIS FIXES
 * ---------------
 * A scenario simulates 1,000 games and publishes play paths for six. The field
 * could therefore draw about a thousand plays and had no way to show where the
 * other 151,000 ended up. `populationSpatial` is the producer's binned count of
 * every simulated play, so the field can paint that distribution as a density
 * behind the individual paths.
 *
 * The two layers answer different questions and must stay visually distinct:
 * a path is one play that actually happened in a published game; a density cell
 * is how often the engine put a play of that type in that part of the field
 * across the whole population. Drawing them the same way would invite reading
 * the density as more paths.
 *
 * COORDINATES
 * -----------
 * `start` is the producer's `yardline100`: yards to the goal line the
 * possessing team is attacking. It is offense-relative, so a bin means the same
 * thing whichever team has the ball and whichever way they are moving. The
 * field draws left-to-right in absolute yards, so a start bin covers BOTH
 * absolute positions -- x and 100-x -- and the density is mirrored onto both.
 * That is a property of the producer's own normalisation, not an assumption
 * added here.
 */
import type { NflPopulationSpatial } from '../../types/nfl'
import { familyOf, type PlayFamily } from './populationGeometry'

export interface DensityCell {
  /** Absolute field x of the bin's left edge, 0-100. */
  x: number
  width: number
  family: PlayFamily
  count: number
}

export interface DensityLayer {
  cells: DensityCell[]
  /** Largest single cell count, for scaling opacity. */
  peak: number
  /** Plays represented, which is every play in the population. */
  plays: number
  /** Plays that carry no field position and cannot be placed. */
  unplaced: number
}

const FAMILY_OF_TYPE = (playType: string): PlayFamily =>
  familyOf({ t: playType } as Parameters<typeof familyOf>[0])

/**
 * Fold the producer's cells into a per-family, per-yard-bin density.
 *
 * Cells whose `start` is not a number -- the explicit UNDERFLOW / OVERFLOW / NA
 * bins -- carry no placeable field position. They are counted in `unplaced`
 * rather than being dropped or forced to an edge, so the layer can say how much
 * of the population it is NOT showing.
 */
export function buildDensity(
  spatial: NflPopulationSpatial | null,
  families: ReadonlySet<PlayFamily> | null,
): DensityLayer | null {
  if (!spatial || !Array.isArray(spatial.cells) || spatial.cells.length === 0) return null

  const totals = new Map<string, DensityCell>()
  let plays = 0
  let unplaced = 0

  for (const cell of spatial.cells) {
    const family = FAMILY_OF_TYPE(cell.playType)
    if (families && !families.has(family)) continue
    plays += cell.count
    if (typeof cell.start !== 'number') {
      unplaced += cell.count
      continue
    }
    const key = `${family}:${cell.start}`
    const existing = totals.get(key)
    if (existing) existing.count += cell.count
    else totals.set(key, { x: cell.start, width: 5, family, count: cell.count })
  }

  const cells = [...totals.values()]
  if (cells.length === 0) return null
  return {
    cells,
    peak: Math.max(...cells.map((cell) => cell.count)),
    plays,
    unplaced,
  }
}
