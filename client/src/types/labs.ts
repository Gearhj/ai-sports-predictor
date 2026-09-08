/**
 * Interactive Labs read model (AISP_INTERACTIVE_LABS_V1).
 *
 * A game appears here only when its own persisted simulation can be replayed,
 * and `v18_available` is per game — never a property of the league.
 */
export interface LabsGame {
  game_id: string
  label: string
  home_team: string | null
  away_team: string | null
  starts_at: string | null
  slate_date: string | null
  event_status: string
  is_final: boolean
  is_pregame: boolean
  home_score: number | null
  away_score: number | null
  gen1_available: boolean
  gen1_href: string | null
  v18_available: boolean
  v18_href: string | null
  v18_forecast_id: string | null
  v18_path_events: number
  v18_forecast_at: string | null
  v18_object_sha256: string | null
  v18_views: {
    broadcast: boolean
    geometry: boolean
    population: boolean
  }
  v18_skip_category: string | null
  v18_skip_reason: string | null
  analysis_href: string
}

export interface LabsGames {
  schema_version: 'AISP_INTERACTIVE_LABS_V1'
  league: string
  generated_at: string
  eligible_count: number
  v18_count: number
  v18_skip_count: number
  featured_slate_date: string | null
  featured_v18_count: number
  featured_v18_skip_count: number
  games: LabsGame[]
  note: string
}
