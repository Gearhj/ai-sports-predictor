/**
 * The forecast a surface leads with, as the backend resolved it.
 *
 * The selection itself is made once on the server, by `ForecastSelector`, and
 * every surface reads the same answer. Nothing here re-decides which engine a
 * game belongs to — a client that made its own choice would be a second,
 * disagreeing selector, which is the problem this replaces.
 *
 * What this module adds is the presentation vocabulary: how to name a
 * forecast, how to describe a condition in a sentence a reader can act on, and
 * how to key a cache so switching model or snapshot cannot serve the other
 * one's numbers.
 */

export type ForecastAvailability =
  | 'AVAILABLE'
  | 'MISSING'
  | 'SUPERSEDED'
  | 'INVALIDATED'
  | 'NOT_PREGAME'
  | 'OUTSIDE_ERA'

export interface ForecastIdentity {
  engine_id: string
  engine_version: string | null
  model_label: string
  forecast_id: string
  snapshot_type: string | null
  snapshot_version: number | null
  /** When the newest evidence this forecast consumed was observed. */
  source_cutoff_utc: string | null
  /** When the producer published it. Never the page's refresh time. */
  published_utc: string | null
  recorded_utc: string | null
  home_win_probability: number | null
  away_win_probability: number | null
  expected_home_score: number | null
  expected_away_score: number | null
  availability: ForecastAvailability
  availability_detail: string
  /** False whenever availability is anything but AVAILABLE. */
  decision_eligible: boolean
}

export interface ForecastSelection {
  primary: ForecastIdentity | null
  comparison: ForecastIdentity[]
  controlling_engine: string | null
  selection_note: string | null
  /**
   * The engine whose numbers this surface is actually showing.
   *
   * Almost always `primary.engine_id`. It differs in one case: the selected
   * forecast has no probability at all (the controlling engine published
   * nothing for this game), so a surface falls back to the other engine's
   * published forecast. The chip must then read "Gen 1", not "V18" — naming
   * the selected engine above a number it did not produce is precisely the
   * mislabelling this field exists to prevent.
   */
  displayed_engine_id?: string | null
}

/** A short engine name for a chip: "V18", "Gen 1". */
export function engineShortName(engineId: string | null | undefined): string {
  if (!engineId) return 'Unknown model'
  if (engineId.includes('V18')) return 'V18'
  if (engineId.includes('BASELINE')) return 'Gen 1'
  if (engineId.includes('NFL_M1')) return 'M1'
  return engineId
}

/**
 * One sentence saying what a reader can and cannot do with this forecast.
 *
 * Empty for an available forecast: a reader does not need to be told that
 * nothing is wrong.
 */
export function availabilityNote(identity: ForecastIdentity | null | undefined): string {
  if (!identity || identity.availability === 'AVAILABLE') return ''
  if (identity.availability_detail) return identity.availability_detail
  switch (identity.availability) {
    case 'MISSING':
      return 'The controlling engine published no forecast for this game.'
    case 'SUPERSEDED':
      return 'A newer forecast from the same engine has replaced this one.'
    case 'NOT_PREGAME':
      return 'This forecast was published after the game began, so it is a replay.'
    case 'OUTSIDE_ERA':
      return 'This game predates the current engine, so it keeps the forecast that governed it.'
    default:
      return 'This forecast is not currently decision-eligible.'
  }
}

/** True when the forecast may be presented as the current, governing one. */
export function isCurrent(identity: ForecastIdentity | null | undefined): boolean {
  return Boolean(identity && identity.availability === 'AVAILABLE' && identity.decision_eligible)
}

/**
 * Cache identity for anything derived from one game's forecast.
 *
 * The engine, the forecast id and the snapshot revision are all in the key, so
 * switching model or moving to a different snapshot cannot serve the previous
 * one's numbers out of cache. `horizon` separates a current read from an
 * explicitly historical one for the same game.
 */
export function forecastCacheScope(input: {
  gameId: string
  identity?: ForecastIdentity | null
  horizon?: string | null
}): Record<string, string | number | null> {
  const { gameId, identity, horizon } = input
  return {
    game: gameId,
    engine: identity?.engine_id ?? 'none',
    forecast: identity?.forecast_id || 'none',
    revision: identity?.snapshot_version ?? 0,
    horizon: horizon ?? 'current',
  }
}

/**
 * What a market-comparison badge is actually about.
 *
 * A band is an informational comparison between one forecast and the market.
 * It is not a recorded decision, and the two were previously indistinguishable
 * on screen — so a Gen 1 band could sit beside a V18 probability with nothing
 * saying which model, or which side, it described.
 */
export function badgeAttribution(input: {
  identity: ForecastIdentity | null | undefined
  side: string | null
  displayedEngineId?: string | null
}): string {
  const engine = engineShortName(input.displayedEngineId ?? input.identity?.engine_id)
  const side = input.side ? `${input.side}, ` : ''
  return `Market comparison — ${side}${engine} forecast versus the sportsbook consensus. Not a recorded decision.`
}
