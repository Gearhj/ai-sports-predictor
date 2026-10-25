/**
 * NHL on Model Performance (Detailed): research-only NHL mounts its own tiles,
 * its scored-games ledger and an explicit "no official positions" panel -- the
 * regulation 3-way and final 2-way entries are never pooled, and the two-way
 * charts built for single-outcome sports are not drawn.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requests: string[] = []

const metrics = (regulation: boolean) => ({
  n: 2, period: { first_start_utc: '2026-10-08T23:00:00Z', last_start_utc: '2026-10-09T23:00:00Z' },
  accuracy: 0.5, correct_winners: 1,
  log_loss: regulation ? 1.02 : null, brier_three_class: regulation ? 0.61 : null,
  two_way_n: regulation ? 0 : 2, brier_two_way: regulation ? null : 0.231, log_loss_two_way: regulation ? null : 0.655,
  auc: null, margin_mae: null, total_mae: null, margin_errors: [], total_errors: [], market: null, calibration: [], series: [],
  ...(regulation ? { regulation_ties: 1 } : { decided_in: { REGULATION: 1, OVERTIME: 1, SHOOTOUT: 0 }, regulation_not_modelled: 0 }),
})

const entry = (engine: string, outcome: 'REGULATION_3WAY' | 'FINAL_2WAY') => ({
  engine_id: engine, engine_version: 'nhl-gen1-v1', model_label: engine, scoring_version: 'AISP_NHL_GEN1_SCORING_V1',
  snapshot_types: [], evidence_classification: 'PROSPECTIVE_RESEARCH', outcome_definition: outcome, record_class: 'FORECAST',
  cohorts: { ALL: metrics(outcome === 'REGULATION_3WAY'), VERIFIED_PUBLICATION: { ...metrics(outcome === 'REGULATION_3WAY'), n: 0 } },
  cohort_note: 'Published before the start.',
  games: [{
    evaluation_id: `${engine}-1`, nhl_game_id: 'nhl:game:2026020041', start_utc: '2026-10-09T23:00:00Z', home: 'NJD', away: 'OTT',
    final: { home: 3, away: 2 }, regulation: { home: 2, away: 2 }, decided_in: 'OVERTIME',
    probabilities: { home: 0.45, tie: 0.22, away: 0.33 }, predicted_outcome: 'HOME', actual_outcome: outcome === 'REGULATION_3WAY' ? 'TIE' : 'HOME',
    winner_correct: outcome === 'FINAL_2WAY', log_loss: 1.51, brier_three_class: 0.98, brier_two_way: 0.302,
  }],
})

vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    requests.push(path)
    if (path.startsWith('/research/wager-results')) {
      return { rows: [], research_only_sports: { NHL: { statement: 'No official NHL positions exist.', official_positions: 0 } } }
    }
    const league = new URL(path, 'http://x').searchParams.get('league')
    return {
      schema_version: 'AISP_MODEL_PERFORMANCE_V1', generated_at_utc: '2026-10-12T18:00:00Z', league,
      evidence_as_of_utc: null, conventions: {},
      models: league === 'NHL'
        ? [entry('NHL_GEN1_REGULATION_3WAY', 'REGULATION_3WAY'), entry('NHL_GEN1_FINAL_2WAY', 'FINAL_2WAY')] : [],
      requirement: league === 'NHL' ? null : `No ${league} forecast has been scored.`,
    }
  }),
}))

import { CommandPerformance } from './CommandPerformance'

afterEach(() => { cleanup(); requests.length = 0 })

describe('CommandPerformance · NHL', () => {
  it('mounts the NHL tiles, ledger and no-positions panel instead of the two-way charts', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NHL" /></MemoryRouter>)
    expect(await screen.findByTestId('nhl-perf-regulation')).toBeInTheDocument()
    expect(screen.getByTestId('nhl-ledger')).toHaveTextContent('regulation 3-way')
    expect(screen.getByTestId('nhl-ledger')).toHaveTextContent('OTT @ NJD')
    expect(await screen.findByText('No official NHL positions exist.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Performance charts' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Recent scored games' })).toBeNull()
    expect(requests).toContain('/research/model-performance?league=NHL')
    expect(requests).toContain('/research/wager-results?sport=NHL')
  })

  it('switches to the final 2-way entry without pooling it with regulation', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NHL" /></MemoryRouter>)
    await screen.findByTestId('nhl-perf-regulation')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'NHL_GEN1_FINAL_2WAY|nhl-gen1-v1' } })
    expect(await screen.findByTestId('nhl-perf-final')).toBeInTheDocument()
    expect(screen.queryByTestId('nhl-perf-regulation')).toBeNull()
    expect(screen.getByTestId('nhl-ledger')).toHaveTextContent('final 2-way')
    expect(screen.getByText('0.231')).toBeInTheDocument()
  })

  it('offers no verified-clock cohort for NHL: the control is hidden and the shown cohort is stated (MAIN 6496d6b P2)', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NHL" /></MemoryRouter>)
    await screen.findByTestId('nhl-perf-regulation')
    expect(screen.queryByRole('group', { name: 'Evidence cohort' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Verified publication clock/ })).toBeNull()
    expect(screen.getByTestId('nhl-cohort-all-only')).toHaveTextContent('All scored · 2')
    expect(screen.getByTestId('nhl-cohort-all-only')).toHaveTextContent('no verified-clock cohort is offered')
    // What renders is the ALL cohort: n=2 tiles and the full ledger, never relabelled as verified.
    expect(screen.getByTestId('nhl-perf-regulation')).toHaveTextContent('n=2')
    expect(screen.getByTestId('nhl-ledger')).toHaveTextContent('OTT @ NJD')
  })

  it('offers NHL as a sport tab beside the four official sports', async () => {
    render(<MemoryRouter><CommandPerformance initialSport="NHL" /></MemoryRouter>)
    await screen.findByTestId('nhl-perf-regulation')
    const tabs = screen.getByRole('tablist', { name: 'Sport' })
    expect(tabs).toHaveTextContent('NHL')
    expect(screen.getByRole('tab', { name: /NHL/ }).getAttribute('aria-selected')).toBe('true')
  })
})
