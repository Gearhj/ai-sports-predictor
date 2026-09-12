/**
 * Markets & Crowd — what the price board must not merge.
 *
 * The redesign's whole purpose is comparison, and comparison is exactly where
 * a price board can lie. These tests pin the separations: a moneyline is not a
 * run line, a no-vig consensus is not a book's vigged price, a prediction
 * market is not a sportsbook, and the temporary SportsGameOdds fallback is not
 * The Odds API.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, endpoints } from '../services/api'
import { impliedProbability } from '../components/broadcast/ComparisonPlot'
import type { MarketBoardData, MarketPulseBoard } from '../types/domain'
import { bookName, UnifiedMarketsPage } from './UnifiedMarketsPage'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

const crowd = {
  data_mode: 'live', date: '2026-09-10', sport: 'MLB', rows: [{
    game_id: 'g1', sport: 'MLB', league: 'MLB',
    home_team: 'Atlanta Braves', away_team: 'Tampa Bay Rays', home_abbrev: 'ATL', away_abbrev: 'TB',
    starts_at: '2026-09-10T16:15:00Z', status: 'SCHEDULED',
    aisp_home_probability: 0.518508, aisp_forecast_fresh: true,
    sportsbook_home_probability: 0.531,
    polymarket_home_probability: 0.535, kalshi_home_probability: 0.535,
    consensus_note: 'No provider qualifies; semantics unmatched',
    providers: [
      { provider: 'POLYMARKET', midpoint: 0.535, best_bid: 0.53, best_ask: 0.54, semantics_status: 'SEMANTICS_REVIEW_REQUIRED', observed_at: '2026-09-10T14:54:55Z' },
      { provider: 'KALSHI', midpoint: 0.535, best_bid: 0.46, best_ask: 0.47, semantics_status: 'SEMANTICS_REVIEW_REQUIRED', observed_at: '2026-09-10T14:54:55Z' },
    ],
  }],
} as unknown as MarketPulseBoard

const books = {
  data_mode: 'live', date: '2026-09-10', rows: [
    {
      id: 'r1', game_id: 'g1', matchup: 'TB @ ATL', start_time: '2026-09-10T16:15:00Z',
      market_type: 'Moneyline', selection: 'Atlanta Braves', line: undefined,
      asp_fair: -108, consensus: -113, edge: 1.2, observed_at: '2026-09-10T14:44:49Z',
      books: [
        { book: 'Fanduel (SportsGameOdds, research)', price: -122, best: false },
        { book: 'Pointsbet (SportsGameOdds, research)', price: -118, best: true },
      ],
    },
    {
      id: 'r2', game_id: 'g1', matchup: 'TB @ ATL', start_time: '2026-09-10T16:15:00Z',
      market_type: 'Run line', selection: 'Atlanta Braves -1.5', line: -1.5,
      asp_fair: 150, consensus: 145, edge: null, observed_at: '2026-09-10T14:44:49Z',
      books: [{ book: 'Draftkings', price: 148, best: true }],
    },
  ],
} as unknown as MarketBoardData

afterEach(() => { cleanup(); vi.restoreAllMocks() })

/** fireEvent, wrapped so the state update it causes is flushed before asserting. */
async function click(element: HTMLElement) {
  await act(async () => { fireEvent.click(element) })
}

function mount() {
  vi.spyOn(endpoints, 'marketPulse').mockResolvedValue(crowd)
  vi.spyOn(endpoints, 'nflWeeks').mockRejectedValue(new Error('no NFL in this test'))
  vi.mocked(apiRequest).mockImplementation((path: string) => {
    if (path.startsWith('/markets/board')) return Promise.resolve(books as never)
    return Promise.reject(new Error('unexpected request'))
  })
  render(<MemoryRouter initialEntries={['/markets?sport=MLB']}><UnifiedMarketsPage /></MemoryRouter>)
}

describe('Markets & Crowd', () => {
  it('opens on the moneyline and never shows a run line on that tab', async () => {
    mount()
    const mlb = await screen.findByRole('region', { name: /Baseball prices/i })
    const row = within(mlb).getByRole('article', { name: /Tampa Bay Rays at Atlanta Braves/i })
    // The best moneyline price, not the run-line price from the same game.
    expect(row).toHaveTextContent('-118')
    expect(row).not.toHaveTextContent('148')
  })

  it('separates the model probability from the sportsbook no-vig consensus', async () => {
    mount()
    const mlb = await screen.findByRole('region', { name: /Baseball prices/i })
    const row = within(mlb).getByRole('article', { name: /Tampa Bay Rays at Atlanta Braves/i })
    expect(row).toHaveTextContent('51.9%')
    expect(row).toHaveTextContent('53.1%')
  })

  it('labels the temporary fallback separately from the primary provider', async () => {
    // The badge moved out of the collapsed row into the expanded source
    // detail: the row now carries the book name, the price and its age, and
    // the provider implementation is detail rather than a repeated headline.
    mount()
    const mlb = await screen.findByRole('region', { name: /Baseball prices/i })
    const row = within(mlb).getByRole('article', { name: /Tampa Bay Rays at Atlanta Braves/i })
    await userEvent.click(within(row).getByRole('button', { name: /Show bookmaker detail/i }))
    expect(within(mlb).getAllByText(/SGO fallback/i).length).toBeGreaterThan(0)
    expect(within(mlb).queryByText(/^Odds API$/)).toBeNull()
  })

  it('keeps every bookmaker price in a table inside a drawer, not in the row', async () => {
    mount()
    const mlb = await screen.findByRole('region', { name: /Baseball prices/i })
    // Closed: only the best price is on screen.
    expect(within(mlb).queryByText('-122')).toBeNull()
    await click(within(mlb).getByRole('button', { name: /Show bookmaker detail/i }))
    // Two tables open: the book prices and, separately, the prediction markets.
    const table = screen.getAllByRole('table')[0]
    expect(within(table).getByText('-122')).toBeInTheDocument()
    // The provenance badge carries the provider, so the book name is the book.
    expect(within(table).getByText('Fanduel')).toBeInTheDocument()
  })

  it('shows prediction markets only on the moneyline tab and never as a sportsbook', async () => {
    mount()
    const mlb = await screen.findByRole('region', { name: /Baseball prices/i })
    await click(within(mlb).getByRole('button', { name: /Show bookmaker detail/i }))
    expect(screen.getAllByText(/unreviewed . research only/i)).toHaveLength(2)

    await click(screen.getByRole('button', { name: /Run line/i }))
    expect(screen.getByText(/They are not run-line or total prices/i)).toBeInTheDocument()
  })

  it('states that reloading calls no provider', async () => {
    mount()
    expect(await screen.findByText(/Reloading this page makes no provider call/i)).toBeInTheDocument()
  })
})

describe('price arithmetic', () => {
  it('converts an American price to the implied probability the margin is still in', () => {
    expect(impliedProbability(-110)).toBeCloseTo(110 / 210, 6)
    expect(impliedProbability(150)).toBeCloseTo(100 / 250, 6)
    // Two sides of a real market sum above 1: that surplus is the margin, and
    // it is why a book's implied probability is not a no-vig probability.
    expect(impliedProbability(-122)! + impliedProbability(102)!).toBeGreaterThan(1)
    expect(impliedProbability(null)).toBeNull()
    expect(impliedProbability(0)).toBeNull()
  })

  it('strips the provenance parenthetical from a book name and nothing else', () => {
    expect(bookName('Fanduel (SportsGameOdds, research)')).toBe('Fanduel')
    expect(bookName('DraftKings')).toBe('DraftKings')
  })
})
