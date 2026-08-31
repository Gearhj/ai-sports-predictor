import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { endpoints } from '../services/api'
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
      scheduled_event_count: 1, valid_forecast_count: 1, final_qualified_count: 0, awaiting_final_evidence_count: 1,
      provisional_count: 0, watch_count: 0, pass_count: 0, open_position_count: 0, settled_today_count: 0,
      virtual_stake: 0, data_health: 91, latest_forecast_at: '2026-08-31T15:00:00Z', latest_market_at: '2026-08-31T15:00:00Z',
      engine_label: 'AISP_BASELINE', can_create_official_positions: true, official_positions_blocked_reason: null,
      degraded: false, degraded_reason: null, selections: [baseSelection],
    },
    {
      sport_code: 'FOOTBALL', league_code: 'NFL', display_name: 'National Football League', availability_state: 'RESEARCH_SHADOW',
      scheduled_event_count: 1, valid_forecast_count: 1, final_qualified_count: 0, awaiting_final_evidence_count: 0,
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
    render(<MemoryRouter><MultiSportTodayPage /></MemoryRouter>)

    expect(await screen.findByRole('heading', { name: 'Today’s Sports Command Center' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Qualified today' })).toBeInTheDocument()
    expect(screen.getByText('BOS @ NYY')).toBeInTheDocument()
    expect(screen.getByText('DAL @ PHI')).toBeInTheDocument()
    expect(screen.getByText(/ranked only within MLB/i)).toBeInTheDocument()
    expect(screen.getByText(/ranked only within NFL/i)).toBeInTheDocument()
    expect(screen.queryByText(/opportunity score/i)).not.toBeInTheDocument()
    expect(screen.getByText(/No governed NFL market observation/i)).toBeInTheDocument()
    expect(screen.getByText('NBA').closest('[aria-disabled="true"]')).toBeInTheDocument()
    expect(screen.getByText(/Provider calls made by this read: 0/i)).toBeInTheDocument()
    expect(hasNoCrossSportRanking(payload)).toBe(true)
  })

  it('reports an honest empty daily slate without manufacturing events', async () => {
    vi.spyOn(endpoints, 'today').mockResolvedValue({
      ...payload,
      awaiting_final_evidence: [], provisional_opportunities: [],
      sport_sections: payload.sport_sections.map((section) => ({ ...section, scheduled_event_count: 0, valid_forecast_count: 0, selections: [] })),
    })
    render(<MemoryRouter><MultiSportTodayPage /></MemoryRouter>)
    expect(await screen.findByText(/No registered live or research-shadow league has a slate/i)).toBeInTheDocument()
    expect(screen.getByText(/A zero-position day is valid/i)).toBeInTheDocument()
  })
})
