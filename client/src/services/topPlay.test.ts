import { describe, expect, it } from 'vitest'
import fixture from '../test/topPlayFixture.json'
import type { CoverageGame, DecisionItem } from './commandCenter'
import type { AssessedQuote, GameAssessment, LegMasses } from './probability'
import {
  PRICE_BLOCKED_TEXT, decimalFromAmerican, expectation, expectedProfitText, expectedReturnText, noCurrentPriceText, payoutText,
  profitIfWinText, rankOutcomes, rankableParlays, topPlay, winPayout,
} from './topPlay'
import { akronAssessment, akronGame } from '../test/cfbAkronFixture'

type Fx = { assessment: GameAssessment; game: CoverageGame; item: DecisionItem }
const real = (matchup: string) => (fixture.games as unknown as Fx[]).find((g) => g.game.matchup === matchup)!
const ATH = real('ATH @ TB')
const LAD = real('LAD @ CIN')

const masses = (win: number, loss: number, push = 0): LegMasses => ({
  win, loss, push, void: 0, conditional: push ? win / (win + loss) : win, mcse: 0.007, fair_decimal: null, fair_american: null,
})
const serverEv = (m: LegMasses, dec: number) => m.win * (dec - 1) - m.loss
function quote(market: string, line: number | null, book: string, sides: Record<string, [number, LegMasses | null]>, over: Partial<AssessedQuote> = {}): AssessedQuote {
  const current = (over.state ?? 'CURRENT') === 'CURRENT'
  return {
    market, line, sportsbook: book, sportsbook_key: book.toLowerCase(), provider: 'SportsGameOdds', classification: 'RESEARCH',
    observed_at: '2026-09-17T13:00:00Z', age_minutes: 30, fresh: current, state: 'CURRENT',
    sides: Object.fromEntries(Object.entries(sides).map(([side, [american, model]]) => {
      const decimal = decimalFromAmerican(american)
      return [side, { american, decimal, no_vig: 0.5, model, edge: model ? 0.01 : null,
        expected_return: current && model ? serverEv(model, decimal) : null,
        edge_reason: current ? (model ? null : 'NO_VERIFIED_POPULATION') : 'QUOTE_NOT_CURRENT' }]
    })),
    ...over,
  }
}
const GAME = { game_id: 'g', league: 'NFL', matchup: 'DET @ BUF', home: { abbreviation: 'BUF', name: 'Buffalo Bills' }, away: { abbreviation: 'DET', name: 'Detroit Lions' }, starts_at: null, status: 'SCHEDULED', model: null } as unknown as CoverageGame
const assessment = (quotes: AssessedQuote[], status: 'SUPPORTED' | 'UNSUPPORTED' = 'SUPPORTED'): GameAssessment => ({
  game_id: 'g', sport: 'NFL', binding: { channel: 'PAIRED_POPULATION', status }, quotes, parlay_lab: [],
})
const item = (over: Partial<DecisionItem>): DecisionItem => ({ selection: 'BUF ML', market: 'MONEYLINE', recommendation: 'QUALIFIED_BET', position: null, adjusted: null, ...over } as DecisionItem)

describe('$1 arithmetic', () => {
  it('separates win payout, expected return and expected profit', () => {
    expect(winPayout(decimalFromAmerican(-140)).total).toBeCloseTo(1.714, 3)
    expect(payoutText(decimalFromAmerican(-140))).toBe('Bet $1 → $1.71 total if it wins')
    expect(profitIfWinText(decimalFromAmerican(-140))).toBe('Profit if it wins: $0.71')
    expect(payoutText(decimalFromAmerican(230))).toBe('Bet $1 → $3.30 total if it wins')
    expect(profitIfWinText(decimalFromAmerican(230))).toBe('Profit if it wins: $2.30')
    expect(payoutText(decimalFromAmerican(-118))).toBe('Bet $1 → $1.85 total if it wins')
    const e = expectation(masses(0.5864, 0.3448, 0.0688), decimalFromAmerican(-115))
    expect(e.total - 1).toBeCloseTo(e.profit, 12)
    expect(expectedProfitText(-0.08)).toBe('Expected loss: −$0.08 per $1')
    expect(expectedReturnText(1.1293)).toBe('AISP expected return: $1.13 per $1')
  })
})

describe('real slate: ATH at TB (captured ' + fixture.captured_at + ')', () => {
  const play = topPlay(ATH.assessment, ATH.game, ATH.item)
  it('the official pick is the Athletics moneyline at +230 and it is also the best value', () => {
    expect(play.state).toBe('OFFICIAL')
    expect(play.official!.label).toBe('Athletics to win')
    expect(play.official!.best!.american).toBe(230)
    expect(play.official!.rank).toBe(1)
    expect(play.differs).toBe(false)
  })
  it('reconciles the $1 figures from full precision', () => {
    const o = play.official!
    const m = o.masses!
    expect(m.win).toBeCloseTo(0.3422, 4)
    expect(o.best!.decimal).toBeCloseTo(3.3, 6)
    const e = expectation(m, o.best!.decimal)
    expect(o.expectedProfit!).toBeCloseTo(e.profit, 9)
    expect(o.expectedProfit!).toBeCloseTo(0.12926, 4)
    expect(o.expectedReturn!).toBeCloseTo(1.12926, 4)
    expect(expectedProfitText(o.expectedProfit!)).toBe('Expected profit: +$0.13 per $1')
    expect(expectedReturnText(o.expectedReturn!)).toBe('AISP expected return: $1.13 per $1')
    expect(profitIfWinText(o.best!.decimal)).toBe('Profit if it wins: $2.30')
  })
  it('chooses among equal best prices by freshness, then name', () => {
    const tied = play.official!.books.filter((b) => b.current && b.american === 230)
    expect(tied.length).toBeGreaterThan(1)
    const freshest = Math.min(...tied.map((b) => b.quote.age_minutes))
    expect(play.official!.best!.quote.age_minutes).toBe(freshest)
  })
})

describe('real slate: LAD at CIN', () => {
  const play = topPlay(LAD.assessment, LAD.game, LAD.item)
  const byLabel = (label: string) => play.outcomes.find((o) => o.label === label)!
  it('ranks every priced outcome by push-aware expected profit, without hardcoding a winner', () => {
    const ranked = play.outcomes.filter((o) => o.rank != null).sort((a, b) => a.rank! - b.rank!)
    for (let i = 1; i < ranked.length; i++) expect(ranked[i - 1].expectedProfit!).toBeGreaterThanOrEqual(ranked[i].expectedProfit!)
    for (const o of ranked) expect(o.expectedProfit!).toBeCloseTo(expectation(o.masses!, o.best!.decimal).profit, 9)
    // The four comparisons asked for.
    const ml = byLabel('Dodgers to win')
    const rl = byLabel('Dodgers −1.5')
    const over10 = byLabel('Over 10')
    const under10 = byLabel('Under 10')
    expect(ml.best!.american).toBe(-168)
    expect(under10.masses!.push).toBeGreaterThan(0) // integer total: push mass counted
    expect(byLabel('Under 9.5').masses!.push).toBe(0) // half point: no push
    expect(over10.expectedProfit!).toBeLessThan(0)
    expect(under10.expectedProfit!).toBeGreaterThan(ml.expectedProfit!)
    expect(under10.expectedProfit!).toBeGreaterThan(rl.expectedProfit!)
    // In this capture a half-point Under is stronger still.
    expect(ranked[0].market).toBe('TOTAL')
    expect(ranked[0].side).toBe('UNDER')
  })
  it('shows the official moneyline pick and the different cross-market value side by side', () => {
    expect(play.state).toBe('OFFICIAL')
    expect(play.official!.label).toBe('Dodgers to win')
    expect(play.differs).toBe(true)
    expect(play.bestValue!.market).toBe('TOTAL')
  })
  it('never ranks the same-game combinations, which have no sportsbook price', () => {
    expect(LAD.assessment.parlay_lab.length).toBeGreaterThan(0)
    expect(rankableParlays(LAD.assessment)).toHaveLength(0)
  })
})

describe('states', () => {
  const ml = (away: number, home: number, over: Partial<AssessedQuote> = {}) =>
    quote('MONEYLINE', null, 'Book A', { AWAY: [away, masses(0.3, 0.7)], HOME: [home, masses(0.7, 0.3)] }, over)

  it('best current model value when no official decision exists', () => {
    const play = topPlay(assessment([ml(260, -300)]), GAME, item({ recommendation: 'WATCH' }))
    expect(play.state).toBe('MODEL_VALUE')
    expect(play.bestValue!.label).toBe('Lions to win')
  })

  it('no positive value: says so instead of picking the least-negative outcome', () => {
    const play = topPlay(assessment([ml(200, -300)]), GAME, null)
    expect(play.state).toBe('NO_POSITIVE')
    expect(play.bestValue).toBeNull()
    expect(play.outcomes.every((o) => o.expectedProfit! < 0)).toBe(true)
  })

  it('official pick with a negative current expectation stays the official pick', () => {
    const play = topPlay(assessment([ml(200, -300)]), GAME, item({}))
    expect(play.state).toBe('OFFICIAL')
    expect(play.official!.expectedProfit!).toBeLessThan(0)
    expect(play.differs).toBe(false)
  })

  it('stale prices: no current expectation and a price-needed state', () => {
    const play = topPlay(assessment([ml(260, -300, { state: 'STORED_REFERENCE_ONLY', fresh: false })]), GAME, null)
    expect(play.state).toBe('PRICE_NEEDED')
    expect(play.outcomes.every((o) => o.expectedProfit == null && o.blocked === 'STALE')).toBe(true)
  })

  it('a stale better price never beats a current one', () => {
    const stale = ml(400, -500, { sportsbook: 'Old Book', state: 'STORED_REFERENCE_ONLY', fresh: false })
    const play = topPlay(assessment([stale, ml(260, -300)]), GAME, null)
    const lions = play.outcomes.find((o) => o.side === 'AWAY')!
    expect(lions.best!.american).toBe(260)
    expect(lions.books.map((b) => b.american)).toEqual([260, 400])
  })

  it('pending forecast: payouts remain, expectation pending', () => {
    const q = quote('MONEYLINE', null, 'Book A', { AWAY: [150, null], HOME: [-170, null] })
    const play = topPlay(assessment([q], 'UNSUPPORTED'), GAME, null)
    expect(play.state).toBe('PENDING_FORECAST')
    expect(play.outcomes[0].best!.decimal).toBeCloseTo(2.5)
    expect(play.outcomes.every((o) => o.blocked === 'PENDING')).toBe(true)
  })

  it('no assessment at all is pending, never silent', () => {
    expect(topPlay(null, GAME, null).state).toBe('PENDING_FORECAST')
  })

  it('spread and total rows carry each side’s own line; integer spread push is counted', () => {
    const spread = quote('SPREAD', -3, 'Book A', { AWAY: [-110, masses(0.36, 0.614, 0.026)], HOME: [-110, masses(0.614, 0.36, 0.026)] })
    const rows = rankOutcomes(assessment([spread]), GAME)
    expect(rows.map((r) => r.label)).toEqual(['Lions +3', 'Bills −3'])
    const bills = rows[1]
    expect(bills.expectedProfit!).toBeCloseTo(0.614 * (decimalFromAmerican(-110) - 1) - 0.36, 9)
  })

  it('ties break on policy-adjusted edge for the official outcome', () => {
    const q = quote('MONEYLINE', null, 'Book A', { AWAY: [100, masses(0.55, 0.45)], HOME: [100, masses(0.55, 0.45)] })
    const rows = rankOutcomes(assessment([q]), GAME, { key: 'MONEYLINE|HOME|', adjustedEdge: 0.05 })
    expect(rows.find((r) => r.side === 'HOME')!.rank).toBe(1)
  })
})

describe('no current price: named for what is actually stored', () => {
  it('Akron @ Minnesota: no quote row and no collector is NO SPORTSBOOK COVERAGE, never a stored reference', () => {
    const play = topPlay(akronAssessment, akronGame(), null)
    expect(play.state).toBe('NO_COVERAGE')
    expect(play.stored).toBe(false)
    expect(play.outcomes).toHaveLength(0)
    expect(noCurrentPriceText(play, akronGame())).toBe('No sportsbook price is stored for this game: CFB price collection is not active (no allocation)')
  })

  it('a stored stale quote is still a stored reference price (unchanged)', () => {
    const stale = quote('MONEYLINE', null, 'Book A', { AWAY: [260, masses(0.3, 0.7)], HOME: [-300, masses(0.7, 0.3)] },
      { state: 'STORED_REFERENCE_ONLY', fresh: false })
    const play = topPlay(assessment([stale]), GAME, null)
    expect(play.state).toBe('PRICE_NEEDED')
    expect(play.stored).toBe(true)
    expect(noCurrentPriceText(play, GAME)).toBe(PRICE_BLOCKED_TEXT.STALE)
  })

  it('no quote row at all is never called a stored reference price', () => {
    const play = topPlay(assessment([]), GAME, null)
    expect(play.state).toBe('PRICE_NEEDED')
    expect(noCurrentPriceText(play, GAME)).toBe('No sportsbook price is stored for this game yet')
  })
})
