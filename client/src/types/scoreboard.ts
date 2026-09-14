/**
 * Observed game state, as the shared scoreboard service publishes it.
 *
 * Absence is modelled, never defaulted. `away_score` is null before a game
 * starts rather than 0; `clock_seconds_remaining` is null because no current
 * source publishes a clock, and `clock_available` says which of "no time left"
 * and "no clock exists" is true. A period a team did not bat in is null with
 * `home_played: false`, because a scoreless inning and an unplayed one are
 * different facts and the difference is the whole point of a linescore.
 */

export type ScoreboardStatus =
  | 'SCHEDULED' | 'IN_PROGRESS' | 'DELAYED' | 'SUSPENDED'
  | 'POSTPONED' | 'CANCELLED' | 'FINAL' | 'UNKNOWN'

/** How much to trust this reading. Never derived in the browser. */
export type FreshnessState =
  | 'CURRENT' | 'DELAYED' | 'UNAVAILABLE' | 'SETTLED_STATE' | 'NOT_STARTED'

export interface ScoreboardPeriod {
  number: number | null
  label: string | null
  away: number | null
  home: number | null
  /** False means the half/period was not played, not that it was scoreless. */
  away_played: boolean
  home_played: boolean
}

export interface ScoreboardSituation {
  outs: number | null
  balls: number | null
  strikes: number | null
  bases_occupied: string[]
  runners: Record<string, string | null>
}

export interface GameScoreboard {
  schema_version: 'AISP_GAME_SCOREBOARD_V1'
  game_id: string
  league_code: string | null
  available: boolean
  unavailable_reason?: string
  teams: {
    home?: { abbreviation: string; name: string }
    away?: { abbreviation: string; name: string }
  } | null
  status?: ScoreboardStatus
  /** The source's own words: "In Progress", "Warmup", "Delayed", "Game Over". */
  status_detail?: string | null
  is_final?: boolean
  is_live?: boolean
  away_score?: number | null
  home_score?: number | null
  period?: {
    number: number | null
    label: string | null
    state: string | null
    in_break: boolean | null
  }
  clock_seconds_remaining?: number | null
  /** False where the source publishes no clock at all. */
  clock_available?: boolean
  situation?: ScoreboardSituation | null
  periods?: ScoreboardPeriod[]
  periods_complete?: boolean
  periods_reconcile?: boolean | null
  periods_note?: string | null
  freshness?: {
    state: FreshnessState
    detail: string
    checked_age_seconds: number
    /** False for every current source: none publishes a score timestamp. */
    source_timestamp_available: boolean
    source_latency: 'known' | 'unknown'
  }
  source?: {
    name: string
    mode: string
    source_game_id: string | null
    /** When the SOURCE says the score held. Null when it does not say. */
    source_observed_at: string | null
    /** When AISP received the bytes. Never presented as the score's own time. */
    retrieved_at: string | null
    last_checked_at: string | null
    timestamp_note: string
  }
  settlement_note?: string
}

export interface ScoreboardPayload {
  schema_version: 'AISP_GAME_SCOREBOARD_V1'
  generated_at: string
  games: Record<string, GameScoreboard>
  requested: number
  available: number
  live: number
  refresh: {
    source: 'AISP_CACHE'
    auto_refresh_seconds: number
    manual_cooldown_seconds: number
    /** Always 0: reading this endpoint never reaches a provider. */
    provider_calls_on_refresh: number
    collection_cadence: Record<string, string>
  }
}

export interface ScoreboardCoverage {
  schema_version: 'AISP_SCOREBOARD_COVERAGE_V1'
  generated_at: string
  note: string
  sports: Record<string, {
    source: string | null
    cost: string | null
    live_score: boolean
    period_breakdown: boolean
    period_label: string
    situation: boolean
    clock: boolean
    source_score_timestamp: boolean
    player_stats: boolean
    target_cadence_seconds: number | null
    cadence_note: string
    available_fields: string[]
    unavailable_fields: Record<string, string>
    measured: {
      observations_last_24h: number
      last_checked_at: string | null
      last_check_age_seconds: number | null
      observations_with_source_timestamp: number
    }
  }>
}
