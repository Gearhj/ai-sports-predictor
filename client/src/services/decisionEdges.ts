/**
 * The edge figures behind one decision, each named for what it is.
 *
 * "Edge" meant three different things on one card (CHC @ CIN, 2026-09-18):
 *  - advantage over the no-vig market, p − q: what the MLB and CFB policies
 *    test (MLB after its evidence penalties) against a 2.0-point minimum;
 *  - the stored `edge` column, p − 1/decimal: the model against the offered
 *    price's break-even probability, vig included;
 *  - expected return at the offered price, p × decimal − 1: what the stake is
 *    paid at, and what the NFL policy tests.
 * The server states each one and the policy's own tests (services/edge_metrics.py).
 */

export interface EdgeTest {
  metric: string
  label: string
  value: number | null
  threshold: number
  operator: '>=' | '>'
  passed: boolean | null
}

export interface EdgeMetrics {
  policy: string | null
  offered: { american: number | null; decimal: number; break_even_probability: number | null }
  no_vig_probability: number | null
  model_probability: {
    raw: number | null
    adjusted: number | null
    penalties: { uncertainty: number | null; starter: number | null; lineup: number | null } | null
    tie?: number | null
  }
  advantage_vs_no_vig: { raw: number | null; adjusted: number | null }
  advantage_vs_break_even: { raw: number | null; adjusted: number | null }
  return_at_offered_price: { raw: number | null; adjusted: number | null }
  vig_cost_at_offered_price: number | null
  tests: EdgeTest[]
}

const sign = (v: number) => (v >= 0 ? '+' : '−')
export const ptsText = (v: number | null | undefined) => (v == null ? '—' : `${sign(v)}${Math.abs(v * 100).toFixed(1)} pts`)
export const returnText = (v: number | null | undefined) => (v == null ? '—' : `${sign(v)}${Math.abs(v * 100).toFixed(1)}% per $1`)
const probText = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const american = (a: number | null | undefined) => (a == null ? '—' : a > 0 ? `+${a}` : `${a}`)

const isAdvantage = (t: EdgeTest) => t.metric.startsWith('advantage')

export function testValueText(t: EdgeTest): string {
  return isAdvantage(t) ? ptsText(t.value) : returnText(t.value)
}

export function testThresholdText(t: EdgeTest): string {
  const value = isAdvantage(t) ? `${(t.threshold * 100).toFixed(1)} pts` : `${(t.threshold * 100).toFixed(1)}%`
  return `needs ${t.operator === '>=' ? '≥' : '>'} ${value}`
}

/** "✓ Adjusted advantage over the no-vig market +0.1 pts (needs ≥ 2.0 pts)" */
export function testLine(t: EdgeTest): string {
  const mark = t.passed == null ? '·' : t.passed ? '✓' : '✗'
  return `${mark} ${t.label}: ${testValueText(t)} (${testThresholdText(t)})`
}

/** The inputs behind the tests, in one line: model (and after penalties), no-vig, break-even. */
export function inputsLine(m: EdgeMetrics): string {
  const parts: string[] = []
  const p = m.model_probability
  if (p.raw != null) {
    parts.push(p.adjusted != null && p.adjusted !== p.raw
      ? `Model ${probText(p.raw)} → ${probText(p.adjusted)} after penalties`
      : `Model ${probText(p.raw)}${p.tie ? ` (tie ${probText(p.tie)})` : ''}`)
  }
  if (m.no_vig_probability != null) parts.push(`no-vig ${probText(m.no_vig_probability)}`)
  if (m.offered.break_even_probability != null) parts.push(`break-even at ${american(m.offered.american)} ${probText(m.offered.break_even_probability)}`)
  return parts.join(' · ')
}

/** Figures the tests do not already state, so nothing is shown twice. */
export function otherFigures(m: EdgeMetrics): string {
  const tested = new Set(m.tests.map((t) => t.metric))
  const parts: string[] = []
  if (!tested.has('advantage_vs_no_vig.raw') && m.advantage_vs_no_vig.raw != null) parts.push(`raw advantage over no-vig ${ptsText(m.advantage_vs_no_vig.raw)}`)
  if (!tested.has('return_at_offered_price.raw') && m.return_at_offered_price.raw != null) parts.push(`raw expected return at ${american(m.offered.american)} ${returnText(m.return_at_offered_price.raw)}`)
  if (m.advantage_vs_break_even.raw != null) parts.push(`raw model minus break-even ${ptsText(m.advantage_vs_break_even.raw)}`)
  return parts.join(' · ')
}

/** The policy's first test: what "closest to qualifying" should rank on. */
export function controllingTest(item: { edge_metrics?: EdgeMetrics | null }): EdgeTest | null {
  const t = item.edge_metrics?.tests?.[0]
  return t && t.value != null ? t : null
}

/** How far the first test is from passing (positive = clears it). */
export function controllingMargin(item: { edge_metrics?: EdgeMetrics | null }): number | null {
  const t = controllingTest(item)
  return t ? (t.value as number) - t.threshold : null
}

/** Model minus the no-vig market, for charts that plot the two probabilities. */
export function advantageOverNoVig(item: { edge_metrics?: EdgeMetrics | null; model_probability: number | null; market_probability: number | null }): number | null {
  const raw = item.edge_metrics?.advantage_vs_no_vig.raw
  if (raw != null) return raw
  return item.model_probability != null && item.market_probability != null ? item.model_probability - item.market_probability : null
}
