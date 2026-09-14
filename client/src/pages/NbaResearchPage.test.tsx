/**
 * The NBA research surface: discoverable, and honest about what NBA is not.
 *
 * NBA is research-only and unfunded. The page has to make one imported forecast
 * findable without implying a market, a policy, a position or a bankroll exists
 * — so these tests check the absences as carefully as the content.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../services/api'
import { NbaResearchPage } from './NbaResearchPage'
import type { NbaResearchBoard } from '../types/nbaResearch'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

const BOARD: NbaResearchBoard = {
  schema_version: 'AISP_NBA_RESEARCH_FORECASTS_V1',
  note: 'NBA is research-only and unfunded. These forecasts create no account, '
    + 'wager, bankroll, staking policy or scheduler entry.',
  forecasts: [{
    forecast_id: 'nba-point:1b0f9b6f',
    forecast_sha256: 'b'.repeat(64),
    engine_id: 'NBA_GEN1_POINT_ELO',
    configuration_identity: 'NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1',
    game_id: 'f4465955-4d0a-4e7f-8a40-f8bcebe8f5da',
    source_game_id: '401909088',
    teams: { away: { abbreviation: 'BOS' }, home: { abbreviation: 'DET' } },
    captured_schedule: {
      tipoff_utc: '2026-10-20T19:00:00+00:00',
      game_date: '2026-10-20', source_revision: null,
    },
    current_schedule: {
      tipoff_utc: '2026-10-20T19:00:00+00:00', differs_from_captured: false,
    },
    ridge_expected_points: {
      away: 111.93456692347031, home: 113.78511290268382,
      total: 225.71967982615413, home_margin: 1.850545979213507,
      model: 'Ridge alpha100', units: 'points including overtime',
    },
    elo_win_probability: {
      away: 0.41887505293332616, home: 0.5811249470666738, tie: 0.0,
      model: 'Elo — separate model output', method: 'UNCHANGED_CHRONOLOGICAL_ELO',
      units: 'fractions 0 to 1',
    },
    distributions: {
      draw_count: 0, status: 'UNAVAILABLE',
      note: 'No simulated draws were retained, so run distributions, prediction '
        + 'intervals, Monte Carlo error, Over/Under probabilities and market fair '
        + 'prices are unavailable. None is derived from the expected total.',
    },
    clocks: {
      source_cutoff_utc: '2026-09-14T13:04:43.697362+00:00',
      forecast_created_utc: '2026-09-14T13:09:39.891972+00:00',
      exchange_delivered_utc: '2026-09-14T14:01:00.644249+00:00',
      producer_published_utc: null,
    },
    label: 'Early research forecast — September 14, 2026',
    limitations: null, rest_support: null, bindings: null, authority: null,
    superseded_by: null,
  }],
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount(path = '/nba/research') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/nba/research" element={<NbaResearchPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('NBA research page', () => {
  it('groups the forecast under the date it was computed against', async () => {
    vi.mocked(apiRequest).mockResolvedValue(BOARD as never)
    mount()
    const day = await screen.findByTestId('nba-day-2026-10-20')
    expect(day.textContent).toContain('October 20, 2026')
    expect(day.textContent).toContain('BOS at DET')
  })

  it('links each forecast to its own game route', async () => {
    vi.mocked(apiRequest).mockResolvedValue(BOARD as never)
    mount()
    const link = await screen.findByRole('link', { name: /open this game/i })
    expect(link.getAttribute('href'))
      .toBe('/nba/games/f4465955-4d0a-4e7f-8a40-f8bcebe8f5da')
  })

  it('narrows to one captured date from the query', async () => {
    vi.mocked(apiRequest).mockResolvedValue(BOARD as never)
    mount('/nba/research?date=2026-10-20')
    const filter = await screen.findByTestId('nba-date-filter')
    // The heading must not slip a day: the stored value is a calendar date and
    // parsing it as an instant would render October 19 west of UTC.
    expect(filter.textContent).toContain('October 20, 2026')
    expect(screen.getByTestId('nba-day-2026-10-20')).toBeTruthy()
  })

  it('says nothing was imported for a date that has no forecast', async () => {
    vi.mocked(apiRequest).mockResolvedValue(BOARD as never)
    mount('/nba/research?date=2026-10-21')
    const empty = await screen.findByTestId('nba-research-empty')
    expect(empty.textContent).toContain('No research forecast has been imported')
    expect(screen.queryByTestId('nba-day-2026-10-20')).toBeNull()
  })

  it('never implies a market, a policy or a position exists', async () => {
    vi.mocked(apiRequest).mockResolvedValue(BOARD as never)
    mount()
    await screen.findByTestId('nba-day-2026-10-20')
    const page = document.body.textContent ?? ''
    expect(page).toContain('no NBA odds, evaluation, policy, position or bankroll')
    expect(page).toContain('Over/Under probabilities and market fair prices are unavailable')
    // The expected total must never surface as a line to bet against.
    expect(page).not.toContain('225.72')
    expect(page).not.toMatch(/\bstake\b/i)
  })

  it('shows an unavailable state rather than an empty page when the read fails', async () => {
    vi.mocked(apiRequest).mockRejectedValue(new Error('offline'))
    mount()
    await waitFor(() => {
      expect(screen.getByRole('status').textContent)
        .toContain('unavailable right now')
    })
  })
})
