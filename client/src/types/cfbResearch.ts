/**
 * The College Football research read, as the server sends it.
 *
 * Probabilities are FRACTIONS here, exactly as the artifact delivered them. The
 * single multiplication by 100 happens in the view. Typing them as fractions is
 * the cheapest way to stop a second multiplication appearing later.
 */

export interface CfbInterval {
  lower: number
  upper: number
}

export interface CfbWinProbability {
  method: 'joint' | 'ridge' | 'elo'
  label: string
  /** Fraction 0–1. */
  home: number
  /** Fraction 0–1. */
  away: number
  primary: boolean
}

export interface CfbPmf {
  values: number[]
  counts: number[]
  draw_count: number
  mass: number
}

export interface CfbIllustrativeTotal {
  line: number
  over: number
  push: number
  under: number
  drawCount?: number
  label: string
}

export interface CfbTotalScenario {
  line: number
  over: number
  push: number
  under: number
  draw_count: number
  counts: { over: number; push: number; under: number }
  integer_line: boolean
  push_possible: boolean
  label: string
  market_recorded: false
  note: string
}

export interface CfbResearchForecast {
  schema_version: string
  forecast_row_id: string
  forecast_id: string
  forecast_sha256: string
  package_index_sha256: string
  publication_receipt_sha256: string | null
  baseline_configuration: string
  engine_label: string
  game: {
    source_provider: string
    source_game_id: string
    home_team: string
    away_team: string
    home_source_team_id: string
    away_source_team_id: string
    matchup: string
    kickoff_utc: string
    neutral_site: boolean
    conference_competition: boolean
  }
  pick: { team: string; label: string; basis: string; is_a_wager: false }
  expected: {
    away_points: number
    home_points: number
    total: number
    home_margin: number
  }
  win_probabilities: CfbWinProbability[]
  primary_method: string
  primary_method_note: string
  explanation: string
  tie_probability: number
  draw_count: number
  simulation_precision_note: string
  simulation_mcse: Record<string, number> | null
  intervals: Record<string, Record<string, CfbInterval>>
  margin_sentence: string | null
  pmfs: Record<string, CfbPmf>
  illustrative_totals: CfbIllustrativeTotal[]
  clocks: {
    source_cutoff_utc: string
    forecast_created_utc: string
    exchange_published_utc: string | null
    imported_at_utc: string
  }
  limitations: string[]
  disclosure: string[]
  authority: {
    sport: string
    status: string
    bankroll: string
    policy: string
    market: string
    research_only: boolean
    note: string
  }
  attribution: string
  raw_source_policy: string
  technical: Record<string, unknown>
}

export interface CfbResearchBoard {
  schema_version: string
  sport: string
  label: string
  date: string | null
  forecasts: CfbResearchForecast[]
  count: number
  empty_state: string
  coverage_note: string
  authority: CfbResearchForecast['authority']
  attribution: string
}
