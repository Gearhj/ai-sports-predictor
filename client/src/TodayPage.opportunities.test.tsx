import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { demoDashboard, getDemoGame } from './data/demo'
import type { AvailabilityContext, GameOpportunity, OpportunitySelection } from './types/domain'
import { TeamLogo, teamLogoUrl } from './components/ui'

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
const g4 = cloneGame('opp-game-4', 'Indias', 'Julietts')   // EXTREME DISAGREEMENT

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

function avail(overrides: Partial<AvailabilityContext> = {}): AvailabilityContext {
  return {
    lineup_status: 'PROJECTED', lineup_complete: false, starting_pitcher_status: 'PROBABLE',
    injury_feed_status: 'INJURY_FEED_NOT_CONFIGURED', injury_feed_authoritative: false, production_injury_coverage: false,
    unavailable_players: null, last_availability_update: null, last_availability_event: null, rescored: false,
    forecast_before: null, forecast_after: null, edge_before: null, edge_after: null, book_moved: null,
    ...overrides,
  }
}

const opportunities: GameOpportunity[] = [
  {
    game_id: g0.id, sport: 'MLB', recommendation_state: 'QUALIFIED_BET', review_required: false,
    recommendation_reason: 'Uncertainty-adjusted edge clears the qualified bar.', qualification_reasons: [], opportunity_score: 84,
    markets_available: ['Moneyline', 'Total'], readiness: 78, uncertainty: 2.4, model_disagreement: 5.1,
    experiment_status: 'ELIGIBLE', experiment_stake: null,
    availability: avail({ lineup_status: 'CONFIRMED', lineup_complete: true, starting_pitcher_status: 'CONFIRMED', injury_feed_authoritative: true, production_injury_coverage: true, unavailable_players: 0, last_availability_update: '2026-08-27T14:50:00Z', last_availability_event: 'LINEUP_CONFIRMED', rescored: true, forecast_before: 52, forecast_after: 55, edge_before: 2.4, edge_after: 4.0, book_moved: true }),
    best: sel({ market_type: 'Moneyline', selection: 'QUAL-ML', american_odds: 120, expected_value: 22.5, opportunity_score: 84, virtual_stake: 10, potential_profit: 12, total_return: 22 }),
    alternates: [sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'ALT-OVER-85', line: 8.5, american_odds: -110, opportunity_score: 30, virtual_stake: 0, potential_profit: 0, total_return: 0 })],
  },
  {
    game_id: g1.id, sport: 'MLB', recommendation_state: 'PROVISIONAL_EDGE', review_required: false,
    recommendation_reason: 'Edge clears the value bar, but a required starter is unconfirmed — provisional.',
    qualification_reasons: ['Starting pitcher probable', 'Lineup not confirmed'], opportunity_score: 60,
    markets_available: ['Run line'], readiness: 66, uncertainty: 3.1, model_disagreement: 2.2,
    experiment_status: 'PROVISIONAL', experiment_stake: null,
    availability: avail({ lineup_status: 'PROJECTED', starting_pitcher_status: 'PROBABLE' }),
    best: sel({ recommendation: 'PROVISIONAL_EDGE', market_type: 'Run line', selection: 'PROV-RL-15', line: 1.5, american_odds: -150, opportunity_score: 60, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g2.id, sport: 'MLB', recommendation_state: 'WATCH', review_required: false,
    recommendation_reason: 'Model–market disagreement does not clear the qualified bar.',
    qualification_reasons: ['Latest quote is stale'], opportunity_score: 40,
    markets_available: ['Total'], readiness: 64, uncertainty: 3.4, model_disagreement: 6.0,
    experiment_status: 'NO_POSITION', experiment_stake: null,
    availability: avail(),
    best: sel({ recommendation: 'WATCH', market_type: 'Total', selection: 'WATCH-UNDER-9', line: 9, american_odds: -105, opportunity_score: 40, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g4.id, sport: 'MLB', recommendation_state: 'EXTREME_DISAGREEMENT', review_required: true,
    recommendation_reason: 'Model–market gap +34.2 pts exceeds the review threshold — EXTREME DISAGREEMENT.',
    qualification_reasons: ['Extreme model-market disagreement'], opportunity_score: 55,
    markets_available: ['Total'], readiness: 74, uncertainty: 2.8, model_disagreement: 6.6,
    experiment_status: 'FROZEN', experiment_stake: 8,
    availability: avail({ lineup_status: 'DETAILS_MISSING', unavailable_players: 1 }),
    best: sel({ recommendation: 'EXTREME_DISAGREEMENT', market_type: 'Total', selection: 'EXTREME-OVER-65', line: 6.5, american_odds: -103, raw_edge: 34.2, uncertainty_adjusted_edge: 34, expected_value: 67.8, opportunity_score: 55, virtual_stake: 0, potential_profit: 0, total_return: 0 }),
    alternates: [],
  },
  {
    game_id: g3.id, sport: 'MLB', recommendation_state: 'NO_BET', review_required: false,
    recommendation_reason: 'No positive-expected-value edge; no position.', qualification_reasons: ['No complete two-sided governed quote'], opportunity_score: 0,
    markets_available: [], readiness: 60, uncertainty: 4.0, model_disagreement: null,
    experiment_status: 'SETTLED', experiment_stake: 5,
    availability: avail(),
    best: null, alternates: [],
  },
]

const dashboardPayload = {
  ...demoDashboard,
  data_mode: 'live',
  games: [g0, g1, g2, g4, g3],
  opportunities,
  opportunity_summary: {
    qualified: 1, provisional: 1, review_required: 1, watch: 1, no_bet: 1,
    best_expected_value: 67.8, virtual_daily_exposure: 10,
    top_opportunity: 'QUAL-ML +120 · BookX',
    markets_available: ['Moneyline', 'Run line', 'Total'], sports_available: ['MLB'],
    injury_feed_status: 'INJURY_FEED_NOT_CONFIGURED', injury_feed_authoritative: false,
    odds_last_refresh: '2026-08-27T14:57:00Z', odds_quote_age_seconds: 18000, odds_quota_remaining: 488, odds_scheduler_running: false,
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
    // Ranked by Opportunity Score: QUALIFIED (84) > PROVISIONAL (60) > EXTREME (55).
    expect(within(cards[0]).getByText('QUALIFIED BET')).toBeInTheDocument()
    expect(within(cards[0]).getByText('#1')).toBeInTheDocument()
    expect(within(cards[1]).getByText('PROVISIONAL EDGE')).toBeInTheDocument()
    expect(within(cards[2]).getByText('REVIEW REQUIRED')).toBeInTheDocument()
    // WATCH (40) and NO_BET are not featured.
    expect(within(region).queryByText(g2.away_team.name)).not.toBeInTheDocument()
    expect(within(region).queryByText(g3.away_team.name)).not.toBeInTheDocument()
  })

  it('shows the complete slate — every game, grouped by sport, no omissions or duplicates', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    expect(within(slate).getByText(/Complete MLB slate/i)).toBeInTheDocument()
    const cards = within(slate).getAllByRole('article')
    expect(cards).toHaveLength(5) // all 5 games, none omitted
    for (const game of [g0, g1, g2, g4, g3]) {
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

  it('renders experiment lifecycle labels only from the explicit experiment status fields', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()

    expect(within(slate).getByText('$1K EXPERIMENT · ELIGIBLE')).toBeInTheDocument()
    expect(within(slate).getByText('$1K EXPERIMENT · PROVISIONAL')).toBeInTheDocument()
    expect(within(slate).getByText('$1K EXPERIMENT · NO POSITION')).toBeInTheDocument()
    expect(within(slate).getByText('$1K EXPERIMENT · FROZEN · $8.00')).toBeInTheDocument()
    expect(within(slate).getByText('$1K EXPERIMENT · SETTLED · $5.00')).toBeInTheDocument()
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

  it('summarizes qualified, provisional, review-required, watch, passes, and injury feed', async () => {
    stub()
    render(<App />)
    const summary = await screen.findByRole('region', { name: /Daily opportunity summary/i })
    expect(within(summary).getByText('Qualified bets').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Provisional').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Review required').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Watchlist').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Passes').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText(/QUAL-ML \+120/)).toBeInTheDocument()
  })

  it('does not surface the deferred paid injury feed on the public product', async () => {
    // Even a TRIAL feed status must not leak to the public cards/summary.
    const trialPayload = {
      ...dashboardPayload,
      opportunities: opportunities.map((o) => ({ ...o, availability: { ...o.availability, injury_feed_status: 'INJURY_FEED_TRIAL' } })),
      opportunity_summary: { ...dashboardPayload.opportunity_summary, injury_feed_status: 'INJURY_FEED_TRIAL' },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
      if (url.includes('/dashboard/today')) return { ok: true, status: 200, json: async () => trialPayload } as Response
      return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) } as Response
    }))
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    // No "Injury feed" field, no trial label, no paid-provider deficiency note.
    expect(screen.queryByText('Injury feed')).not.toBeInTheDocument()
    expect(screen.queryByText(/not used for picks/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Production injury coverage unavailable/i)).not.toBeInTheDocument()
  })

  it('never claims 0 unavailable and shows no paid-injury caveat on a card', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    const card = within(slate).getByText(g1.away_team.name).closest('article') as HTMLElement
    // Lineup/starter (real governed evidence) remain; injury-feed/unavailable do not.
    expect(within(card).getByText(/^Starter/)).toBeInTheDocument()
    expect(within(card).getAllByText(/^Lineup/).length).toBeGreaterThan(0)
    expect(within(card).queryByText(/^Unavailable/)).not.toBeInTheDocument()
    expect(within(card).queryByText(/Injury feed/)).not.toBeInTheDocument()
  })

  it('explains blockers with real governed reasons only (no paid-feed reason)', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    const card = within(slate).getByText(g1.away_team.name).closest('article') as HTMLElement
    expect(within(card).getByText('Why not qualified')).toBeInTheDocument()
    expect(within(card).getByText('Starting pitcher probable')).toBeInTheDocument()
    expect(within(card).queryByText(/injury feed/i)).not.toBeInTheDocument()
  })

  it('surfaces odds-refresh diagnostics including a disabled scheduler', async () => {
    stub()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    expect(screen.getByText(/scheduler OFF — automatic odds refresh disabled/i)).toBeInTheDocument()
    expect(screen.getByText(/provider quota 488/i)).toBeInTheDocument()
  })

  it('resolves team logos from canonical identity and falls back to the abbreviation', () => {
    expect(teamLogoUrl('LAD')).toMatch(/espncdn\.com.*\/lad\.png$/)
    expect(teamLogoUrl('nyy')).toMatch(/\/nyy\.png$/)   // case-insensitive
    expect(teamLogoUrl('ZZZ')).toBeNull()               // unknown team → no logo

    const mapped = render(<TeamLogo abbreviation="LAD" color="#005A9C" />)
    const img = mapped.container.querySelector('img')
    expect(img).not.toBeNull()
    expect(img!.getAttribute('alt')).toBe('LAD')        // abbreviation is the alt text
    cleanup()

    // Unmapped team → abbreviation badge fallback, no broken image.
    const fallback = render(<TeamLogo abbreviation="ZZZ" color="#000000" />)
    expect(fallback.container.querySelector('img')).toBeNull()
    expect(fallback.getByText('ZZZ')).toBeInTheDocument()
  })

  it('flags an extreme model–market gap as REVIEW REQUIRED with no virtual position', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    const card = within(slate).getByText(g4.away_team.name).closest('article') as HTMLElement
    expect(within(card).getByText('REVIEW REQUIRED')).toBeInTheDocument()
    expect(within(card).getByText(/EXTREME DISAGREEMENT — REVIEW REQUIRED/i)).toBeInTheDocument()
    expect(within(card).getByText('EXTREME-OVER-65')).toBeInTheDocument()
    expect(within(card).getByText('No virtual position')).toBeInTheDocument()
  })

  it('surfaces live availability and before/after-rescore context', async () => {
    stub()
    render(<App />)
    const slate = await completeSlate()
    const card = within(slate).getByText(g0.away_team.name).closest('article') as HTMLElement
    // Availability strip keeps real governed evidence (lineup / starter); the
    // deferred paid injury feed is not shown.
    expect(within(card).getByText(/^Lineup/)).toBeInTheDocument()
    expect(within(card).getByText(/^Starter/)).toBeInTheDocument()
    expect(within(card).queryByText(/Injury feed/)).not.toBeInTheDocument()
    // Before/after strip from the persisted rescore evidence.
    expect(within(card).getByText('Rescored')).toBeInTheDocument()
    expect(within(card).getByText(/Home win/)).toBeInTheDocument()
    expect(within(card).getByText(/Book moved/)).toBeInTheDocument()
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
