import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlayTheater, trailsFor, playDuration } from './PlayTheater'
import { ScenarioLaboratory, firstDivergence } from './ScenarioLaboratory'
import {
  clockLabel, lineToGainX, scrimmageX, segmentPath, situationLabel, traverse, type Play,
} from './playGeometry'
import type { NflPlayRepresentative, NflPlayTheaterIndex } from '../../types/nfl'

const state = (over: Partial<Play['before']> = {}): Play['before'] => ({
  possession: 'HOME', quarter: 1, clockSeconds: 900, gameSecondsRemaining: 3600,
  down: 1, distance: 10, yardline100: 75, homeScore: 0, awayScore: 0, phase: 'SCRIMMAGE', ...over,
})

const play = (over: Partial<Play> = {}): Play => ({
  play_number: 1, drive_number: 1, play_type: 'RUSH', description: 'SEA: Rush; 5 yards.',
  before: state(), after: state({ yardline100: 70, down: 2, distance: 5, clockSeconds: 860 }),
  outcome: { endYardline100: 70, seconds: 40, homePointsAdded: 0, awayPointsAdded: 0 },
  path: {
    kind: 'RUSH_GROUND', direction: 'LEFT',
    segments: [{
      kind: 'RUSH_GROUND', interpolation: 'LINEAR', possession: 'HOME',
      start: [75, 0.5], control: [], end: [70, 0.5],
    }],
    markers: [],
  },
  ...over,
})

const touchdown = play({
  play_number: 2, play_type: 'PASS_COMPLETE', description: 'SEA: Touchdown pass.',
  before: state({ yardline100: 70, down: 2, distance: 5, clockSeconds: 860 }),
  after: state({ yardline100: 0, down: 0, distance: 0, clockSeconds: 830, homeScore: 6 }),
  outcome: { endYardline100: 0, seconds: 30, homePointsAdded: 6, awayPointsAdded: 0, scoringType: 'TD' },
  path: {
    kind: 'PASS_WITH_MODELED_AIR_AND_YAC', direction: 'LEFT',
    segments: [{
      kind: 'PASS_MODELED_AIR_CURVE', interpolation: 'CUBIC_BEZIER', possession: 'HOME',
      start: [70, 0.5], control: [[55, 0.3], [30, 0.3]], end: [10, 0.5],
    }, {
      kind: 'MODELED_YAC_GROUND', interpolation: 'LINEAR', possession: 'HOME',
      start: [10, 0.5], control: [], end: [0, 0.5],
    }],
    markers: [{ kind: 'TD', point: [0, 0.5] }],
  },
})

const penalty = play({
  play_number: 3, play_type: 'PENALTY', description: 'SEA: Penalty; no play.',
  before: state({ yardline100: 40, clockSeconds: 800 }),
  after: state({ yardline100: 45, clockSeconds: 800 }),
  outcome: { endYardline100: 45, seconds: 0, homePointsAdded: 0, awayPointsAdded: 0 },
  path: { kind: 'NO_BALL_MOTION', direction: 'LEFT', segments: [], markers: [{ kind: 'PENALTY', point: [40, 0.5] }] },
})

const scenario = (id: string, label: string) => ({
  scenario_id: id, label, explanation: `${label} explanation`,
  parameters: { parameter: 'x', multiplier: 1 }, interpretation: 'SENSITIVITY',
  turnover_sensitivity_meaning: null, model_input_change: id !== 'BASE',
  score_summary: {
    homeWinProbability: id === 'BASE' ? 0.5 : 0.52, awayWinProbability: 0.48, tieProbability: 0.02,
    expectedHomeScore: 21, expectedAwayScore: 20,
    expectedTotal: id === 'BASE' ? 41 : 45, expectedMarginHomeMinusAway: 1,
  },
  scenario_delta: null, quantiles: null,
  reaching_overtime_probability: 0.04, final_tie_probability: 0.02,
  draw_count: 1000, seed: 930947, population_accounting: null,
  representatives: [
    { position: 0, label: 'Typical', simulationIndex: id === 'BASE' ? 5 : 9, seed: 1, homeScore: 21, awayScore: 20, regulationHomeScore: 21, regulationAwayScore: 20, reachedOvertime: false, finalTie: false, playCount: 3, driveCount: 1, turnovers: 0, sacks: 0, playTypeCounts: null },
    { position: 1, label: 'Close', simulationIndex: 7, seed: 2, homeScore: 20, awayScore: 20, regulationHomeScore: 20, regulationAwayScore: 20, reachedOvertime: true, finalTie: true, playCount: 3, driveCount: 1, turnovers: 0, sacks: 0, playTypeCounts: null },
  ],
  supplement_sha256: `sha-${id}`, play_count: 3,
  population_drive_stats: {
    draws: 1000, mean_home_score: 21, mean_away_score: 20, mean_total: 41,
    mean_drives: id === 'BASE' ? 21 : 22, mean_plays: 156, plays_per_drive: 7.4,
    points_per_drive: 1.95, reached_overtime: 0.04,
  },
})

const index: NflPlayTheaterIndex = {
  schema_version: 'AISP_NFL_PLAY_THEATER_READ_V1', event_id: '2026_01_NE_SEA',
  engine: {
    engine_id: 'NFL_DEVELOPMENT_HIERARCHICAL_PLAY_V1', label: 'Play-level development engine',
    version: '1.0.0', training_cutoff: '2025-02-09', generating_law: null,
    historical_evidence_through_date: '2024-12-31', historical_evidence_precision: 'GAME_DATE',
    input_treatment: null, explains_m1_forecast: false, related_m1_forecast_id: 'm1',
  },
  governance: {
    status: 'RESEARCH_SHADOW', promotion_status: 'NOT_PROMOTED',
    execution_allowed_in_aisp: false, wagering_authority: false,
  },
  limitations: ['Research-only and uncalibrated.', 'Schematic modeled geometry, not measured tracking.'],
  m1_comparison: null, home_team: null, away_team: null,
  scenarios: [scenario('BASE', 'Base'), scenario('PASS_HEAVY', 'Pass-heavy')],
  pairing: {
    population_is_seed_paired: true,
    population_note: 'Same 1,000 seeds under both settings.',
    representatives_are_seed_paired: false,
    representative_note: 'Each scenario selects its own six games.',
    seed_paired_games: {
      PASS_HEAVY: [{
        simulation_index: 7, seed: 2,
        base: { label: 'Close', position: 1, home_score: 20, away_score: 20 },
        challenger: { label: 'Close', position: 1, home_score: 20, away_score: 20 },
      }],
    },
  },
  geometry_notice: 'Ball paths are modeled schematic geometry, not measured tracking.',
}

const game: NflPlayRepresentative = {
  schema_version: 'AISP_NFL_PLAY_THEATER_READ_V1', event_id: '2026_01_NE_SEA',
  scenario_id: 'BASE', position: 0, available: true, supplement_sha256: 'sha-BASE',
  label: 'Typical', simulation_index: 5, seed: 1, home_score: 6, away_score: 0,
  regulation_home_score: 6, regulation_away_score: 0, reached_overtime: false, final_tie: false,
  diagnostics: {}, plays: [play(), touchdown, penalty],
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('play geometry', () => {
  it('re-expresses distance-to-goal in the fixed left-to-right frame', () => {
    // HOME attacks left, so its distance to goal counts up from the left goal.
    expect(scrimmageX(state({ possession: 'HOME', yardline100: 65 }))).toBe(65)
    // AWAY attacks right, so the same number is measured from the other end.
    expect(scrimmageX(state({ possession: 'AWAY', yardline100: 65 }))).toBe(35)
  })

  it('places the line to gain on the correct side and drops it at first and goal', () => {
    expect(lineToGainX(state({ possession: 'HOME', yardline100: 65, distance: 10 }))).toBe(55)
    expect(lineToGainX(state({ possession: 'AWAY', yardline100: 65, distance: 10 }))).toBe(45)
    // The goal line IS the line to gain; drawing a second one would be wrong.
    expect(lineToGainX(state({ yardline100: 6, distance: 10 }))).toBeNull()
    expect(lineToGainX(state({ phase: 'KICKOFF', down: 0 }))).toBeNull()
  })

  it('builds a cubic path only when two control points are supplied', () => {
    const [air, ground] = touchdown.path.segments
    expect(segmentPath(air)).toContain(' C ')
    expect(segmentPath(ground)).toContain(' L ')
  })

  it('walks the whole play at one speed across joined segments', () => {
    const walk = traverse(touchdown.path.segments)
    const start = walk.at(0)
    const end = walk.at(1)
    expect(start.x).toBeGreaterThan(end.x) // HOME moves toward the left goal
    // Constant speed: half the progress covers about half the distance.
    const mid = walk.at(0.5)
    const first = Math.hypot(mid.x - start.x, mid.y - start.y)
    const second = Math.hypot(end.x - mid.x, end.y - mid.y)
    expect(Math.abs(first - second) / walk.length).toBeLessThan(0.05)
  })

  it('reads the clock and the situation straight from the state', () => {
    expect(clockLabel(state({ quarter: 3, clockSeconds: 125 }))).toEqual({ period: 'Q3', clock: '2:05' })
    expect(clockLabel(state({ quarter: 5, clockSeconds: 600 }))).toEqual({ period: 'OT', clock: '10:00' })
    expect(situationLabel(state({ down: 3, distance: 7, yardline100: 34 }), 'SEA', 'NE'))
      .toBe('3rd & 7 at NE 34')
    expect(situationLabel(state({ down: 1, distance: 10, yardline100: 80 }), 'SEA', 'NE'))
      .toBe('1st & 10 at SEA 20')
    expect(situationLabel(state({ phase: 'FINAL' }), 'SEA', 'NE')).toBe('Final')
  })
})

describe('play theater', () => {
  const mount = () => render(
    <PlayTheater index={index} game={game} scenarioId="BASE" position={0} loading={false}
      error={null} onScenario={vi.fn()} onRepresentative={vi.fn()} homeTeam="SEA" awayTeam="NE" />)

  it('shows the first play, its situation and the running score', () => {
    mount()
    // A completed play shows its AFTER state: the ball has already moved.
    expect(screen.getByTestId('situation')).toHaveTextContent('2nd & 5 at SEA 30')
    expect(screen.getByTestId('game-clock')).toHaveTextContent('Q1')
    expect(screen.getByTestId('home-score')).toHaveTextContent('0')
  })

  it('steps to the next play and carries the score with it', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Next play' }))
    expect(screen.getByTestId('play-description')).toHaveTextContent('Play 2 of 3')
    expect(screen.getByTestId('home-score')).toHaveTextContent('6')
  })

  it('scrubs directly to any play', () => {
    mount()
    fireEvent.change(screen.getByTestId('play-scrubber'), { target: { value: '2' } })
    expect(screen.getByTestId('play-description')).toHaveTextContent('Play 3 of 3')
  })

  it('draws the ball on a play that has no path rather than dropping it', () => {
    mount()
    fireEvent.change(screen.getByTestId('play-scrubber'), { target: { value: '2' } })
    expect(screen.getByTestId('football-marker')).toBeInTheDocument()
    expect(screen.queryAllByTestId('play-segment')).toHaveLength(0)
  })

  it('keeps a persistent trail only for earlier plays of the same drive', () => {
    expect(trailsFor([play(), touchdown, penalty], 2)).toHaveLength(2)
    const other = { ...play(), drive_number: 2 }
    expect(trailsFor([play(), other], 1)).toHaveLength(0)
  })

  it('filters the play list to scoring plays', () => {
    mount()
    fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'scoring' } })
    const list = screen.getByRole('list', { name: 'Plays' })
    expect(list.querySelectorAll('li')).toHaveLength(1)
  })

  it('advances without animating when reduced motion is requested', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    mount()
    expect(screen.getByText(/Reduced motion is on/)).toBeInTheDocument()
    expect(playDuration(touchdown, 1, true)).toBe(0)
    expect(playDuration(touchdown, 1, false)).toBeGreaterThan(0)
    expect(playDuration(touchdown, 2, false)).toBeLessThan(playDuration(touchdown, 1, false))
  })

  it('carries the producer limitations where a reader can find them', () => {
    mount()
    expect(screen.getByText(/Schematic modeled geometry, not measured tracking/)).toBeInTheDocument()
    expect(screen.getByText(index.geometry_notice)).toBeInTheDocument()
  })

  it('names the exact cause when the plays cannot be read', () => {
    render(<PlayTheater index={index} game={{ ...game, available: false, plays: undefined, cause: 'PLAY_ARTIFACT_FILE_MISSING', detail: 'file gone' }}
      scenarioId="BASE" position={0} loading={false} error={null}
      onScenario={vi.fn()} onRepresentative={vi.fn()} homeTeam="SEA" awayTeam="NE" />)
    expect(screen.getByRole('alert')).toHaveTextContent('PLAY_ARTIFACT_FILE_MISSING')
  })
})

describe('scenario laboratory', () => {
  const load = vi.fn(async (scenarioId: string) => ({
    ...game, scenario_id: scenarioId,
    plays: scenarioId === 'BASE' ? [play(), touchdown] : [play(), { ...touchdown, play_type: 'SACK' as const }],
  }))

  const mount = () => render(
    <ScenarioLaboratory index={index} gameId="g1" homeTeam="SEA" awayTeam="NE" load={load} />)

  it('reports the aggregate comparison as like-for-like over the same seeds', async () => {
    mount()
    expect(await screen.findByText((_, node) => (node?.textContent ?? '')
      .includes('re-runs the same 1,000 seeds'), { selector: 'p' })).toBeInTheDocument()
  })

  it('says plainly when the two shown games are the same draw', async () => {
    mount()
    // PASS_HEAVY publishes simulation 7 in both scenarios, so this pair is real.
    expect(await screen.findByText(/the same draw/i)).toBeInTheDocument()
  })

  it('falls back to an explicit illustrative warning when no pair exists', async () => {
    const unpaired = { ...index, pairing: { ...index.pairing, seed_paired_games: {} } }
    render(<ScenarioLaboratory index={unpaired} gameId="g1" homeTeam="SEA" awayTeam="NE" load={load} />)
    expect(await screen.findByText(/two different draws/i)).toBeInTheDocument()
  })

  it('finds the first play at which two paired runs stop agreeing', () => {
    const a = [play(), touchdown]
    const b = [play(), { ...touchdown, play_type: 'SACK' }]
    expect(firstDivergence(a, b)).toBe(1)
    expect(firstDivergence(a, a)).toBeNull()
    expect(firstDivergence(a, [play()])).toBe(1)
  })

  it('moves both fields from one scrubber', async () => {
    mount()
    const scrubber = await screen.findByTestId('sync-scrubber')
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    fireEvent.change(scrubber, { target: { value: '1' } })
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
    // Both fields moved together: each shows its own play 2.
    expect(screen.getAllByText(/Play 2 of 2/)).toHaveLength(2)
  })

  it('highlights the rows that actually moved', async () => {
    mount()
    const total = (await screen.findByText('Expected total')).closest('tr')
    expect(total).toHaveClass('is-moved')
    const margin = screen.getByText('Expected margin').closest('tr')
    expect(margin).not.toHaveClass('is-moved')
  })
})
