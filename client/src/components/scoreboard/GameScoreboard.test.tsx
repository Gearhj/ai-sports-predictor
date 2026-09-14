/**
 * The scoreboard, tested on the things it must never say.
 *
 * The score itself is the easy part. What these pin down is the rest:
 *
 *  - a clock that does not exist is shown as nothing, not as 0:00;
 *  - a retrieval time is never labelled as the time the score changed;
 *  - a stale reading keeps its score and says how old it is;
 *  - an unplayed inning is a dash, not a zero;
 *  - FINAL is a fact about the game, not about a wager.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GameScoreboard, progressLabel } from './GameScoreboard'
import type { GameScoreboard as Scoreboard } from '../../types/scoreboard'

function game(over: Partial<Scoreboard> = {}): Scoreboard {
  return {
    schema_version: 'AISP_GAME_SCOREBOARD_V1',
    game_id: 'g1',
    league_code: 'MLB',
    available: true,
    teams: {
      away: { abbreviation: 'COL', name: 'Colorado Rockies' },
      home: { abbreviation: 'DET', name: 'Detroit Tigers' },
    },
    status: 'IN_PROGRESS',
    status_detail: 'In Progress',
    is_final: false,
    is_live: true,
    away_score: 1,
    home_score: 8,
    period: { number: 7, label: '7th', state: 'Top', in_break: false },
    clock_seconds_remaining: null,
    clock_available: false,
    situation: {
      outs: 2, balls: 1, strikes: 2,
      bases_occupied: ['first'],
      runners: { first: 'A Runner', second: null, third: null },
    },
    periods: [
      { number: 1, label: '1st', away: 1, home: 2, away_played: true, home_played: true },
      { number: 9, label: '9th', away: 0, home: null, away_played: true, home_played: false },
    ],
    periods_complete: true,
    periods_reconcile: true,
    periods_note: null,
    freshness: {
      state: 'CURRENT',
      detail: 'Checked 40 seconds ago.',
      checked_age_seconds: 40,
      source_timestamp_available: false,
      source_latency: 'unknown',
    },
    source: {
      name: 'MLB_STATS_API',
      mode: 'LIVE',
      source_game_id: '824225',
      source_observed_at: null,
      retrieved_at: '2026-09-14T02:10:28Z',
      last_checked_at: '2026-09-14T02:10:28Z',
      timestamp_note:
        'This source publishes no score timestamp, so AISP can show when it last '
        + 'checked but not when the score changed. Source latency is unknown.',
    },
    settlement_note:
      'This is the observed score. Whether a wager is settled is recorded '
      + 'separately and may still be pending.',
    ...over,
  }
}

afterEach(cleanup)

describe('the score line', () => {
  it('shows both clubs with their own scores', () => {
    render(<GameScoreboard game={game()} />)
    const line = screen.getByTestId('scoreboard-score').textContent ?? ''
    expect(line).toMatch(/COL/)
    expect(line).toMatch(/DET/)
    // Orientation: away first, and 1 belongs to COL.
    expect(line.indexOf('COL')).toBeLessThan(line.indexOf('DET'))
  })

  it('shows dashes rather than zeros before a game starts', () => {
    render(<GameScoreboard game={game({
      status: 'SCHEDULED', is_live: false, away_score: null, home_score: null,
    })} />)
    expect(screen.getByTestId('scoreboard-score').textContent).toMatch(/–/)
    expect(screen.queryByTestId('scoreboard-progress')).toBeNull()
  })
})

describe('the clock', () => {
  it('shows the period alone when the source publishes no clock', () => {
    render(<GameScoreboard game={game()} />)
    const progress = screen.getByTestId('scoreboard-progress').textContent ?? ''
    expect(progress).toBe('Top 7th')
    expect(progress).not.toMatch(/remaining/)
    expect(progress).not.toMatch(/0:00/)
  })

  it('shows time remaining only when a clock was actually supplied', () => {
    const withClock = game({
      league_code: 'NFL',
      period: { number: 3, label: 'Q3', state: null, in_break: false },
      clock_available: true,
      clock_seconds_remaining: 402,
    })
    expect(progressLabel(withClock)).toBe('Q3, 6:42 remaining')
  })

  it('says the clock is unavailable in the detail rather than leaving it blank', () => {
    render(<GameScoreboard game={game()} />)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    expect(screen.getByTestId('scoreboard-no-clock').textContent)
      .toMatch(/publishes no game clock/)
  })
})

describe('freshness', () => {
  it('says when it last checked, never when the score changed', () => {
    render(<GameScoreboard game={game()} />)
    expect(screen.getByTestId('scoreboard-freshness').textContent)
      .toMatch(/Checked 40 seconds ago/)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    const detail = within(screen.getByTestId('scoreboard-detail'))
    expect(detail.getByText('Score timestamp')).toBeTruthy()
    expect(detail.getByText('not published by this source')).toBeTruthy()
    expect(detail.queryByText('Score as of')).toBeNull()
  })

  it('keeps the last known score during an outage and states its age', () => {
    render(<GameScoreboard game={game({
      freshness: {
        state: 'UNAVAILABLE',
        detail: 'Updates have not reached AISP for 2.0 hours ago. The score below '
          + 'is the last one confirmed and is probably out of date.',
        checked_age_seconds: 7200,
        source_timestamp_available: false,
        source_latency: 'unknown',
      },
    })} />)
    // The score is still there.
    expect(screen.getByTestId('scoreboard-score').textContent).toMatch(/8/)
    const freshness = screen.getByTestId('scoreboard-freshness')
    expect(freshness.getAttribute('role')).toBe('note')
    expect(freshness.textContent).toMatch(/probably out of date/)
  })

  it('does not call a finished game stale', () => {
    render(<GameScoreboard game={game({
      status: 'FINAL', is_final: true, is_live: false,
      freshness: {
        state: 'SETTLED_STATE',
        detail: 'This game is final; the reading does not go stale. Last checked '
          + '249.9 hours ago.',
        checked_age_seconds: 899640,
        source_timestamp_available: false,
        source_latency: 'unknown',
      },
    })} />)
    const freshness = screen.getByTestId('scoreboard-freshness')
    expect(freshness.getAttribute('role')).toBeNull()
    expect(freshness.textContent).toMatch(/does not go stale/)
  })

  it('offers a refresh that reads AISP rather than a provider', () => {
    const onRefresh = vi.fn()
    render(<GameScoreboard game={game()} onRefresh={onRefresh} />)
    const button = screen.getByTestId('scoreboard-refresh')
    expect(button.getAttribute('title')).toMatch(/makes no provider request/)
    fireEvent.click(button)
    expect(onRefresh).toHaveBeenCalledOnce()
  })
})

describe('the period breakdown', () => {
  it('renders an unplayed half as a dash, not a zero', () => {
    render(<GameScoreboard game={game()} />)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    const table = within(screen.getByTestId('scoreboard-periods'))
    const rows = table.getAllByRole('row')
    // Header, away, home. The home ninth was not played.
    const homeCells = within(rows[2]).getAllByRole('cell')
    expect(homeCells[1].textContent).toBe('–')
    expect(homeCells[1].getAttribute('title')).toBe('Not played')
    // The away half of the same inning WAS played and was scoreless.
    const awayCells = within(rows[1]).getAllByRole('cell')
    expect(awayCells[1].textContent).toBe('0')
    expect(awayCells[1].getAttribute('title')).toBeNull()
  })

  it('names an incomplete breakdown instead of filling the gap', () => {
    render(<GameScoreboard game={game({
      periods_complete: false,
      periods_note: 'Some periods were not reported by the source. The missing '
        + 'ones are shown as unavailable rather than filled in.',
    })} />)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    expect(screen.getByTestId('scoreboard-periods-note').textContent)
      .toMatch(/rather than filled in/)
  })

  it('reports a breakdown that does not add up rather than adjusting it', () => {
    render(<GameScoreboard game={game({
      periods_reconcile: false,
      periods_note: 'The reported period scores do not add up to the reported '
        + 'final. Both are shown as published; neither was adjusted to fit.',
    })} />)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    expect(screen.getByTestId('scoreboard-periods-note').textContent)
      .toMatch(/neither was adjusted to fit/)
  })
})

describe('what it refuses to imply', () => {
  it('does not present FINAL as a settled wager', () => {
    render(<GameScoreboard game={game({ status: 'FINAL', is_final: true })} />)
    fireEvent.click(screen.getByTestId('scoreboard-toggle'))
    expect(screen.getByTestId('scoreboard-detail').textContent)
      .toMatch(/Whether a wager is settled is recorded separately/)
  })

  it('says a delay is a delay and keeps the score', () => {
    render(<GameScoreboard game={game({
      status: 'DELAYED', status_detail: 'Delayed', is_live: true,
    })} />)
    expect(screen.getByTestId('scoreboard-progress').textContent).toBe('Delayed')
    expect(screen.getByTestId('scoreboard-score').textContent).toMatch(/8/)
  })

  it('states that no score exists rather than rendering an empty one', () => {
    render(<GameScoreboard game={{
      schema_version: 'AISP_GAME_SCOREBOARD_V1', game_id: 'g2',
      league_code: 'NFL', available: false,
      unavailable_reason: 'No score has been collected for this game yet.',
      teams: null,
    }} />)
    expect(screen.getByTestId('scoreboard-unavailable').textContent)
      .toMatch(/No score has been collected/)
    expect(screen.queryByTestId('scoreboard-score')).toBeNull()
  })
})
