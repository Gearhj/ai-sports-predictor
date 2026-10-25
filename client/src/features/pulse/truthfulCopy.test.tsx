/**
 * Copy that used to say something false (MAIN audit, 2026-10-16), each now read from evidence:
 *
 *   * "refreshes about 1 h before kickoff" for every sport -- false for College Football,
 *     whose forecasts are published days ahead and re-issued only when inputs change;
 *   * a game past its start with no live feed (College Football, NHL) shown as upcoming;
 *   * a Model-vs-Vegas gap read as an open edge when the policy cannot act on the game;
 *   * College Football "no price / authorization expired" while The Odds API CFB lane is on;
 *   * the NHL board saying no prices and no positions while its paper era holds both.
 *
 * Fixtures are invented; every clock is fixed.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CoverageGame, DecisionItem } from '../../services/commandCenter'
import { FORECAST_TIMING_SUMMARY, forecastRefreshText } from '../../services/forecastCadence'
import { marketLine, ODDS_API_LANE_ACTIVE, type MarketStatus } from '../../services/pregameStatus'
import { NhlNoMarket } from '../../components/nhl/NhlForecastCard'
import type { NhlGameRow } from '../../types/nhlResearch'
import { bettableNow, buildGame } from './pulseData'

afterEach(() => { cleanup(); vi.useRealTimers() })

const NOW = Date.parse('2026-10-17T19:00:00Z')

function cov(over: Partial<CoverageGame> = {}): CoverageGame {
  return {
    game_id: 'g1', league: 'CFB', matchup: 'AWY @ HME', home: { abbreviation: 'HME', name: 'Home' },
    away: { abbreviation: 'AWY', name: 'Away' }, starts_at: '2026-10-17T19:30:00Z', status: 'SCHEDULED',
    price_state: 'CURRENT_PRICE', books: 1, fresh_books: 1, freshest_observed_at: '2026-10-17T18:20:00Z',
    stalest_usable_observed_at: '2026-10-17T18:20:00Z', consensus_no_vig_home: 0.5, consensus_is_current: true,
    best: { HOME: null, AWAY: null },
    model: { engine_id: 'CFB_GEN1_JOINT_BASELINE', home: 0.6, away: 0.4, home_two_way: 0.6, created_at: '2026-10-11T05:00:00Z' },
    quotes: [],
    checkpoints: { collection_opens_at: '2026-10-17T18:15:00Z', decision_window_opens_at: '2026-10-17T19:00:00Z', decision_window_closes_at: '2026-10-17T19:15:00Z' },
    ...over,
  }
}

describe('forecast refresh copy', () => {
  it('says each sport\'s own cadence, never one rule for all', () => {
    expect(forecastRefreshText('CFB')).toBe('inputs re-checked every 15 min; forecast re-issued only when inputs change')
    expect(forecastRefreshText('MLB')).toBe('final forecast about 2–3 h before first pitch')
    expect(forecastRefreshText('NFL')).toBe('final forecast about 1 h before kickoff')
    expect(forecastRefreshText('nhl')).toBe('forecast run daily at 9:20 AM ET')
    expect(FORECAST_TIMING_SUMMARY).not.toMatch(/about an hour before each game/)
  })
})

describe('pulse phases and bettable state', () => {
  it('a started game with no score is in progress, never upcoming', () => {
    vi.useFakeTimers({ toFake: ['Date'], now: Date.parse('2026-10-17T20:10:00Z') })
    const started = buildGame(cov({ league: 'NHL' }), '2026-10-17', null, null, undefined)!
    expect(started.phase).toBe('STARTED')
    vi.setSystemTime(Date.parse('2026-10-17T19:00:00Z'))
    expect(buildGame(cov(), '2026-10-17', null, null, undefined)!.phase).toBe('UPCOMING')
    vi.setSystemTime(Date.parse('2026-10-18T03:00:00Z'))
    expect(buildGame(cov(), '2026-10-17', null, null, undefined)!.phase).toBe('STALE')
  })

  it('a gap on a game the policy cannot act on says why, from the server\'s readings', () => {
    vi.useFakeTimers({ toFake: ['Date'], now: NOW - 3600_000 })
    expect(bettableNow(cov(), null, null)).toEqual({ ok: true, label: 'decided 30–15 min before start' })
    const lapsed = cov({ model_status: { state: 'ELIGIBILITY_EXPIRED' } as CoverageGame['model_status'] })
    expect(bettableNow(lapsed, null, null)).toEqual({ ok: false, label: 'not bettable now · eligibility lease expired' })
    const decided = { phase: 'FINAL_PREGAME', recommendation: 'NO_BET' } as DecisionItem
    expect(bettableNow(cov(), decided, null).label).toBe('decided · no bet')
    vi.setSystemTime(Date.parse('2026-10-17T19:20:00Z'))
    expect(bettableNow(cov(), null, null)).toEqual({ ok: false, label: 'decision window closed' })
    const game = buildGame(lapsed, '2026-10-17', null, null, undefined)!
    expect(game.lean).not.toBeNull()
    expect(game.bettable.ok).toBe(false)
  })
})

describe('College Football price copy', () => {
  const base: MarketStatus = {
    state: 'NO_QUOTES_YET', rule: '', books_two_sided: 0, books_fresh_now: 0, books_fresh_through_window: 0,
    oldest_leg_observed_at: null, newest_leg_observed_at: null, oldest_leg_age_minutes: null, fresh_until: null,
    earliest_expiry: null, window_opens_at: '2026-10-17T19:00:00Z', window_closes_at: '2026-10-17T19:15:00Z',
    quotes_stored: 0,
    collection: { lane: 'THE_ODDS_API_CFB', active: true, lane_enabled: true, allocation_state: ODDS_API_LANE_ACTIVE,
      objects_allowed: 0, spent_on_lane: null, explanation: null, provider: 'The Odds API',
      opens_at: '2026-10-17T18:15:00Z', closes_at: '2026-10-17T18:55:00Z' },
  }

  it('names The Odds API window before it, and an unbound game after it -- never an expired authorization', () => {
    vi.useFakeTimers({ toFake: ['Date'], now: Date.parse('2026-10-17T16:00:00Z') })
    const before = marketLine(base)!
    expect(before.label).toBe('No quotes yet')
    expect(before.detail).toContain('The Odds API prices this game once, 75–35 minutes before kickoff (2:15 PM–2:55 PM ET)')
    vi.setSystemTime(Date.parse('2026-10-17T19:05:00Z'))
    const after = marketLine(base)!
    expect(after.label).toBe('No quote bound')
    expect(after.detail).toMatch(/did not list it, or its team names or kickoff did not match/)
    for (const line of [before, after]) expect(line.detail).not.toMatch(/authorization|allocation|SportsGameOdds/i)
  })
})

describe('NHL board copy', () => {
  const row = (over: Partial<NhlGameRow> = {}) => ({
    home: { name: 'Anaheim Ducks', abbreviation: 'ANA', score: null, canonical_team_id: null },
    away: { name: 'Boston Bruins', abbreviation: 'BOS', score: null, canonical_team_id: null },
    markets: { status: 'NHL_MARKET_PRICES_COLLECTED', statement: 'The Odds API NHL lane collects each game once. 568 NHL quotes stored.' },
    wagering: { enabled: true, statement: 'Virtual paper only, no real money. 1 paper position recorded (1 open, 0 settled).' },
    paper: {
      decision: { decision_id: 'd1', recommendation: 'QUALIFIED_BET', reason: 'ALL_GATES_PASSED', detail: null,
        recorded_at: '2026-10-17T01:31:57Z', selection: 'AWAY', sportsbook: 'DraftKings', american_odds: 130,
        model_probability: 0.564, no_vig_probability: 0.43, edge: 0.13, expected_return: 0.09 },
      position: { position_id: 'p1', stake: 5, status: 'OPEN', result: null, profit_loss: null },
    },
    ...over,
  }) as NhlGameRow

  it('states the stored prices, the paper account and the recorded position', () => {
    render(<MemoryRouter><NhlNoMarket row={row()} /></MemoryRouter>)
    const note = screen.getByTestId('nhl-no-market').textContent ?? ''
    expect(note).toContain('568 NHL quotes stored')
    expect(note).toContain('1 paper position recorded')
    expect(note).not.toMatch(/No NHL market prices collected|wagering is disabled/)
    const paper = screen.getByTestId('nhl-paper').textContent ?? ''
    expect(paper).toContain('qualified bet (all gates passed)')
    expect(paper).toContain('BOS +130 at DraftKings')
    expect(paper).toContain('position $5.00 · open')
    expect(paper).toContain('virtual paper only')
  })

  it('without an era it keeps the server\'s research-only statements and shows no paper line', () => {
    render(<MemoryRouter><NhlNoMarket row={row({
      markets: { status: 'NO_NHL_MARKET_PRICES_COLLECTED', statement: 'No NHL market prices collected.' },
      wagering: { enabled: false, statement: 'Research only. NHL wagering is disabled.' }, paper: null,
    })} /></MemoryRouter>)
    expect(screen.getByTestId('nhl-no-market').textContent).toContain('No NHL market prices collected')
    expect(screen.queryByTestId('nhl-paper')).toBeNull()
  })
})
