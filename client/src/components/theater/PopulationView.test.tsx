/**
 * The Simulation Population view: convergence, layers, and the boundary
 * between a 5,000-game aggregate and one representative simulated game.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PopulationView, representativePayload } from './PopulationView'
import { availableFilters, cellBox, decodePathTuple, revealFraction } from './PopulationLayers'
import { endpoints } from '../../services/api'
import type {
  PopulationFrame,
  PopulationLayersPayload,
  PopulationPayload,
  PopulationRepresentativesPayload,
  RepresentativeSelector,
  ShadowForecastView,
} from '../../types/shadow'

const FORECAST = 'mlb-v18-1'
const GAME = 'game-1'
const FRAME_COUNT = 100
const FRAME_SIZE = 50
const SIMULATIONS = FRAME_COUNT * FRAME_SIZE

const FINAL = {
  homeWinProbability: 0.4334,
  meanHomeRuns: 3.4694,
  meanAwayRuns: 4.423,
  meanTotalRuns: 7.8924,
  meanRunDifferential: -0.9536,
  oneRunRate: 0.3094,
  extraInningRate: 0.1074,
}

/** Frames that converge on the accepted population, as the artifact's do. */
const FRAMES: PopulationFrame[] = Array.from({ length: FRAME_COUNT }, (_, index) => {
  const cumulative = (index + 1) * FRAME_SIZE
  const share = cumulative / SIMULATIONS
  return {
    frameIndex: index,
    simulationIndexStart: index * FRAME_SIZE,
    simulationIndexEndInclusive: cumulative - 1,
    cumulativeSimulationCount: cumulative,
    cumulativeHomeWins: Math.round(cumulative * FINAL.homeWinProbability),
    cumulativeAwayWins: cumulative - Math.round(cumulative * FINAL.homeWinProbability),
    cumulativeHomeWinProbability: FINAL.homeWinProbability,
    cumulativeAwayWinProbability: 1 - FINAL.homeWinProbability,
    cumulativeExpectedHomeRuns: FINAL.meanHomeRuns,
    cumulativeExpectedAwayRuns: FINAL.meanAwayRuns,
    cumulativeExpectedTotal: FINAL.meanTotalRuns,
    cumulativeRunDifferential: FINAL.meanRunDifferential,
    cumulativeOneRunGameRate: FINAL.oneRunRate,
    cumulativeExtraInningRate: FINAL.extraInningRate,
    cumulativeContactPlayCount: Math.round(246035 * share),
    cumulativeGeometryDensity: Math.round(246035 * share),
    cumulativeRunnerPathTraffic: Math.round(607156 * share),
    cumulativeScoringPlayCount: Math.round(30151 * share),
    frameDigest: `digest-${index}`,
  }
})

const SELECTOR_INDEXES: Record<RepresentativeSelector, number> = {
  LOW_SCORING: 0, HOME_WIN: 13, TYPICAL: 15, AWAY_WIN: 34, EXTRA_INNINGS: 44, HIGH_SCORING: 526,
}

const SELECTORS = Object.fromEntries(
  (Object.keys(SELECTOR_INDEXES) as RepresentativeSelector[]).map((selector, position) => [
    selector,
    {
      selector,
      simulationIndex: SELECTOR_INDEXES[selector],
      pathDigest: `path-${position}`,
      geometryDigest: `geom-${position}`,
      definition: `${selector} definition`,
      candidateCount: SIMULATIONS,
      rank: 1,
      distance: 0,
      tieBreak: 'LOWEST_SIMULATION_INDEX_AFTER_DISTANCE_AND_DIFFERENTIAL_DISTANCE',
      selectionInputs: {
        totalRuns: 3 + position, runDifferential: -1,
        homeRuns: 1, awayRuns: 2 + position, innings: position === 4 ? 10 : 9,
      },
      target: { kind: selector },
    },
  ]),
) as PopulationPayload['selectors']

const IDENTITY = {
  supplement_id: 'mlb-v18-population-geometry-d2586fba829c059fb20b4251',
  supplement_sha256: 'd'.repeat(64),
  parent_bundle_sha256: 'a'.repeat(64),
  play_geometry_sha256: 'b'.repeat(64),
  population_digest: 'c'.repeat(64),
  execution_authority_sha256: 'e'.repeat(64),
  wagering_authority: false,
  promotion_status: 'NOT_PROMOTED',
  execution_allowed_in_aisp: false,
  display_authority: 'RESEARCH_SHADOW_POPULATION_GEOMETRY',
} as const

const POPULATION: PopulationPayload = {
  schema_version: 'AISP_MLB_V18_POPULATION_VIEW_V1',
  forecast_id: FORECAST,
  ...IDENTITY,
  simulation_count: SIMULATIONS,
  frame_count: FRAME_COUNT,
  frame_size: FRAME_SIZE,
  master_seed: 20260907,
  counts: {
    plateAppearances: 377078, contactPlays: 246035, geometryBearingPlays: 246035,
    scoringPlays: 30151, runnerSegments: 607156,
  },
  final_population: FINAL,
  illegal_state_count: 0,
  nontermination_count: 0,
  deterministic_replay: true,
  frames: FRAMES,
  selectors: SELECTORS,
  notes: 'Deterministic aggregation over 5,000 complete V18 simulations.',
}

const LAYERS: PopulationLayersPayload = {
  schema_version: 'AISP_MLB_V18_POPULATION_LAYERS_V1',
  forecast_id: FORECAST,
  ...IDENTITY,
  contact_density: {
    coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
    grid: { width: 32, height: 32, xRange: [-1, 1], yRange: [0, 1] },
    normalization: 'RAW_COUNTS',
    layers: {
      ALL: { count: 246035, maximumCellCount: 25883, sparseRawCounts: [[271, 25883], [272, 16484]] },
      SCORING: { count: 30151, maximumCellCount: 4000, sparseRawCounts: [[271, 4000]] },
      OUTCOME_HOME_RUN: { count: 5000, maximumCellCount: 2915, sparseRawCounts: [[527, 2915]] },
    },
  },
  path_density: {
    coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
    grid: { width: 24, height: 24, cellEncoding: 'row_major', tupleEncoding: 'x' },
    representation: 'SPARSE_QUADRATIC_PATH_CELLS',
    semantics: 'MODELLED_GEOMETRY aggregated for display.',
    layers: {
      ALL: [[6924, 12, 2636], [6947, 59, 747], [6948, 60, 4995]],
      SCORING: [[6924, 12, 900]],
      OUTCOME_HOME_RUN: [[6947, 83, 400]],
    },
  },
  runner_traffic: {
    allSegments: {
      BATTER_BOX_TO_FIRST: 131427, FIRST_TO_SECOND: 31054, FIRST_TO_THIRD: 9215,
      THIRD_TO_HOME: 20000, BATTER_BOX_TO_OUT: 217250, FIRST_TO_FIRST: 44036,
    },
    dimensions: { TEAM_HOME: { BATTER_BOX_TO_FIRST: 65000, FIRST_TO_SECOND: 15000 } },
    requiredTrafficCounts: { HOME_TO_FIRST: 131427 },
    representation: 'ANONYMOUS_SEGMENT_TRAFFIC_COUNTS',
    semantics: 'HISTORICAL_RUNNER_TEMPLATE; never player identity.',
  },
}

function event(pa: number, contact: boolean) {
  return {
    pa, inning: 1, half: 'TOP', battingTeam: 'AWAY', battingOrderPosition: 1,
    pitcherState: 'STARTER', pitchingChange: false,
    outcome: contact ? 'SINGLE' : 'STRIKEOUT',
    outsBefore: 0, outsAfter: contact ? 0 : 1,
    baseMaskBefore: 0, baseMaskAfter: contact ? 1 : 0,
    runsScored: 0, homeScoreAfter: 0, awayScoreAfter: 0,
    geometry: {
      contactOccurred: contact,
      battedBallType: contact ? 'LINE_DRIVE' : null,
      locationZone: contact ? '7' : null,
      geometryAuthority: contact ? 'RETROSHEET_LOCATION_ZONE_CENTROID' : 'NO_CONTACT',
      geometryBackoffLevel: null,
      geometryModelVersion: 'MLB_V18_PLAY_GEOMETRY_SAMPLER_V1',
      geometrySamplingSeed: '0'.repeat(16),
      coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
      landingOrFieldingPoint: contact
        ? { x: 0.1, y: 0.5, depth: 0.5, depthClass: 'DEEP', directionDegrees: 8, fairTerritory: true }
        : null,
      exitVelocityMph: null, launchAngleDegrees: null,
      sprayAngleDegrees: null, projectedDistanceFeet: null, landingCoordinateMeasured: null,
    },
    ballTrace: null,
    runnerMovements: [],
    runnerModelVersion: 'MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1',
    runnerBackoffLevel: 'outcome_state_outs_runs_after_bbtype',
    runnerBackoffSupport: 120,
  }
}

/** Six genuinely different games, as the artifact carries. */
const REPRESENTATIVES: PopulationRepresentativesPayload = {
  schema_version: 'AISP_MLB_V18_POPULATION_REPRESENTATIVES_V1',
  forecast_id: FORECAST,
  ...IDENTITY,
  selectors: SELECTORS,
  paths: (Object.keys(SELECTOR_INDEXES) as RepresentativeSelector[]).map((selector, position) => ({
    pathIndex: SELECTOR_INDEXES[selector],
    gameId: GAME,
    plateAppearances: 70 + position,
    pathDigest: `path-${position}`,
    geometryDigest: `geom-${position}`,
    backoffSummary: { runnerBackoffRate: 0.06 },
    events: Array.from({ length: 70 + position }, (_, pa) => event(pa + 1, pa % 2 === 0)),
  })),
  unique_path_count: 6,
  notes: 'One selected simulated game out of 5,000.',
} as unknown as PopulationRepresentativesPayload

const SHADOW = {
  simulation_count: SIMULATIONS,
  sides: { AWAY: { starter: 'Away Starter' }, HOME: { starter: 'Home Starter' } },
  population: { simulation_count: SIMULATIONS },
} as unknown as ShadowForecastView

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount({ layers = true, representatives = true } = {}) {
  vi.spyOn(endpoints, 'shadowForecastPopulation').mockResolvedValue(POPULATION)
  vi.spyOn(endpoints, 'shadowForecastPopulationLayers').mockImplementation(
    layers ? () => Promise.resolve(LAYERS) : () => Promise.reject(new Error('no layers')),
  )
  vi.spyOn(endpoints, 'shadowForecastPopulationRepresentatives').mockImplementation(
    representatives
      ? () => Promise.resolve(REPRESENTATIVES)
      : () => Promise.reject(new Error('no representatives')),
  )
  return render(
    <PopulationView
      forecastId={FORECAST} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" gameId={GAME}
    />,
  )
}

function frameSlider() {
  return screen.getByRole('slider', { name: /accumulation frame/i })
}

function seek(index: number) {
  fireEvent.change(frameSlider(), { target: { value: String(index) } })
}

describe('the population animates its own frames', () => {
  it('represents exactly 100 frames covering 5,000 simulations', async () => {
    mount()
    await screen.findByText(/of 100/)
    expect(frameSlider()).toHaveAttribute('max', '99')
    seek(FRAME_COUNT - 1)
    expect(screen.getByText('Frame 100 of 100')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /5,000 of 5,000 simulations/ }))
      .toBeInTheDocument()
  })

  it('increases the simulation count monotonically as frames advance', async () => {
    mount()
    await screen.findByText(/of 100/)
    const counts: number[] = []
    for (const position of [0, 10, 40, 75, 99]) {
      seek(position)
      const heading = screen.getByRole('heading', { level: 2 }).textContent ?? ''
      counts.push(Number(heading.split(' of ')[0].replace(/,/g, '')))
    }
    expect(counts).toEqual([...counts].sort((a, b) => a - b))
    expect(counts[0]).toBe(FRAME_SIZE)
    expect(counts.at(-1)).toBe(SIMULATIONS)
  })

  it('jumps straight to the completed population', async () => {
    mount()
    await screen.findByText(/of 100/)
    fireEvent.click(screen.getByRole('button', { name: /jump to all 5,000/i }))
    expect(screen.getByRole('heading', { level: 2, name: /5,000 of 5,000/ })).toBeInTheDocument()
  })

  it('shows the accepted population metrics at the final frame', async () => {
    mount()
    await screen.findByText(/of 100/)
    seek(FRAME_COUNT - 1)
    const metrics = screen.getByRole('complementary', { name: /population convergence/i })
    expect(within(metrics).getByText('43.3%')).toBeInTheDocument()
    expect(within(metrics).getByText('56.7%')).toBeInTheDocument()
    expect(within(metrics).getByText('3.47')).toBeInTheDocument()
    expect(within(metrics).getByText('4.42')).toBeInTheDocument()
    expect(within(metrics).getByText('7.89')).toBeInTheDocument()
    expect(within(metrics).getByText('-0.95')).toBeInTheDocument()
    expect(within(metrics).getByText('30.9%')).toBeInTheDocument()
    expect(within(metrics).getByText('10.7%')).toBeInTheDocument()
    expect(within(metrics).getAllByText('246,035')).toHaveLength(2)
    expect(within(metrics).getByText('607,156')).toBeInTheDocument()
    expect(within(metrics).getByText('30,151')).toBeInTheDocument()
  })

  it('never attaches single-game state to the aggregate', async () => {
    const view = mount()
    await screen.findByText(/of 100/)
    seek(50)
    expect(screen.queryByRole('group', { name: /scoreboard/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/plate appearance \d/i)).not.toBeInTheDocument()
    expect(view.container.textContent).not.toMatch(/\b\d outs?\b/)
    expect(view.container.textContent).not.toMatch(/bases/i)
    expect(screen.getByText(/it is\s+not one continuous baseball game/i)).toBeInTheDocument()
  })
})

describe('the field layers', () => {
  it('draws only the artifact’s compact grids', async () => {
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('.pop-contact rect')).not.toBeNull())
    seek(FRAME_COUNT - 1)
    // ALL (2 cells) + SCORING (1 cell); never one path per contact play.
    expect(view.container.querySelectorAll('.pop-contact rect')).toHaveLength(3)
    expect(view.container.querySelectorAll('.pop-paths path')).toHaveLength(3)
    expect(view.container.querySelectorAll('.pop-runners path').length).toBeGreaterThan(0)
  })

  it('toggles each layer independently', async () => {
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('.pop-contact rect')).not.toBeNull())
    seek(FRAME_COUNT - 1)

    fireEvent.click(screen.getByRole('button', { name: /^ball paths$/i }))
    expect(view.container.querySelectorAll('.pop-paths path')).toHaveLength(0)
    expect(view.container.querySelectorAll('.pop-contact rect').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /^runner traffic$/i }))
    expect(view.container.querySelectorAll('.pop-runners path')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: /^scoring plays$/i }))
    expect(view.container.querySelectorAll('.pop-contact--scoring rect')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: /^contact density$/i }))
    expect(view.container.querySelectorAll('.pop-contact rect')).toHaveLength(0)
  })

  it('offers only the filters the artifact carries', async () => {
    mount()
    await waitFor(() =>
      expect(screen.getByLabelText(/population filter/i)).toBeInTheDocument())
    const options = within(screen.getByLabelText(/population filter/i))
      .getAllByRole('option').map((item) => item.textContent)
    expect(options).toContain('All plays')
    expect(options).toContain('Home runs')
    expect(options).toContain('Scoring plays')
    // No layer in the fixture, so it is not offered as a choice.
    expect(options).not.toContain('Triples')
  })

  it('redraws from the selected filter’s own layer', async () => {
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('.pop-contact rect')).not.toBeNull())
    seek(FRAME_COUNT - 1)
    fireEvent.change(screen.getByLabelText(/population filter/i),
      { target: { value: 'OUTCOME_HOME_RUN' } })
    // The home-run layer has one contact cell, plus the scoring overlay.
    expect(view.container.querySelectorAll('.pop-contact rect')).toHaveLength(2)
    expect(view.container.querySelectorAll('.pop-paths path')).toHaveLength(1)
  })
})

describe('representative games', () => {
  it('offers six selectors, each a different simulation', async () => {
    mount()
    const group = await screen.findByRole('group', { name: /representative simulated games/i })
    const buttons = within(group).getAllByRole('button')
    expect(buttons).toHaveLength(6)
    const indexes = buttons.map((item) => item.textContent?.match(/#(\d+)/)?.[1])
    expect(new Set(indexes).size).toBe(6)
    expect(indexes).toContain('0')
  })

  it('opens simulation zero for the low-scoring selector', async () => {
    mount()
    const group = await screen.findByRole('group', { name: /representative simulated games/i })
    fireEvent.click(within(group).getByRole('button', { name: /low scoring/i }))
    await waitFor(() => expect(screen.getByTestId('play-geometry')).toBeInTheDocument())
    const term = screen.getByText('Simulation index')
    expect(term.nextElementSibling?.textContent).toBe('0')
    expect(screen.getByText(/not a summary of\s+them and not the expected exact play sequence/i))
      .toBeInTheDocument()
  })

  it('returns to the population without losing it', async () => {
    mount()
    const group = await screen.findByRole('group', { name: /representative simulated games/i })
    fireEvent.click(within(group).getByRole('button', { name: /typical/i }))
    await waitFor(() => expect(screen.getByTestId('play-geometry')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /back to the 5,000-game population/i }))
    expect(screen.getByRole('heading', { level: 2, name: /of 5,000 simulations/ }))
      .toBeInTheDocument()
  })

  it('builds each payload from that selector’s own path', () => {
    const seen = new Set<string>()
    for (const selector of Object.keys(SELECTOR_INDEXES) as RepresentativeSelector[]) {
      const payload = representativePayload(REPRESENTATIVES, selector, POPULATION, GAME)
      expect(payload).not.toBeNull()
      expect(payload!.events.length).toBe(payload!.plate_appearances)
      seen.add(payload!.events.length + ':' + payload!.supplement_sha256 + ':' + payload!.plate_appearances)
    }
    // Six selectors, six distinct games — never one game shown six times.
    expect(seen.size).toBe(6)
  })
})

describe('the view fails closed', () => {
  it('says so when no verified supplement is registered', async () => {
    vi.spyOn(endpoints, 'shadowForecastPopulation').mockRejectedValue(new Error('404'))
    render(
      <PopulationView
        forecastId={FORECAST} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" gameId={GAME}
      />,
    )
    expect(await screen.findByText(/No checksum-verified population supplement is registered/i))
      .toBeInTheDocument()
  })

  it('keeps the frames usable when the density layers do not load', async () => {
    const view = mount({ layers: false })
    await screen.findByText(/of 100/)
    expect(view.container.querySelector('.pop-contact rect')).toBeNull()
    expect(screen.getByRole('heading', { level: 2, name: /of 5,000 simulations/ }))
      .toBeInTheDocument()
  })
})

describe('the layer helpers', () => {
  it('maps a grid cell into the diamond', () => {
    const grid = { width: 32, height: 32, xRange: [-1, 1] as [number, number], yRange: [0, 1] as [number, number] }
    const home = cellBox(15, grid)
    const deep = cellBox(31 * 32 + 15, grid)
    // Row 0 sits at home plate; the last row is out at the boundary.
    expect(home.centre.y).toBeGreaterThan(deep.centre.y)
    expect(home.width).toBeGreaterThan(0)
  })

  it('decodes a packed ball-path tuple', () => {
    const grid = { width: 24, height: 24 }
    expect(decodePathTuple([6924, 12, 2636], grid)).toEqual({
      origin: 12, control: 12, destination: 12, count: 2636,
    })
  })

  it('reveals the distribution in proportion to the frames accumulated', () => {
    expect(revealFraction(0, SIMULATIONS)).toBe(0)
    expect(revealFraction(2500, SIMULATIONS)).toBeCloseTo(0.5)
    expect(revealFraction(SIMULATIONS, SIMULATIONS)).toBe(1)
  })

  it('offers only all-plays before the layers arrive', () => {
    expect(availableFilters(null)).toEqual([['ALL', 'All plays']])
  })
})
