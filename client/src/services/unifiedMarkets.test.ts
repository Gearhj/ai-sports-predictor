import { describe, expect, it } from 'vitest'
import { comparableModel, conditionalHome, mlbOnly, mlbReviewedGaps, nflEdges, pregameProviders, quoteFreshness } from './unifiedMarkets'
import type { MarketPulseRow, PredictionProviderProbability } from '../types/domain'
import type { NflMarketBlock } from '../types/nfl'

describe('Unified market comparisons', () => {
  it('keeps NFL tie probability outside a two-way moneyline comparison', () => {
    expect(conditionalHome({ home_win_probability: .45, away_win_probability: .35, tie_probability: .20 })).toBeCloseTo(.5625)
    expect(conditionalHome({ home_win_probability: .45, away_win_probability: .35, tie_probability: null })).toBeNull()
    expect(conditionalHome({ home_win_probability: 0, away_win_probability: 0, tie_probability: 1 })).toBeNull()
  })
  it('compares spread and total prices conditional on no push; stale prices have no edge', () => {
    const block: NflMarketBlock = { market_type: 'TOTAL', fresh: true, comparison_available: true, reason: null, model: { a: .45, b: .35, push: .2 }, market_no_vig: { a: .5, b: .5 } }
    expect(comparableModel(block)?.a).toBeCloseTo(.5625)
    expect(nflEdges([block], 'TOTAL')[0]).toBeCloseTo(.0625)
    expect(nflEdges([block], 'MONEYLINE')).toEqual([])
    expect(nflEdges([{ ...block, fresh: false }], 'ALL')).toEqual([])
    expect(block.model).toEqual({ a: .45, b: .35, push: .2 })
  })
  it('keeps missing/future/stale distinct from a recent stored observation', () => {
    const now = '2026-09-09T16:00:00Z'
    expect(quoteFreshness(null, now)).toBe('MISSING')
    expect(quoteFreshness('2026-09-09T17:00:00Z', now)).toBe('STALE')
    expect(quoteFreshness('2026-09-09T11:59:00Z', now)).toBe('STALE')
    expect(quoteFreshness('2026-09-09T12:00:00Z', now)).toBe('FRESH')
  })
  it('does not mix sports or admit in-play/unmapped crowd observations', () => {
    expect(mlbOnly([{ sport: 'MLB', league: 'MLB' }, { sport: 'NFL', league: 'NFL' }] as MarketPulseRow[])).toHaveLength(1)
    const base = { provider: 'KALSHI', midpoint: .6, mapping_status: 'EXACT', observation_kind: 'PREGAME', in_play: false } as PredictionProviderProbability
    expect(pregameProviders([base, { ...base, in_play: true }, { ...base, mapping_status: 'REVIEW_REQUIRED' }, { ...base, observation_kind: 'UNKNOWN' }])).toEqual([base])
    const row = { sport: 'MLB', aisp_home_probability: .65, aisp_forecast_fresh: true, providers: [{ ...base, observed_at: '2026-09-09T15:00:00Z', semantics_status: 'SEMANTICS_MATCHED', decision_eligible: true }] } as MarketPulseRow
    expect(mlbReviewedGaps(row, '2026-09-09T16:00:00Z')[0]).toBeCloseTo(.05)
    expect(mlbReviewedGaps(row, '2026-09-10T16:00:00Z')).toEqual([])
    expect(mlbReviewedGaps({ ...row, aisp_forecast_fresh: false }, '2026-09-09T16:00:00Z')).toEqual([])
  })
})
