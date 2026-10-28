import { describe, expect, it } from 'vitest'
import type { MissionGame } from '../mission/missionData'
import { litGates, readGame } from './verdict'

const NOW = Date.parse('2026-10-26T12:00:00Z')
const done = (code: string) => ({ state: 'DONE' as const, code })
const game = (over: Partial<MissionGame> & { stages: MissionGame['stages'] }): MissionGame => ({
  game_id: 'g', sport: 'NFL', matchup: 'A @ B', away: { abbr: 'A' }, home: { abbr: 'B' }, starts_at: '2026-10-25T17:00:00Z', status: 'final', phase: 'FINAL', ...over,
})

describe('decision machine verdicts (10-25 cases)', () => {
  it('a recorded position is a BET with all five gates lit', () => {
    const g = game({ stages: { forecast: done('PUBLISHED'), approval: done('OK'), prices: done('OK'), decision: { state: 'DONE', class: 'BET', reason_code: 'ALL_GATES_PASSED' }, bet: { state: 'DONE', code: 'POSITION_RECORDED', position_id: 'p' } } })
    const r = readGame(g, NOW)
    expect(r.verdict).toBe('BET'); expect(litGates(g, r)).toBe(5)
  })
  it('NYI@DAL: a $0.88 position voided by the $1 floor is YOUR RULE at the bet gate, not a bet', () => {
    const g = game({ sport: 'NHL', stages: { forecast: done('P'), approval: done('OK'), prices: done('OK'), decision: { state: 'DONE', class: 'BET' }, bet: { state: 'DONE', code: 'POSITION_RECORDED', position_id: 'p', stake_usd: 0.88, status: 'SETTLED' }, result: { state: 'DONE', code: 'VOID' } } })
    const r = readGame(g, NOW)
    expect(r.verdict).toBe('YOUR_RULE'); expect(r.stoppedAt).toBe('bet'); expect(r.reason).toMatch(/\$0\.88.*under \$1/); expect(litGates(g, r)).toBe(4)
  })
  it('CIN@BAL: forecast in, approval never imported = AISP FAILED at the approval gate', () => {
    const g = game({ stages: { forecast: done('PUBLISHED'), approval: { state: 'BLOCKED', code: 'NO_PAPER_RESEARCH_ELIGIBILITY_RECORD' }, prices: done('OK'), decision: { state: 'BLOCKED', class: 'BLOCKED', reason_code: 'NO_PAPER_RESEARCH_ELIGIBILITY_RECORD' } } })
    const r = readGame(g, NOW)
    expect(r.verdict).toBe('FAILED'); expect(r.stoppedAt).toBe('approval'); expect(r.reason).toMatch(/approval record never imported/); expect(litGates(g, r)).toBe(1)
  })
  it('GB@DET: stale quote = AISP FAILED at the price gate', () => {
    const r = readGame(game({ stages: { forecast: done('P'), approval: done('OK'), prices: { state: 'BLOCKED', code: 'QUOTE_TOO_STALE' }, decision: { state: 'DONE', class: 'NO_PRICE', reason_code: 'QUOTE_TOO_STALE' } } }), NOW)
    expect(r.verdict).toBe('FAILED'); expect(r.stoppedAt).toBe('prices'); expect(r.reason).toMatch(/fresh betting price/)
  })
  it('LAC@OKC: the >10-point gap rule is YOUR RULE, not a failure', () => {
    const r = readGame(game({ sport: 'NBA', stages: { forecast: done('P'), approval: done('OK'), prices: done('OK'), decision: { state: 'DONE', class: 'RECORD_ONLY' as never, reason_code: 'OWNER_GAP_GT_10_RECORD_ONLY' } } }), NOW)
    expect(r.verdict).toBe('YOUR_RULE'); expect(r.stoppedAt).toBe('decision'); expect(r.reason).toMatch(/your record-only rule/)
  })
  it('no edge is AISP working; a started game with no decision is a failure; a future one waits', () => {
    expect(readGame(game({ stages: { decision: { state: 'DONE', class: 'PASS', reason_code: 'NO_QUALIFYING_EDGE' } } }), NOW).verdict).toBe('NO_EDGE')
    const never = readGame(game({ stages: { forecast: done('P'), approval: done('OK'), prices: done('OK'), decision: { state: 'WAITING', code: 'WINDOW_NOT_OPEN' } } }), NOW)
    expect(never.verdict).toBe('FAILED'); expect(never.stoppedAt).toBe('decision')
    const later = readGame(game({ starts_at: '2026-10-27T00:15:00Z', status: 'scheduled', phase: 'PREGAME', stages: { forecast: { state: 'WAITING' }, decision: { state: 'WAITING' } } }), NOW)
    expect(later.verdict).toBe('PENDING'); expect(later.stoppedAt).toBe('forecast')
  })
})
