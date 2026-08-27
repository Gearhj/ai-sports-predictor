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

function stubFetch(board: ShadowBoardData | null) {
  vi.stubGlobal('fetch', vi.fn(async () => {
    if (!board) return { ok: true, status: 200, json: async () => ({ ...BOARD, games: [] }) } as Response
    return { ok: true, status: 200, json: async () => board } as Response
  }))
}

describe('ShadowBoardPage', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
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
})
