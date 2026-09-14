/**
 * The slate board's job is to show the score of every game, unconditionally.
 *
 * THE DEFECT THIS LOCKS OUT
 * ------------------------
 * The only scoreboard on Daily Decisions used to live inside a recorded
 * selection row and was additionally skipped unless the game had started. Under
 * a moneyline-only funded policy most days record no selection at all, so on
 * most days the page showed no score for any game — including games that had
 * finished hours earlier. A score must not depend on a forecast, a quote, a
 * qualifying edge or a recorded wager, and these tests assert exactly that by
 * supplying a slate with none of them.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SlateBoard } from './SlateBoard'
import type { SlateGame, SlateStatus } from '../../types/slate'
import type { GameScoreboard as Scoreboard } from '../../types/scoreboard'

function slateGame(over: Partial<SlateGame> = {}): SlateGame {
  return {
    game_id: 'g-col-det',
    league_code: 'MLB',
    teams: { away: 'COL', home: 'DET' },
    starts_at: '2026-09-13T16:10:00Z',
    game_status: 'GameStatus.FINAL',
    // Deliberately empty: none of these may gate the score.
    forecast: null,
    quote: null,
    policy: { active_for_sport: true, note: null },
    latest_evaluation: null,
    next_checkpoint: null,
    positions: 0,
    state: 'FORECAST_PENDING',
    explanation: 'No eligible forecast has been published for this game yet.',
    ...over,
  } as SlateGame
}

function status(games: SlateGame[]): SlateStatus {
  return {
    schema_version: 'AISP_SLATE_STATUS_V1',
    window: {
      date: '2026-09-13',
      timezone: 'America/New_York',
      description: 'Games starting on 2026-09-13 local time in America/New_York.',
    },
    games,
  } as unknown as SlateStatus
}

function score(over: Partial<Scoreboard> = {}): Scoreboard {
  return {
    schema_version: 'AISP_GAME_SCOREBOARD_V1',
    game_id: 'g-col-det', league_code: 'MLB', available: true,
    teams: { away: { abbreviation: 'COL', name: 'Colorado Rockies' },
             home: { abbreviation: 'DET', name: 'Detroit Tigers' } },
    status: 'FINAL', status_detail: 'Final', is_final: true, is_live: false,
    away_score: 1, home_score: 8,
    period: { number: 9, label: '9th', state: 'Top', in_break: false },
    clock_seconds_remaining: null, clock_available: false, situation: null,
    periods: [], periods_complete: true, periods_reconcile: true, periods_note: null,
    freshness: {
      state: 'SETTLED_STATE',
      detail: 'This game is final; the reading does not go stale. Last checked 15.0 hours ago.',
      checked_age_seconds: 54000,
      source_timestamp_available: false, source_latency: 'unknown',
    },
    source: {
      name: 'MLB_STATS_API', mode: 'LIVE', source_game_id: '824225',
      source_observed_at: null,
      retrieved_at: '2026-09-13T20:13:08Z', last_checked_at: '2026-09-14T01:42:54Z',
      timestamp_note: 'This source publishes no score timestamp.',
    },
    settlement_note: 'This is the observed score.',
    ...over,
  } as Scoreboard
}

afterEach(cleanup)

const noop = () => undefined

describe('scores on the slate board', () => {
  it('shows a final score on a game with no forecast, quote or position', () => {
    render(<SlateBoard slate={status([slateGame()])} filter="ALL" onFilter={noop}
      scores={{ 'g-col-det': score() }} />)
    const row = screen.getByTestId('slate-g-col-det')
    const text = row.textContent ?? ''
    // The real 2026-09-13 result.
    expect(text).toContain('COL')
    expect(text).toContain('1')
    expect(text).toContain('8')
    expect(text).toContain('DET')
    expect(text).toContain('Final')
    // And the row genuinely has none of the things that used to gate it.
    expect(text).toContain('not published yet')   // no forecast
    expect(text).toContain('none captured')       // no quote
  })

  it('says Scheduled with a start time rather than inventing 0-0', () => {
    const future = new Date(Date.now() + 6 * 3600_000).toISOString()
    render(<SlateBoard
      slate={status([slateGame({ game_id: 'g-soon', starts_at: future })])}
      filter="ALL" onFilter={noop} scores={{}} />)
    const scheduled = screen.getByTestId('slate-scheduled')
    expect(scheduled.textContent).toContain('Scheduled')
    expect(scheduled.textContent).toContain('starts')
    // The specific fabrication this must never commit.
    expect(screen.getByTestId('slate-g-soon').textContent).not.toContain('0–0')
  })

  it('says the score is unavailable for a game that should have one', () => {
    render(<SlateBoard
      slate={status([slateGame({ starts_at: '2026-09-13T16:10:00Z' })])}
      filter="ALL" onFilter={noop}
      scores={{ 'g-col-det': score({
        available: false,
        unavailable_reason: 'No score has been collected for this game yet.',
      }) }} />)
    expect(screen.getByTestId('scoreboard-unavailable').textContent)
      .toContain('No score has been collected')
  })

  it('shows a score under every filter, including Positions', () => {
    const games = [slateGame({ positions: 1, state: 'POSITION_RECORDED' })]
    for (const filter of ['ALL', 'OPPORTUNITIES', 'POSITIONS'] as const) {
      cleanup()
      render(<SlateBoard slate={status(games)} filter={filter} onFilter={noop}
        scores={{ 'g-col-det': score() }} />)
      expect(within(screen.getByTestId('slate-g-col-det')).getByTestId('scoreboard-score')
        .textContent).toContain('8')
    }
  })
})

describe('the date control', () => {
  it('is absent unless the page supplies a day and a handler', () => {
    render(<SlateBoard slate={status([slateGame()])} filter="ALL" onFilter={noop} />)
    expect(screen.queryByTestId('slate-dates')).toBeNull()
  })

  it('offers Today and Yesterday and steps a day at a time', () => {
    const onDay = vi.fn()
    render(<SlateBoard slate={status([slateGame()])} filter="ALL" onFilter={noop}
      day="2026-09-13" onDay={onDay} />)
    screen.getByTestId('slate-day-prev').click()
    expect(onDay).toHaveBeenLastCalledWith('2026-09-12')
    screen.getByTestId('slate-day-next').click()
    expect(onDay).toHaveBeenLastCalledWith('2026-09-14')
    expect(screen.getByTestId('slate-day-today')).toBeTruthy()
    expect(screen.getByTestId('slate-day-yesterday')).toBeTruthy()
  })

  it('does not step past today', () => {
    const now = new Date()
    const today = [now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0')].join('-')
    render(<SlateBoard slate={status([slateGame()])} filter="ALL" onFilter={noop}
      day={today} onDay={vi.fn()} />)
    expect(screen.getByTestId('slate-day-next').hasAttribute('disabled')).toBe(true)
  })
})
