/**
 * A College Football game WITH stored SportsGameOdds quotes -- a LABELLED FIXTURE.
 * No real provider id, price or clock: AISP has not collected a CFB quote yet.
 * Each side carries its own book clock (home 3:10, away 3:13 PM ET); AISP sent
 * the request at 3:20 and received the response at 3:20:02.
 */
import type { CfbMarket, CfbStoredQuote, QuoteClocks } from '../services/cfbMarket'

export const FIXTURE_NOW = Date.parse('2026-09-26T19:35:00Z')

export const fixtureClocks: QuoteClocks = {
  quote_clock_basis: 'BOOK_LAST_UPDATED_AT_PER_SIDE',
  legs: { HOME: '2026-09-26T19:10:00+00:00', AWAY: '2026-09-26T19:13:00+00:00' },
  older_leg_at: '2026-09-26T19:10:00+00:00',
  leg_skew_seconds: 180,
  quote_age_minutes: 25,
  fetched_at: '2026-09-26T19:20:00+00:00',
  fetch_basis: 'AISP_REQUEST_SENT (or later review binding)',
  received_at: '2026-09-26T19:20:02+00:00',
  receipt_basis: 'AISP_RESPONSE_RECEIVED',
  fetch_age_minutes: 15,
  fresh: true,
  usable_until: '2026-09-26T23:10:00+00:00',
}

const quote = (over: Partial<CfbStoredQuote>): CfbStoredQuote => ({
  market: 'MONEYLINE', line: null, sportsbook: 'DraftKings (SportsGameOdds, research)', sportsbook_key: 'sgo:draftkings',
  provider: 'SPORTSGAMEODDS', observed_at: fixtureClocks.older_leg_at, age_minutes: 25, fresh: true,
  clocks: fixtureClocks, sides: { HOME: { american: -150, decimal: 1.667 }, AWAY: { american: 130, decimal: 2.3 } },
  ...over,
})

export const fixtureMarket: CfbMarket = {
  state: 'QUOTES_STORED',
  freshness_rule: 'older leg at most 240 minutes old',
  quotes: [
    quote({}),
    quote({ market: 'SPREAD', line: -3.5, sides: { HOME: { american: -110, decimal: 1.909 }, AWAY: { american: -110, decimal: 1.909 } } }),
    quote({ market: 'TOTAL', line: 51.5, sides: { OVER: { american: -105, decimal: 1.952 }, UNDER: { american: -115, decimal: 1.87 } } }),
  ],
  comparisons: [
    { market: 'MONEYLINE', line: null, sportsbook: 'DraftKings (SportsGameOdds, research)', provider: 'SPORTSGAMEODDS', current: true,
      available: true, channel: 'JOINT_PAIRED_RESIDUAL_BASELINE', channel_label: 'Joint score simulation (paired-residual draws) · the channel the paper policy decides on',
      model: { HOME: 0.62, AWAY: 0.38, tie: 0 }, no_vig: { HOME: 0.5798, AWAY: 0.4202 },
      difference: { HOME: 0.0402, AWAY: -0.0402 }, policy_test: true,
      excluded_channels: ['RIDGE_DEVELOPMENT_CALIBRATED', 'ELO_DIAGNOSTIC'] },
    { market: 'SPREAD', line: -3.5, sportsbook: 'DraftKings (SportsGameOdds, research)', provider: 'SPORTSGAMEODDS', current: true,
      available: true, channel: 'JOINT_SCORE_DISTRIBUTION', channel_label: 'Joint score simulation · its own margin/total distribution over the retained draws',
      model: { HOME: 0.51, PUSH: 0, AWAY: 0.49, HOME_if_not_push: 0.51 }, no_vig: { HOME: 0.5, AWAY: 0.5 },
      difference: { HOME: 0.01, AWAY: -0.01 }, draws: 10000, policy_test: false },
    { market: 'TOTAL', line: 51.5, sportsbook: 'DraftKings (SportsGameOdds, research)', provider: 'SPORTSGAMEODDS', current: true,
      available: false, reason: 'FORECAST_PROVIDES_NO_SCORE_DISTRIBUTION', policy_test: false },
  ],
  coverage: null,
  note: 'Research comparison against the joint simulation only.',
}
