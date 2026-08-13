import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { resolveDemoRoutesEnabled } from './config/demoAccess'
import styles from './styles.css?raw'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
})

describe('Phase 1.5 provenance integrity', () => {
  it('defaults demo routes off in production and allows deliberate overrides', () => {
    expect(resolveDemoRoutesEnabled(undefined, false, 'production')).toBe(false)
    expect(resolveDemoRoutesEnabled(undefined, true, 'development')).toBe(true)
    expect(resolveDemoRoutesEnabled(undefined, false, 'test')).toBe(true)
    expect(resolveDemoRoutesEnabled('false', true, 'development')).toBe(false)
    expect(resolveDemoRoutesEnabled('true', false, 'production')).toBe(true)
  })

  it('keeps the exact demo label persistent and avoids synthetic freshness', async () => {
    window.history.pushState({}, '', '/demo')
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline'))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    expect((await screen.findAllByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/Illustrative date/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/\b\d+\s+(?:minutes?|hours?)\s+ago\b/i)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/dashboard/today'))).toBe(false)
  })

  it('never substitutes the canonical fixture for an unknown demo id', async () => {
    window.history.pushState({}, '', '/demo/games/not-a-demo-game')
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline'))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Illustrative game not found' })).toBeInTheDocument()
    expect(screen.queryByText(/Spencer Strider/i)).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/games/'))).toBe(false)
  })

  it('shows a legitimate live 404 without retrying as demo', async () => {
    window.history.pushState({}, '', '/games/unknown-live-id')
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/games/unknown-live-id')) {
        return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: 'Game not found' }) } as Response)
      }
      return Promise.reject(new TypeError('offline'))
    }))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Game not found' })).toBeInTheDocument()
    expect(screen.getByText('No live game exists with this identifier.')).toBeInTheDocument()
    expect(screen.queryByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).not.toBeInTheDocument()
    expect(screen.queryByText(/Spencer Strider/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument()
  })
})

describe('Phase 1.5 typography safety', () => {
  it('contains no pixel font declaration below 11px', () => {
    const sizes = [
      ...Array.from(styles.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g), (match) => Number(match[1])),
      ...Array.from(styles.matchAll(/font:\s*[^;\n]*?(\d+(?:\.\d+)?)px/g), (match) => Number(match[1])),
    ]
    expect(sizes.length).toBeGreaterThan(0)
    expect(sizes.filter((size) => size < 11)).toEqual([])
  })
})
