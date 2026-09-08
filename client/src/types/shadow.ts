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
