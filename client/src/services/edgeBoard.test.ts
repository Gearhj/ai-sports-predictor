import { describe, expect, it } from 'vitest'
import type { CoverageGame, DecisionItem, Ledger } from './commandCenter'
import { buildEdgeBoard, compareEvaluated, edgeIneligibility, evaluatedCard, plainReason, type BoardInput } from './edgeBoard'

const NOW = Date.parse('2026-09-15T20:00:00Z')
const at = (minutesFromNow: number) => new Date(NOW + minutesFromNow * 60_000).toISOString()

export function game(id: string, opts: Partial<CoverageGame> & { startIn?: number; homeP?: number | null } = {}): CoverageGame {
  const start = opts.startIn ?? 180
  const home = opts.homeP === undefined ? 0.55 : opts.homeP
  return {
    game_id: id, league: 'MLB', matchup: `A${id} @ H${id}`,
    home: { abbreviation: `H${id}`, name: `Home ${id}` }, away: { abbreviation: `A${id}`, name: `Away ${id}` },
    starts_at: at(start), status: 'SCHEDULED', price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0,
    freshest_observed_at: null, stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
    best: { HOME: null, AWAY: null },
    model: home == null ? null : { engine_id: 'MLB_V18_WHOLE_GAME', home, away: 1 - home, home_two_way: home, expected_home: 4.6, expected_away: 4.1, created_at: at(-60) },
    quotes: [],
    checkpoints: { collection_opens_at: at(start - 255), decision_window_opens_at: at(start - 30), decision_window_closes_at: at(start - 15) },
    ...opts,
  }
}

export function priced(g: CoverageGame, consensus = 0.5): CoverageGame {
  return {
    ...g, price_state: 'CURRENT_PRICE', books: 2, fresh_books: 2, consensus_no_vig_home: consensus, consensus_is_current: true,
    freshest_observed_at: at(-10),
    best: { HOME: { american: -110, sportsbook: 'Fanduel', provider: 'SportsGameOdds', observed_at: at(-10) }, AWAY: { american: 105, sportsbook: 'Betmgm', provider: 'SportsGameOdds', observed_at: at(-10) } },
    quotes: [
      { market: 'MONEYLINE', line: null, sportsbook: 'Fanduel', sportsbook_key: 'sgo:fanduel', provider: 'SportsGameOdds', classification: 'RESEARCH', observed_at: at(-10), age_minutes: 10, fresh: true, sides: { HOME: { american: -110, decimal: 1.909 }, AWAY: { american: -110, decimal: 1.909 } }, no_vig_home: consensus - 0.01 },
      { market: 'MONEYLINE', line: null, sportsbook: 'Betmgm', sportsbook_key: 'sgo:betmgm', provider: 'SportsGameOdds', classification: 'RESEARCH', observed_at: at(-10), age_minutes: 10, fresh: true, sides: { HOME: { american: -120, decimal: 1.833 }, AWAY: { american: 105, decimal: 2.05 } }, no_vig_home: consensus + 0.01 },
    ],
  }
}

export function decision(id: string, g: CoverageGame, opts: Partial<DecisionItem> = {}): DecisionItem {
  return {
    decision_id: `d${id}`, sport: 'MLB', experiment: 'X', game_id: g.game_id, matchup: g.matchup, starts_at: g.starts_at,
    phase: 'FINAL_PREGAME', market: 'MONEYLINE', selection: `${g.home!.abbreviation} ML`, line: null, sportsbook: 'Fanduel',
    american: -110, quote_observed_at: at(-20), quote_age_minutes: 20, model_probability: 0.56, market_probability: 0.5,
    edge: 0.04, expected_value: 0.05, recommendation: 'NO_BET', stored_recommendation: 'NO_BET', reason: null, recommended_at: at(-5),
    decision_window: { opens_at: g.checkpoints.decision_window_opens_at, closes_at: g.checkpoints.decision_window_closes_at },
    group: 'PASSED', position: null,
    market_integrity: { two_sided_same_book: true, market_matches: true, provider: 'SportsGameOdds', provider_permitted: true, classification: 'RESEARCH', sportsbook: 'Fanduel', sportsbook_key: 'sgo:fanduel', selected_american: -110, opposite_american: -110, older_leg_observed_at: at(-20), older_leg_age_minutes_at_decision: 15, fresh_at_decision: true },
    forecast_check: { engine_id: 'MLB_V18_WHOLE_GAME', controlling: true, latest_at_decision: true, superseded_since_decision: false, published_at: at(-60) },
    adjusted: { edge: 0.02, expected_value: 0.03, probability: 0.54 },
    plain_reasons: [],
    ...opts,
  }
}

const input = (decisions: DecisionItem[], games: CoverageGame[], ledger: Ledger | null = null): BoardInput => ({
  decisions, games, ledger, now: NOW,
  decisionsHref: (s) => `/decisions?sport=${s}`,
  analyzeHref: (s, id) => `/games/${id}?sport=${s}`,
})

describe('edge eligibility', () => {
  const g = priced(game('1'))
  it('accepts a complete, fresh, controlling evaluation', () => {
    expect(edgeIneligibility(decision('1', g))).toBeNull()
  })
  it.each([
    ['not evaluated', { recommendation: 'NOT_YET_EVALUATED' }],
    ['incomplete market', { market_integrity: { ...decision('x', g).market_integrity!, two_sided_same_book: false } }],
    ['incomplete market', { market_integrity: { ...decision('x', g).market_integrity!, market_matches: false } }],
    ['stale quote', { market_integrity: { ...decision('x', g).market_integrity!, fresh_at_decision: false } }],
    ['provider not permitted', { market_integrity: { ...decision('x', g).market_integrity!, provider_permitted: false } }],
    ['no controlling forecast', { forecast_check: { ...decision('x', g).forecast_check!, controlling: false } }],
    ['no controlling forecast', { forecast_check: null }],
    ['incomplete market', { market_integrity: null }],
    ['missing edge', { edge: null, adjusted: null }],
    ['forecast superseded', { phase: 'MORNING', forecast_check: { ...decision('x', g).forecast_check!, superseded_since_decision: true } }],
  ] as const)('rejects %s', (why, patch) => {
    expect(edgeIneligibility(decision('1', g, patch as Partial<DecisionItem>))).toBe(why)
  })
})

describe('ranking', () => {
  it('orders by status, then adjusted edge, then expected return, then urgency', () => {
    const g1 = priced(game('1', { startIn: 200 }))
    const g2 = priced(game('2', { startIn: 100 }))
    const g3 = priced(game('3', { startIn: 150 }))
    const g4 = priced(game('4', { startIn: 120 }))
    const g5 = priced(game('5', { startIn: 110 }))
    const cards = [
      evaluatedCard(decision('a', g1, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.03, expected_value: 0.05, probability: null } }), g1, null, ''),
      evaluatedCard(decision('b', g2, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.06, expected_value: 0.02, probability: null } }), g2, null, ''),
      evaluatedCard(decision('c', g3, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.03, expected_value: 0.09, probability: null } }), g3, null, ''),
      evaluatedCard(decision('d', g4, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.03, expected_value: 0.05, probability: null } }), g4, null, ''),
      evaluatedCard(decision('e', g5, { recommendation: 'NO_BET', adjusted: { edge: 0.5, expected_value: 0.5, probability: null } }), g5, null, ''),
    ].sort(compareEvaluated)
    // b: largest edge; c: tie on edge, higher EV; d before a: same edge and EV, sooner window; e: declined last despite its edge.
    expect(cards.map((c) => c.gameId)).toEqual(['2', '3', '4', '1', '5'])
  })

  it('puts a recorded position ahead of a qualified game with a larger edge', () => {
    const g1 = priced(game('1'))
    const g2 = priced(game('2'))
    const board = buildEdgeBoard(input([
      decision('a', g1, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.09, expected_value: 0.1, probability: null } }),
      decision('b', g2, { recommendation: 'QUALIFIED_BET', adjusted: { edge: 0.01, expected_value: 0.01, probability: null }, position: { position_id: 'p', stake: 10, status: 'OPEN', result: null, profit_loss: null } }),
    ], [g1, g2]))
    expect(board.cards.map((c) => [c.gameId, c.status])).toEqual([['2', 'POSITION'], ['1', 'QUALIFIED']])
  })

  it('keeps a positioned game even after it starts, and a settled position with its result', () => {
    const g1 = priced(game('1', { startIn: -60, status: 'FINAL' }))
    const g2 = priced(game('2', { startIn: 90 }))
    const board = buildEdgeBoard(input([
      decision('a', g1, { recommendation: 'QUALIFIED_BET', position: { position_id: 'p', stake: 10, status: 'SETTLED', result: 'WIN', profit_loss: 9.1 } }),
      decision('b', g1, { selection: `${g1.away!.abbreviation} ML`, recommendation: 'NO_BET' }),
      decision('c', g2, { recommendation: 'NO_BET' }),
    ], [g1, g2]))
    expect(board.state).toBe('BEST_EDGES')
    expect(board.cards[0]).toMatchObject({ gameId: '1', status: 'POSITION', position: { result: 'WIN', profitLoss: 9.1 } })
    expect(board.cards.map((c) => c.gameId)).toEqual(['1', '2'])
  })

  it('shows at most four cards and never pads', () => {
    const games = ['1', '2', '3', '4', '5', '6'].map((id) => priced(game(id)))
    const many = buildEdgeBoard(input(games.map((g) => decision(g.game_id, g, { recommendation: 'QUALIFIED_BET' })), games))
    expect(many.cards).toHaveLength(4)
    expect(many.available).toBe(6)
    const two = buildEdgeBoard(input(games.slice(0, 2).map((g) => decision(g.game_id, g, { recommendation: 'QUALIFIED_BET' })), games))
    expect(two.cards).toHaveLength(2)
    expect(two.cards.every((c) => c.kind === 'evaluated')).toBe(true)
  })
})

describe('hero states', () => {
  it('Closest to a Play when evaluated games exist but none qualifies, with a plain reason', () => {
    const g = priced(game('1'))
    const board = buildEdgeBoard(input([decision('a', g, { plain_reasons: ['Adjusted edge -0.1 pts below the 2.0 pt minimum (penalties: starter 2.0, lineup 0.0)'] })], [g]))
    expect(board.state).toBe('CLOSEST')
    expect(board.title).toBe('Closest to a Play')
    expect(board.cards[0].status).toBe('DECLINED')
    expect(board.cards[0].reason).toBe('Adjusted edge -0.1 pts below the 2.0 pt minimum')
  })

  it('Model Leans when only forecasts exist: ranked by conviction, no edge, no EV, market pending', () => {
    const games = [game('1', { homeP: 0.52 }), game('2', { homeP: 0.31 }), game('3', { homeP: 0.6 })]
    const board = buildEdgeBoard(input([], games))
    expect(board.state).toBe('MODEL_LEANS')
    expect(board.subtitle).toBe('Sportsbook markets pending')
    expect(board.cards.map((c) => c.gameId)).toEqual(['2', '3', '1'])
    expect(board.cards[0]).toMatchObject({ side: 'AWAY', status: 'MARKET_PENDING', edge: null, expectedValue: null, range: null, consensus: null })
    expect(board.cards[0].modelProbability).toBeCloseTo(0.69)
  })

  it('a later game with an early forecast never hides earlier games still awaiting theirs', () => {
    // The 2026-09-18 Home report: two evening CFB forecasts (published at midday)
    // displaced every MLB game, whose forecasts arrive near first pitch.
    const games = [
      game('mlb-1840', { homeP: null, startIn: 170 }), game('mlb-1905', { homeP: null, startIn: 195 }),
      game('cfb-1930', { homeP: 0.23, startIn: 220, league: 'CFB' }), game('cfb-2000', { homeP: 0.56, startIn: 250, league: 'CFB' }),
    ]
    const board = buildEdgeBoard(input([], games))
    expect(board.state).toBe('MODEL_LEANS')
    expect(board.cards.map((c) => c.gameId)).toEqual(['mlb-1840', 'mlb-1905', 'cfb-1930', 'cfb-2000'])
    expect(board.cards.map((c) => c.status)).toEqual(['ON_DECK', 'ON_DECK', 'MARKET_PENDING', 'MARKET_PENDING'])
  })

  it('partial price coverage: leans on priced games carry the market rail but still no edge', () => {
    const board = buildEdgeBoard(input([], [priced(game('1', { homeP: 0.6 }), 0.52), game('2', { homeP: 0.3 })]))
    expect(board.subtitle).toBe('Markets arriving · final evaluations pending')
    const pricedCard = board.cards.find((c) => c.gameId === '1')!
    expect(pricedCard).toMatchObject({ status: 'EVALUATION_PENDING', edge: null, expectedValue: null })
    expect(pricedCard.range).not.toBeNull()
    expect(board.cards.find((c) => c.gameId === '2')!.range).toBeNull()
  })

  it('a stale reference price is never a current market on a lean card', () => {
    const stale = { ...priced(game('1', { homeP: 0.6 })), price_state: 'STORED_REFERENCE_ONLY' as const, consensus_is_current: false, best: { HOME: null, AWAY: null } }
    const card = buildEdgeBoard(input([], [stale])).cards[0]
    expect(card).toMatchObject({ range: null, consensus: null, price: { basis: 'REFERENCE', american: null } })
  })

  it('Games on Deck when neither forecasts nor evaluations exist, ordered by decision window', () => {
    const board = buildEdgeBoard(input([], [game('1', { homeP: null, startIn: 300 }), game('2', { homeP: null, startIn: 120 })]))
    expect(board.state).toBe('ON_DECK')
    expect(board.subtitle).toBe('Forecasts and sportsbook markets still to arrive')
    expect(board.cards.map((c) => c.gameId)).toEqual(['2', '1'])
    const collected = buildEdgeBoard(input([], [priced(game('1', { homeP: null, startIn: 300 })), game('2', { homeP: null, startIn: 120 })]))
    expect(collected.subtitle).toBe('Forecasts pending · markets collected for 1 of 2 games')
    expect(collected.cards.every((c) => c.edge == null && c.range == null)).toBe(true)
    expect(board.cards.every((c) => c.status === 'ON_DECK' && c.edge == null && c.modelProbability == null)).toBe(true)
  })

  it('an incomplete or stale market never produces an edge card; the game falls back to a lean', () => {
    const g = priced(game('1', { homeP: 0.58 }))
    const board = buildEdgeBoard(input([
      decision('a', g, { recommendation: 'QUALIFIED_BET', market_integrity: { ...decision('x', g).market_integrity!, fresh_at_decision: false } }),
    ], [g]))
    expect(board.state).toBe('MODEL_LEANS')
    expect(board.cards[0]).toMatchObject({ kind: 'lean', edge: null })
  })

  it('no premature Declined: a not-yet-evaluated decision is not a card status', () => {
    const g = game('1', { homeP: 0.58 })
    const board = buildEdgeBoard(input([decision('a', g, { recommendation: 'NOT_YET_EVALUATED', market_integrity: null })], [g]))
    expect(board.cards.some((c) => c.status === 'DECLINED')).toBe(false)
    expect(board.cards[0].status).toBe('MARKET_PENDING')
  })

  it('a pre-final evaluation is Watching, never Qualified or Declined', () => {
    const g = priced(game('1'))
    const card = buildEdgeBoard(input([decision('a', g, { phase: 'MORNING', recommendation: 'QUALIFIED_BET' })], [g])).cards[0]
    expect(card).toMatchObject({ status: 'WATCHING', preFinal: true })
  })

  it('uses the best current price when current, else labels the price as at evaluation', () => {
    const g = priced(game('1'))
    expect(evaluatedCard(decision('a', g), g, null, '').price?.basis).toBe('BEST_CURRENT')
    const unpriced = game('1')
    expect(evaluatedCard(decision('a', unpriced), unpriced, null, '').price).toMatchObject({ basis: 'AT_EVALUATION', american: -110 })
  })

  it('orients probabilities and the book range to the selected side', () => {
    const g = priced(game('1'), 0.6)
    const card = evaluatedCard(decision('a', g, { selection: `${g.away!.abbreviation} ML` }), g, null, '')
    expect(card.side).toBe('AWAY')
    expect(card.consensus).toBeCloseTo(0.4)
    expect(card.range![0]).toBeCloseTo(0.39)
    expect(card.range![1]).toBeCloseTo(0.41)
  })

  it('plain reasons drop machine codes and policy prefixes', () => {
    const g = game('1')
    expect(plainReason(decision('a', g, { recommendation: 'WATCH', plain_reasons: [] }))).toBe('Edge is below the qualifying threshold')
    expect(plainReason(decision('a', g, { plain_reasons: ['Exact MONEYLINE selection is WATCH under the existing policy'] }))).toBe('Did not clear the qualifying thresholds')
  })
})
