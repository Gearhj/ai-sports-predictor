import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixture from '../../test/nflScorecardFixture.json'
import { PRODUCTION_BASELINE, productionLabel } from '../../services/nflScorecard'

let payload: unknown = fixture
vi.mock('../../services/api', () => ({
  apiRequest: vi.fn(async (path: string) => {
    if (path === '/research/nfl-scorecard') return payload
    throw new Error(`unexpected ${path}`)
  }),
}))

import { NflScorecard } from './NflScorecard'

afterEach(() => { cleanup(); payload = fixture; try { window.localStorage.clear() } catch { /* ignore */ } })

describe('NFL scorecard (captured Week 1 data)', () => {
  it('leads with coverage, the small-sample warning and review progress', async () => {
    render(<MemoryRouter><NflScorecard /></MemoryRouter>)
    const section = await screen.findByRole('region', { name: 'NFL scorecard' })
    expect(within(section).getByText('Settled and scored').nextElementSibling).toHaveTextContent('16')
    expect(within(section).getByText('Research shadow · paper research evidence · no production authority')).toBeInTheDocument()
    expect(within(section).getByRole('note')).toHaveTextContent(/Small sample/)
    const bar = within(section).getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuenow', '16')
    expect(bar).toHaveAttribute('aria-valuemax', '50')
    expect(section).toHaveTextContent('First formal review at 50 games · 34 to go')
    expect(section).toHaveTextContent('Calibration is shown from 100 settled games; 16 so far.')
  })

  it('explains log loss and Brier in plain language beside the numbers', async () => {
    render(<MemoryRouter><NflScorecard /></MemoryRouter>)
    const section = await screen.findByRole('region', { name: 'NFL scorecard' })
    expect(section).toHaveTextContent('0.69 is a coin flip')
    expect(section).toHaveTextContent('0 perfect · 2 worst')
    expect(section).toHaveTextContent('A tie counts as a miss for a home or away call.')
    expect(section).toHaveTextContent('10 of 16')
  })

  it('shows predicted versus final for each game, with the technical evidence collapsed', async () => {
    render(<MemoryRouter><NflScorecard /></MemoryRouter>)
    const games = await screen.findByRole('region', { name: 'Scored NFL games' })
    const rows = within(games).getAllByRole('listitem').filter((li) => li.classList.contains('ns__game'))
    expect(rows).toHaveLength(2)
    const den = rows.find((r) => r.textContent?.includes('DEN at KC'))!
    expect(den).toHaveTextContent('final DEN 10 – KC 31')
    expect(den).toHaveTextContent(/predicted DEN \d+\.\d – KC \d+\.\d/)
    const audit = den.querySelector('details.ns__audit') as HTMLDetailsElement
    expect(audit.open).toBe(false)
    expect(audit).toHaveTextContent('FINAL_PREGAME v1')
    expect(audit).toHaveTextContent('NFLVERSE · tier 2')
    expect(audit).toHaveTextContent('separate revision')
    // DEN @ KC carried an experiment position; its settlement is shown as a separate record.
    expect(within(audit).getByText('Settlement record').nextElementSibling).toHaveTextContent(/SETTLED · (LOSS|LOST)/)
    const sea = rows.find((r) => r.textContent?.includes('NE at SEA'))!
    expect(sea.querySelector('details.ns__audit')).toHaveTextContent('none — no experiment position on this game')
    expect(within(audit).getByText('Scoring receipt').nextElementSibling).toHaveTextContent('AISP_NFL_PROSPECTIVE_SCORING_V1')
  })

  it('draws the predicted and final margin for every game, with a table fallback', async () => {
    render(<MemoryRouter><NflScorecard /></MemoryRouter>)
    const chart = await screen.findByRole('img', { name: /Predicted and final margin/ })
    expect(chart.querySelectorAll('.ns__pred')).toHaveLength(2)
    expect(chart.querySelectorAll('.ns__final')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Total' }))
    expect(await screen.findByRole('img', { name: /Predicted and final total/ })).toBeInTheDocument()
  })

  it('keeps lines, the paper view and the benchmark statement behind a disclosure', async () => {
    render(<MemoryRouter><NflScorecard /></MemoryRouter>)
    const more = (await screen.findByText('Spread and total lines, paper view and benchmark')).closest('details')!
    expect(more.open).toBe(false)
    expect(more).toHaveTextContent('Spread (home side)')
    expect(more).toHaveTextContent('a push is never dropped or counted as a loss')
    expect(more).toHaveTextContent('Paper research view.')
    expect(more).toHaveTextContent('No benchmark is registered as authoritative')
  })
})

describe('production label', () => {
  it('is shown only for a verified production baseline', () => {
    const base = { status: 'NOT_RECEIVED', accepted: false, production_label_allowed: false, statement: '', reasons: [] }
    expect(productionLabel(base)).toMatch(/no production authority/)
    expect(productionLabel({ ...base, status: PRODUCTION_BASELINE, accepted: true, production_label_allowed: false })).toMatch(/no production authority/)
    expect(productionLabel({ ...base, status: 'NFL_M1_SHADOW_ONLY', accepted: true, production_label_allowed: true })).toMatch(/no production authority/)
    expect(productionLabel({ ...base, status: PRODUCTION_BASELINE, accepted: true, production_label_allowed: true })).toBe('Production baseline · prospective monitoring')
  })
})
