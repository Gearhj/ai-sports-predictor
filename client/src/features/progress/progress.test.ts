import { describe, expect, it } from 'vitest'
import type { PreviewPick } from '../../services/previewPicks'
import type { ArenaCall } from '../arena/arenaStore'
import { computeProgress, pageKey, streakOf } from './progress'

const stored = (pages: string[], days: string[]) => ({ pages: Object.fromEntries(pages.map((p) => [p, 'x'])), days, seenBadges: [], seenLevel: 1 })

describe('site progression', () => {
  it('folds game pages into one page key', () => {
    expect(pageKey('/games/abc-123')).toBe('/games/:id')
    expect(pageKey('/nba/games/xyz/')).toBe('/nba/games/:id')
    expect(pageKey('/')).toBe('/')
    expect(pageKey('/arena/')).toBe('/arena')
  })
  it('counts day streaks, current only if it reaches today or yesterday', () => {
    expect(streakOf(['2026-10-24', '2026-10-25', '2026-10-26'], '2026-10-26')).toEqual({ current: 3, best: 3 })
    expect(streakOf(['2026-10-20', '2026-10-21', '2026-10-25'], '2026-10-26')).toEqual({ current: 1, best: 2 })
    expect(streakOf(['2026-10-20'], '2026-10-26')).toEqual({ current: 0, best: 1 })
    expect(streakOf([], '2026-10-26')).toEqual({ current: 0, best: 0 })
  })
  it('adds XP from pages, days, locked picks and the Arena', () => {
    const call: ArenaCall = { game_id: 'g', sport: 'NBA', away: 'A', home: 'H', starts_at: '2026-10-26T23:00:00Z', side: 'HOME', power: 2, blind: false, mode: 'LIVE', at: 'x', result: { winner: 'HOME', aisp: 'HOME', at: 'x' } }
    const picks = [{ status: 'locked' }, { status: 'slip' }] as PreviewPick[]
    const p = computeProgress(stored(['/', '/live', '/mission-control', '/decisions', '/methodology'], ['2026-10-25', '2026-10-26']), [call], picks, '2026-10-26')
    expect(p.xp).toBe(200 + 5 * 25 + 2 * 50 + 50 + 2 * 50) // + two Rookie Quests (Live View, Daily Decisions)
    expect(p.level).toBe(2)
    const got = p.badges.filter((b) => b.got).map((b) => b.id)
    expect(got).toEqual(expect.arrayContaining(['first', 'locked', 'glassbox', 'film']))
    expect(got).not.toContain('explorer')
  })
})
