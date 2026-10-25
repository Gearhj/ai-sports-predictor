import { describe, expect, it } from 'vitest'
import type { DecisionItem, LedgerPosition } from './commandCenter'
import type { EdgeMetrics } from './decisionEdges'
import {
  BEST_QUOTE_LABEL, NOT_AVAILABLE, OFFICIAL_EDGE_LABEL, SHADOW_ONLY_LABEL, bestQuoteSource, decisionLead, freshness, latestSeen, leadCountdown, leadEdge, quoteOrder,
} from './decisionLead'
import type { CoverageGame } from './commandCenter'

const NOW = Date.parse('2026-09-15T20:00:00Z')
const at = (minutesFromNow: number) => new Date(NOW + minutesFromNow * 60_000).toISOString()

// Minimal read-model fixtures in the served shapes (same as services/edgeBoard.test).
function game(id: string, opts: Partial<CoverageGame> & { startIn?: number; homeP?: number | null } = {}): CoverageGame {
  const start = opts.startIn ?? 180
  const home = opts.homeP === undefined ? 0.55 : opts.homeP
  return {
    game_id: id, league: 'MLB', matchup: `A${id} @ H${id}`,
    home: { abbreviation: `H${id}`, name: `Home ${id}` }, away: { abbreviation: `A${id}`, name: `Away ${id}` },
    starts_at: at(start), status: 'SCHEDULED', price_state: 'NOT_COLLECTED', books: 0, fresh_books: 0,
    freshest_observed_at: null, stalest_usable_observed_at: null, consensus_no_vig_home: null, consensus_is_current: false,
    best: { HOME: null, AWAY: null },
    model: home == null ? null : { engine_id: 'MLB_V18_WHOLE_GAME', home, away: 1 - home, home_two_way: home, created_at: at(-60) },
    quotes: [],
    checkpoints: { collection_opens_at: at(start - 255), decision_window_opens_at: at(start - 30), decision_window_closes_at: at(start - 15) },
    ...opts,
  }
}
function priced(g: CoverageGame): CoverageGame {
  return {
    ...g, price_state: 'CURRENT_PRICE', books: 2, fresh_books: 2, consensus_no_vig_home: 0.5, consensus_is_current: true,
    best: { HOME: { american: -110, sportsbook: 'Fanduel', provider: 'SportsGameOdds', observed_at: at(-10) }, AWAY: { american: 105, sportsbook: 'Betmgm', provider: 'SportsGameOdds', observed_at: at(-10) } },
  }
}
function decision(id: string, g: CoverageGame, opts: Partial<DecisionItem> = {}): DecisionItem {
  return {
    decision_id: `d${id}`, sport: 'MLB', experiment: 'X', game_id: g.game_id, matchup: g.matchup, starts_at: g.starts_at,
    phase: 'FINAL_PREGAME', market: 'MONEYLINE', selection: `${g.home!.abbreviation} ML`, line: null, sportsbook: 'Fanduel',
    american: -110, quote_observed_at: at(-20), quote_age_minutes: 20, model_probability: 0.56, market_probability: 0.5,
    edge: 0.04, expected_value: 0.05, recommendation: 'NO_BET', stored_recommendation: 'NO_BET', reason: null, recommended_at: at(-5),
    decision_window: { opens_at: g.checkpoints.decision_window_opens_at, closes_at: g.checkpoints.decision_window_closes_at },
    group: 'PASSED', position: null, adjusted: { edge: 0.02, expected_value: 0.03, probability: 0.54 }, plain_reasons: [],
    ...opts,
  }
}

// Recorded fields as the decision board serves them (CHC @ CIN shape): the stored
// `edge` column (unadjusted, vs break-even) differs from the recorded policy-adjusted edge.
const metrics = (policyEdge: number | null, passed: boolean | null = false): EdgeMetrics => ({
  policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1',
  offered: { american: -110, decimal: 1.909, break_even_probability: 0.5238 },
  inputs: { model_probability: 0.56, adjusted_probability: 0.54, no_vig_probability: 0.5, penalties: null },
  fields: [
    { key: 'recorded_policy_adjusted_edge', label: 'Recorded policy-adjusted edge', value: policyEdge, unit: 'probability_points', source: 'evidence.adjusted_edge', policy_test: true, available: policyEdge != null, note: null },
    { key: 'unadjusted_edge_vs_break_even', label: "Unadjusted model probability minus the offered price's break-even probability", value: 0.0362, unit: 'probability_points', source: 'shadow_experiment_decisions.edge', policy_test: false, available: true, note: null },
  ],
  tests: [
    { metric: 'recorded_policy_adjusted_edge', label: 'Recorded policy-adjusted edge', value: policyEdge, unit: 'probability_points', threshold: 0.02, operator: '>=', passed },
  ],
})

const g = priced(game('1'))
const item = (opts: Partial<DecisionItem> = {}) => decision('a', g, { edge_metrics: metrics(0.012), ...opts })
const ledgerPosition = (id: string): LedgerPosition => ({
  position_id: id, game_id: g.game_id, matchup: g.matchup, starts_at: g.starts_at, market: 'MONEYLINE', selection: 'H1 ML',
  entry: { american: -108, decimal: 1.926, sportsbook: 'Fanduel (SportsGameOdds, research)', sportsbook_key: 'sgo:fanduel', provider: 'SportsGameOdds', classification: 'RESEARCH', quote_observed_at: null } as LedgerPosition['entry'],
  stake: 5, placed_at: null, status: 'OPEN', result: null, profit_loss: null, settled_at: null, experiment: { id: 'e', name: 'X' },
})

describe('decision status mapping: provisional vs final decline vs funded', () => {
  it('a MORNING read is an early read, never final, and names when the official decision happens', () => {
    const lead = decisionLead({ item: item({ phase: 'MORNING', recommendation: 'NO_BET' }), game: g, now: NOW })
    expect(lead).toMatchObject({ kind: 'PROVISIONAL', authority: 'PROVISIONAL', label: 'Morning read · not final' })
    expect(lead.sentence).toMatch(/^The morning read does not clear the policy so far; the official decision is made in the .* ET decision window\.$/)
  })

  it('a morning read that qualifies is still provisional, not qualified', () => {
    const lead = decisionLead({ item: item({ phase: 'LINEUP_CONFIRMED', recommendation: 'QUALIFIED_BET' }), game: g, now: NOW })
    expect(lead.kind).toBe('PROVISIONAL')
    expect(lead.label).toBe('Lineup read · not final')
    expect(lead.sentence).toMatch(/qualifies so far/)
  })

  it('a FINAL_PREGAME NO_BET is a final decline with its reason from the recorded failing test', () => {
    const lead = decisionLead({ item: item({ recommendation: 'NO_BET' }), game: g, now: NOW })
    expect(lead).toMatchObject({ kind: 'FINAL_DECLINE', authority: 'FINAL', label: 'Final · declined', tone: 'red' })
    expect(lead.sentence).toBe('Declined at the final check: recorded policy-adjusted edge was +1.2 pts; policy requires ≥ 2.0 pts.')
  })

  it('a final decline on a stale price says so, from the recorded reason', () => {
    const lead = decisionLead({ item: item({ recommendation: 'NO_BET', reason: 'quote is stale at the decision' }), game: g, now: NOW })
    expect(lead.sentence).toBe('Declined at the final check: the price was not current.')
  })

  it('a final QUALIFIED_BET without a position is qualified but explicitly not funded', () => {
    const lead = decisionLead({ item: item({ recommendation: 'QUALIFIED_BET', edge_metrics: metrics(0.031, true) }), game: g, now: NOW })
    expect(lead).toMatchObject({ kind: 'QUALIFIED', authority: 'FINAL', label: 'Qualified · not funded yet', funded: null })
  })

  describe('MLB V18 funding pause: shadow only', () => {
    const pause = {
      record_id: 'rec-1', content_sha256: 'ab'.repeat(32), verified: true, feature: 'MLB_V18_FUNDING_PAUSE_V1',
      reason: 'FUNDING_EDGE_NOT_ESTABLISHED', status: 'PENDING_PROSPECTIVE_VALIDATION', paused_at: at(-2),
      qualified_edge_threshold: 0.02, policy_adjusted_edge: 0.031, position_created: false as const,
    }
    const paused = (opts: Partial<DecisionItem> = {}) =>
      item({ recommendation: 'QUALIFIED_BET', group: 'READY', edge_metrics: metrics(0.031, true), funding_pause: pause, ...opts })

    it('a paused qualifier is "Qualified · shadow only (funding paused)", with the one-sentence reason', () => {
      const lead = decisionLead({ item: paused(), game: g, now: NOW })
      expect(lead).toMatchObject({ kind: 'QUALIFIED_SHADOW_ONLY', authority: 'FINAL', label: SHADOW_ONLY_LABEL, funded: null })
      expect(lead.label).toBe('Qualified · shadow only (funding paused)')
      expect(lead.sentence).toBe(
        'It met the policy at the final check, but MLB V18 funding is paused pending prospective validation, so no paper position was created.')
      expect(lead.fundingPause).toEqual({ recordId: 'rec-1', contentSha256: 'ab'.repeat(32), pausedAt: pause.paused_at,
        reason: 'FUNDING_EDGE_NOT_ESTABLISHED', status: 'PENDING_PROSPECTIVE_VALIDATION', verified: true })
    })

    it('is never confused with "Qualified · not funded yet", before or after the start', () => {
      for (const now of [NOW, Date.parse(g.starts_at!) + 60_000]) {
        const lead = decisionLead({ item: paused(), game: g, now })
        expect(lead.kind).toBe('QUALIFIED_SHADOW_ONLY')
        expect(lead.label).not.toMatch(/not funded/)
        expect(lead.authority).not.toBe('FUNDED')
        expect(lead.funded).toBeNull()
      }
      // Without a pause record the same decision keeps its existing meaning.
      expect(decisionLead({ item: paused({ funding_pause: null }), game: g, now: NOW }).label).toBe('Qualified · not funded yet')
    })

    it('only a final QUALIFIED_BET can be shadow only; an early read stays provisional', () => {
      expect(decisionLead({ item: paused({ phase: 'MORNING' }), game: g, now: NOW }).kind).toBe('PROVISIONAL')
      expect(decisionLead({ item: paused({ recommendation: 'NO_BET', group: 'PASSED' }), game: g, now: NOW }).kind).toBe('FINAL_DECLINE')
    })
  })

  it('a decision with a position is a funded paper wager, cross-checked against the ledger', () => {
    const position = { position_id: 'p1', stake: 5, status: 'OPEN', result: null, profit_loss: null }
    const funded = decisionLead({ item: item({ recommendation: 'QUALIFIED_BET', position }), game: g, ledgerPosition: ledgerPosition('p1'), ledgerLoaded: true, now: NOW })
    expect(funded).toMatchObject({ kind: 'FUNDED_OPEN', authority: 'FUNDED', label: 'Funded · open' })
    // Entry terms come from the ledger position, not from the decision row.
    expect(funded.funded).toMatchObject({ ledgerConfirmed: true, stake: 5, american: -108, sportsbook: 'Fanduel' })
    expect(funded.sentence).toBe('Funded with $5.00 at -108 (Fanduel) in the paper ledger; it settles after the final score.')
    const missing = decisionLead({ item: item({ position }), game: g, ledgerPosition: null, ledgerLoaded: true, now: NOW })
    expect(missing.funded?.ledgerConfirmed).toBe(false)
    const pending = decisionLead({ item: item({ position }), game: g, now: NOW })
    expect(pending.funded?.ledgerConfirmed).toBeNull()
  })

  it('settled and awaiting-settlement positions are stated as such', () => {
    const settled = decisionLead({ item: item({ position: { position_id: 'p', stake: 5, status: 'SETTLED', result: 'WIN', profit_loss: 4.55 } }), game: g, now: NOW })
    expect(settled).toMatchObject({ kind: 'FUNDED_SETTLED', label: 'Funded · settled Won +$4.55', tone: 'green' })
    const awaiting = decisionLead({ item: item({ position: { position_id: 'p', stake: 5, status: 'OPEN', result: null, profit_loss: null, awaiting_settlement: true } }), game: g, now: NOW })
    expect(awaiting).toMatchObject({ kind: 'FUNDED_AWAITING', label: 'Funded · settlement pending' })
  })

  it('NOT_YET_EVALUATED shows no selection and says what it waits for', () => {
    const lead = decisionLead({ item: item({ phase: 'MORNING', recommendation: 'NOT_YET_EVALUATED', selection: '—', american: null }), game: game('1'), now: NOW })
    expect(lead).toMatchObject({ kind: 'NOT_EVALUATED', authority: 'NONE', official: null, label: 'Not evaluated yet' })
    expect(lead.sentence).toMatch(/^Waiting for a current two-sided sportsbook price/)
    const noForecast = decisionLead({ item: null, game: game('2', { homeP: null }), now: NOW })
    expect(noForecast.sentence).toMatch(/^Waiting for the forecast/)
  })

  it('a started game without a final evaluation says no wager was made', () => {
    const started = priced(game('9', { startIn: -5 }))
    const lead = decisionLead({ item: decision('a', started, { phase: 'MORNING' }), game: started, now: NOW })
    expect(lead).toMatchObject({ kind: 'MISSED', label: 'No final decision' })
  })

  it('College Football checkpoint semantics keep no-price and not-activated apart', () => {
    const noPrice = decisionLead({ item: item({ sport: 'CFB', checkpoint_semantics: { class: 'NO_PRICE', reason: null, evaluated: false, priced: false, text: '' } }), game: g, now: NOW })
    expect(noPrice.label).toBe('Final · declined (no usable price)')
    const off = decisionLead({ item: item({ sport: 'CFB', recommendation: 'QUALIFIED_BET', checkpoint_semantics: { class: 'QUALIFIED_NOT_ACTIVATED', reason: null, evaluated: true, priced: true, text: '' } }), game: g, now: NOW })
    expect(off.kind).toBe('QUALIFIED')
    expect(off.funded).toBeNull()
  })

  it('a decline because the shared $100 had no room is a budget decline, never a no-bet (any sport)', () => {
    const cfb = decisionLead({ item: item({ sport: 'CFB', recommendation: 'DECLINED', reason: 'held', checkpoint_semantics: { class: 'NO_ROOM', reason: 'SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS', evaluated: true, priced: true, text: '' } }), game: g, now: NOW })
    expect(cfb).toMatchObject({ kind: 'FINAL_DECLINE', label: 'Final · declined (no room in the shared $100)' })
    expect(cfb.sentence).toMatch(/for budget, not value/)
    const nfl = decisionLead({ item: item({ sport: 'NFL', recommendation: 'DECLINED', reason: 'The shared $100 for 2026-10-18 is fully entered ($100.00); the value qualified, but no room remains for this NFL decision.' }), game: g, now: NOW })
    expect(nfl.label).toBe('Final · declined (no room in the shared $100)')
    const noBet = decisionLead({ item: item({ recommendation: 'NO_BET', reason: 'edge below the minimum' }), game: g, now: NOW })
    expect(noBet.label).not.toMatch(/shared \$100/)
  })
})

describe('edge label', () => {
  it('is the recorded policy-adjusted edge, never the stored unadjusted `edge` column', () => {
    const e = leadEdge(item({ edge: 0.0362 }))
    expect(e.label).toBe(OFFICIAL_EDGE_LABEL)
    expect(e.text).toBe('+1.2 pts')
    expect(e.qualifier).toBe('as recorded · policy requires ≥ 2.0 pts')
  })
  it('shows "not available" when the decision recorded none, even if the stored edge exists', () => {
    const e = leadEdge(item({ edge: 0.0362, edge_metrics: metrics(null) }))
    expect(e).toMatchObject({ value: null, text: NOT_AVAILABLE })
    expect(leadEdge(item({ edge: 0.0362, edge_metrics: null })).text).toBe(NOT_AVAILABLE)
  })
})

describe('recorded vs latest price', () => {
  it('keeps the recorded decision price and the latest observation apart', () => {
    const lead = decisionLead({ item: item({ american: -110, sportsbook: 'Fanduel (SportsGameOdds, research)' }), game: g, now: NOW })
    expect(lead.official).toMatchObject({ american: -110, sportsbook: 'Fanduel', quoteAgeAtDecisionMinutes: 20 })
    const moved = { ...g, best: { ...g.best, HOME: { american: -125, sportsbook: 'Betmgm (SportsGameOdds, research)', provider: 'SportsGameOdds', observed_at: new Date(NOW - 3 * 60_000).toISOString() } } }
    const latest = latestSeen(item(), moved)
    expect(latest).toMatchObject({ american: -125, sportsbook: 'Betmgm' })
    // The lead's recorded price never follows the market.
    expect(decisionLead({ item: item(), game: moved, now: NOW }).official?.american).toBe(-110)
    expect(freshness(lead, latest, NOW)).toEqual(['Price 20 min old at decision', expect.stringMatching(/^Decision recorded /), 'Best current quote observed 3 min ago'])
  })
  it('has no latest price when the coverage price is not current, and says unavailable values explicitly', () => {
    expect(latestSeen(item(), game('1'))).toBeNull()
    const lead = decisionLead({ item: item({ quote_age_minutes: null, recommended_at: null }), game: g, now: NOW })
    expect(freshness(lead, null, NOW)).toEqual([`Price age at decision ${NOT_AVAILABLE}`, `Decision time ${NOT_AVAILABLE}`])
  })
})

describe('best current cross-book quote: later only when the clocks say so', () => {
  const recorded = '2026-09-24T18:37:00Z' // 2:37 PM ET
  it('LATER: observed after the recorded quote', () => {
    expect(quoteOrder('2026-09-24T20:12:00Z', recorded)).toBe('LATER')
    expect(bestQuoteSource({ sportsbook: 'Caesars (SportsGameOdds, research)', observedAt: '2026-09-24T20:12:00Z' }, recorded))
      .toBe('Caesars · observed 4:12 PM ET · later than the decision price')
  })
  it('NOT_LATER: same time or earlier is never called later', () => {
    expect(quoteOrder(recorded, recorded)).toBe('NOT_LATER')
    expect(quoteOrder('2026-09-24T18:01:00Z', recorded)).toBe('NOT_LATER')
    expect(bestQuoteSource({ sportsbook: 'Caesars', observedAt: '2026-09-24T18:01:00Z' }, recorded)).toBe('Caesars · observed 2:01 PM ET')
  })
  it('UNKNOWN: a missing clock on either side is stated, never guessed', () => {
    expect(quoteOrder(null, recorded)).toBe('UNKNOWN')
    expect(quoteOrder('2026-09-24T20:12:00Z', null)).toBe('UNKNOWN')
    expect(bestQuoteSource({ sportsbook: 'Caesars', observedAt: null }, recorded)).toBe('Caesars · observed · time not available')
    expect(bestQuoteSource({ sportsbook: 'Caesars', observedAt: '2026-09-24T20:12:00Z' }, null)).toBe('Caesars · observed 4:12 PM ET')
    expect(bestQuoteSource({ sportsbook: null, observedAt: 'garbage' }, recorded)).toBe('sportsbook not available · observed · time not available')
    expect(BEST_QUOTE_LABEL).toBe('Best current quote across books')
  })
  it('the freshness chip says when the clock is missing', () => {
    const lead = decisionLead({ item: item(), game: g, now: NOW })
    expect(freshness(lead, { american: -120, sportsbook: 'Caesars', observedAt: null }, NOW)).toContain('Best current quote time not available')
  })
})

describe('countdown', () => {
  it('counts to the window opening, then its close, then the start', () => {
    const gg = game('1', { startIn: 180 })
    expect(leadCountdown(null, gg, NOW)).toEqual({ label: 'Decision window opens in', value: '2 h 30 min' })
    expect(leadCountdown(null, gg, NOW + 155 * 60_000)).toEqual({ label: 'Decision window closes in', value: '10 min' })
    expect(leadCountdown(null, gg, NOW + 170 * 60_000)).toEqual({ label: 'Starts in', value: '10 min' })
    expect(leadCountdown(null, gg, NOW + 181 * 60_000)).toEqual({ label: 'Game', value: 'started' })
  })
  it('after a final or funded decision it counts to the start, not the window', () => {
    const gg = game('1', { startIn: 180 })
    expect(leadCountdown(decision('a', gg, { phase: 'FINAL_PREGAME' }), gg, NOW)).toEqual({ label: 'Starts in', value: '3 h 00 min' })
    expect(leadCountdown(decision('a', gg, { phase: 'MORNING' }), gg, NOW)?.label).toBe('Decision window opens in')
  })
})
