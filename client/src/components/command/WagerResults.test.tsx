import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requests: string[] = []

function band(key: string, label: string, over: Record<string, unknown> = {}) {
  return {
    key, label, lower: null, upper: null, n: 0, settled: 0, wins: 0, losses: 0, pushes: 0, voids: 0, decided: 0,
    settled_stake: 0, net_profit: 0, roi: null, roi_ci95: null, estimated_profit: null, estimated_roi: null, estimated_n: 0,
    avg_recorded_return: null, win_rate: null, win_rate_ci95: null, avg_raw_probability: null, probability_n: 0,
    avg_adjusted_probability: null, adjusted_n: 0, comparison_n: 0, avg_comparison_probability: null,
    expected_wins: null, actual_wins_with_probability: 0, comparison_win_rate: null, comparison_win_rate_ci95: null,
    tie_derived_n: 0, binding_excluded: 0,
    open_count: 0, open_exposure: 0, open_estimated_profit: 0, ...over,
  }
}

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    position_id: id, decision_id: `d-${id}`, decision_phase: 'FINAL_PREGAME', game_id: `g-${id}`, matchup: 'TB @ NYY',
    starts_at: '2026-09-20T23:05:00Z', slate_date: '2026-09-20', sport: 'MLB', engine: 'MLB_V18_WHOLE_GAME',
    experiment_id: 'e6', experiment_name: 'ASP MLB V18', experiment_version: 6,
    recommendation_policy_version: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', staking_policy_version: 'K',
    selection: 'TB ML', selected_side: 'AWAY', decision_at: '2026-09-20T22:20:00Z', placed_at: '2026-09-20T22:21:00Z',
    sportsbook: 'LowVig.ag', entry_american: 112, entry_decimal: 2.12, raw_probability: 0.517, adjusted_probability: 0.497,
    tie_probability: null, no_vig_probability: 0.461, comparison_probability: 0.517, comparison_basis: 'RAW_NO_TIE_OUTCOME',
    binding_status: 'OK', probability_valid: true,
    adjustments: { uncertainty: 0, starter: 0.02, lineup: 0, uncertainty_status: 'APPLIED' }, policy_adjusts: true,
    recorded_return: 0.038, return_source: 'evidence.adjusted_expected_value', return_band: '0to5', probability_band: '50to60',
    stake: 2.44, estimated_profit: 0.0927, status: 'SETTLED', result: 'WIN', settled: true, net_profit: 2.73,
    settled_at: '2026-09-21T02:40:00Z', flags: [], ...over,
  }
}

vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    requests.push(path)
    const q = new URL(path, 'http://x').searchParams
    const sport = q.get('sport') ?? 'ALL'
    const engine = q.get('engine')
    return {
      schema_version: 'AISP_WAGER_RESULTS_V1', generated_at_utc: '2026-09-27T02:00:00Z', data_as_of_utc: '2026-09-27T01:30:00Z',
      population: { key: 'OFFICIAL_FUNDED_POSITIONS', label: 'Official funded positions', note: 'One row per position.',
        exclusions: ['House Divided copies (competition_wagers) are never read.'], positions_outside_funded_eras: 0 },
      scope: { sport, engine, era: null, date_from: null, date_to: null,
        label: sport === 'ALL' ? 'All official funded positions — all sports · all engines and policy eras'
          : `Official funded positions — MLB${engine ? ' · MLB V18 engine' : ' · all engines and policy eras'}` },
      date_basis: { field: 'slate_date', label: 'Slate date (ET) of the funding decision', note: 'Uses the slate date.' },
      methods: { roi: 'ROI.', roi_ci95: 'Bootstrap.', win_rate_ci95: 'Wilson.', expected_wins: 'Sum p.', estimated_profit: 'Est.', exploratory: 'Exploratory slices.', return_vs_confidence: 'x' },
      options: { sports: [], engines: [{ sport: 'MLB', engine: 'MLB_V18_WHOLE_GAME', label: 'MLB · V18 engine', n: 3 }], eras: [] },
      summary: band('all', 'ALL', { n: 3, settled: 2, wins: 1, losses: 1, decided: 2, settled_stake: 4.88, net_profit: 0.29, roi: 0.059,
        roi_ci95: [-0.9, 1.1], estimated_profit: 0.52, estimated_roi: 0.107, estimated_n: 2, open_count: 1, open_exposure: 2.44, open_estimated_profit: 0.31 }),
      return_bands: [band('lt0', '<0%'), band('0to5', '0–<5%', { settled: 1, wins: 1, decided: 1 }), band('5to10', '5–<10%'),
        band('10to20', '10–<20%', { settled: 1, losses: 1, decided: 1, open_count: 1 }), band('ge20', '≥20%'), band('missing', 'Missing')],
      probability_bands: [band('lt40', '<40%'), band('40to50', '40–<50%', { settled: 10, wins: 4, losses: 4, pushes: 2, decided: 8, comparison_n: 8,
        avg_raw_probability: 0.4, avg_comparison_probability: 0.5, expected_wins: 4, actual_wins_with_probability: 4, comparison_win_rate: 0.5,
        comparison_win_rate_ci95: [0.22, 0.78], tie_derived_n: 8 }), band('50to60', '50–<60%', { settled: 2, wins: 1, losses: 1, decided: 2 }),
        band('60to70', '60–<70%', { open_count: 1 }), band('ge70', '≥70%'), band('missing', 'Missing')],
      rows: [row('p1'), row('p2', { return_band: '10to20', selection: 'AZ ML', result: 'LOSS', net_profit: -2.44 }),
        row('p3', { return_band: '10to20', probability_band: '60to70', selection: 'ATH ML', status: 'OPEN', settled: false, result: null, net_profit: null })],
    }
  }),
}))

import { WagerResults, wagerCsv, type WagerRow } from './WagerResults'

afterEach(() => { cleanup(); requests.length = 0 })

describe('WagerResults', () => {
  it('defaults to the page model scope and states it, then widens only on an explicit change', async () => {
    render(<MemoryRouter><WagerResults pageSport="MLB" pageEngine="MLB_V18_WHOLE_GAME" /></MemoryRouter>)
    expect(await screen.findByText('Official funded positions — MLB · MLB V18 engine')).toBeInTheDocument()
    expect(requests[0]).toContain('sport=MLB')
    expect(requests[0]).toContain('engine=MLB_V18_WHOLE_GAME')
    expect(screen.getByText(/not the scored forecasts/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'All sports' }))
    expect(await screen.findByText(/All official funded positions — all sports/)).toBeInTheDocument()
    expect(requests.at(-1)).toContain('sport=ALL')
    expect(requests.at(-1)).not.toContain('engine=')
  })

  it('shows estimated-at-entry and open exposure separately from settled results', async () => {
    render(<MemoryRouter><WagerResults pageSport="MLB" /></MemoryRouter>)
    const summary = await screen.findByTestId('wager-summary')
    expect(within(summary).getByText('Estimated at entry')).toBeInTheDocument()
    expect(within(summary).queryByText(/promised/i)).not.toBeInTheDocument()
    expect(within(summary).getByText(/\$2\.44 open exposure/)).toBeInTheDocument()
    expect(within(summary).getByText(/slate date \(et\)/i)).toBeInTheDocument()
  })

  it('filters the wager table when a band is selected', async () => {
    render(<MemoryRouter><WagerResults pageSport="MLB" /></MemoryRouter>)
    expect(await screen.findByText('Wagers · 3')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: '10–<20%' }).find((b) => b.tagName === 'BUTTON') as HTMLElement)
    expect(screen.getByText('Wagers · 2 in return band 10–<20%')).toBeInTheDocument()
    expect(screen.queryByText('TB ML')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Export CSV · 2/ })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Show all bands' }))
    expect(screen.getByText('Wagers · 3')).toBeInTheDocument()
  })

  it('labels the NFL tie-adjusted comparison and keeps the recorded p beside it', async () => {
    render(<MemoryRouter><WagerResults pageSport="MLB" /></MemoryRouter>)
    await screen.findByTestId('wager-summary')
    expect(screen.getByRole('columnheader', { name: 'Avg selected-side p (non-tie, derived)' })).toBeInTheDocument()
    const row = screen.getByRole('button', { name: '40–<50%' }).closest('tr') as HTMLElement
    expect(within(row).getByText('40.0%')).toBeInTheDocument()
    // Derived non-tie p and observed win rate agree on the same denominator (calibrated fixture).
    expect(within(row).getAllByText('50.0%')).toHaveLength(2)
    expect(within(row).getByText('4.0')).toBeInTheDocument()
    expect(within(row).getByText('NFL derived, n=8')).toBeInTheDocument()
  })

  it('exports one CSV line per filtered row with quoting', () => {
    const csv = wagerCsv([row('p1', { matchup: 'A, B @ "C"' }) as unknown as WagerRow])
    const lines = csv.split('\n')
    expect(lines).toHaveLength(2)
    expect(lines[0].startsWith('position_id,decision_id,slate_date_et')).toBe(true)
    expect(lines[1]).toContain('"A, B @ ""C"""')
    expect(lines[1]).toContain('AWAY')
  })
})
