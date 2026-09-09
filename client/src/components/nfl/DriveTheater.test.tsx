import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DriveTheater, driveClock, recordedDriveGeometry } from './DriveTheater'
import { ScenarioComparison } from './ScenarioComparison'
import type { NflDriveSupplement, NflExplorer, NflRecordedDrive } from '../../types/nfl'

vi.mock('echarts-for-react', () => ({ default: () => <div aria-label="Recorded distribution chart" /> }))
const drive: NflRecordedDrive = { driveNumber: 1, period: 'REGULATION_HALF_1', periodSecondsBefore: 1800, periodSecondsAfter: 1500, durationSeconds: 300, possession: 'HOME', offenseTeam: 'SEA', startYardline100: 75, endYardline100: 1, outcome: 'TD', homePointsAdded: 7, awayPointsAdded: 0, homeScoreAfter: 7, awayScoreAfter: 0 }
const awayDrive: NflRecordedDrive = { ...drive, driveNumber: 2, possession: 'AWAY', offenseTeam: 'NE', startYardline100: 80, endYardline100: 40, outcome: 'PUNT', homePointsAdded: 0, periodSecondsBefore: 1500, periodSecondsAfter: 1250 }
const clockEnd: NflRecordedDrive = { ...drive, driveNumber: 3, period: 'REGULATION_HALF_2', periodSecondsBefore: 15, periodSecondsAfter: 0, endYardline100: null, outcome: 'CLOCK_EXPIRED', homePointsAdded: 0 }
const q = { p05: 0, p25: 0, p50: 7, p75: 7, p95: 7 }
const explorer: NflExplorer = { simulation: { kind: 'MONTE_CARLO', count: 1000, seed: 1 }, expected: { home: 7, away: 0, margin_home: 7, total: 7 }, outcomes: { home: 1, away: 0, tie: 0 }, joint_score_distribution: [{ home: 7, away: 0, probability: 1 }], margin_distribution: [{ margin: 7, probability: 1 }], total_distribution: [{ total: 7, probability: 1 }], quantiles: { home: q, away: q, margin: q, total: q }, most_likely_scores: [], cells: 1 }
const base: NflDriveSupplement = { supplement_sha256: 'base', supplement_id: 'base', kind: 'DRIVE_SIMULATION', engine_id: 'independent-drive-v2', forecast_id: 'forecast-base', artifact_version: 'V1', simulation_kind: 'MONTE_CARLO', simulation_count: 1000, simulation_seed: 1, source_cutoff_utc: null, published_utc: null, imported_at_utc: null, summaries: null, scenario: { scenarioId: 'BASE', paceDurationMultiplier: 1 }, explorer, paths: ['Typical', 'Home win', 'Away win', 'High scoring', 'Low scoring', 'Overtime'].map((label, i) => ({ label, simulationIndex: i === 0 ? 946 : i === 2 ? 0 : i, seed: 10 + i, homeScore: 7, awayScore: 0, reachedOvertime: false, finalTie: false, drives: [drive, awayDrive, clockEnd] })) }
const pace: NflDriveSupplement = { ...base, supplement_sha256: 'pace', scenario: { scenarioId: 'PACE_DURATION_0.95', paceDurationMultiplier: .95 }, explorer: { ...explorer, expected: { ...explorer.expected, total: 8, home: 8, margin_home: 8 } } }

beforeEach(() => { vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('persisted drive playback', () => {
  it('uses raw distance-to-opponent direction, recorded half clocks, and preserves unknown endpoints', () => {
    expect(recordedDriveGeometry(drive)).toEqual({ fromYard: 75, toYard: 1 })
    expect(recordedDriveGeometry(awayDrive)).toEqual({ fromYard: 20, toYard: 60 })
    expect(recordedDriveGeometry(clockEnd).toYard).toBeNull()
    expect(driveClock(drive, 0)).toEqual({ period: 'Q1', clock: '15:00' })
    expect(driveClock({ ...drive, periodSecondsAfter: 870 })).toEqual({ period: 'Q2', clock: '14:30' })
    expect(driveClock(clockEnd)).toEqual({ period: 'Q4', clock: '0:00' })
  })
  it('moves only between recorded endpoints, awards the recorded score at completion, and steps possession', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] })
    render(<DriveTheater supplements={[base, pace]} requirement={null} homeTeam="SEA" awayTeam="NE" m1Label="M1" />)
    fireEvent.click(screen.getByRole('button', { name: /^Play$/ }))
    expect(screen.getByTestId('football-marker')).toHaveAttribute('data-yard', '75.00')
    expect(within(screen.getByRole('group', { name: 'Scoreboard' })).getAllByRole('status').map((s) => s.textContent)).toEqual(['0', '0'])
    act(() => { vi.advanceTimersByTime(1300) })
    const middle = Number(screen.getByTestId('football-marker').getAttribute('data-yard'))
    expect(middle).toBeGreaterThan(1); expect(middle).toBeLessThan(75)
    act(() => { vi.advanceTimersByTime(1350) })
    expect(screen.getByTestId('football-marker')).toHaveAttribute('data-yard', '1.00')
    expect(screen.getByTestId('drive-result')).toHaveTextContent('Touchdown')
    fireEvent.click(screen.getByRole('button', { name: 'Next drive' }))
    expect(screen.getByLabelText('NE possession')).toBeInTheDocument()
    expect(screen.getByTestId('football-marker')).toHaveAttribute('data-yard', '60.00')
    fireEvent.change(screen.getByLabelText('Drive position'), { target: { value: '2' } })
    expect(screen.getByText(/endpoint not recorded/)).toBeInTheDocument()
    expect(screen.getByTestId('football-marker')).toHaveAttribute('data-yard', '75.00')
  })
  it('changes supplied representatives and scenarios and supports timeline, trails, speed and theater controls', () => {
    render(<DriveTheater supplements={[base, pace]} requirement={null} homeTeam="SEA" awayTeam="NE" m1Label="M1" />)
    expect(within(screen.getByLabelText('Representative simulation')).getAllByRole('option')).toHaveLength(6)
    expect(screen.getByLabelText('Representative simulation')).toHaveValue('946')
    fireEvent.change(screen.getByLabelText('Representative simulation'), { target: { value: '3' } })
    expect(screen.getByText('High scoring · Drive 1 / 3')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Drive scenario'), { target: { value: 'pace' } })
    expect(screen.getByText(/recorded drive durations scaled by 0.95/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Timeline view'), { target: { value: 'scoring' } })
    expect(within(screen.getByRole('list', { name: 'Drive timeline' })).getAllByRole('listitem')).toHaveLength(1)
    fireEvent.click(screen.getByLabelText('Keep trails'))
    expect(screen.getByLabelText('Keep trails')).not.toBeChecked()
    fireEvent.change(screen.getByLabelText('Replay speed'), { target: { value: '4' } })
    expect(screen.getByLabelText('Replay speed')).toHaveValue('4')
    fireEvent.click(screen.getByRole('button', { name: 'Theater mode' }))
    expect(screen.getByRole('button', { name: 'Exit theater' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Theater mode' })).toBeInTheDocument()
  })
  it('honors reduced motion using state changes rather than animated travel', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] })
    render(<DriveTheater supplements={[base]} requirement={null} homeTeam="SEA" awayTeam="NE" m1Label="M1" />)
    expect(screen.getByText(/Reduced motion is on/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Play$/ }))
    act(() => { vi.advanceTimersByTime(300) })
    expect(screen.getByTestId('football-marker')).toHaveAttribute('data-yard', '1.00')
  })
  it('compares the actual paired distributions and links the shared representative drive control', () => {
    render(<ScenarioComparison supplements={[{ engine_id: 'independent-drive-v2', baseline: base, scenarios: [pace] }]} requirement={null} homeTeam="SEA" awayTeam="NE" m1Label="M1" />)
    expect(screen.getByRole('region', { name: 'Scenario Comparison' })).toBeInTheDocument()
    expect(within(screen.getByLabelText('Pace minus BASE')).getAllByText('+1.00')).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('Comparison drive'), { target: { value: '1' } })
    expect(screen.getByText('BASE · Typical · Drive 2')).toBeInTheDocument()
    expect(screen.getByText('Pace · Typical · Drive 2')).toBeInTheDocument()
  })
})
