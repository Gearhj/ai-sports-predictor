/**
 * The edge figures a decision RECORDED, each named for what it is.
 *
 * "Edge" meant different things on one card (CHC @ CIN, 2026-09-18). Traced:
 *  - evidence.adjusted_edge: adjusted model probability minus the same-book
 *    no-vig probability -> "Recorded policy-adjusted edge" (MLB's first test);
 *  - evidence.adjusted_expected_value: the policy-adjusted expected return at
 *    the offered odds (MLB's second test);
 *  - the stored `edge` column: the UNADJUSTED model probability minus the
 *    offered price's break-even probability (1/decimal, vig included);
 *  - the stored `expected_value` column: the UNADJUSTED model-implied expected
 *    return at the offered odds.
 * Values are shown exactly as the decision recorded them -- never recomputed
 * from current quotes, current forecasts or a later-corrected mapping -- and a
 * value the decision did not record is shown as unavailable.
 * Server: services/edge_metrics.py.
 */

export type EdgeUnit = 'probability_points' | 'return_per_dollar'

export interface EdgeField {
  key: string
  label: string
  value: number | null
  unit: EdgeUnit
  source: string
  policy_test: boolean
  available: boolean
  note: string | null
}

export interface EdgeTest {
  metric: string
  label: string
  value: number | null
  unit: EdgeUnit
  threshold: number
  operator: '>=' | '>'
  passed: boolean | null
}

export interface EdgeMetrics {
  policy: string | null
  offered: { american: number | null; decimal: number; break_even_probability: number | null }
  inputs: {
    model_probability: number | null
    adjusted_probability: number | null
    no_vig_probability: number | null
    penalties: { uncertainty: number | null; starter: number | null; lineup: number | null } | null
    tie_probability?: number | null
  }
  fields: EdgeField[]
  tests: EdgeTest[]
}

export const UNAVAILABLE = 'unavailable'
const sign = (v: number) => (v >= 0 ? '+' : '−')
export const ptsText = (v: number | null | undefined) => (v == null ? UNAVAILABLE : `${sign(v)}${Math.abs(v * 100).toFixed(1)} pts`)
export const returnText = (v: number | null | undefined) => (v == null ? UNAVAILABLE : `${sign(v)}${Math.abs(v * 100).toFixed(1)}% per $1`)
const probText = (v: number | null | undefined) => (v == null ? UNAVAILABLE : `${(v * 100).toFixed(1)}%`)
const american = (a: number | null | undefined) => (a == null ? '—' : a > 0 ? `+${a}` : `${a}`)

export function valueText(item: { value: number | null; unit?: EdgeUnit; metric?: string }): string {
  // Older payloads carried no unit; their metric name says which kind it is.
  const points = item.unit ? item.unit === 'probability_points' : !String(item.metric ?? '').startsWith('return')
  return points ? ptsText(item.value) : returnText(item.value)
}

export function testThresholdText(t: EdgeTest): string {
  const points = t.unit ? t.unit === 'probability_points' : !String(t.metric).startsWith('return')
  const value = points ? `${(t.threshold * 100).toFixed(1)} pts` : `${(t.threshold * 100).toFixed(1)}%`
  return `policy requires ${t.operator === '>=' ? '≥' : '>'} ${value}`
}

/** "✗ Recorded policy-adjusted edge: +0.1 pts (policy requires ≥ 2.0 pts)" */
export function testLine(t: EdgeTest): string {
  const mark = t.passed == null ? '·' : t.passed ? '✓' : '✗'
  return `${mark} ${t.label}: ${valueText(t)} (${testThresholdText(t)})`
}

/** The recorded inputs behind the tests. */
export function inputsLine(m: EdgeMetrics): string {
  const parts: string[] = []
  // An older payload (before the recorded-fields read model) has no inputs block.
  const i = m.inputs ?? { model_probability: null, adjusted_probability: null, no_vig_probability: null, penalties: null }
  if (i.model_probability != null) {
    parts.push(i.adjusted_probability != null && i.adjusted_probability !== i.model_probability
      ? `Recorded model ${probText(i.model_probability)} → ${probText(i.adjusted_probability)} after recorded penalties`
      : `Recorded model ${probText(i.model_probability)}${i.tie_probability ? ` (tie ${probText(i.tie_probability)})` : ''}`)
  }
  if (i.no_vig_probability != null) parts.push(`no-vig ${probText(i.no_vig_probability)}`)
  if (m.offered.break_even_probability != null) parts.push(`break-even at ${american(m.offered.american)} ${probText(m.offered.break_even_probability)}`)
  return parts.join(' · ')
}

/** Recorded figures that are not policy tests, each under its own name. */
export function otherFields(m: EdgeMetrics): EdgeField[] {
  const tested = new Set((m.tests ?? []).map((t) => t.metric))
  return (m.fields ?? []).filter((f) => !tested.has(f.key))
}

/** The recorded policy-adjusted edge, or null when the decision did not record one. */
export function recordedPolicyEdge(item: { edge_metrics?: EdgeMetrics | null }): EdgeField | null {
  return item.edge_metrics?.fields?.find((f) => f.key === 'recorded_policy_adjusted_edge') ?? null
}

/** The policy's first recorded test: what "closest to qualifying" should rank on. */
export function controllingTest(item: { edge_metrics?: EdgeMetrics | null }): EdgeTest | null {
  const t = item.edge_metrics?.tests?.[0]
  return t && t.value != null ? t : null
}

/** How far the first recorded test is from passing (positive = clears it). */
export function controllingMargin(item: { edge_metrics?: EdgeMetrics | null }): number | null {
  const t = controllingTest(item)
  return t ? (t.value as number) - t.threshold : null
}

/** Recorded unadjusted model minus no-vig, for charts that plot those two probabilities. */
export function unadjustedOverNoVig(item: { edge_metrics?: EdgeMetrics | null; model_probability: number | null; market_probability: number | null }): number | null {
  const recorded = item.edge_metrics?.fields?.find((f) => f.key === 'recorded_unadjusted_edge_vs_no_vig')?.value
  if (recorded != null) return recorded
  return item.model_probability != null && item.market_probability != null ? item.model_probability - item.market_probability : null
}
