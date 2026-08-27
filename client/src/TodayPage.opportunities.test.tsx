import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { demoDashboard, getDemoGame } from './data/demo'
import type { GameOpportunity, OpportunitySelection } from './types/domain'

vi.setConfig({ testTimeout: 20000 })

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

// The demo fixture ships a single game; clone it into a distinguishable slate.
const base = demoDashboard.games[0]
const cloneGame = (id: string, away: string, home: string) => ({
  ...base,
  id,
  away_team: { ...base.away_team, name: away, abbreviation: away.slice(0, 3).toUpperCase() },
  home_team: { ...base.home_team, name: home, abbreviation: home.slice(0, 3).toUpperCase() },
})
const g0 = cloneGame('opp-game-0', 'Alphas', 'Bravos')     // QUALIFIED
const g1 = cloneGame('opp-game-1', 'Charlies', 'Deltas')   // PROVISIONAL
const g2 = cloneGame('opp-game-2', 'Echos', 'Foxtrots')    // WATCH
const g3 = cloneGame('opp-game-3', 'Golfs', 'Hotels')      // NO BET

function sel(overrides: Partial<OpportunitySelection>): OpportunitySelection {
  return {
    recommendation: 'QUALIFIED_BET', market_type: 'Moneyline', period: 'Full game', selection: 'PICK',
    line: null, sportsbook: 'BookX', american_odds: 120, decimal_odds: 2.2,
    model_probability: 58, no_vig_probability: 49, consensus_probability: 49, fair_odds: -138,
    raw_edge: 9, uncertainty_adjusted_edge: 6, expected_value: 12.5,
    quote_observed_at: '2026-08-27T14:58:00Z', quote_age_seconds: 300, is_stale: false,
    virtual_stake: 10, potential_profit: 12, total_return: 22, opportunity_score: 80,
    ...overrides,
  }
}

const opportunities: GameOpportunity[] = [
  {
    game_id: g0.id, sport: 'MLB', recommendation_state: 'QUALIFIED_BET',
    recommendation_reason: 'Uncertainty-adjusted edge clears the qualified bar.', opportunity_score: 84,
    markets_available: ['Moneyline', 'Total'], readiness: 78, uncertainty: 2.4, model_disagreement: 5.1,
    best: sel({ market_type: 'Moneyline', selection: 'QUAL-ML', american_odds: 120, expected_value: 22.5, opportunity_score: 84, virtual_stake: 10, potential_profit: 12, total_return: 22 }),
    alternates: [sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'ALT-OVER-85', line: 8.5, american_odds: -110, opportunity_score: 30, virtual_stake: 0, potential_profit: 0, total_return: 0 })],
  },
  {
    game_id: g1.id, sport: 'MLB', recommendation_state: 'PROVISIONAL_EDGE',
    recommendation_reason: 'Edge clears the value bar, but a required lineup is unconfirmed — provisional.', opportunity_score: 60,
    markets_available: ['Run line'], readiness: 66, uncertainty: 3.1, model_disagreement: 2.2,
    best: sel({ recommendation: 'PROVISIONAL_EDGE', market_type: 'Run line', selection: 'PROV-RL-15', line: 1.5, american_odds: -150, opportunity_score: 60, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g2.id, sport: 'MLB', recommendation_state: 'WATCH',
    recommendation_reason: 'Model–market disagreement does not clear the qualified bar.', opportunity_score: 40,
    markets_available: ['Total'], readiness: 64, uncertainty: 3.4, model_disagreement: 6.0,
    best: sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'WATCH-UNDER-9', line: 9, american_odds: -105, opportunity_score: 40, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g3.id, sport: 'MLB', recommendation_state: 'NO_BET',
    recommendation_reason: 'No positive-expected-value edge; no position.', opportunity_score: 0,
    markets_available: [], readiness: 60, uncertainty: 4.0, model_disagreement: null,
    best: null, alternates: [],
  },
]

const dashboardPayload = {
  ...demoDashboard,
  data_mode: 'live',
  games: [g0, g1, g2, g3],
  opportunities,
  opportunity_summary: {
    qualified: 1, provisional: 1, watch: 1, no_bet: 1,
    best_expected_value: 22.5, virtual_daily_exposure: 10,
    top_opportunity: 'QUAL-ML +120 · BookX',
    markets_available: ['Moneyline', 'Run line', 'Total'], sports_available: ['MLB'],
  },
}

function stub() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    if (url.includes('/dashboard/today')) {
      return { ok: true, status: 200, json: async () => dashboardPayload } as Response
    }
    const gameMatch = url.match(/\/games\/([^/?]+)/)
    if (gameMatch) {
      try {
        return { ok: true, status: 200, json: async () => getDemoGame(decodeURIComponent(gameMatch[1])) } as Response
      } catch {
        return { ok: false, status: 404, json: async () => ({ detail: 'Game not found' }) } as Response
      }
    }
    return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const featured = () => screen.findByRole('region', { name: /Today's best opportunities/i })
const completeSlate = () => screen.findByRole('region', { name: /Complete MLB slate/i })

describe('Complete slate + multi-sport board', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.history.pushState({}, '', '/')
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('ranks the top three opportunities by Opportunity Score', async () => {
    stub()
    render(<App />)
    const region = await featured()
    const cards = within(region).getAllByRole('article')
    expect(cards).toHaveLength(3)
    expect(within(cards[0]).getByText('QUALIFIED BET')).toBeInTheDocument()
    expect(within(cards[0]).getByText('#1')).toBeInTheDocument()
    expect(within(cards[1]).getByText('PROVISIONAL EDGE')).toBeInTheDocument()
    expect(within(cards[2]).getByText('WATCH')).toBeInTheDocument()
    // The NO_BET game is never featured.
    expect(within(region).queryByText(g3.away_team.name)).not.toBeInTheDocument()
  })

  it('shows the complete slate — every game, grouped by sport, no omissions or duplicates', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    expect(within(slate).getByText(/Complete MLB slate/i)).toBeInTheDocument()
    const cards = within(slate).getAllByRole('article')
    expect(cards).toHaveLength(4) // all 4 games, none omitted
    for (const game of [g0, g1, g2, g3]) {
      // Each game appears exactly once in the complete slate.
      expect(within(slate).getAllByText(game.away_team.name)).toHaveLength(1)
    }
  })

  it('marks the top-three games with #N OPPORTUNITY badges in the complete slate', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    expect(within(slate).getByText('#1 OPPORTUNITY')).toBeInTheDocument()
    expect(within(slate).getByText('#2 OPPORTUNITY')).toBeInTheDocument()
    expect(within(slate).getByText('#3 OPPORTUNITY')).toBeInTheDocument()
  })

  it('labels a provisional edge distinctly and takes no virtual position', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    const provisionalCard = within(slate).getByText(g1.away_team.name).closest('article') as HTMLElement
    expect(within(provisionalCard).getByText('PROVISIONAL EDGE')).toBeInTheDocument()
    expect(within(provisionalCard).getByText('No virtual position')).toBeInTheDocument()
    expect(within(provisionalCard).getByText(/provisional edge, no virtual position/i)).toBeInTheDocument()
  })

  it('summarizes qualified, provisional, watch, and passes', async () => {
    stub()
    render(<App />)
    const summary = await screen.findByRole('region', { name: /Daily opportunity summary/i })
    expect(within(summary).getByText('Qualified bets').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Provisional').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Watchlist').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Passes').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText(/QUAL-ML \+120/)).toBeInTheDocument()
  })

  it('shows a sport filter honestly reflecting only sports with events', async () => {
    stub()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const sportBar = screen.getByRole('tablist', { name: /Sport filter/i })
    expect(within(sportBar).getByRole('tab', { name: 'MLB' })).toBeInTheDocument()
    // Only one sport has data, so no cross-sport "All Sports" toggle appears.
    expect(within(sportBar).queryByRole('tab', { name: 'All Sports' })).not.toBeInTheDocument()
    expect(within(sportBar).queryByRole('tab', { name: 'NFL' })).not.toBeInTheDocument()
  })

  it('shows sport-correct market tabs and hides absent markets', async () => {
    stub()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const tablist = screen.getByRole('tablist', { name: /Opportunity filters/i })
    expect(within(tablist).getByRole('tab', { name: 'Moneyline' })).toBeInTheDocument()
    expect(within(tablist).getByRole('tab', { name: 'Run Line' })).toBeInTheDocument()
    expect(within(tablist).getByRole('tab', { name: 'Totals' })).toBeInTheDocument()
    expect(within(tablist).queryByRole('tab', { name: 'First Five' })).not.toBeInTheDocument()
    expect(within(tablist).queryByRole('tab', { name: 'Puck Line' })).not.toBeInTheDocument()
  })

  it('filters the complete slate to a market', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    await user.click(screen.getByRole('tab', { name: 'Totals' }))
    const slate = await completeSlate()
    expect(within(slate).getByText('ALT-OVER-85')).toBeInTheDocument() // g0 alternate total
    expect(within(slate).getByText('WATCH-UNDER-9')).toBeInTheDocument() // g2 best total
    expect(within(slate).queryByText('PROV-RL-15')).not.toBeInTheDocument() // run-line only game excluded
  })

  it('keeps NO_BET games visible under the No Bet tab', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    await user.click(screen.getByRole('tab', { name: 'No Bet' }))
    expect(await screen.findByText(g3.away_team.name)).toBeInTheDocument()
    expect(screen.getAllByText('No qualified bet').length).toBeGreaterThan(0)
  })

  it('exposes accessible view-analysis and compare-books links and keyboard nav', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    const region = await featured()
    const card = within(region).getAllByRole('article')[0]
    expect(within(card).getByRole('link', { name: /Open full analysis/i })).toHaveAttribute('href', `/games/${g0.id}`)
    expect(within(card).getByRole('link', { name: /Compare all sportsbooks/i })).toHaveAttribute('href', '/markets')
    const link = within(card).getByRole('link', { name: /Open full analysis/i })
    link.focus()
    expect(link).toHaveFocus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(window.location.pathname).toBe(`/games/${g0.id}`))
  })

  it('reads only the dashboard endpoint (no provider calls) on load', async () => {
    const fetchMock = stub()
    render(<App />)
    await screen.findByRole('region', { name: /Daily opportunity summary/i })
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.includes('/dashboard/today'))).toBe(true)
    expect(urls.some((url) => /odds|provider|refresh|markets\/board|the-odds/i.test(url))).toBe(false)
  })
})
