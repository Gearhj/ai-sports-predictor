/**
 * The Play Geometry view: accumulation, layer control, and the boundary
 * between what the supplement supports and what the screen may claim.
 *
 * Focused on the new view only. The Broadcast Replay keeps its own suite.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PlayGeometryView, SimulationPopulationView } from './PlayGeometryView'
import {
  ballPathD, runnerPathD, trailOpacity, visibleEvents,
} from './GeometryPathLayers'
import type {
  GeometryEvent, PlayGeometryPayload, ShadowForecastView,
} from '../../types/shadow'

function trace(overrides: Record<string, unknown> = {}) {
  return {
    coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1' as const,
    start: { x: 0, y: 0 },
    control: [{ x: 0.05, y: 0.3, lift: 0.06 }],
    end: { x: 0.12, y: 0.62 },
    traceStyle: 'LINE' as const,
    normalizedDuration: 0.5,
    fairTerritory: true,
    outcomeCompatibility: 'COMPATIBLE_WITH_PERSISTED_OUTCOME',
    semantics: 'PROJECTED_VISUAL_TRAJECTORY_FROM_SIMULATED_GEOMETRY_NOT_A_MEASURED_FLIGHT_PATH',
    ...overrides,
  } as GeometryEvent['ballTrace']
}

function event(overrides: Partial<GeometryEvent> & { pa: number }): GeometryEvent {
  const contact = overrides.ballTrace !== undefined
  return {
    inning: 1, half: 'TOP', battingTeam: 'AWAY', battingOrderPosition: 1,
    pitcherState: 'STARTER', pitchingChange: false, outcome: 'BIP_OUT',
    outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0,
    runsScored: 0, homeScoreAfter: 0, awayScoreAfter: 0,
    geometry: {
      contactOccurred: contact,
      battedBallType: contact ? 'LINE_DRIVE' : null,
      locationZone: contact ? '7' : null,
      geometryAuthority: contact ? 'RETROSHEET_LOCATION_ZONE_CENTROID' : 'NO_CONTACT',
      geometryBackoffLevel: contact ? 'outcome_bbtype_zone' : null,
      geometryModelVersion: 'MLB_V18_PLAY_GEOMETRY_SAMPLER_V1',
      geometrySamplingSeed: '0'.repeat(16),
      coordinateSystem: 'ASP_NORMALIZED_DIAMOND_V1',
      landingOrFieldingPoint: contact
        ? { x: 0.12, y: 0.62, depth: 0.63, depthClass: 'DEEP', directionDegrees: -11, fairTerritory: true }
        : null,
      exitVelocityMph: null, launchAngleDegrees: null,
      sprayAngleDegrees: null, projectedDistanceFeet: null,
      landingCoordinateMeasured: null,
    },
    ballTrace: null,
    runnerMovements: [],
    runnerModelVersion: 'MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1',
    runnerBackoffLevel: 'outcome_state_outs_runs_after_bbtype',
    runnerBackoffSupport: 240,
    ...overrides,
  } as GeometryEvent
}

function movement(overrides: Partial<GeometryEvent['runnerMovements'][number]> = {}) {
  return {
    anonymousRunnerId: 'r0001-01', role: 'BATTER', from: 'BATTER_BOX', to: 'FIRST',
    scored: false, out: false, movementReason: 'BATTER_REACHED',
    startFraction: 0, endFraction: 0.6, authority: 'FITTED_HISTORICAL_TEMPLATE',
    ...overrides,
  } as GeometryEvent['runnerMovements'][number]
}

/** Five plays: two innings, one strikeout with no contact, one scoring play. */
const EVENTS: GeometryEvent[] = [
  event({ pa: 1, outcome: 'STRIKEOUT', runnerMovements: [movement({ to: 'OUT', out: true, movementReason: 'BATTER_RETIRED' })] }),
  event({ pa: 2, outcome: 'SINGLE', outsBefore: 1, outsAfter: 1, baseMaskAfter: 1, ballTrace: trace(), runnerMovements: [movement({ anonymousRunnerId: 'r0001-02' })] }),
  event({
    pa: 3, outcome: 'DOUBLE', outsBefore: 1, outsAfter: 1, baseMaskBefore: 1, baseMaskAfter: 6,
    ballTrace: trace({ traceStyle: 'ARC_HIGH' }),
    runnerMovements: [
      movement({ anonymousRunnerId: 'r0001-03', to: 'SECOND' }),
      movement({ anonymousRunnerId: 'r0001-02', role: 'RUNNER', from: 'FIRST', to: 'THIRD', movementReason: 'FORCED_ADVANCE' }),
    ],
  }),
  event({
    pa: 4, inning: 2, half: 'BOTTOM', battingTeam: 'HOME', outcome: 'SINGLE',
    baseMaskBefore: 0, baseMaskAfter: 1, runsScored: 1, homeScoreAfter: 1,
    ballTrace: trace({ traceStyle: 'GROUND' }),
    runnerMovements: [
      movement({ anonymousRunnerId: 'r0002-01' }),
      movement({ anonymousRunnerId: 'r0002-02', role: 'RUNNER', from: 'THIRD', to: 'HOME', scored: true, movementReason: 'SCORED_ON_HIT' }),
    ],
  }),
  event({
    pa: 5, inning: 2, half: 'BOTTOM', battingTeam: 'HOME', outcome: 'BIP_OUT',
    outsBefore: 0, outsAfter: 1, baseMaskBefore: 1, baseMaskAfter: 1, homeScoreAfter: 1,
    ballTrace: trace({ traceStyle: 'GROUND', fairTerritory: true }),
    runnerMovements: [movement({ anonymousRunnerId: 'r0002-03', to: 'OUT', out: true, movementReason: 'BATTER_RETIRED' })],
  }),
]

const GEOMETRY: PlayGeometryPayload = {
  schema_version: 'AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1',
  forecast_id: 'mlb-v18-1', game_id: 'game-1', engine_id: 'MLB_V18_WHOLE_GAME',
  supplement_id: 'mlb-v18-play-geometry-7b638efe0caab3b4b3ffcf7d',
  supplement_sha256: '7'.repeat(64),
  parent_bundle_id: 'mlb-v18-whole-game-a6e7e99122f1b60701a4728b',
  parent_bundle_sha256: 'a'.repeat(64),
  coordinate_system: { name: 'ASP_NORMALIZED_DIAMOND_V1', origin: 'HOME_PLATE' },
  geometry_model_version: 'MLB_V18_PLAY_GEOMETRY_SAMPLER_V1',
  runner_model_version: 'MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1',
  registered_at: '2026-09-08T00:00:00+00:00',
  backoff_summary: { runnerBackoffRate: 0.0625 },
  limitations: ['Exit velocity is not registered.'],
  wagering_authority: false,
  promotion_status: 'NOT_PROMOTED',
  execution_allowed_in_aisp: false,
  display_authority: 'RESEARCH_SHADOW_PLAY_GEOMETRY',
  plate_appearances: EVENTS.length,
  contact_plays: 4,
  ball_traces: 4,
  runner_movements: 7,
  events: EVENTS,
  population: {
    simulationCount: 5000,
    populationGeometryAvailable: false,
    populationGeometryStatus: 'AWAITING_CHECKSUM_VERIFIED_MODEL_STUDIO_SUPPLEMENT',
  },
  notes: 'One representative simulated game.',
} as unknown as PlayGeometryPayload

const SHADOW = {
  simulation_count: 5000,
  sides: { AWAY: { starter: 'Away Starter' }, HOME: { starter: 'Home Starter' } },
  population: {
    simulation_count: 5000, home_win_probability: 43.34, away_win_probability: 56.66,
    expected_total_runs: 7.8924,
  },
} as unknown as ShadowForecastView

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount(geometry: PlayGeometryPayload = GEOMETRY) {
  return render(
    <PlayGeometryView geometry={geometry} shadow={SHADOW} awayTeam="TOR" homeTeam="ATH" />,
  )
}

/** The replay scrubber, distinct from the trail-persistence range. */
function seek(index: number) {
  const sliders = screen.getAllByRole('slider')
  const scrubber = sliders.find(
    (item) => !/persistence/i.test(item.getAttribute('aria-label') ?? ''),
  )!
  fireEvent.change(scrubber, { target: { value: String(index) } })
}

function scope(label: RegExp) {
  fireEvent.click(within(screen.getByRole('group', { name: /geometry scope/i }))
    .getByRole('button', { name: label }))
}

const ballPaths = (view: { container: HTMLElement }) =>
  view.container.querySelectorAll('.pg-ball')
const runnerPaths = (view: { container: HTMLElement }) =>
  view.container.querySelectorAll('.pg-runner')

describe('only supplied geometry is ever drawn', () => {
  it('draws no ball path for a plate appearance with no contact', () => {
    const view = mount()
    scope(/current play/i)
    // Opens on PA 1, a strikeout: no trace was supplied, so nothing is drawn.
    expect(ballPaths(view)).toHaveLength(0)
    expect(screen.getByText(/no contact/i)).toBeInTheDocument()
  })

  it('draws exactly the one supplied trace for a contact play', () => {
    const view = mount()
    scope(/current play/i)
    seek(1)
    expect(ballPaths(view)).toHaveLength(1)
  })

  it('draws runner paths only from the supplied movements', () => {
    const view = mount()
    scope(/current play/i)
    seek(2)
    // Two movements, both between distinct bases, so two paths.
    expect(runnerPaths(view)).toHaveLength(2)
  })

  it('never names a runner', () => {
    mount()
    seek(3)
    const panel = screen.getByRole('complementary', { name: /plate appearance/i })
    expect(within(panel).getByText(/anonymous/i)).toBeInTheDocument()
    // The movement list carries anonymous ids and base names only: any real
    // name would show up as a capitalised word.
    const list = within(panel).getByRole('list')
    expect(list.textContent).not.toMatch(/[A-Z]/)
    expect(list.textContent).toMatch(/01|02|03/)
  })
})

describe('accumulation scopes', () => {
  it('current play excludes previous plays', () => {
    const view = mount()
    scope(/current play/i)
    seek(4)
    expect(ballPaths(view)).toHaveLength(1)
  })

  it('current inning excludes earlier innings', () => {
    const view = mount()
    scope(/current inning/i)
    seek(4)
    // Bottom of the 2nd holds PAs 4 and 5 only; the first inning is excluded.
    expect(ballPaths(view)).toHaveLength(2)
  })

  it('full game accumulates only through the current plate appearance', () => {
    const view = mount()
    scope(/full game/i)
    seek(2)
    // PAs 1-3: two traces supplied, and PA 4-5 are in the future.
    expect(ballPaths(view)).toHaveLength(2)
  })

  it('scrubbing backward removes the future paths', () => {
    const view = mount()
    scope(/full game/i)
    seek(4)
    expect(ballPaths(view)).toHaveLength(4)
    seek(1)
    expect(ballPaths(view)).toHaveLength(1)
  })

  it('the final position holds every supported path exactly once', () => {
    const view = mount()
    scope(/full game/i)
    seek(EVENTS.length - 1)
    expect(ballPaths(view)).toHaveLength(GEOMETRY.ball_traces)
    expect(runnerPaths(view)).toHaveLength(
      // Every movement except the retired batters, drawn as stubs, is a path;
      // stubs are drawn too, so the total equals the supplied movement count.
      EVENTS.reduce((total, item) => total + item.runnerMovements.length, 0),
    )
  })

  it('clearing returns to the opening plate appearance', () => {
    const view = mount()
    scope(/full game/i)
    seek(4)
    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    expect(ballPaths(view)).toHaveLength(0)
  })
})

describe('layers toggle independently', () => {
  it('hides ball paths without hiding runner paths', () => {
    const view = mount()
    scope(/full game/i)
    seek(4)
    fireEvent.click(screen.getByRole('button', { name: /ball paths/i }))
    expect(ballPaths(view)).toHaveLength(0)
    expect(runnerPaths(view).length).toBeGreaterThan(0)
  })

  it('hides runner paths without hiding ball paths', () => {
    const view = mount()
    scope(/full game/i)
    seek(4)
    fireEvent.click(screen.getByRole('button', { name: /runner paths/i }))
    expect(runnerPaths(view)).toHaveLength(0)
    expect(ballPaths(view).length).toBeGreaterThan(0)
  })

  it('restricts the accumulation to scoring plays', () => {
    const view = mount()
    scope(/full game/i)
    seek(4)
    fireEvent.click(screen.getByRole('button', { name: /scoring plays only/i }))
    expect(ballPaths(view)).toHaveLength(1)
  })
})

describe('the information panel stays honest', () => {
  it('reports persisted state and never renders a missing value as zero', () => {
    mount()
    const panel = screen.getByRole('complementary', { name: /plate appearance/i })
    expect(within(panel).getByText('Plate appearance 1')).toBeInTheDocument()
    expect(within(panel).getByText('0 → 1')).toBeInTheDocument()
    // PA 1 has no contact: the batted-ball facts read as unavailable, not 0.
    expect(within(panel).getAllByText(/not available|no contact/i).length).toBeGreaterThan(0)
    expect(within(panel).queryByText(/^0 mph$/)).not.toBeInTheDocument()
  })

  it('refuses to estimate exit velocity, launch angle, spray angle or distance', () => {
    mount()
    const panel = screen.getByRole('complementary', { name: /plate appearance/i })
    for (const label of [/exit velocity/i, /launch angle/i, /spray angle/i, /projected distance/i]) {
      const term = within(panel).getAllByText(label, { selector: 'dt' })[0]
      expect(term.nextElementSibling?.textContent).toMatch(/not available/i)
    }
  })

  it('labels the drawn trajectory as modelled, not measured', () => {
    mount()
    expect(
      screen.getByText(/modelled visual trajectory from simulated geometry/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/runner identities\s+are anonymous/i)).toBeInTheDocument()
  })

  it('attributes runner movement to a template, never to tracking', () => {
    mount()
    seek(2)
    const panel = screen.getByRole('complementary', { name: /plate appearance/i })
    fireEvent.click(within(panel).getByText(/technical evidence/i))
    expect(within(panel).getByText(/fitted historical template/i)).toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('advances and rewinds with the arrow keys', () => {
    mount()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('Plate appearance 2')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByText('Plate appearance 1')).toBeInTheDocument()
  })

  it('returns to the first plate appearance with Home', () => {
    mount()
    seek(4)
    fireEvent.keyDown(window, { key: 'Home' })
    expect(screen.getByText('Plate appearance 1')).toBeInTheDocument()
  })

  it('leaves theater mode on Escape even while a control holds focus', () => {
    mount()
    const enter = screen.getByRole('button', { name: /theater mode/i })
    fireEvent.click(enter)
    expect(document.querySelector('.geometry--full')).not.toBeNull()
    // Clicking left focus on that button; Escape must still work.
    fireEvent.keyDown(enter, { key: 'Escape' })
    expect(document.querySelector('.geometry--full')).toBeNull()
  })

  it('keeps manual navigation under reduced motion', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation(((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia)

    const view = mount()
    // Reduced motion opens on the completed game rather than animating to it.
    expect(screen.getByText(`Plate appearance ${EVENTS.length}`)).toBeInTheDocument()
    expect(view.container.querySelector('.pg-ball--draw')).toBeNull()
    seek(1)
    expect(screen.getByText('Plate appearance 2')).toBeInTheDocument()
  })
})

describe('the population is a separate fact', () => {
  it('reports the population honestly as pending', () => {
    render(<SimulationPopulationView shadow={SHADOW} geometry={GEOMETRY} />)
    expect(screen.getByText(/5,000 completed simulations/i)).toBeInTheDocument()
    expect(screen.getByText(/population geometry is not yet registered/i)).toBeInTheDocument()
    expect(
      screen.getByText(/AWAITING_CHECKSUM_VERIFIED_MODEL_STUDIO_SUPPLEMENT/),
    ).toBeInTheDocument()
  })

  it('never presents the one representative path as the population', () => {
    const view = render(<SimulationPopulationView shadow={SHADOW} geometry={GEOMETRY} />)
    expect(screen.getByText(/1 of 5,000 · 5 plate appearances/i)).toBeInTheDocument()
    // No density, heatmap or accumulation frame is drawn from one trace.
    expect(view.container.querySelector('.pg-ball')).toBeNull()
    expect(view.container.querySelector('svg')).toBeNull()
  })

  it('never shows a Gen 1 draw count as a V18 population', () => {
    render(<SimulationPopulationView shadow={SHADOW} geometry={GEOMETRY} />)
    expect(document.body.textContent).not.toMatch(/20,000|20000/)
  })

  it('never presents the plate-appearance count as a simulation count', () => {
    mount()
    const provenance = screen.getByText(/one representative simulated game/i)
    expect(provenance.textContent).toMatch(/5,000 completed\s+V18 simulations/)
    expect(provenance.textContent).not.toMatch(/5 simulations/)
  })
})

describe('the scope and path helpers', () => {
  it('never returns an event after the current index', () => {
    for (const chosen of ['PLAY', 'INNING', 'GAME'] as const) {
      const shown = visibleEvents(EVENTS, 2, chosen)
      expect(shown.every((item) => item.pa <= 3)).toBe(true)
    }
  })

  it('keeps the active play fully opaque and fades older ones', () => {
    const current = EVENTS[4]
    expect(trailOpacity(current, current, 14)).toBe(1)
    const older = trailOpacity(EVENTS[0], current, 14)
    expect(older).toBeLessThan(1)
    expect(older).toBeGreaterThan(0)
    // A longer persistence keeps an older trail more legible.
    expect(trailOpacity(EVENTS[0], current, 30)).toBeGreaterThanOrEqual(older)
  })

  it('bends the ball path through the supplement’s own control point', () => {
    const d = ballPathD(EVENTS[1].ballTrace!)
    expect(d).toMatch(/^M[\d.]+ [\d.]+ Q/)
  })

  it('draws no runner path when the runner does not move', () => {
    expect(runnerPathD(movement({ from: 'FIRST', to: 'FIRST' }))).toBeNull()
  })

  it('draws a retired runner as a stub, never as a route to a base', () => {
    const d = runnerPathD(movement({ from: 'FIRST', to: 'OUT', out: true }))
    expect(d).toMatch(/l0 -26$/)
  })
})
