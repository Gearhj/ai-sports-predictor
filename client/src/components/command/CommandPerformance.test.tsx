import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requests: string[] = []
vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    requests.push(path)
    const league = new URL(path, 'http://x').searchParams.get('league')
    const metrics = (n: number) => ({
      n, period: { first_start_utc: '2026-09-07T22:05:00Z', last_start_utc: '2026-09-14T21:40:00Z' },
      accuracy: 0.6, correct_winners: Math.round(n * 0.6), log_loss: 0.64, brier_three_class: 0.45,
      two_way_n: n, brier_two_way: 0.225, log_loss_two_way: 0.64, auc: 0.7, margin_mae: 3.2, total_mae: 3.4,
      margin_errors: [1, -2], total_errors: [0.5, -1],
      market: { n: 2, model_brier_two_way: 0.23, market_brier_two_way: 0.22, delta_brier: 0.01, model_log_loss_two_way: 0.65, market_log_loss_two_way: 0.64, delta_log_loss: 0.01 },
      calibration: [{ lower: 0.5, upper: 0.6, n: 2, mean_predicted: 0.55, observed: 0.5 }],
      series: [],
    })
    return {
      schema_version: 'AISP_MODEL_PERFORMANCE_V1', generated_at_utc: '2026-09-15T18:00:00Z', league,
      evidence_as_of_utc: '2026-09-15T05:00:00Z', conventions: { brier_three_class: 'summed over three classes' },
      models: league === 'MLB' ? [{
        engine_id: 'MLB_V18_WHOLE_GAME', engine_version: 'v18', model_label: 'MLB_V18_WHOLE_GAME', scoring_version: 'S',
        snapshot_types: ['PROSPECTIVE'], evidence_classification: 'PROSPECTIVE_RESEARCH_SCORES',
        cohorts: { ALL: metrics(70), VERIFIED_PUBLICATION: metrics(56) }, cohort_note: '56 of 70 verified.', games: [],
      }] : [],
      requirement: league === 'MLB' ? null : `No ${league} forecast has been scored.`,
    }
  }),
}))

import { CommandPerformance } from './CommandPerformance'

afterEach(() => { cleanup(); requests.length = 0 })

describe('CommandPerformance', () => {
  it('reads one league at a time and states the Brier convention beside the number', async () => {
    render(<MemoryRouter><CommandPerformance /></MemoryRouter>)
    const tiles = await screen.findByText('70 games')
    const summary = tiles.closest('dl') as HTMLElement
    expect(within(summary).getByText(/Brier · three-class/i)).toBeInTheDocument()
    expect(within(summary).getByText('range 0–2 · two-way 0.225')).toBeInTheDocument()
    expect(requests.every((path) => path.includes('league=MLB'))).toBe(true)
  })

  it('switches cohort without pooling and shows an honest empty state for an unscored sport', async () => {
    render(<MemoryRouter><CommandPerformance /></MemoryRouter>)
    await screen.findByText('70 games')
    fireEvent.click(screen.getByRole('button', { name: /Verified publication clock · 56/ }))
    expect(await screen.findByText('56 games')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /NBA/ }))
    expect(await screen.findByText('No NBA forecast has been scored.')).toBeInTheDocument()
    expect(screen.queryByText('70 games')).not.toBeInTheDocument()
  })
})
