import { beforeEach, describe, expect, it } from 'vitest'
import { logPredictions, readLog, scorecard, settle, valueSide } from './modelLog'

const NOW = Date.parse('2026-10-27T16:00:00Z')
const e = (id: string, you: number, aisp: number, market: number | null, value: ReturnType<typeof valueSide> = null, version = 1) => ({
  game_id: id, sport: 'NHL', matchup: 'A @ H', home: 'H', away: 'A', starts_at: '2026-10-27T23:00:00Z', version, you, aisp, market, value,
})

describe('my model track record', () => {
  beforeEach(() => localStorage.removeItem('aisp-my-model-log-v1'))
  it('logs only before the start and only once per game and version', () => {
    expect(logPredictions([e('g1', 0.6, 0.55, 0.5)], NOW)).toBe(1)
    expect(logPredictions([e('g1', 0.7, 0.55, 0.5)], NOW)).toBe(0)                       // first write wins
    expect(logPredictions([e('g1', 0.7, 0.55, 0.5, null, 2)], NOW)).toBe(1)               // a new version is a new entry
    expect(logPredictions([e('g2', 0.6, 0.55, 0.5)], Date.parse('2026-10-28T00:00:00Z'))).toBe(0)   // after the start
    expect(readLog()).toHaveLength(2)
  })
  it('settles and scores you, AISP and the market head to head, plus flat value plays', () => {
    logPredictions([e('g1', 0.7, 0.6, 0.5, { side: 'HOME', american: 100, book: 'FanDuel', ev: 0.4 }), e('g2', 0.4, 0.6, 0.55)], NOW)
    expect(settle(new Map([['g1', true], ['g2', false]]), NOW)).toBe(2)
    const s = scorecard()
    expect(s.n).toBe(2)
    expect(s.you.hits).toBe(2); expect(s.aisp.hits).toBe(1)
    expect(s.you.logLoss).toBeLessThan(s.aisp.logLoss)
    expect(s.value).toMatchObject({ bets: 1, wins: 1, pl: 10, staked: 10 })
  })
  it('value side is the side your number prices above the book', () => {
    expect(valueSide(0.6, { HOME: { american: 110, book: 'DK' }, AWAY: { american: -130, book: 'DK' } })?.side).toBe('HOME')
    expect(valueSide(0.5, { HOME: { american: -150, book: 'DK' }, AWAY: { american: 120, book: 'DK' } })?.side).toBe('AWAY')
    expect(valueSide(0.6, { HOME: { american: -200, book: 'DK' }, AWAY: null })).toBeNull()
  })
})
