/**
 * One status-to-style mapping, used by cards, list rows and legends.
 *
 * Every surface used to decide its own colours from its own string test, so
 * the same status could read differently in two places and a status nobody
 * had thought about fell through to an unstyled default. This is the single
 * table; nothing else should branch on a status string to pick a colour.
 *
 * The tones are deliberately few. A reader learns three: grey is "nothing to
 * act on", yellow is "watching", green is "the model disagrees with the market
 * enough to matter". STANDARD EDGE is a quieter green than STRONG EDGE, not a
 * fourth colour and not relabelled as STRONG — it is a real, distinct status
 * and keeps its own name.
 */

/** Market-disagreement bands published by `classify_opportunity`. */
export type OpportunityLabel = 'NO BET' | 'WATCH' | 'STANDARD EDGE' | 'STRONG EDGE'

/** What a persisted decision recorded. A different question from the band. */
export type DecisionLabel =
  | 'QUALIFIED_BET' | 'PROVISIONAL_EDGE' | 'EXTREME_DISAGREEMENT' | 'WATCH' | 'NO_BET'

export type StatusTone = 'neutral' | 'watch' | 'edge' | 'strong' | 'decided' | 'closed'

export interface StatusStyle {
  tone: StatusTone
  /** The class the pill carries. One per tone. */
  className: string
  /** What the status means, in a sentence a reader can act on. */
  title: string
}

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: 'signal-pill signal-pill--neutral',
  watch: 'signal-pill signal-pill--watch',
  edge: 'signal-pill signal-pill--edge',
  strong: 'signal-pill signal-pill--strong',
  decided: 'signal-pill signal-pill--decided',
  closed: 'signal-pill signal-pill--closed',
}

const OPPORTUNITY: Record<string, { tone: StatusTone; title: string }> = {
  'NO BET': {
    tone: 'neutral',
    title: 'The model and the market are within the minimum edge. Nothing to act on.',
  },
  WATCH: {
    tone: 'watch',
    title: 'The model differs from the market, but by less than the standard band.',
  },
  'STANDARD EDGE': {
    tone: 'edge',
    title: 'The model differs from the market by at least the standard band.',
  },
  'STRONG EDGE': {
    tone: 'strong',
    title: 'The model differs from the market by at least the strong band.',
  },
}

const DECISION: Record<string, { tone: StatusTone; title: string; label: string }> = {
  QUALIFIED_BET: {
    tone: 'decided', label: 'Qualified',
    title: 'A position was recorded for this game under the governing policy.',
  },
  PROVISIONAL_EDGE: {
    tone: 'watch', label: 'Provisional edge',
    title: 'An edge was recorded but the evidence did not qualify it.',
  },
  EXTREME_DISAGREEMENT: {
    tone: 'watch', label: 'Extreme disagreement',
    title: 'The gap is large enough to be treated as suspect rather than actionable.',
  },
  WATCH: {
    tone: 'watch', label: 'Watch',
    title: 'Recorded as watching; no position was permitted.',
  },
  NO_BET: {
    tone: 'neutral', label: 'No bet',
    title: 'A decision was recorded and it was not to take a position.',
  },
}

/**
 * The style for a market-disagreement band.
 *
 * An unknown label keeps its own text and gets the neutral tone: a status this
 * table has not met is not promoted into a green one.
 */
export function opportunityStyle(label: string | null | undefined): StatusStyle {
  const key = (label ?? '').trim().toUpperCase()
  const entry = OPPORTUNITY[key]
  if (!entry) {
    return {
      tone: 'neutral',
      className: TONE_CLASS.neutral,
      title: 'Status published by the model without a known band.',
    }
  }
  return { tone: entry.tone, className: TONE_CLASS[entry.tone], title: entry.title }
}

export function decisionStyle(state: string | null | undefined): StatusStyle & { label: string } {
  const key = (state ?? '').trim().toUpperCase().replace(/\s+/g, '_')
  const entry = DECISION[key]
  if (!entry) {
    return {
      tone: 'neutral', className: TONE_CLASS.neutral, label: state ?? 'No decision recorded',
      title: 'No persisted decision is recorded for this game.',
    }
  }
  return {
    tone: entry.tone, className: TONE_CLASS[entry.tone], label: entry.label, title: entry.title,
  }
}

export function closedStyle(): StatusStyle {
  return {
    tone: 'closed',
    className: TONE_CLASS.closed,
    title: 'The game has started. This is the last pregame reading, kept for the record.',
  }
}

/**
 * Whether a game has passed the point where a pregame band means anything.
 *
 * A started or finished game still carries the band it had before first pitch.
 * That reading is history, and presenting it with the same weight as a live
 * opportunity is the difference between a record and an invitation.
 */
export function hasStarted(status: string | null | undefined): boolean {
  const value = (status ?? '').trim().toUpperCase()
  return value === 'LIVE' || value === 'IN_PROGRESS' || value === 'FINAL'
    || value === 'COMPLETED' || value === 'CLOSED' || value === 'SUSPENDED'
    || value === 'POSTPONED'
}

/**
 * Name the side a market-disagreement reading is about.
 *
 * The row renders a *home-side* signed difference while the band describes
 * whichever side the model prefers. Those disagree in sign whenever the edge is
 * on the away team, which is exactly how "STANDARD EDGE" came to sit beside a
 * negative number with nothing saying which team either referred to.
 *
 * Returns the edge side's own figure when the backend published one, because
 * that is the number the band was computed from.
 */
export function edgeDescription(input: {
  edgeSide: 'home' | 'away' | null | undefined
  /** Backend edge, in percentage points, against `edgeSide`. */
  modelEdgePoints: number | null | undefined
  /** Home-side model minus market, in percentage points. */
  homeEdgePoints: number | null | undefined
  homeAbbrev: string | null | undefined
  awayAbbrev: string | null | undefined
}): { text: string; side: string | null } {
  const { edgeSide, modelEdgePoints, homeEdgePoints, homeAbbrev, awayAbbrev } = input
  const side = edgeSide === 'home' ? homeAbbrev : edgeSide === 'away' ? awayAbbrev : null

  if (side && modelEdgePoints != null && Number.isFinite(modelEdgePoints)) {
    // The published edge is already signed against its own side.
    return { text: `${side} ${signed(modelEdgePoints)} pp vs market`, side }
  }
  if (homeEdgePoints != null && Number.isFinite(homeEdgePoints) && homeAbbrev) {
    // No published side: say whose number this is rather than leaving a bare
    // signed value the reader has to attribute.
    return { text: `${homeAbbrev} ${signed(homeEdgePoints)} pp vs market`, side: homeAbbrev }
  }
  return { text: 'No comparable price', side: null }
}

function signed(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}`
}
