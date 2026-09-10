/** Shapes returned by /research/nfl-prospective. */

export interface ProspectiveTeam {
  abbreviation: string
  name: string
}

export interface IntervalLevel {
  level: number
  lower: number
  upper: number
  contains: boolean
  width: number
}

export interface ProspectiveGame {
  evaluation_id: string
  event_id: string
  canonical_game_id: string | null
  home: ProspectiveTeam | null
  away: ProspectiveTeam | null
  kickoff_utc: string | null
  final: { home: number; away: number }
  forecast: {
    identity: string
    snapshot_type: string | null
    snapshot_version: number | null
    frozen_published_utc: string | null
    evidence_utc: string | null
    imported_utc: string | null
    hours_before_kickoff: number | null
    engine_id: string
    engine_version: string
  }
  result: {
    identity: string
    source: string
    observed_utc: string | null
    /** Provenance rank; 1 governed provider, 2 automated feed, 3 operator. */
    tier: number
    /** "Provider-verified" / "Automated source" / "Operator-entered". */
    label: string
    note: string
    /** True only for the governed provider. An automated feed is not this. */
    provider_verified: boolean
    /** False when a human keyed the score in; the page must say so. */
    automated: boolean
    /** Earlier evaluations this one replaced. Usually zero. */
    superseded_count: number
  }
  outcome: {
    actual: 'HOME' | 'AWAY' | 'TIE'
    predicted: 'HOME' | 'AWAY' | 'TIE'
    winner_correct: boolean
    probability_of_actual: number
    log_loss: number
    brier_score: number
    published_probabilities: {
      home: number; away: number; tie: number; mass_before_normalising: number
    } | null
  }
  scores: {
    predicted_home: number | null
    predicted_away: number | null
    home_error: number | null
    away_error: number | null
    team_score_mae: number | null
  }
  margin: { predicted: number | null; actual: number; error: number | null }
  total: { predicted: number | null; actual: number; error: number | null }
  distribution: {
    available: boolean
    reason: string | null
    draws: number | null
    crps_home: number | null
    crps_away: number | null
    crps_margin: number | null
    crps_total: number | null
    energy_score: number | null
    interval_coverage: {
      margin: Record<string, IntervalLevel>
      total: Record<string, IntervalLevel>
      note?: string
    } | null
  }
  market: {
    available: boolean
    reason: string | null
    evidence: Record<string, unknown> | null
  }
  excluded_snapshots: {
    forecast_identity: string
    snapshot_type: string | null
    snapshot_version: number | null
    published_utc: string | null
    reason: string
    detail: string
  }[]
  snapshots_considered: number | null
}

export interface ProspectiveModel {
  engine_id: string
  engine_version: string
  model_label: string
  scored_games: number
  scoreboard: {
    scored_games: number
    correct_winners: number
    winner_accuracy: number | null
    average_log_loss: number | null
    average_brier_score: number | null
    margin_mae: number | null
    total_mae: number | null
    team_score_mae: number | null
    average_probability_of_actual: number | null
    distribution_sample: number
    market_comparison_sample: number
    average_crps_margin: number | null
    average_crps_total: number | null
    average_energy_score: number | null
    interval_coverage: {
      sample: number
      margin: Record<string, { contained: number; of: number; rate: number | null }>
      total: Record<string, { contained: number; of: number; rate: number | null }>
      note?: string
    } | null
  }
  games: ProspectiveGame[]
}

export interface NflProspectiveResults {
  schema_version: string
  data_mode: 'live'
  generated_at_utc: string
  league: 'NFL'
  scoring_version: string
  models: ProspectiveModel[]
  total_scored_games: number
  /** Retained for audit and excluded from every metric. */
  superseded_evaluations: number
  provenance_note: string
  review_checkpoint_games: number
  sample_note: string
  inventory: {
    numerical_forecast_models: {
      engine_id: string
      engine_version: string
      snapshots: number
      events: number
      first_published_utc: string | null
      last_published_utc: string | null
      scoreable: boolean
    }[]
    supporting_evidence: {
      kind: string
      engine_id: string | null
      records: number
      events: number
      scoreable_as_a_forecast: boolean
      why: string
    }[]
    nfl_games_with_a_final: number
  }
  authority: Record<string, boolean | string>
  requirement: string | null
}
