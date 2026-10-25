/**
 * The Overview must describe the snapshot it shows (a filter change refetches and
 * prints the server's findings for THAT filter), keep the forecast favourite apart
 * from the wager, and keep accuracy, probability quality and returns separate.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const requests: string[] = []
let nhlBlock: unknown = null

function game(id: string, over: Record<string, unknown> = {}) {
  return {
    evaluation_id: id, game_id: `g-${id}`, sport: 'NFL', model_key: 'NFL_M1_XGBOOST|v1', model: 'NFL M1',
    kickoff_utc: '2026-09-24T00:15:00+00:00', slate_date: '2026-09-23', home: 'GB', away: 'ATL',
    forecast_type: 'FINAL', snapshot: 'FINAL_PREGAME v1', published_utc: '2026-09-23T23:18:00+00:00',
    lead_hours: 0.95, lead_bucket: 'LE_1H', publication_clock: 'VERIFIED_INDEPENDENT_PUBLICATION',
    p_home_two_way: 0.674, p_tie: 0.02, final: { home: 14, away: 35 }, actual_outcome: 'AWAY',
    forecast_favourite: 'HOME', forecast_favourite_p: 0.674, forecast_favourite_correct: false,
    log_loss_two_way: 1.12, brier_two_way: 0.45, brier_native_stored: 0.9,
    predicted_margin: 5.64, actual_margin: -21, margin_error: 26.64, predicted_total: 43.47, actual_total: 49,
    total_error: -5.53, interval_coverage: {}, wager: null, decision: null, ...over,
  }
}

vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    requests.push(path)
    const type = new URL(path, 'http://x').searchParams.get('forecast_type') ?? 'ALL'
    const games = type === 'CURRENT'
      ? [game('c1', { forecast_type: 'CURRENT', lead_hours: 130.5, home: 'LAC', away: 'LV', total_error: 2.4 })]
      : [game('f1'), game('f2', { home: 'KC', away: 'DEN', forecast_favourite: 'AWAY', forecast_favourite_p: 0.59, forecast_favourite_correct: false,
        total_error: -2.0, margin_error: -1, wager: { side: 'AWAY', result: 'LOSS', status: 'SETTLED', stake: 5, position_id: 'p1' } })]
    return {
      schema_version: 'AISP_MODEL_PERFORMANCE_OVERVIEW_V2', generated_at_utc: '2026-09-27T19:00:00Z', evidence_as_of_utc: '2026-09-27T18:00:00Z',
      scope: { sport: 'ALL', model: 'ALL', forecast_type: type, lead_bucket: 'ALL', label: type === 'CURRENT' ? 'all sports · CURRENT forecasts · all lead times' : 'all sports · all forecast types · all lead times', n_games: games.length, n_decisions: 1 },
      narrative: {
        scope: 'x',
        findings: [{ key: 'timing:NFL', title: type === 'CURRENT' ? 'Only CURRENT forecasts' : 'NFL mixes kickoff and days-old forecasts', text: 't', experiment: 'Near-kickoff capture proposal (design, not active)', game_ids: ['g-f1'] }],
        selection_statements: ['NFL · NFL M1: selected sides won 10 of 19 (53%, 95% CI 32%–73%); n=19 is too small to support a conclusion.'],
      },
      groups: [], decision_groups: [], cfb_publication_vs_decision: null, nhl: nhlBlock, wagering: { NFL: { n: 2, settled: 1, wins: 0, losses: 1, pushes: 0, settled_stake: 5, net_profit: -5, roi: -1, open_count: 1, open_exposure: 5 } }, games,
      funnel: {
        forecast_evaluations: games.length, evaluations_with_governing_decision: 0, evaluations_funded: 1, governing_decisions: 1,
        decisions_available_before_decision: 1, decisions_excluded_not_available: 0, decisions_excluded_no_clock_or_forecast: 0,
        decisions_with_market: 0, decisions_comparable_with_market: 0, decisions_selected: 0, decisions_funded: 0,
        decision_vs_evaluation_forecast_joined: 0, decision_vs_evaluation_forecast_differs: 0, funded_positions: { total: 1, open: 1, settled: 0 },
      },
      options: { sports: ['NFL'], models: [], forecast_types: ['FINAL', 'CURRENT'], lead_buckets: [] },
      methodology: { two_way: 'Two-way only.' },
    }
  }),
}))

import { ModelPerformanceOverview, sortGames, wagerLabel } from './ModelPerformanceOverview'

afterEach(() => { cleanup(); requests.length = 0; nhlBlock = null })

const population = (n: number) => ({
  scored: n,
  regulation_3way: { n, not_modelled: 0, outcome: 'HOME / TIE / AWAY at 60:00.', brier_three_way: n ? 0.61 : null, log_loss_three_way: n ? 1.02 : null, accuracy: n ? 0.5 : null, ties_observed: 0, brier_range: '0-2' },
  final_2way: { n, outcome: 'Winner including OT/SO.', brier_two_way: n ? 0.231 : null, log_loss_two_way: n ? 0.655 : null, accuracy: n ? 0.5 : null, decided_in: { REGULATION: n, OVERTIME: 0, SHOOTOUT: 0 }, brier_range: '0-1' },
})
const nhl = (scored: number) => ({
  league: 'NHL', forecast_games: 4, finals: scored, ...population(scored), preview_fixture: population(0), synthetic_fixture_rows: 0,
  include_test_data: false, test_data_rows: 0,
  markets: { statement: 'No NHL market prices are collected.' }, wagering: { statement: 'NHL is research only; no positions.' },
  statement: 'NHL Gen1 research forecasts.',
})

describe('ModelPerformanceOverview · NHL', () => {
  it('mounts the NHL research block, saying plainly when nothing is scored yet', async () => {
    nhlBlock = nhl(0)
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    const block = await screen.findByTestId('mpo-nhl')
    expect(block).toHaveTextContent('4 NHL games with a forecast')
    expect(within(block).getByTestId('mpo-nhl-none')).toBeInTheDocument()
    expect(within(block).getByRole('link', { name: 'Detailed NHL analysis' }).getAttribute('href')).toBe('/performance?panel=detailed&sport=NHL')
  })

  it('shows regulation and final scores separately once real forecasts are scored', async () => {
    nhlBlock = nhl(2)
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    const block = await screen.findByTestId('mpo-nhl')
    expect(block).toHaveTextContent('Real forecasts · Regulation 3-way (60:00)')
    expect(block).toHaveTextContent('Real forecasts · Final 2-way (incl. OT/SO)')
    expect(within(block).queryByTestId('mpo-nhl-none')).toBeNull()
  })

  it('draws no NHL block when the server omits it', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    await screen.findByTestId('mpo-findings')
    expect(screen.queryByTestId('mpo-nhl')).toBeNull()
  })
})

describe('ModelPerformanceOverview', () => {
  it('leads with findings for the stated snapshot, each naming its experiment, and recomputes on a filter change', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    const findings = await screen.findByTestId('mpo-findings')
    expect(within(findings).getByText('NFL mixes kickoff and days-old forecasts')).toBeInTheDocument()
    expect(findings).toHaveTextContent('Near-kickoff capture proposal (design, not active)')
    fireEvent.change(screen.getByLabelText('Forecast type'), { target: { value: 'CURRENT' } })
    expect(await screen.findByText('Only CURRENT forecasts')).toBeInTheDocument()
    expect(screen.getByTestId('mpo-scope')).toHaveTextContent('CURRENT forecasts')
    expect(requests.at(-1)).toBe('/research/model-performance/overview?forecast_type=CURRENT')
  })

  it('keeps the forecast favourite apart from the wagered side', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    const table = await screen.findByTestId('mpo-table')
    const favs = within(table).getAllByTestId('mpo-fav').map((c) => c.textContent)
    const wagers = within(table).getAllByTestId('mpo-wager').map((c) => c.textContent)
    expect(favs[0]).toContain('favourite lost')
    expect(wagers).toContain('no position')
    expect(wagers).toContain('AWAY · L')
    expect(within(table).queryByText('Called')).toBeNull()
    expect(wagerLabel(null)).toBe('no position')
    expect(wagerLabel({ side: 'HOME', result: 'PENDING', status: 'OPEN', stake: 5, position_id: 'p' })).toBe('HOME · Open')
  })

  it('shows accuracy, probability quality and paper returns as separate sections, and per-sport selection wording', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    expect(await screen.findByTestId('mpo-accuracy')).toBeInTheDocument()
    expect(screen.getByTestId('mpo-probability')).toBeInTheDocument()
    expect(screen.getByTestId('mpo-returns')).toHaveTextContent('3 · Paper returns')
    expect(screen.getByTestId('mpo-selection')).toHaveTextContent('too small to support a conclusion')
  })

  it('says the view creates no position and links to where official wagers are shown', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    const note = await screen.findByTestId('mpo-authority')
    expect(note).toHaveTextContent('This evaluation view does not create positions.')
    expect(within(note).getByRole('link', { name: 'Wager Results' })).toHaveAttribute('href', '/performance?panel=wagers&sport=NFL')
  })

  it('narrows the table to a finding’s supporting games and words a negative total as an underestimate', async () => {
    render(<MemoryRouter><ModelPerformanceOverview /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Supporting games (1)' }))
    const table = screen.getByTestId('mpo-table')
    expect(within(table).getByText('ATL at GB')).toBeInTheDocument()
    expect(within(table).queryByText('DEN at KC')).toBeNull()
    expect(within(table).getByText('underestimate by 5.5')).toBeInTheDocument()
    expect(within(table).getByText('+26.6').className).toBe('mpo-tone--large')
  })

  it('sorts with missing values last in both directions', () => {
    const rows = [game('a', { log_loss_two_way: 0.5 }), game('b', { log_loss_two_way: null }), game('c', { log_loss_two_way: 0.2 })]
    expect(sortGames(rows as never, 'll', 1).map((r) => r.evaluation_id)).toEqual(['c', 'a', 'b'])
    expect(sortGames(rows as never, 'll', -1).map((r) => r.evaluation_id)).toEqual(['a', 'c', 'b'])
  })
})
