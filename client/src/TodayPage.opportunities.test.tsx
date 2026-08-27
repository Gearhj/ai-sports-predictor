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
const g0 = cloneGame('opp-game-0', 'Alphas', 'Bravos')
const g1 = cloneGame('opp-game-1', 'Charlies', 'Deltas')
const g2 = cloneGame('opp-game-2', 'Echos', 'Foxtrots')

function sel(overrides: Partial<OpportunitySelection>): OpportunitySelection {
  return {
    recommendation: 'QUALIFIED_BET',
    market_type: 'Moneyline',
    period: 'Full game',
    selection: 'PICK',
    line: null,
    sportsbook: 'BookX',
    american_odds: 120,
    decimal_odds: 2.2,
    model_probability: 58,
    no_vig_probability: 49,
    consensus_probability: 49,
    fair_odds: -138,
    raw_edge: 9,
    uncertainty_adjusted_edge: 6,
    expected_value: 12.5,
    quote_observed_at: '2026-08-27T14:58:00Z',
    quote_age_seconds: 300,
    is_stale: false,
    virtual_stake: 10,
    potential_profit: 12,
    total_return: 22,
    ...overrides,
  }
}

const opportunities: GameOpportunity[] = [
  {
    game_id: g0.id,
    recommendation_state: 'QUALIFIED_BET',
    recommendation_reason: 'Uncertainty-adjusted edge +6.0 pts clears the qualified bar.',
    markets_available: ['Moneyline', 'Total'],
    readiness: 78,
    uncertainty: 2.4,
    model_disagreement: 5.1,
    best: sel({ market_type: 'Moneyline', selection: 'QUALIFIED-ML-PICK', american_odds: 120, expected_value: 22.5, virtual_stake: 10, potential_profit: 12, total_return: 22 }),
    alternates: [sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'ALT-OVER-85', line: 8.5, american_odds: -110, virtual_stake: 0, potential_profit: 0, total_return: 0 })],
  },
  {
    game_id: g1.id,
    recommendation_state: 'WATCH',
    recommendation_reason: 'Model–market disagreement +3.0 pts, but it does not clear the qualified bar.',
    markets_available: ['Total'],
    readiness: 64,
    uncertainty: 3.1,
    model_disagreement: 2.2,
    best: sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'WATCH-UNDER-9', line: 9, american_odds: -105, expected_value: 4.0, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g2.id,
    recommendation_state: 'NO_BET',
    recommendation_reason: 'No positive-expected-value edge versus the no-vig market; no position.',
    markets_available: [],
    readiness: 60,
    uncertainty: 4.0,
    model_disagreement: null,
    best: null,
    alternates: [],
  },
]

const dashboardPayload = {
  ...demoDashboard,
  data_mode: 'live',
  games: [g0, g1, g2],
  opportunities,
  opportunity_summary: {
    qualified: 1,
    watch: 1,
    no_bet: 1,
    best_expected_value: 22.5,
    virtual_daily_exposure: 10,
    top_opportunity: 'QUALIFIED-ML-PICK +120 · BookX',
    markets_available: ['Moneyline', 'Total'],
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

describe('Daily Opportunity Board', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.history.pushState({}, '', '/')
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('opens in Best Opportunities mode with the qualified pick ranked first', async () => {
    stub()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const featured = await screen.findByRole('region', { name: /Featured opportunities/i })
    const cards = within(featured).getAllByRole('article')
    // QUALIFIED (higher EV) ranked ahead of WATCH; NO_BET is not featured.
    expect(within(cards[0]).getByText('QUALIFIED BET')).toBeInTheDocument()
    expect(within(cards[0]).getByText('QUALIFIED-ML-PICK')).toBeInTheDocument()
    expect(within(cards[0]).getByText('#1')).toBeInTheDocument()
    expect(within(cards[1]).getByText('WATCH')).toBeInTheDocument()
  })

  it('shows market, book, price, edge, EV and virtual payout on the pick', async () => {
    stub()
    render(<App />)
    const featured = await screen.findByRole('region', { name: /Featured opportunities/i })
    const card = within(featured).getAllByRole('article')[0]
    expect(within(card).getByText(/Moneyline · Full game/i)).toBeInTheDocument()
    expect(within(card).getByText('+120')).toBeInTheDocument()
    expect(within(card).getByText('BookX')).toBeInTheDocument()
    expect(within(card).getByText('+6.0 pts')).toBeInTheDocument()   // uncertainty-adjusted edge
    expect(within(card).getByText('+22.5%')).toBeInTheDocument()      // expected value
    expect(within(card).getByText('Virtual stake')).toBeInTheDocument()
    expect(within(card).getByText('$10.00')).toBeInTheDocument()
    expect(within(card).getByText('$12.00')).toBeInTheDocument()      // potential profit
  })

  it('summarizes qualified, watch, passes and the top opportunity', async () => {
    stub()
    render(<App />)
    const summary = await screen.findByRole('region', { name: /Daily opportunity summary/i })
    expect(within(summary).getByText('Qualified bets').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Watchlist').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Passes').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText(/QUALIFIED-ML-PICK \+120/)).toBeInTheDocument()
    expect(within(summary).getByText('+22.5%')).toBeInTheDocument()
  })

  it('shows only market tabs that have governed quotes', async () => {
    stub()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const tablist = screen.getByRole('tablist', { name: /Opportunity filters/i })
    expect(within(tablist).getByRole('tab', { name: 'Moneyline' })).toBeInTheDocument()
    expect(within(tablist).getByRole('tab', { name: 'Totals' })).toBeInTheDocument()
    // No governed run-line or first-five quotes on this slate → tabs hidden.
    expect(within(tablist).queryByRole('tab', { name: 'Run Line' })).not.toBeInTheDocument()
    expect(within(tablist).queryByRole('tab', { name: 'First Five' })).not.toBeInTheDocument()
  })

  it('filters to a market and surfaces each game’s selection in it', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    await user.click(screen.getByRole('tab', { name: 'Totals' }))
    // g0's total is an alternate; g1's total is its best — both appear.
    expect(await screen.findByText('ALT-OVER-85')).toBeInTheDocument()
    expect(screen.getByText('WATCH-UNDER-9')).toBeInTheDocument()
  })

  it('keeps NO_BET games visible under the No Bet tab', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    await user.click(screen.getByRole('tab', { name: 'No Bet' }))
    expect(await screen.findByText(g2.away_team.name)).toBeInTheDocument()
    expect(screen.getAllByText('No qualified bet').length).toBeGreaterThan(0)
  })

  it('exposes accessible view-analysis and compare-books links', async () => {
    stub()
    render(<App />)
    const featured = await screen.findByRole('region', { name: /Featured opportunities/i })
    const card = within(featured).getAllByRole('article')[0]
    expect(within(card).getByRole('link', { name: /Open full analysis/i })).toHaveAttribute('href', `/games/${g0.id}`)
    expect(within(card).getByRole('link', { name: /Compare all sportsbooks/i })).toHaveAttribute('href', '/markets')
  })

  it('navigates to the game via keyboard on the analysis link', async () => {
    stub()
    const user = userEvent.setup()
    render(<App />)
    const featured = await screen.findByRole('region', { name: /Featured opportunities/i })
    const link = within(within(featured).getAllByRole('article')[0]).getByRole('link', { name: /Open full analysis/i })
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
    // The board is served entirely by the persisted dashboard read; the shell
    // may also read config/identity, but nothing touches a provider or odds feed.
    expect(urls.some((url) => url.includes('/dashboard/today'))).toBe(true)
    expect(urls.some((url) => /odds|provider|refresh|markets\/board|the-odds/i.test(url))).toBe(false)
  })
})
