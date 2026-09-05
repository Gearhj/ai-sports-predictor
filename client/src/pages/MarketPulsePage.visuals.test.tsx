import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MarketPulsePage, evidenceCounts } from './MarketPulsePage'
import { buildComparisonRows, matrixState } from '../components/MarketPulseVisuals'
import type { MarketPulseBoard, MarketPulseGameDetail, MarketPulseRow } from '../types/domain'

/**
 * Crowd & Market Pulse information design:
 *  - disjoint game-level states that sum exactly to the canonical slate (test 11);
 *  - the probability comparison marks reviewed evidence normally, unreviewed
 *    evidence hollow/research-only, and excluded evidence not at all (test 13);
 *  - the coverage matrix states are text + glyph, not colour alone;
 *  - the movement view never connects across missing intervals.
 */

const optionSpy = vi.fn()
vi.mock('echarts-for-react/lib/core', () => ({
  default: (props: { option: unknown }) => {
    optionSpy(props.option)
    return <div data-testid="echarts-placeholder" />
  },
}))

const baseRow = {
  sport: 'MLB', league: 'MLB', home_team: 'H', away_team: 'A', starts_at: '2026-09-05T01:40:00Z', status: 'SCHEDULED',
  aisp_home_probability: 0.56, aisp_forecast_fresh: true, sportsbook_home_probability: 0.53, polymarket_home_probability: null,
  kalshi_home_probability: null, research_consensus_home_probability: null, consensus_eligible: false, consensus_note: 'No consensus computed',
  diff_aisp_sportsbook: 0.03, diff_aisp_prediction: null, diff_polymarket_kalshi: null, cross_market_dispersion: null,
  providers: [], movement_5m: null, movement_30m: null, movement_2h: null, movement_24h: null, first_mover: null,
  mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', best_quality_tier: null, alert_count: 0,
} as unknown as MarketPulseRow

function row(over: Partial<MarketPulseRow>): MarketPulseRow {
  return { ...baseRow, ...over } as MarketPulseRow
}

const rows: MarketPulseRow[] = [
  row({ game_id: 'g1', home_abbrev: 'SD', away_abbrev: 'NYY', polymarket_home_probability: 0.61, kalshi_home_probability: 0.58,
    coverage_state: 'QUOTED_REVIEWED', evidence_state: 'REVIEWED_PREGAME',
    providers: [
      { provider: 'POLYMARKET', midpoint: 0.61, best_bid: 0.6, best_ask: 0.62, quality_tier: 'ROBUST', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_MATCHED', observation_kind: 'FINAL_PREGAME', timeline_cut: 'T_MINUS_5_MINUTES', observed_at: '2026-09-05T01:35:00Z', latest_in_play: true },
      { provider: 'KALSHI', midpoint: 0.58, best_bid: 0.57, best_ask: 0.59, quality_tier: 'ROBUST', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_MATCHED', observation_kind: 'FINAL_PREGAME', observed_at: '2026-09-05T01:36:00Z' },
    ] as MarketPulseRow['providers'],
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_REVIEWED', reason: 'Reviewed semantics; final pregame observation 5 min before first pitch', contracts_listed: 8, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_MATCHED', quality_tier: 'ROBUST', last_snapshot_at: null },
      { provider: 'KALSHI', state: 'QUOTED_REVIEWED', reason: 'Reviewed semantics; final pregame observation 4 min before first pitch', contracts_listed: 2, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_MATCHED', quality_tier: 'ROBUST', last_snapshot_at: null },
    ] }),
  row({ game_id: 'g2', home_abbrev: 'SEA', away_abbrev: 'ATH', polymarket_home_probability: 0.44, kalshi_home_probability: null,
    coverage_state: 'QUOTED_UNREVIEWED', evidence_state: 'PREGAME_UNREVIEWED', unreviewed_observation: true, diff_aisp_prediction_unreviewed: 0.12,
    providers: [{ provider: 'POLYMARKET', midpoint: 0.44, quality_tier: 'SEMANTICS_UNREVIEWED', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', observation_kind: 'FINAL_PREGAME', observed_at: '2026-09-05T02:05:00Z' }] as MarketPulseRow['providers'],
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_UNREVIEWED', reason: 'Final pregame observation; semantics await template approval', contracts_listed: 8, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: 'SEMANTICS_UNREVIEWED', last_snapshot_at: null },
      { provider: 'KALSHI', state: 'MAPPED_NOT_QUOTED', reason: '2 contract(s) listed; not yet quoted', contracts_listed: 2, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: null, last_snapshot_at: null },
    ] }),
  row({ game_id: 'g3', home_abbrev: 'CLE', away_abbrev: 'DET', status: 'FINAL', polymarket_home_probability: null, kalshi_home_probability: null,
    coverage_state: 'QUOTED_STRUCTURALLY_EXCLUDED', evidence_state: 'EXCLUDED_IN_PLAY', display_note: 'QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)',
    providers: [{ provider: 'POLYMARKET', midpoint: null, quality_tier: 'SEMANTICS_UNREVIEWED', mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', observation_kind: 'IN_PLAY', in_play: true }] as MarketPulseRow['providers'],
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'QUOTED_STRUCTURALLY_EXCLUDED', reason: 'Quote persisted but structurally excluded: game is FINAL, not pregame.', contracts_listed: 8, mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', quality_tier: 'SEMANTICS_UNREVIEWED', last_snapshot_at: null },
      { provider: 'KALSHI', state: 'NO_MAPPED_MARKET', reason: 'No KALSHI market is mapped to this game', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
    ] }),
  row({ game_id: 'g4', home_abbrev: 'LAD', away_abbrev: 'WSH', mapping_status: 'UNRESOLVED', coverage_state: 'NO_MAPPED_MARKET', evidence_state: 'NO_MAPPED_MARKET',
    provider_coverage: [
      { provider: 'POLYMARKET', state: 'NO_MAPPED_MARKET', reason: 'No POLYMARKET market is mapped', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
      { provider: 'KALSHI', state: 'PROVIDER_ERROR', reason: 'KALSHI latest discovery run failed', contracts_listed: 0, mapping_status: null, semantics_status: null, quality_tier: null, last_snapshot_at: null },
    ] }),
]

const board: MarketPulseBoard = {
  data_mode: 'live', date: '2026-09-04', sport: null, sports_available: ['MLB'],
  disclosure: 'RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE', public_display_enabled: true, observer_enabled: true,
  board_state: 'ELIGIBLE_MARKETS_AVAILABLE', state_reason: '1 of 4 canonical game(s) have a reviewed, decision-eligible quote.',
  providers: [], next_discovery_at: null, semantics_review_count: 2, rows,
  summary: { consensus_available: 1 }, canonical_games: 4,
  coverage: { quoted_any: 3, semantics_review_pending: 2, decision_eligible: 1, evidence_state_sum: 4 },
  coverage_by_provider: { POLYMARKET: { QUOTED_REVIEWED: 1, QUOTED_UNREVIEWED: 1, QUOTED_STRUCTURALLY_EXCLUDED: 1, NO_MAPPED_MARKET: 1 }, KALSHI: { QUOTED_REVIEWED: 1, MAPPED_NOT_QUOTED: 1, NO_MAPPED_MARKET: 1, PROVIDER_ERROR: 1 } },
  excluded_summary: {}, jobs: [],
} as unknown as MarketPulseBoard

const detail: MarketPulseGameDetail = {
  data_mode: 'live', game_id: 'g1', home_team: 'San Diego Padres', away_team: 'New York Yankees', starts_at: '2026-09-05T01:40:00Z', status: 'SCHEDULED',
  disclosure: '', lead_lag_note: '',
  timeline: [
    { time: '2026-09-04T20:00+00:00', aisp: 56, sportsbook: 53, polymarket: 60, kalshi: null, consensus: null, polymarket_pregame: true },
    { time: '2026-09-04T22:00+00:00', aisp: null, sportsbook: 54, polymarket: null, kalshi: 58, consensus: null, kalshi_pregame: true },
    { time: '2026-09-05T01:35+00:00', aisp: null, sportsbook: 55, polymarket: 61, kalshi: 58, consensus: 59.5, polymarket_pregame: true, kalshi_pregame: true },
  ],
  timeline_markers: [{ key: 'decision_cutoff', label: 'Decision cutoff (T-15m)', time: '2026-09-05T01:25+00:00' }, { key: 'first_pitch', label: 'First pitch', time: '2026-09-05T01:40+00:00' }],
  order_books: [], movement_events: [], contracts: [], mappings: [], row: null,
} as unknown as MarketPulseGameDetail

function stub() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body } as Response)
    if (url.includes('/prediction-markets/games/')) return json(detail)
    return json(board)
  }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  optionSpy.mockClear()
})

describe('disjoint evidence states', () => {
  it('sum exactly to the canonical slate and render as the headline', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    const grid = await screen.findByRole('region', { name: 'Game evidence states' }).catch(() => screen.getByLabelText('Game evidence states'))
    const cards = within(grid as HTMLElement).getAllByRole('article')
    expect(cards).toHaveLength(6)
    const values = cards.map((c) => c.querySelector('strong')?.textContent)
    expect(values).toEqual(['1 / 4', '1 / 4', '1 / 4', '0 / 4', '1 / 4', '0 / 4'])
    const note = screen.getByRole('note', { name: 'Coverage reconciliation' })
    expect(note).toHaveTextContent('= 4 of 4 — reconciles.')
    expect(note).not.toHaveClass('pulse-coverage-reconciliation--mismatch')
    const counts = evidenceCounts(rows)
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(4)
  })

  it('keeps overlapping provider counts out of the headline, inside the audit drawer', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByLabelText('Game evidence states')
    const drawer = screen.getByText(/Audit detail — overlapping provider counts/).closest('details') as HTMLDetailsElement
    expect(drawer.open).toBe(false)
    expect(within(drawer).getByText('Semantics review pending')).toBeInTheDocument()
    expect(within(drawer).getByText('Overlapping measure · counted per provider')).toBeInTheDocument()
    expect(within(drawer).getByText('Quoted (any observation)')).toBeInTheDocument()
  })
})

describe('market probability comparison', () => {
  it('marks reviewed evidence normally, unreviewed as research-only, and excluded not at all', () => {
    const data = buildComparisonRows(rows)
    const g1 = data.find((d) => d.game_id === 'g1')!
    expect(g1.marks.map((m) => [m.source, m.reviewed])).toEqual([['ASP', true], ['SPORTSBOOK', true], ['POLYMARKET', true], ['KALSHI', true]])
    expect(g1.reviewedMin).toBeCloseTo(0.53) // sportsbook, polymarket, kalshi reviewed range
    expect(g1.reviewedMax).toBeCloseTo(0.61)
    const g2 = data.find((d) => d.game_id === 'g2')!
    expect(g2.marks.find((m) => m.source === 'POLYMARKET')!.reviewed).toBe(false)
    expect(g2.marks.find((m) => m.source === 'POLYMARKET')!.detail).toContain('RESEARCH ONLY')
    const g3 = data.find((d) => d.game_id === 'g3')!
    expect(g3.marks.map((m) => m.source)).toEqual(['ASP', 'SPORTSBOOK']) // in-play/final evidence gets no mark
    // sorted by disagreement: g2 (ASP 0.56 vs sportsbook 0.53 → 0.03; unreviewed excluded from range) vs g1 (0.08)
    expect(data[0].game_id).toBe('g1')
  })

  it('renders hollow research-only marks and a text table that does not rely on colour', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByLabelText('Game evidence states')
    const table = screen.getByRole('region', { name: 'Probability comparison table' })
    expect(within(table).getAllByText('research-only').length).toBeGreaterThan(0)
    expect(within(table).getByText('NYY @ SD')).toBeInTheDocument()
    const opts = optionSpy.mock.calls.map((c) => c[0] as { series?: Array<{ name: string; symbol?: string; itemStyle?: { color?: string; borderColor?: string }; data?: unknown[] }> })
    const comparison = opts.find((o) => o.series?.some((s) => s.name === 'Reviewed market range'))!
    const hollow = comparison.series!.find((s) => s.name === 'Polymarket · research-only')!
    expect(hollow.itemStyle?.color).toBe('transparent')
    expect((hollow.data ?? []).length).toBe(1)
    const symbols = comparison.series!.filter((s) => s.symbol).map((s) => s.symbol)
    expect(new Set(symbols).size).toBeGreaterThanOrEqual(4) // distinct shapes per source, not colour alone
  })
})

describe('provider coverage matrix', () => {
  it('derives text-labelled cell states per provider', () => {
    expect(matrixState(rows[0], 'POLYMARKET')).toBe('REVIEWED_PREGAME')
    expect(matrixState(rows[1], 'POLYMARKET')).toBe('UNREVIEWED')
    expect(matrixState(rows[1], 'KALSHI')).toBe('NOT_QUOTED')
    expect(matrixState(rows[2], 'POLYMARKET')).toBe('EXCLUDED')
    expect(matrixState(rows[3], 'KALSHI')).toBe('PROVIDER_ERROR')
    expect(matrixState(rows[3], 'POLYMARKET')).toBe('NO_MARKET')
    expect(matrixState(rows[0], 'SPORTSBOOK')).toBe('REVIEWED_PREGAME')
  })

  it('renders every canonical game as a matrix row with three provider cells', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByLabelText('Game evidence states')
    const matrix = screen.getByRole('region', { name: 'Provider coverage matrix' })
    expect(within(matrix).getAllByRole('row')).toHaveLength(5)
    expect(within(matrix).getAllByText('Excluded (in-play / final)')).toHaveLength(1)
    expect(within(matrix).getAllByText('Provider error')).toHaveLength(1)
  })
})

describe('selected-game movement view', () => {
  it('never connects across missing intervals and marks decision cutoff and first pitch', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/market-pulse?date=2026-09-04']}><MarketPulsePage /></MemoryRouter>)
    await screen.findByLabelText('Game evidence states')
    await screen.findByText(/3 observation minutes/)
    const opts = optionSpy.mock.calls.map((c) => c[0] as { series?: Array<{ name: string; connectNulls?: boolean; markLine?: { data: Array<{ name: string }> } }> })
    const movement = opts.find((o) => o.series?.some((s) => s.name === 'Sportsbook (no-vig)' && 'connectNulls' in s))!
    expect(movement.series!.every((s) => s.connectNulls === false)).toBe(true)
    const kalshi = movement.series!.find((s) => s.name === 'Kalshi')!
    expect(kalshi.markLine?.data.map((d) => d.name)).toEqual(['Decision cutoff (T-15m)', 'First pitch'])
    expect(screen.getByRole('combobox', { name: 'Select game for market movement' })).toHaveValue('g1')
  })
})
