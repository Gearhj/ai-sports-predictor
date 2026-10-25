import { describe, expect, it } from 'vitest'
import type { DecisionItem } from './commandCenter'
import { ledgerOf, pos } from '../test/activeWagersFixture'
import type { GameScoreboard } from '../types/scoreboard'
import {
  COLLAPSE_STORAGE_KEY, activeWagers, classify, countdown, decisionsByPosition, openPositions,
  potentialPayout, readCollapsed, summarize, watchingItems, writeCollapsed,
} from './activeWagers'

const NOW = Date.parse('2026-09-24T18:00:00Z')

const score = (over: Partial<GameScoreboard>): GameScoreboard =>
  ({ schema_version: 'AISP_GAME_SCOREBOARD_V1', game_id: 'x', league_code: 'MLB', available: true, teams: null, ...over }) as GameScoreboard

function item(id: string, over: Partial<DecisionItem> = {}): DecisionItem {
  return {
    decision_id: id, sport: 'MLB', experiment: 'x', game_id: `dg-${id}`, matchup: `W${id} @ X${id}`, starts_at: '2026-09-24T22:00:00Z',
    phase: 'MORNING', market: 'MONEYLINE', selection: `W${id} ML`, line: null, sportsbook: 'Fanduel', american: 120,
    quote_observed_at: null, quote_age_minutes: null, model_probability: 0.5, market_probability: 0.45, edge: 0.03, expected_value: 0.1,
    recommendation: 'NO_BET', stored_recommendation: 'NO_BET', reason: null, recommended_at: null, decision_window: null,
    group: 'WATCHING', position: null, ...over,
  }
}

describe('grouping: live / upcoming / awaiting settlement', () => {
  it('uses observed game state, then the clock, and never guesses a started game', () => {
    expect(classify(pos('1'), undefined, null, NOW)).toEqual({ group: 'UPCOMING', statusText: 'Scheduled' })
    expect(classify(pos('1'), score({ is_live: true, status: 'IN_PROGRESS', status_detail: 'Top 3rd' }), null, NOW))
      .toEqual({ group: 'LIVE', statusText: 'Top 3rd' })
    expect(classify(pos('1'), score({ is_final: true, status: 'FINAL' }), null, NOW).group).toBe('AWAITING')
    expect(classify(pos('1'), score({ status: 'POSTPONED' }), null, NOW)).toEqual({ group: 'AWAITING', statusText: 'Postponed · settlement pending' })
    const started = pos('1', { starts_at: '2026-09-24T17:00:00Z' })
    expect(classify(started, undefined, null, NOW)).toEqual({ group: 'LIVE', statusText: 'Started · live status not available' })
    // The decision board's own flag wins even without a scoreboard reading.
    const flagged = item('d', { position: { position_id: '1', stake: 5, status: 'OPEN', result: null, profit_loss: null, awaiting_settlement: true } })
    expect(classify(started, undefined, flagged, NOW).group).toBe('AWAITING')
    // An unavailable scoreboard reading is not treated as a status.
    expect(classify(pos('1'), score({ available: false, status: 'FINAL' }), null, NOW).group).toBe('UPCOMING')
  })

  it('places every open position in exactly one group, ordered by scheduled start', () => {
    const ledger = ledgerOf({
      MLB: [pos('late', { starts_at: '2026-09-25T01:00:00Z' }), pos('early', { starts_at: '2026-09-24T19:00:00Z' }), pos('live', { starts_at: '2026-09-24T17:00:00Z' })],
      NFL: [pos('mid', { starts_at: '2026-09-24T21:00:00Z' }), pos('done', { starts_at: '2026-09-24T14:00:00Z' }), pos('old', { status: 'SETTLED', result: 'WIN', profit_loss: 7.5 })],
    })
    const groups = activeWagers(ledger, { 'g-done': score({ is_final: true, status: 'FINAL' }), 'g-live': score({ is_live: true, status: 'IN_PROGRESS' }) }, new Map(), NOW)
    expect(groups.UPCOMING.map((p) => p.position_id)).toEqual(['early', 'mid', 'late'])
    expect(groups.LIVE.map((p) => p.position_id)).toEqual(['live'])
    expect(groups.AWAITING.map((p) => p.position_id)).toEqual(['done'])
    // Settled positions leave the active list.
    expect(Object.values(groups).flat().some((p) => p.position_id === 'old')).toBe(false)
    // Global across sports: the NFL wager sits beside the MLB ones.
    expect(groups.UPCOMING.map((p) => p.sport)).toEqual(['MLB', 'NFL', 'MLB'])
  })
})

describe('dedupe and totals', () => {
  it('counts a position id once even if the ledger repeats it', () => {
    const p = pos('dup', { stake: 10 })
    const ledger = ledgerOf({ MLB: [p, { ...p }], NFL: [{ ...p }] }, { open_exposure: 10, open_count: 1 })
    expect(openPositions(ledger)).toHaveLength(1)
    const summary = summarize(ledger, activeWagers(ledger, {}, new Map(), NOW))
    expect(summary.openCount).toBe(1)
    expect(summary.stakeAtRisk).toBe(10)
    expect(summary.reconciled).toBe(true)
  })

  it('reconciles with the ledger totals and flags a mismatch rather than hiding it', () => {
    const ledger = ledgerOf({ MLB: [pos('a', { stake: 2.5 }), pos('b', { stake: 7.25 })] })
    const s = summarize(ledger, activeWagers(ledger, {}, new Map(), NOW))
    expect(s).toMatchObject({ openCount: 2, stakeAtRisk: 9.75, ledgerOpenExposure: 9.75, ledgerOpenCount: 2, reconciled: true, bankroll: 3969.01 })
    const off = { ...ledger, totals: { ...ledger.totals, open_exposure: 12 } }
    expect(summarize(off, activeWagers(off, {}, new Map(), NOW)).reconciled).toBe(false)
  })

  it('payout includes the returned stake; unknown price stays unknown', () => {
    expect(potentialPayout(pos('a', { stake: 5 }))).toBe(12.5)
    expect(potentialPayout(pos('a', { stake: 5, entry: { ...pos('a').entry, decimal: null, american: -125 } }))).toBe(9)
    expect(potentialPayout(pos('a', { entry: { ...pos('a').entry, decimal: null, american: null } }))).toBeNull()
  })

  it('next start is the earliest upcoming wager', () => {
    const ledger = ledgerOf({ MLB: [pos('x', { starts_at: '2026-09-24T22:00:00Z' }), pos('y', { starts_at: '2026-09-24T19:30:00Z' })] })
    const s = summarize(ledger, activeWagers(ledger, {}, new Map(), NOW))
    expect(s.nextStart).toBe('2026-09-24T19:30:00Z')
    expect(countdown(s.nextStart, NOW)).toBe('1 h 30 min')
    expect(countdown('2026-09-24T17:00:00Z', NOW)).toBeNull()
  })
})

describe('watching is separate from funded money', () => {
  it('keeps only upcoming, unfunded decisions and never counts them', () => {
    const funded = item('f', { group: 'POSITION', position: { position_id: 'a', stake: 5, status: 'OPEN', result: null, profit_loss: null } })
    const items = [
      item('w2', { starts_at: '2026-09-24T23:00:00Z' }),
      item('ready', { group: 'READY', recommendation: 'QUALIFIED_BET', starts_at: '2026-09-24T20:00:00Z' }),
      item('passed', { group: 'PASSED', phase: 'FINAL_PREGAME' }),
      item('closed', { group: 'CLOSED' }),
      item('past', { starts_at: '2026-09-24T17:00:00Z' }),
      funded,
    ]
    const watch = watchingItems(items, new Set(['a']), NOW)
    expect(watch.map((w) => w.decision_id)).toEqual(['ready', 'passed', 'w2'])
    expect(watch[0].label).toBe('Qualified · not funded')
    expect(watch[1].label).toBe('Declined at final check')

    const ledger = ledgerOf({ MLB: [pos('a', { stake: 5 })] })
    const groups = activeWagers(ledger, {}, decisionsByPosition(items), NOW)
    const s = summarize(ledger, groups)
    expect(s.openCount).toBe(1)
    expect(s.stakeAtRisk).toBe(5)
    expect(groups.UPCOMING[0].decision?.decision_id).toBe('f')
  })
})

describe('a funding-paused V18 qualifier is shadow only', () => {
  it('is listed as shadow only, never as "not funded yet", and never enters a total', () => {
    const funding_pause = {
      record_id: 'r', content_sha256: 'c'.repeat(64), verified: true, feature: 'MLB_V18_FUNDING_PAUSE_V1',
      reason: 'FUNDING_EDGE_NOT_ESTABLISHED', status: 'PENDING_PROSPECTIVE_VALIDATION', paused_at: '2026-09-24T19:50:00Z',
      qualified_edge_threshold: 0.02, policy_adjusted_edge: 0.031, position_created: false as const,
    }
    const paused = item('paused', { group: 'READY', phase: 'FINAL_PREGAME', recommendation: 'QUALIFIED_BET',
      starts_at: '2026-09-24T20:05:00Z', funding_pause })
    const [watch] = watchingItems([paused], new Set(), NOW)
    expect(watch.label).toBe('Qualified · shadow only (funding paused)')
    expect(watch.label).not.toMatch(/not funded/)
    expect(watch.tone).toBe('')

    const ledger = ledgerOf({ MLB: [] })
    const groups = activeWagers(ledger, {}, decisionsByPosition([paused]), NOW)
    const s = summarize(ledger, groups)
    expect(s.openCount).toBe(0)
    expect(s.stakeAtRisk).toBe(0)
  })
})

describe('collapse persistence', () => {
  it('round-trips through localStorage and survives a throwing storage', () => {
    writeCollapsed(true)
    expect(window.localStorage.getItem(COLLAPSE_STORAGE_KEY)).toBe('1')
    expect(readCollapsed()).toBe(true)
    writeCollapsed(false)
    expect(readCollapsed()).toBe(false)
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage')!
    Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('blocked') } })
    try {
      expect(() => writeCollapsed(true)).not.toThrow()
      expect(readCollapsed()).toBe(false)
    } finally {
      Object.defineProperty(window, 'localStorage', original)
    }
  })
})
