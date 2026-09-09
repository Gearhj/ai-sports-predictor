import type { MarketPulseRow, PredictionProviderProbability } from '../types/domain'
import type { NflEngine, NflMarketBlock } from '../types/nfl'

export type MarketFamily = 'ALL' | 'MONEYLINE' | 'SPREAD' | 'TOTAL'
export type Freshness = 'FRESH' | 'STALE' | 'MISSING'
export const marketLabels = { MLB: { MONEYLINE: 'Moneyline', SPREAD: 'Run line', TOTAL: 'Total runs' }, NFL: { MONEYLINE: 'Moneyline', SPREAD: 'Point spread', TOTAL: 'Total points' } } as const

/** Display classification only; never grants decision eligibility. Future times are not fresh. */
export function quoteFreshness(observed: string | null | undefined, now: string, windowMinutes = 240): Freshness {
  if (!observed) return 'MISSING'
  const age = Date.parse(now) - Date.parse(observed)
  return Number.isFinite(age) && age >= 0 && age <= windowMinutes * 60_000 ? 'FRESH' : 'STALE'
}

/** Two-way book prices refund a tied NFL game. Never compare raw three-way p(home) to them. */
export function conditionalHome(engine: Pick<NflEngine, 'home_win_probability' | 'away_win_probability' | 'tie_probability'>): number | null {
  if (engine.tie_probability == null) return null
  const total = engine.home_win_probability + engine.away_win_probability
  return total > 0 ? engine.home_win_probability / total : null
}

export function comparableModel(block: NflMarketBlock): { a: number | null; b: number | null } | null {
  if (!block.model) return null
  if (block.market_type === 'MONEYLINE') return block.model
  const decisive = 1 - block.model.push
  return { a: decisive > 0 && block.model.a != null ? block.model.a / decisive : null,
    b: decisive > 0 && block.model.b != null ? block.model.b / decisive : null }
}

export function nflEdges(blocks: NflMarketBlock[], family: MarketFamily): number[] {
  return blocks.filter((b) => b.comparison_available && b.fresh && (family === 'ALL' || b.market_type === family))
    .flatMap((b) => {
      const model = comparableModel(b)
      return model && b.market_no_vig ? (['a', 'b'] as const).flatMap((side) => model[side] == null ? [] : [model[side]! - b.market_no_vig![side]]) : []
    })
}

export function pregameProviders(providers: PredictionProviderProbability[]): PredictionProviderProbability[] {
  return providers.filter((p) => p.midpoint != null && !p.in_play && ['PREGAME', 'FINAL_PREGAME'].includes(p.observation_kind ?? '')
    && ['EXACT', 'MANUAL_APPROVED'].includes(p.mapping_status))
}

/** Defensive selection: even an older unfiltered API cannot leak the other sport into a board. */
export function mlbOnly(rows: MarketPulseRow[]): MarketPulseRow[] {
  return rows.filter((row) => row.sport === 'MLB' && row.league === 'MLB')
}

export function mlbReviewedGaps(row: MarketPulseRow, now: string): number[] {
  if (row.sport !== 'MLB' || !row.aisp_forecast_fresh || row.aisp_home_probability == null) return []
  return pregameProviders(row.providers).filter((p) => p.semantics_status === 'SEMANTICS_MATCHED' && p.decision_eligible
    && quoteFreshness(p.observed_at, now) === 'FRESH').map((p) => row.aisp_home_probability! - p.midpoint!)
}
