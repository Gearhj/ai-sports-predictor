/**
 * NBA on Model Performance: HISTORICAL is stated in the title, a points-only
 * channel shows no probability metric, and a null winner is never "Missed".
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const metrics = (probability: boolean) => ({
  n: 1225, period: { first_start_utc: '2025-10-21T23:30:00Z', last_start_utc: '2026-04-12T23:00:00Z' },
  accuracy: probability ? 0.679 : null, correct_winners: probability ? 832 : null,
  log_loss: probability ? 0.606258 : null, brier_three_class: null, two_way_n: probability ? 1225 : 0,
  brier_two_way: probability ? 0.208971 : null, log_loss_two_way: probability ? 0.606258 : null, auc: null,
  margin_mae: probability ? null : 11.393, total_mae: probability ? null : 15.178,
  margin_errors: probability ? [] : [1, -2], total_errors: probability ? [] : [3, -4], market: null,
  calibration: [], series: [],
})

vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    const league = new URL(path, 'http://x').searchParams.get('league')
    const model = (engine: string, probability: boolean) => ({
      engine_id: engine, engine_version: 'A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION', model_label: engine,
      scoring_version: 'AISP_NBA_GEN1_CHANNEL_SCORING_V1', snapshot_types: [],
      evidence_classification: 'HISTORICAL_RECONSTRUCTION_NOT_PROSPECTIVE',
      channel: probability ? 'ELO_WINNER' : 'RIDGE_POINTS', is_probability: probability,
      cohorts: { ALL: metrics(probability), VERIFIED_PUBLICATION: { ...metrics(probability), n: 0 } },
      cohort_note: 'HISTORICAL: no publication clock.',
      games: [{ evaluation_id: `${engine}-1`, canonical_game_id: 'g-1', start_utc: '2025-10-21T23:30:00Z',
        home: 'OKC', away: 'HOU', final: { home: 125, away: 124 },
        home_probability_two_way: probability ? 0.735 : null, predicted_outcome: probability ? 'HOME' : null,
        actual_outcome: 'HOME', winner_correct: probability ? true : null, log_loss: probability ? 0.307 : null,
        margin_error: probability ? null : 7.5, total_error: probability ? null : -23.6, market_no_vig_home: null,
        market_book: null, publication_clock: null }],
    })
    return {
      schema_version: 'AISP_MODEL_PERFORMANCE_V1', generated_at_utc: '2026-09-19T18:00:00Z', league,
      evidence_as_of_utc: null, conventions: {},
      models: league === 'NBA'
        ? [model('NBA_GEN1_RIDGE_POINTS', false), model('NBA_GEN1_ELO_WINNER', true)] : [],
      requirement: league === 'NBA' ? null : `No ${league} forecast has been scored.`,
    }
  }),
}))

import { CommandPerformance } from './CommandPerformance'

afterEach(() => cleanup())

describe('CommandPerformance · NBA', () => {
  it('says HISTORICAL and draws no probability metric for the points channel', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NBA" /></MemoryRouter>)
    expect(await screen.findByTestId('perf-points-only')).toBeInTheDocument()
    expect(screen.getByText(/HISTORICAL reconstruction scores — not prospective/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2 }).textContent).toMatch(/Ridge expected points · HISTORICAL 2025–26/)
    expect(screen.getByText(/An expected score is not a probability/)).toBeInTheDocument()
    expect(screen.queryByText('Missed')).not.toBeInTheDocument()
  })

  it('shows the Elo winner channel with its own probability metrics', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NBA" /></MemoryRouter>)
    await screen.findByTestId('perf-points-only')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'NBA_GEN1_ELO_WINNER|A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION' } })
    expect(await screen.findByText('0.606')).toBeInTheDocument()
    expect(screen.queryByTestId('perf-points-only')).not.toBeInTheDocument()
  })
})
