/**
 * Research-shadow comparison contract (AISP_MLB_V18_SHADOW_COMPARISON_V1).
 *
 * The shadow track is deliberately a separate type from the official forecast:
 * nothing in the app can accidentally pass a shadow probability where an
 * official one is expected.
 */
export interface OfficialForecastView {
  track: 'GEN_1_OFFICIAL'
  label: string
  model: string | null
  home_win_probability: number
  generated_at: string | null
  status: string
  version: number
  wagering_authority: true
  pregame: boolean
}

/** A recorded final outcome attached to a shadow forecast, never rewritten. */
export interface ShadowSettlement {
  settlementId: string
  gameId: string
  status: string
  settledAt: string
  immutable: true
  homeRuns: number | null
  awayRuns: number | null
  extraInnings: boolean | null
  homeWin: boolean | null
  outcomeRevision: number
  official: boolean
  source: string
  lineageSha256: string
}

export interface ShadowPopulation {
  simulation_count: number | null
  home_win_probability: number | null
  away_win_probability: number | null
  expected_home_runs: number | null
  expected_away_runs: number | null
  expected_total_runs: number | null
  expected_run_differential: number | null
  one_run_probability: number | null
  extra_inning_probability: number | null
  pitching_change_rate_home: number | null
  pitching_change_rate_away: number | null
  monte_carlo_standard_error: number | null
  monte_carlo_95_half_width: number | null
  total_run_distribution: Array<{ value: number; probability: number }> | null
}

export interface ShadowForecastView {
  track: 'RESEARCH_SHADOW'
  engine_id: string
  label: string
  forecast_id: string
  bundle_id: string | null
  bundle_sha256: string | null
  evaluation_contract: string | null
  home_win_probability: number
  expected_home_runs: number | null
  expected_away_runs: number | null
  generated_at: string | null
  evidence_cutoff: string | null
  pregame: boolean
  simulation_count: number | null
  master_seed: number
  monte_carlo_standard_error: number | null
  monte_carlo_95_half_width: number | null
  deterministic_replay_verified: boolean | null
  illegal_state_count: number | null
  nontermination_count: number | null
  path_digest: string | null
  sequential_path_events: number
  available_views?: {
    broadcast: boolean
    geometry: boolean
    population: boolean
  }
  lineup_modes: string[] | null
  sides: Record<string, Record<string, string | null>> | null
  limitations: string[] | null
  /** Always false. A shadow forecast can never fund or justify a position. */
  wagering_authority: false
  /** V18's own frozen results across all of its simulations. Never Gen 1's. */
  population: ShadowPopulation | null
  settlement: ShadowSettlement | null
  settlement_state: 'SETTLED' | 'PENDING'
  settlement_note: string
  paired_with_official: boolean
  pairing_note: string | null
  difference_vs_official: number | null
  difference_orientation: 'V18_MINUS_GEN1'
  simulation_indistinguishable: boolean | null
}

export interface ShadowComparison {
  schema_version: 'AISP_MLB_V18_SHADOW_COMPARISON_V1'
  game_id: string
  home_team: string | null
  away_team: string | null
  scheduled_start: string | null
  event_status: string
  official: OfficialForecastView | null
  shadows: ShadowForecastView[]
  notes: string
  /** What the package does and does not grant, kept apart from the experiment. */
  authority_note?: string | null
  /** Present only when an ensemble release candidate has been imported. */
  candidate_comparison?: CandidateComparison | null
  /** The cohort the selected configuration belongs to. */
  candidate_cohort?: CandidateCohort | null
}


/** One side of the baseline-versus-candidate comparison. */
export interface CandidateSide {
  track: 'RESEARCH_SHADOW' | 'RESEARCH_CANDIDATE'
  label: string
  engine_id: string
  engine_version: string | null
  forecast_id: string
  model_artifact_sha256: string | null
  artifact_type: 'PROSPECTIVE' | 'HISTORICAL_REPLAY'
  is_replay: boolean
  home_win_probability: number | null
  away_win_probability: number | null
  expected_home_runs: number | null
  expected_away_runs: number | null
  evidence_cutoff_utc: string | null
  published_utc: string | null
  imported_utc: string | null
  scheduled_start_utc: string | null
  simulation_count: number | null
  wagering_authority: false
  execution_allowed_in_aisp: false
  score: CandidateScore | null
}

/** The persisted research score, when eligible final evidence exists. */
export interface CandidateScore {
  evaluation_id: string
  scoring_version: string
  evaluated_at: string | null
  home_score: number | null
  away_score: number | null
  actual_outcome: string | null
  predicted_outcome: string | null
  winner_correct: boolean | null
  probability_of_actual: number | null
  log_loss: number | null
  brier_score: number | null
  margin_error: number | null
  total_error: number | null
  crps_home?: number | null
  crps_away?: number | null
  crps_margin: number | null
  crps_total: number | null
  energy_score?: number | null
  /**
   * An absent distribution is absent, never zero. When this is false the CRPS
   * and energy fields are null and the reason says why, so a blank cell is
   * never read as a perfect score.
   */
  distribution_available?: boolean
  distribution_unavailable_reason?: string | null
  distribution_draws?: number | null
  /** Which recorded final this score was computed against. */
  result_identity?: string | null
  result_source: string | null
  result_tier: number | null
  result_observed_utc?: string | null
  publication_clock: string | null
  publication_clock_verified: boolean | null
}

/** One loss metric, candidate against baseline, with the direction spelled out. */
export interface CandidateScoreMetric {
  key: string
  label: string
  candidate: number
  baseline: number
  delta: number
  favours: 'CANDIDATE' | 'BASELINE' | 'NEITHER'
}

export interface CandidateScoreDifference {
  convention: string
  direction: string
  metrics: CandidateScoreMetric[]
}

/** Whether both arms were scored against the same recorded final. */
export interface CandidateSharedFinal {
  status: 'SHARED' | 'DIVERGENT' | 'UNKNOWN'
  candidate_result_identity: string | null
  baseline_result_identity: string | null
  detail: string
}

/** The separately governed, revisioned outcome record for the game. */
export interface CandidateGovernedFinal {
  revision: number
  outcome_status: string
  home_score: number
  away_score: number
  final_at_utc: string | null
  payload_checksum: string | null
  source_record_id: string | null
}

/** A mean over the games where one configuration and its baseline both scored. */
export interface CandidateCohortMetric {
  key: string
  label: string
  games: number
  candidate_mean: number
  baseline_mean: number
  delta: number
  favours: 'CANDIDATE' | 'BASELINE' | 'NEITHER'
}

export interface CandidateCohortGame {
  source_game_id: string
  canonical_game_id: string | null
  candidate_forecast_id: string
  baseline_forecast_id: string
  scheduled_start_utc: string | null
  candidate: CandidateScore
  baseline: CandidateScore
}

export interface CandidateCohortExclusion {
  forecast_identity: string
  source_game_id: string
  reason: string
  detail: string
}

export interface CandidateCohort {
  schema_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_COHORT_V1'
  configuration: string
  games: number
  metrics: CandidateCohortMetric[]
  included: CandidateCohortGame[]
  excluded: CandidateCohortExclusion[]
  convention: string
  claim_limit: string
}

export interface CandidateComponent {
  component_id: string
  role: string | null
  status: string | null
  sha256: string | null
  ran: boolean
  fallback_reason: string | null
  detail: string | null
}

export interface CandidateConfiguration {
  key: string
  letter: string
  title: string
  status: string
  note: string
  enabled_in_release: boolean
  provenance: string
  simulations: number | null
  home_win_probability: number
  home_win_monte_carlo_standard_error: number | null
  expected_home_runs: number
  expected_away_runs: number
  expected_total_runs: number | null
  extra_inning_rate: number | null
  paired_delta_vs_a: {
    home_win_probability: number | null
    home_win_paired_standard_error: number | null
    expected_home_runs: number | null
    expected_away_runs: number | null
    expected_total_runs: number | null
  }
  illegal_state_count: number | null
  nontermination_count: number | null
}

/** One published configuration of the candidate engine family for a game. */
export interface CandidateConfigurationOption {
  key: string
  configuration_id: string | null
  configuration_identity_source: 'PRODUCER_CONFIGURATION_ID' | 'CANDIDATE_ENGINE_VERSION'
  actual_configuration: string | null
  engine_version: string | null
  forecast_id: string
  contract_version: string | null
  component_trial_supported: boolean
  fell_back_to_baseline_draws: boolean
  published_utc: string | null
}

export interface CandidateComparison {
  schema_version: 'AISP_MLB_ENSEMBLE_CANDIDATE_COMPARISON_V1'
  game_id: string
  artifact_type: 'PROSPECTIVE' | 'HISTORICAL_REPLAY'
  is_replay: boolean
  prospective_eligible: boolean
  /** Which arm this payload describes, and every arm published for the game. */
  configuration?: CandidateConfigurationOption | null
  available_configurations?: CandidateConfigurationOption[]
  /** How the displayed baseline was resolved. */
  baseline_binding?: string | null
  /** Present when this arm's component did not run and it reused baseline draws. */
  fallback_note?: string | null
  /** Candidate minus baseline on every rule BOTH sides actually scored. */
  score_difference?: CandidateScoreDifference | null
  shared_final?: CandidateSharedFinal | null
  governed_final?: CandidateGovernedFinal | null
  baseline: CandidateSide | null
  candidate: CandidateSide
  difference: {
    home_win_probability_points: number | null
    expected_home_runs: number | null
    expected_away_runs: number | null
    convention: string
  } | null
  components: CandidateComponent[]
  configurations?: CandidateConfiguration[]
  default_configuration?: string | null
  comparison_status?: string | null
  limitations?: string[]
  candidate_package_sha256?: string | null
  ordering_claim?: string
  component_attribution: Record<string, unknown> | null
  component_attribution_note: string | null
  candidate_identity: {
    engine_id: string
    engine_version: string | null
    model_artifact_sha256: string | null
    handoff_sha256: string | null
    object_sha256: string | null
    baseline_forecast_id: string | null
  }
  authority: {
    candidate_wagering_authority: false
    candidate_execution_allowed_in_aisp: false
    candidate_activates_model: false
    controlling_engine_for_official_decisions: string
    controlling_engine_source: string
  }
  note: string
}

export interface ShadowPathEvent {
  pa: number
  inning: number
  half: 'TOP' | 'BOTTOM'
  battingOrderPosition: number
  outcome: string
  outsBefore: number
  outsAfter: number
  baseMaskBefore: number
  baseMaskAfter: number
  runsScored: number
  homeScoreAfter: number
  awayScoreAfter: number
  pitcherState: string
  pitchingChange: boolean
}

export interface ShadowPath {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1'
  forecast_id: string
  game_id: string
  engine_id: string
  bundle_sha256: string | null
  path_digest: string | null
  master_seed: number
  simulation_count: number | null
  path_count: number
  paths: ShadowPathEvent[][]
}

// -- Play Geometry (AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1) ---------------------
//
// Modelled geometry and anonymous runner movements published beside a
// registered V18 research-shadow forecast. Every field here is either
// PERSISTED V18 state or explicitly MODELLED; the two are never merged.

export interface GeometryPoint {
  x: number
  y: number
}

export interface BallTrace {
  coordinateSystem: string
  start: GeometryPoint
  control: Array<GeometryPoint & { lift: number }>
  end: GeometryPoint
  traceStyle: 'GROUND' | 'LINE' | 'ARC_HIGH' | 'ARC_STEEP'
  normalizedDuration: number
  fairTerritory: boolean
  outcomeCompatibility: string
  /** Always states that this is not a measured flight path. */
  semantics: string
}

export interface PlayGeometry {
  contactOccurred: boolean
  battedBallType: string | null
  locationZone?: string | null
  depthModifier?: string | null
  foulLocation?: boolean
  geometryAuthority: string
  geometryBackoffLevel: string | null
  geometryBackoffSupport?: number
  geometryModelVersion: string
  geometrySamplingSeed: string
  coordinateSystem: string
  landingOrFieldingPoint: (GeometryPoint & {
    depth: number
    depthClass: string
    directionDegrees: number
    fairTerritory: boolean
  }) | null
  /** Unregistered in every MLB authority; always null, never zero. */
  exitVelocityMph: null
  launchAngleDegrees: null
  sprayAngleDegrees: null
  projectedDistanceFeet: null
  landingCoordinateMeasured: null
}

export type BaseState = 'BATTER_BOX' | 'FIRST' | 'SECOND' | 'THIRD' | 'HOME' | 'OUT'

export interface RunnerMovement {
  anonymousRunnerId: string
  role: 'BATTER' | 'RUNNER'
  from: BaseState
  to: BaseState
  scored: boolean
  out: boolean
  movementReason: string
  /** Presentation sequencing only. Never a measured timestamp. */
  startFraction: number
  endFraction: number
  authority: 'FITTED_HISTORICAL_TEMPLATE' | 'DETERMINISTIC_LEGAL_BACKOFF'
}

export interface GeometryEvent {
  pa: number
  inning: number
  half: 'TOP' | 'BOTTOM'
  battingTeam: 'AWAY' | 'HOME'
  battingOrderPosition: number
  pitcherState: string
  pitchingChange: boolean
  outcome: string
  outsBefore: number
  outsAfter: number
  baseMaskBefore: number
  baseMaskAfter: number
  runsScored: number
  homeScoreAfter: number
  awayScoreAfter: number
  geometry: PlayGeometry
  ballTrace: BallTrace | null
  runnerMovements: RunnerMovement[]
  runnerModelVersion: string
  runnerBackoffLevel: string
  runnerBackoffSupport: number
}

export interface PlayGeometryPayload {
  schema_version: 'AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1'
  forecast_id: string
  game_id: string
  engine_id: string
  supplement_id: string
  supplement_sha256: string
  parent_bundle_id: string
  parent_bundle_sha256: string
  coordinate_system: Record<string, unknown> | null
  geometry_model_version: string
  runner_model_version: string
  registered_at: string
  backoff_summary: Record<string, unknown> | null
  limitations: string[] | null
  wagering_authority: false
  promotion_status: 'NOT_PROMOTED'
  execution_allowed_in_aisp: false
  display_authority: 'RESEARCH_SHADOW_PLAY_GEOMETRY'
  plate_appearances: number
  contact_plays: number
  ball_traces: number
  runner_movements: number
  events: GeometryEvent[]
  population: PopulationGeometrySlot
  notes: string
}

/**
 * The integration boundary for a future population-geometry artifact.
 *
 * Only identity and availability are fixed here. The eventual importer must be
 * driven by the real Model Studio handoff, so nothing beyond this minimal
 * adapter shape is guessed.
 */
export interface PopulationGeometrySlot {
  simulationCount: number | null
  populationGeometryAvailable: boolean
  populationGeometryStatus: string
  /** Present only once a checksum-verified aggregate is registered. */
  populationSupplementId?: string | null
  populationSupplementSha256?: string | null
  accumulationFrames?: unknown[] | null
  contactDensity?: unknown | null
  ballPathDensity?: unknown | null
  runnerTraffic?: unknown | null
  representativeSelectors?: unknown | null
  frameDigest?: string | null
}

/* --------------------------------------------------------------------------
 * Population geometry: deterministic aggregation over 5,000 complete V18
 * simulations. Every shape here mirrors the checksum-verified supplement; the
 * browser derives nothing that the artifact does not supply.
 * ------------------------------------------------------------------------ */

/** Provenance repeated on every population response. */
export interface PopulationIdentity {
  supplement_id: string
  supplement_sha256: string
  parent_bundle_sha256: string
  play_geometry_sha256: string
  population_digest: string
  execution_authority_sha256: string
  wagering_authority: false
  promotion_status: 'NOT_PROMOTED'
  execution_allowed_in_aisp: false
  display_authority: 'RESEARCH_SHADOW_POPULATION_GEOMETRY'
}

/** One deterministic accumulation frame: 50 more completed games. */
export interface PopulationFrame {
  frameIndex: number
  simulationIndexStart: number
  simulationIndexEndInclusive: number
  cumulativeSimulationCount: number
  cumulativeHomeWins: number
  cumulativeAwayWins: number
  cumulativeHomeWinProbability: number
  cumulativeAwayWinProbability: number
  cumulativeExpectedHomeRuns: number
  cumulativeExpectedAwayRuns: number
  cumulativeExpectedTotal: number
  cumulativeRunDifferential: number
  cumulativeOneRunGameRate: number
  cumulativeExtraInningRate: number
  cumulativeContactPlayCount: number
  cumulativeGeometryDensity: number
  cumulativeRunnerPathTraffic: number
  cumulativeScoringPlayCount: number
  frameDigest: string
}

export type RepresentativeSelector =
  | 'TYPICAL' | 'HOME_WIN' | 'AWAY_WIN'
  | 'HIGH_SCORING' | 'LOW_SCORING' | 'EXTRA_INNINGS'

export interface PopulationSelector {
  selector: RepresentativeSelector
  /** May legitimately be 0 — that is simulation zero, not a missing value. */
  simulationIndex: number
  pathDigest: string
  geometryDigest: string
  definition: string
  candidateCount: number
  rank: number
  distance: number
  tieBreak: string
  selectionInputs: {
    totalRuns: number
    runDifferential: number
    homeRuns: number
    awayRuns: number
    innings: number
  }
  target: Record<string, unknown>
}

export interface PopulationPayload extends PopulationIdentity {
  schema_version: 'AISP_MLB_V18_POPULATION_VIEW_V1'
  forecast_id: string
  simulation_count: number
  frame_count: number
  frame_size: number
  master_seed: number
  counts: {
    plateAppearances: number
    contactPlays: number
    geometryBearingPlays: number
    scoringPlays: number
    runnerSegments: number
  }
  final_population: {
    homeWinProbability: number
    meanHomeRuns: number
    meanAwayRuns: number
    meanTotalRuns: number
    meanRunDifferential: number
    oneRunRate: number
    extraInningRate: number
  }
  illegal_state_count: number
  nontermination_count: number
  deterministic_replay: boolean
  frames: PopulationFrame[]
  selectors: Record<RepresentativeSelector, PopulationSelector>
  notes: string
}

/** A sparse 32x32 contact-density layer: [cellIndex, rawCount] pairs. */
export interface ContactDensityLayer {
  count: number
  maximumCellCount: number
  sparseRawCounts: Array<[number, number]>
}

/** A sparse ball-path layer: [origin*cells + control, destination, count]. */
export type PathDensityLayer = Array<[number, number, number]>

export interface PopulationLayersPayload extends PopulationIdentity {
  schema_version: 'AISP_MLB_V18_POPULATION_LAYERS_V1'
  forecast_id: string
  contact_density: {
    coordinateSystem: string
    grid: { width: number; height: number; xRange: [number, number]; yRange: [number, number] }
    layers: Record<string, ContactDensityLayer>
    normalization: string
  }
  path_density: {
    coordinateSystem: string
    grid: { width: number; height: number; cellEncoding: string; tupleEncoding: string }
    layers: Record<string, PathDensityLayer>
    representation: string
    semantics: string
  }
  runner_traffic: {
    allSegments: Record<string, number>
    dimensions: Record<string, Record<string, number>>
    requiredTrafficCounts: Record<string, number>
    representation: string
    semantics: string
  }
}

export interface RepresentativePath {
  pathIndex: number
  gameId: string
  plateAppearances: number
  pathDigest: string
  geometryDigest: string
  backoffSummary: Record<string, unknown> | null
  events: GeometryEvent[]
}

export interface PopulationRepresentativesPayload extends PopulationIdentity {
  schema_version: 'AISP_MLB_V18_POPULATION_REPRESENTATIVES_V1'
  forecast_id: string
  selectors: Record<RepresentativeSelector, PopulationSelector>
  paths: RepresentativePath[]
  unique_path_count: number
  notes: string
}
