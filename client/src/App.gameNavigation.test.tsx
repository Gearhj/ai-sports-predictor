import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { demoDashboard, getDemoGame } from './data/demo'

vi.setConfig({ testTimeout: 20000 })

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

// Serve the demo dashboard + per-game analysis as if they were live API data.
function stubApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    if (url.includes('/dashboard/today')) {
      return { ok: true, status: 200, json: async () => demoDashboard } as Response
    }
    const gameMatch = url.match(/\/games\/([^/?]+)/)
    if (gameMatch) {
      const id = decodeURIComponent(gameMatch[1])
      try {
        return { ok: true, status: 200, json: async () => getDemoGame(id) } as Response
      } catch {
        return { ok: false, status: 404, json: async () => ({ detail: 'Game not found' }) } as Response
      }
    }
    return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const firstGame = demoDashboard.games[0]

describe('Today → Game Analysis navigation', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.history.pushState({}, '', '/')
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders every current game with a correct game-analysis destination', async () => {
    stubApi()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const links = await screen.findAllByRole('link', { name: /Open full analysis/i })
    expect(links.length).toBe(demoDashboard.games.length)
    // Each destination is the internal game id, not a placeholder.
    expect(links[0]).toHaveAttribute('href', `/games/${firstGame.id}`)
    for (const link of links) {
      expect(link.getAttribute('href')).toMatch(/^\/games\/[^/]+$/)
    }
  })

  it('opens the correct game when the matchup card is clicked', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    // Click the card body (a team name), not the small footer link.
    const card = screen.getByText(firstGame.away_team.name).closest('article.forecast-card') as HTMLElement
    expect(card).toBeTruthy()
    await user.click(within(card).getByText(firstGame.away_team.name))
    // The correct game's analysis page loads.
    expect(await screen.findByRole('heading', { name: new RegExp(`${firstGame.away_team.name}.*game analysis`, 'i') }))
      .toBeInTheDocument()
    expect(window.location.pathname).toBe(`/games/${firstGame.id}`)
  })

  it('navigates via the Open full analysis call-to-action', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const links = await screen.findAllByRole('link', { name: /Open full analysis/i })
    await user.click(links[0])
    expect(await screen.findByRole('heading', { name: new RegExp(`${firstGame.away_team.name}.*game analysis`, 'i') }))
      .toBeInTheDocument()
  })

  it('supports direct URL access and refresh on a game page', async () => {
    stubApi()
    window.history.pushState({}, '', `/games/${firstGame.id}`)
    render(<App />)
    expect(await screen.findByRole('heading', { name: new RegExp(`${firstGame.away_team.name}.*game analysis`, 'i') }))
      .toBeInTheDocument()
  })

  it('shows an honest not-found state for a missing game', async () => {
    stubApi()
    window.history.pushState({}, '', '/games/does-not-exist-000')
    render(<App />)
    await waitFor(() => expect(screen.getByText(/Game not found|Live game analysis unavailable/i)).toBeInTheDocument())
  })
})
