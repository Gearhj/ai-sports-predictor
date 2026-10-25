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

  it('is in the one left menu, in the order a night flows, and says so when AISP cannot be reached', async () => {
    window.history.pushState({}, '', '/mission-control')
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Mission Control' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'YOUR NIGHT' })
    const link = within(nav).getByRole('link', { name: /Mission Control/ })
    expect(link.getAttribute('href')).toBe('/mission-control')
    expect(link).toHaveClass('is-on')
    // Games (edges) -> Mission Control (ready) -> Live View -> Game Night (outcomes).
    const hrefs = within(nav).getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(hrefs.indexOf('/mission-control')).toBe(hrefs.indexOf('/games') + 1)
    expect(hrefs.indexOf('/game-night')).toBe(hrefs.indexOf('/mission-control') + 2)
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
