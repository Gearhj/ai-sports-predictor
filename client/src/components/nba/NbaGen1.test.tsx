/**
 * NBA Gen1 surfaces: three channels kept apart, HISTORICAL always labelled,
 * and a points-only channel never drawn as a probability. Fixture data only.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../services/api'
import { NbaChannelPanel, NbaChannelScores } from './NbaChannels'
import { NbaResearchGamePage } from '../../pages/NbaResearchGamePage'
import { modelNote } from '../../services/commandCenter'
import type { NbaChannels, NbaChannelScore, NbaGen1Game } from '../../types/nbaGen1'

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})
vi.mock('../story/GameStory', () => ({ GameStory: () => null }))

const CHANNELS: NbaChannels = {
  RIDGE_POINTS: { label: 'Ridge expected points', units: 'points (overtime included)', is_probability: false,
    home: 116.97, away: 108.45, total: 225.42, home_margin: 8.52, note: 'An expected score, not a probability.' },
  ELO_WINNER: { label: 'Elo win probability', units: 'fractions 0 to 1', is_probability: true,
    home: 0.7353426413768491, away: 0.2646573586231509, tie: 0, method: 'UNCHANGED_CHRONOLOGICAL_ELO',
    note: 'Separately estimated.' },
  JOINT_DISTRIBUTION: { label: 'Joint-distribution probabilities', units: 'fractions', is_probability: true,
    draw_count: 10000, home: 0.7294, away: 0.2706, home_mcse: 0.0044,
    expected: { home: 116.81, away: 108.34, total: 225.15, margin: 8.48 },
    intervals: { total: [{ level: 0.8, lower: 201, upper: 249 }], margin: [{ level: 0.8, lower: -9, upper: 26 }] },
    illustrative_totals: [{ line: 225, lineClassification: 'ILLUSTRATIVE_NOT_RECORDED_MARKET', drawCount: 10000,
      over: { win: 0.4915, push: 0.0237, loss: 0.4848 }, under: { win: 0.4848, push: 0.0237, loss: 0.4915 } }],
    note: 'The only channel with a distribution.' },
}

const score = (channel: NbaChannelScore['channel'], extra: Partial<NbaChannelScore>): NbaChannelScore => ({
  evidence_class: 'A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION', channel, channel_label: channel,
  forecast_identity: 'historical:x', result_source: 'ESPN_SPORTSDATAVERSE',
  final: { home: 125, away: 124, overtime: true }, home_win_probability: null, probability_of_actual: null,
  log_loss: null, two_way_brier: null, winner_correct: null, predicted: { home: null, away: null },
  home_abs_error: null, away_abs_error: null, margin_error: null, total_error: null, crps: null,
  interval_coverage: null, ...extra,
})

const SCORES: NbaChannelScore[] = [
  score('RIDGE_POINTS', { predicted: { home: 116.97, away: 108.45 }, home_abs_error: 8.03, away_abs_error: 15.55,
    margin_error: 7.52, total_error: -23.58 }),
  score('ELO_WINNER', { home_win_probability: 0.7353, log_loss: 0.3074, two_way_brier: 0.0700, winner_correct: true }),
  score('JOINT_DISTRIBUTION', { home_win_probability: 0.7294, log_loss: 0.3155, two_way_brier: 0.0732, winner_correct: true,
    crps: { home: 4.98, away: 10.06, margin: 4.82, total: 15.17 } }),
]

afterEach(() => { cleanup(); vi.mocked(apiRequest).mockReset() })

describe('NbaChannelPanel', () => {
  it('keeps points, Elo and joint in separate sections and never puts a percentage on Ridge', () => {
    render(<NbaChannelPanel channels={CHANNELS} home="OKC" away="HOU" />)
    const ridge = screen.getByTestId('nba-channel-ridge')
    expect(within(ridge).getByText('117.0')).toBeInTheDocument()
    expect(ridge.textContent).not.toMatch(/%/)
    expect(within(screen.getByTestId('nba-channel-elo')).getByText('73.5%')).toBeInTheDocument()
    expect(within(screen.getByTestId('nba-channel-joint')).getByText('72.9%')).toBeInTheDocument()
    expect(screen.getByTestId('nba-illustrative-totals').textContent).toMatch(/not prices/)
  })

  it('scores each channel on its own terms', () => {
    render(<NbaChannelScores scores={SCORES} />)
    const ridge = screen.getByTestId('nba-score-RIDGE_POINTS')
    expect(ridge.textContent).toMatch(/n\/a · points/)
    expect(ridge.textContent).not.toMatch(/Correct|Missed/)
    expect(screen.getByTestId('nba-score-ELO_WINNER').textContent).toMatch(/0\.307/)
    expect(screen.getByTestId('nba-score-JOINT_DISTRIBUTION').textContent).toMatch(/15\.17/)
  })
})

describe('modelNote', () => {
  it('names the joint probability and the Ridge points as two channels for NBA', () => {
    const note = modelNote({ engine_id: 'NBA_GEN1_PRODUCTION', home: 0.68, away: 0.32, home_two_way: 0.68,
      expected_home: 117.1, expected_away: 111.1, created_at: null, controlling_channel: 'JOINT_DISTRIBUTION',
      expected_points_channel: 'RIDGE_POINTS' })
    expect(note).toBe('joint · Ridge 111.1–117.1 pts')
    expect(modelNote({ engine_id: 'NBA_GEN1_HISTORICAL', home: 0.7, away: 0.3, home_two_way: 0.7, created_at: null,
      controlling_channel: 'JOINT_DISTRIBUTION', evidence_class: 'A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION' }))
      .toBe('joint · HISTORICAL')
    expect(modelNote({ engine_id: 'MLB_V18_WHOLE_GAME', home: 0.5, away: 0.5, home_two_way: 0.5, created_at: null,
      expected_home: 4.2, expected_away: 3.9 })).toBe('3.9–4.2 proj.')
  })
})

const GAME: NbaGen1Game = {
  schema_version: 'AISP_NBA_GEN1_GAME_V1', game_id: 'g-1', espn_game_id: '401809243',
  codex_game_id: 'nba-espn:game:401809243',
  teams: { home: { abbreviation: 'OKC', name: 'Oklahoma City Thunder' }, away: { abbreviation: 'HOU', name: 'Houston Rockets' } },
  tipoff_utc: '2025-10-21T23:30:00+00:00', status: 'FINAL', final: { home: 125, away: 124 },
  production_forecasts: [],
  historical_forecasts: [{ codex_game_id: 'nba-espn:game:401809243',
    evidence_class: 'A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION', label: 'HISTORICAL',
    assessment_receipt_sha256: '26125dfc'.padEnd(64, '0'), joint_receipt_sha256: 'd62e3efb'.padEnd(64, '0'),
    joint_counts_int64_le_sha256: '92a2ab66'.padEnd(64, '0'), point_model: { id: 'ridge_a100_rest', sha256: '7'.repeat(64) },
    elo_model_sha256: 'e'.repeat(64), game_date: '2025-10-21', qualification: 'Category A reconstruction.',
    channels: CHANNELS }],
  finals: [{ source: 'ESPN_SPORTSDATAVERSE', home: 125, away: 124, periods: 6, overtime: true,
    source_object_sha256: 'b3884aa4'.padEnd(64, '0'), captured_at_utc: '2026-09-14T12:52:46Z',
    completion_clock_status: 'ABSENT_FROM_RETAINED_SOURCE_SCHEMA', status: 'STATUS_FINAL' }],
  evaluations: SCORES,
  price: { reason: 'LANE_DISABLED', detail: 'The NBA SportsGameOdds lane is off.' },
  limitations: [], authority: { research_only: true, wagering_authority: false, note: 'none' },
}

describe('NbaResearchGamePage', () => {
  it('labels a 2025-26 row HISTORICAL and shows its final and per-channel scores', async () => {
    vi.mocked(apiRequest).mockImplementation(async (path: string) => {
      if (path.startsWith('/nba/gen1/games/')) return GAME as never
      return { schema_version: 'AISP_NBA_RESEARCH_FORECASTS_V1', forecasts: [], note: '' } as never
    })
    render(
      <MemoryRouter initialEntries={['/nba/games/g-1']}>
        <Routes><Route path="/nba/games/:gameId" element={<NbaResearchGamePage />} /></Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('nba-gen1-game')).toBeInTheDocument())
    expect(screen.getByTestId('nba-evidence-badge').textContent).toMatch(/HISTORICAL/)
    expect(screen.getByTestId('nba-final').textContent).toMatch(/HOU 124 – OKC 125/)
    expect(screen.getByTestId('nba-score-RIDGE_POINTS').textContent).toMatch(/n\/a · points/)
    expect(screen.getByTestId('nba-price-note').textContent).toMatch(/lane is off/)
  })
})
