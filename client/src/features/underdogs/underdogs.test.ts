import { describe, expect, it } from 'vitest'
import { aispSplit, americanOf, bandOf, tally, upsets, type DogRow } from './underdogs'

const row = (p: number, won: boolean, dec: number | null, aisp: DogRow['aisp'] = null): DogRow => ({
  sport: 'NHL', date: '2026-10-27', home: 'A', away: 'B', dog_team: 'B', fav_team: 'A', dog_p: p, dog_best_decimal: dec, dog_won: won, score: '', aisp,
})

describe('underdog report', () => {
  const rows = [row(0.4, true, 2.6), row(0.4, false, 2.5), row(0.2, true, 5.5), row(0.48, false, null)]

  it('counts wins, the market expectation and $1 on every dog', () => {
    const t = tally(rows)
    expect(t.n).toBe(4)
    expect(t.won).toBe(2)
    expect(t.rate).toBe(0.5)
    expect(t.expected).toBeCloseTo((0.4 + 0.4 + 0.2 + 0.48) / 4, 9)
    expect(t.flatBets).toBe(3)                 // no price, no bet
    expect(t.flatProfit).toBeCloseTo(1.6 - 1 + 4.5, 9)
  })

  it('bands by size of underdog', () => {
    expect(bandOf(0.49).key).toBe('coin')
    expect(bandOf(0.36).key).toBe('mid')
    expect(bandOf(0.1).key).toBe('long')
  })

  it('splits AISP by dog or favourite and ignores unsettled positions', () => {
    const a = aispSplit([
      row(0.4, true, 2.6, { side: 'AWAY', on_dog: true, result: 'WIN', stake: 5, payout: 13 }),
      row(0.4, false, 2.6, { side: 'AWAY', on_dog: true, result: 'LOSS', stake: 5, payout: 0 }),
      row(0.4, false, 2.6, { side: 'HOME', on_dog: false, result: 'WIN', stake: 5, payout: 8 }),
      row(0.4, false, 2.6, { side: 'HOME', on_dog: false, result: null, stake: 5, payout: 0 }),
    ])
    expect(a.dog).toEqual({ n: 2, w: 1, l: 1, net: 3, staked: 10 })
    expect(a.fav.n).toBe(1)
  })

  it('lists the longest-priced winners first and converts prices', () => {
    expect(upsets(rows).map((r) => r.dog_p)).toEqual([0.2, 0.4])
    expect(americanOf(2.6)).toBe(160)
    expect(americanOf(1.5)).toBe(-200)
  })
})
