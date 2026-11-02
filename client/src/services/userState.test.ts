import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { PUSH_DELAY_MS, STORE_SPECS, _resetUserStateForTests, pull, sig, startUserState } from './userState'

const calls: Array<{ path: string; body: unknown }> = []
let server: Record<string, { store: string; payload: unknown; version: number; updated_at: string }> = {}

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    getStoredToken: () => 'token',
    apiRequest: vi.fn(async (path: string, options: RequestInit = {}) => {
      const body = options.body ? JSON.parse(String(options.body)) : null
      calls.push({ path, body })
      if (path === '/me/state') return { stores: server, known: [], as_of: 'now' }
      const store = path.replace('/me/state/', '')
      if (store === 'conflict') throw new ApiError('STORE_VERSION_CONFLICT: A newer copy exists.', 409)
      const current = server[store]
      if (body.base_version != null && current && current.version !== body.base_version) throw new ApiError('STORE_VERSION_CONFLICT', 409)
      const next = { store, payload: body.payload, version: (current?.version ?? 0) + 1, updated_at: 'now' }
      server[store] = next
      return next
    }),
  }
})

const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }

describe('the account mirror of the browser stores', () => {
  beforeEach(() => { vi.useFakeTimers(); calls.length = 0; server = {}; _resetUserStateForTests() })
  afterEach(() => { _resetUserStateForTests(); vi.useRealTimers() })

  it('imports what the browser already holds when the account is empty', async () => {
    localStorage.setItem('aisp-preview-picks-v1', JSON.stringify([{ id: 'g1:HOME', game_id: 'g1' }]))
    localStorage.setItem('aisp-arena-v1', JSON.stringify({ calls: [], revealed: [], seenResults: [], level: 1 }))
    startUserState('user-1')
    await flush(); await flush()
    const pushed = calls.filter((c) => c.path !== '/me/state').map((c) => c.path)
    expect(pushed).toEqual(['/me/state/picks', '/me/state/arena'])
    expect(server.picks.payload).toEqual([{ id: 'g1:HOME', game_id: 'g1' }])
    expect(calls.find((c) => c.path === '/me/state/picks')?.body).toEqual({ payload: [{ id: 'g1:HOME', game_id: 'g1' }], base_version: null })
  })

  it('takes the account copy over an unseen version and tells the store', async () => {
    localStorage.setItem('aisp-progress-v1', JSON.stringify({ days: ['old'] }))
    server.progress = { store: 'progress', payload: { days: ['2026-11-01', '2026-11-02'] }, version: 4, updated_at: 'now' }
    const heard = vi.fn()
    window.addEventListener('aisp-progress', heard)
    startUserState('user-1')
    await flush(); await flush()
    expect(JSON.parse(localStorage.getItem('aisp-progress-v1')!)).toEqual({ days: ['2026-11-01', '2026-11-02'] })
    expect(heard).toHaveBeenCalled()
    expect(calls.some((c) => c.path === '/me/state/progress')).toBe(false)
    window.removeEventListener('aisp-progress', heard)
  })

  it('pushes a local change a moment after the store fires its event, with the version it was based on', async () => {
    server.picks = { store: 'picks', payload: [], version: 2, updated_at: 'now' }
    startUserState('user-1')
    await flush(); await flush()
    calls.length = 0
    localStorage.setItem('aisp-preview-picks-v1', JSON.stringify([{ id: 'g2:AWAY' }]))
    window.dispatchEvent(new CustomEvent('aisp-preview-picks'))
    expect(calls).toEqual([])
    vi.advanceTimersByTime(PUSH_DELAY_MS)
    await flush(); await flush()
    expect(calls).toEqual([{ path: '/me/state/picks', body: { payload: [{ id: 'g2:AWAY' }], base_version: 2 } }])
    expect(server.picks.version).toBe(3)
  })

  it('a refused push means another device wrote first: the account copy wins', async () => {
    server.arena = { store: 'arena', payload: { level: 1 }, version: 1, updated_at: 'now' }
    startUserState('user-1')
    await flush(); await flush()
    server.arena = { store: 'arena', payload: { level: 7 }, version: 2, updated_at: 'now' }  // the other device
    localStorage.setItem('aisp-arena-v1', JSON.stringify({ level: 2 }))
    window.dispatchEvent(new CustomEvent('aisp-arena'))
    vi.advanceTimersByTime(PUSH_DELAY_MS)
    await flush(); await flush(); await flush()
    expect(JSON.parse(localStorage.getItem('aisp-arena-v1')!)).toEqual({ level: 7 })
  })

  it('signed out, nothing is sent and the browser keeps its copies', async () => {
    localStorage.setItem('aisp-preview-picks-v1', '[]')
    startUserState(null)
    await pull()
    vi.advanceTimersByTime(60_000)
    expect(calls).toEqual([])
    expect(localStorage.getItem('aisp-preview-picks-v1')).toBe('[]')
  })

  it('every mirrored store has a distinct server name and key', () => {
    expect(new Set(STORE_SPECS.map((s) => s.store)).size).toBe(STORE_SPECS.length)
    expect(new Set(STORE_SPECS.map((s) => s.key)).size).toBe(STORE_SPECS.length)
    expect(sig('abc')).not.toBe(sig('abd'))
    expect(sig(null)).toBe('')
  })
})
