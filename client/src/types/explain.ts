/**
 * Decision and outcome explanation payloads.
 *
 * Every field is derived from retained evidence on the server. Nothing here is
 * generated text: the bullets are assembled from the decision row and the
 * forecast it bound, so the same wager explained before and after settlement
 * produces the same sentences.
 */

export interface ExplanationBullet {
  key: string
  text: string
  /** Present on the support bullet: whether real attribution existed. */
  attribution_available?: boolean
  /** Present on the events bullet. */
  evidence_available?: boolean
}

export interface PriceExplanation {
  american_odds: number | null
  decimal_odds: number | null
  model_probability_pct: number | null
  raw_model_probability_pct: number | null
  /** What the OFFERED price requires. Not the market's opinion. */
  break_even_probability_pct: number | null
  /** What the market implies with margin removed. A different question. */
  no_vig_probability_pct: number | null
  margin_over_break_even_pts: number | null
  disagreement_with_market_pts: number | null
  clears_break_even: boolean | null
  /** Whether the MODEL makes it the likely winner. Often false for value. */
  model_makes_it_favoured: boolean | null
  priced_as_underdog: boolean | null
  probability_units: 'PERCENT'
  definitions: Record<string, string>
}

export interface ValueFraming {
  kind:
    | 'FAVOURED_AND_PRICED_WELL'
    | 'UNDERDOG_TAKEN_ON_PRICE'
    | 'FAVOURED_BUT_PRICE_SHORT'
    | 'NEITHER'
    | 'UNKNOWN'
  headline: string
  model_makes_it_favoured: boolean | null
  priced_as_underdog: boolean | null
  clears_break_even: boolean | null
  note: string
}

export interface ExplanationDetails {
  decision_id: string | null
  position_id: string | null
  phase: string | null
  recommended_at_utc: string | null
  quote_observed_at_utc: string | null
  sportsbook: string | null
  policy_version: string | null
  staking_version: string | null
  tier: string | null
  gates_passed: string[]
  forecast_identity: string | null
  engine_id: string | null
  engine_version: string | null
  forecast_evidence_cutoff_utc: string | null
  forecast_published_utc: string | null
  bound_at_decision_time: string
}

export interface WhyExplanation {
  schema_version: 'AISP_WAGER_EXPLANATION_V1'
  /** False for a qualifying opportunity that was never recorded as a wager. */
  recorded_wager: boolean
  status_line: string
  market: string
  selection: string
  bullets: ExplanationBullet[]
  price: PriceExplanation
  value_framing: ValueFraming
  details: ExplanationDetails
}

export interface OutcomeExplanation {
  schema_version: 'AISP_OUTCOME_EXPLANATION_V1'
  result: string | null
  result_language: string | null
  bullets: ExplanationBullet[]
  uncertainty: {
    resolved: boolean
    inside_central_range?: boolean
    central_range?: (number | null)[]
    text: string
  }
  interpretation_guard: string
  event_evidence_available: boolean
}

export interface DecisionExplanation {
  schema_version: 'AISP_DECISION_EXPLANATION_V1'
  decision_id: string
  position_id: string | null
  game_id: string
  slate_date: string
  matchup: {
    home: string
    away: string
    home_name: string
    away_name: string
    label: string
    starts_at_utc: string | null
  } | null
  recorded_wager: boolean
  why: WhyExplanation
  /** Null while the wager is still open. */
  what_happened: OutcomeExplanation | null
  derived_only: string
}

export interface SlateExplanations {
  schema_version: 'AISP_SLATE_EXPLANATIONS_V1'
  event_date: string
  explanations: DecisionExplanation[]
  recorded_wagers: number
  qualifying_only: number
}
