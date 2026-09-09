/**
 * Turning modeled play geometry into something you can watch.
 *
 * THE COORDINATE FRAME IS THE PRODUCER'S, NOT OURS
 * x is a global schematic field coordinate: 0 is the left goal line, 100 the
 * right one, and the orientation is fixed (HOME attacks left, AWAY attacks
 * right). y is a unitless renderer width coordinate from 0 to 1. It is NOT
 * lateral yards, NOT a route position and NOT air height. Everything below
 * only maps those two numbers onto the SVG; it never invents a third.
 *
 * WHAT THE ANIMATION IS ALLOWED TO SAY
 * Each segment carries its own declared kind, and the kind is what decides how
 * it is drawn: a modeled air curve looks like a throw, a ground segment looks
 * like a carry, a return runs the other way. None of this is measured
 * tracking, so the styling communicates the CATEGORY of the play, never a
 * claim about how a real ball moved.
 */

export type Interpolation = 'LINEAR' | 'POLYLINE' | 'CUBIC_BEZIER'

export interface PlaySegment {
  kind: string
  interpolation: Interpolation
  possession: 'HOME' | 'AWAY'
  start: [number, number]
  control: [number, number][]
  end: [number, number]
}

export interface PlayMarker {
  kind: string
  point: [number, number]
}

export interface PlayPath {
  kind: string
  direction: 'LEFT' | 'RIGHT'
  segments: PlaySegment[]
  markers: PlayMarker[]
}

export interface PlayState {
  possession: 'HOME' | 'AWAY'
  quarter: number
  clockSeconds: number
  gameSecondsRemaining: number
  down: number
  distance: number
  yardline100: number
  homeScore: number
  awayScore: number
  phase: 'SCRIMMAGE' | 'KICKOFF' | 'TRY' | 'FINAL'
}

export interface PlayOutcome {
  endYardline100?: number | null
  seconds: number
  homePointsAdded: number
  awayPointsAdded: number
  scoringType?: 'TD' | 'FG' | 'XP' | 'TWO_POINT' | 'SAFETY' | null
  turnover?: boolean
  turnoverDowns?: boolean
  turnoverYardline100?: number | null
  returnEndYardline100?: number | null
  fumble?: boolean
  fumbleLost?: boolean
  targetYardline100?: number | null
}

export interface Play {
  play_number: number
  drive_number: number
  play_type: string
  description: string
  before: PlayState
  after: PlayState
  outcome: PlayOutcome
  path: PlayPath
}

/* -- the SVG frame ------------------------------------------------------- */

/** The field spans x 100..1100 inside a 1200-unit viewBox; 100 units per 10 yards. */
export const FIELD_X0 = 100
export const FIELD_X1 = 1100
/** The playing surface, inset from the top and bottom rails. */
export const FIELD_Y0 = 74
export const FIELD_Y1 = 486

export function fieldX(x: number): number {
  return FIELD_X0 + Math.max(0, Math.min(100, x)) * ((FIELD_X1 - FIELD_X0) / 100)
}

export function fieldY(y: number): number {
  return FIELD_Y0 + Math.max(0, Math.min(1, y)) * (FIELD_Y1 - FIELD_Y0)
}

export type Pt = { x: number; y: number }

function px(point: [number, number]): Pt {
  return { x: fieldX(point[0]), y: fieldY(point[1]) }
}

/* -- path construction --------------------------------------------------- */

/** The SVG `d` for one segment, in screen units. */
export function segmentPath(segment: PlaySegment): string {
  const a = px(segment.start)
  const b = px(segment.end)
  const c = segment.control.map(px)
  if (segment.interpolation === 'CUBIC_BEZIER' && c.length === 2) {
    return `M ${a.x} ${a.y} C ${c[0].x} ${c[0].y} ${c[1].x} ${c[1].y} ${b.x} ${b.y}`
  }
  if (segment.interpolation === 'POLYLINE' && c.length) {
    return `M ${a.x} ${a.y} ${c.map((p) => `L ${p.x} ${p.y}`).join(' ')} L ${b.x} ${b.y}`
  }
  return `M ${a.x} ${a.y} L ${b.x} ${b.y}`
}

function cubic(a: Pt, c0: Pt, c1: Pt, b: Pt, t: number): Pt {
  const u = 1 - t
  const w = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t]
  return {
    x: w[0] * a.x + w[1] * c0.x + w[2] * c1.x + w[3] * b.x,
    y: w[0] * a.y + w[1] * c0.y + w[2] * c1.y + w[3] * b.y,
  }
}

/** Sample one segment into screen-space points, densely enough for a smooth curve. */
function samples(segment: PlaySegment, steps = 24): Pt[] {
  const a = px(segment.start)
  const b = px(segment.end)
  const c = segment.control.map(px)
  if (segment.interpolation === 'CUBIC_BEZIER' && c.length === 2) {
    return Array.from({ length: steps + 1 }, (_, i) => cubic(a, c[0], c[1], b, i / steps))
  }
  if (segment.interpolation === 'POLYLINE' && c.length) return [a, ...c, b]
  return [a, b]
}

export interface Traversal {
  /** Screen-space point at 0..1 of the whole play, at constant speed. */
  at(progress: number): Pt
  /** The partial `d` covering 0..progress, for drawing the ball's wake. */
  partial(progress: number): string
  length: number
}

/**
 * Walk the whole play at constant speed.
 *
 * Advancing t segment-by-segment would make a two-yard plunge take as long as
 * a fifty-yard bomb, which reads as broken. Sampling the whole path once and
 * indexing by cumulative length keeps the ball moving at one speed across
 * segments, which is what makes it look like a ball and not a slideshow.
 */
export function traverse(segments: PlaySegment[]): Traversal {
  const points: Pt[] = []
  segments.forEach((segment) => {
    const sampled = samples(segment)
    // Segments join end-to-start; drop the duplicated joint.
    points.push(...(points.length ? sampled.slice(1) : sampled))
  })
  if (points.length < 2) {
    const only = points[0] ?? { x: fieldX(50), y: fieldY(0.5) }
    return { at: () => only, partial: () => '', length: 0 }
  }
  const cumulative = [0]
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i].x - points[i - 1].x
    const dy = points[i].y - points[i - 1].y
    cumulative.push(cumulative[i - 1] + Math.hypot(dx, dy))
  }
  const length = cumulative[cumulative.length - 1]

  function locate(progress: number): { index: number; t: number } {
    if (length === 0) return { index: 0, t: 0 }
    const target = Math.max(0, Math.min(1, progress)) * length
    let low = 0
    let high = cumulative.length - 1
    while (low < high - 1) {
      const mid = (low + high) >> 1
      if (cumulative[mid] <= target) low = mid
      else high = mid
    }
    const span = cumulative[high] - cumulative[low]
    return { index: low, t: span === 0 ? 0 : (target - cumulative[low]) / span }
  }

  return {
    length,
    at(progress) {
      const { index, t } = locate(progress)
      const a = points[index]
      const b = points[Math.min(index + 1, points.length - 1)]
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    },
    partial(progress) {
      const { index, t } = locate(progress)
      const head = points.slice(0, index + 1)
      const a = points[index]
      const b = points[Math.min(index + 1, points.length - 1)]
      const tip = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
      return [...head, tip].map((p, i) => `${i ? 'L' : 'M'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
    },
  }
}

/* -- how each declared kind is drawn -------------------------------------- */

export interface SegmentStyle {
  /** 'offense' follows the team in possession; the others are fixed meanings. */
  tone: 'offense' | 'defense' | 'kick' | 'loss' | 'incomplete'
  width: number
  dash?: string
  /** Air segments are drawn above the surface with a lighter, brighter stroke. */
  air?: boolean
}

const SEGMENT_STYLE: Record<string, SegmentStyle> = {
  RUSH_GROUND: { tone: 'offense', width: 9 },
  QB_SCRAMBLE_GROUND: { tone: 'offense', width: 9, dash: '16 7' },
  KNEEL_GROUND: { tone: 'offense', width: 7, dash: '4 8' },
  MODELED_YAC_GROUND: { tone: 'offense', width: 9 },
  TRY_OUTCOME_GROUND: { tone: 'offense', width: 9 },
  PASS_MODELED_AIR_CURVE: { tone: 'offense', width: 7, dash: '18 9', air: true },
  PASS_OUTCOME_CURVE: { tone: 'incomplete', width: 6, dash: '12 10', air: true },
  SACK_BACKWARD: { tone: 'loss', width: 10 },
  SACK_NET_BACKWARD: { tone: 'loss', width: 10 },
  RETURN_GROUND: { tone: 'defense', width: 9, dash: '14 6' },
  KICKOFF_ARC: { tone: 'kick', width: 6, dash: '20 10', air: true },
  PUNT_ARC: { tone: 'kick', width: 6, dash: '20 10', air: true },
  FIELD_GOAL_ARC: { tone: 'kick', width: 7, dash: '22 8', air: true },
  TRY_KICK_ARC: { tone: 'kick', width: 6, dash: '16 8', air: true },
}

export function segmentStyle(kind: string): SegmentStyle {
  return SEGMENT_STYLE[kind] ?? { tone: 'offense', width: 8 }
}

/** Human wording for a segment, used in the legend and for screen readers. */
export const SEGMENT_LABEL: Record<string, string> = {
  RUSH_GROUND: 'Run',
  QB_SCRAMBLE_GROUND: 'Scramble',
  KNEEL_GROUND: 'Kneel-down',
  MODELED_YAC_GROUND: 'After the catch',
  TRY_OUTCOME_GROUND: 'Two-point try',
  PASS_MODELED_AIR_CURVE: 'Pass in the air',
  PASS_OUTCOME_CURVE: 'Pass outcome',
  SACK_BACKWARD: 'Sack',
  SACK_NET_BACKWARD: 'Sack (net)',
  RETURN_GROUND: 'Return',
  KICKOFF_ARC: 'Kickoff',
  PUNT_ARC: 'Punt',
  FIELD_GOAL_ARC: 'Field goal',
  TRY_KICK_ARC: 'Extra point',
}

export const MARKER_LABEL: Record<string, string> = {
  MODELED_CATCH: 'Catch',
  KICK_RECEIPT: 'Received',
  TD: 'Touchdown',
  FG: 'Field goal',
  XP: 'Extra point',
  TWO_POINT: 'Two-point',
  SAFETY: 'Safety',
  TURNOVER: 'Turnover',
  TURNOVER_ON_DOWNS: 'Turnover on downs',
  FUMBLE_LOST: 'Fumble lost',
  FUMBLE_RECOVERED: 'Fumble recovered',
  PENALTY: 'Penalty',
  PERIOD_BOUNDARY: 'End of period',
  SPIKE: 'Spike',
  SACK_NET_ZERO: 'Sack, no gain',
  INCOMPLETE_PASS: 'Incomplete',
}

export const PLAY_TYPE_LABEL: Record<string, string> = {
  RUSH: 'Run', QB_SNEAK: 'QB sneak', QB_SCRAMBLE: 'Scramble',
  PASS_COMPLETE: 'Completed pass', PASS_INCOMPLETE: 'Incomplete pass',
  SACK: 'Sack', INTERCEPTION: 'Interception', PUNT: 'Punt',
  FIELD_GOAL: 'Field goal', EXTRA_POINT: 'Extra point', TWO_POINT: 'Two-point try',
  KICKOFF: 'Kickoff', KNEEL: 'Kneel-down', SPIKE: 'Spike', PENALTY: 'Penalty',
  PERIOD_END: 'End of period',
}

/** Markers a viewer should not be allowed to miss. */
export const BIG_MARKERS = new Set([
  'TD', 'FG', 'XP', 'TWO_POINT', 'SAFETY', 'TURNOVER', 'TURNOVER_ON_DOWNS',
  'FUMBLE_LOST', 'FUMBLE_RECOVERED',
])

/* -- reading a play ------------------------------------------------------- */

export function isScoring(play: Play): boolean {
  return play.outcome.homePointsAdded > 0 || play.outcome.awayPointsAdded > 0
}

export function isTurnover(play: Play): boolean {
  return Boolean(play.outcome.turnover || play.outcome.turnoverDowns || play.outcome.fumbleLost)
}

export function isBigPlay(play: Play): boolean {
  return isScoring(play) || isTurnover(play) || play.play_type === 'SACK'
}

/** The celebration tone the scoreboard flashes, or null for an ordinary play. */
export function celebrationFor(play: Play): 'td' | 'fg' | 'turnover' | null {
  if (play.outcome.scoringType === 'TD') return 'td'
  if (play.outcome.scoringType === 'FG') return 'fg'
  if (isTurnover(play)) return 'turnover'
  return null
}

/** Quarter and mm:ss, straight from the state. Nothing is derived or guessed. */
export function clockLabel(state: PlayState): { period: string; clock: string } {
  const whole = Math.max(0, Math.ceil(state.clockSeconds))
  return {
    period: state.quarter >= 5 ? 'OT' : `Q${state.quarter}`,
    clock: `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`,
  }
}

/** "3rd & 7 at the ARI 34" -- the producer's own state, put into football words. */
export function situationLabel(state: PlayState, homeTeam: string, awayTeam: string): string {
  if (state.phase === 'FINAL') return 'Final'
  const offense = state.possession === 'HOME' ? homeTeam : awayTeam
  const defense = state.possession === 'HOME' ? awayTeam : homeTeam
  const spot = state.yardline100 > 50
    ? `${offense} ${Math.round(100 - state.yardline100)}`
    : state.yardline100 === 50 ? 'midfield' : `${defense} ${Math.round(state.yardline100)}`
  if (state.phase === 'KICKOFF') return `Kickoff · ${offense}`
  if (state.phase === 'TRY') return `Try · ${offense}`
  const ordinal = ['', '1st', '2nd', '3rd', '4th'][state.down] || ''
  if (!ordinal) return `${offense} ball at ${spot}`
  const toGo = state.distance >= state.yardline100 ? 'goal' : Math.round(state.distance)
  return `${ordinal} & ${toGo} at ${spot}`
}

/**
 * Where the line of scrimmage and the line to gain sit, in global x.
 *
 * yardline100 is distance to the opponent's goal for the team in possession,
 * so it has to be re-expressed in the fixed left-to-right frame before it can
 * be drawn. HOME attacks left, so HOME's distance-to-goal counts up from the
 * left goal line; AWAY's counts down from the right.
 */
export function scrimmageX(state: PlayState): number {
  return state.possession === 'HOME' ? state.yardline100 : 100 - state.yardline100
}

export function lineToGainX(state: PlayState): number | null {
  if (state.phase !== 'SCRIMMAGE' || !state.down) return null
  const gain = state.distance
  if (gain >= state.yardline100) return null // first and goal: the goal line is the line
  return state.possession === 'HOME' ? state.yardline100 - gain : 100 - state.yardline100 + gain
}
