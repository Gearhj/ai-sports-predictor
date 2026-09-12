import { describe, expect, it } from 'vitest'
import { quoteDescription, resolveQuotedContract } from './marketOrientation'

/**
 * The rule these protect: a contract's side is a property of the contract, not
 * of its price. The previous version compared the raw midpoint against the
 * home probability, which worked on the data in front of it and would have
 * silently relabelled a contract as its price crossed 50%.
 *
 * Values are the real COL @ DET observations.
 */

const POLYMARKET_AWAY = {
  quoted_selection: 'AWAY',
  quoted_outcome_label: 'Colorado Rockies',
  quoted_market_id: '0x923c5ee08d25025640b5acb89cfd94a8b179b6eed86574440642fbaff6',
  quoted_outcome_id: '324003548110204684449894107495112500970858984525383301858393',
  quoted_market_question: 'Colorado Rockies vs. Detroit Tigers',
  quoted_settlement_meaning: 'YES settles if Colorado Rockies wins',
  quoted_side_source: 'CONTRACT_METADATA' as const,
  best_bid: 0.40,
  best_ask: 0.41,
}

const KALSHI_HOME = {
  quoted_selection: 'HOME',
  quoted_outcome_label: 'Detroit',
  quoted_market_id: 'KXMLBGAME-26SEP121310COLDET-DET',
  quoted_outcome_id: 'KXMLBGAME-26SEP121310COLDET-DET:YES',
  quoted_market_question: 'Detroit wins',
  quoted_settlement_meaning: 'YES settles if Detroit wins',
  quoted_side_source: 'CONTRACT_METADATA' as const,
  best_bid: 0.59,
  best_ask: 0.60,
}

describe('the side comes from the contract, not the price', () => {
  it('reads the away side from the contract that settles on Colorado', () => {
    const result = resolveQuotedContract(POLYMARKET_AWAY, 0.595)
    expect(result.side).toBe('AWAY')
    expect(result.verified).toBe(true)
    expect(result.nativeLabel).toBe('Colorado Rockies')
    expect(result.settlementMeaning).toBe('YES settles if Colorado Rockies wins')
  })

  it('reads the home side from the contract that settles on Detroit', () => {
    const result = resolveQuotedContract(KALSHI_HOME, 0.595)
    expect(result.side).toBe('HOME')
    expect(result.outcomeId).toBe('KXMLBGAME-26SEP121310COLDET-DET:YES')
  })

  it('keeps a contract on the same side when its price crosses 50%', () => {
    // This is the case the old numerical test got wrong. Detroit drifting from
    // 0.59 to 0.41 is still the Detroit contract.
    const drifted = { ...KALSHI_HOME, best_bid: 0.40, best_ask: 0.42 }
    expect(resolveQuotedContract(drifted, 0.41).side).toBe('HOME')
    expect(resolveQuotedContract(drifted, 0.41).nativeLabel).toBe('Detroit')
  })

  it('resolves a side near even money, where a price test could not', () => {
    const even = { ...KALSHI_HOME, best_bid: 0.49, best_ask: 0.51 }
    expect(resolveQuotedContract(even, 0.50).side).toBe('HOME')
    expect(resolveQuotedContract(even, 0.50).verified).toBe(true)
  })
})

describe('when identity cannot establish the side', () => {
  it('keeps the native label and the raw quote, and marks the side unverified', () => {
    const unmapped = {
      quoted_selection: null,
      quoted_outcome_label: 'Rockies ML',
      quoted_side_source: 'UNVERIFIED' as const,
      best_bid: 0.40, best_ask: 0.41,
    }
    const result = resolveQuotedContract(unmapped, 0.595)
    expect(result.side).toBeNull()
    expect(result.verified).toBe(false)
    // Independently valid information survives: the venue's own label stays.
    expect(result.nativeLabel).toBe('Rockies ML')
    expect(result.reason).toMatch(/without a canonical side mapping/)
  })

  it('says so when no contract identity is recorded at all', () => {
    const result = resolveQuotedContract({ best_bid: 0.4, best_ask: 0.41 }, 0.6)
    expect(result.side).toBeNull()
    expect(result.reason).toMatch(/No contract identity/)
  })
})

describe('price and identity disagreeing', () => {
  it('reports a contradiction without acting on it', () => {
    // Identity says Detroit; the stored price looks like Colorado's. The side
    // stands and the reader is told the two sources differ.
    const contradictory = { ...KALSHI_HOME, best_bid: 0.10, best_ask: 0.11 }
    const result = resolveQuotedContract(contradictory, 0.595)
    expect(result.side).toBe('HOME')
    expect(result.consistencyWarning).toMatch(/far from the published home probability/)
  })

  it('stays quiet when price and identity agree', () => {
    expect(resolveQuotedContract(KALSHI_HOME, 0.595).consistencyWarning).toBeNull()
    expect(resolveQuotedContract(POLYMARKET_AWAY, 0.595).consistencyWarning).toBeNull()
  })

  it('never rewrites a recorded price', () => {
    const result = resolveQuotedContract(POLYMARKET_AWAY, 0.595)
    expect(POLYMARKET_AWAY.best_bid).toBe(0.40)
    expect(result.side).toBe('AWAY')
  })
})

describe('describing a quote without calling it live', () => {
  it('calls a final pregame observation what it is', () => {
    expect(quoteDescription({
      observationKind: 'FINAL_PREGAME', inPlay: false, ageLabel: '4h ago',
    })).toEqual({ kind: 'Final pregame quote', recorded: 'last recorded 4h ago' })
  })

  it('does not call a pregame quote fresh just because it was read recently', () => {
    const described = quoteDescription({
      observationKind: 'PREGAME', inPlay: false, ageLabel: '2h ago',
    })
    expect(described.kind).toBe('Pregame quote')
    expect(described.recorded).toBe('last recorded 2h ago')
  })

  it('says when a recording time is missing instead of implying it is current', () => {
    expect(quoteDescription({ observationKind: 'PREGAME', inPlay: false, ageLabel: null }).recorded)
      .toBe('recording time not available')
  })

  it('marks an in-play observation as in-play', () => {
    expect(quoteDescription({ observationKind: null, inPlay: true, ageLabel: '5m ago' }).kind)
      .toBe('In-play observation')
  })
})
