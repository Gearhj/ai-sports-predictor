/**
 * Akron @ Minnesota (College Football, kickoff 2026-09-19 12:00 PM ET), as the
 * read models reported it on 2026-09-19: no quote row and no active price
 * collector (SportsGameOdds CFB lane: no uncommitted capacity, switched off,
 * 0 objects spent), and a forecast whose producer lease expired at 8:20 AM ET
 * (newest manifest 8:05 AM ET), so the 11:30 AM ET checkpoint recorded
 * BLOCKED / DISCOVERY_NOT_CURRENT. Values are copied from
 * /command-center/markets, /command-center/assessments and
 * /command-center/decisions, plus the market_status / model_status the server
 * now adds for College Football.
 */
import type { CoverageGame, DecisionItem } from '../services/commandCenter'
import type { GameAssessment } from '../services/probability'
import type { LeaseReading, MarketStatus, ModelStatus } from '../services/pregameStatus'

export const AKRON_GAME_ID = '018cbc48-7799-4c3e-869c-7e7e6823cb2e'
export const AKRON_KICKOFF = '2026-09-19T16:00:00+00:00'
/** 11:00 AM ET: after the lease lapsed, before the T-30 checkpoint. */
export const BEFORE_CHECKPOINT = Date.parse('2026-09-19T15:00:00Z')
/** 11:50 AM ET: the checkpoint has run, kickoff not yet. */
export const AFTER_CHECKPOINT = Date.parse('2026-09-19T15:50:00Z')

export const akronMarketStatus: MarketStatus = {
  state: 'COLLECTION_INACTIVE_NO_ALLOCATION',
  rule: 'same-book two-sided moneyline, older leg at most 240 minutes old at the decision',
  books_two_sided: 0, books_fresh_now: 0, books_fresh_through_window: 0,
  oldest_leg_observed_at: null, newest_leg_observed_at: null, oldest_leg_age_minutes: null,
  fresh_until: null, earliest_expiry: null,
  window_opens_at: '2026-09-19T15:30:00+00:00', window_closes_at: '2026-09-19T15:45:00+00:00',
  quotes_stored: 0,
  collection: {
    lane: 'sgo_cfb_events', active: false, lane_enabled: false, allocation_state: 'NO_UNCOMMITTED_SGO_CAPACITY',
    objects_allowed: 0, spent_on_lane: 0,
    explanation: 'Guard B has 211 objects left above its reserve and guard A 211; MLB/NFL still need about 207 objects this month and 10 more are kept as their margin, so 0 are genuinely uncommitted. CFB takes the smaller of 10 and that: 0.',
  },
}

export const expiredLease: LeaseReading = {
  state: 'EXPIRED_LEASE',
  manifest_sha256: '03ba7f58a4955c5671ae14161697f72ab8c0fd720010b5d3365be943368f337e',
  manifest_created_utc: '2026-09-19T12:05:46.539274+00:00',
  lease_valid_until: '2026-09-19T12:20:44.067209+00:00',
  producer_status: 'CURRENT_ELIGIBLE',
}

const schedule = {
  input_checks_from: null, publication_target: null,
  window_opens_at: '2026-09-19T15:30:00+00:00', window_closes_at: '2026-09-19T15:45:00+00:00',
  target_is_guaranteed: false as const,
}

/** Before the checkpoint: the present manifest's reading is the only one. */
export const akronModelBefore: ModelStatus = {
  state: 'ELIGIBILITY_EXPIRED', engine_id: 'CFB_GEN1_JOINT_BASELINE', schedule, producer: null,
  basis: 'PRESENT_DISCOVERY', eligibility: expiredLease, eligibility_now: expiredLease, checkpoint: null,
  renewed_since_checkpoint: false,
}

/** After the checkpoint: the lease as the checkpoint recorded it. */
export const akronModelAfter: ModelStatus = {
  ...akronModelBefore,
  basis: 'RECORDED_AT_CHECKPOINT',
  eligibility: { ...expiredLease, manifest_created_utc: '2026-09-19T08:05:46.539274-04:00', lease_valid_until: '2026-09-19T08:20:44.067209-04:00' },
  checkpoint: {
    decision_id: '29d99c43-0772-462c-99d3-ec80a9093771', recorded_at: '2026-09-19T15:30:55.019582+00:00',
    recommendation: 'BLOCKED', reason: 'DISCOVERY_NOT_CURRENT',
    detail: "EXPIRED_LEASE: The producer's eligibility lease has expired; a new owner tick must renew it.",
    discovery: { ...expiredLease, manifest_created_utc: '2026-09-19T08:05:46.539274-04:00', lease_valid_until: '2026-09-19T08:20:44.067209-04:00' },
  },
}

export function akronGame(model: ModelStatus = akronModelAfter, market: MarketStatus = akronMarketStatus): CoverageGame {
  return {
    game_id: AKRON_GAME_ID, league: 'CFB', matchup: 'Akron @ Minnesota',
    home: { abbreviation: 'Minnesota', name: 'Minnesota' }, away: { abbreviation: 'Akron', name: 'Akron' },
    starts_at: AKRON_KICKOFF, status: 'SCHEDULED', price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0,
    freshest_observed_at: null, stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
    best: { HOME: null, AWAY: null },
    model: { engine_id: 'CFB_GEN1_JOINT_BASELINE', home: 0.7831, away: 0.2169, home_two_way: 0.7831,
      expected_home: 32.88, expected_away: 19.81, created_at: '2026-09-18T15:57:55.943370+00:00' },
    quotes: [],
    checkpoints: { collection_opens_at: null, decision_window_opens_at: '2026-09-19T15:30:00+00:00',
      decision_window_closes_at: '2026-09-19T15:45:00+00:00' },
    price_unavailable: { reason: 'NO_UNCOMMITTED_SGO_CAPACITY', detail: akronMarketStatus.collection!.explanation! },
    market_status: market, model_status: model,
  }
}

export const akronAssessment: GameAssessment = {
  game_id: AKRON_GAME_ID, sport: 'CFB',
  binding: { channel: 'PAIRED_POPULATION', status: 'SUPPORTED', authority: 'RESEARCH_ONLY',
    authority_label: 'College Football paired-residual baseline — research only' },
  quotes: [], parlay_lab: [],
} as unknown as GameAssessment

export const akronBlockedItem: DecisionItem = {
  decision_id: '29d99c43-0772-462c-99d3-ec80a9093771', sport: 'CFB',
  experiment: 'ASP CFB RESEARCH PAPER BANKROLL — 2026 V1', game_id: AKRON_GAME_ID, matchup: 'Akron @ Minnesota',
  starts_at: AKRON_KICKOFF, phase: 'FINAL_PREGAME', market: 'MONEYLINE', selection: 'NONE', line: null, sportsbook: null,
  american: null, quote_observed_at: null, quote_age_minutes: null, model_probability: null, market_probability: null,
  edge: null, expected_value: null, recommendation: 'BLOCKED', stored_recommendation: 'BLOCKED',
  reason: "EXPIRED_LEASE: The producer's eligibility lease has expired; a new owner tick must renew it.",
  recommended_at: '2026-09-19T15:30:55.019582+00:00',
  decision_window: { opens_at: '2026-09-19T15:30:00+00:00', closes_at: '2026-09-19T15:45:00+00:00' },
  group: 'CLOSED', position: null,
} as DecisionItem
