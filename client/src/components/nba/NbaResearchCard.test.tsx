/**
 * The NBA card's job is to show two model outputs without letting a reader
 * merge them, and to say what is unavailable instead of leaving a gap.
 *
 * The percentage assertions are exact on purpose. The delivered value is a
 * fraction, and a second multiplication anywhere on the path would render
 * 4188.68% or 0.42% — both of which a loose matcher would accept.
 */
import { cleanup, render, screen, fireEvent } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { NbaResearchCard } from './NbaResearchCard'
import type { NbaResearchForecast } from '../../types/nbaResearch'

const FORECAST: NbaResearchForecast = {
  forecast_id: 'NBA_GEN1_REDUCED_POINT_ELO_2026_10_20_BOS_DET',
  forecast_sha256: 'a'.repeat(64),
  engine_id: 'NBA_GEN1_POINT_ELO',
  configuration_identity: 'RIDGE_ALPHA100_ELO_K20',
  game_id: 'f4465955-4d0a-4e7f-8a40-f8bcebe8f5da',
  source_game_id: '401810123',
  teams: { away: { abbreviation: 'BOS' }, home: { abbreviation: 'DET' } },
  captured_schedule: {
    tipoff_utc: '2026-10-20T19:00:00+00:00',
    game_date: '2026-10-20',
    source_revision: null,
  },
  current_schedule: { tipoff_utc: '2026-10-20T19:00:00+00:00', differs_from_captured: false },
  ridge_expected_points: {
    away: 111.93, home: 113.79, total: 225.72, home_margin: 1.86,
    model: 'Ridge alpha100', units: 'points',
  },
  elo_win_probability: {
    away: 0.4189, home: 0.5811, tie: null,
    model: 'Elo', method: 'LOGISTIC', units: 'probability',
  },
  distributions: {
    draw_count: 0,
    status: 'UNAVAILABLE',
    note: 'No simulated distribution, interval or Over/Under probability is available '
      + 'for this forecast. None is derived from the expected total.',
  },
  clocks: {
    source_cutoff_utc: '2026-09-13T00:00:00+00:00',
    forecast_created_utc: '2026-09-14T01:12:00+00:00',
    exchange_delivered_utc: '2026-09-14T01:20:00+00:00',
    producer_published_utc: null,
  },
  label: 'Early research forecast — September 14, 2026',
  limitations: null,
  rest_support: null,
  bindings: null,
  authority: null,
  superseded_by: null,
}

afterEach(cleanup)

describe('NbaResearchCard', () => {
  it('labels the two model outputs separately and never merges them', () => {
    render(<NbaResearchCard forecast={FORECAST} />)
    const ridge = screen.getByTestId('nba-ridge')
    const elo = screen.getByTestId('nba-elo')
    expect(ridge.textContent).toContain('Ridge expected points')
    expect(ridge.textContent).toContain('111.93')
    expect(ridge.textContent).toContain('113.79')
    expect(elo.textContent).toContain('Elo win probability')
    // The single multiplication. 41.89 / 58.11, not 0.42 and not 4188.68.
    expect(elo.textContent).toContain('41.89%')
    expect(elo.textContent).toContain('58.11%')
    expect(elo.textContent).toContain('not derived from the expected points')
    // The probability must never appear inside the points section.
    expect(ridge.textContent).not.toContain('%')
  })

  it('states that distributions and Over/Under are unavailable', () => {
    render(<NbaResearchCard forecast={FORECAST} />)
    const note = screen.getByTestId('nba-unavailable').textContent ?? ''
    expect(note).toContain('Over/Under')
    expect(note.toLowerCase()).toContain('no simulated distribution')
    // The expected total is not offered anywhere as a line to bet against.
    expect(screen.queryByText(/225\.72/)).toBeNull()
  })

  it('shows the original forecast date and keeps limitations collapsed', () => {
    render(<NbaResearchCard forecast={FORECAST} />)
    expect(screen.getByTestId('nba-research-label').textContent)
      .toBe('Early research forecast — September 14, 2026')
    expect(screen.queryByTestId('nba-research-detail')).toBeNull()
    fireEvent.click(screen.getByTestId('nba-research-toggle'))
    const detail = screen.getByTestId('nba-research-detail').textContent ?? ''
    expect(detail).toContain('SEASON_OPENER_NO_ORDINARY_REST')
    // The 190-day gap must never read as a claim of rest.
    expect(detail).toContain('not claims of 190 days of physical rest')
    expect(detail).toContain('lineups and injuries were not available')
    expect(detail).toContain('the producer had not published locally')
    expect(detail).toContain('creates no account, wager, bankroll')
  })

  it('does not announce a schedule revision when there is none', () => {
    render(<NbaResearchCard forecast={FORECAST} />)
    expect(screen.queryByTestId('nba-schedule-revision')).toBeNull()
  })

  it('keeps both schedules when AISP now holds a different tip-off', () => {
    render(<NbaResearchCard forecast={{
      ...FORECAST,
      current_schedule: { tipoff_utc: '2026-10-20T23:30:00+00:00', differs_from_captured: true },
    }} />)
    expect(screen.getByTestId('nba-schedule-revision').textContent)
      .toContain('is not rewritten')
    // The captured value still shows; a revision does not overwrite it.
    expect(screen.getByTestId('nba-captured-schedule').textContent).toContain('7:00 PM')
  })
})
