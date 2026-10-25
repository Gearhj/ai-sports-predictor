/**
 * The NHL research surface: real forecasts by default, test data only behind the
 * toggle (labelled), regulation and final kept apart, Final/OT and Final/SO, goals,
 * goalie assumptions, technical provenance collapsed, and NO market, edge, pick or
 * position anywhere.
 *
 * Fixtures are real responses captured from the isolated preview server (8015).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../services/api'
import { NhlResearchGamePage, NhlResearchPage } from './NhlResearchPage'
import BOARD from './__fixtures__/nhlBoard.json'
import BOARD_TEST from './__fixtures__/nhlBoardTest.json'
import BOARD_PROD from './__fixtures__/nhlBoardProd.json'
import FUTURE from './__fixtures__/nhlGameFuture.json'
import REAL from './__fixtures__/nhlGameReal.json'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, apiRequest: vi.fn() }
})

afterEach(() => { cleanup(); vi.mocked(apiRequest).mockReset() })

function renderAt(path: string, production = false) {
  vi.mocked(apiRequest).mockImplementation(async (url: string) => {
    if (url.startsWith('/nhl/games/2026020011')) return REAL as never
    if (url.startsWith('/nhl/games/')) return FUTURE as never
    if (production) return BOARD_PROD as never
    return (url.includes('test_data=true') ? BOARD_TEST : BOARD) as never
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/nhl/research" element={<NhlResearchPage />} />
        <Route path="/nhl/games/:gameId" element={<NhlResearchGamePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('NHL research board', () => {
  it('defaults to the two real forecasts with test data hidden', async () => {
    renderAt('/nhl/research')
    await screen.findByTestId('nhl-card-2026020011')
    expect(screen.getByTestId('nhl-card-2026020013')).toBeTruthy()
    expect(document.querySelectorAll('.nhl-card')).toHaveLength(2)
    expect(screen.queryByTestId('nhl-fixture-badge')).toBeNull()
    expect(screen.getByTestId('nhl-counts').textContent).toContain('2 real games')
    expect(screen.getByTestId('nhl-test-toggle').textContent).toMatch(/hidden/)
  })

  it('shows labelled test data only after the toggle is turned on', async () => {
    renderAt('/nhl/research')
    await screen.findByTestId('nhl-card-2026020011')
    fireEvent.click(within(screen.getByTestId('nhl-test-toggle')).getByRole('checkbox'))
    await screen.findByTestId('nhl-card-2026010073')
    expect(screen.getByTestId('nhl-fixture-notice')).toBeTruthy()
    expect(screen.getAllByTestId('nhl-fixture-badge').length).toBeGreaterThanOrEqual(6)
    expect(within(screen.getByTestId('nhl-card-2026010071')).getByTestId('nhl-status').textContent).toBe('Final')
    expect(within(screen.getByTestId('nhl-card-2026010072')).getByTestId('nhl-status').textContent).toBe('Final OT')
    const so = screen.getByTestId('nhl-card-2026010073')
    expect(within(so).getByTestId('nhl-status').textContent).toBe('Final SO')
    expect(within(so).getByTestId('nhl-score').textContent).toContain('goals')
    expect(within(so).getByTestId('nhl-regulation-not-modelled')).toBeTruthy()
    const pending = within(screen.getByTestId('nhl-card-2026010081'))
    expect(pending.getByTestId('nhl-no-forecast')).toBeTruthy()
    expect(pending.getByTestId('nhl-forecast-label').textContent).toBe('Forecast pending')
  })

  it('renders no market, edge, pick or wager', async () => {
    renderAt('/nhl/research?nhlTest=1')
    await screen.findByTestId('nhl-card-2026010080')
    expect(screen.getAllByTestId('nhl-no-market')[0].textContent).toContain('No NHL market prices collected')
    expect(document.body.textContent ?? '').not.toMatch(/\bedge:|\bpick\b|recommended|stake \$|moneyline|\+\d{3}\b/i)
  })
})

describe('production (test data flag off)', () => {
  it('offers no test-data toggle and shows only real forecasts, labelled research-only', async () => {
    renderAt('/nhl/research?nhlTest=1', true)
    await screen.findByTestId('nhl-card-2026020011')
    expect(screen.queryByTestId('nhl-test-toggle')).toBeNull()
    expect(screen.queryByTestId('nhl-fixture-badge')).toBeNull()
    for (const label of screen.getAllByTestId('nhl-forecast-label')) {
      expect(label.textContent).toBe('Research forecast — wagering disabled')
    }
  })
})

describe('NHL game page', () => {
  it('shows the real BUF@CBJ forecast with provenance collapsed under Technical details', async () => {
    renderAt('/nhl/games/2026020011')
    const card = within(await screen.findByTestId('nhl-card-2026020011'))
    expect(card.getByTestId('nhl-status').textContent).toBe('Scheduled')
    expect(card.queryByTestId('nhl-fixture-badge')).toBeNull()
    expect(card.getByTestId('nhl-goalie-home').textContent).toMatch(/unknown/i)
    const tech = screen.getByTestId('nhl-technical-details') as HTMLDetailsElement
    expect(tech.open).toBe(false)
    expect(tech.textContent).toContain('nhl-gen1-forecast:sha256:0cf90433')
  })

  it('says a played game without a result is awaiting it, and a post-start forecast is never scored', async () => {
    const played = {
      ...REAL, status: 'played_awaiting_result', status_label: 'Played — result not yet received',
      forecast: { ...(REAL as { forecast: object }).forecast, published_before_start: false },
    }
    vi.mocked(apiRequest).mockImplementation(async () => played as never)
    render(
      <MemoryRouter initialEntries={['/nhl/games/2026020011']}>
        <Routes><Route path="/nhl/games/:gameId" element={<NhlResearchGamePage />} /></Routes>
      </MemoryRouter>,
    )
    const card = within(await screen.findByTestId('nhl-card-2026020011'))
    const status = card.getByTestId('nhl-status')
    expect(status.textContent).toBe('Played — result not yet received')
    expect(status.className).toContain('nhl-status--awaiting')
    expect(card.getByTestId('nhl-forecast-late').textContent).toMatch(/never scored/)
    expect(card.getByTestId('nhl-forecast-age').textContent).toMatch(/published after puck drop/)
  })

  it('keeps revision history inside Technical details', async () => {
    renderAt('/nhl/games/2026010080')
    await screen.findByTestId('nhl-history')
    const card = within(screen.getByTestId('nhl-card-2026010080'))
    expect(card.getAllByTestId('nhl-goalie-home')[0].textContent).toMatch(/confirmed/i)
    expect(card.getAllByTestId('nhl-goalie-away')[0].textContent).toMatch(/projected/i)
    expect(screen.getAllByTestId('nhl-history-item')).toHaveLength(2)
    expect(screen.getAllByTestId('nhl-history-item')[0].textContent).toContain('Current')
  })
})
