import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { agoText, GameSoFar, ReplayBadge, stoppageOf } from './ArenaIdle'
import type { ArenaPlay } from './arenaData'

const play = (type: string, text = ''): ArenaPlay => ({
  id: '1', seq: '1', type, abbr: null, text, short: null, period: 2, clock: '5:00', team: 'h', scoring: false, value: null, shooting: false,
  attempted: null, coord: null, away: 10, home: 12, wallclock: null, players: [], strength: null,
})

describe('stoppageOf: only plays that stop the game', () => {
  it('names timeouts, period ends, reviews, intermissions and delays', () => {
    expect(stoppageOf(play('Full Timeout'))?.label).toBe('TIMEOUT')
    expect(stoppageOf(play('Official Timeout'))?.label).toBe('OFFICIAL TIMEOUT')
    expect(stoppageOf(play('End of Period'))?.kind).toBe('end')
    expect(stoppageOf(play('Period End'))?.kind).toBe('end')
    expect(stoppageOf(play('Instant Replay', 'Coach challenge'))?.label).toBe('UNDER REVIEW')
    expect(stoppageOf(play('Stoppage', 'Intermission'))?.kind).toBe('intermission')
    expect(stoppageOf(play('Delay'))?.label).toBe('STOPPAGE')
  })
  it('a shot, a goal or nothing at all is not a stoppage', () => {
    expect(stoppageOf(play('Jump Shot', 'J. Tatum makes 26-foot three point jumper'))).toBeNull()
    expect(stoppageOf(play('Goal'))).toBeNull()
    expect(stoppageOf(null)).toBeNull()
  })
})

describe('agoText: how long ago the replayed play happened', () => {
  it('reads seconds, then minutes with seconds, then minutes', () => {
    const now = Date.parse('2026-11-01T20:00:00Z')
    expect(agoText('2026-11-01T19:59:22Z', now)).toBe('38s ago')
    expect(agoText('2026-11-01T19:58:40Z', now)).toBe('1m 20s ago')
    expect(agoText('2026-11-01T19:48:00Z', now)).toBe('12m ago')
    expect(agoText('2026-11-01T15:37:00Z', now)).toBe('4h 23m ago')
    expect(agoText(null, now)).toBeNull()
    expect(agoText('not a date', now)).toBeNull()
  })
})

describe('the badge and the board say what is happening', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())
  it('a replay is labelled as one, with whose idea it was', () => {
    render(<ReplayBadge auto when="38s ago" what="Q2 5:00" />)
    expect(screen.getByRole('status')).toHaveTextContent('INSTANT REPLAY')
    expect(screen.getByRole('status')).toHaveTextContent('Q2 5:00 · the moment, once more · 38s ago')
  })
  it('the board carries the reason and rotates its slides on a timer', () => {
    render(<GameSoFar label="LAL TIMEOUT" slides={[{ key: 'a', title: 'Shooting tonight', body: <p>A</p> }, { key: 'b', title: 'Scoring leaders', body: <p>B</p> }]} every={1000} />)
    expect(screen.getByRole('region', { name: 'Game so far' })).toHaveTextContent('LAL TIMEOUT')
    expect(screen.getByText('Shooting tonight')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByText('Scoring leaders')).toBeInTheDocument()
  })
})
