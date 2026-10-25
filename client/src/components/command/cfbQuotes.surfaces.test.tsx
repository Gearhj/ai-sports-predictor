/**
 * College Football quotes on Daily Decisions and Home: the three clocks travel
 * with the quote, and a recorded checkpoint states whether it was a priced
 * no-bet, a declined no-price, or a block.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { describeDecision } from './CommandDecisions'
import { cardQuoteClocks } from './EdgeBoard'
import { WhyDetails } from './TopOpportunity'
import type { CoverageGame, DecisionItem } from '../../services/commandCenter'
import type { Outcome } from '../../services/topPlay'
import type { AssessedQuote } from '../../services/probability'
import { fixtureClocks } from '../../test/cfbQuoteFixture'

afterEach(cleanup)

function cfbItem(recommendation: string, reason: string, cls: 'NO_BET' | 'NO_PRICE' | 'BLOCKED'): DecisionItem {
  return {
    decision_id: 'd1', sport: 'CFB', experiment: 'ASP CFB', game_id: 'g1', matchup: 'Away U @ Home U', starts_at: null,
    phase: 'FINAL_PREGAME', market: 'MONEYLINE', selection: 'NONE', line: null, sportsbook: null, american: null,
    quote_observed_at: null, quote_age_minutes: null, model_probability: null, market_probability: null, edge: null,
    expected_value: null, recommendation, stored_recommendation: recommendation, reason: 'recorded detail',
    recommended_at: '2026-09-26T19:31:00Z', decision_window: null, group: 'WATCHING', position: null,
    checkpoint_semantics: { class: cls, reason, evaluated: cls !== 'BLOCKED', priced: cls === 'NO_BET', text: 'x' },
  }
}

describe('Daily Decisions · CFB checkpoint meaning', () => {
  it('a priced no-bet, a declined no-price and a block read differently', () => {
    expect(describeDecision(cfbItem('DECLINED', 'NO_QUALIFYING_EDGE', 'NO_BET'), null, false).label)
      .toBe('No bet · priced, edge below policy')
    expect(describeDecision(cfbItem('DECLINED', 'NO_TWO_SIDED_QUOTE', 'NO_PRICE'), null, false).label)
      .toBe('Declined · no same-book two-sided moneyline was stored')
    const blocked = describeDecision(cfbItem('BLOCKED', 'AWAITING_PRODUCER_ELIGIBILITY', 'BLOCKED'), null, false).label
    expect(blocked).not.toMatch(/No bet|Declined/)
  })
})

const quote: AssessedQuote = {
  market: 'MONEYLINE', line: null, sportsbook: 'DraftKings (SportsGameOdds, research)', sportsbook_key: 'sgo:draftkings',
  provider: 'SportsGameOdds', classification: 'RESEARCH', observed_at: fixtureClocks.older_leg_at!, age_minutes: 25,
  fresh: true, state: 'CURRENT', clocks: fixtureClocks,
  sides: { HOME: { american: -150, decimal: 1.667, no_vig: 0.58, model: null, edge: null, expected_return: null, edge_reason: null } },
} as unknown as AssessedQuote

describe('Daily Decisions · Why? shows every clock', () => {
  it('lists the quote, fetch and receipt clocks for a CFB quote', () => {
    const outcome = {
      key: 'MONEYLINE|HOME|', market: 'MONEYLINE', side: 'HOME', line: null, ownLine: null, label: 'Home U to win',
      best: { quote, side: quote.sides.HOME, sportsbook: 'DraftKings', american: -150, decimal: 1.667, current: true },
      books: [], masses: null, expectedProfit: null, expectedReturn: null, blocked: 'PENDING', rank: null,
    } as unknown as Outcome
    render(<WhyDetails outcome={outcome} channel={null} />)
    expect(screen.getByTestId('quote-clocks')).toHaveTextContent('quote 3:10 PM ET (older leg; home 3:10 PM, away 3:13 PM) · 25 min old · fetched 3:20 PM ET · received 3:20 PM ET')
  })
})

describe('Home · card quote clocks', () => {
  it('uses the card book’s CFB moneyline clocks, and nothing for a quote without them', () => {
    const game = { quotes: [
      { market: 'TOTAL', sportsbook: 'DraftKings', clocks: fixtureClocks },
      { market: 'MONEYLINE', sportsbook: 'DraftKings (SportsGameOdds, research)', clocks: fixtureClocks },
    ] } as unknown as CoverageGame
    expect(cardQuoteClocks({ game, price: { sportsbook: 'DraftKings', american: -150, provider: null, classification: null, observedAt: null, basis: 'BEST_CURRENT' } }))
      .toBe(fixtureClocks)
    const mlb = { quotes: [{ market: 'MONEYLINE', sportsbook: 'FanDuel' }] } as unknown as CoverageGame
    expect(cardQuoteClocks({ game: mlb, price: null })).toBeNull()
  })
})
