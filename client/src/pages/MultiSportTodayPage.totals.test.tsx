/**
 * Daily Decisions with a market filter on it.
 *
 * The page now carries two things that must never be confused: recorded
 * moneyline decisions, and totals research that is not a decision at all. The
 * tests below are the seams where that could go wrong.
 *
 *  - Selecting Totals must hide the moneyline rows without printing the
 *    empty-slate sentence. That sentence is a finding about the day; showing it
 *    because of a filter would report a UI state as a fact about the world.
 *  - Selecting Moneyline must issue no totals request at all. A reader who
 *    never opened the market should not cause the read.
 *  - A totals card must carry no stake and say what it is, sitting on the same
 *    page as a funded position.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { apiRequest, endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { TodayAllSportsReadModel } from '../types/multisport'
import type { TotalsBoard } from '../types/totals'
import { MultiSportTodayPage } from './MultiSportTodayPage'
// The four-sport CommandDecisions region has its own tests; these cover the page's audit view.
vi.mock('../components/command/CommandDecisions', () => ({ CommandDecisions: () => null }))


vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

const FUNNEL = {
  schema_version: 'AISP_EXPERIMENT_FUNNEL_V1', data_mode: 'live', available: true,
  experiment: {
    id: 'e1', name: 'ASP PRACTICAL PREGAME BANKROLL', policy_version: 'V2_1',
    starting_bankroll: 1000, start_date: '2026-09-01',
  },
  generated_at_utc: '2026-09-13T16:00:00Z', cutoff_minutes: 15, min_adjusted_edge: 0.02,
  stages: [], blocked_totals: [], price_restoration: null,
}

/** One priced Over, shaped the way the live slate actually carries it. */
const TOTALS = {
  schema_version: 'AISP_MLB_TOTALS_BOARD_V1', event_date: '2026-09-13',
  generated_at: '2026-09-13T19:00:00Z', probability_units: 'PERCENT',
  overs_only: false, filter_note: null,
  rows: [{
    game_id: 'mlb-sdsf', matchup: 'SD at SF',
    away_team: { id: 'a', abbreviation: 'SD', name: 'San Diego Padres' },
    home_team: { id: 'h', abbreviation: 'SF', name: 'San Francisco Giants' },
    starts_at: '2026-09-13T20:15:00Z', status: 'SCHEDULED',
    forecast: {
      engine_id: 'MLB_V18_WHOLE_GAME', engine_version: 'mlb-v18-whole-game-a6e7e99',
      model_label: 'V18 whole-game', forecast_id: 'fc-1', availability: 'AVAILABLE',
      availability_detail: '', decision_eligible: true, distribution_available: true,
      expected_total_runs: 8.0296, simulation_count: 5000, support: [1, 30],
      unavailable_reason: null,
      totals_verification: {
        status: 'VERIFIED_AGAINST_PUBLISHED_INDEX', detail: 'verified',
        index_sha256: '73d9', attached_at_utc: '2026-09-13T19:30:24Z',
        source_field: 'distributions.totalRunDistribution',
      },
    },
    market: {
      line: 7.5, line_source: 'RECORDED_SPORTSBOOK_LINE', line_note: null, books: {},
      observed_at: '2026-09-13T17:39:00Z', quote_age_minutes: 101, other_lines: [],
      unavailable_reason: null,
    },
    selections: [{
      side: 'OVER', line: 7.5, line_source: 'RECORDED_SPORTSBOOK_LINE',
      win_probability_pct: 49.42, push_probability_pct: 0, loss_probability_pct: 50.58,
      probability_units: 'PERCENT', push_possible: false,
      model_fair_decimal_odds: 2.023, model_fair_american_odds: 102,
      price_unavailable_reason: null,
      price: {
        sportsbook: 'Betmgm', decimal_odds: 2.05, american_odds: 105,
        observed_at: '2026-09-13T17:39:00Z', quote_age_minutes: 101, stale: false,
        odds_snapshot_id: 's1', market_outcome_id: 'm1',
        model_implied_net_return_per_unit: 0.0131,
        estimate_basis: 'V18_TOTAL_RUN_DISTRIBUTION',
      },
      explanation: {
        recorded_wager: false,
        status_line:
          'Research opportunity. No virtual wager, decision or stake exists for this selection.',
        bullets: [{
          key: 'selection',
          text: 'Over 7.5 in SD at SF is a research opportunity, not a recorded wager.',
        }],
        price: {
          model_probability_pct: 49.42, break_even_probability_pct: 48.78,
          no_vig_probability_pct: null,
        },
        value_framing: {
          headline: 'The model does NOT make this the more likely side.',
          note: 'The most likely outcome and the best estimated value can be different selections.',
        },
        details: { gates_passed: [], bound_at_decision_time: 'Bound to this exact line.' },
      },
    }],
  }],
  coverage: {
    games: 15, games_with_v18_distribution: 15, games_index_verified: 11,
    games_with_recorded_line: 1, games_priceable: 1, selections_with_usable_price: 2,
  },
  market_availability: {
    latest_total_quote_at: '2026-09-13T17:39:00Z',
    latest_total_quote_source: 'SPORTSGAMEODDS', age_hours: 1.7,
    detail: 'Totals were last captured at 17:39Z.',
  },
} as unknown as TotalsBoard

/** A funded moneyline position: the thing a totals card must not resemble. */
const MONEYLINE_SELECTION = {
  market_evidence_state: 'QUOTE_PRESENT', sport_code: 'BASEBALL', league_code: 'MLB',
  event_id: 'mlb-bosnyy', event_label: 'BOS @ NYY', starts_at: '2026-09-13T23:00:00Z',
  decision_state: 'FINAL_QUALIFIED', funding_state: 'FUNDED', market_label: 'Moneyline',
  selection_label: 'NYY', line: null, price_american: -120, sportsbook: 'governed-book',
  model_probability: 56, no_vig_probability: 53, adjusted_edge: 3, expected_value: 4,
  evidence_tier: 'TIER_1', virtual_stake: 20, experiment_id: 'e1',
  experiment_name: 'ASP PRACTICAL PREGAME BANKROLL', policy_version: 'POLICY_V1',
  official_position_id: 'pos-1', decision_timestamp: '2026-09-13T15:00:00Z',
  final_decision_cutoff: '2026-09-13T22:45:00Z',
  can_ever_fund_official_position: true, blocked_reasons: [],
}

const MODEL = {
  schema_version: 'AISP_TODAY_ALL_SPORTS_V1', data_mode: 'live',
  operational_date: '2026-09-13', generated_at_utc: '2026-09-13T19:00:00Z',
  sport_sections: [{
    sport_code: 'BASEBALL', league_code: 'MLB', league_label: 'MLB',
    scheduled_event_count: 15, valid_forecast_count: 15, final_qualified_count: 1,
    official_position_count: 1, selections: [MONEYLINE_SELECTION],
    research_only: false, notes: {},
  }],
  experiment: FUNNEL.experiment,
  operations: {
    automatic_odds_refresh_running: false, odds_refresh_state: 'POLLING_DISABLED',
    odds_refresh_detail: null,
  },
  availability: [], cross_sport_ranking: 'NOT_APPLICABLE_PHASE_1', notes: {},
} as unknown as TodayAllSportsReadModel

function mount(entry = '/today') {
  vi.spyOn(endpoints, 'today').mockResolvedValue(MODEL)
  vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
  // Routed by path: a totals request receives a totals payload, so the test
  // proves the page handles the right answer rather than surviving a wrong one.
  vi.mocked(apiRequest).mockImplementation((path: string) =>
    Promise.resolve((path.startsWith('/mlb/totals') ? TOTALS : FUNNEL) as never))
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <SportRegistryProvider>
        <MultiSportTodayPage />
      </SportRegistryProvider>
    </MemoryRouter>,
  )
}

const CARD = 'totals-op-mlb-sdsf-OVER-7.5'

describe('Daily Decisions — markets', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-13T19:00:00Z'))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('shows both markets by default', async () => {
    mount()
    expect(await screen.findByTestId('market-filter')).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId(CARD)).toBeTruthy())
    expect(screen.getByText('NYY')).toBeTruthy()
  })

  it('counts each market on its button, so an empty one is visible before switching', async () => {
    mount()
    const filter = within(await screen.findByTestId('market-filter'))
    await waitFor(() =>
      expect(filter.getByTestId('market-TOTALS').textContent).toMatch(/Totals\s*1/))
    expect(filter.getByTestId('market-MONEYLINE').textContent).toMatch(/Moneyline\s*1/)
  })

  it('hides moneyline rows under Totals without calling the slate empty', async () => {
    mount('/today?market=TOTALS')
    await waitFor(() => expect(screen.getByTestId(CARD)).toBeTruthy())
    expect(screen.queryByText('NYY')).toBeNull()
    expect(screen.queryByText(/No fundable selection has a recorded adjusted edge/)).toBeNull()
  })

  it('hides totals under Moneyline and issues no totals request', async () => {
    mount('/today?market=MONEYLINE')
    expect(await screen.findByText('NYY')).toBeTruthy()
    expect(screen.queryByTestId('totals-opportunities')).toBeNull()
    const paths = vi.mocked(apiRequest).mock.calls.map((call) => String(call[0]))
    expect(paths.some((path) => path.startsWith('/mlb/totals'))).toBe(false)
  })

  it('keeps the funded position and the research opportunity apart', async () => {
    mount('/today?market=TOTALS')
    await waitFor(() => expect(screen.getByTestId(CARD)).toBeTruthy())
    const card = screen.getByTestId(CARD).textContent ?? ''
    expect(card).toMatch(/Research opportunity · no stake/)
    expect(card).toMatch(/No virtual wager, decision or stake exists/)
    expect(card).not.toMatch(/stake of|Stake|\$20/)
  })

  it('narrows to Overs from the filter', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mount('/today?market=TOTALS')
    await waitFor(() => expect(screen.getByTestId(CARD)).toBeTruthy())
    await user.click(screen.getByTestId('overs-only'))
    await waitFor(() =>
      expect(screen.getByTestId('totals-op-count').textContent)
        .toMatch(/1 priced Over selection/))
  })
})
