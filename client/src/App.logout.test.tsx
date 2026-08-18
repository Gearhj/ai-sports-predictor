import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthUser } from './types/domain'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

const MEMBER: AuthUser = { id: 'member-1', email: 'member@example.com', display_name: 'Member User', role: 'MEMBER' }

describe('frontend logout calls the backend revocation endpoint', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    window.sessionStorage.clear()
  })

  it('posts to /auth/logout and clears the session', async () => {
    window.sessionStorage.setItem('asp_access_token', 'test-token')
    window.history.pushState({}, '', '/account')

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
      if (url.includes('/auth/me')) return { ok: true, status: 200, json: async () => MEMBER } as Response
      if (url.includes('/auth/logout')) return { ok: true, status: 200, json: async () => ({ status: 'logged_out' }) } as Response
      throw new TypeError('offline')
    })
    vi.stubGlobal('fetch', fetchMock)

    const user = userEvent.setup()
    render(<App />)

    const signOut = await screen.findByRole('button', { name: /Sign out/i })
    await user.click(signOut)

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/auth/logout'))).toBe(true),
    )
    // The signed-in profile view is replaced by the unauthenticated form.
    expect(await screen.findByRole('heading', { name: /Research that shows its work/i })).toBeInTheDocument()
    expect(window.sessionStorage.getItem('asp_access_token')).toBeNull()
  })
})
