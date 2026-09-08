import type { DataProvenance } from './domain'

/**
 * Contract for the read-only Monte Carlo replay of an official forecast.
 *
 * Every field here originates from a deterministic replay of the official
 * simulation, verified field-by-field against the persisted forecast. Nothing
 * is generated in the browser.
 */

export interface SimulationIdentity {
  forecast_id: string
  forecast_version: number
  forecast_track: string
  lifecycle: string
  generated_at: string
  available_as_of: string
  model_name: string | null
  model_version: string | null
  model_status: string | null
  feature_snapshot_reference: string | null
  development_model: boolean
  development_note: string
}

export interface ReplayValidation {
  exact: boolean
  max_absolute_delta: number
  compared_fields: number
  tolerance: number
  note: string
}

export interface RepresentativeOutcome {
  key: 'typical' | 'home_win' | 'away_win' | 'high_scoring' | 'low_scoring' | 'extra_innings'
  available: boolean
  criterion: string
  reason: string | null
  home_runs: number | null
  away_runs: number | null
  total_runs: number | null
  winner: 'home' | 'away' | null
  went_extra_innings: boolean | null
  draw_index: number | null
  share_of_simulations: number
  matching_simulations: number
  share_within_criterion: number
  criterion_simulations: number
}

export interface JointScoreDistribution {
  home_labels: string[]
  away_labels: string[]
  probabilities: number[][]
  counts: number[][]
  /** Per-cell subset that was level after regulation. */
  extra_inning_counts?: number[][]
  cap: number
  note: string
}

export interface PlayByPlayState {
  available: boolean
  reason: string
  what_is_shown_instead: string
}

export interface SamplingUncertainty {
  standard_error: number
  confidence_95_low: number
  confidence_95_high: number
  simulations: number
  note: string
}

export interface GameSimulationData extends DataProvenance {
  simulation_available: boolean
  unavailable_reason: string | null
  identity: SimulationIdentity
  play_by_play: PlayByPlayState
  official_win_probability: { home: number; away: number }
  simulation_win_probability?: { home: number; away: number }
  inputs?: {
    expected_home_runs: number
    expected_away_runs: number
    simulations: number
    seed: number
    simulator: string
  }
  replay_validation?: ReplayValidation
  means?: {
    home_runs: number
    away_runs: number
    total_runs: number
    run_difference: number
  }
  home_runs_distribution?: Record<string, number>
  away_runs_distribution?: Record<string, number>
  total_runs_distribution?: Record<string, number>
  run_difference_distribution?: Record<string, number>
  home_cover_probabilities?: Record<string, number>
  away_cover_probabilities?: Record<string, number>
  total_probabilities?: Record<string, { over: number; under: number; push: number }>
  joint_score_distribution?: JointScoreDistribution
  representative_outcomes?: RepresentativeOutcome[]
  extra_inning_probability?: number
  sampling_uncertainty?: SamplingUncertainty
}

/** ---- What-If Scenario Lab ---- */

export interface ScenarioControl {
  key: string
  label: string
  status: 'SUPPORTED' | 'PARTIAL' | 'UNSUPPORTED'
  model_path: string
  reason: string | null
  required_capability: string | null
  current_value: number | null
  min?: number
  max?: number
  step?: number
  domain_min?: number
  domain_max?: number
  unit?: string
}

export interface ScenarioCapabilities extends DataProvenance {
  game_id: string
  baseline_forecast_id: string
  baseline_evidence_cutoff: string
  feature_snapshot_id: string
  missing_features: string[]
  controls: ScenarioControl[]
  supported_count: number
  notice: string
}

export interface ScenarioMetrics {
  home_win_probability: number
  away_win_probability: number
  expected_home_runs: number
  expected_away_runs: number
  expected_total_runs: number
  expected_run_difference: number
  home_cover_probabilities: Record<string, number>
  total_probabilities: Record<string, { over: number; under: number; push: number }>
  total_runs_distribution: Record<string, number>
}

export interface ScenarioResult extends DataProvenance {
  status: 'HYPOTHETICAL'
  notice: string
  identity: {
    baseline_forecast_id: string
    feature_snapshot_id: string
    evidence_cutoff: string
    simulations: number
    seed: number
    overrides: Record<string, number>
    scenario_hash: string
    generated_at: string
    baseline_model_name: string | null
    baseline_model_version: string | null
    baseline_model_status: string | null
  }
  applied_overrides: Record<string, number>
  rejected_overrides: Record<string, string>
  out_of_domain: string[]
  baseline_reproduction: {
    exact: boolean
    max_absolute_delta: number
    tolerance: number
    note: string
  }
  official: ScenarioMetrics
  scenario: ScenarioMetrics
  deltas: {
    home_win_probability_points: number
    away_win_probability_points: number
    expected_home_runs: number
    expected_away_runs: number
    expected_total_runs: number
    expected_run_difference: number
    home_cover_points: Record<string, number>
    total_over_points: Record<string, number>
  }
  scenario_joint: JointScoreDistribution | Record<string, never>
  interpretation: string
  sensitivity: Array<{
    key: string
    home_win_probability_points: number
    expected_total_runs: number
  }>
}
