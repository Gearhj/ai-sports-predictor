/**
 * Model Performance is one page with four views held in the URL: Overview (default),
 * Wager Results, Monday Morning QB and Detailed model analysis. Each tab pushes a URL,
 * direct links open their view, and the legacy nested Wager Results URL redirects.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../components/command/ModelPerformanceOverview', () => ({
  ModelPerformanceOverview: ({ wagersHref }: { wagersHref: string }) => <div data-testid="overview">overview {wagersHref}</div>,
}))
vi.mock('../components/command/WagerResults', () => ({
  WagerResults: ({ pageSport }: { pageSport: string }) => <div data-testid="wager-results">wagers {pageSport}</div>,
}))
vi.mock('../components/command/MmqbReport', () => ({
  MmqbReport: ({ league, season, week }: { league: string; season: number | null; week: number | null }) =>
    <div data-testid="mmqb">mmqb {league} {season} {week}</div>,
}))
vi.mock('../components/command/CommandPerformance', () => ({
  CommandPerformance: ({ initialSport }: { initialSport?: string }) => <div data-testid="detailed">detailed {initialSport}</div>,
}))
vi.mock('./UnifiedResearchPage', () => ({ UnifiedResearchPage: () => <div>diagnostics</div> }))
vi.mock('../hooks/useSportSelection', () => ({ useSportSelection: () => ({ sport: 'ALL' }) }))

import { PerformanceRoute, legacyRedirect, panelOf, wagersHref } from './PerformanceRoute'

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{`${loc.pathname}${loc.search}`}</output>
}

function mount(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/performance" element={<><PerformanceRoute /><Where /></>} /></Routes>
    </MemoryRouter>,
  )
}

afterEach(() => cleanup())

describe('Model Performance views', () => {
  it('opens on the Overview, whose Wager Results links point at the top-level view', () => {
    mount('/performance')
    expect(screen.getByTestId('overview').textContent).toContain('/performance?panel=wagers&sport=NFL')
    expect(screen.getByRole('tab', { name: 'Overview' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(
      ['Overview', 'Wager Results', 'Monday Morning QB', 'Detailed model analysis'])
  })

  it('each tab updates the URL and shows only its own view', () => {
    mount('/performance')
    fireEvent.click(screen.getByRole('tab', { name: 'Wager Results' }))
    expect(screen.getByTestId('where').textContent).toBe('/performance?panel=wagers&sport=NFL')
    expect(screen.getByTestId('wager-results').textContent).toBe('wagers NFL')
    expect(screen.queryByTestId('overview')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Monday Morning QB' }))
    expect(screen.getByTestId('where').textContent).toBe('/performance?panel=mmqb')
    expect(screen.getByTestId('mmqb')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Detailed model analysis' }))
    expect(screen.getByTestId('where').textContent).toBe('/performance?panel=detailed')
    expect(screen.getByTestId('detailed')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))
    expect(screen.getByTestId('where').textContent).toBe('/performance')
  })

  it('direct links open their view with their parameters', () => {
    mount('/performance?panel=wagers&sport=MLB')
    expect(screen.getByTestId('wager-results').textContent).toBe('wagers MLB')
    cleanup()
    mount('/performance?panel=mmqb&league=NFL&season=2026&week=3')
    expect(screen.getByTestId('mmqb').textContent).toBe('mmqb NFL 2026 3')
    cleanup()
    mount('/performance?panel=detailed&sport=NFL')
    expect(screen.getByTestId('detailed').textContent).toBe('detailed NFL')
  })

  it('a Detailed link for research-only NHL opens NHL, never MLB', () => {
    mount('/performance?panel=detailed&sport=NHL')
    expect(screen.getByTestId('detailed').textContent).toBe('detailed NHL')
    cleanup()
    mount('/performance?panel=detailed&sport=nhl')
    expect(screen.getByTestId('detailed').textContent).toBe('detailed NHL')
    cleanup()
    mount('/performance?panel=detailed&sport=UFC')
    expect(screen.getByTestId('detailed').textContent).toBe('detailed ')
  })

  it('the Wager Results sport buttons move the URL', () => {
    mount('/performance?panel=wagers&sport=NFL')
    fireEvent.click(screen.getByRole('button', { name: 'College Football' }))
    expect(screen.getByTestId('where').textContent).toBe('/performance?panel=wagers&sport=CFB')
    expect(screen.getByTestId('wager-results').textContent).toBe('wagers CFB')
  })

  it('redirects the legacy nested Wager Results URL to the top-level view', () => {
    mount('/performance?panel=detailed&view=wagers&sport=NFL')
    expect(screen.getByTestId('where').textContent).toBe('/performance?panel=wagers&sport=NFL')
    expect(screen.getByTestId('wager-results').textContent).toBe('wagers NFL')
    expect(legacyRedirect('?panel=detailed&view=calibration')).toBeNull()
    expect(legacyRedirect('?panel=detailed&view=WAGERS&sport=CFB')).toBe('/performance?panel=wagers&sport=CFB')
    expect(panelOf('nonsense')).toBe('overview')
    expect(wagersHref('MLB')).toBe('/performance?panel=wagers&sport=MLB')
  })
})
