/**
 * The Outcome Explorer renders actual joint-score evidence and every query it
 * answers is arithmetic on that one distribution.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OutcomeExplorer, sampledOutcome, simulationLabel, spreadQuery, totalQuery } from './OutcomeExplorer'
import type { NflExplorer } from '../../types/nfl'

vi.mock('echarts-for-react', () => ({ default: ({ onEvents }: { onEvents?: { click: (p: { value: [number, number, number] }) => void } }) => onEvents ? <button type="button" aria-label="Select heatmap cell" onClick={() => onEvents.click({ value: [20, 24, .45] })}>Heatmap cell</button> : <div /> }))

const CELLS = [
  { home: 24, away: 20, probability: 0.45 },
  { home: 17, away: 20, probability: 0.35 },
  { home: 20, away: 20, probability: 0.20 },
]

const EXPLORER: NflExplorer = {
  simulation: { kind: 'MONTE_CARLO', count: 10000, seed: 731001 },
  joint_score_distribution: CELLS,
  outcomes: { home: 0.45, away: 0.35, tie: 0.2 },
  expected: { home: 20.75, away: 20, margin_home: 0.75, total: 40.75 },
  margin_distribution: [{ margin: -3, probability: 0.35 }, { margin: 0, probability: 0.2 }, { margin: 4, probability: 0.45 }],
  total_distribution: [{ total: 37, probability: 0.35 }, { total: 40, probability: 0.2 }, { total: 44, probability: 0.45 }],
  quantiles: {
    margin: { p05: -3, p25: -3, p50: 0, p75: 4, p95: 4 },
    total: { p05: 37, p25: 37, p50: 40, p75: 44, p95: 44 },
    home: { p05: 17, p25: 17, p50: 20, p75: 24, p95: 24 },
    away: { p05: 20, p25: 20, p50: 20, p75: 20, p95: 20 },
  },
  most_likely_scores: CELLS,
  cells: 3,
}

afterEach(cleanup)

describe('OutcomeExplorer arithmetic', () => {
  it('reads spread and total queries off the same distribution, with push mass kept apart', () => {
    expect(spreadQuery(CELLS, -3)).toEqual({ home_cover: 0.45, away_cover: 0.55, push: 0 })
    expect(spreadQuery(CELLS, 3)).toEqual({ home_cover: 0.65, away_cover: 0, push: 0.35 })
    expect(totalQuery(CELLS, 40)).toEqual({ over: 0.45, under: 0.35, push: 0.2 })
  })

  it('samples only outcomes that exist in the distribution, reproducibly', () => {
    const first = sampledOutcome(CELLS, 731001, 0)
    expect(CELLS).toContain(first)
    expect(sampledOutcome(CELLS, 731001, 0)).toBe(first)
  })

  it('never claims draws that were not generated', () => {
    expect(simulationLabel({ kind: 'MONTE_CARLO', count: 10000, seed: 1 })).toContain('10,000 Monte Carlo draws')
    expect(simulationLabel({ kind: 'EXACT_DISTRIBUTION', count: null, seed: null })).toContain('Exact probability distribution')
    expect(simulationLabel({ kind: 'UNKNOWN', count: null, seed: null })).toContain('not stated')
  })
})

describe('OutcomeExplorer rendering', () => {
  it('selects an exact heatmap cell, links only a real representative and preserves key-line arithmetic', () => {
    const replay = vi.fn()
    render(<OutcomeExplorer explorer={EXPLORER} homeTeam="SEA" awayTeam="NE" engineLabel="Drive V2" representativeFinals={[{ homeScore: 24, awayScore: 20, simulationIndex: 17, label: 'Typical' }]} onReplayRepresentative={replay} marketSpread={-2.5} />)
    fireEvent.click(screen.getByRole('button', { name: 'Select heatmap cell' }))
    expect(screen.getByText(/Matches a supplied sampled final: Typical/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Replay this representative' }))
    expect(replay).toHaveBeenCalledWith(17)
    fireEvent.click(screen.getByRole('button', { name: /^SEA \+3$/ }))
    expect(screen.getAllByText('Push')[0].nextSibling).toHaveTextContent('35.0%')
    fireEvent.click(screen.getByRole('button', { name: 'Current market -2.5' }))
    expect(screen.getByLabelText(/SEA line/)).toHaveValue(-2.5)
    fireEvent.change(screen.getByLabelText('Exact final score'), { target: { value: '17:20' } })
    expect(screen.queryByRole('button', { name: 'Replay this representative' })).not.toBeInTheDocument()
    expect(screen.getByText(/No supplied representative game has this exact final/)).toBeInTheDocument()
    expect(screen.getByText('SEA win').nextSibling).toHaveTextContent('45.0%')
  })
  it('shows home, away and tie as separate outcomes and a sampled final outcome on the field', () => {
    render(<OutcomeExplorer explorer={EXPLORER} homeTeam="SEA" awayTeam="NE" engineLabel="M1" />)
    expect(screen.getByText('SEA win').nextSibling).toHaveTextContent('45.0%')
    expect(screen.getByText('NE win').nextSibling).toHaveTextContent('35.0%')
    expect(screen.getByText('Tie').nextSibling).toHaveTextContent('20.0%')
    // The field now leads the section and says the same thing in the caption
    // a reader actually reads, rather than in a footnote under a small chart.
    expect(screen.getByText(/not a sequence of plays/)).toBeInTheDocument()
    expect(screen.getByText(/10,000 Monte Carlo draws/)).toBeInTheDocument()

    // A hypothetical line changes the query answer only.
    const spread = screen.getByLabelText(/SEA line/) as HTMLInputElement
    fireEvent.change(spread, { target: { value: '3' } })
    expect(screen.getAllByText('Push')[0].nextSibling).toHaveTextContent('35.0%')
    expect(screen.getByText('SEA win').nextSibling).toHaveTextContent('45.0%')
  })
})
