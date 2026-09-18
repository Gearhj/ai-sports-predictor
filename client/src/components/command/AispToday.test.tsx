import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { decision, game, priced, scenario } from '../../test/homeFixtures'
import type { SportReadinessEntry } from '../readiness/SportReadiness'
import { AispToday, buildTodaySummary, readinessLine } from './AispToday'

afterEach(cleanup)

describe('AISP Today summary', () => {
  it('reconciles with the read models it counts', () => {
    const clock = { now: Date.parse('2026-09-16T16:00:00Z') }
    const a = priced(clock, game(clock, '1', { startIn: 120 }))
    const b = priced(clock, game(clock, '2', { startIn: 240 }), 0.5, false)
    const c = game(clock, '3', { startIn: 300, homeP: null })
    const started = priced(clock, game(clock, '4', { startIn: -30 }))
    const items = [
      decision(clock, '1', a, { recommendation: 'QUALIFIED_BET', edge: 0.06 }),
      decision(clock, '1b', a, { recommendation: 'QUALIFIED_BET', edge: 0.05 }),
      decision(clock, '2', b, { recommendation: 'WATCH', phase: 'LINEUP_CONFIRMED', edge: 0.021 }),
      decision(clock, '4', started, { recommendation: 'NO_BET', edge: 0.09 }),
    ]
    const positions = [{
      position_id: 'p1', game_id: '1', matchup: a.matchup, starts_at: a.starts_at, market: 'MONEYLINE', selection: 'X',
      entry: { american: 100, decimal: 2, sportsbook: null, sportsbook_key: null, provider: null, classification: 'GOVERNED' as const, quote_observed_at: null },
      stake: 5, placed_at: null, status: 'OPEN', result: null, profit_loss: null, settled_at: null, experiment: { id: 'e', name: 'e' },
    }]
    const s = scenario(clock, [a, b, c, started], items, positions)
    const summary = buildTodaySummary({ games: s.markets.games, items: s.decisions.items, ledger: s.ledger, now: clock.now })

    expect(summary.games).toBe(s.markets.games.length)
    expect(summary.forecasts).toBe(s.markets.games.filter((g) => g.model).length)
    expect(summary.currentPrices).toBe(s.markets.games.filter((g) => g.price_state === 'CURRENT_PRICE').length)
    // Two qualified rows on one game count as one qualified decision.
    expect(summary.qualified).toBe(1)
    expect(summary.openPositions).toBe(s.ledger.sports.flatMap((x) => x.positions).filter((p) => p.status !== 'SETTLED').length)
    // Closest excludes qualified games and games already under way.
    expect(summary.closest).toMatchObject({ matchup: b.matchup, edge: 0.021 })
    expect(summary.closest?.text).toBe('model minus break-even +2.1 pts')
    expect(summary.nextWindow?.at).toBe(a.checkpoints.decision_window_opens_at)
    expect(summary.story).toMatch(/^4 games on the slate, 3 with a forecast and 2 with a current sportsbook price; 1 decision qualifies; 1 virtual position open; the next decision window opens at/)
  })

  it('says so plainly when there is no slate', () => {
    const summary = buildTodaySummary({ games: [], items: [], ledger: null, now: Date.now() })
    expect(summary).toMatchObject({ games: 0, forecasts: 0, currentPrices: 0, qualified: 0, openPositions: 0, nextWindow: null, closest: null })
    expect(summary.story).toBe('No MLB or NFL game is on today’s slate.')
  })

  it('shows all four sports and labels NBA and CFB as research only', () => {
    const readiness = [
      { sport: 'MLB', label: 'MLB', controlling_engine: 'MLB_V18_WHOLE_GAME', forecast: { state: 'FORECAST_AWAITING_PRODUCER', label: '' }, price: { state: 'FRESH_PRICE', label: '' }, next_game: { matchup: 'LAD @ CIN', starts_label: 'Wed 6:40 PM ET' }, pipeline: { ready: true, blocker: null, could_qualify: null } },
      { sport: 'NBA', label: 'NBA', controlling_engine: 'NBA_GEN1_JOINT_BASELINE', forecast: { state: 'RESEARCH_FORECAST_AVAILABLE', label: '' }, price: { state: 'COLLECTION_NOT_OPEN', label: '' }, eligibility: { eligible: false, reason: 'x' }, next_game: { matchup: 'BOS @ DET', starts_label: 'Tue Oct 20' }, pipeline: { ready: false, blocker: 'x', could_qualify: null } },
    ] as SportReadinessEntry[]
    expect(readinessLine(readiness[1], 'NBA').authority).toMatch(/research only/)
    expect(readinessLine(undefined, 'CFB')).toMatchObject({ authority: 'Research only', eligibility: 'Not eligible' })
    const summary = buildTodaySummary({ games: [], items: [], ledger: null, now: Date.now() })
    render(<MemoryRouter><AispToday summary={summary} readiness={readiness} loading={false} decisionsHref="/decisions" /></MemoryRouter>)
    const region = screen.getByRole('region', { name: 'AISP Today' })
    for (const code of ['MLB', 'NFL', 'NBA', 'CFB']) expect(within(region).getByTestId(`today-${code}`)).toBeInTheDocument()
    expect(within(region).getByTestId('today-MLB')).toHaveTextContent('Forecast awaiting')
    expect(within(region).getByTestId('today-MLB')).toHaveTextContent('Price current')
    expect(within(region).getByTestId('today-NBA')).toHaveTextContent('Forecast research held')
    expect(within(region).getByTestId('today-NFL')).toHaveTextContent('No game scheduled')
  })
})
