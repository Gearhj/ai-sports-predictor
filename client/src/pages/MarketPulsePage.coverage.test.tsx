import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MarketPulsePage } from './MarketPulsePage'
import type { MarketPulseBoard, MarketPulseRow } from '../types/domain'

/**
 * Canonical-slate coverage: every scheduled game is a row, provider columns
 * carry an honest state instead of a blank, and an unreviewed quote is shown
 * as a research observation — never as the claimed AISP–market gap.
 */

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

const baseRow: MarketPulseRow = {
  game_id: 'g', sport: 'MLB', league: 'MLB', home_team: 'H', away_team: 'A', home_abbrev: 'H', away_abbrev: 'A',
  starts_at: '2026-09-04T23:10:00Z', status: 'SCHEDULED', aisp_home_probability: 0.5677, aisp_forecast_fresh: true,
  sportsbook_home_probability: 0.55, polymarket_home_probability: null, kalshi_home_probability: null,
  research_consensus_home_probability: null, consensus_eligible: false, consensus_note: 'No consensus computed',
  diff_aisp_sportsbook: 0.0177, diff_aisp_prediction: null, diff_polymarket_kalshi: null, cross_market_dispersion: null,
  providers: [], movement_5m: null, movement_30m: null, movement_2h: null, movement_24h: null, first_mover: null,
  mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', best_quality_tier: null, alert_count: 0,
}

const rows: MarketPulseRow[] = [
  {
    ...baseRow, game_id: 'g1', home_abbrev: 'CLE', away_abbrev: 'DET',
    coverage_state: 'MAPPED_NOT_QUOTED', display_note: 'LISTED — NOT YET QUOTED',
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'MAPPED_NOT_QUOTED', reason: '15 contract(s) listed; not yet quoted by the last refresh', contracts_listed: 15, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: null, last_snapshot_at: null },
      { provider: 'KALSHI', state: 'NO_MAPPED_MARKET', reason: 'No KALSHI market is mapped to this game', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
    ],
  },
  {
    ...baseRow, game_id: 'g2', home_abbrev: 'LAD', away_abbrev: 'WSH', polymarket_home_probability: 0.725,
    coverage_state: 'QUOTED_UNREVIEWED', unreviewed_observation: true, diff_aisp_prediction: null, diff_aisp_prediction_unreviewed: -0.1573,
    display_note: 'UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE', best_quality_tier: 'SEMANTICS_UNREVIEWED',
    providers: [{ provider: 'POLYMARKET', midpoint: 0.725, quality_tier: 'SEMANTICS_UNREVIEWED', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED' }],
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_UNREVIEWED', reason: 'Quote persisted but market semantics are not yet reviewed', contracts_listed: 8, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: 'SEMANTICS_UNREVIEWED', last_snapshot_at: '2026-09-04T12:26:00Z' },
      { provider: 'KALSHI', state: 'NO_MAPPED_MARKET', reason: 'No KALSHI market is mapped to this game', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
    ],
  },
  {
    ...baseRow, game_id: 'g3', home_abbrev: 'SEA', away_abbrev: 'ATH', mapping_status: 'UNRESOLVED', semantics_status: 'SEMANTICS_UNKNOWN',
    coverage_state: 'NO_MAPPED_MARKET', display_note: 'NO PROVIDER MARKET MAPPED',
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'NO_MAPPED_MARKET', reason: 'No POLYMARKET market is mapped', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
      { provider: 'KALSHI', state: 'NO_MAPPED_MARKET', reason: 'No KALSHI market is mapped', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
    ],
  },
]

const board: MarketPulseBoard = {
  data_mode: 'live', date: '2026-09-04', sport: null, sports_available: ['MLB'],
  disclosure: 'RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE',
  public_display_enabled: false, observer_enabled: true,
  board_state: 'RESEARCH_OBSERVATIONS_UNREVIEWED',
  state_reason: '1 of 3 canonical game(s) have persisted quotes whose market semantics are not yet reviewed.',
  providers: [], next_discovery_at: null, semantics_review_count: 3, rows,
  summary: { games: 3, canonical_games: 3, mapped: 2, quoted: 1, decision_eligible: 0, unreviewed_observations: 1, review_required: 0, semantics_review_pending: 2, consensus_available: 0, biggest_disagreement: null, biggest_unreviewed_gap: 0.1573 },
  canonical_games: 3,
  coverage: { QUOTED_REVIEWED: 0, QUOTED_UNREVIEWED: 1, MAPPED_NOT_QUOTED: 1, MAPPING_REVIEW_REQUIRED: 0, MAPPING_REJECTED: 0, NO_MAPPED_MARKET: 1, PROVIDER_DISABLED: 0, NO_PROVIDER_ENABLED: 0, canonical_games: 3, quoted_any: 1, decision_eligible: 0, consensus_available: 0, semantics_review_pending: 2 },
  excluded_summary: {}, jobs: [],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('MarketPulsePage canonical coverage', () => {
  it('renders every canonical game as a row, even without a quote, with honest provider states', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(board)))
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByText('Cross-market probability board')
    // The unreviewed observation state renders the BOARD, not the health-only panel.
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument()
    expect(screen.getByText('Research observations — semantics unreviewed')).toBeInTheDocument()
    const table = screen.getByRole('region', { name: /Cross-market probability comparison/i })
    expect(within(table).getAllByRole('row')).toHaveLength(4) // header + 3 canonical games
    expect(within(table).getByText('DET @ CLE')).toBeInTheDocument()
    expect(within(table).getByText('ATH @ SEA')).toBeInTheDocument()
    // Blank provider columns carry a state, never a bare dash.
    expect(within(table).getByText('Listed · not quoted')).toBeInTheDocument()
    expect(within(table).getAllByText('No market mapped').length).toBeGreaterThanOrEqual(3)
    expect(within(table).getByText('LISTED — NOT YET QUOTED')).toBeInTheDocument()
    expect(within(table).getByText('NO PROVIDER MARKET MAPPED')).toBeInTheDocument()
  })

  it('shows the coverage strip with canonical denominators', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(board)))
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByText('Cross-market probability board')
    const grid = screen.getByLabelText('Game evidence states')
    const cards = within(grid).getAllByRole('article')
    expect(cards.map((c) => c.querySelector('strong')?.textContent)).toEqual(['0 / 3', '1 / 3', '0 / 3', '1 / 3', '1 / 3', '0 / 3'])
    expect(screen.getByRole('note', { name: 'Coverage reconciliation' })).toHaveTextContent('= 3 of 3 — reconciles.')
  })

  it('labels an unreviewed quote as a research observation and does not claim it as the AISP–market gap', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(board)))
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByText('Cross-market probability board')
    const table = screen.getByRole('region', { name: /Cross-market probability comparison/i })
    const ladRow = within(table).getByText('WSH @ LAD').closest('tr') as HTMLElement
    expect(within(ladRow).getByText('72.5%')).toBeInTheDocument()
    expect(within(ladRow).getAllByText('unreviewed').length).toBe(2) // provider cell + gap cell
    expect(within(ladRow).getByText('-15.7 pts')).toBeInTheDocument()
    expect(within(ladRow).getByText('UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE')).toBeInTheDocument()
    // Headline gap card: no reviewed gap is claimed; the unreviewed observation is disclosed as such.
    const gaps = screen.getByRole('region', { name: /Gaps and review/i })
    expect(within(gaps).getByText('Biggest AISP–market gap (reviewed)')).toBeInTheDocument()
    expect(within(gaps).getAllByText('—').length).toBeGreaterThanOrEqual(1)
    expect(within(gaps).getByText(/Unreviewed observation: -15\.7 pts \(WSH @ LAD\) — not decision eligible/)).toBeInTheDocument()
  })

  it('still shows the health-only panel when the date has no canonical game', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...board, rows: [], canonical_games: 0, data_mode: 'empty', board_state: 'PROVIDERS_HEALTHY_ZERO_ELIGIBLE', state_reason: 'none of the 0 canonical game(s)' })))
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    expect(await screen.findByRole('button', { name: /Retry/i })).toBeInTheDocument()
    expect(screen.queryByText('Cross-market probability board')).not.toBeInTheDocument()
  })
})
