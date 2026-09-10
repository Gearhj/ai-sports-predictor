/**
 * Drawing a POPULATION of modeled plays, rather than one play at a time.
 *
 * WHAT IS ON SCREEN, EXACTLY
 * --------------------------
 * A scenario simulates 1,000 games. Six of them carry play paths. Everything
 * here draws those six -- roughly 850 to 1,100 plays -- and the caller is
 * responsible for saying so. Nothing in this module interpolates, resamples or
 * synthesises a play that the producer did not publish, and nothing pools two
 * scenarios or two matchups to make the field look busier.
 *
 * THE COORDINATE FRAME IS THE PRODUCER'S
 * --------------------------------------
 * `x` is a global schematic field coordinate: 0 is the left goal line, 100 the
 * right. `y` is a unitless width coordinate from 0 to 1 -- NOT lateral yards,
 * NOT a route position, NOT air height. The producer re-centres each play
 * laterally and says outright that cross-play lateral continuity is not
 * asserted, so the y spread here is layout, not a claim about where on the
 * field anything happened.
 *
 * WHY CANVAS
 * ----------
 * Trails persist: a play is drawn once and stays. With a thousand paths that is
 * a thousand retained DOM nodes in SVG and one bitmap in canvas, and the whole
 * point of the view is watching them pile up. Canvas also lets "clear" mean
 * clear.
 */

export type PopSegment = {
  k: string
  i: 'LINEAR' | 'POLYLINE' | 'CUBIC_BEZIER'
  s: [number, number]
  c: [number, number][]
  e: [number, number]
}

export type PopMarker = { k: string; p: [number, number] }

export interface PopulationPlay {
  /** Index of the representative game this play belongs to (0-5). */
  g: number
  n: number
  t: string
  poss: 'HOME' | 'AWAY' | null
  dir: 'LEFT' | 'RIGHT'
  dn: number | null
  di: number | null
  yl: number | null
  q: number | null
  yds: number | null
  sc: string | null
  scTeam: string | null
  to: boolean
  segs: PopSegment[]
  mk: PopMarker[]
}

/* -- classification ------------------------------------------------------- */

/**
 * The three families the field colours by. Derived from the producer's own
 * `playType`; no play is reclassified and none is invented.
 */
export type PlayFamily = 'PASS' | 'RUN' | 'KICK' | 'OTHER'

const PASS_TYPES = new Set(['PASS_COMPLETE', 'PASS_INCOMPLETE', 'INTERCEPTION', 'SACK'])
const RUN_TYPES = new Set(['RUSH', 'QB_SCRAMBLE', 'QB_SNEAK', 'KNEEL'])
const KICK_TYPES = new Set(['KICKOFF', 'PUNT', 'FIELD_GOAL', 'EXTRA_POINT'])

export function familyOf(play: PopulationPlay): PlayFamily {
  if (PASS_TYPES.has(play.t)) return 'PASS'
  if (RUN_TYPES.has(play.t)) return 'RUN'
  if (KICK_TYPES.has(play.t)) return 'KICK'
  return 'OTHER'
}

/** Segments the producer marks as airborne, which are drawn dashed. */
const AIRBORNE_KINDS = new Set([
  'PASS_MODELED_AIR_CURVE', 'INCOMPLETE_PASS_TARGET_CURVE', 'PASS_OUTCOME_CURVE',
  'KICKOFF_ARC', 'PUNT_ARC', 'FIELD_GOAL_ARC', 'TRY_KICK_ARC',
])

export function isAirborne(segment: PopSegment): boolean {
  return AIRBORNE_KINDS.has(segment.k)
}

/* -- palette -------------------------------------------------------------- */

/**
 * Colour carries the play family and nothing else. It is deliberately not a
 * quality or success signal: an incomplete pass and a 40-yard completion are
 * the same cyan, because the field is showing what KIND of play the engine
 * generated, not how well it went.
 */
export const POPULATION_PALETTE = {
  PASS: '#5ad4e6',
  RUN: '#e8b44a',
  KICK: '#8fa3b8',
  OTHER: '#7d8ea1',
  TURNOVER: '#ff4fd8',
  SCORE: '#7cf0a8',
} as const

/* -- sampling ------------------------------------------------------------- */

export type Pt = { x: number; y: number }

/** Where the play's own path is, in unit space, sampled for drawing. */
function sampleSegment(segment: PopSegment, steps = 18): Pt[] {
  const [sx, sy] = segment.s
  const [ex, ey] = segment.e
  if (segment.i === 'CUBIC_BEZIER' && segment.c.length === 2) {
    const [c1x, c1y] = segment.c[0]
    const [c2x, c2y] = segment.c[1]
    const points: Pt[] = []
    for (let i = 0; i <= steps; i += 1) {
      const t = i / steps
      const u = 1 - t
      points.push({
        x: u * u * u * sx + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t * t * t * ex,
        y: u * u * u * sy + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t * t * t * ey,
      })
    }
    return points
  }
  if (segment.i === 'POLYLINE' && segment.c.length > 0) {
    return [{ x: sx, y: sy }, ...segment.c.map(([x, y]) => ({ x, y })), { x: ex, y: ey }]
  }
  return [{ x: sx, y: sy }, { x: ex, y: ey }]
}

export function samplePlay(play: PopulationPlay): Pt[][] {
  return play.segs.map((segment) => sampleSegment(segment))
}

/* -- filtering ------------------------------------------------------------ */

export type DownGroup = 'ALL' | 'FIRST' | 'SECOND' | 'THIRD' | 'FOURTH'
export type FieldZone = 'ALL' | 'OWN' | 'MIDDLE' | 'RED'
export type FamilyFilter = 'BOTH' | 'PASS' | 'RUN'
export type TeamFilter = 'BOTH' | 'HOME' | 'AWAY'

export interface PopulationFilters {
  family: FamilyFilter
  team: TeamFilter
  down: DownGroup
  zone: FieldZone
  scoringOnly: boolean
  turnoversOnly: boolean
  games: Set<number> | null
}

export const DEFAULT_FILTERS: PopulationFilters = {
  family: 'BOTH', team: 'BOTH', down: 'ALL', zone: 'ALL',
  scoringOnly: false, turnoversOnly: false, games: null,
}

/**
 * Zones use `yardline100` -- the producer's distance to the OPPONENT goal for
 * the team in possession -- so "red zone" means the same thing for both teams.
 */
function inZone(play: PopulationPlay, zone: FieldZone): boolean {
  if (zone === 'ALL') return true
  if (play.yl == null) return false
  if (zone === 'RED') return play.yl <= 20
  if (zone === 'MIDDLE') return play.yl > 20 && play.yl <= 60
  return play.yl > 60
}

function inDownGroup(play: PopulationPlay, group: DownGroup): boolean {
  if (group === 'ALL') return true
  // Down 0 is a kickoff or a try -- it has no down, so it is not "first down".
  const wanted = { FIRST: 1, SECOND: 2, THIRD: 3, FOURTH: 4 }[group]
  return play.dn === wanted
}

export function matches(play: PopulationPlay, filters: PopulationFilters): boolean {
  if (filters.games && !filters.games.has(play.g)) return false
  if (filters.scoringOnly && !play.sc) return false
  if (filters.turnoversOnly && !play.to) return false
  if (filters.team !== 'BOTH' && play.poss !== filters.team) return false
  if (filters.family !== 'BOTH' && familyOf(play) !== filters.family) return false
  if (!inDownGroup(play, filters.down)) return false
  if (!inZone(play, filters.zone)) return false
  return true
}

/* -- linked statistics ---------------------------------------------------- */

/**
 * Rates over whatever is currently selected, each with its own denominator.
 *
 * These are recomputed in the browser from the DRAWN plays. They are not the
 * 1,000-game population's rates, which arrive precomputed and are displayed
 * beside these under their own heading. Mixing the two would let a six-game
 * sample wear a thousand-game denominator.
 */
export interface DrawnStats {
  plays: number
  scrimmage: number
  passAttempts: number
  completions: number
  completionRate: number | null
  rushes: number
  sacks: number
  turnovers: number
  turnoverRate: number | null
  scoring: number
  scoringRate: number | null
  passShare: number | null
  yards: { count: number; mean: number | null; median: number | null; p05: number | null; p95: number | null }
  histogram: { bucket: number; count: number }[]
}

const SCRIMMAGE = new Set([
  'RUSH', 'PASS_COMPLETE', 'PASS_INCOMPLETE', 'SACK', 'QB_SCRAMBLE',
  'QB_SNEAK', 'KNEEL', 'SPIKE', 'INTERCEPTION',
])
/** A pass ATTEMPT excludes sacks and scrambles, matching the producer. */
const ATTEMPTS = new Set(['PASS_COMPLETE', 'PASS_INCOMPLETE', 'INTERCEPTION'])
const RUSHES = new Set(['RUSH', 'QB_SCRAMBLE', 'QB_SNEAK'])

function quantile(sorted: number[], fraction: number): number | null {
  if (sorted.length === 0) return null
  const index = Math.min(sorted.length - 1, Math.max(0, Math.round(fraction * (sorted.length - 1))))
  return sorted[index]
}

export function drawnStats(plays: PopulationPlay[]): DrawnStats {
  const scrimmage = plays.filter((p) => SCRIMMAGE.has(p.t))
  const attempts = plays.filter((p) => ATTEMPTS.has(p.t))
  const completions = plays.filter((p) => p.t === 'PASS_COMPLETE').length
  const rushes = plays.filter((p) => RUSHES.has(p.t)).length
  const turnovers = plays.filter((p) => p.to).length
  const scoring = plays.filter((p) => p.sc).length
  const gains = scrimmage.map((p) => p.yds).filter((v): v is number => v != null)
  const sorted = [...gains].sort((a, b) => a - b)

  // Yard buckets wide enough to be readable and narrow enough to show the
  // spike at zero and the long right tail the engine actually produces.
  const buckets = new Map<number, number>()
  gains.forEach((value) => {
    const bucket = Math.max(-10, Math.min(40, Math.floor(value / 5) * 5))
    buckets.set(bucket, (buckets.get(bucket) ?? 0) + 1)
  })

  const passRunTotal = attempts.length + rushes
  return {
    plays: plays.length,
    scrimmage: scrimmage.length,
    passAttempts: attempts.length,
    completions,
    completionRate: attempts.length ? completions / attempts.length : null,
    rushes,
    sacks: plays.filter((p) => p.t === 'SACK').length,
    turnovers,
    turnoverRate: scrimmage.length ? turnovers / scrimmage.length : null,
    scoring,
    scoringRate: scrimmage.length ? scoring / scrimmage.length : null,
    passShare: passRunTotal ? attempts.length / passRunTotal : null,
    yards: {
      count: gains.length,
      mean: gains.length ? gains.reduce((a, b) => a + b, 0) / gains.length : null,
      median: quantile(sorted, 0.5),
      p05: quantile(sorted, 0.05),
      p95: quantile(sorted, 0.95),
    },
    histogram: [...buckets.entries()].sort((a, b) => a[0] - b[0]).map(([bucket, count]) => ({ bucket, count })),
  }
}

/* -- lateral layout ------------------------------------------------------- */

/**
 * A deterministic lateral lane for one play.
 *
 * The producer re-centres every play laterally and states outright that
 * "cross-play lateral continuity is not asserted" -- so `y` says something
 * about the SHAPE of a single play and nothing whatsoever about where on the
 * field it happened relative to any other play. Drawn as supplied, eight
 * hundred plays therefore stack into one unreadable band down the middle.
 *
 * Spreading them across lanes destroys no information, because there is no
 * cross-play lateral information to destroy. It is a layout decision and the
 * view labels it as one. What it must NOT do is vary between renders, or the
 * same play would jump each time the filters change -- so the offset is a hash
 * of the play's own identity, not a random number.
 */
export function laneOffset(play: PopulationPlay): number {
  // FNV-1a over the play's stable identity: which representative game, and
  // which play within it.
  let hash = 2166136261
  const key = `${play.g}:${play.n}`
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  const unit = ((hash >>> 0) % 10000) / 10000
  // Keep the band clear of the sidelines so a play's own lateral shape still
  // has room to read.
  return (unit - 0.5) * 0.72
}
