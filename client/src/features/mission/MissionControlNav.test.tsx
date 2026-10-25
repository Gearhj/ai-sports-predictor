import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../../App'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

describe('Mission Control in the app shell', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('is in the one left menu, in the order a night flows, with a you-are-here card, and says so when AISP cannot be reached', async () => {
    window.history.pushState({}, '', '/mission-control')
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Mission Control' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'TONIGHT' })
    const link = within(nav).getAllByRole('link', { name: /Mission Control/ })[0]
    expect(link.getAttribute('href')).toBe('/mission-control')
    expect(link).toHaveClass('is-on')
    // Games (edges) -> Mission Control (ready) -> Game Night (outcomes) -> Live View.
    const menu = within(nav).getAllByRole('link').filter((a) => !a.classList.contains('as-here__next')).map((a) => a.getAttribute('href'))
    expect(menu.indexOf('/mission-control')).toBe(menu.indexOf('/games') + 1)
    expect(menu.indexOf('/game-night')).toBe(menu.indexOf('/mission-control') + 1)
    // The page you are on says what it answers and where to go next.
    expect(within(nav).getByText('YOU ARE HERE')).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Next: Game Night →' }).getAttribute('href')).toBe('/game-night')
    // Offline: the page says so instead of rendering an empty success.
    expect(await screen.findByText(/Mission Control could not be read/)).toBeInTheDocument()
    expect(screen.getByText('Cannot reach AISP')).toBeInTheDocument()
  })

  it('keeps the previous page at /mission-control/classic', async () => {
    window.history.pushState({}, '', '/mission-control/classic')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Every game, every gate' })).toBeInTheDocument()
  })
})
