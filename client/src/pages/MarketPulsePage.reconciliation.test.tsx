import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MarketPulsePage } from './MarketPulsePage'
import type { MarketPulseBoard, MarketPulseRow } from '../types/domain'

/**
 * Coverage-count reconciliation: every canonical game is counted in exactly
 * one exclusive state, the strip and the observer banner share that
 * authority, and a quote that cannot be a pregame observation (final game,
 * in-play capture) is labelled as structurally excluded — never as an
 * unreviewed observation and never silently dropped.
 */

const baseRow: MarketPulseRow = {
  game_id: 'g', sport: 'MLB', league: 'MLB', home_team: 'H', away_team: 'A', home_abbrev: 'H', away_abbrev: 'A',
  starts_at: '2026-09-04T23:10:00Z', status: 'SCHEDULED', aisp_home_probability: 0.5677, aisp_forecast_fresh: true,
  sportsbook_home_probability: 0.55, polymarket_home_probability: null, kalshi_home_probability: null,
  research_consensus_home_probability: null, consensus_eligible: false, consensus_note: 'No consensus computed',
  diff_aisp_sportsbook: 0.0177, diff_aisp_prediction: null, diff_polymarket_kalshi: null, cross_market_dispersion: null,
  providers: [], movement_5m: null, movement_30m: null, movement_2h: null, movement_24h: null, first_mover: null,
  mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', best_quality_tier: null, alert_count: 0,
} as MarketPulseRow

const kalshiNone = { provider: 'KALSHI', state: 'NO_MAPPED_MARKET', reason: 'No KALSHI market is mapped to this game', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null } as NonNullable<MarketPulseRow['provider_coverage']>[number]

const rows: MarketPulseRow[] = [
  {
    ...baseRow, game_id: 'g1', home_abbrev: 'LAD', away_abbrev: 'WSH', polymarket_home_probability: 0.725,
    coverage_state: 'QUOTED_UNREVIEWED', unreviewed_observation: true, diff_aisp_prediction_unreviewed: -0.1573,
    display_note: 'UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE', best_quality_tier: 'SEMANTICS_UNREVIEWED',
    providers: [{ provider: 'POLYMARKET', midpoint: 0.725, quality_tier: 'SEMANTICS_UNREVIEWED', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED' } as MarketPulseRow['providers'][number]],
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_UNREVIEWED', reason: 'Quote persisted but market semantics are not yet reviewed', contracts_listed: 8, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: 'SEMANTICS_UNREVIEWED', last_snapshot_at: '2026-09-04T20:00:00Z' },
      kalshiNone,
    ],
  },
  {
    ...baseRow, game_id: 'g2', home_abbrev: 'CLE', away_abbrev: 'DET', status: 'FINAL',
    coverage_state: 'QUOTED_STRUCTURALLY_EXCLUDED', unreviewed_observation: false, diff_aisp_prediction_unreviewed: null,
    display_note: 'QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)',
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_STRUCTURALLY_EXCLUDED', reason: 'Quote persisted but structurally excluded: game is FINAL, not pregame.', contracts_listed: 15, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: 'SEMANTICS_UNREVIEWED', last_snapshot_at: '2026-09-04T20:00:00Z' },
      kalshiNone,
    ],
  },
  {
    ...baseRow, game_id: 'g3', home_abbrev: 'SEA', away_abbrev: 'ATH', mapping_status: 'UNRESOLVED', semantics_status: 'SEMANTICS_UNKNOWN',
    coverage_state: 'NO_MAPPED_MARKET', display_note: 'NO PROVIDER MARKET MAPPED',
    provider_coverage: [{ ...kalshiNone, provider: 'POLYMARKET', reason: 'No POLYMARKET market is mapped' }, kalshiNone],
  },
]

const board: MarketPulseBoard = {
  data_mode: 'live', date: '2026-09-04', sport: null, sports_available: ['MLB'],
  disclosure: 'RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE',
  public_display_enabled: false, observer_enabled: true,
  board_state: 'RESEARCH_OBSERVATIONS_UNREVIEWED',
  state_reason: '1 of 3 canonical game(s) have persisted quotes whose market semantics are not yet reviewed. They are shown as unreviewed research observations; none is decision-eligible and no AISP–market gap is claimed from them. 1 further quoted game(s) are structurally excluded (final, in-play or no usable price) and are not observations.',
  providers: [], next_discovery_at: null, semantics_review_count: 2, rows,
  summary: { games: 3, canonical_games: 3, mapped: 2, quoted: 2, decision_eligible: 0, unreviewed_observations: 1, review_required: 0, semantics_review_pending: 2, consensus_available: 0, biggest_disagreement: null, biggest_unreviewed_gap: 0.1573 },
  canonical_games: 3,
  coverage: { QUOTED_REVIEWED: 0, QUOTED_UNREVIEWED: 1, QUOTED_STRUCTURALLY_EXCLUDED: 1, MAPPED_NOT_QUOTED: 0, MAPPING_REVIEW_REQUIRED: 0, MAPPING_REJECTED: 0, NO_MAPPED_MARKET: 1, PROVIDER_DISABLED: 0, NO_PROVIDER_ENABLED: 0, canonical_games: 3, exclusive_sum: 3, quoted_any: 2, unreviewed_observations: 1, decision_eligible: 0, consensus_available: 0, semantics_review_pending: 2 },
  excluded_summary: {}, jobs: [],
} as MarketPulseBoard

function stub(payload: MarketPulseBoard) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => payload } as Response)))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('MarketPulsePage coverage reconciliation', () => {
  it('counts every canonical game exactly once and says so', async () => {
    stub(board)
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    const note = await screen.findByRole('note', { name: 'Coverage reconciliation' })
    expect(note).toHaveTextContent('0 reviewed pregame quote · 1 pregame quote · awaiting review · 1 in-play / final · excluded · 0 listed · not quoted · 1 no mapped market · 0 provider unavailable / error = 3 of 3 — reconciles.')
    expect(note).toHaveTextContent('Overlapping provider-level counts')
    expect(note).not.toHaveClass('pulse-coverage-reconciliation--mismatch')
  })

  it('agrees with the observer banner on the same denominator', async () => {
    stub(board)
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    const banner = await screen.findByRole('status', { name: 'Observer state' })
    expect(banner).toHaveTextContent('1 of 3 canonical game(s)')
    expect(banner).toHaveTextContent('1 further quoted game(s) are structurally excluded')
    const grid = screen.getByLabelText('Game evidence states')
    const cards = within(grid).getAllByRole('article')
    expect(cards.map((c) => c.querySelector('strong')?.textContent)).toEqual(['0 / 3', '1 / 3', '1 / 3', '0 / 3', '1 / 3', '0 / 3'])
    const drawer = screen.getByText(/Audit detail — overlapping provider counts/).closest('details') as HTMLElement
    expect(within(drawer).getByText('Quoted (any observation)')).toBeInTheDocument()
    expect(within(drawer).getByText('2 / 3')).toBeInTheDocument() // quoted-any is an overlapping audit measure now
  })

  it('labels the structurally excluded game in the board without a price and never as an observation', async () => {
    stub(board)
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findAllByText('DET @ CLE')
    const table = screen.getByRole('region', { name: 'Cross-market probability comparison' }).querySelector('table') as HTMLTableElement
    const row = within(table).getByText('DET @ CLE').closest('tr') as HTMLElement
    expect(within(row).getByText('Quoted · excluded (not pregame)')).toBeInTheDocument()
    expect(within(row).queryByText('unreviewed')).not.toBeInTheDocument()
    expect(within(table).getByText('QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)')).toBeInTheDocument()
    expect(within(table).getAllByRole('row')).toHaveLength(4) // header + 3 canonical games — nothing dropped
  })

  it('flags a non-reconciling decomposition instead of hiding it', async () => {
    stub({ ...board, canonical_games: 4 })
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    const note = await screen.findByRole('note', { name: 'Coverage reconciliation' })
    expect(note).toHaveTextContent('= 3 of 4 — DOES NOT RECONCILE.')
    expect(note).toHaveClass('pulse-coverage-reconciliation--mismatch')
  })
})
