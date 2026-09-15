/**
 * One shared query cache for the whole app.
 *
 * WHY THIS EXISTS
 * ---------------
 * Every page held its data in component state. Navigating away unmounted the
 * component, so coming back meant `null` data, a loading state and a full
 * refetch — which is exactly the reported symptom: pages look broken or empty
 * until you stay on them long enough. And on error the old hook cleared `data`,
 * so a failed refresh replaced good content with an empty page.
 *
 * This is deliberately not a new framework. No package was added; it is ~200
 * lines over `fetch` and React state, using the router already installed.
 *
 * WHAT IT GUARANTEES
 * ------------------
 * - **Scoped identity.** A cache key carries every parameter that changes the
 *   answer — user, sport, date, game, filters. One game's numbers can never
 *   appear under another game's heading, because they are different keys.
 * - **No torn state from a late response.** Every write carries the fetch's
 *   sequence number; a response that started earlier than the newest one in
 *   flight is discarded rather than overwriting newer data.
 * - **Content survives failure.** A failed refresh keeps the last successful
 *   snapshot and marks it stale with the error. It never blanks the page.
 * - **One request per key.** Concurrent callers for the same key share a single
 *   in-flight promise.
 * - **Nothing is acquired.** Every read here is a GET against stored AISP data.
 *   No refresh or prefetch can spend a provider credit, create a decision or
 *   run a model — those live in backend jobs, and this never posts.
 */

export type CacheStatus = 'idle' | 'loading' | 'success' | 'error'

export interface CacheEntry<T = unknown> {
  key: string
  status: CacheStatus
  /** The last value that loaded successfully. Kept across failed refreshes. */
  data: T | null
  error: string | null
  /** When `data` was last successfully fetched. Null until one succeeds. */
  updatedAt: number | null
  /** True while a fetch is running with `data` already present. */
  isRefreshing: boolean
  /** Sequence of the newest fetch started for this key. */
  sequence: number
  /** How long this entry stays fresh before a background refresh is due. */
  staleTimeMs: number
  /**
   * When the SERVER generated `data` (epoch ms), from X-AISP-Generated-At, or
   * when it was fetched if the server sent no clock. This is the "as of" a page
   * shows; it is never advanced by reading the value back out of a cache.
   */
  asOf: number | null
  /** True when `data` was restored from browser storage and not yet refreshed. */
  restored: boolean
}

type Listener = () => void

/**
 * Refresh cadences, chosen against how fast each kind of data can actually
 * change on the server. Polling faster than ingestion only adds load.
 */
export const STALE_TIME = {
  /** Current slate: collectors and the freeze job run on a 5-minute cadence. */
  slate: 60_000,
  /** Operational status and bankroll ledger: changes on settlement. */
  operational: 120_000,
  /** One game's detail: forecasts arrive on the publisher's cadence. */
  game: 90_000,
  /** Finalized history and research exports: effectively immutable. */
  history: 15 * 60_000,
  /** Static reference data: registry, policy, methodology. */
  reference: 60 * 60_000,
} as const

const entries = new Map<string, CacheEntry>()
const listeners = new Map<string, Set<Listener>>()
const inFlight = new Map<string, Promise<unknown>>()
const controllers = new Map<string, AbortController>()
let sequenceCounter = 0

/**
 * Access scope.
 *
 * Every key is namespaced by the current scope, and changing the scope clears
 * the cache outright. One account's stored data cannot be shown to the next,
 * and a key written before sign-in cannot be read after it.
 *
 * Scoping happens HERE rather than at the call sites, so a prefetch and the
 * hook that later reads it cannot disagree about the namespace: they pass the
 * same plain key and this module resolves it the same way for both.
 */
let scopeId = 'anon'
let scopeGeneration = 0

export function currentScope(): string {
  return `${scopeGeneration}:${scopeId}`
}

export function setCacheScope(next: string): void {
  if (next === scopeId) return
  scopeId = next
  scopeGeneration += 1
  clearCache()
}

/** Resolve a caller's key into the namespace of the current scope. */
function scoped(key: string): string {
  return `${currentScope()}|${key}`
}

/** Build a stable key from a name and its scope. Order-independent. */
export function cacheKey(name: string, scope: Record<string, unknown> = {}): string {
  const parts = Object.keys(scope)
    .sort()
    .filter((k) => scope[k] !== undefined && scope[k] !== null && scope[k] !== '')
    .map((k) => `${k}=${String(scope[k])}`)
  return parts.length ? `${name}?${parts.join('&')}` : name
}

function emit(key: string): void {
  listeners.get(key)?.forEach((listener) => listener())
}

/**
 * Entries are replaced, never mutated. `useSyncExternalStore` compares
 * snapshots by identity, so an in-place edit would update the cache without
 * ever re-rendering the page that is reading it.
 */
function write<T>(key: string, patch: Partial<CacheEntry<T>>): CacheEntry<T> {
  // `key` is already scoped: only the public entry points resolve scope.
  const previous = entries.get(key) as CacheEntry<T> | undefined
  const base: CacheEntry<T> = previous ?? {
    key, status: 'idle', data: null, error: null, updatedAt: null,
    isRefreshing: false, sequence: 0, staleTimeMs: STALE_TIME.slate, asOf: null, restored: false,
  }
  const next: CacheEntry<T> = { ...base, ...patch }
  entries.set(key, next as CacheEntry)
  return next
}

function ensure<T>(key: string, staleTimeMs: number): CacheEntry<T> {
  const existing = (entries.get(key) ?? restore(key)) as CacheEntry<T> | undefined
  if (existing && existing.staleTimeMs === staleTimeMs) return existing
  return write<T>(key, { staleTimeMs })
}

export function peek<T>(key: string): CacheEntry<T> | undefined {
  const resolved = scoped(key)
  return (entries.get(resolved) ?? restore(resolved)) as CacheEntry<T> | undefined
}

/* -- persistence -----------------------------------------------------------
 * The last successful answer for the primary read models survives a reload,
 * so a cold navigation shows the last known view at once (with its real "as
 * of") while a background refresh runs. Only public read models are stored --
 * never an admin, account or auth response -- and only per signed-in scope. */

const STORAGE_PREFIX = 'aisp-query:v1:'
const STORAGE_INDEX = 'aisp-query:v1:index'
const PERSISTABLE = [
  'home', 'games', 'markets', 'decisions', 'today', 'bankroll', 'sport-readiness', 'sport-registry',
  'command-', 'show-board', 'slate-status', 'experiment', 'scoreboard', 'nfl-', 'nba-', 'cfb-', 'research',
]
const MAX_PERSISTED = 40
const MAX_PERSISTED_CHARS = 1_500_000

function persistable(scopedKey: string): boolean {
  const name = scopedKey.split('|').slice(1).join('|')
  return !/admin|auth|account|token|user/i.test(name) && PERSISTABLE.some((p) => name.startsWith(p))
}

function storageKey(scopedKey: string): string {
  // The generation number is session-local; storage is keyed by the scope id.
  return `${STORAGE_PREFIX}${scopeId}|${scopedKey.split('|').slice(1).join('|')}`
}

function storage(): Storage | null {
  try { return typeof window !== 'undefined' ? window.localStorage : null } catch { return null }
}

function restore(scopedKey: string): CacheEntry | undefined {
  if (!persistable(scopedKey)) return undefined
  const store = storage()
  if (!store) return undefined
  try {
    const raw = store.getItem(storageKey(scopedKey))
    if (!raw) return undefined
    const saved = JSON.parse(raw) as { data: unknown; updatedAt: number; asOf: number | null; staleTimeMs: number }
    const entry: CacheEntry = {
      key: scopedKey, status: 'success', data: saved.data, error: null,
      // Restored content is never fresh: a refresh is always due.
      updatedAt: 0, isRefreshing: false, sequence: 0,
      staleTimeMs: saved.staleTimeMs ?? STALE_TIME.slate, asOf: saved.asOf ?? saved.updatedAt ?? null, restored: true,
    }
    entries.set(scopedKey, entry)
    return entry
  } catch {
    return undefined
  }
}

function persist(scopedKey: string, entry: CacheEntry): void {
  if (!persistable(scopedKey) || entry.data == null) return
  const store = storage()
  if (!store) return
  try {
    const body = JSON.stringify({ data: entry.data, updatedAt: entry.updatedAt, asOf: entry.asOf, staleTimeMs: entry.staleTimeMs })
    if (body.length > MAX_PERSISTED_CHARS) return
    const name = storageKey(scopedKey)
    const index = (JSON.parse(store.getItem(STORAGE_INDEX) ?? '[]') as string[]).filter((k) => k !== name)
    index.push(name)
    while (index.length > MAX_PERSISTED) store.removeItem(index.shift() as string)
    store.setItem(name, body)
    store.setItem(STORAGE_INDEX, JSON.stringify(index))
  } catch {
    // Quota or privacy mode: persistence is a convenience, never a requirement.
  }
}

function serverClock(value: unknown): number | null {
  if (!value || typeof value !== 'object') return null
  const read = (v: unknown) => {
    const clock = v && typeof v === 'object' ? (v as Record<string, unknown>).__aispAsOf : null
    return typeof clock === 'number' && Number.isFinite(clock) ? clock : null
  }
  const own = read(value)
  if (own != null) return own
  let oldest: number | null = null
  for (const part of Object.values(value as Record<string, unknown>)) {
    const clock = read(part)
    if (clock != null) oldest = oldest == null ? clock : Math.min(oldest, clock)
  }
  return oldest
}

export function isStale(entry: CacheEntry | undefined, now = Date.now()): boolean {
  if (!entry || entry.updatedAt === null || entry.restored) return true
  return now - entry.updatedAt >= entry.staleTimeMs
}

export function subscribe(rawKey: string, listener: Listener): () => void {
  const key = scoped(rawKey)
  const set = listeners.get(key) ?? new Set<Listener>()
  set.add(listener)
  listeners.set(key, set)
  return () => {
    set.delete(listener)
    if (set.size === 0) listeners.delete(key)
  }
}

export interface FetchOptions {
  staleTimeMs?: number
  /** Fetch even when the entry is still fresh. */
  force?: boolean
  /** Background work: never flips a populated entry back to `loading`. */
  background?: boolean
}

/**
 * Fetch a key through the cache.
 *
 * Concurrent callers share one request. A response whose sequence is behind the
 * newest started fetch is dropped — the guard against out-of-order writes.
 */
export function fetchQuery<T>(
  rawKey: string,
  loader: (signal: AbortSignal) => Promise<T>,
  options: FetchOptions = {},
): Promise<T | null> {
  const key = scoped(rawKey)
  const staleTimeMs = options.staleTimeMs ?? STALE_TIME.slate
  const entry = ensure<T>(key, staleTimeMs)

  if (!options.force && !isStale(entry) && entry.status === 'success') {
    return Promise.resolve(entry.data)
  }
  const existing = inFlight.get(key)
  if (existing && !options.force) return existing as Promise<T | null>
  if (existing) {
    // An explicit refresh must not be swallowed by a request that is already
    // struggling. The older one is abandoned, and the sequence check below
    // discards its answer if it arrives after the replacement's.
    controllers.get(key)?.abort()
    inFlight.delete(key)
  }

  const sequence = ++sequenceCounter
  const controller = new AbortController()
  controllers.set(key, controller)
  write<T>(key, {
    sequence,
    isRefreshing: entry.data !== null,
    status: entry.data === null ? 'loading' : entry.status,
  })
  emit(key)

  const promise = loader(controller.signal)
    .then((result) => {
      const current = entries.get(key)
      // A slower earlier request must not overwrite a newer answer.
      if (!current || current.sequence !== sequence) return result
      const fetchedAt = Date.now()
      const next = write<T>(key, {
        data: result, status: 'success', error: null,
        updatedAt: fetchedAt, isRefreshing: false,
        asOf: serverClock(result) ?? fetchedAt, restored: false,
      })
      emit(key)
      persist(key, next as CacheEntry)
      return result
    })
    .catch((reason: unknown) => {
      const current = entries.get(key)
      if (!current || current.sequence !== sequence) return null
      if (controller.signal.aborted) {
        write<T>(key, { isRefreshing: false })
        emit(key)
        return null
      }
      // The last good snapshot survives. A failed refresh must never blank a
      // page that was showing usable content a moment ago.
      write<T>(key, {
        error: reason instanceof Error ? reason.message : 'Request failed',
        status: current.data !== null ? 'success' : 'error',
        isRefreshing: false,
      })
      emit(key)
      return null
    })
    .finally(() => {
      if (inFlight.get(key) === promise) inFlight.delete(key)
      if (controllers.get(key) === controller) controllers.delete(key)
    })

  inFlight.set(key, promise)
  return promise as Promise<T | null>
}

/** Warm a key at low priority. Never disturbs an entry that is already fresh. */
export function prefetchQuery<T>(
  key: string,
  loader: (signal: AbortSignal) => Promise<T>,
  staleTimeMs: number = STALE_TIME.slate,
): void {
  const entry = peek(key)
  if (entry && !isStale(entry)) return
  if (inFlight.has(scoped(key))) return
  if (activePrefetches >= MAX_CONCURRENT_PREFETCH) return
  activePrefetches += 1
  void fetchQuery(key, loader, { staleTimeMs, background: true }).finally(() => {
    activePrefetches -= 1
  })
}

/** Background work stays small so it cannot compete with the active page. */
const MAX_CONCURRENT_PREFETCH = 3
let activePrefetches = 0

/**
 * Nobody is reading this key any more.
 *
 * A request whose only reader has navigated away is obsolete: its answer would
 * land in a cache slot no component is watching, while still occupying a
 * connection the page the reader is actually looking at needs. It is abandoned.
 *
 * The grace window matters. React StrictMode mounts, unmounts and remounts in
 * development, so an immediate abort would cancel a request that is about to be
 * wanted again a tick later. A prefetch is never affected: it has no listeners
 * to release in the first place.
 */
const RELEASE_GRACE_MS = 300

export function release(rawKey: string): void {
  const key = scoped(rawKey)
  window.setTimeout(() => {
    if ((listeners.get(key)?.size ?? 0) > 0) return
    controllers.get(key)?.abort()
  }, RELEASE_GRACE_MS)
}

/** Drop every key whose name matches, so the next read refetches. */
export function invalidate(rawPrefix: string): void {
  const prefix = scoped(rawPrefix)
  for (const key of [...entries.keys()]) {
    if (key === prefix || key.startsWith(`${prefix}?`) || key.startsWith(`${prefix}/`)) {
      if (entries.has(key)) {
        write(key, { updatedAt: null }) // stale now; content stays visible
        emit(key)
      }
    }
  }
}

/**
 * Remove everything. Used on logout and account change so one account's data
 * can never be shown to the next.
 */
export function clearCache(): void {
  for (const controller of controllers.values()) controller.abort()
  controllers.clear()
  inFlight.clear()
  entries.clear()
  // Every subscriber is notified, not only those that had an entry, so a hook
  // still waiting on its first response also re-reads under the new scope.
  for (const set of [...listeners.values()]) set.forEach((listener) => listener())
}

export function cacheSnapshot(): { keys: number; inFlight: number } {
  return { keys: entries.size, inFlight: inFlight.size }
}
