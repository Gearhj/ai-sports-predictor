import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import type { DataMode, DataProvenance, ResourceState } from '../types/domain'
import {
  STALE_TIME,
  currentScope,
  fetchQuery,
  isStale,
  peek,
  release,
  subscribe,
} from '../services/queryCache'

interface ApiResource<T> {
  data: T | null
  state: ResourceState
  /** True only when there is nothing to show yet. A refresh over existing
   *  content reports `refreshing` instead, so a page never blanks. */
  loading: boolean
  error: string | null
  refresh: () => void
  /** A fetch is running while usable content is already on screen. */
  refreshing: boolean
  /** When the displayed data was fetched. `null` until the first success. */
  updatedAt: number | null
  /** Content is being shown past its refresh interval, or after a failure. */
  stale: boolean
  /** When the server generated the displayed data (epoch ms). */
  asOf: number | null
  /** The displayed data was restored from browser storage and not yet refreshed. */
  restored: boolean
}

interface ApiResourceOptions<T> {
  demoData?: T
  /**
   * Cache identity. Must include every input that changes the answer — sport,
   * slate date, game, filters — so one game's numbers can never render under
   * another game's heading. Omit it and the resource is per-mount, as before.
   */
  key?: string
  /** How long the entry stays fresh before a background refresh is due. */
  staleTimeMs?: number
  /** Poll cadence while the tab is visible. Omit for no polling. */
  refreshIntervalMs?: number
}

export function resolveDataMode(payload: DataProvenance): DataMode {
  if (payload.data_mode) return payload.data_mode
  if (typeof payload.is_demo === 'boolean') return payload.is_demo ? 'demo' : 'live'
  // An unprovenanced response must not be promoted to either live or demo results.
  return 'empty'
}

let localKeySequence = 0

// Unconstrained on purpose. `DataProvenance` has only optional fields, so as a
// constraint TypeScript's weak-type check rejected every payload that did not
// happen to share one -- and failed inference then collapsed T to the
// constraint itself, which is how a `TotalsBoard` became `DataProvenance` at
// every call site. The production build (`tsc -b`) failed on this for a week
// while the dev server, which strips types without checking, ran normally.
// Nothing in this hook reads provenance fields from T.
export function useApiResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  options: ApiResourceOptions<T> = {},
): ApiResource<T> {
  const { demoData, key: explicitKey, staleTimeMs = STALE_TIME.slate, refreshIntervalMs } = options

  // Callers that do not supply a key keep their previous per-mount behaviour:
  // a private cache slot whose identity follows the loader, so changing the
  // game or date still refetches. Only keyed callers survive navigation.
  const localIdRef = useRef<string | null>(null)
  if (localIdRef.current === null) localIdRef.current = `__local:${++localKeySequence}`
  const loaderRef = useRef(loader)
  const loaderRevisionRef = useRef(0)
  if (loaderRef.current !== loader) {
    loaderRef.current = loader
    if (!explicitKey) loaderRevisionRef.current += 1
  }
  // The cache namespaces this by the signed-in account itself, so a prefetch
  // and this hook cannot disagree about where the entry lives.
  const key = explicitKey ?? `${localIdRef.current}#${loaderRevisionRef.current}`

  // The subscription follows the access scope too: after sign-in the same raw
  // key lives in a new namespace, and a listener left on the old one would
  // never hear the answer.
  const scope = currentScope()
  const entry = useSyncExternalStore(
    useCallback((listener: () => void) => {
      const unsubscribe = subscribe(key, listener)
      // Leaving this key abandons its request if nothing else is reading it,
      // so a page the reader has left cannot keep a connection from the one
      // they are looking at.
      return () => { unsubscribe(); release(key) }
      // eslint-disable-next-line react-hooks/exhaustive-deps -- scope re-keys the subscription
    }, [key, scope]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scope re-keys the snapshot
    useCallback(() => peek<T>(key), [key, scope]),
    useCallback(() => undefined, []),
  )

  const run = useCallback(
    (force: boolean) => {
      if (demoData) return
      void fetchQuery<T>(key, (signal) => loaderRef.current(signal), { staleTimeMs, force })
    },
    [demoData, key, staleTimeMs],
  )

  // Mount, and every key change, resolves from cache first and only fetches
  // when the entry is missing or past its refresh interval.
  //
  // So does a change of access scope. Signing in finishes after a page has
  // mounted; the scope change clears the cache and wakes every subscriber,
  // but the raw key is unchanged, so without the scope here nothing would
  // ever fetch again and the page would wait on "loading" forever.
  useEffect(() => {
    run(false)
  }, [run, scope])

  // A tab that is not on screen must not keep polling; a tab that comes back
  // refreshes immediately if what it is showing has gone stale.
  useEffect(() => {
    if (demoData) return
    const refreshIfStale = () => {
      if (document.visibilityState !== 'visible') return
      if (isStale(peek(key))) run(false)
    }
    const tick = () => {
      if (document.visibilityState !== 'visible') return
      run(true)
    }
    window.addEventListener('focus', refreshIfStale)
    document.addEventListener('visibilitychange', refreshIfStale)
    const timer = refreshIntervalMs
      ? window.setInterval(tick, refreshIntervalMs)
      : undefined
    return () => {
      window.removeEventListener('focus', refreshIfStale)
      document.removeEventListener('visibilitychange', refreshIfStale)
      if (timer !== undefined) window.clearInterval(timer)
    }
  }, [demoData, key, refreshIntervalMs, run])

  const refresh = useCallback(() => run(true), [run])

  return useMemo<ApiResource<T>>(() => {
    if (demoData) {
      return {
        data: demoData, state: 'demo', loading: false, error: null,
        refresh, refreshing: false, updatedAt: null, stale: false, asOf: null, restored: false,
      }
    }
    const data = entry?.data ?? null
    let state: ResourceState
    if (data !== null) state = resolveDataMode(data)
    else if (entry?.status === 'error') state = 'unavailable'
    else state = 'loading'
    return {
      data,
      state,
      loading: data === null && state === 'loading',
      // A failed refresh over good content surfaces as an error alongside the
      // content it could not replace, rather than replacing it with nothing.
      error: entry?.error ?? null,
      refresh,
      refreshing: Boolean(entry?.isRefreshing),
      updatedAt: entry?.updatedAt ?? null,
      stale: Boolean(entry?.error) || (data !== null && isStale(entry)),
      asOf: entry?.asOf ?? null,
      restored: Boolean(entry?.restored),
    }
  }, [demoData, entry, refresh])
}
