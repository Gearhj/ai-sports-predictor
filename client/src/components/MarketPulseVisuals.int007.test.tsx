import { describe, expect, it } from 'vitest'
import { buildComparisonRows, matrixState } from './MarketPulseVisuals'
import type { MarketPulseRow } from '../types/domain'

/**
 * INT-007: a game with no reviewed sportsbook quote must never render a 0%
 * sportsbook mark. The backend now sends `sportsbook_home_probability: null`;
 * these tests pin the frontend contract that null yields no reviewed mark, no
 * reviewed range, no ASP gap, and a "Not quoted" coverage cell — while a real
 * two-sided quote is still plotted.
 */

const baseRow = {
  sport: 'MLB', league: 'MLB', home_team: 'H', away_team: 'A', starts_at: '2026-09-05T01:40:00Z', status: 'SCHEDULED',
  aisp_home_probability: 0.56, aisp_forecast_fresh: true, sportsbook_home_probability: null, polymarket_home_probability: null,
  kalshi_home_probability: null, research_consensus_home_probability: null, consensus_eligible: false, consensus_note: 'No consensus computed',
  diff_aisp_sportsbook: null, diff_aisp_prediction: null, diff_polymarket_kalshi: null, cross_market_dispersion: null,
  providers: [], provider_coverage: [], movement_5m: null, movement_30m: null, movement_2h: null, movement_24h: null, first_mover: null,
  mapping_status: 'EXACT', semantics_status: 'SEMANTICS_REVIEW_REQUIRED', best_quality_tier: null, alert_count: 0,
} as unknown as MarketPulseRow

function row(over: Partial<MarketPulseRow>): MarketPulseRow {
  return { ...baseRow, ...over } as MarketPulseRow
}

describe('INT-007 missing sportsbook evidence', () => {
  it('produces no sportsbook mark and no reviewed range when sportsbook probability is null', () => {
    const [built] = buildComparisonRows([row({ game_id: 'g1', home_abbrev: 'LAD', away_abbrev: 'SF' })])
    expect(built.marks.some((m) => m.source === 'SPORTSBOOK')).toBe(false)
    // Only ASP is present; ASP is excluded from the reviewed-market range.
    expect(built.reviewedMin).toBeNull()
    expect(built.reviewedMax).toBeNull()
    // No mark carries the fabricated 0 value.
    expect(built.marks.every((m) => m.value !== 0)).toBe(true)
  })

  it('labels the sportsbook coverage cell as Not quoted, not reviewed', () => {
    expect(matrixState(row({ game_id: 'g1' }), 'SPORTSBOOK')).toBe('NOT_QUOTED')
  })

  it('still plots a real two-sided sportsbook quote as reviewed', () => {
    const [built] = buildComparisonRows([row({ game_id: 'g2', sportsbook_home_probability: 0.55 })])
    const book = built.marks.find((m) => m.source === 'SPORTSBOOK')
    expect(book).toBeDefined()
    expect(book?.value).toBe(0.55)
    expect(book?.reviewed).toBe(true)
    expect(matrixState(row({ game_id: 'g2', sportsbook_home_probability: 0.55 }), 'SPORTSBOOK')).toBe('REVIEWED_PREGAME')
  })
})
