/**
 * Daily Decisions — the guarantees the redesign had to carry forward.
 *
 * The visual language changed completely; the things this page is not allowed
 * to say did not. Each test below is one of those: a missing price is never a
 * zero, a research-shadow league is never a declined decision, blockers never
 * outnumber the games they are drawn from, an empty slate is stated rather
 * than filled in, and a failed read substitutes nothing.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { apiRequest, endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { NormalizedSelection, SportSection, TodayAllSportsReadModel } from '../types/multisport'
import { firstBlocker, hasNoCrossSportRanking, MultiSportTodayPage } from './MultiSportTodayPage'
// The four-sport CommandDecisions region has its own tests; these cover the page's audit view.
vi.mock('../components/command/CommandDecisions', () => ({ CommandDecisions: () => null }))


vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

const funnel = {
  schema_version: 'AISP_EXPERIMENT_FUNNEL_V1', data_mode: 'live', available: true,
  experiment: { id: 'e1', name: 'ASP PRACTICAL PREGAME BANKROLL', policy_version: 'V2_1', starting_bankroll: 1000, start_date: '2026-09-01' },
  generated_at_utc: '2026-08-31T16:00:00Z', cutoff_minutes: 15, min_adjusted_edge: 0.02,
  stages: [], blocked_totals: [], price_restoration: null,
}

const baseSelection: NormalizedSelection = {
  market_evidence_state: 'QUOTE_PRESENT',
  sport_code: 'BASEBALL', league_code: 'MLB', event_id: 'mlb-1', event_label: 'BOS @ NYY',
  starts_at: '2026-08-31T23:00:00Z', decision_state: 'AWAITING_FINAL_EVIDENCE',
  funding_state: 'ELIGIBLE_NOT_FUNDED', market_label: 'Moneyline', selection_label: 'NYY',
  line: null, price_american: -120, sportsbook: 'governed-book',
  model_probability: 56, no_vig_probability: 53, adjusted_edge: 3, expected_value: 4,
  evidence_tier: 'TIER_1', virtual_stake: null, experiment_id: null, experiment_name: null,
  policy_version: 'POLICY_V1', official_position_id: null,
  decision_timestamp: '2026-08-31T15:00:00Z', final_decision_cutoff: '2026-08-31T22:45:00Z',
  source_label: 'AISP_BASELINE', model_status: 'DEVELOPMENT', outstanding_gates: [],
  status_note: 'Awaiting final evidence.', can_ever_fund_official_position: true,
  sport_rank: 1, detail_href: '/games/mlb-1',
} as NormalizedSelection

const nflSelection: NormalizedSelection = {
  ...baseSelection,
  sport_code: 'FOOTBALL', league_code: 'NFL', event_id: 'nfl-1', event_label: 'DAL @ PHI',
  decision_state: 'PROVISIONAL_EDGE', funding_state: 'RESEARCH_ONLY',
  can_ever_fund_official_position: false, source_label: 'NFL_M1_XGBOOST',
  market_evidence_state: null, price_american: null, adjusted_edge: null,
  outstanding_gates: ['Awaiting final pregame research snapshot'],
  status_note: 'NFL research shadow — no official bankroll',
  detail_href: '/shadow-board?date=2026-08-31&sport=NFL',
}

function section(over: Partial<SportSection> & { selections: NormalizedSelection[] }): SportSection {
  return {
    sport_code: 'BASEBALL', league_code: 'MLB', display_name: 'MLB', availability_state: 'LIVE',
    scheduled_event_count: over.selections.length, valid_forecast_count: over.selections.length,
    final_qualified_decision_count: 0, official_position_count: 0, final_qualified_count: 0,
    awaiting_final_evidence_count: over.selections.length, provisional_count: 0, watch_count: 0,
    pass_count: 0, open_position_count: 0, settled_today_count: 0, virtual_stake: 0,
    data_health: 91, latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
    engine_label: 'AISP_BASELINE', can_create_official_positions: true,
    official_positions_blocked_reason: null, degraded: false, degraded_reason: null,
    ...over,
  } as SportSection
}

function model(sections: SportSection[]): TodayAllSportsReadModel {
  const selections = sections.flatMap((entry) => entry.selections)
  return {
    data_mode: 'live', schema_version: 'TODAY_ALL_SPORTS_READ_MODEL_V1',
    registry_version: 'SPORT_REGISTRY_V1', operational_date: '2026-08-31',
    display_timezone: 'America/New_York', generated_at: '2026-08-31T16:00:00Z',
    overall_summary: {
      final_qualified_decision_count: 0, official_position_count: 0, final_qualified_position_count: 0,
      total_virtual_stake: 0, open_position_count: 0, settled_today_count: 0,
      sports_with_official_positions: [], sports_with_events: ['MLB'],
      latest_complete_refresh: '2026-08-31T15:00:00Z', stale_or_degraded_sport_count: 0,
    },
    qualified_positions: selections.filter((item) => item.decision_state === 'FINAL_QUALIFIED'),
    awaiting_final_evidence: selections.filter((item) => item.decision_state === 'AWAITING_FINAL_EVIDENCE'),
    provisional_opportunities: selections.filter((item) => item.decision_state === 'PROVISIONAL_EDGE'),
    sport_sections: sections,
    data_freshness: {
      latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
      scheduler_running: true, provider_calls_made: 0,
      automatic_odds_refresh_running: false, odds_refresh_state: 'POLLING_DISABLED', odds_refresh_detail: null,
    },
    availability: [], cross_sport_ranking: 'NOT_APPLICABLE_PHASE_1', notes: {},
  } as unknown as TodayAllSportsReadModel
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-08-31T16:00:00Z'))
  vi.mocked(apiRequest).mockResolvedValue(funnel as never)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function mount(data: TodayAllSportsReadModel, registryOk = true) {
  vi.spyOn(endpoints, 'today').mockResolvedValue(data)
  if (registryOk) vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
  else vi.spyOn(endpoints, 'sportsRegistry').mockRejectedValue(new Error('registry offline'))
  render(
    <MemoryRouter>
      <SportRegistryProvider>
        <MultiSportTodayPage />
      </SportRegistryProvider>
    </MemoryRouter>,
  )
}

const panel = (name: RegExp | string) => screen.getByRole('region', { name })

describe('Daily Decisions — the funnel', () => {
  it('counts games, not decisions, and shows what dropped at each stage', async () => {
    mount(model([section({
      selections: [baseSelection, { ...baseSelection, event_id: 'mlb-2', event_label: 'LAD @ SF' }],
      scheduled_event_count: 4, valid_forecast_count: 3,
      final_qualified_count: 1, official_position_count: 1,
    })]))
    const funnelPanel = await screen.findByRole('region', { name: /recorded position/i })
    expect(within(funnelPanel).getByText('Scheduled').parentElement).toHaveTextContent('4')
    expect(within(funnelPanel).getByText('Forecasted').parentElement).toHaveTextContent('3')
    // Two distinct events carry a persisted quote.
    expect(within(funnelPanel).getByText('Priced').parentElement).toHaveTextContent('2')
    expect(within(funnelPanel).getByText('Qualified').parentElement).toHaveTextContent('1')
    expect(within(funnelPanel).getByText('Positioned').parentElement).toHaveTextContent('1')
  })

  it('counts one game once even when it was evaluated at several phases', async () => {
    // The same event at MORNING and FINAL_PREGAME is one game, not two.
    mount(model([section({
      selections: [
        baseSelection,
        { ...baseSelection, decision_state: 'FINAL_QUALIFIED', decision_timestamp: '2026-08-31T15:30:00Z' },
      ],
      scheduled_event_count: 1, valid_forecast_count: 1,
    })]))
    const funnelPanel = await screen.findByRole('region', { name: /recorded position/i })
    expect(within(funnelPanel).getByText('Priced').parentElement).toHaveTextContent('1')
  })

  it('says a research-shadow league is not a declined decision', async () => {
    mount(model([
      section({ selections: [baseSelection] }),
      section({
        sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'NFL',
        availability_state: 'RESEARCH_SHADOW', can_create_official_positions: false,
        official_positions_blocked_reason: 'Research shadow only.', selections: [nflSelection],
      }),
    ]))
    const funnelPanel = await screen.findByRole('region', { name: /recorded position/i })
    expect(funnelPanel).toHaveTextContent(/NFL is research shadow in this phase and cannot create an official position/i)
    expect(funnelPanel).toHaveTextContent(/not counted as decisions the policy declined/i)
  })
})

describe('Daily Decisions — opportunities and the threshold', () => {
  it('separates a qualified selection from one that is merely close', async () => {
    mount(model([section({
      selections: [
        { ...baseSelection, decision_state: 'FINAL_QUALIFIED', adjusted_edge: 3.4 },
        { ...baseSelection, event_id: 'mlb-2', event_label: 'LAD @ SF', adjusted_edge: 1.9 },
      ],
    })]))
    const best = await screen.findByRole('region', { name: /Ranked by adjusted edge/i })
    const rows = within(best).getAllByRole('article')
    expect(rows[0].className).toContain('bx-op--qualified')
    expect(rows[0]).toHaveTextContent(/Qualified/)
    expect(rows[1].className).toContain('bx-op--near')
    expect(rows[1]).toHaveTextContent(/Near threshold/)
  })

  it('states the exact distance from the minimum in both directions', async () => {
    mount(model([section({
      selections: [
        { ...baseSelection, decision_state: 'FINAL_QUALIFIED', adjusted_edge: 2.96 },
        { ...baseSelection, event_id: 'mlb-2', event_label: 'LAD @ SF', adjusted_edge: 1.9 },
      ],
    })]))
    const best = await screen.findByRole('region', { name: /Ranked by adjusted edge/i })
    expect(best).toHaveTextContent(/\+0\.96 pts above the minimum/)
    expect(best).toHaveTextContent(/0\.10 pts short of the minimum/)
  })

  it('reads the 2.0-point minimum from the policy rather than restating it', async () => {
    vi.mocked(apiRequest).mockResolvedValue({ ...funnel, min_adjusted_edge: 0.03 } as never)
    mount(model([section({ selections: [{ ...baseSelection, adjusted_edge: 2.5 }] })]))
    const best = await screen.findByRole('region', { name: /Ranked by adjusted edge/i })
    // With a 3.0 minimum the same 2.5-point edge is short, not clear.
    expect(best).toHaveTextContent(/0\.50 pts short of the minimum/)
  })

  it('says plainly that a zero-selection day is a valid outcome', async () => {
    mount(model([section({ selections: [] as NormalizedSelection[], scheduled_event_count: 0, valid_forecast_count: 0 })]))
    const best = await screen.findByRole('region', { name: /Ranked by adjusted edge/i })
    expect(best).toHaveTextContent(/A zero-selection day is a valid outcome, not a missing result/i)
  })

  it('keeps a game with no recorded edge out of the ranking entirely', async () => {
    mount(model([section({ selections: [{ ...baseSelection, adjusted_edge: null }] })]))
    const best = await screen.findByRole('region', { name: /Ranked by adjusted edge/i })
    expect(within(best).queryAllByRole('article')).toHaveLength(0)
  })
})

describe('Daily Decisions — blockers', () => {
  it('charges each game to the first gate it failed, so counts never double', () => {
    // No price AND no edge is a pricing problem. Counting it twice would make
    // the blockers add to more games than the slate holds.
    expect(firstBlocker({
      ...baseSelection, market_evidence_state: null, price_american: null,
      outstanding_gates: ['Adjusted edge -1.9 pts below the 2.0 pt minimum'],
    })).toBe('MISSING_QUOTE')
    expect(firstBlocker({
      ...baseSelection,
      outstanding_gates: ['Adjusted edge -1.9 pts below the 2.0 pt minimum'],
    })).toBe('INSUFFICIENT_EDGE')
    expect(firstBlocker({ ...baseSelection, model_probability: null })).toBe('MISSING_FORECAST')
    expect(firstBlocker(nflSelection)).toBe('RESEARCH_ONLY')
    expect(firstBlocker(baseSelection)).toBeNull()
  })

  it('never renders a missing sportsbook price as a zero', async () => {
    mount(model([section({
      selections: [{ ...baseSelection, market_evidence_state: null, price_american: null, adjusted_edge: null }],
    })]))
    const blockers = await screen.findByRole('region', { name: /Blockers/i })
    expect(blockers).toHaveTextContent(/No usable price/)
    expect(blockers).not.toHaveTextContent(/\$0|0 pts|0%/)
  })

  it('counts distinct games, and says so', async () => {
    mount(model([section({
      selections: [
        { ...baseSelection, outstanding_gates: ['Adjusted edge -1.9 pts below the 2.0 pt minimum'] },
        { ...baseSelection, outstanding_gates: ['Adjusted edge -0.4 pts below the 2.0 pt minimum'] },
      ],
      scheduled_event_count: 1, valid_forecast_count: 1,
    })]))
    const blockers = await screen.findByRole('region', { name: /Blockers/i })
    // Two rows, one event: one blocked game.
    expect(within(blockers).getByText('Edge below the threshold').parentElement).toHaveTextContent('1')
    expect(blockers).toHaveTextContent(/never exceed the games on the slate/i)
  })
})

describe('Daily Decisions — preserved governance guarantees', () => {
  it('fails closed when registry authority is unavailable', async () => {
    mount(model([section({ selections: [baseSelection] })]), false)
    expect(await screen.findByRole('alert')).toHaveTextContent(/fails closed rather than guessing/i)
    expect(screen.queryByRole('region', { name: /Ranked by adjusted edge/i })).toBeNull()
  })

  it('substitutes nothing when the read fails', async () => {
    vi.spyOn(endpoints, 'today').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    render(
      <MemoryRouter><SportRegistryProvider><MultiSportTodayPage /></SportRegistryProvider></MemoryRouter>,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/No decision, count or price is substituted/i)
  })

  it('states that the read made no provider call', async () => {
    mount(model([section({ selections: [baseSelection] })]))
    expect(await screen.findByText(/made no provider call and created no decision, position or settlement/i))
      .toBeInTheDocument()
  })

  it('ranks within a sport only and never publishes a cross-sport score', async () => {
    const payload = model([section({ selections: [baseSelection] })])
    expect(hasNoCrossSportRanking(payload)).toBe(true)
    mount(payload)
    expect(await screen.findByText(/ranked within a sport only; no cross-sport score exists/i))
      .toBeInTheDocument()
  })

  it('shows a cutoff timeline that separates locked from upcoming', async () => {
    mount(model([section({
      selections: [
        { ...baseSelection, final_decision_cutoff: '2026-08-31T15:00:00Z' },
        { ...baseSelection, event_id: 'mlb-2', event_label: 'LAD @ SF', final_decision_cutoff: '2026-08-31T23:00:00Z' },
      ],
    })]))
    const timeline = await screen.findByRole('region', { name: /When decisions lock/i })
    expect(timeline).toHaveTextContent(/locked/)
    expect(timeline).toHaveTextContent(/upcoming/)
  })
})
