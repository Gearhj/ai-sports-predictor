import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { CAPTURE_STATE, PregameCaptureMatrix, coverageReconciles } from './PregameCaptureCoverage'
import type { PregameCaptureCell, PregameCaptureCoverage } from '../types/domain'

/**
 * Pregame evidence capture matrix:
 *  - every cell is glyph + text (never colour alone) and exactly one state;
 *  - the per-provider counts reconcile to the canonical slate;
 *  - a click reveals the persisted observation (source time, cut label, book);
 *  - a future milestone renders as "Not due", never as missed.
 */

const MILESTONES = ['FIRST_DISCOVERED', 'T_MINUS_6_HOURS', 'T_MINUS_2_HOURS', 'T_MINUS_30_MINUTES', 'T_MINUS_5_MINUTES', 'FINAL_VALID_PREGAME']

function cell(milestone: string, state: string, over: Partial<PregameCaptureCell> = {}): PregameCaptureCell {
  return {
    milestone, state, reason: `${state} reason`, snapshot_id: null, observed_at: null, received_at: null, timeline_cut: null,
    midpoint: null, best_bid: null, best_ask: null, seconds_before_start: null, observed_before_start: null, late_ingestion: null, ...over,
  }
}

function build(): PregameCaptureCoverage {
  const kalshi = [
    cell('FIRST_DISCOVERED', 'CAPTURED', { observed_at: '2026-09-05T14:00:00Z', received_at: '2026-09-05T14:00:03Z', timeline_cut: 'T_MINUS_6_HOURS',
      midpoint: 0.56, best_bid: 0.55, best_ask: 0.57, seconds_before_start: 21600, observed_before_start: true, late_ingestion: false }),
    cell('T_MINUS_6_HOURS', 'CAPTURED', { observed_at: '2026-09-05T14:10:00Z', midpoint: 0.56, observed_before_start: true, seconds_before_start: 21000 }),
    cell('T_MINUS_2_HOURS', 'NOT_DUE'), cell('T_MINUS_30_MINUTES', 'NOT_DUE'), cell('T_MINUS_5_MINUTES', 'NOT_DUE'), cell('FINAL_VALID_PREGAME', 'NOT_DUE'),
  ]
  const poly = MILESTONES.map((m) => cell(m, m === 'FIRST_DISCOVERED' ? 'NO_LIQUIDITY' : 'MAPPING_UNAVAILABLE'))
  const counts: PregameCaptureCoverage['counts'] = { KALSHI: {}, POLYMARKET: {} }
  const totals: PregameCaptureCoverage['totals'] = { KALSHI: {}, POLYMARKET: {} }
  for (const m of MILESTONES) {
    counts.KALSHI[m] = { [kalshi.find((c) => c.milestone === m)!.state]: 1 }
    counts.POLYMARKET[m] = { [poly.find((c) => c.milestone === m)!.state]: 1 }
    totals.KALSHI[m] = 1
    totals.POLYMARKET[m] = 1
  }
  return {
    date: '2026-09-05', generated_at: '2026-09-05T15:00:00Z', milestones: MILESTONES, providers: ['KALSHI', 'POLYMARKET'],
    states: Object.keys(CAPTURE_STATE), canonical_games: 1,
    rows: [{ game_id: 'g1', label: 'SF @ NYM', home: 'New York Mets', away: 'San Francisco Giants', starts_at: '2026-09-05T20:10:00Z',
      game_status: 'scheduled', doubleheader_status: null,
      providers: { KALSHI: { mapping_status: 'EXACT', provider_event_id: 'KXMLBGAME-x', cells: kalshi },
        POLYMARKET: { mapping_status: null, provider_event_id: null, cells: poly } } }],
    counts, totals, capture_cadence_minutes: 5, disclosure: 'Read-only audit.',
  }
}

describe('PregameCaptureMatrix', () => {
  afterEach(cleanup)

  it('renders one glyph+text cell per game, provider and milestone, and reconciles counts', () => {
    const cov = build()
    expect(coverageReconciles(cov)).toBe(true)
    render(<PregameCaptureMatrix cov={cov} />)
    const table = screen.getByRole('table', { name: /Pregame snapshot coverage/i })
    const buttons = within(table).getAllByRole('button')
    expect(buttons).toHaveLength(2 * MILESTONES.length)
    expect(within(table).getAllByLabelText(/SF @ NYM KALSHI .*: Captured/)).toHaveLength(2)
    expect(within(table).getAllByLabelText(/SF @ NYM KALSHI .*: Not due/)).toHaveLength(4)
    expect(within(table).getAllByLabelText(/SF @ NYM POLYMARKET .*: Mapping unavailable/)).toHaveLength(5)
    expect(screen.getByTestId('capture-reconcile').textContent).toMatch(/counts reconcile/)
    // No cell is ever labelled missed for a future milestone in this fixture.
    expect(within(table).queryByLabelText(/Missed scheduler run/)).toBeNull()
  })

  it('reveals the persisted observation behind a clicked cell', () => {
    render(<PregameCaptureMatrix cov={build()} />)
    fireEvent.click(screen.getByLabelText('SF @ NYM KALSHI FIRST: Captured'))
    const detail = screen.getByRole('region', { name: /Selected capture cell/i })
    expect(detail.textContent).toMatch(/T_MINUS_6_HOURS/)
    expect(detail.textContent).toMatch(/56\.0% \/ 55\.0% \/ 57\.0%/)
    expect(detail.textContent).toMatch(/360 min/)
    expect(detail.textContent).not.toMatch(/NOT before first pitch/)
  })

  it('flags counts that do not reconcile instead of hiding the mismatch', () => {
    const cov = build()
    cov.totals.KALSHI.FIRST_DISCOVERED = 2
    expect(coverageReconciles(cov)).toBe(false)
    render(<PregameCaptureMatrix cov={cov} />)
    expect(screen.getByTestId('capture-reconcile').textContent).toMatch(/DO NOT RECONCILE/)
  })
})
