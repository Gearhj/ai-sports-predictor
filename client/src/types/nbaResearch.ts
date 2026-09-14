/**
 * Imported NBA research forecasts.
 *
 * Ridge points and Elo probability are separate model outputs and stay under
 * separate names. Probabilities arrive as fractions; the view multiplies by 100
 * exactly once. Distributions are unavailable and nothing derives an
 * Over/Under from the expected total.
 */
export interface NbaResearchForecast {
  forecast_id: string
  forecast_sha256: string
  engine_id: string
  configuration_identity: string | null
  game_id: string
  source_game_id: string
  teams: { home?: { abbreviation: string }; away?: { abbreviation: string } }
  captured_schedule: {
    tipoff_utc: string
    game_date: string
    source_revision: string | null
  }
  current_schedule: { tipoff_utc: string | null; differs_from_captured: boolean }
  ridge_expected_points: {
    away: number | null
    home: number | null
    total: number | null
    home_margin: number | null
    model: string
    units: string
  }
  elo_win_probability: {
    away: number | null
    home: number | null
    tie: number | null
    model: string
    method: string | null
    units: string
  }
  distributions: { draw_count: number; status: string; note: string }
  clocks: Record<string, string | null>
  label: string | null
  limitations: unknown
  rest_support: Record<string, unknown> | null
  bindings: Record<string, unknown> | null
  authority: Record<string, boolean> | null
  superseded_by: string | null
}

export interface NbaResearchBoard {
  schema_version: 'AISP_NBA_RESEARCH_FORECASTS_V1'
  forecasts: NbaResearchForecast[]
  note: string
}
