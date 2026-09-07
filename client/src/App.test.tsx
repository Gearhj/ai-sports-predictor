import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { sportRegistryFixture } from './test/sportRegistryFixture'
import type { SportRegistryResponse } from './types/multisport'

function stubRegistry(registry: SportRegistryResponse) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/sports/registry')) {
      return { ok: true, status: 200, json: async () => registry } as Response
    }
    throw new TypeError('offline')
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('AI Sports Predictor application shell', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders an honest unavailable desk without fixture substitution', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Today’s Sports Command Center/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/LIVE DATA UNAVAILABLE/i)).toBeInTheDocument())
    expect(screen.queryByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).not.toBeInTheDocument()
    expect(screen.queryByText(/STRONG EDGE/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open the MLB desk/i })).not.toBeInTheDocument()
  })

  it('routes to methodology from the shared navigation', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s Sports Command Center/i })
    await user.click(screen.getByRole('link', { name: 'Methodology' }))
    expect(await screen.findByRole('heading', { name: 'Methodology' })).toBeInTheDocument()
    expect(screen.getByText(/Probability estimates, not picks/i)).toBeInTheDocument()
  })

  it('operates the canonical sport selector and exposes only governed states', async () => {
    const fetchMock = stubRegistry(sportRegistryFixture)
    const user = userEvent.setup()
    render(<StrictMode><App /></StrictMode>)
    await screen.findByRole('heading', { name: /Today’s Sports Command Center/i })
    const selector = screen.getByRole('button', { name: /All registered sports/i })
    expect(selector).toHaveAttribute('aria-expanded', 'false')

    selector.focus()
    await user.keyboard('{Enter}')

    expect(selector).toHaveAttribute('aria-expanded', 'true')
    const options = screen.getByLabelText('Sport options')
    expect(within(options).getByRole('link', { name: /MLB Active/i })).toHaveAttribute('href', '/mlb')
    expect(within(options).getByRole('link', { name: /NFL Research foundation/i })).toHaveAttribute('href', '/shadow-board?sport=NFL')

    // Planned sports are now visible so the product reads as multi-sport, but
    // the guarantee that matters is unchanged: a sport the backend has not made
    // navigable can never be selected. Each is a disabled span, never a link.
    for (const planned of ['NBA', 'WNBA', 'NHL', 'UFC']) {
      const entry = within(options).getByText(new RegExp(`^${planned}$`))
        .closest('[aria-disabled="true"]')
      expect(entry).toBeTruthy()
      expect(entry).toHaveTextContent(/Planned/)
    }
    expect(within(options).queryByRole('link', { name: /NBA|WNBA|NHL|UFC/i })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/sports/registry'))).toHaveLength(1)
  })

  it('changes navigation entirely from a changed registry fixture', async () => {
    stubRegistry({
      ...sportRegistryFixture,
      sports: sportRegistryFixture.sports.map((sport) => sport.league_code === 'NBA'
        ? { ...sport, availability: 'LIVE', navigable: true, navigation_href: '/basketball-lab' }
        : sport),
      supported_filters: ['ALL', 'NBA'],
    })
    render(<App />)

    const options = screen.getByLabelText('Sport options')
    expect(await within(options).findByRole('link', { name: /NBA Active/i })).toHaveAttribute('href', '/basketball-lab')
    expect(within(options).queryByRole('link', { name: /MLB Active/i })).not.toBeInTheDocument()
    expect(within(options).queryByRole('link', { name: /NFL Research foundation/i })).not.toBeInTheDocument()
  })

  it('fails closed when the authoritative registry cannot be loaded', async () => {
    render(<App />)
    const options = screen.getByLabelText('Sport options')

    expect(await within(options).findByText(/Registry unavailable/i)).toBeInTheDocument()
    expect(within(options).queryByRole('link', { name: /MLB|NFL|NBA|WNBA|UFC/i })).not.toBeInTheDocument()
  })
})
