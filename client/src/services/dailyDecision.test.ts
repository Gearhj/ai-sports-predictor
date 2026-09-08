import { describe, expect, it } from 'vitest'
import {
  cutoffPassed,
  distinctGameCount,
  deriveDecision,
  edgeDisplay,
  fairAmericanOdds,
  groupSelections,
  humanizeGate,
  nextCheckpoint,
  slateMessage,
  slateNextCheckpoint,
} from './dailyDecision'
import type { NormalizedSelection } from '../types/multisport'

const NOW = Date.parse('2026-09-06T18:00:00Z')

function selection(overrides: Partial<NormalizedSelection> = {}): NormalizedSelection {
  return {
    sport_code: 'BASEBALL',
    league_code: 'MLB',
    event_id: 'game-1',
    event_label: 'Away @ Home',
    starts_at: '2026-09-06T23:10:00Z',
    decision_state: 'AWAITING_FINAL_EVIDENCE',
    funding_state: 'ELIGIBLE_NOT_FUNDED',
    market_label: 'MONEYLINE',
    selection_label: 'Home',
    line: null,
    price_american: -110,
    sportsbook: 'BOOK',
    model_probability: 55.4,
    no_vig_probability: 52.1,
    adjusted_edge: null,
    expected_value: null,
    evidence_tier: 'B',
    virtual_stake: null,
    experiment_id: 'exp-1',
    experiment_name: 'ASP $1,000 virtual',
    policy_version: 'V2.1',
    official_position_id: null,
    decision_timestamp: null,
    final_decision_cutoff: '2026-09-06T22:55:00Z',
    source_label: 'ASP engine',
    model_status: 'DEVELOPMENT',
    market_evidence_state: 'QUOTE_PRESENT',
    outstanding_gates: [],
    status_note: null,
    can_ever_fund_official_position: true,
    sport_rank: null,
    detail_href: '/games/game-1',
    ...overrides,
  }
}

describe('grouping: evaluated-pass versus not-yet-evaluated', () => {
  it('treats a final NO_BET as PASSED because a real negative decision exists', () => {
    const result = deriveDecision(selection({ decision_state: 'FINAL_NO_BET' }), NOW)
    expect(result.group).toBe('PASSED')
    expect(result.reasonCode).toBe('PASSED_NO_QUALIFYING_EDGE')
  })

  it('treats an explicit PASS as a policy rejection, not a missing evaluation', () => {
    const result = deriveDecision(selection({ decision_state: 'PASS' }), NOW)
    expect(result.group).toBe('PASSED')
    expect(result.reasonCode).toBe('PASSED_POLICY_REJECTION')
  })

  it('never calls an unevaluated game a pass', () => {
    const result = deriveDecision(selection({ decision_state: 'NOT_EVALUATED' }), NOW)
    expect(result.group).toBe('WATCHING')
    expect(result.reasonCode).toBe('WATCH_NOT_EVALUATED')
    expect(result.status).not.toMatch(/pass/i)
  })

  it('separates a closed decision window from an evaluated pass', () => {
    const item = selection({
      decision_state: 'NOT_EVALUATED',
      final_decision_cutoff: '2026-09-06T17:00:00Z',
      starts_at: '2026-09-06T17:15:00Z',
    })
    const result = deriveDecision(item, NOW)
    expect(result.group).toBe('WATCHING')
    expect(result.reasonCode).toBe('WATCH_WINDOW_CLOSED')
    expect(result.checkpoint).toBeNull()
  })

  it('does not infer a pass merely because no position exists', () => {
    const result = deriveDecision(
      selection({ decision_state: 'AWAITING_FINAL_EVIDENCE', official_position_id: null }),
      NOW,
    )
    expect(result.group).toBe('WATCHING')
  })
})

describe('grouping: ready and final', () => {
  it('puts a funded qualified decision in READY', () => {
    const result = deriveDecision(selection({
      decision_state: 'FINAL_QUALIFIED',
      funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-1',
      virtual_stake: 25,
    }), NOW)
    expect(result.group).toBe('READY')
    expect(result.reasonCode).toBe('READY_FUNDED')
  })

  it('keeps a qualified-but-unfunded decision out of the funded reason', () => {
    const result = deriveDecision(selection({
      decision_state: 'FINAL_QUALIFIED',
      funding_state: 'ELIGIBLE_NOT_FUNDED',
      official_position_id: null,
    }), NOW)
    expect(result.group).toBe('READY')
    expect(result.reasonCode).toBe('READY_QUALIFIED_UNFUNDED')
    expect(result.detail).toMatch(/not a position/i)
  })

  it('lets a settled position outrank its pregame decision state', () => {
    const result = deriveDecision(selection({
      decision_state: 'FINAL_QUALIFIED',
      funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-1',
      position_status: 'SETTLED',
      position_result: 'WIN',
    }), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe('CLOSED_POSITION_SETTLED')
    expect(result.status).toBe('Final — virtual position settled, won')
  })

  it('reports a completed game awaiting settlement', () => {
    const result = deriveDecision(selection({
      decision_state: 'FINAL_QUALIFIED',
      funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-1',
      event_status: 'FINAL',
    }), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe('CLOSED_FINAL_SETTLEMENT_PENDING')
  })

  it('groups a postponed game as closed rather than watching', () => {
    const result = deriveDecision(selection({ event_status: 'POSTPONED' }), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe('CLOSED_POSTPONED')
    expect(result.checkpoint).toBeNull()
  })
})

describe('NFL research shadow', () => {
  it('never presents a research selection as fundable', () => {
    const result = deriveDecision(selection({
      league_code: 'NFL',
      decision_state: 'PROVISIONAL_EDGE',
      funding_state: 'RESEARCH_ONLY',
      can_ever_fund_official_position: false,
    }), NOW)
    expect(result.group).toBe('WATCHING')
    expect(result.reasonCode).toBe('WATCH_RESEARCH_ONLY')
    expect(result.status).toMatch(/no funded decision/i)
  })

  it('keeps an unevaluated research game out of READY and PASSED', () => {
    const result = deriveDecision(selection({
      league_code: 'NFL',
      decision_state: 'NOT_EVALUATED',
      funding_state: 'RESEARCH_ONLY',
      can_ever_fund_official_position: false,
    }), NOW)
    expect(result.group).toBe('WATCHING')
    expect(result.reasonCode).toBe('WATCH_RESEARCH_ONLY')
  })
})

describe('unavailable versus zero', () => {
  it('shows no edge when the sportsbook probability is unavailable', () => {
    expect(edgeDisplay(selection({ no_vig_probability: null }))).toBeNull()
  })

  it('shows no edge when the model probability is unavailable', () => {
    expect(edgeDisplay(selection({ model_probability: null }))).toBeNull()
  })

  it('does not turn a missing probability into a zero edge', () => {
    const result = edgeDisplay(selection({ model_probability: null, adjusted_edge: 0 }))
    expect(result).toBeNull()
  })

  it('labels a raw edge distinctly from a policy-adjusted edge', () => {
    expect(edgeDisplay(selection())).toEqual({ label: 'Raw edge', valuePercent: 55.4 - 52.1 })
    expect(edgeDisplay(selection({ adjusted_edge: 2.5 }))?.label).toBe('Policy-adjusted edge')
  })

  it('returns no fair price for an unavailable or impossible probability', () => {
    expect(fairAmericanOdds(null)).toBeNull()
    expect(fairAmericanOdds(0)).toBeNull()
    expect(fairAmericanOdds(100)).toBeNull()
  })

  it('derives fair odds on both sides of even money', () => {
    expect(fairAmericanOdds(50)).toBe(-100)
    expect(fairAmericanOdds(55.4)).toBe(-124)
    expect(fairAmericanOdds(40)).toBe(150)
  })
})

describe('checkpoints are never fabricated', () => {
  it('returns null when no future timestamp is recorded', () => {
    expect(nextCheckpoint(selection({
      starts_at: null, final_decision_cutoff: null, next_scheduled_refresh_at: null,
    }), NOW)).toBeNull()
  })

  it('ignores timestamps already in the past', () => {
    expect(nextCheckpoint(selection({
      starts_at: '2026-09-06T17:00:00Z',
      final_decision_cutoff: '2026-09-06T16:45:00Z',
      next_scheduled_refresh_at: null,
    }), NOW)).toBeNull()
  })

  it('chooses the soonest future checkpoint', () => {
    const result = nextCheckpoint(selection({ next_scheduled_refresh_at: '2026-09-06T18:30:00Z' }), NOW)
    expect(result).toEqual({ label: 'Next forecast refresh', at: '2026-09-06T18:30:00Z' })
  })

  it('detects a passed cutoff', () => {
    expect(cutoffPassed(selection({ final_decision_cutoff: '2026-09-06T17:00:00Z' }), NOW)).toBe(true)
    expect(cutoffPassed(selection({ final_decision_cutoff: null }), NOW)).toBe(false)
  })

  it('reports the slate checkpoint from watching games only', () => {
    const grouped = groupSelections([
      selection({ event_id: 'a', next_scheduled_refresh_at: '2026-09-06T19:30:00Z' }),
      selection({ event_id: 'b', next_scheduled_refresh_at: '2026-09-06T18:30:00Z' }),
    ], NOW)
    expect(slateNextCheckpoint(grouped.WATCHING)?.at).toBe('2026-09-06T18:30:00Z')
    expect(slateNextCheckpoint([])).toBeNull()
  })
})

describe('gate language', () => {
  it('translates known evidence gates into consumer language', () => {
    expect(humanizeGate('NO_TWO_SIDED_QUOTE')).toBe('Waiting for a complete two-sided sportsbook quote')
    expect(humanizeGate('LINEUP_NOT_CONFIRMED')).toBe('Starting lineup has not been confirmed')
  })

  it('never leaves an unknown internal token in raw form', () => {
    expect(humanizeGate('SOME_INTERNAL_TOKEN')).toBe('Some internal token')
  })
})

describe('slate message', () => {
  it('states a zero-position day honestly', () => {
    expect(slateMessage({ READY: 0, WATCHING: 11, PASSED: 0, CLOSED: 0 }))
      .toBe('No qualified decisions yet. 11 decisions are still under review.')
  })

  it('reports ready positions without implying profit', () => {
    const message = slateMessage({ READY: 2, WATCHING: 3, PASSED: 0, CLOSED: 0 }, 2)
    expect(message).toBe('2 virtual positions are ready. 3 decisions remain under review.')
    expect(message).not.toMatch(/profit|win|edge|lock|value/i)
  })

  it('describes a completed slate', () => {
    expect(slateMessage({ READY: 0, WATCHING: 0, PASSED: 13, CLOSED: 2 }))
      .toBe('Nothing is left to decide today: 2 decisions are closed and 13 decisions passed.')
  })

  it('reports an empty slate without inventing games', () => {
    expect(slateMessage({ READY: 0, WATCHING: 0, PASSED: 0, CLOSED: 0 }))
      .toBe('No governed slate is persisted for this date.')
  })
})

describe('groupSelections', () => {
  it('places every selection in exactly one group', () => {
    const items = [
      selection({ event_id: '1', decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION', official_position_id: 'p1' }),
      selection({ event_id: '2', decision_state: 'AWAITING_FINAL_EVIDENCE' }),
      selection({ event_id: '3', decision_state: 'FINAL_NO_BET' }),
      selection({ event_id: '4', event_status: 'FINAL' }),
    ]
    const grouped = groupSelections(items, NOW)
    expect(grouped.READY).toHaveLength(1)
    expect(grouped.WATCHING).toHaveLength(1)
    expect(grouped.PASSED).toHaveLength(1)
    expect(grouped.CLOSED).toHaveLength(1)
  })
})

describe('in-progress games are never actionable', () => {
  const live = (overrides = {}) => selection({ event_status: 'LIVE', ...overrides })

  it('overrides READY once the game has started', () => {
    const result = deriveDecision(live({
      decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-1', virtual_stake: 25,
    }), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe('CLOSED_IN_PROGRESS')
    expect(result.status).toBe('In progress — pregame decision window closed')
    expect(result.detail).toMatch(/open and will settle/i)
  })

  it('overrides WATCHING so a live game never awaits more pregame evidence', () => {
    const result = deriveDecision(live({
      decision_state: 'AWAITING_FINAL_EVIDENCE', outstanding_gates: ['LINEUP_NOT_CONFIRMED'],
    }), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe('CLOSED_IN_PROGRESS')
    expect(result.status).not.toMatch(/waiting|lineup/i)
  })

  it('overrides PASSED presentation for a live game', () => {
    expect(deriveDecision(live({ decision_state: 'FINAL_NO_BET' }), NOW).group).toBe('CLOSED')
  })

  it('never leaves a live game with a pregame countdown', () => {
    const result = deriveDecision(live({
      next_scheduled_refresh_at: '2026-09-06T23:00:00Z',
      final_decision_cutoff: '2026-09-06T23:30:00Z',
    }), NOW)
    expect(result.checkpoint).toBeNull()
  })

  it('accepts the other persisted in-progress spellings', () => {
    for (const status of ['LIVE', 'IN_PROGRESS', 'STARTED']) {
      expect(deriveDecision(live({ event_status: status }), NOW).reasonCode).toBe('CLOSED_IN_PROGRESS')
    }
  })

  it('lets a settled position still outrank in-progress', () => {
    const result = deriveDecision(live({ position_status: 'SETTLED', position_result: 'LOSS' }), NOW)
    expect(result.reasonCode).toBe('CLOSED_POSITION_SETTLED')
  })
})

describe('closed-group card distinctions stay separate', () => {
  const cases: Array<[string, Partial<NormalizedSelection>, string, RegExp]> = [
    ['final with a settled position', { event_status: 'FINAL', position_status: 'SETTLED', position_result: 'WIN', official_position_id: 'p' }, 'CLOSED_POSITION_SETTLED', /settled, won/],
    ['final with a position not yet settled', { event_status: 'FINAL', official_position_id: 'p' }, 'CLOSED_FINAL_SETTLEMENT_PENDING', /settlement pending/],
    ['final with no position', { event_status: 'FINAL' }, 'CLOSED_FINAL_NO_POSITION', /no position was taken/],
    ['in progress', { event_status: 'LIVE' }, 'CLOSED_IN_PROGRESS', /in progress/i],
    ['postponed', { event_status: 'POSTPONED' }, 'CLOSED_POSTPONED', /^Postponed$/],
    ['canceled', { event_status: 'CANCELED' }, 'CLOSED_CANCELED', /^Canceled$/],
    ['voided position', { position_status: 'VOID', official_position_id: 'p' }, 'CLOSED_POSITION_VOID', /voided/],
  ]

  it.each(cases)('%s keeps its own reason and wording', (_name, overrides, reason, wording) => {
    const result = deriveDecision(selection(overrides), NOW)
    expect(result.group).toBe('CLOSED')
    expect(result.reasonCode).toBe(reason)
    expect(result.status).toMatch(wording)
  })

  it('never calls a postponed or canceled game final or settled', () => {
    for (const status of ['POSTPONED', 'CANCELED']) {
      const result = deriveDecision(selection({ event_status: status, official_position_id: 'p' }), NOW)
      expect(result.status).not.toMatch(/final|settled/i)
      expect(result.detail).toMatch(/has not been settled/i)
    }
  })

  it('produces a distinct reason code for every closed situation', () => {
    const codes = cases.map(([, overrides]) => deriveDecision(selection(overrides), NOW).reasonCode)
    expect(new Set(codes).size).toBe(cases.length)
  })
})

describe('counting unit: one decision per game per policy', () => {
  const gameA1 = selection({ event_id: 'game-a', policy_version: 'V1', decision_state: 'FINAL_NO_BET' })
  const gameA2 = selection({ event_id: 'game-a', policy_version: 'V2', decision_state: 'AWAITING_FINAL_EVIDENCE' })
  const gameB = selection({ event_id: 'game-b', policy_version: 'V1', decision_state: 'AWAITING_FINAL_EVIDENCE' })

  it('groups two policies on one game independently', () => {
    const grouped = groupSelections([gameA1, gameA2, gameB], NOW)
    expect(grouped.PASSED).toHaveLength(1)
    expect(grouped.WATCHING).toHaveLength(2)
  })

  it('counts decisions, not games, so the groups sum to the decision total', () => {
    const items = [gameA1, gameA2, gameB]
    const grouped = groupSelections(items, NOW)
    const total = grouped.READY.length + grouped.WATCHING.length + grouped.PASSED.length + grouped.CLOSED.length
    expect(total).toBe(items.length)
    expect(total).toBeGreaterThan(distinctGameCount(items.map((s) => ({ selection: s }))))
  })

  it('reports distinct games separately from decisions', () => {
    expect(distinctGameCount([gameA1, gameA2, gameB].map((s) => ({ selection: s })))).toBe(2)
  })

  it('deduplicates games within a single group', () => {
    const both = [
      selection({ event_id: 'game-a', policy_version: 'V1', decision_state: 'FINAL_NO_BET' }),
      selection({ event_id: 'game-a', policy_version: 'V2', decision_state: 'PASS' }),
    ]
    const grouped = groupSelections(both, NOW)
    expect(grouped.PASSED).toHaveLength(2)
    expect(distinctGameCount(grouped.PASSED)).toBe(1)
  })

  it('lets one game appear in two groups when its policies disagree', () => {
    const grouped = groupSelections([
      selection({ event_id: 'game-a', policy_version: 'V1', decision_state: 'FINAL_QUALIFIED', funding_state: 'OFFICIAL_VIRTUAL_POSITION', official_position_id: 'p1' }),
      selection({ event_id: 'game-a', policy_version: 'V2', decision_state: 'FINAL_NO_BET' }),
    ], NOW)
    expect(grouped.READY).toHaveLength(1)
    expect(grouped.PASSED).toHaveLength(1)
    expect(distinctGameCount(grouped.READY)).toBe(1)
    expect(distinctGameCount(grouped.PASSED)).toBe(1)
  })
})


describe('an unpriceable decision is not "under review"', () => {
  it('separates a missing quote from a stale one', () => {
    const missing = deriveDecision(
      selection({ decision_state: 'AWAITING_FINAL_EVIDENCE', market_evidence_state: 'QUOTE_MISSING' }),
      NOW,
    )
    expect(missing.group).toBe('WATCHING')
    expect(missing.reasonCode).toBe('WATCH_NO_MARKET_PRICE')
    expect(missing.status).toMatch(/no sportsbook price is available/i)
    expect(missing.status).not.toMatch(/under review/i)

    const stale = deriveDecision(
      selection({ decision_state: 'AWAITING_FINAL_EVIDENCE', market_evidence_state: 'QUOTE_STALE' }),
      NOW,
    )
    expect(stale.reasonCode).toBe('WATCH_MARKET_PRICE_STALE')
    expect(stale.status).toMatch(/too old to use/i)
  })

  it('says the slate cannot be priced when every open decision lacks a quote', () => {
    const counts = { READY: 0, WATCHING: 3, PASSED: 0, CLOSED: 0 }
    expect(slateMessage(counts, 0, 3)).toMatch(/cannot be evaluated against a sportsbook price/i)
    expect(slateMessage(counts, 0, 3)).not.toMatch(/under review/i)
    // A genuinely pending decision still reads as pending.
    expect(slateMessage(counts, 0, 0)).toMatch(/still under review/i)
  })
})
