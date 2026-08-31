import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SportRegistryProvider } from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import { sportRegistryFixture } from '../test/sportRegistryFixture'
import type { TodayAllSportsReadModel } from '../types/multisport'
import { hasNoCrossSportRanking, MultiSportTodayPage } from './MultiSportTodayPage'

const baseSelection = {
  sport_code: 'BASEBALL' as const,
  league_code: 'MLB' as const,
  event_id: 'mlb-1',
  event_label: 'BOS @ NYY',
  starts_at: '2026-08-31T23:00:00Z',
  decision_state: 'AWAITING_FINAL_EVIDENCE' as const,
  funding_state: 'ELIGIBLE_NOT_FUNDED' as const,
  market_label: 'Moneyline',
  selection_label: 'NYY',
  line: null,
  price_american: -120,
  sportsbook: 'governed-book',
  model_probability: 0.56,
  no_vig_probability: 0.53,
  adjusted_edge: 0.03,
  expected_value: 0.04,
  evidence_tier: 'TIER_1',
  virtual_stake: null,
  experiment_id: null,
  experiment_name: null,
  policy_version: 'POLICY_V1',
  official_position_id: null,
  decision_timestamp: '2026-08-31T15:00:00Z',
  final_decision_cutoff: '2026-08-31T22:45:00Z',
  source_label: 'AISP_BASELINE',
  model_status: 'DEVELOPMENT',
  outstanding_gates: ['LINEUP'],
  status_note: 'Awaiting final evidence.',
  can_ever_fund_official_position: true,
  sport_rank: 1,
  detail_href: '/games/mlb-1',
}

const payload: TodayAllSportsReadModel = {
  data_mode: 'live',
  schema_version: 'TODAY_ALL_SPORTS_READ_MODEL_V1',
  registry_version: 'SPORT_REGISTRY_V1',
  operational_date: '2026-08-31',
  display_timezone: 'America/New_York',
  generated_at: '2026-08-31T16:00:00Z',
  overall_summary: {
    final_qualified_decision_count: 0,
    official_position_count: 0,
    final_qualified_position_count: 0,
    total_virtual_stake: 0,
    open_position_count: 0,
    settled_today_count: 0,
    sports_with_official_positions: [],
    sports_with_events: ['MLB', 'NFL'],
    latest_complete_refresh: '2026-08-31T15:00:00Z',
    stale_or_degraded_sport_count: 1,
  },
  qualified_positions: [],
  awaiting_final_evidence: [baseSelection],
  provisional_opportunities: [{
    ...baseSelection,
    sport_code: 'FOOTBALL',
    league_code: 'NFL',
    event_id: 'nfl-1',
    event_label: 'DAL @ PHI',
    decision_state: 'PROVISIONAL_EDGE',
    funding_state: 'RESEARCH_ONLY',
    can_ever_fund_official_position: false,
    source_label: 'NFL_M1_XGBOOST',
    status_note: 'NFL research shadow — no official bankroll',
    detail_href: '/shadow-board?date=2026-08-31&sport=NFL#event-nfl-1',
  }],
  sport_sections: [
    {
      sport_code: 'BASEBALL', league_code: 'MLB', display_name: 'Major League Baseball', availability_state: 'LIVE',
      scheduled_event_count: 1, valid_forecast_count: 1, final_qualified_decision_count: 0, official_position_count: 0,
      final_qualified_count: 0, awaiting_final_evidence_count: 1,
      provisional_count: 0, watch_count: 0, pass_count: 0, open_position_count: 0, settled_today_count: 0,
      virtual_stake: 0, data_health: 91, latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
      engine_label: 'AISP_BASELINE', can_create_official_positions: true, official_positions_blocked_reason: null,
      degraded: false, degraded_reason: null, selections: [baseSelection],
    },
    {
      sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'National Football League', availability_state: 'RESEARCH_SHADOW',
      scheduled_event_count: 1, valid_forecast_count: 1, final_qualified_decision_count: 0, official_position_count: 0,
      final_qualified_count: 0, awaiting_final_evidence_count: 0,
      provisional_count: 1, watch_count: 0, pass_count: 0, open_position_count: 0, settled_today_count: 0,
      virtual_stake: 0, data_health: null, latest_forecast_at: '2026-08-31T14:00:00Z', latest_market_at: null,
      engine_label: 'NFL_M1_XGBOOST', can_create_official_positions: false, official_positions_blocked_reason: 'Research shadow only.',
      degraded: true, degraded_reason: 'No governed NFL market observation is persisted for this slate.', selections: [],
    },
  ],
  data_freshness: { latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z', scheduler_running: false, provider_calls_made: 0 },
  availability: [
    { sport_code: 'BASEBALL', league_code: 'MLB', display_name: 'Major League Baseball', availability: 'LIVE', navigable: true, note: null },
    { sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'National Football League', availability: 'RESEARCH_SHADOW', navigable: true, note: null },
    { sport_code: 'BASKETBALL', league_code: 'NBA', display_name: 'National Basketball Association', availability: 'PLANNED', navigable: false, note: null },
  ],
  cross_sport_ranking: 'NOT_APPLICABLE_PHASE_1',
  notes: {},
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('MultiSportTodayPage', () => {
  it('renders governed hierarchy and sport-isolated ordering without a cross-sport score', async () => {
    vi.spyOn(endpoints, 'today').mockResolvedValue(payload)
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Today’s Sports Command Center' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Qualified today' })).toBeInTheDocument()
    expect(screen.getByText('BOS @ NYY')).toBeInTheDocument()
    expect(screen.getByText('DAL @ PHI')).toBeInTheDocument()
    expect(screen.getByText(/ranked only within MLB/i)).toBeInTheDocument()
    expect(screen.getByText(/ranked only within NFL/i)).toBeInTheDocument()
    expect(screen.queryByText(/opportunity score/i)).not.toBeInTheDocument()
    expect(screen.getByText(/No governed NFL market observation/i)).toBeInTheDocument()
    const plannedSports = screen.getByRole('heading', { name: 'Planned sports' }).closest('section')!
    expect(within(plannedSports).getAllByText('NBA')[0].closest('[aria-disabled="true"]')).toBeInTheDocument()
    expect(screen.getAllByText(/Final-decision cutoff Aug 31, 6:45 PM EDT/i)).toHaveLength(2)
    expect(screen.getByText(/Provider calls made by this read: 0/i)).toBeInTheDocument()
    expect(hasNoCrossSportRanking(payload)).toBe(true)
  })

  it('reports an honest empty daily slate without manufacturing events', async () => {
    vi.spyOn(endpoints, 'today').mockResolvedValue({
      ...payload,
      awaiting_final_evidence: [], provisional_opportunities: [],
      sport_sections: payload.sport_sections.map((section) => ({ ...section, scheduled_event_count: 0, valid_forecast_count: 0, selections: [] })),
    })
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    renderPage()
    expect(await screen.findByText(/No registered live or research-shadow league has a slate/i)).toBeInTheDocument()
    expect(screen.getByText(/A zero-position day is valid/i)).toBeInTheDocument()
  })

  it('keeps official-position and final-decision counts semantically distinct', async () => {
    const officialPosition = {
      ...baseSelection,
      event_id: 'mlb-official-1',
      event_label: 'TOR @ BAL',
      decision_state: 'FINAL_QUALIFIED' as const,
      funding_state: 'OFFICIAL_VIRTUAL_POSITION' as const,
      official_position_id: 'position-1',
      virtual_stake: 12,
      outstanding_gates: [],
    }
    const reconciled: TodayAllSportsReadModel = {
      ...payload,
      overall_summary: {
        ...payload.overall_summary,
        official_position_count: 1,
        final_qualified_decision_count: 2,
        final_qualified_position_count: 99,
      },
      qualified_positions: [officialPosition],
      sport_sections: payload.sport_sections.map((section) => section.league_code === 'MLB'
        ? { ...section, official_position_count: 1, final_qualified_decision_count: 2, final_qualified_count: 99 }
        : { ...section, official_position_count: 0, final_qualified_decision_count: 0 }),
    }
    vi.spyOn(endpoints, 'today').mockResolvedValue(reconciled)
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    renderPage()

    const summary = await screen.findByRole('region', { name: 'All-sports daily summary' })
    expect(withinMetric(summary, 'Official positions')).toHaveTextContent('1')
    expect(withinMetric(summary, 'Final qualified decisions')).toHaveTextContent('2')
    expect(screen.getByRole('heading', { name: 'Qualified today' }).closest('section')).toHaveTextContent('TOR @ BAL')

    const mlbCard = screen.getByRole('heading', { name: 'Major League Baseball' }).closest('article')!
    expect(withinMetric(mlbCard, 'Official positions')).toHaveTextContent('1')
    expect(withinMetric(mlbCard, 'Final qualified decisions')).toHaveTextContent('2')
    const nflCard = screen.getByRole('heading', { name: 'National Football League' }).closest('article')!
    expect(withinMetric(nflCard, 'Official positions')).toHaveTextContent('0')
  })

  it('fails the command center closed when registry authority is unavailable', async () => {
    vi.spyOn(endpoints, 'today').mockResolvedValue(payload)
    vi.spyOn(endpoints, 'sportsRegistry').mockRejectedValue(new Error('registry offline'))
    renderPage()

    expect(await screen.findByText(/authoritative sport registry is unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/closed rather than inventing active sports/i)).toBeInTheDocument()
    expect(screen.queryByText('BOS @ NYY')).not.toBeInTheDocument()
    expect(screen.queryByText('DAL @ PHI')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Major League Baseball' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'National Football League' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'All-sports daily summary' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open MLB view/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open NFL view/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Planned sports' })).not.toBeInTheDocument()
  })

  it('uses full multi-authority identity and counts selections separately from events', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const v1 = {
      ...baseSelection,
      experiment_id: 'experiment-v1',
      experiment_name: 'V1 strict control',
      policy_version: 'AISP_V1',
    }
    const v21 = {
      ...baseSelection,
      experiment_id: 'experiment-v2-1',
      experiment_name: 'V2.1 challenger',
      policy_version: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1',
    }
    vi.spyOn(endpoints, 'today').mockResolvedValue({
      ...payload,
      awaiting_final_evidence: [v1, v21],
    })
    vi.spyOn(endpoints, 'sportsRegistry').mockResolvedValue(sportRegistryFixture)
    renderPage()

    expect(await screen.findByText(/2 selections · 1 event · ranked only within MLB/i)).toBeInTheDocument()
    expect(screen.getAllByText('BOS @ NYY')).toHaveLength(2)
    expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(/same key|encountered two children/i)
  })
})

function renderPage() {
  render(
    <MemoryRouter>
      <SportRegistryProvider>
        <MultiSportTodayPage />
      </SportRegistryProvider>
    </MemoryRouter>,
  )
}

function withinMetric(container: HTMLElement, label: string): HTMLElement {
  const labelNode = Array.from(container.querySelectorAll('dt, .metric-label'))
    .find((node) => node.textContent === label)
  if (!labelNode?.parentElement) throw new Error(`Metric not found: ${label}`)
  return labelNode.parentElement
}
