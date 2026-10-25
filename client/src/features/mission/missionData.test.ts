import { describe, expect, it } from 'vitest'
import { missionFixture, missionGames } from '../../test/missionFixture'
import {
  asSportFilter, bankrollStrip, buyWindowText, chipAria, chipLabel, countdownText, forecastAgeSuffix, isNetworkFailure, showsTeamMark, tickPeriod, filterBySport, freshnessClock, isLive, isSwitchedOff, jobSummary, minutesText, whenText, liveScoreState,
  missionKey, nextStepLine, normalizeState, progressIndex, sinceText, sortRows, sportCounts, stageOf, stageTone,
  type MissionGame,
} from './missionData'

const NOW = Date.parse('2026-10-17T22:00:00Z')
const at = (minutes: number, seconds = 0) => new Date(NOW + minutes * 60_000 + seconds * 1000).toISOString()

describe('stage states', () => {
  it('maps every server state to its tone', () => {
    expect(stageTone('DONE')).toBe('done')
    expect(stageTone('WAITING')).toBe('waiting')
    expect(stageTone('BLOCKED')).toBe('blocked')
    expect(stageTone('NOT_APPLICABLE')).toBe('na')
    expect(stageTone('UNKNOWN')).toBe('unknown')
  })

  it('never turns an unrecognised or missing state into done', () => {
    for (const value of ['SUCCESS', 'done', '', null, undefined, 1, {}]) {
      expect(normalizeState(value)).toBe('UNKNOWN')
      expect(stageTone(value)).toBe('unknown')
    }
  })

  it('gives a stage the server left out an explicit UNKNOWN placeholder', () => {
    const nba = missionGames(NOW).find((g) => g.sport === 'NBA')!
    const bet = stageOf(nba, 'bet')
    expect(bet.state).toBe('UNKNOWN')
    expect(chipLabel('bet', bet)).toBe('Not reported')
    expect(chipAria('bet', bet)).toBe('Bet: unknown — AISP did not report the bet stage for this game.')
    // An unknown state string is normalised, keeping its words.
    const approval = stageOf(nba, 'approval')
    expect(approval.state).toBe('UNKNOWN')
    expect(chipAria('approval', approval)).toBe('Approval: unknown — A state this client does not know.')
  })

  it('falls back to plain chip text that claims nothing it was not told', () => {
    expect(chipLabel('prices', { state: 'DONE' })).toBe('Prices')
    expect(chipLabel('prices', { state: 'WAITING', due_at: '2026-10-17T23:45:00Z' })).toBe('Due 7:45 PM')
    expect(chipLabel('prices', { state: 'WAITING' })).toBe('Waiting')
    expect(chipLabel('approval', { state: 'BLOCKED' })).toBe('Blocked')
    expect(chipLabel('live', { state: 'NOT_APPLICABLE' })).toBe('n/a')
    expect(chipLabel('result', { state: 'UNKNOWN' })).toBe('Unknown')
    expect(chipAria('approval', { state: 'BLOCKED', detail: 'Lease expired at 11 AM.' })).toBe('Approval: blocked — Lease expired at 11 AM.')
  })
})

describe('countdown', () => {
  it('reads like a glance at every range', () => {
    expect(countdownText(at(0, 45), NOW)).toBe('45 s')
    expect(countdownText(at(4, 32), NOW)).toBe('4:32')
    expect(countdownText(at(12), NOW)).toBe('12 min')
    expect(countdownText(at(125), NOW)).toBe('2 h 05 min')
    expect(countdownText(at(27 * 60), NOW)).toBe('1d 3h')
  })

  it('returns null for a clock that has passed or is missing', () => {
    expect(countdownText(at(-1), NOW)).toBeNull()
    expect(countdownText(null, NOW)).toBeNull()
    expect(countdownText('not a date', NOW)).toBeNull()
  })

  it('builds the next-step headline from the server label', () => {
    expect(nextStepLine({ stage: 'DECISION', kind: 'WAIT', at: at(12), label: 'Decision in', text: 'Decision window opens 7:30 PM ET' }, NOW))
      .toEqual({ headline: 'Decision in 12 min', sub: 'Decision window opens 7:30 PM ET', overdue: false })
    expect(nextStepLine({ stage: 'BET', kind: 'WAIT', at: at(65), label: 'Puck drop' }, NOW).headline).toBe('Puck drop in 1 h 05 min')
    expect(nextStepLine({ stage: 'RESULT', kind: 'WAIT', at: at(-20), label: 'Settlement in' }, NOW))
      .toMatchObject({ headline: 'Settlement · due 20 min ago', overdue: true })
    expect(nextStepLine({ stage: 'APPROVAL', kind: 'BLOCKED', label: 'Lease expired', text: 'The lease expired.' }, NOW))
      .toEqual({ headline: 'Blocked · Lease expired', sub: 'The lease expired.', overdue: false })
    expect(nextStepLine({ stage: 'LIVE', kind: 'WATCH', label: null, text: null }, NOW).headline).toBe('Watching live')
    expect(nextStepLine({ stage: 'DONE', kind: 'DONE', label: 'W +$2.40' }, NOW).headline).toBe('W +$2.40')
    expect(nextStepLine(null, NOW).headline).toBe('Next step not reported')
    expect(nextStepLine({ stage: 'DECISION', kind: 'WAIT', at: null, label: 'Decision in', text: 'Waiting on prices' }, NOW))
      .toEqual({ headline: 'Decision', sub: 'Waiting on prices', overdue: false })
  })

  it('says how long ago something happened', () => {
    expect(sinceText(NOW - 40_000, NOW)).toBe('40 s')
    expect(sinceText(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe('12 min')
    expect(sinceText(NOW - 3 * 3_600_000, NOW)).toBe('3 h')
    expect(sinceText(null, NOW)).toBeNull()
  })
})

describe('rows', () => {
  const games = missionGames(NOW)

  it('sorts live first, then by the next clock, then finished games', () => {
    expect(sortRows(games).map((g) => g.sport)).toEqual(['MLB', 'NHL', 'CFB', 'NBA', 'NFL'])
  })

  it('filters by sport, NHL included', () => {
    expect(filterBySport(games, 'NHL').map((g) => g.matchup)).toEqual(['BOS @ TOR'])
    expect(filterBySport(games, 'ALL')).toHaveLength(5)
    expect(asSportFilter('nhl')).toBe('NHL')
    expect(asSportFilter('ALL')).toBe('ALL')
    expect(asSportFilter('WNBA')).toBe('ALL')
    expect(asSportFilter(null)).toBe('ALL')
  })

  it('knows when a game is live, never after it ends', () => {
    expect(games.filter(isLive).map((g) => g.sport)).toEqual(['MLB'])
    const ended: MissionGame = { ...games[2], phase: 'FINAL' }
    expect(isLive(ended)).toBe(false)
  })

  it('tracks pipeline progress by the last DONE stage', () => {
    const [nhl, cfb, mlb, nfl, nba] = games
    expect(progressIndex(nhl)).toBe(1)
    expect(progressIndex(cfb)).toBe(2)
    expect(progressIndex(mlb)).toBe(5)
    expect(progressIndex(nfl)).toBe(6)
    expect(progressIndex(nba)).toBe(-1)
  })

  it('counts sports for the filter chips and flags a failed read', () => {
    const data = missionFixture(NOW)
    expect(sportCounts(data).map((c) => `${c.sport}:${c.games}`)).toEqual(['MLB:1', 'NFL:1', 'CFB:1', 'NHL:1', 'NBA:1'])
    const failed = missionFixture(NOW, {
      games: games.filter((g) => g.sport !== 'CFB'),
      sports: [{ sport: 'CFB', label: 'CFB', games: 0, read_state: 'READ_FAILED', error_type: 'OperationalError' }],
    })
    expect(sportCounts(failed).find((c) => c.sport === 'CFB')).toEqual({ sport: 'CFB', games: 0, failed: true })
    expect(sportCounts(null)).toEqual([])
  })

  it('lists five bankrolls, NHL with its $1,000, and a missing sport as missing', () => {
    const strip = bankrollStrip(missionFixture(NOW))
    expect(strip.map((b) => b.sport)).toEqual(['MLB', 'NFL', 'CFB', 'NHL', 'NBA'])
    const nhl = strip[3]
    expect('missing' in nhl).toBe(false)
    expect(!('missing' in nhl) && nhl.starting_usd).toBe(1000)
    expect('missing' in strip[4]).toBe(true)
    expect(bankrollStrip(null).every((b) => 'missing' in b)).toBe(true)
  })

  it('summarises job health', () => {
    // The switched-off NBA lane (NOT_REGISTERED, expected false) is counted as off, not as a fault.
    expect(jobSummary(missionFixture(NOW).health.jobs)).toMatchObject({ total: 4, ok: 3, running: 1, failed: 1, stale: 0, unregistered: 0, off: 1 })
    const jobs = missionFixture(NOW).health.jobs
    // A job the server did not classify (no `expected`) is treated as expected: a missing field never hides a fault.
    const unclassified = jobs.map((j) => j.key === 'nba-decisions' ? { ...j, expected: undefined } : j)
    expect(jobSummary(unclassified)).toMatchObject({ total: 5, unregistered: 1, off: 0 })
    expect(isSwitchedOff(jobs.find((j) => j.key === 'nba-decisions')!)).toBe(true)
    expect(isSwitchedOff({ ...jobs[0], state: 'NOT_REGISTERED', expected: true })).toBe(false)
    expect(jobSummary(undefined).total).toBe(0)
  })

  it('reads a live block as a score only when one is available', () => {
    expect(liveScoreState({ available: true, is_live: true, away_score: 3, home_score: 2, period_label: 'Bot 7th' }))
      .toEqual({ phase: 'LIVE', away: 3, home: 2, detail: 'Bot 7th' })
    expect(liveScoreState({ available: true, is_final: true, away_score: 20, home_score: 27 }).phase).toBe('FINAL')
    expect(liveScoreState({ available: false, is_live: true, away_score: null, home_score: null })).toMatchObject({ away: null, home: null })
    expect(liveScoreState(null).phase).toBe('PRE')
  })

  it('keys the cache by day', () => {
    expect(missionKey('2026-10-17')).toBe('mission-control?day=2026-10-17')
    expect(missionKey('2026-10-18')).not.toBe(missionKey('2026-10-17'))
  })
})

describe('freshness clock', () => {
  it('takes the older of the header clock and the body clock', () => {
    expect(freshnessClock(NOW, new Date(NOW - 20_000).toISOString())).toBe(NOW - 20_000)
    expect(freshnessClock(NOW - 60_000, new Date(NOW).toISOString())).toBe(NOW - 60_000)
    expect(freshnessClock(null, new Date(NOW).toISOString())).toBe(NOW)
    expect(freshnessClock(NOW, null)).toBe(NOW)
    expect(freshnessClock(null, 'garbage')).toBeNull()
  })
})

describe('detail clocks and ages', () => {
  const now = Date.parse('2026-10-17T04:00:00Z')
  it('names a weekday only within three days, else the date', () => {
    expect(whenText('2026-10-17T15:30:00Z', now)).toBe('Sat 11:30 AM ET')
    // Six days back: "Sun" alone would read as tomorrow.
    expect(whenText('2026-10-11T05:12:00Z', now)).toBe('Oct 11, 1:12 AM ET')
    expect(whenText(null, now)).toBeNull()
    expect(whenText('not a date', now)).toBeNull()
  })
  it('says ages in the unit that reads at a glance', () => {
    expect(minutesText(12.4)).toBe('12 min')
    expect(minutesText(328)).toBe('5.5 h')
    expect(minutesText(900)).toBe('15 h')
    expect(minutesText(8581)).toBe('6 d')
    expect(minutesText(null)).toBeNull()
  })
})

describe('review fixes', () => {
  it('ticks a countdown every second only inside its last eleven minutes', () => {
    expect(tickPeriod(NOW + 5 * 60_000, NOW)).toBe(1000)
    expect(tickPeriod(NOW + 40 * 60_000, NOW)).toBe(15_000)
    // Overdue rows no longer re-render every second for ever.
    expect(tickPeriod(NOW - 3 * 60_000, NOW)).toBe(30_000)
    expect(tickPeriod(Number.NaN, NOW)).toBe(3_600_000)
  })

  it('writes a buy window with no closing clock as "from"', () => {
    expect(buyWindowText('2026-10-17T20:10:00Z', null)).toBe('from 4:10 PM ET')
    expect(buyWindowText('2026-10-17T20:10:00Z', '2026-10-17T20:50:00Z')).toBe('4:10 PM–4:50 PM ET')
    expect(buyWindowText(null, null)).toBeNull()
  })

  it('ages a forecast on its chip unless it is fresh', () => {
    expect(forecastAgeSuffix({ state: 'DONE', freshness: 'STALE', age_minutes: 8213 })).toBe('6 d')
    expect(forecastAgeSuffix({ state: 'DONE', freshness: 'AGING', age_minutes: 1800 })).toBe('30 h')
    expect(forecastAgeSuffix({ state: 'DONE', freshness: 'FRESH', age_minutes: 60 })).toBeNull()
    expect(forecastAgeSuffix({ state: 'DONE', freshness: null })).toBeNull()
  })

  it('calls the backend unreachable only for a request that never reached it', () => {
    expect(isNetworkFailure('Failed to fetch')).toBe(true)
    expect(isNetworkFailure('NetworkError when attempting to fetch resource.')).toBe(true)
    expect(isNetworkFailure('Load failed')).toBe(true)
    expect(isNetworkFailure('Request failed (503)')).toBe(false)
    expect(isNetworkFailure('Unexpected response (expected AISP_MISSION_CONTROL_V1)')).toBe(false)
    expect(isNetworkFailure(null)).toBe(false)
  })

  it('draws a team mark only for a short code', () => {
    expect(showsTeamMark('TOR')).toBe(true)
    expect(showsTeamMark('MICH')).toBe(true)
    expect(showsTeamMark('Western Michigan')).toBe(false)
    expect(showsTeamMark(null)).toBe(false)
  })
})
