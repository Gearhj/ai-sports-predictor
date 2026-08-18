import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthUser } from './types/domain'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

const ADMIN: AuthUser = { id: 'admin-1', email: 'admin@example.com', display_name: 'Admin User', role: 'ADMIN' }
const MEMBER: AuthUser = { id: 'member-1', email: 'member@example.com', display_name: 'Member User', role: 'MEMBER' }

function stubFetch(me: AuthUser | null) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    if (url.includes('/auth/me')) {
      if (!me) return { ok: false, status: 401, json: async () => ({ detail: 'unauthorized' }) } as Response
      return { ok: true, status: 200, json: async () => me } as Response
    }
    // Every other endpoint (health/sources, public/config, …) is offline for
    // this test; the app must degrade without substituting data.
    throw new TypeError('offline')
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function signInAs(user: AuthUser | null) {
  window.sessionStorage.clear()
  if (user) window.sessionStorage.setItem('asp_access_token', 'test-token')
  return stubFetch(user)
}

describe('operational Health page is admin-only end to end', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    window.sessionStorage.clear()
  })

  beforeEach(() => {
    window.history.pushState({}, '', '/admin')
  })

  it('lets an administrator reach the operational Health page and shows the nav item', async () => {
    signInAs(ADMIN)
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: /Data Health/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Data health' })).toBeInTheDocument()
  })

  it('shows a member a clean access-controlled state and never calls the protected endpoint', async () => {
    const fetchMock = signInAs(MEMBER)
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: /Administrator access required/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Data health' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/health/sources'))).toBe(false)
  })

  it('prompts an anonymous visitor to sign in without exposing the admin nav', async () => {
    const fetchMock = signInAs(null)
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: /Administrator access required/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Data health' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/health/sources'))).toBe(false)
  })
})
