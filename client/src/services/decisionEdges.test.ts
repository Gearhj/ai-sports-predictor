import { describe, expect, it } from 'vitest'
import {
  controllingMargin, inputsLine, otherFields, recordedPolicyEdge, testLine, unadjustedOverNoVig, valueText,
  type EdgeMetrics,
} from './decisionEdges'

// CHC @ CIN, 2026-09-18 18:16:16 ET, exactly as recorded.
const chc: EdgeMetrics = {
  policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1',
  offered: { american: -134, decimal: 1.746269, break_even_probability: 0.57265 },
  inputs: { model_probability: 0.5718, adjusted_probability: 0.5518, no_vig_probability: 0.5506567,
    penalties: { uncertainty: 0, starter: 0.02, lineup: 0 } },
  fields: [
    { key: 'recorded_policy_adjusted_edge', label: 'Recorded policy-adjusted edge', value: 0.001143, unit: 'probability_points', source: 'evidence.adjusted_edge', policy_test: true, available: true, note: null },
    { key: 'recorded_policy_adjusted_return', label: 'Recorded policy-adjusted expected return at the offered odds', value: -0.036392, unit: 'return_per_dollar', source: 'evidence.adjusted_expected_value', policy_test: true, available: true, note: null },
    { key: 'unadjusted_edge_vs_break_even', label: "Unadjusted model probability minus the offered price's break-even probability", value: -0.00084957, unit: 'probability_points', source: 'shadow_experiment_decisions.edge', policy_test: false, available: true, note: null },
    { key: 'unadjusted_model_return', label: 'Unadjusted model-implied expected return at the offered odds', value: -0.00148358, unit: 'return_per_dollar', source: 'shadow_experiment_decisions.expected_value', policy_test: false, available: true, note: null },
    { key: 'recorded_unadjusted_edge_vs_no_vig', label: 'Recorded unadjusted model probability minus the no-vig probability', value: 0.021143, unit: 'probability_points', source: 'evidence.raw_edge', policy_test: false, available: true, note: null },
  ],
  tests: [
    { metric: 'recorded_policy_adjusted_edge', label: 'Recorded policy-adjusted edge', value: 0.001143, unit: 'probability_points', threshold: 0.02, operator: '>=', passed: false },
    { metric: 'recorded_policy_adjusted_return', label: 'Recorded policy-adjusted expected return at the offered odds', value: -0.036392, unit: 'return_per_dollar', threshold: 0, operator: '>', passed: false },
  ],
}

describe('recorded decision edge figures', () => {
  it('names the +0.1 the recorded policy-adjusted edge and never gives that name to the −0.1', () => {
    expect(testLine(chc.tests[0])).toBe('✗ Recorded policy-adjusted edge: +0.1 pts (policy requires ≥ 2.0 pts)')
    const others = otherFields(chc)
    const breakEven = others.find((f) => f.key === 'unadjusted_edge_vs_break_even')!
    expect(`${breakEven.label}: ${valueText(breakEven)}`).toBe("Unadjusted model probability minus the offered price's break-even probability: −0.1 pts")
    const modelReturn = others.find((f) => f.key === 'unadjusted_model_return')!
    expect(`${modelReturn.label}: ${valueText(modelReturn)}`).toBe('Unadjusted model-implied expected return at the offered odds: −0.1% per $1')
    expect(others.some((f) => f.label.includes('policy-adjusted'))).toBe(false)
  })

  it('shows a missing recorded policy edge as unavailable, not a substitute', () => {
    const missing: EdgeMetrics = { ...chc, fields: chc.fields.map((f) => (f.key === 'recorded_policy_adjusted_edge' ? { ...f, value: null, available: false } : f)) }
    const field = recordedPolicyEdge({ edge_metrics: missing })!
    expect(valueText(field)).toBe('unavailable')
  })

  it('states the recorded inputs, and ranks by distance from the policy minimum', () => {
    expect(inputsLine(chc)).toBe('Recorded model 57.2% → 55.2% after recorded penalties · no-vig 55.1% · break-even at -134 57.3%')
    expect(controllingMargin({ edge_metrics: chc })).toBeCloseTo(0.001143 - 0.02, 6)
    expect(unadjustedOverNoVig({ edge_metrics: chc, model_probability: 0.5718, market_probability: 0.55066 })).toBeCloseTo(0.021143, 6)
  })
})
