import { describe, expect, it } from 'vitest'
import { quoteDescription, resolveQuotedSide } from './marketOrientation'

/**
 * The bug these cover: "DET win 59.5%" was shown in the same row as a
 * "Bid / ask" of 40.0% / 41.0%, under one heading, as though both described
 * Detroit. The midpoint is home-oriented; the bid and ask are the venue's own
 * contract, and venues list different sides.
 */

describe('resolving which side a book is quoted on', () => {
  it('recognises an away-side book beside a home-oriented probability', () => {
    // Polymarket's real COL @ DET observation.
    expect(resolveQuotedSide({ homeProbability: 0.595, bestBid: 0.40, bestAsk: 0.41 }))
      .toMatchObject({ side: 'AWAY', reason: null })
  })

  it('recognises a home-side book', () => {
    // Kalshi's, from the same game at an earlier snapshot.
    expect(resolveQuotedSide({ homeProbability: 0.595, bestBid: 0.59, bestAsk: 0.60 }))
      .toMatchObject({ side: 'HOME', reason: null })
  })

  it('withholds the side when the book matches neither', () => {
    const result = resolveQuotedSide({ homeProbability: 0.595, bestBid: 0.10, bestAsk: 0.11 })
    expect(result.side).toBeNull()
    expect(result.reason).toMatch(/does not match either side/)
  })

  it('withholds near even money, where the test cannot tell the sides apart', () => {
    // A coin flip presented as a fact is worse than an explicit unknown.
    const result = resolveQuotedSide({ homeProbability: 0.50, bestBid: 0.49, bestAsk: 0.51 })
    expect(result.side).toBeNull()
    expect(result.reason).toMatch(/too near even money/)
  })

  it('withholds when there is no quote or no oriented probability to compare', () => {
    expect(resolveQuotedSide({ homeProbability: 0.6, bestBid: null, bestAsk: null }).side).toBeNull()
    expect(resolveQuotedSide({ homeProbability: null, bestBid: 0.4, bestAsk: 0.41 }).side).toBeNull()
  })

  it('never rewrites a price to make the two agree', () => {
    // The resolver reports the raw midpoint as recorded. Complementing it
    // would make a mislabelled price indistinguishable from a correct one.
    const result = resolveQuotedSide({ homeProbability: 0.595, bestBid: 0.40, bestAsk: 0.41 })
    expect(result.rawMidpoint).toBeCloseTo(0.405, 5)
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
