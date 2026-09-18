import { describe, expect, it } from 'vitest'
import { advantageOverNoVig, controllingMargin, inputsLine, otherFigures, testLine, type EdgeMetrics } from './decisionEdges'

// CHC @ CIN, 2026-09-18 18:16:16 ET, as recorded.
const chc: EdgeMetrics = {
  policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1',
  offered: { american: -134, decimal: 1.746269, break_even_probability: 0.57265 },
  no_vig_probability: 0.5506567,
  model_probability: { raw: 0.5718, adjusted: 0.5518, penalties: { uncertainty: 0, starter: 0.02, lineup: 0 } },
  advantage_vs_no_vig: { raw: 0.0211433, adjusted: 0.001143 },
  advantage_vs_break_even: { raw: -0.00084957, adjusted: -0.0208496 },
  return_at_offered_price: { raw: -0.00148358, adjusted: -0.036392 },
  vig_cost_at_offered_price: -0.0384054,
  tests: [
    { metric: 'advantage_vs_no_vig.adjusted', label: 'Adjusted advantage over the no-vig market', value: 0.001143, threshold: 0.02, operator: '>=', passed: false },
    { metric: 'return_at_offered_price.adjusted', label: 'Adjusted expected return at the offered price', value: -0.036392, threshold: 0, operator: '>', passed: false },
  ],
}

describe('decision edge figures', () => {
  it('states the +0.1 policy figure and the −0.1 break-even figure under their own names', () => {
    expect(testLine(chc.tests[0])).toBe('✗ Adjusted advantage over the no-vig market: +0.1 pts (needs ≥ 2.0 pts)')
    expect(testLine(chc.tests[1])).toBe('✗ Adjusted expected return at the offered price: −3.6% per $1 (needs > 0.0%)')
    expect(otherFigures(chc)).toContain('raw model minus break-even −0.1 pts')
    expect(otherFigures(chc)).toContain('raw expected return at -134 −0.1% per $1')
    expect(otherFigures(chc)).toContain('raw advantage over no-vig +2.1 pts')
  })

  it('shows the inputs: model before and after penalties, no-vig and break-even', () => {
    expect(inputsLine(chc)).toBe('Model 57.2% → 55.2% after penalties · no-vig 55.1% · break-even at -134 57.3%')
  })

  it('ranks by distance from the policy minimum, and charts model minus no-vig', () => {
    expect(controllingMargin({ edge_metrics: chc })).toBeCloseTo(0.001143 - 0.02, 6)
    expect(advantageOverNoVig({ edge_metrics: chc, model_probability: 0.5718, market_probability: 0.55066 })).toBeCloseTo(0.0211433, 6)
    expect(advantageOverNoVig({ edge_metrics: null, model_probability: 0.5, market_probability: 0.45 })).toBeCloseTo(0.05, 6)
  })
})
