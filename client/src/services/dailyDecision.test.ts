import { describe, expect, it } from 'vitest'
import {
  cutoffPassed,
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
    expect(result.group).toBe('FINAL')
    expect(result.status).toMatch(/settled — won/)
  })

  it('reports a completed game awaiting settlement', () => {
    const result = deriveDecision(selection({
      decision_state: 'FINAL_QUALIFIED',
      funding_state: 'OFFICIAL_VIRTUAL_POSITION',
      official_position_id: 'pos-1',
      event_status: 'FINAL',
    }), NOW)
    expect(result.group).toBe('FINAL')
    expect(result.reasonCode).toBe('FINAL_AWAITING_SETTLEMENT')
  })

  it('groups a postponed game as final rather than watching', () => {
    const result = deriveDecision(selection({ event_status: 'POSTPONED' }), NOW)
    expect(result.group).toBe('FINAL')
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
    expect(slateMessage({ READY: 0, WATCHING: 11, PASSED: 0, FINAL: 0 }))
      .toBe('No qualified positions yet. 11 games are still under review.')
  })

  it('reports ready positions without implying profit', () => {
    const message = slateMessage({ READY: 2, WATCHING: 3, PASSED: 0, FINAL: 0 })
    expect(message).toBe('2 qualified positions are ready. 3 games remain under review.')
    expect(message).not.toMatch(/profit|win|edge|lock|value/i)
  })

  it('describes a completed slate', () => {
    expect(slateMessage({ READY: 0, WATCHING: 0, PASSED: 13, FINAL: 2 }))
      .toBe('Today’s slate is complete: 2 games are complete and 13 games passed.')
  })

  it('reports an empty slate without inventing games', () => {
    expect(slateMessage({ READY: 0, WATCHING: 0, PASSED: 0, FINAL: 0 }))
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
    expect(grouped.FINAL).toHaveLength(1)
  })
})
