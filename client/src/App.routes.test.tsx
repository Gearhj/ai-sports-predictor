import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

const routes = [
  { path: '/research/history', heading: /Historical MLB Research Warehouse/i, level: 1 },
  { path: '/', heading: /^AI Sports Predictor$/i, level: 1 },
  { path: '/mlb', heading: /Today’s MLB Forecasts/i, level: 1 },
  { path: '/games/demo-nym-atl', heading: /Live game analysis unavailable/i, level: 2 },
  { path: '/markets', heading: /Market Board/i, level: 1 },
  { path: '/market-pulse', heading: /Crowd & Market Pulse/i, level: 1 },
  { path: '/performance', heading: /Model Performance/i, level: 1 },
  { path: '/experiment', heading: /Can AI Beat the Sportsbooks\?/i, level: 1 },
  { path: '/methodology', heading: /^Methodology$/i, level: 1 },
  { path: '/admin', heading: /Administrator access required/i, level: 1 },
  { path: '/account', heading: /Research that shows its work/i, level: 1 },
] as const

describe('all Phase 1 routes', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it.each(routes)('renders $path inside the shared accessible shell', async ({ path, heading, level }) => {
    window.history.pushState({}, '', path)
    render(<App />)

    expect(await screen.findByRole('heading', { level, name: heading })).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
    expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main-content')
  })

  it('provides labeled authentication fields on the account route', async () => {
    window.history.pushState({}, '', '/account')
    render(<App />)

    await screen.findByRole('heading', { level: 1, name: /Research that shows its work/i })
    expect(screen.getByLabelText('Email address')).toHaveAttribute('type', 'email')
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Sign in securely' })).toBeInTheDocument()
  })
})
