import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ShadowBoardPage } from './ShadowBoardPage'
import type { ShadowBoardData } from '../types/domain'

const BOARD: ShadowBoardData = {
  slate_date: '2024-09-16',
  status: 'RESEARCH_SHADOW',
  executable: false,
  games: [
    {
      event_id: 'game-1',
      event_date: '2024-09-16',
      home_team_id: 'team-h',
      away_team_id: 'team-a',
      model_disagreement: 0.013,
      engines: [
        {
          engine_id: 'DIRECT_GOLD', forecast_id: 'aisp-game-forecast:sha256:aa', status: 'RESEARCH_SHADOW_NOT_READY',
          is_ready: false, readiness_score: 80, readiness_threshold: 85,
          home_win_probability: 0.665, away_win_probability: 0.335,
          expected_home_score: 4.68, expected_away_score: 3.23,
          first_five_home_win_probability: 0.54, first_five_away_win_probability: 0.30,
          decisions: [
            {
              market: 'FULL_GAME_MONEYLINE', selection: 'HOME', model_probability: 0.665, has_quote: false,
              book: null, line: null, american_odds: null, best_american_odds: null, no_vig_probability: null,
              raw_edge: null, no_vig_edge: null, expected_value: null, uncertainty_adjusted_edge: null,
              recommendation: 'NO_BET', virtual_stake: 0,
            },
          ],
        },
        {
          engine_id: 'E10', forecast_id: 'aisp-game-forecast:sha256:bb', status: 'RESEARCH_SHADOW_NOT_READY',
          is_ready: false, readiness_score: 80, readiness_threshold: 85,
          home_win_probability: 0.652, away_win_probability: 0.348,
          expected_home_score: 4.75, expected_away_score: 3.51,
          first_five_home_win_probability: 0.54, first_five_away_win_probability: 0.326,
          decisions: [
            {
              market: 'FULL_GAME_MONEYLINE', selection: 'HOME', model_probability: 0.652, has_quote: false,
              book: null, line: null, american_odds: null, best_american_odds: null, no_vig_probability: null,
              raw_edge: null, no_vig_edge: null, expected_value: null, uncertainty_adjusted_edge: null,
              recommendation: 'NO_BET', virtual_stake: 0,
            },
          ],
        },
      ],
    },
  ],
}

const NFL_BOARD: ShadowBoardData = {
  slate_date: '2026-09-09',
  status: 'RESEARCH_SHADOW',
  executable: false,
  sports_available: ['BASEBALL', 'NFL'],
  active_sport: 'NFL',
  lineage_disclosure: {
    model: 'NFL M1 Long-History XGBoost',
    status: 'Research shadow — not promoted',
    training_evidence: '1999 through completed 2025 season',
    training_cutoff: '2026-02-08',
    feature_tier: 'LONG_HISTORY_CORE',
    historical_oot_win_log_loss: 0.628770,
    historical_sportsbook_benchmark: 0.606989,
    historical_conclusion: 'No demonstrated improvement over the market',
  },
  games: [],
}

const RESCORE_BOARD = {
  slate_date: '2024-09-16',
  status: 'RESEARCH_SHADOW',
  executable: false,
  rescores: [
    {
      id: 'r1', game_id: 'game-1', engine: 'AISP_BASELINE',
      availability_change: { event_type: 'LATE_SCRATCH', source: 'MANUAL_ADMIN', player_name: 'Star Player', description: 'scratched' },
      forecast_before: { home_win_probability: 0.6, readiness: 70, uncertainty: 0.05 },
      forecast_after: { home_win_probability: 0.55, readiness: 68, uncertainty: 0.06 },
      quote_before: { book_home: 'B', book_away: 'B', home_american: -120, away_american: 100, no_vig_home: 0.53, no_vig_away: 0.47, observed_at: 'x' },
      quote_after: { book_home: 'B', book_away: 'B', home_american: -110, away_american: 100, no_vig_home: 0.52, no_vig_away: 0.48, observed_at: 'y' },
      book_moved: true,
      decisions: { before: { HOME: { selection: 'HOME', model_probability: 0.6, has_quote: true, no_vig_probability: 0.53, edge: 0.07, expected_value: 0.1, recommendation: 'WATCH', virtual_stake: 0 } }, after: { HOME: { selection: 'HOME', model_probability: 0.55, has_quote: true, no_vig_probability: 0.52, edge: 0.03, expected_value: 0.05, recommendation: 'WATCH', virtual_stake: 0 } } },
      recommendation: 'WATCH', virtual_stake: 0,
      timing: { aisp_received_at: '2024-09-16T00:00:00Z', rescore_completed_at: '2024-09-16T00:00:01Z' },
      quote_timeline: {},
    },
  ],
}

function stubFetch(board: ShadowBoardData | null, rescoreBoard: unknown = { ...RESCORE_BOARD, rescores: [] }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/shadow-board/rescores')) {
      return { ok: true, status: 200, json: async () => rescoreBoard } as Response
    }
    if (!board) return { ok: true, status: 200, json: async () => ({ ...BOARD, games: [] }) } as Response
    return { ok: true, status: 200, json: async () => board } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ShadowBoardPage', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    window.history.replaceState({}, '', '/')
  })

  it('shows both engines, the research-shadow notice, and NO_BET decisions', async () => {
    stubFetch(BOARD)
    render(<ShadowBoardPage />)

    expect(await screen.findByRole('heading', { name: /Shadow Betting Board/i })).toBeInTheDocument()
    expect(screen.getByText(/Research shadow — not executable/i)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'DIRECT_GOLD' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'E10' })).toBeInTheDocument()
    // NO_BET is shown (and there is no qualified/executable affordance).
    expect(screen.getAllByText('NO BET').length).toBeGreaterThan(0)
    expect(screen.queryByText(/place bet/i)).not.toBeInTheDocument()
  })

  it('renders a clean empty state when no shadow forecasts are imported', async () => {
    stubFetch(null)
    render(<ShadowBoardPage />)
    expect(await screen.findByText(/No imported shadow forecasts for this slate/i)).toBeInTheDocument()
  })

  it('shows an availability rescore with before/after and book-moved state', async () => {
    stubFetch(null, RESCORE_BOARD)
    render(<ShadowBoardPage />)
    expect(await screen.findByText(/Live availability rescores/i)).toBeInTheDocument()
    expect(screen.getByText('Star Player')).toBeInTheDocument()
    expect(screen.getByText(/LATE_SCRATCH · MANUAL_ADMIN/i)).toBeInTheDocument()
    expect(screen.getByText('BOOK MOVED')).toBeInTheDocument()
    expect(screen.getByText('Home win before')).toBeInTheDocument()
    expect(screen.getByText('Home win after')).toBeInTheDocument()
  })

  it('initializes an NFL slate from the URL and renders the exact frozen model lineage', async () => {
    window.history.replaceState({}, '', '/shadow-board?date=2026-09-09&sport=NFL')
    const fetchMock = stubFetch(NFL_BOARD)
    render(<ShadowBoardPage />)

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) =>
        String(input).includes('/shadow-board?date=2026-09-09&sport=NFL'),
      )).toBe(true)
    })
    expect(screen.getByLabelText('Slate date')).toHaveValue('2026-09-09')
    expect(await screen.findByRole('tab', { name: 'NFL' })).toHaveClass('tab-button--active')
    expect(screen.getByRole('heading', { name: 'What model is this?' })).toBeInTheDocument()
    expect(screen.getByText(/NFL M1 Long-History XGBoost/)).toBeInTheDocument()
    expect(screen.getByText(/Research shadow — not promoted/)).toBeInTheDocument()
    expect(screen.getByText(/1999 through completed 2025 season/)).toBeInTheDocument()
    expect(screen.getByText(/2026-02-08/)).toBeInTheDocument()
    expect(screen.getByText(/LONG_HISTORY_CORE/)).toBeInTheDocument()
    expect(screen.getByText(/0\.628770/)).toBeInTheDocument()
    expect(screen.getByText(/0\.606989/)).toBeInTheDocument()
    expect(screen.getByText(/No demonstrated improvement over the market/)).toBeInTheDocument()
  })
})
