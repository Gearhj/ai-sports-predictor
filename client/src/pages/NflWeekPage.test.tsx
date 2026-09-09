/**
 * The NFL week page renders one card per event from the read model, links
 * every game to its analysis and Simulation Lab, and states the import status.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NflWeekPage } from './NflWeekPage'
import { endpoints } from '../services/api'
import type { NflEngine, NflEvent, NflWeekOverview, NflWeeks } from '../types/nfl'

function engine(): NflEngine {
  return {
    engine_id: 'NFL_M1_XGBOOST', engine_version: 'v1', model_label: 'M1 long-history XGBoost (research shadow)',
    forecast_id: 'f-1', snapshot_type: 'CURRENT', snapshot_version: 9, status: 'RESEARCH_SHADOW',
    home_win_probability: 0.5736, away_win_probability: 0.3966, tie_probability: 0.0298, tie_note: 'own outcome',
    expected_home_score: 22.75, expected_away_score: 19.94, expected_margin_home: 2.81, expected_total_score: 42.69,
    evidence_readiness: { score: 78, threshold: 50, label: 'Evidence readiness 78 of 100 (publishes at 50)', missing_input_count: 44, explanation: 'x' },
    evidence_basis: { model_inputs: '197 inputs', not_consumed: ['weather'], note: 'n' },
    timestamps: { source_evidence_utc: '2026-08-29T14:07:34Z', forecast_cutoff_utc: null, published_utc: '2026-09-08T22:53:07Z', imported_at_utc: '2026-09-08T23:08:28Z' },
    simulation: { simulator_id: 'sim', simulator_version: 'v1', seed: 1, kind: 'MONTE_CARLO', count: 10000, joint_available: true },
    uncertainty: {},
  }
}

function event(id: string, home: string, away: string, kickoff: string): NflEvent {
  return {
    event_id: id, canonical_game_id: `game-${id}`, season: 2026, week: 1, kickoff_utc: kickoff, state: 'UPCOMING',
    final_score: null,
    home: { team_id: home, abbreviation: home, name: `${home} Team` },
    away: { team_id: away, abbreviation: away, name: `${away} Team` },
    engines: [engine()], snapshot_history_count: 9,
    supplements: { joint_engines: [], drive_engines: [], scenario_engines: [] },
    lab_available: true, analysis_href: `/nfl/games/game-${id}`, lab_href: `/nfl/games/game-${id}/lab`,
  }
}

const WEEKS: NflWeeks = {
  schema_version: 'AISP_NFL_PRODUCT_READ_V1', sport: 'NFL', now_utc: '2026-09-09T12:00:00Z', data_mode: 'live',
  weeks: [{ season: 2026, week: 1, events: 2, first_kickoff_utc: '2026-09-10T00:20:00Z', last_kickoff_utc: '2026-09-15T00:15:00Z' },
          { season: 2026, week: 2, events: 16, first_kickoff_utc: '2026-09-18T00:15:00Z', last_kickoff_utc: '2026-09-22T00:15:00Z' }],
  current: { season: 2026, week: 1 },
}

const OVERVIEW: NflWeekOverview = {
  schema_version: 'AISP_NFL_PRODUCT_READ_V1', sport: 'NFL', season: 2026, week: 1, now_utc: '2026-09-09T12:00:00Z',
  generated_at_utc: '2026-09-09T12:00:00Z', authority: 'RESEARCH_SHADOW', data_mode: 'live',
  events: [event('2026_01_NE_SEA', 'SEA', 'NE', '2026-09-10T00:20:00Z'), event('2026_01_SF_LA', 'LA', 'SF', '2026-09-11T00:35:00Z')],
  counts: { events: 2, upcoming: 2, started: 0, final: 0, with_joint_evidence: 2 },
  consumer: {
    auto_import_enabled: true, job_name: 'consume-model-exchange-research-artifacts',
    last_success_utc: '2026-09-09T11:45:00Z', last_success_result: null, last_run_utc: '2026-09-09T11:45:00Z',
    last_run_status: 'SUCCEEDED', last_failure_utc: null, last_failure_message: null,
    legacy_bundle_job: { job_name: 'import-nfl-shadow-forecast-bundle', last_success_utc: null },
    registered_bundles: 573, registered_supplements: 1,
    last_bundle_import_utc: '2026-09-08T23:08:28Z', last_supplement_import_utc: '2026-09-09T02:30:00Z',
  },
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('NflWeekPage', () => {
  it('renders one card per event with separate outcomes, analysis and lab links, and the import status', async () => {
    vi.spyOn(endpoints, 'nflWeeks').mockResolvedValue(WEEKS)
    vi.spyOn(endpoints, 'nflWeek').mockResolvedValue(OVERVIEW)
    render(<MemoryRouter initialEntries={['/nfl']}><NflWeekPage /></MemoryRouter>)

    const cards = await screen.findAllByRole('article')
    expect(cards).toHaveLength(2)
    const first = within(cards[0])
    expect(first.getByText('NE @ SEA')).toBeInTheDocument()
    expect(first.getByText('SEA win').nextSibling).toHaveTextContent('57.4%')
    expect(first.getByText('NE win').nextSibling).toHaveTextContent('39.7%')
    expect(first.getByText('Tie').nextSibling).toHaveTextContent('3.0%')
    expect(first.getByRole('link', { name: /Open game analysis/ })).toHaveAttribute('href', '/nfl/games/game-2026_01_NE_SEA')
    expect(first.getByRole('link', { name: /Open NFL Simulation Lab/ })).toHaveAttribute('href', '/nfl/games/game-2026_01_NE_SEA/lab')
    expect(first.getByText(/Evidence readiness 78 of 100/)).toBeInTheDocument()

    expect(screen.getByLabelText('Research import status')).toHaveTextContent(/Automatic research import on/)
    expect(screen.getByLabelText('Research import status')).toHaveTextContent(/573 forecast bundles, 1 supplements/)
  })
})
