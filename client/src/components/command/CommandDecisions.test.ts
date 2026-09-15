/**
 * The decision feed: plain-language states, ordering by next action, and no
 * category reserving space it does not use.
 */
import { describe, expect, it } from 'vitest'
import { buildFeed, countdown, describeDecision } from './CommandDecisions'
import type { CoverageGame, DecisionItem } from '../../services/commandCenter'

const NOW = Date.parse('2026-09-15T16:00:00Z')

function game(id: string, starts: string, over: Partial<CoverageGame> = {}): CoverageGame {
  const start = Date.parse(starts)
  return {
    game_id: id, league: 'MLB', matchup: `A${id} @ H${id}`, home: { abbreviation: `H${id}`, name: 'Home' },
    away: { abbreviation: `A${id}`, name: 'Away' }, starts_at: starts, status: 'SCHEDULED',
    price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0, freshest_observed_at: null,
    stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
    best: { HOME: null, AWAY: null }, model: null, quotes: [],
    checkpoints: {
      collection_opens_at: new Date(start - 255 * 60_000).toISOString(),
      decision_window_opens_at: new Date(start - 30 * 60_000).toISOString(),
      decision_window_closes_at: new Date(start - 15 * 60_000).toISOString(),
    },
    ...over,
  }
}

function item(gameId: string, over: Partial<DecisionItem> = {}): DecisionItem {
  return {
    decision_id: `d-${gameId}`, sport: 'MLB', experiment: 'ASP MLB V18', game_id: gameId, matchup: 'x', starts_at: null,
    phase: 'MORNING', market: 'MONEYLINE', selection: 'HOME ML', line: null, sportsbook: null, american: null,
    quote_observed_at: null, quote_age_minutes: null, model_probability: null, market_probability: null, edge: null,
    expected_value: null, recommendation: 'NOT_YET_EVALUATED', stored_recommendation: 'NO_BET',
    reason: 'No complete two-sided governed MONEYLINE quote is available.', recommended_at: null,
    decision_window: null, group: 'WATCHING', position: null, ...over,
  }
}

describe('describeDecision', () => {
  it('translates internal codes into short language and never calls an unevaluated game a pass', () => {
    const waiting = describeDecision(item('1'), game('1', '2026-09-15T22:40:00Z'), false)
    expect(waiting.label).toBe('Waiting for the forecast')
    expect(waiting.tone).toBe('gold')
    expect(waiting.label).not.toMatch(/NOT_YET_EVALUATED|NO_BET/)
    // The raw reason is kept for the expandable detail.
    expect(waiting.detail).toMatch(/two-sided/)
  })

  it('marks a final-pregame qualification green and a final pass red', () => {
    expect(describeDecision(item('1', { phase: 'FINAL_PREGAME', recommendation: 'QUALIFIED_BET' }), null, false).tone).toBe('green')
    expect(describeDecision(item('1', { phase: 'FINAL_PREGAME', recommendation: 'NO_BET' }), null, false).tone).toBe('red')
  })

  it('does not treat a morning qualification as actionable', () => {
    const morning = describeDecision(item('1', { phase: 'MORNING', recommendation: 'QUALIFIED_BET' }), null, false)
    expect(morning.tone).toBe('gold')
  })
})

describe('buildFeed', () => {
  it('orders a day where every game is waiting by the next decision window', () => {
    const games = [game('late', '2026-09-16T01:40:00Z'), game('early', '2026-09-15T22:40:00Z'), game('mid', '2026-09-15T23:10:00Z')]
    const feed = buildFeed([], games, [], NOW)
    expect(feed.map((c) => c.key)).toEqual(['early', 'mid', 'late'])
    expect(new Set(feed.map((c) => c.tab))).toEqual(new Set(['WATCHING']))
  })

  it('puts a recorded open position ahead of games that are only waiting', () => {
    const games = [game('w', '2026-09-15T22:40:00Z'), game('p', '2026-09-16T00:10:00Z')]
    const items = [item('p', { phase: 'FINAL_PREGAME', recommendation: 'QUALIFIED_BET', position: { position_id: 'x', stake: 5, status: 'OPEN', result: null, profit_loss: null } })]
    const feed = buildFeed(items, games, [], NOW)
    expect(feed[0].key).toBe('p')
    expect(feed[0].tab).toBe('POSITIONS')
  })
})

describe('countdown', () => {
  it('reads hours and minutes, and says now once the moment has passed', () => {
    expect(countdown('2026-09-15T18:25:00Z', NOW)).toBe('2h 25m')
    expect(countdown('2026-09-15T15:00:00Z', NOW)).toBe('now')
  })
})
