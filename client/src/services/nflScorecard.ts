/**
 * The NFL model-performance scorecard (`/research/nfl-scorecard`).
 *
 * Model scoring only: decision-policy results, the virtual bankroll and My
 * Card are separate evaluations and never appear in this payload.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { expectSchema } from './commandCenter'
import { STALE_TIME, cacheKey } from './queryCache'

export interface Revision {
  forecast_id: string
  snapshot_type: string
  snapshot_version: number | null
  engine_id?: string
  engine_version?: string
  model_id?: string
  model_revision?: string
  model_artifact_sha256: string | null
  package_sha256: string | null
  created_utc: string | null
  published_utc: string | null
  source_cutoff_utc: string | null
  kickoff_utc: string | null
  horizon: { snapshot_type: string; hours_before_kickoff: number | null }
  population_sha256: string | null
  population_verification: { verified: boolean; code?: string; detail?: string } | null
  reason?: string
  controlling_reason?: string
}

export interface LineScore {
  market: 'SPREAD' | 'TOTAL'
  line: number | null
  book: string | null
  observed_utc: string | null
  scored: boolean
  reason?: string
  scored_side?: string
  line_kind?: string
  probabilities?: { win: number; loss: number; push: number }
  actual_state?: 'WIN' | 'LOSS' | 'PUSH'
  log_loss?: number
  brier_score?: number
}

export interface ScoredNflGame {
  evaluation_id: string
  game_id: string | null
  week: number | null
  nflverse_id: string | null
  matchup: string
  home: { abbreviation: string; name: string } | null
  away: { abbreviation: string; name: string } | null
  kickoff_utc: string | null
  probabilities: { home: number | null; away: number | null; tie: number | null }
  predicted: { home: number | null; away: number | null; margin: number | null; total: number | null; outcome: string | null }
  final: { home: number; away: number; outcome: string; margin: number; total: number; overtime: boolean | null }
  errors: { home: number | null; away: number | null; margin: number | null; total: number | null }
  scores: { log_loss: number | null; brier_score: number | null; winner_correct: boolean | null; probability_of_actual: number | null }
  mass_reconciliation: { reconciled: boolean; sum: number | null; residual: number | null }
  lines: LineScore[]
  market_reference: null | { book: string | null; no_vig_home: number | null; observed_utc: string | null }
  paper_view: null | { forecast_id: string; same_revision: boolean; scored: boolean; log_loss: number | null; brier_score: number | null; supplement_sha256?: string; reason?: string }
  audit: {
    forecast_revision: Revision | null
    population_sha256: string | null
    kickoff_utc: string | null
    final_result: null | {
      result_identity: string; source: string; tier: number; overtime: boolean | null; label?: string
      revisions: { source: string; result_identity: string; score: string; available_utc: string | null; current: boolean }[]
    }
    scoring_receipt: {
      evaluation_id: string; scoring_version: string; evaluated_utc: string | null
      result_identity: string; forecast_identity: string
      replaces: { evaluation_id: string; reason: string; score: string; result_identity: string }[]
    }
    settlement_records: { position_id: string; status: string; result: string | null; settlement_id: string | null; settled_utc: string | null }[]
    publication_clock: string | null
  }
}

export interface NflCohort {
  model_id: string
  model_revision: string
  model_label: string
  authority: string
  settled_games: number
  dates: string[]
  weeks: number[]
  review: {
    settled_eligible_games: number
    checkpoints: { games: number; reached: boolean; remaining: number }[]
    next_checkpoint: number | null
    note: string
  }
  summary: {
    games: number; correct_winners: number; winner_accuracy: number | null; ties: number
    log_loss: number | null; brier_score: number | null
    home_score_mae: number | null; away_score_mae: number | null; pooled_score_mae: number | null
    margin_mae: number | null; total_mae: number | null
    mean_margin_error: number | null; mean_total_error: number | null
    probability_of_actual: number | null
  }
  explanations: Record<string, string>
  lines: Record<string, { scored: number; pushes: number; integer_lines: number; mean_log_loss: number | null; mean_brier_score: number | null; basis: string }>
  calibration: { available: boolean; pairs: number; minimum: number; reason?: string }
  paper_view: { games: number; same_revision_as_research: number; log_loss: number | null; brier_score: number | null; note: string }
  games: ScoredNflGame[]
}

export interface NflScorecard {
  schema_version: string
  generated_at_utc: string
  coverage: {
    eligible_games_started: number
    settled_and_scored: number
    open_awaiting_final: number
    unscored: number
    unscored_reasons: Record<string, number>
    upcoming_with_forecast: number
  }
  cohorts: NflCohort[]
  production_status: { status: string; accepted: boolean; production_label_allowed: boolean; statement: string; reasons: { code: string; detail: string }[] }
  benchmark: { registered_prospective_benchmark: string | null; statement: string }
  small_sample_warning: string
}

export const PRODUCTION_BASELINE = 'NFL_M1_PRODUCTION_BASELINE_PROSPECTIVE_MONITORING'

export function useNflScorecard() {
  const loader = useCallback(async (signal: AbortSignal) =>
    expectSchema(await apiRequest<NflScorecard>('/research/nfl-scorecard', {}, signal), 'AISP_NFL_SCORECARD_V1'), [])
  return useApiResource<NflScorecard>(loader, {
    key: cacheKey('nfl-scorecard'), staleTimeMs: STALE_TIME.slate,
  })
}

/** The production label is shown only for a verified, non-fixture production handoff. */
export function productionLabel(status: NflScorecard['production_status'] | undefined): string {
  if (status?.production_label_allowed && status.status === PRODUCTION_BASELINE) {
    return 'Production baseline · prospective monitoring'
  }
  return 'Research shadow · paper research evidence · no production authority'
}
