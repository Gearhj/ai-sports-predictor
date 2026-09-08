/**
 * Pure derivations for the V18 Diamond Theater.
 *
 * Everything here is a function of the persisted path. Nothing invents a
 * batter, a reliever, a pitch, a batted-ball location or a defensive play, and
 * nothing recomputes the simulation. Where the persisted record supports only
 * an aggregate transition, the derivation is deliberately conservative and
 * says so rather than guessing a specific baseball event.
 */
import type { ShadowPathEvent } from '../types/shadow'

/** Base occupancy from the persisted bitmask: 1 = first, 2 = second, 4 = third. */
export interface Bases {
  first: boolean
  second: boolean
  third: boolean
}

export function basesFromMask(mask: number): Bases {
  return {
    first: (mask & 1) === 1,
    second: (mask & 2) === 2,
    third: (mask & 4) === 4,
  }
}

export function occupiedBases(mask: number): number[] {
  return [1, 2, 3].filter((base) => (mask & (1 << (base - 1))) !== 0)
}

export function runnerCount(mask: number): number {
  return occupiedBases(mask).length
}

/** Public label for one persisted outcome class. Unknown classes pass through. */
const OUTCOME_LABEL: Record<string, string> = {
  STRIKEOUT: 'Strikeout',
  BIP_OUT: 'Ball in play — out',
  SINGLE: 'Single',
  DOUBLE: 'Double',
  TRIPLE: 'Triple',
  HOME_RUN: 'Home run',
  WALK: 'Walk',
  HIT_BY_PITCH: 'Hit by pitch',
  WALK_HBP_CI: 'Walk / hit by pitch',
  OTHER_REACH: 'Reached base',
}

export function outcomeLabel(outcome: string): string {
  return (
    OUTCOME_LABEL[outcome]
    ?? outcome.replaceAll('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase())
  )
}

/** Outcome classes that put the batter on base, per the persisted class alone. */
const REACHES_BASE = new Set([
  'SINGLE', 'DOUBLE', 'TRIPLE', 'WALK', 'HIT_BY_PITCH', 'WALK_HBP_CI', 'OTHER_REACH',
])

export function outcomeTone(outcome: string): 'out' | 'reach' | 'homer' {
  if (outcome === 'HOME_RUN') return 'homer'
  return REACHES_BASE.has(outcome) ? 'reach' : 'out'
}

// -- runner movement ------------------------------------------------------

/**
 * One runner token's position across a plate appearance.
 *
 * ``from`` and ``to`` are base numbers; 0 is home plate as an origin (the
 * batter) and 4 is home plate as a destination (a run). ``null`` means the
 * runner left the bases without scoring.
 */
export interface RunnerMove {
  id: string
  from: 0 | 1 | 2 | 3
  to: 1 | 2 | 3 | 4 | null
  scored: boolean
}

/**
 * Deterministic, conservative runner movement between two persisted states.
 *
 * The persisted record gives before/after occupancy and a run count, not who
 * moved where. This resolves that with one rule, applied identically every
 * time: runners keep their order (nobody passes anybody), the lead runner
 * takes the lead destination, and any runner displaced ahead of the remaining
 * destinations scores while runs remain — otherwise they simply leave the
 * bases. It is a visual reading of the aggregate transition, never a claim
 * about a real baserunning route.
 */
export function inferRunnerMoves(event: ShadowPathEvent): RunnerMove[] {
  const before = occupiedBases(event.baseMaskBefore).sort((a, b) => b - a)
  const after = occupiedBases(event.baseMaskAfter).sort((a, b) => b - a)
  const moves: RunnerMove[] = []

  // A home run empties the bases: everyone on, plus the batter, scores.
  if (event.outcome === 'HOME_RUN') {
    return [
      ...before.map((base) => ({ id: `r${base}`, from: base as 1 | 2 | 3, to: 4 as const, scored: true })),
      { id: 'batter', from: 0 as const, to: 4 as const, scored: true },
    ]
  }

  const destinations = [...after]
  let runsLeft = event.runsScored

  // The batter's own base is implied by the persisted outcome class, so it is
  // read rather than guessed. A batter who did not reach draws no token.
  const batterBase = BATTER_BASE[event.outcome]
  if (batterBase !== undefined) {
    const slot = destinations.indexOf(batterBase)
    if (slot >= 0) {
      destinations.splice(slot, 1)
      moves.push({ id: 'batter', from: 0, to: batterBase, scored: false })
    }
  }

  // Remaining destinations go to the runners already on, lead runner first, so
  // nobody passes anybody.
  for (const base of before) {
    const destination = destinations.shift()
    if (destination !== undefined && destination >= base) {
      moves.push({ id: `r${base}`, from: base as 1 | 2 | 3, to: destination as 1 | 2 | 3, scored: false })
      continue
    }
    if (destination !== undefined) destinations.unshift(destination)
    if (runsLeft > 0) {
      runsLeft -= 1
      moves.push({ id: `r${base}`, from: base as 1 | 2 | 3, to: 4, scored: true })
    } else {
      moves.push({ id: `r${base}`, from: base as 1 | 2 | 3, to: null, scored: false })
    }
  }

  return moves
}

/**
 * The base the batter ends on, by persisted outcome class alone.
 *
 * A single/walk/hit-by-pitch ends at first, a double at second, a triple at
 * third. Nothing else is assumed; an outcome that does not put the batter on
 * base is absent from this map and draws no batter token.
 */
const BATTER_BASE: Record<string, 1 | 2 | 3 | undefined> = {
  SINGLE: 1, WALK: 1, HIT_BY_PITCH: 1, WALK_HBP_CI: 1, OTHER_REACH: 1,
  DOUBLE: 2,
  TRIPLE: 3,
}

// -- event feed -----------------------------------------------------------

export interface FeedEntry {
  key: string
  pa: number
  inning: number
  half: 'TOP' | 'BOTTOM'
  headline: string
  detail: string | null
  tone: 'out' | 'reach' | 'homer' | 'score' | 'change' | 'inning'
}

const BASE_WORD: Record<number, string> = { 1: 'first', 2: 'second', 3: 'third' }

/** "first and third", "first, second and third", or null when empty. */
export function describeBases(mask: number): string | null {
  const bases = occupiedBases(mask).map((base) => BASE_WORD[base])
  if (bases.length === 0) return null
  if (bases.length === 1) return bases[0]
  return `${bases.slice(0, -1).join(', ')} and ${bases[bases.length - 1]}`
}

function runPhrase(runs: number): string {
  if (runs === 1) return 'One run scores'
  if (runs === 2) return 'Two runs score'
  if (runs === 3) return 'Three runs score'
  return `${runs} runs score`
}

/**
 * Plain baseball language for one persisted plate appearance.
 *
 * Only states the record supports: the outcome class, the out count, the
 * resulting base state and the runs. It never narrates how a ball was hit or
 * who fielded it.
 */
export function describeEvent(event: ShadowPathEvent): { headline: string; detail: string | null } {
  const label = outcomeLabel(event.outcome)
  const bases = describeBases(event.baseMaskAfter)
  const newOuts = event.outsAfter - event.outsBefore
  const parts: string[] = []

  if (event.outcome === 'HOME_RUN') {
    return {
      headline: `Home run — ${runPhrase(event.runsScored).toLowerCase()}`,
      detail: `${event.awayScoreAfter}–${event.homeScoreAfter} after the play.`,
    }
  }

  if (event.outsAfter >= 3) {
    parts.push('side retired')
  } else if (newOuts > 0) {
    parts.push(`${event.outsAfter} ${event.outsAfter === 1 ? 'out' : 'outs'}`)
  } else if (bases) {
    parts.push(occupiedBases(event.baseMaskAfter).length === 1
      ? `runner on ${bases}`
      : `runners on ${bases}`)
  } else {
    parts.push('bases empty')
  }

  const headline = `${label} — ${parts[0]}`
  const detail = event.runsScored > 0
    ? `${runPhrase(event.runsScored)}. ${event.awayScoreAfter}–${event.homeScoreAfter} after the play.`
    : null
  return { headline, detail }
}

/**
 * Completed events up to and including ``index``, newest first.
 *
 * Never returns anything after ``index``: the feed cannot reveal a play the
 * viewer has not reached.
 */
export function buildFeed(
  events: ShadowPathEvent[],
  index: number,
  limit = 8,
): FeedEntry[] {
  const entries: FeedEntry[] = []
  const upto = Math.min(index, events.length - 1)

  for (let position = upto; position >= 0 && entries.length < limit; position -= 1) {
    const event = events[position]
    const previous = position > 0 ? events[position - 1] : null
    const { headline, detail } = describeEvent(event)

    entries.push({
      key: `pa-${event.pa}`,
      pa: event.pa,
      inning: event.inning,
      half: event.half,
      headline,
      detail,
      tone: event.runsScored > 0
        ? (event.outcome === 'HOME_RUN' ? 'homer' : 'score')
        : outcomeTone(event.outcome),
    })

    if (event.pitchingChange && entries.length < limit) {
      entries.push({
        key: `change-${event.pa}`,
        pa: event.pa,
        inning: event.inning,
        half: event.half,
        headline: 'Pitching change — team bullpen enters',
        detail: 'Governed pitcher state; no reliever identity is persisted.',
        tone: 'change',
      })
    }

    if (previous && previous.inning !== event.inning && entries.length < limit) {
      entries.push({
        key: `inning-${event.pa}`,
        pa: event.pa,
        inning: previous.inning,
        half: previous.half,
        // Entering extras is announced once, on the crossing itself.
        headline: previous.inning <= 9 && event.inning > 9
          ? 'Game moves to extra innings'
          : `End of the ${ordinal(previous.inning)}`,
        detail: null,
        tone: 'inning',
      })
    }
  }

  return entries
}

export function ordinal(value: number): string {
  const names = [
    '', 'first', 'second', 'third', 'fourth', 'fifth',
    'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
  ]
  if (value < names.length) return names[value]
  const suffix = value % 10 === 1 && value % 100 !== 11 ? 'st'
    : value % 10 === 2 && value % 100 !== 12 ? 'nd'
      : value % 10 === 3 && value % 100 !== 13 ? 'rd' : 'th'
  return `${value}${suffix}`
}

// -- line score and summary ----------------------------------------------

export interface InningLine {
  inning: number
  awayRuns: number | null
  homeRuns: number | null
  awayTotal: number
  homeTotal: number
  pitchingChanges: number
  extra: boolean
}

/**
 * Inning-by-inning runs for the representative path.
 *
 * Runs are attributed from the persisted per-PA ``runsScored`` and the half
 * they occurred in. A half-inning that never appears in the path is null
 * rather than zero — a home team that never batted in the ninth did not score
 * nothing, it did not bat.
 */
export function buildLineScore(events: ShadowPathEvent[]): InningLine[] {
  const byInning = new Map<number, InningLine>()

  for (const event of events) {
    const line = byInning.get(event.inning) ?? {
      inning: event.inning,
      awayRuns: null, homeRuns: null,
      awayTotal: 0, homeTotal: 0,
      pitchingChanges: 0,
      extra: event.inning > 9,
    }
    if (event.half === 'TOP') line.awayRuns = (line.awayRuns ?? 0) + event.runsScored
    else line.homeRuns = (line.homeRuns ?? 0) + event.runsScored
    if (event.pitchingChange) line.pitchingChanges += 1
    line.awayTotal = event.awayScoreAfter
    line.homeTotal = event.homeScoreAfter
    byInning.set(event.inning, line)
  }

  return [...byInning.values()].sort((a, b) => a.inning - b.inning)
}

export interface PathSummary {
  plateAppearances: number
  innings: number
  extraInnings: boolean
  pitchingChanges: number[]
  scoringPlays: ShadowPathEvent[]
  awayFinal: number
  homeFinal: number
  /** Score changes that flipped or broke a tie — derived, never labelled causal. */
  turningPoints: ShadowPathEvent[]
}

export function summarizePath(events: ShadowPathEvent[]): PathSummary | null {
  if (events.length === 0) return null
  const last = events[events.length - 1]
  const scoring = events.filter((event) => event.runsScored > 0)

  const turningPoints = scoring.filter((event, position) => {
    const index = events.indexOf(event)
    const previous = index > 0 ? events[index - 1] : null
    if (!previous) return true
    const wasLeader = Math.sign(previous.awayScoreAfter - previous.homeScoreAfter)
    const nowLeader = Math.sign(event.awayScoreAfter - event.homeScoreAfter)
    return wasLeader !== nowLeader || position === scoring.length - 1
  })

  return {
    plateAppearances: events.length,
    innings: last.inning,
    extraInnings: last.inning > 9,
    pitchingChanges: events.filter((event) => event.pitchingChange).map((event) => event.pa),
    scoringPlays: scoring,
    awayFinal: last.awayScoreAfter,
    homeFinal: last.homeScoreAfter,
    turningPoints,
  }
}

/** Broadcast status for the simulated game at this point in the replay. */
export function replayStatus(
  events: ShadowPathEvent[],
  index: number,
): 'PREGAME' | 'IN_PROGRESS' | 'EXTRA_INNINGS' | 'FINAL' {
  if (events.length === 0) return 'PREGAME'
  if (index >= events.length - 1) return 'FINAL'
  return events[index].inning > 9 ? 'EXTRA_INNINGS' : 'IN_PROGRESS'
}
