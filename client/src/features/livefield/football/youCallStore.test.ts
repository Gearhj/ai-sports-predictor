import { describe, expect, it } from 'vitest'
import { CLASSES } from './playcall'
import { actualOption, isRight, optionProbs, topOption, xpOfCall, type YouCall } from './youCallStore'

describe('you call it', () => {
  it('folds the 12 model classes into run / short / deep / kick', () => {
    const probs = CLASSES.map((c) => (c === 'RUN_M' ? 0.3 : c === 'PASS_SL' ? 0.2 : c === 'PASS_DR' ? 0.1 : c === 'PUNT' ? 0.4 : 0))
    const o = optionProbs(probs)
    expect(o).toEqual({ RUN: 0.3, SHORT: 0.2, DEEP: 0.1, KICK: 0.4 })
    expect(topOption(o)).toBe('KICK')
  })
  it('reads what happened; a pass of unknown depth counts for either pass call', () => {
    expect(actualOption('PASS_DM', 'PASS')).toBe('DEEP')
    expect(actualOption('KNEEL', 'KNEEL')).toBe('RUN')
    expect(actualOption(null, 'PASS')).toBe('PASS')
    expect(actualOption(null, null)).toBeNull()
    expect(isRight('SHORT', 'PASS')).toBe(true)
    expect(isRight('RUN', 'PASS')).toBe(false)
  })
  it('pays rarer calls more, +20 for beating the model, half in film session, nothing when wrong', () => {
    const c = (over: Partial<YouCall>): YouCall => ({ key: 'k', event: 'e', mode: 'LIVE', pick: 'DEEP', modelPick: 'RUN', pModel: 0.15, at: 'x', ...over })
    expect(xpOfCall(c({ result: { actual: 'DEEP', right: true, modelRight: false, text: null } }))).toBe(120)
    expect(xpOfCall(c({ pModel: 0.6, result: { actual: 'DEEP', right: true, modelRight: true, text: null } }))).toBe(25)
    expect(xpOfCall(c({ pModel: 0.01, result: { actual: 'DEEP', right: true, modelRight: true, text: null } }))).toBe(100)
    expect(xpOfCall(c({ mode: 'FILM', result: { actual: 'DEEP', right: true, modelRight: false, text: null } }))).toBe(60)
    expect(xpOfCall(c({ result: { actual: 'RUN', right: false, modelRight: true, text: null } }))).toBe(0)
  })
})
