import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  STALE_TIME,
  release,
  cacheKey,
  clearCache,
  currentScope,
  fetchQuery,
  invalidate,
  isStale,
  peek,
  prefetchQuery,
  setCacheScope,
  subscribe,
} from './queryCache'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  setCacheScope('anon')
  clearCache()
})

describe('cacheKey', () => {
  it('is independent of the order the scope is written in', () => {
    expect(cacheKey('games', { sport: 'MLB', date: '2026-09-12' }))
      .toBe(cacheKey('games', { date: '2026-09-12', sport: 'MLB' }))
  })

  it('separates two games so one cannot be read under the other', () => {
    expect(cacheKey('game', { gameId: 'a' })).not.toBe(cacheKey('game', { gameId: 'b' }))
  })

  it('drops empty scope values rather than keying on the empty string', () => {
    expect(cacheKey('board', { league: undefined, date: '' })).toBe('board')
  })
})

describe('scoping by access', () => {
  it('empties the cache when the signed-in account changes', async () => {
    await fetchQuery('bankroll', async () => ({ total: 1000 }))
    expect(peek('bankroll')?.data).toEqual({ total: 1000 })

    setCacheScope('user:second')
    // Nothing survives, and the namespace itself has moved on, so even a key
    // written under the previous scope cannot be addressed again.
    expect(peek('bankroll')).toBeUndefined()
    expect(currentScope()).toContain('user:second')
  })

  it('notifies subscribers that have no entry yet', () => {
    const listener = vi.fn()
    subscribe('pending', listener)
    setCacheScope('user:first')
    expect(listener).toHaveBeenCalled()
  })
})

describe('fetchQuery', () => {
  it('serves a fresh entry without issuing a second request', async () => {
    const loader = vi.fn(async () => ({ value: 1 }))
    await fetchQuery('slate', loader, { staleTimeMs: STALE_TIME.slate })
    await fetchQuery('slate', loader, { staleTimeMs: STALE_TIME.slate })
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('collapses concurrent callers onto one in-flight request', async () => {
    const gate = deferred<{ value: number }>()
    const loader = vi.fn(() => gate.promise)
    const a = fetchQuery('slate', loader)
    const b = fetchQuery('slate', loader)
    gate.resolve({ value: 7 })
    await Promise.all([a, b])
    expect(loader).toHaveBeenCalledTimes(1)
    expect(peek('slate')?.data).toEqual({ value: 7 })
  })

  it('keeps the last good snapshot when a refresh fails', async () => {
    await fetchQuery('slate', async () => ({ value: 1 }))
    await fetchQuery('slate', async () => { throw new Error('backend down') }, { force: true })

    const entry = peek<{ value: number }>('slate')
    // The page keeps rendering what it had. This is the defect that made a
    // failed refresh blank a page that was working a moment earlier.
    expect(entry?.data).toEqual({ value: 1 })
    expect(entry?.error).toBe('backend down')
    expect(entry?.status).toBe('success')
  })

  it('reports an error only when there is nothing to show', async () => {
    await fetchQuery('slate', async () => { throw new Error('backend down') })
    expect(peek('slate')?.status).toBe('error')
    expect(peek('slate')?.data).toBeNull()
  })

  it('discards a late response that would overwrite a newer one', async () => {
    const slow = deferred<{ value: string }>()
    const fast = deferred<{ value: string }>()

    const first = fetchQuery('game', () => slow.promise, { force: true })
    // The first request is still open; starting a second bumps the sequence.
    const second = fetchQuery('game', () => fast.promise, { force: true })

    fast.resolve({ value: 'newest' })
    await second
    slow.resolve({ value: 'stale-arrival' })
    await first

    expect(peek('game')?.data).toEqual({ value: 'newest' })
  })

  it('replaces the entry object so a subscriber can detect the change', async () => {
    await fetchQuery('slate', async () => ({ value: 1 }))
    const before = peek('slate')
    await fetchQuery('slate', async () => ({ value: 2 }), { force: true })
    expect(peek('slate')).not.toBe(before)
  })
})

describe('release', () => {
  it('abandons a request once nothing is reading its key', async () => {
    const gate = deferred<{ value: number }>()
    let aborted = false
    void fetchQuery('slate', (signal) => {
      signal.addEventListener('abort', () => { aborted = true })
      return gate.promise
    })
    const unsubscribe = subscribe('slate', () => {})
    unsubscribe()
    release('slate')
    await vi.waitFor(() => expect(aborted).toBe(true))
    gate.resolve({ value: 1 })
  })

  it('leaves a request alone while something is still reading it', async () => {
    const gate = deferred<{ value: number }>()
    let aborted = false
    void fetchQuery('slate', (signal) => {
      signal.addEventListener('abort', () => { aborted = true })
      return gate.promise
    })
    // Two readers; one leaves. The remaining one still needs the answer, which
    // is the StrictMode remount case as much as it is two live panels.
    const first = subscribe('slate', () => {})
    subscribe('slate', () => {})
    first()
    release('slate')
    await new Promise((resolve) => setTimeout(resolve, 400))
    expect(aborted).toBe(false)
    gate.resolve({ value: 1 })
  })
})

describe('staleness', () => {
  it('treats an entry that has never loaded as stale', () => {
    expect(isStale(undefined)).toBe(true)
  })

  it('becomes stale once the refresh interval has elapsed', async () => {
    await fetchQuery('slate', async () => ({ value: 1 }), { staleTimeMs: 1_000 })
    const entry = peek('slate')!
    expect(isStale(entry, entry.updatedAt! + 999)).toBe(false)
    expect(isStale(entry, entry.updatedAt! + 1_000)).toBe(true)
  })

  it('invalidation marks content stale without removing it from the screen', async () => {
    await fetchQuery('markets/board?date=2026-09-12', async () => ({ rows: [1] }))
    invalidate('markets/board')
    const entry = peek('markets/board?date=2026-09-12')
    expect(entry?.data).toEqual({ rows: [1] })
    expect(isStale(entry)).toBe(true)
  })
})

describe('prefetchQuery', () => {
  it('does nothing when the entry is already fresh', async () => {
    await fetchQuery('game', async () => ({ value: 1 }))
    const loader = vi.fn(async () => ({ value: 2 }))
    prefetchQuery('game', loader)
    expect(loader).not.toHaveBeenCalled()
  })

  it('is readable afterwards by a plain fetch of the same key', async () => {
    // The prefetch and the read must resolve to the same cache slot. When
    // scoping lived at one call site and not the other, every warm read was
    // written somewhere the page could never look, and the page refetched.
    const warm = vi.fn(async () => ({ value: 1 }))
    prefetchQuery('game', warm, STALE_TIME.game)
    await vi.waitFor(() => expect(peek('game')?.data).toEqual({ value: 1 }))

    const read = vi.fn(async () => ({ value: 2 }))
    await fetchQuery('game', read, { staleTimeMs: STALE_TIME.game })
    expect(read).not.toHaveBeenCalled()
  })

  it('is scoped to the account, like every other entry', async () => {
    prefetchQuery('bankroll', async () => ({ value: 1 }))
    await vi.waitFor(() => expect(peek('bankroll')?.data).toEqual({ value: 1 }))
    setCacheScope('user:other')
    expect(peek('bankroll')).toBeUndefined()
  })

  it('warms a key that has never been read', async () => {
    const loader = vi.fn(async () => ({ value: 1 }))
    prefetchQuery('game', loader)
    await vi.waitFor(() => expect(peek('game')?.data).toEqual({ value: 1 }))
  })

  it('caps how much background work can run at once', () => {
    const gate = deferred<{ value: number }>()
    for (let index = 0; index < 6; index += 1) {
      prefetchQuery(`game-${index}`, () => gate.promise)
    }
    const started = [0, 1, 2, 3, 4, 5].filter((index) => peek(`game-${index}`) !== undefined)
    // Background warming must not compete with the page the user is on.
    expect(started).toHaveLength(3)
    gate.resolve({ value: 1 })
  })
})

describe('server clock and persistence', () => {
  it('keeps the server generation time as the "as of", never the fetch time', async () => {
    const payload = { rows: [] }
    Object.defineProperty(payload, '__aispAsOf', { value: 1_000, enumerable: false })
    await fetchQuery('command-ledger', async () => payload, { force: true })
    const entry = peek('command-ledger')
    expect(entry?.asOf).toBe(1_000)
    expect(entry?.updatedAt).toBeGreaterThan(1_000)
  })

  it('restores a saved read model after a reload as stale, with its original clock', async () => {
    const payload = { total: 3 }
    Object.defineProperty(payload, '__aispAsOf', { value: 42_000, enumerable: false })
    await fetchQuery('command-markets?day=2026-09-15', async () => payload, { force: true })
    clearCache() // a reload: memory is gone, storage is not
    const restored = peek<{ total: number }>('command-markets?day=2026-09-15')
    expect(restored?.data).toEqual({ total: 3 })
    expect(restored?.asOf).toBe(42_000)
    expect(restored?.restored).toBe(true)
    expect(isStale(restored)).toBe(true)
  })

  it('never persists an account or admin response', async () => {
    await fetchQuery('admin/jobs', async () => ({ secret: true }), { force: true })
    clearCache()
    expect(peek('admin/jobs')).toBeUndefined()
  })
})
