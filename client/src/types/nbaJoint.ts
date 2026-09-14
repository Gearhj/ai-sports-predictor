/**
 * The NBA joint comparison: two research arms of one simulation.
 *
 * Distinct from the point/Elo forecast, which declares it has NO distribution.
 * These arms have ten thousand draws each, and the difference matters: a
 * reader who conflates them would think the point/Elo forecast could answer
 * questions it explicitly cannot.
 */
export interface NbaJointArm {
  arm: string
  label: string
  forecast_id: string
  forecast_sha256: string
  configuration_identity: string
  draw_count: number
  winner_probabilities: { away: number | null; home: number | null; tie: number | null }
  expected_scores: { away: number | null; home: number | null; total: number | null }
  intervals: Record<string, Array<{
    level: number; lower: number; upper: number; method: string; units: string
  }>> | null
  illustrative_totals: Array<{
    line: number | null
    /** ILLUSTRATIVE_NOT_RECORDED_MARKET — never a price AISP was offered. */
    classification: string | null
    over: Record<string, number> | null
    under: Record<string, number> | null
    draw_count: number | null
  }>
  limitations: unknown
  supplements_forecast_id: string
  source_cutoff_utc: string | null
  created_at_utc: string | null
  imported_at: string | null
}

export interface NbaJointComparison {
  schema_version: 'AISP_NBA_JOINT_COMPARISON_V1'
  arms: NbaJointArm[]
  winner_probabilities_identical: boolean
  comparison_note: string | null
  authority: { research_only: boolean; wagering_authority: boolean; note: string }
}
