import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

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
    expect(await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })).toBeInTheDocument()
    expect(screen.getByText('MLB')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/LIVE DATA UNAVAILABLE/i)).toBeInTheDocument())
    expect(screen.queryByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).not.toBeInTheDocument()
    expect(screen.queryByText(/STRONG EDGE/i)).not.toBeInTheDocument()
  })

  it('routes to methodology from the shared navigation', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    await user.click(screen.getByRole('link', { name: 'Methodology' }))
    expect(await screen.findByRole('heading', { name: 'Methodology' })).toBeInTheDocument()
    expect(screen.getByText(/Probability estimates, not picks/i)).toBeInTheDocument()
  })

  it('operates the sport selector from the keyboard and exposes unavailable labels', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /Today’s MLB Forecasts/i })
    const selector = screen.getByRole('button', { name: /Major League Baseball/i })
    expect(selector).toHaveAttribute('aria-expanded', 'false')

    selector.focus()
    await user.keyboard('{Enter}')

    expect(selector).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('NFL').closest('[aria-disabled="true"]')).toBeInTheDocument()
    expect(screen.getByText('Coming soon')).toBeInTheDocument()
  })
})
