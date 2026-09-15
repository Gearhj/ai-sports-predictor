/**
 * Deterministic read-model fixtures for Home: the decision board, market
 * coverage and ledger shapes the page reads, built relative to a fixed "now".
 * Used by unit tests and, serialized, by browser verification through request
 * interception. Never written to a database.
 */
import type { CoverageGame, DecisionBoard, DecisionItem, Ledger, MarketCoverage, SportCode } from '../services/commandCenter'

export interface Clock { now: number }

const iso = (clock: Clock, minutes: number) => new Date(clock.now + minutes * 60_000).toISOString()

const TEAMS: Record<SportCode, [string, string][]> = {
  MLB: [['NYY', 'BOS'], ['LAD', 'SF'], ['ATL', 'PHI'], ['HOU', 'SEA'], ['CHC', 'STL'], ['SD', 'COL'], ['TB', 'TOR'], ['MIL', 'PIT']],
  NFL: [['KC', 'BUF'], ['DET', 'GB']],
  NBA: [['BOS', 'NYK']],
  CFB: [['UGA', 'BAMA']],
}

export function game(clock: Clock, id: string, opts: Partial<CoverageGame> & { startIn?: number; homeP?: number | null; sport?: SportCode; pair?: number } = {}): CoverageGame {
  const { startIn = 180, homeP = 0.55, sport = 'MLB', pair = Number(id) % 8, ...rest } = opts
  const [away, home] = TEAMS[sport][pair % TEAMS[sport].length] ?? [`A${id}`, `H${id}`]
  return {
    game_id: id, league: sport, matchup: `${away} @ ${home}`,
    home: { abbreviation: home, name: home }, away: { abbreviation: away, name: away },
    starts_at: iso(clock, startIn), status: 'SCHEDULED', price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0,
    freshest_observed_at: null, stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
    best: { HOME: null, AWAY: null },
    model: homeP == null ? null : {
      engine_id: sport === 'NFL' ? 'NFL_M1_XGBOOST' : 'MLB_V18_WHOLE_GAME', home: homeP, away: 1 - homeP, home_two_way: homeP,
      expected_home: 4.6, expected_away: 4.1, created_at: iso(clock, -60),
    },
    quotes: [],
    checkpoints: { collection_opens_at: iso(clock, startIn - 255), decision_window_opens_at: iso(clock, startIn - 30), decision_window_closes_at: iso(clock, startIn - 15) },
    ...rest,
  }
}

export function priced(clock: Clock, g: CoverageGame, consensus = 0.5, current = true): CoverageGame {
  const q = (book: string, key: string, noVig: number, home: number, away: number) => ({
    market: 'MONEYLINE', line: null, sportsbook: book, sportsbook_key: key, provider: 'SportsGameOdds', classification: 'RESEARCH' as const,
    observed_at: iso(clock, current ? -12 : -600), age_minutes: current ? 12 : 600, fresh: current,
    sides: { HOME: { american: home, decimal: home > 0 ? 1 + home / 100 : 1 + 100 / -home }, AWAY: { american: away, decimal: away > 0 ? 1 + away / 100 : 1 + 100 / -away } },
    no_vig_home: noVig,
  })
  return {
    ...g, price_state: current ? 'CURRENT_PRICE' : 'STORED_REFERENCE_ONLY', books: 3, fresh_books: current ? 3 : 0,
    consensus_no_vig_home: consensus, consensus_is_current: current,
    freshest_observed_at: iso(clock, current ? -12 : -600), stalest_usable_observed_at: current ? iso(clock, -12) : null,
    best: current
      ? { HOME: { american: -118, sportsbook: 'Fanduel', provider: 'SportsGameOdds', observed_at: iso(clock, -12) }, AWAY: { american: 112, sportsbook: 'Betmgm', provider: 'SportsGameOdds', observed_at: iso(clock, -12) } }
      : { HOME: null, AWAY: null },
    quotes: [
      q('Fanduel', 'sgo:fanduel', consensus - 0.02, -118, 100),
      q('Betmgm', 'sgo:betmgm', consensus + 0.015, -130, 112),
      q('Draftkings', 'sgo:draftkings', consensus + 0.005, -125, 105),
    ],
  }
}

export function decision(clock: Clock, id: string, g: CoverageGame, opts: Partial<DecisionItem> = {}): DecisionItem {
  return {
    decision_id: `d${id}`, sport: g.league as SportCode, experiment: 'Virtual research', game_id: g.game_id, matchup: g.matchup, starts_at: g.starts_at,
    phase: 'FINAL_PREGAME', market: 'MONEYLINE', selection: `${g.home!.abbreviation} ML`, line: null, sportsbook: 'Fanduel (SportsGameOdds, research)',
    american: -110, quote_observed_at: iso(clock, -20), quote_age_minutes: 20, model_probability: 0.56, market_probability: 0.5,
    edge: 0.04, expected_value: 0.05, recommendation: 'NO_BET', stored_recommendation: 'NO_BET', reason: null, recommended_at: iso(clock, -5),
    decision_window: { opens_at: g.checkpoints.decision_window_opens_at, closes_at: g.checkpoints.decision_window_closes_at },
    group: 'PASSED', position: null,
    market_integrity: {
      two_sided_same_book: true, market_matches: true, provider: 'SportsGameOdds', provider_permitted: true, classification: 'RESEARCH',
      sportsbook: 'Fanduel (SportsGameOdds, research)', sportsbook_key: 'sgo:fanduel', selected_american: -110, opposite_american: -110,
      older_leg_observed_at: iso(clock, -20), older_leg_age_minutes_at_decision: 15, fresh_at_decision: true,
    },
    forecast_check: { engine_id: 'MLB_V18_WHOLE_GAME', controlling: true, latest_at_decision: true, superseded_since_decision: false, published_at: iso(clock, -60) },
    adjusted: { edge: 0.02, expected_value: 0.03, probability: 0.54 },
    plain_reasons: [],
    ...opts,
  }
}

export function ledger(clock: Clock, positions: Ledger['sports'][number]['positions'] = []): Ledger {
  const sport = (code: SportCode, pnl: number) => ({
    sport: code, label: code, account: { funded: true, label: code }, starting_capital: 1000, realized_bankroll: 1000 + pnl, profit_loss: pnl,
    open_exposure: code === 'MLB' ? positions.filter((p) => p.status !== 'SETTLED').reduce((s, p) => s + (p.stake ?? 0), 0) : 0,
    available_capital: 1000 + pnl, open_count: code === 'MLB' ? positions.filter((p) => p.status !== 'SETTLED').length : 0,
    settled_count: 10, record: { WIN: 5, LOSS: 5, PUSH: 0 }, positions: code === 'MLB' ? positions : [],
    series: [{ at: iso(clock, -14 * 1440), bankroll: 1000, kind: 'FUNDING' as const, position_id: null }, { at: iso(clock, -600), bankroll: 1000 + pnl, kind: 'SETTLEMENT' as const, position_id: 'x', change: pnl }],
    reconciled: true,
  })
  const sports = [sport('MLB', 12.4), sport('NFL', -5), sport('NBA', 0), sport('CFB', 0)]
  return {
    schema_version: 'AISP_COMMAND_CENTER_LEDGER_V1', generated_at: iso(clock, -1), authority: 'Virtual research only.',
    totals: {
      starting_capital: 4000, realized_bankroll: 4007.4, profit_loss: 7.4, open_exposure: sports[0].open_exposure, available_capital: 4007.4,
      open_count: sports[0].open_count, settled_count: 40, record: { WIN: 20, LOSS: 20, PUSH: 0 },
    },
    combined_series: [{ at: iso(clock, -14 * 1440), bankroll: 4000, kind: 'FUNDING', position_id: null }, { at: iso(clock, -600), bankroll: 4007.4, kind: 'SETTLEMENT', position_id: 'x', change: 7.4 }],
    sports,
  }
}

export interface Scenario { decisions: DecisionBoard; markets: MarketCoverage; ledger: Ledger }

export function scenario(clock: Clock, games: CoverageGame[], items: DecisionItem[], positions: Ledger['sports'][number]['positions'] = []): Scenario {
  return {
    decisions: { schema_version: 'AISP_COMMAND_CENTER_DECISIONS_V1', date: '2026-09-15', generated_at: iso(clock, -1), counts: { READY: 0, WATCHING: 0, POSITION: 0, PASSED: 0, CLOSED: 0 }, exposure: 0, staked: 0, realized_profit_loss: 0, next_decision_deadline: null, items },
    markets: {
      schema_version: 'AISP_COMMAND_CENTER_MARKET_COVERAGE_V1', date: '2026-09-15', generated_at: iso(clock, -1), freshness_window_minutes: 240,
      summary: { games: games.length, by_state: {}, by_league: {}, sportsbooks: ['Betmgm', 'Draftkings', 'Fanduel'], providers: ['SportsGameOdds'], freshest_observed_at: null, stalest_usable_observed_at: null, next_collection_at: null, latest_snapshot: null },
      provider_calls_made: 0, games,
    },
    ledger: ledger(clock, positions),
  }
}

const position = (clock: Clock, g: CoverageGame, settled: boolean) => ({
  position_id: `p${g.game_id}`, game_id: g.game_id, matchup: g.matchup, starts_at: g.starts_at, market: 'MONEYLINE', selection: `${g.home!.abbreviation} ML`,
  entry: { american: 125, decimal: 2.25, sportsbook: 'Betmgm (SportsGameOdds, research)', sportsbook_key: 'sgo:betmgm', provider: 'SportsGameOdds', classification: 'RESEARCH' as const, quote_observed_at: iso(clock, -40) },
  stake: 12.5, placed_at: iso(clock, -30), status: settled ? 'SETTLED' : 'OPEN', result: settled ? 'WIN' : null, profit_loss: settled ? 15.63 : null,
  settled_at: settled ? iso(clock, -10) : null, experiment: { id: 'e', name: 'Virtual research' },
})

/** The eleven verification states, by name. */
export function namedScenario(name: string, clock: Clock): Scenario {
  const c = clock
  switch (name) {
    case 'pending-all': {
      const games = [0, 1, 2, 3, 4].map((i) => game(c, String(i + 1), { startIn: 200 + i * 35, homeP: null }))
      return scenario(c, games, [])
    }
    case 'forecasts-only': {
      const games = [0.61, 0.44, 0.7, 0.52, 0.35, 0.58].map((p, i) => game(c, String(i + 1), { startIn: 190 + i * 20, homeP: p }))
      return scenario(c, games, [])
    }
    case 'partial-prices': {
      const games = [0.61, 0.44, 0.7, 0.52, 0.35].map((p, i) => game(c, String(i + 1), { startIn: 150 + i * 30, homeP: p }))
      games[0] = priced(c, games[0], 0.57)
      games[2] = priced(c, games[2], 0.64)
      return scenario(c, games, [])
    }
    case 'four-plus': {
      const games = [0, 1, 2, 3, 4, 5].map((i) => priced(c, game(c, String(i + 1), { startIn: 40 + i * 25, homeP: 0.5 + i * 0.03 }), 0.46 + i * 0.02))
      const recs = ['QUALIFIED_BET', 'QUALIFIED_BET', 'NO_BET', 'QUALIFIED_BET', 'NO_BET', 'NO_BET']
      const items = games.map((g, i) => decision(c, g.game_id, g, {
        recommendation: recs[i], stored_recommendation: recs[i], group: recs[i] === 'QUALIFIED_BET' ? 'READY' : 'PASSED',
        model_probability: 0.52 + i * 0.03, market_probability: 0.46 + i * 0.02,
        adjusted: { edge: [0.071, 0.034, 0.012, 0.052, -0.004, 0.009][i], expected_value: [0.18, 0.07, 0.02, 0.11, -0.01, 0.015][i], probability: null },
        plain_reasons: recs[i] === 'NO_BET' ? ['Adjusted edge 1.2 pts below the 2.0 pt minimum (penalties: starter 2.0, lineup 0.0)'] : [],
      }))
      return scenario(c, games, items)
    }
    case 'fewer-than-four': {
      const games = [0, 1].map((i) => priced(c, game(c, String(i + 1), { startIn: 45 + i * 30, homeP: 0.58 }), 0.5))
      const items = [
        decision(c, '1', games[0], { recommendation: 'QUALIFIED_BET', stored_recommendation: 'QUALIFIED_BET', group: 'READY', model_probability: 0.58, market_probability: 0.5, adjusted: { edge: 0.06, expected_value: 0.14, probability: null } }),
        decision(c, '2', games[1], { phase: 'LINEUP_CONFIRMED', recommendation: 'WATCH', stored_recommendation: 'WATCH', group: 'WATCHING', adjusted: null, edge: 0.015, expected_value: 0.02 }),
      ]
      return scenario(c, [...games, game(c, '3', { startIn: 400, homeP: null })], items)
    }
    case 'no-qualifiers': {
      const games = [0, 1, 2].map((i) => priced(c, game(c, String(i + 1), { startIn: 40 + i * 20, homeP: 0.52 }), 0.51))
      const items = games.map((g, i) => decision(c, g.game_id, g, {
        adjusted: { edge: [0.011, -0.002, 0.006][i], expected_value: [0.01, -0.02, 0.004][i], probability: null },
        plain_reasons: ['Adjusted edge below the 2.0 pt minimum (penalties: starter 2.0, lineup 0.0)', 'Adjusted expected value is not positive'],
      }))
      return scenario(c, games, items)
    }
    case 'open-position': {
      const games = [priced(c, game(c, '1', { startIn: 10, homeP: 0.6 }), 0.52), priced(c, game(c, '2', { startIn: 90, homeP: 0.57 }), 0.5)]
      const pos = position(c, games[0], false)
      const items = [
        decision(c, '1', games[0], { recommendation: 'QUALIFIED_BET', stored_recommendation: 'QUALIFIED_BET', group: 'POSITION', model_probability: 0.6, market_probability: 0.52, adjusted: { edge: 0.058, expected_value: 0.12, probability: null }, position: { position_id: pos.position_id, stake: 12.5, status: 'OPEN', result: null, profit_loss: null } }),
        decision(c, '2', games[1], { recommendation: 'QUALIFIED_BET', stored_recommendation: 'QUALIFIED_BET', group: 'READY', model_probability: 0.57, market_probability: 0.5, adjusted: { edge: 0.041, expected_value: 0.09, probability: null } }),
      ]
      return scenario(c, games, items, [pos])
    }
    case 'settled-position': {
      const finished = { ...priced(c, game(c, '1', { startIn: -200, homeP: 0.6 }), 0.52), status: 'FINAL' }
      const later = priced(c, game(c, '2', { startIn: 120, homeP: 0.55 }), 0.52)
      const pos = position(c, finished, true)
      const items = [
        decision(c, '1', finished, { recommendation: 'QUALIFIED_BET', stored_recommendation: 'QUALIFIED_BET', group: 'POSITION', adjusted: { edge: 0.05, expected_value: 0.1, probability: null }, position: { position_id: pos.position_id, stake: 12.5, status: 'SETTLED', result: 'WIN', profit_loss: 15.63 } }),
        decision(c, '2', later, { adjusted: { edge: 0.004, expected_value: 0.002, probability: null }, plain_reasons: ['Adjusted edge 1.6 pts below the 2.0 pt minimum'] }),
      ]
      return scenario(c, [finished, later], items, [pos])
    }
    case 'stale-reference': {
      const games = [priced(c, game(c, '1', { startIn: 300, homeP: 0.63 }), 0.55, false), game(c, '2', { startIn: 330, homeP: 0.41 })]
      return scenario(c, games, [])
    }
    case 'mixed-sports': {
      const games = [
        priced(c, game(c, '1', { startIn: 60, homeP: 0.57 }), 0.5),
        priced(c, game(c, '11', { sport: 'NFL', startIn: 95, homeP: 0.62, pair: 0 }), 0.55),
        game(c, '21', { sport: 'NBA', startIn: 240, homeP: null, pair: 0 }),
        game(c, '31', { sport: 'CFB', startIn: 200, homeP: null, pair: 0 }),
      ]
      const items = [
        decision(c, '1', games[0], { recommendation: 'QUALIFIED_BET', stored_recommendation: 'QUALIFIED_BET', group: 'READY', model_probability: 0.57, market_probability: 0.5, adjusted: { edge: 0.05, expected_value: 0.11, probability: null } }),
        decision(c, '11', games[1], { sport: 'NFL', recommendation: 'NO_BET', model_probability: 0.62, market_probability: 0.55, adjusted: null, edge: 0.012, expected_value: 0.02,
          forecast_check: { engine_id: 'NFL_M1_XGBOOST', controlling: true, latest_at_decision: true, superseded_since_decision: false, published_at: iso(c, -90) } }),
      ]
      return scenario(c, games, items)
    }
    case 'shared-start': {
      const start = 140
      const games = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => game(c, String(i + 1), { startIn: i < 4 ? start : i < 6 ? start + 25 : start + 60, homeP: [0.6, 0.45, 0.66, 0.51, 0.39, 0.58, 0.62, 0.48][i] }))
      games[0] = priced(c, games[0], 0.55)
      games[1] = priced(c, games[1], 0.47)
      return scenario(c, games, [])
    }
    default:
      throw new Error(`unknown scenario ${name}`)
  }
}

export const SCENARIOS = ['pending-all', 'forecasts-only', 'partial-prices', 'four-plus', 'fewer-than-four', 'no-qualifiers', 'open-position', 'settled-position', 'stale-reference', 'mixed-sports', 'shared-start']
