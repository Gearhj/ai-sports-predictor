/**
 * The official final is shown beside a scoreboard reading, never merged into it.
 *
 * Two real cases from 2026-09-14:
 *  - DEN at KC was officially final 10-31 and settled, while the score slot said
 *    "No score has been collected for this game yet" (no NFL live scoreboard).
 *  - MIA at AZ's scoreboard stopped at 7-4 in the 7th with a LIVE chip; the
 *    official final was 7-8.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { GameScoreboard } from './GameScoreboard'
import type { GameScoreboard as Scoreboard, GovernedFinal } from '../../types/scoreboard'

afterEach(cleanup)

const final = (away: number, home: number, source: string): GovernedFinal => ({
  away_score: away, home_score: home, final_at: '2026-09-15T04:45:27Z',
  evidence_observation_id: 'obs-1', evidence_source: source,
  first_confirmed_at: '2026-09-15T05:03:28Z', label: 'Official final · governed record',
})

describe('governed final beside the scoreboard', () => {
  it('shows the official NFL final where no scoreboard was collected', () => {
    const game = {
      schema_version: 'AISP_GAME_SCOREBOARD_V1', game_id: 'den-kc', league_code: 'NFL',
      available: false,
      unavailable_reason: 'No live scoreboard was collected for this game; the official final is shown from the governed record.',
      teams: { away: { abbreviation: 'DEN', name: 'Denver' }, home: { abbreviation: 'KC', name: 'Kansas City' } },
      governed_final: final(10, 31, 'NFLVERSE'),
    } as unknown as Scoreboard
    render(<GameScoreboard game={game} compact />)

    const line = screen.getByTestId('scoreboard-governed-final')
    expect(line.textContent).toContain('DEN')
    expect(line.textContent).toContain('10')
    expect(line.textContent).toContain('31')
    expect(line.textContent).toContain('Official final · governed record')
    expect(screen.queryByText('No score has been collected for this game yet.')).toBeNull()
  })

  it('keeps a stale scoreboard reading as history and drops the LIVE claim', () => {
    const game = {
      schema_version: 'AISP_GAME_SCOREBOARD_V1', game_id: 'mia-az', league_code: 'MLB',
      available: true,
      teams: { away: { abbreviation: 'MIA', name: 'Miami' }, home: { abbreviation: 'AZ', name: 'Arizona' } },
      status: 'IN_PROGRESS', status_detail: 'In Progress', is_final: false, is_live: true,
      away_score: 7, home_score: 4,
      period: { number: 7, label: '7th', state: 'Top', in_break: false },
      situation: { outs: 1, balls: 0, strikes: 0, bases_occupied: [] },
      governed_final: final(7, 8, 'MLB_STATS_API'),
      scoreboard_behind_final: true,
    } as unknown as Scoreboard
    render(<GameScoreboard game={game} compact />)

    expect(screen.getByTestId('scoreboard-governed-final').textContent).toContain('8')
    expect(screen.getByTestId('scoreboard-superseded').textContent)
      .toContain('Last scoreboard reading, before the final')
    expect(screen.queryByTestId('scoreboard-live')).toBeNull()
    expect(screen.queryByTestId('scoreboard-situation')).toBeNull()
  })

  it('changes nothing for a game whose scoreboard already reached the final', () => {
    const game = {
      schema_version: 'AISP_GAME_SCOREBOARD_V1', game_id: 'cws-cle', league_code: 'MLB',
      available: true,
      teams: { away: { abbreviation: 'CWS', name: 'Chicago' }, home: { abbreviation: 'CLE', name: 'Cleveland' } },
      status: 'FINAL', status_detail: 'Final', is_final: true, is_live: false,
      away_score: 7, home_score: 3, period: { number: 9, label: '9th', state: 'Bottom', in_break: false },
      governed_final: final(7, 3, 'MLB_STATS_API'), scoreboard_behind_final: false,
    } as unknown as Scoreboard
    render(<GameScoreboard game={game} compact />)

    expect(screen.queryByTestId('scoreboard-governed-final')).toBeNull()
    expect(screen.queryByTestId('scoreboard-superseded')).toBeNull()
  })
})
