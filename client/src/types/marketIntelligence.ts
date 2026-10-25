/** Shapes returned by the market tape, the RFF index and the information-event boundary. */

export interface TapeTeam {
  team_id: string
  abbreviation: string
  name: string
}

export interface TapeBookEntry {
  t: string
  a: number
  b: number
  point_a: number | null
  point_b: number | null
  observed_at_utc: string
  usable: boolean
  skew_seconds: number
  no_vig_a: number
}

export interface TapeBook {
  sportsbook_id: string
  name: string
  key: string
  provider: 'THE_ODDS_API' | 'SPORTSGAMEODDS' | string
  observations: number
  series: TapeBookEntry[]
}

export interface TapeFrame {
  t: string
  before_cutoff: boolean | null
  usable_books: number
  consensus_no_vig_a: number | null
  consensus_no_vig_home: number | null
  dispersion: number | null
  range_a: [number, number] | null
  best_a: { american: number; sportsbook_id: string } | null
  best_b: { american: number; sportsbook_id: string } | null
}

export interface TapeMilestone {
  t: string
  consensus_no_vig_a: number | null
  usable_books: number
}

export interface MarketTape {
  schema_version: string
  data_mode: 'live'
  generated_at_utc: string
  game_id: string
  market: {
    market_type: 'MONEYLINE' | 'SPREAD' | 'TOTAL'
    period: string
    /** The handicap or total this tape is showing. Null on a moneyline. */
    line?: string | null
    side_a: { market_outcome_id: string; outcome_key: string; label: string } | null
    side_b: { market_outcome_id: string; outcome_key: string; label: string } | null
    home_side: 'a' | 'b' | null
    probability_meaning: string | null
  }
  event: {
    home: TapeTeam | null
    away: TapeTeam | null
    starts_at_utc: string | null
    decision_cutoff_utc: string | null
    status: string
  }
  providers: {
    primary: string
    fallback: string
    fallback_is_temporary?: boolean
    fallback_until?: string
    present: { provider: string; books: number }[]
    note?: string
  }
  books: TapeBook[]
  contract: Record<string, unknown>
  frames: TapeFrame[]
  frame_count: number
  observed_instants: number
  frames_sampled: boolean
  first_observed_utc: string | null
  last_observed_utc: string | null
  model_probability_series: {
    version: number
    generated_at_utc: string
    available_as_of_utc: string | null
    home_win_probability: number
    status: string
    lifecycle: string
  }[]
  milestones: {
    opening: TapeMilestone | null
    latest: TapeMilestone | null
    at_cutoff: TapeMilestone | null
    movement: { points: number; velocity_points_per_hour: number; hours_observed: number } | null
  }
  requirement: string | null
}

export type RffStatus = 'RFF_PUBLISHED' | 'PROVISIONAL_WIDE_RANGE' | 'INSUFFICIENT_EVIDENCE'

/** The Extended components, described and never scored. */
export interface RffExtendedConcept {
  name: string
  calculated: false
  score: null
  statement: string
  missing_components: {
    key: string
    label: string
    family: string
    family_label: string
    weight: null
    needs: string
    reason: string
  }[]
}

/** AISP's own collection health. Deliberately outside the index. */
export interface RffEvidenceHealth {
  schema_version: string
  label: string
  lineup_sides_observed: number
  starter_sides_observed: number
  forecast_readiness_score: number | null
  components_blocked_by_collection: { key: string; label: string; weight: number; reason: string; requires: string | null }[]
  components_awaiting_their_window: { key: string; label: string; weight: number; requires: string | null }[]
  coverage_lost_to_collection_gaps: number
  statement: string
}

export interface RffIndexIdentity {
  key: string
  name: string
  components: { key: string; label: string; weight: number; family: string; family_label: string }[]
  weight_total: number
  statement: string
}

export interface RffComponent {
  key: string
  label: string
  /** Null only for an Extended component, which carries no Core weight. */
  weight: number | null
  family: string
  family_label: string
  available: boolean
  /** Why it has no score: NO_SOURCE, STALE_EVIDENCE, NOT_YET_OBSERVABLE, ... */
  unavailable_reason?: string | null
  observed_utc?: string | null
  stale?: boolean
  score: number | null
  /** "Unavailable" when there is no data. Never "0". */
  display: string
  basis: string
  detail: Record<string, unknown>
  requires: string | null
  limitation: string | null
}

export interface RffBand {
  key: string
  label: string
  description: string
  floor?: number
}

export interface RffGame {
  schema_version: string
  generated_at_utc: string
  game_id: string
  status: RffStatus
  label: string
  description: string
  market_type: string
  event: MarketTape['event']
  components: RffComponent[]
  /** Only present when the evidence supports publication. */
  score: number | null
  /** Sum of weight x score over available components, on the fixed 0-100 scale. */
  known_contribution: number
  unknown_weight: number
  lower_bound: number
  upper_bound: number
  interval_width: number
  score_range: [number, number] | null
  score_basis?: string
  band: RffBand
  category_reason?: string
  /** False whenever the interval does not identify a band: lead with the range. */
  emphasize_point?: boolean
  index?: RffIndexIdentity
  extended?: RffExtendedConcept
  evidence_health?: RffEvidenceHealth
  evidence_coverage: number
  available_components: number
  unavailable_components: number
  families_present: string[]
  family_labels: string[]
  publication_blockers: { code: string; detail: string }[]
  rankable: boolean
  diagnostics?: { observedComponentAverage: number | null; note: string; correlated_components: unknown[] }
  missing_requirements?: { key: string; label: string; weight: number; family: string; reason: string; requires: string | null }[]
  policy?: Record<string, number | string>
  contributions: { key: string; label: string; family: string; score: number | null; weight: number; share_of_measure: number; share_of_known_contribution: number }[]
  earlier: {
    t: string
    comparable_components: string[]
    earlier_score: number
    current_score: number
    change: number
    note: string
  } | null
  explanation: string
  share_card: {
    matchup: string | null
    headline: string | null
    band: string
    coverage: number
    triggers: { label: string; score: number | null; why: string }[]
    disclaimer: string
  }
  authority: Record<string, boolean | string>
  sources: Record<string, unknown>
  requirement: string | null
  tape?: MarketTape
}

export interface RffBoard {
  schema_version: string
  data_mode: 'live'
  generated_at_utc: string
  status: string
  label: string
  description: string
  market_type: string
  /** The ranked, publishable set. Comparable with each other and nothing else. */
  games: RffGame[]
  published: RffGame[]
  provisional: RffGame[]
  insufficient: RffGame[]
  counts?: { published: number; provisional: number; insufficient: number }
  section_notes?: { published: string; provisional: string; insufficient: string }
  index?: RffIndexIdentity
  extended?: RffExtendedConcept
  families?: Record<string, string>
  correlated_components?: { components: string[]; relation: string }[]
  policy?: Record<string, number>
  unscored: { game_id: string; event: MarketTape['event']; requirement: string; evidence_coverage?: number }[]
  weights: Record<string, number>
  bands: { floor: number; label: string; description: string }[]
  date?: string
  league?: string | null
  games_considered?: number
}

export interface InformationEventRecord {
  contract_version: string
  event_id: string
  classification: string
  sport: string
  league: string
  game_id: string | null
  team_id: string | null
  player_id: string | null
  player_name: string | null
  source: string
  reliability_tier: string
  published_at_utc: string | null
  ingested_at_utc: string
  corroboration: string
  headline: string
  expected_effect: { kind: string; direction: string; magnitude: number | null; note?: string } | null
  market_before: { observed_at_utc: string | null; consensus_no_vig_home: number | null; usable_books: number; frame_offset_seconds: number | null } | null
  market_after: { observed_at_utc: string | null; consensus_no_vig_home: number | null; usable_books: number; frame_offset_seconds: number | null } | null
  lead_seconds: number | null
  evidence: Record<string, string>
}

export interface InformationEventsForGame {
  schema_version: string
  contract_version: string
  data_mode: 'live'
  generated_at_utc: string
  game_id: string
  event: MarketTape['event']
  events: InformationEventRecord[]
  event_count: number
  classes: {
    key: string
    description: string
    events: number
    state: 'COLLECTED' | 'COLLECTED_NONE_TONIGHT' | 'NOT_COLLECTED'
    collected: boolean
    requires: string | null
    sources?: string[]
  }[]
  unmapped_stored_types: string[]
  market_reference: Record<string, unknown>
  authority: Record<string, boolean | string>
}
