/**
 * Track routing for the two-engine simulator.
 *
 * The guarantees under test: the default stays Gen 1, ?track=v18 shows the
 * research-shadow replay, a v18 request without a persisted path fails honestly
 * instead of borrowing Gen 1 evidence, and the two engines' numbers never mix.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GameSimulationPage } from './GameSimulationPage'
import { endpoints } from '../services/api'
import type {
  PlayGeometryPayload, PopulationPayload, ShadowComparison, ShadowPath,
} from '../types/shadow'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => (
    <div data-testid="echarts-placeholder" style={style} />
  ),
}))

const GAME_ID = 'game-1'

/** Gen 1: 20,000 draws, final scores only. */
const GEN1 = {
  data_mode: 'live',
  simulation_available: true,
  unavailable_reason: null,
  identity: {
    generated_at: '2026-09-07T22:06:34Z', forecast_version: '2', lifecycle: 'EARLY',
    model_name: 'asp_mlb_ensemble', model_version: '2.0.0',
    development_note: 'Development model.', feature_snapshot_reference: 'snap-1',
  },
  official_win_probability: { home: 0.4566, away: 0.5434 },
  means: { total_runs: 8.9, home_runs: 4.35, away_runs: 4.55 },
  extra_inning_probability: 0.09,
  inputs: { simulations: 20000, seed: 20250601 },
  play_by_play: { available: false, reason: 'Gen 1 draws final scores directly.' },
  score_distribution: [], total_runs_distribution: [], margin_distribution: [],
  representative_outcomes: [], outcome_frequencies: {},
} as never

/** V18: 5,000 paths, sequential. Deliberately different numbers. */
const COMPARISON: ShadowComparison = {
  schema_version: 'AISP_MLB_V18_SHADOW_COMPARISON_V1',
  game_id: GAME_ID, home_team: 'ATH', away_team: 'TOR',
  scheduled_start: '2026-09-08T02:05:00Z', event_status: 'SCHEDULED',
  official: null,
  notes: 'V18 is a research-shadow challenger.',
  shadows: [{
    track: 'RESEARCH_SHADOW', engine_id: 'MLB_V18_WHOLE_GAME',
    label: 'V18 whole-game challenger (research shadow)',
    forecast_id: 'mlb-v18-1', bundle_id: 'mlb-v18-whole-game-abc',
    bundle_sha256: 'a'.repeat(64), evaluation_contract: 'mlb-v18-acceptance-1',
    home_win_probability: 43.34, expected_home_runs: 3.4694, expected_away_runs: 4.423,
    generated_at: '2026-09-07T21:06:36Z', evidence_cutoff: '2026-09-07T21:06:36Z',
    pregame: true, simulation_count: 5000, master_seed: 20260907,
    monte_carlo_standard_error: 0.007008, monte_carlo_95_half_width: 0.013736,
    deterministic_replay_verified: true, illegal_state_count: 0, nontermination_count: 0,
    path_digest: 'd'.repeat(64), sequential_path_events: 2,
    lineup_modes: ['PROJECTED_PRIOR_COMPLETE_LINEUP'],
    sides: { AWAY: { starter: 'Test Starter' } },
    limitations: ['projected lineups are carried forward'],
    wagering_authority: false,
    population: {
      simulation_count: 5000, home_win_probability: 43.34, away_win_probability: 56.66,
      expected_home_runs: 3.4694, expected_away_runs: 4.423, expected_total_runs: 7.8924,
      expected_run_differential: -0.9536, one_run_probability: 0.3094,
      extra_inning_probability: 0.1074, pitching_change_rate_home: 1,
      pitching_change_rate_away: 0.9998, monte_carlo_standard_error: 0.007008,
      monte_carlo_95_half_width: 0.013736, total_run_distribution: null,
    },
    settlement: null, settlement_state: 'PENDING',
    settlement_note: 'The event has no persisted final outcome yet.',
    paired_with_official: false, pairing_note: null,
    difference_vs_official: null, difference_orientation: 'V18_MINUS_GEN1',
    simulation_indistinguishable: null,
  }],
}

const PATH: ShadowPath = {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1',
  forecast_id: 'mlb-v18-1', game_id: GAME_ID, engine_id: 'MLB_V18_WHOLE_GAME',
  bundle_sha256: 'a'.repeat(64), path_digest: 'd'.repeat(64),
  master_seed: 20260907, simulation_count: 5000, path_count: 1,
  paths: [[
    {
      pa: 1, inning: 1, half: 'TOP', battingOrderPosition: 1, outcome: 'STRIKEOUT',
      outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0, runsScored: 0,
      homeScoreAfter: 0, awayScoreAfter: 0, pitcherState: 'STARTER', pitchingChange: false,
    },
    {
      pa: 2, inning: 1, half: 'TOP', battingOrderPosition: 2, outcome: 'SINGLE',
      outsBefore: 1, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 1, runsScored: 0,
      homeScoreAfter: 0, awayScoreAfter: 0, pitcherState: 'STARTER', pitchingChange: false,
    },
  ]],
}

/** A minimal but valid play-geometry payload for the same persisted path. */
const GEOMETRY = {
  schema_version: 'AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1',
  forecast_id: 'mlb-v18-1', game_id: GAME_ID, engine_id: 'MLB_V18_WHOLE_GAME',
  supplement_id: 'mlb-v18-play-geometry-7b638efe', supplement_sha256: '7'.repeat(64),
  parent_bundle_id: 'mlb-v18-whole-game-abc', parent_bundle_sha256: 'a'.repeat(64),
  coordinate_system: { name: 'ASP_NORMALIZED_DIAMOND_V1', origin: 'HOME_PLATE' },
  geometry_model_version: 'MLB_V18_PLAY_GEOMETRY_SAMPLER_V1',
  runner_model_version: 'MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1',
  registered_at: '2026-09-08T00:00:00+00:00',
  backoff_summary: {}, limitations: [],
  wagering_authority: false, promotion_status: 'NOT_PROMOTED',
  execution_allowed_in_aisp: false, display_authority: 'RESEARCH_SHADOW_PLAY_GEOMETRY',
  plate_appearances: 2, contact_plays: 1, ball_traces: 1, runner_movements: 1,
  events: PATH.paths[0].map((event, position) => ({
    ...event,
    battingTeam: 'AWAY',
    geometry: {
      contactOccurred: position === 1,
      battedBallType: position === 1 ? 'LINE_DRIVE' : null,
      locationZone: position === 1 ? '7' : null,
      geometryAuthority: position === 1 ? 'RETROSHEET_LOCATION_ZONE_CENTROID' : 'NO_CONTACT',
      geometryBackoffLevel: null,
      geometryModelVersion: 'MLB_V18_PLAY_GEOMETRY_SAMPLER_V1',
      geometrySamplingSeed: '0'.repeat(16),
      coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
      landingOrFieldingPoint: position === 1
        ? { x: 0.1, y: 0.5, depth: 0.5, depthClass: 'DEEP', directionDegrees: 10, fairTerritory: true }
        : null,
      exitVelocityMph: null, launchAngleDegrees: null, sprayAngleDegrees: null,
      projectedDistanceFeet: null, landingCoordinateMeasured: null,
    },
    ballTrace: position === 1 ? {
      coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
      start: { x: 0, y: 0 }, control: [{ x: 0.05, y: 0.25, lift: 0.05 }],
      end: { x: 0.1, y: 0.5 }, traceStyle: 'LINE', normalizedDuration: 0.5,
      fairTerritory: true, outcomeCompatibility: 'COMPATIBLE_WITH_PERSISTED_OUTCOME',
      semantics: 'PROJECTED_VISUAL_TRAJECTORY_FROM_SIMULATED_GEOMETRY_NOT_A_MEASURED_FLIGHT_PATH',
    } : null,
    runnerMovements: position === 1 ? [{
      anonymousRunnerId: 'r0001-02', role: 'BATTER', from: 'BATTER_BOX', to: 'FIRST',
      scored: false, out: false, movementReason: 'BATTER_REACHED',
      startFraction: 0, endFraction: 0.6, authority: 'FITTED_HISTORICAL_TEMPLATE',
    }] : [],
    runnerModelVersion: 'MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1',
    runnerBackoffLevel: 'outcome_state_outs_runs_after_bbtype',
    runnerBackoffSupport: 120,
  })),
  population: {
    simulationCount: 5000, populationGeometryAvailable: false,
    populationGeometryStatus: 'AWAITING_CHECKSUM_VERIFIED_MODEL_STUDIO_SUPPLEMENT',
  },
  notes: 'One representative simulated game.',
} as unknown as PlayGeometryPayload


/** The registered population supplement, trimmed to two frames. */
const POPULATION = {
  schema_version: 'AISP_MLB_V18_POPULATION_VIEW_V1',
  forecast_id: 'mlb-v18-1',
  supplement_id: 'mlb-v18-population-geometry-d2586fba', supplement_sha256: 'd'.repeat(64),
  parent_bundle_sha256: 'a'.repeat(64), play_geometry_sha256: '7'.repeat(64),
  population_digest: 'c'.repeat(64), execution_authority_sha256: 'e'.repeat(64),
  wagering_authority: false, promotion_status: 'NOT_PROMOTED',
  execution_allowed_in_aisp: false, display_authority: 'RESEARCH_SHADOW_POPULATION_GEOMETRY',
  simulation_count: 5000, frame_count: 2, frame_size: 2500, master_seed: 20260907,
  counts: {
    plateAppearances: 377078, contactPlays: 246035, geometryBearingPlays: 246035,
    scoringPlays: 30151, runnerSegments: 607156,
  },
  final_population: {
    homeWinProbability: 0.4334, meanHomeRuns: 3.4694, meanAwayRuns: 4.423,
    meanTotalRuns: 7.8924, meanRunDifferential: -0.9536, oneRunRate: 0.3094,
    extraInningRate: 0.1074,
  },
  illegal_state_count: 0, nontermination_count: 0, deterministic_replay: true,
  frames: [2500, 5000].map((cumulative, index) => ({
    frameIndex: index, simulationIndexStart: index * 2500,
    simulationIndexEndInclusive: cumulative - 1, cumulativeSimulationCount: cumulative,
    cumulativeHomeWins: 1084 * (index + 1), cumulativeAwayWins: 1416 * (index + 1),
    cumulativeHomeWinProbability: 0.4334, cumulativeAwayWinProbability: 0.5666,
    cumulativeExpectedHomeRuns: 3.4694, cumulativeExpectedAwayRuns: 4.423,
    cumulativeExpectedTotal: 7.8924, cumulativeRunDifferential: -0.9536,
    cumulativeOneRunGameRate: 0.3094, cumulativeExtraInningRate: 0.1074,
    cumulativeContactPlayCount: 123018 * (index + 1),
    cumulativeGeometryDensity: 123018 * (index + 1),
    cumulativeRunnerPathTraffic: 303578 * (index + 1),
    cumulativeScoringPlayCount: 15076 * (index + 1),
    frameDigest: `digest-${index}`,
  })),
  selectors: {},
  notes: 'Deterministic aggregation over 5,000 complete V18 simulations.',
} as unknown as PopulationPayload


function mount(search: string, {
  shadows = true, path = true, geometry = true, population = true, views,
}: {
  shadows?: boolean
  path?: boolean
  geometry?: boolean
  population?: boolean
  views?: { broadcast: boolean; geometry: boolean; population: boolean }
} = {}) {
  vi.spyOn(endpoints, 'gameSimulation').mockResolvedValue(GEN1)
  vi.spyOn(endpoints, 'marketPulseGame').mockRejectedValue(new Error('no pulse'))
  vi.spyOn(endpoints, 'gameShadowForecasts').mockResolvedValue(
    shadows
      ? (views
          ? { ...COMPARISON, shadows: [{ ...COMPARISON.shadows[0], available_views: views }] }
          : COMPARISON)
      : { ...COMPARISON, shadows: [] },
  )
  vi.spyOn(endpoints, 'shadowForecastPopulation').mockImplementation(
    population
      ? () => Promise.resolve(POPULATION)
      : () => Promise.reject(new Error('no population')),
  )
  vi.spyOn(endpoints, 'shadowForecastPopulationLayers').mockRejectedValue(new Error('no layers'))
  vi.spyOn(endpoints, 'shadowForecastPopulationRepresentatives')
    .mockRejectedValue(new Error('no representatives'))
  vi.spyOn(endpoints, 'shadowForecastGeometry').mockImplementation(
    geometry
      ? () => Promise.resolve(GEOMETRY)
      : () => Promise.reject(new Error('no geometry')),
  )
  vi.spyOn(endpoints, 'shadowForecastPath').mockImplementation(
    path
      ? () => Promise.resolve(PATH)
      : () => Promise.reject(new Error('no path')),
  )
  render(
    <MemoryRouter initialEntries={[`/games/${GAME_ID}/simulation${search}`]}>
      <Routes>
        <Route path="/games/:gameId/simulation" element={<GameSimulationPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('simulator track routing', () => {
  it('defaults to the official Gen 1 track and labels it as a distribution', async () => {
    mount('')
    expect(await screen.findByRole('heading', { level: 1, name: /Gen 1 baseline · Final-score distribution/ }))
      .toBeInTheDocument()
    // Gen 1 is described as a distribution, never as play-by-play.
    expect(document.querySelector('.track-scope')).toHaveTextContent(
      /Gen 1 draws final scores directly\. It has no innings, plate appearances, runners, outs or pitching changes/,
    )
    expect(screen.queryByRole('heading', { name: /Sequential game replay/i })).not.toBeInTheDocument()
  })

  it('shows the V18 replay when ?track=v18 is requested', async () => {
    mount('?track=v18')
    expect(await screen.findByRole('heading', { level: 1, name: /V18 Monte Carlo Diamond Theater/ }))
      .toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByTestId('diamond-theater')).toBeInTheDocument())
    expect(screen.getByRole('group', { name: /scoreboard/i })).toHaveTextContent('PA 1 of 2')
  })

  it('keeps an unsupported V18 view discoverable, disabled, with its reason', async () => {
    // Hiding the mode made an unavailable view indistinguishable from one that
    // never existed. These two are unavailable for a specific, reportable
    // reason -- the producer published no supplement -- and saying so is what
    // lets a reader (or a Codex handoff) act on it.
    mount('?track=v18', {
      views: { broadcast: true, geometry: false, population: false },
      geometry: false,
      population: false,
    })
    expect(await screen.findByRole('button', { name: 'Broadcast Replay' })).toBeEnabled()

    // The control itself says one plain thing. Reading a flag name off a
    // button is not what tells a viewer whether the mode is worth clicking.
    const geometry = screen.getByRole('button', { name: 'Play Geometry' })
    expect(geometry).toBeDisabled()
    expect(geometry).toHaveTextContent(/This forecast does not include this view/)
    expect(geometry).toHaveTextContent(/Nothing is reconstructed in its place/)
    expect(geometry).not.toHaveTextContent(/available_views/)

    const population = screen.getByRole('button', { name: 'Simulation Population' })
    expect(population).toBeDisabled()
    expect(population).toHaveTextContent(/This forecast does not include this view/)

    // The exact field names stay reachable for whoever has to chase the
    // missing supplement, one disclosure below the controls.
    const technical = screen.getByText(/Technical detail: unavailable views/)
    expect(technical.parentElement).toHaveTextContent(/available_views\.geometry = false/)
    expect(technical.parentElement).toHaveTextContent(/available_views\.population = false/)
  })

  it('keeps the two engines’ population figures apart', async () => {
    mount('?track=v18')
    await screen.findByRole('heading', { name: /Simulation population · 5,000 games/ })
    // V18's own frozen results are shown...
    expect(screen.getAllByText('43.3%').length).toBeGreaterThan(0)
    expect(screen.getByText('7.89')).toBeInTheDocument()
    // ...and Gen 1's 20,000-draw evidence appears nowhere on this track.
    expect(screen.queryByText(/20,000/)).not.toBeInTheDocument()
    expect(screen.queryByText('45.7%')).not.toBeInTheDocument()
    expect(screen.queryByText(/Seed 20250601/)).not.toBeInTheDocument()
  })

  it('offers no V18 scenario controls and explains why', async () => {
    mount('?track=v18')
    expect(await screen.findByText(/V18 what-if scenarios require a new Model Studio simulation artifact/i))
      .toBeInTheDocument()
    expect(screen.queryByLabelText(/temperature/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/park factor/i)).not.toBeInTheDocument()
  })

  it('switches tracks without claiming either forecast changed', async () => {
    const user = userEvent.setup()
    mount('')
    await screen.findByRole('heading', { level: 1, name: /Gen 1 baseline/ })
    const selector = await screen.findByRole('navigation', { name: /simulation track/i })
    expect(screen.getByText(/Switching tracks changes only which engine/i)).toBeInTheDocument()

    await user.click(within(selector).getByRole('button', { name: /V18 · Saved game simulation/ }))
    expect(await screen.findByRole('heading', { level: 1, name: /V18 Monte Carlo Diamond Theater/ }))
      .toBeInTheDocument()
  })

  it('fails honestly when a V18 track is requested with no registered shadow', async () => {
    mount('?track=v18', { shadows: false })
    expect(await screen.findByRole('heading', { name: /No research-shadow replay exists/i }))
      .toBeInTheDocument()
    expect(screen.getByText(/Nothing has been reconstructed from Gen 1/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Show the official Gen 1 distribution/i }))
      .toHaveAttribute('href', `/games/${GAME_ID}/simulation`)
  })

  it('states when a shadow exists but its path does not', async () => {
    mount('?track=v18', { path: false })
    expect(await screen.findByText(/No sequential path is persisted for this research-shadow forecast/i))
      .toBeInTheDocument()
    expect(screen.queryByTestId('diamond-theater')).not.toBeInTheDocument()
  })

  it('hides the selector when only Gen 1 exists', async () => {
    mount('', { shadows: false })
    await screen.findByRole('heading', { level: 1, name: /Gen 1 baseline/ })
    expect(screen.queryByRole('navigation', { name: /simulation track/i })).not.toBeInTheDocument()
  })
})

describe('the simulator view selector', () => {
  it('keeps Broadcast Replay as the view an existing ?track=v18 link opens', async () => {
    mount('?track=v18')
    await waitFor(() => expect(screen.getByTestId('diamond-theater')).toBeInTheDocument())
    expect(screen.queryByTestId('play-geometry')).not.toBeInTheDocument()
    const selector = screen.getByRole('navigation', { name: /simulator view/i })
    expect(within(selector).getByRole('button', { name: /broadcast replay/i }))
      .toHaveAttribute('aria-current', 'page')
  })

  it('shows Play Geometry for ?view=geometry, and the replay is still one click away', async () => {
    mount('?track=v18&view=geometry')
    await waitFor(() => expect(screen.getByTestId('play-geometry')).toBeInTheDocument())
    expect(screen.queryByTestId('diamond-theater')).not.toBeInTheDocument()
    const selector = screen.getByRole('navigation', { name: /simulator view/i })
    expect(within(selector).getByRole('button', { name: /broadcast replay/i })).toBeEnabled()
  })

  it('switches to Play Geometry and back without leaving the V18 track', async () => {
    const user = userEvent.setup()
    mount('?track=v18')
    await waitFor(() => expect(screen.getByTestId('diamond-theater')).toBeInTheDocument())
    const selector = screen.getByRole('navigation', { name: /simulator view/i })

    await user.click(within(selector).getByRole('button', { name: /play geometry/i }))
    await waitFor(() => expect(screen.getByTestId('play-geometry')).toBeInTheDocument())
    expect(screen.getByRole('navigation', { name: /simulation track/i })).toBeInTheDocument()

    await user.click(within(selector).getByRole('button', { name: /broadcast replay/i }))
    await waitFor(() => expect(screen.getByTestId('diamond-theater')).toBeInTheDocument())
  })

  it('fails closed when no verified supplement is registered', async () => {
    mount('?track=v18&view=geometry', { geometry: false })
    expect(await screen.findByText(/No checksum-verified play-geometry supplement is registered/i))
      .toBeInTheDocument()
    expect(screen.queryByTestId('play-geometry')).not.toBeInTheDocument()
  })

  it('shows the registered 5,000-game population, never Gen 1 draws', async () => {
    mount('?track=v18&view=population')
    expect(await screen.findByRole('heading', { level: 2, name: /of 5,000 simulations/ }))
      .toBeInTheDocument()
    expect(screen.getByText(/not one continuous baseball game/i)).toBeInTheDocument()
    expect(screen.queryByTestId('play-geometry')).not.toBeInTheDocument()
    expect(screen.queryByText(/20,000/)).not.toBeInTheDocument()
  })

  it('fails closed to a clear statement when no population is registered', async () => {
    mount('?track=v18&view=population', { population: false })
    expect(await screen.findByText(/No checksum-verified population supplement is registered/i))
      .toBeInTheDocument()
  })

  it('offers no view selector on the Gen 1 track', async () => {
    mount('')
    await screen.findByRole('heading', { level: 1, name: /Gen 1 baseline/ })
    expect(screen.queryByRole('navigation', { name: /simulator view/i })).not.toBeInTheDocument()
  })
})
