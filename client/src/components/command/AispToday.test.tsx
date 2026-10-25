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
      decision(clock, '2', b, { recommendation: 'WATCH', phase: 'LINEUP_CONFIRMED', edge: 0.021, adjusted: { edge: 0.015, expected_value: 0.01, probability: 0.5 } }),
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
    // Ranked on the recorded policy-adjusted edge, not the stored break-even figure (0.021).
    expect(summary.closest).toMatchObject({ matchup: b.matchup })
    expect(summary.closest?.edge).toBeCloseTo(0.015 - 0.02, 6)
    expect(summary.closest?.text).toBe('recorded policy-adjusted edge +1.5 pts, policy requires ≥ 2.0 pts')
    expect(summary.nextWindow?.at).toBe(a.checkpoints.decision_window_opens_at)
    expect(summary.story).toMatch(/^4 games on the slate, 3 with a forecast and 2 with a current sportsbook price; 1 decision qualifies; 1 virtual position open; the next decision window opens at/)
  })

  it('says so plainly when there is no slate', () => {
    const summary = buildTodaySummary({ games: [], items: [], ledger: null, now: Date.now() })
    expect(summary).toMatchObject({ games: 0, forecasts: 0, currentPrices: 0, qualified: 0, openPositions: 0, nextWindow: null, closest: null })
    expect(summary.story).toBe('No MLB or NFL game is on today’s slate.')
  })

  it('shows every sport (NHL included) and labels NBA, CFB and NHL as research paper', () => {
    const readiness = [
      { sport: 'MLB', label: 'MLB', controlling_engine: 'MLB_V18_WHOLE_GAME', forecast: { state: 'FORECAST_AWAITING_PRODUCER', label: '' }, price: { state: 'FRESH_PRICE', label: '' }, next_game: { matchup: 'LAD @ CIN', starts_label: 'Wed 6:40 PM ET' }, pipeline: { ready: true, blocker: null, could_qualify: null } },
      { sport: 'NBA', label: 'NBA', controlling_engine: 'NBA_GEN1_JOINT_BASELINE', forecast: { state: 'RESEARCH_FORECAST_AVAILABLE', label: '' }, price: { state: 'COLLECTION_NOT_OPEN', label: '' }, eligibility: { eligible: false, reason: 'x' }, next_game: { matchup: 'BOS @ DET', starts_label: 'Tue Oct 20' }, pipeline: { ready: false, blocker: 'x', could_qualify: null } },
      // NHL (live 2026-10-16): AISP's own in-process Gen1 forecast on its own eligibility route.
      { sport: 'NHL', label: 'NHL', controlling_engine: 'NHL_GEN1_FINAL_TWO_WAY', forecast: { state: 'IN_PROCESS_FORECAST_AVAILABLE', label: '' }, price: { state: 'COLLECTION_SCHEDULED', label: '' }, eligibility: { eligible: true, reason: 'AISP_IN_PROCESS_ELIGIBLE', route: 'AISP_IN_PROCESS', label: 'AISP in-process eligibility' }, next_game: { matchup: 'PIT @ COL', starts_label: 'Fri Oct 16, 9:00 PM ET' }, pipeline: { ready: true, blocker: null, could_qualify: null } },
    ] as SportReadinessEntry[]
    expect(readinessLine(readiness[1], 'NBA').authority).toMatch(/research only/)
    expect(readinessLine(undefined, 'CFB')).toMatchObject({ authority: 'Research only', eligibility: 'Not eligible' })
    // College Football 2026-09-19: the eligibility record is present but the producer's discovery lease lapsed.
    const cfb = { sport: 'CFB', label: 'College Football', controlling_engine: 'CFB_GEN1_JOINT_BASELINE', pipeline: { ready: true, blocker: null, could_qualify: null },
      eligibility: { eligible: true, reason: 'ELIGIBLE', contract: 'ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1', discovery_lease: 'EXPIRED_LEASE' } } as SportReadinessEntry
    expect(readinessLine(cfb, 'CFB').eligibility).toBe('Eligibility lease expired')
    expect(readinessLine({ ...cfb, eligibility: { ...cfb.eligibility!, discovery_lease: 'CURRENT' } }, 'CFB').eligibility).toBe('Paper research eligible')
    const summary = buildTodaySummary({ games: [], items: [], ledger: null, now: Date.now() })
    render(<MemoryRouter><AispToday summary={summary} readiness={readiness} loading={false} decisionsHref="/decisions" /></MemoryRouter>)
    const region = screen.getByRole('region', { name: 'AISP Today' })
    for (const code of ['MLB', 'NFL', 'NBA', 'CFB', 'NHL']) expect(within(region).getByTestId(`today-${code}`)).toBeInTheDocument()
    const hockey = within(region).getByTestId('today-NHL')
    expect(hockey).toHaveTextContent('NHL GEN1 FINAL TWO WAY · research only')
    expect(hockey).toHaveTextContent('Forecast in-process held')
    expect(hockey).toHaveTextContent('Price collection scheduled')
    expect(hockey).toHaveTextContent('Eligibility Paper research eligible')
    expect(hockey).toHaveTextContent('PIT @ COL · Fri Oct 16, 9:00 PM ET')
    expect(within(region).getByTestId('today-MLB')).toHaveTextContent('Forecast awaiting')
    expect(within(region).getByTestId('today-MLB')).toHaveTextContent('Price current')
    expect(within(region).getByTestId('today-NBA')).toHaveTextContent('Forecast research held')
    expect(within(region).getByTestId('today-NFL')).toHaveTextContent('No game scheduled')
  })
})
