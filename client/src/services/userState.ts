/**
 * YOUR ACCOUNT, EVERY DEVICE (scale plan 2026-11-01, Phase 1 item 1).
 *
 * My Picks, the Arena, XP, the model builder, favourites: all born as localStorage blobs, each firing a window
 * event when it changes. This module mirrors those blobs to the signed-in account (/me/state) without touching the
 * stores themselves:
 *   - on sign-in it PULLS: a store the account holds at a version this browser has not seen replaces the local copy
 *     (and the store's event is fired so every hook re-reads); a store the account does not hold yet is IMPORTED from
 *     this browser, so a tester keeps everything they already had;
 *   - after that every local change (the store's own event, or a 20 s scan for stores without one) is PUSHED a
 *     moment later, conditional on the version this browser last saw; a refused push means another device wrote
 *     first, and the account's copy is taken;
 *   - signed out, nothing happens: the browser is the only copy, as before.
 * Latest write to the account wins; two devices editing the same store between pulls is the one way to lose a change.
 */
import { useSyncExternalStore } from 'react'
import { ApiError, apiRequest, getStoredToken } from './api'
import { myCard, replayCard } from './myCard'

export interface StoreSpec {
  /** The server's name for the store (services/user_state.py STORES). */
  store: string
  /** The localStorage key. The value is always JSON text. */
  key: string
  /** Window events the store fires on its own writes; empty for stores that have none (the scan catches those). */
  events: string[]
  /** How to tell an in-memory store that its localStorage copy changed under it. */
  notify?: () => void
}

export const STORE_SPECS: readonly StoreSpec[] = [
  { store: 'picks', key: 'aisp-preview-picks-v1', events: ['aisp-preview-picks'] },
  { store: 'play-bankroll', key: 'aisp-play-bankroll-v1', events: ['aisp-preview-picks'] },
  { store: 'my-card', key: 'aisp.my-card.v1', events: [], notify: () => myCard.reload() },
  { store: 'my-card-replay', key: 'aisp.my-card.replay.v1', events: [], notify: () => replayCard.reload() },
  { store: 'arena', key: 'aisp-arena-v1', events: ['aisp-arena'] },
  { store: 'progress', key: 'aisp-progress-v1', events: ['aisp-progress'] },
  { store: 'youcall', key: 'aisp-youcall-v1', events: ['aisp-youcall'] },
  { store: 'my-model', key: 'aisp-my-model-v1', events: ['aisp-my-model-v1'] },
  { store: 'my-reads', key: 'aisp-my-reads-v1', events: ['aisp-my-reads-v1'] },
  { store: 'my-model-log', key: 'aisp-my-model-log-v1', events: ['aisp-my-model-log-v1'] },
  { store: 'favorites', key: 'aisp.live.favorites', events: [] },
  { store: 'alerts-settings', key: 'aisp.alerts.settings.v1', events: [] },
]

interface ServerStore { store: string; payload: unknown; version: number; updated_at: string }
interface StateResponse { stores: Record<string, ServerStore>; known: string[]; as_of: string }
interface Meta { user: string; stores: Record<string, { version: number; sig: string }> }

export interface SyncStatus { state: 'off' | 'syncing' | 'synced' | 'error'; at: number | null; detail: string | null }

const META_KEY = 'aisp-sync-meta-v1'
export const PUSH_DELAY_MS = 1500
export const SCAN_EVERY_MS = 20_000
export const PULL_EVERY_MS = 5 * 60_000

const ls = {
  get(key: string): string | null { try { return window.localStorage.getItem(key) } catch { return null } },
  set(key: string, value: string): void { try { window.localStorage.setItem(key, value) } catch { /* private window */ } },
}

/** A cheap fingerprint of a store's text: enough to know "changed since the last push". */
export function sig(raw: string | null): string {
  if (raw === null) return ''
  let h = 5381
  for (let i = 0; i < raw.length; i++) h = ((h << 5) + h + raw.charCodeAt(i)) | 0
  return `${raw.length}:${(h >>> 0).toString(36)}`
}

let meta: Meta | null = null
let applying = false
let userId: string | null = null
let timers: { push: Record<string, number>; scan: number | null; pull: number | null } = { push: {}, scan: null, pull: null }
let teardown: Array<() => void> = []
let status: SyncStatus = { state: 'off', at: null, detail: null }
const watchers = new Set<() => void>()
let inflight: Promise<void> | null = null

function setStatus(next: Partial<SyncStatus>): void {
  status = { ...status, ...next }
  watchers.forEach((w) => w())
}

function loadMeta(user: string): Meta {
  try {
    const raw = ls.get(META_KEY)
    const parsed = raw ? (JSON.parse(raw) as Meta) : null
    if (parsed && parsed.user === user && parsed.stores) return parsed
  } catch { /* fall through */ }
  return { user, stores: {} }
}
function saveMeta(): void { if (meta) ls.set(META_KEY, JSON.stringify(meta)) }

function apply(spec: StoreSpec, s: ServerStore): void {
  const text = JSON.stringify(s.payload)
  applying = true
  try {
    ls.set(spec.key, text)
    if (meta) { meta.stores[spec.store] = { version: s.version, sig: sig(text) }; saveMeta() }
    for (const ev of spec.events) window.dispatchEvent(new CustomEvent(ev))
    spec.notify?.()
  } finally {
    applying = false
  }
}

async function push(spec: StoreSpec): Promise<void> {
  if (!userId || !meta) return
  const raw = ls.get(spec.key)
  if (raw === null) return
  let payload: unknown
  try { payload = JSON.parse(raw) } catch { return }  // not ours to mirror
  const base = meta.stores[spec.store]?.version
  setStatus({ state: 'syncing' })
  try {
    const res = await apiRequest<ServerStore>(`/me/state/${spec.store}`, { method: 'POST', body: JSON.stringify({ payload, base_version: base ?? null }) })
    if (!meta) return
    meta.stores[spec.store] = { version: res.version, sig: sig(raw) }
    saveMeta()
    setStatus({ state: 'synced', at: Date.now(), detail: null })
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) { await pull(); return }  // another device wrote first: take its copy
    if (error instanceof ApiError && error.status === 401) { stop(); return }
    setStatus({ state: 'error', detail: error instanceof Error ? error.message : 'sync failed' })
  }
}

/** Read the account's copies and reconcile every store (exported for tests and for "sync now"). */
export async function pull(): Promise<void> {
  if (!userId || !meta) return
  if (inflight) return inflight
  inflight = (async () => {
    setStatus({ state: 'syncing' })
    let res: StateResponse
    try {
      res = await apiRequest<StateResponse>('/me/state')
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) { stop(); return }
      setStatus({ state: 'error', detail: error instanceof Error ? error.message : 'sync failed' })
      return
    }
    if (!meta) return
    const toPush: StoreSpec[] = []
    for (const spec of STORE_SPECS) {
      const server = res.stores[spec.store]
      const raw = ls.get(spec.key)
      const known = meta.stores[spec.store]
      if (server) {
        if (!known || known.version !== server.version) apply(spec, server)
        else if (sig(raw) !== known.sig) toPush.push(spec)
      } else if (raw !== null) {
        toPush.push(spec)  // the account holds nothing yet: this browser's copy becomes the account's
      }
    }
    for (const spec of toPush) await push(spec)
    setStatus({ state: status.state === 'error' ? 'error' : 'synced', at: Date.now() })
  })().finally(() => { inflight = null })
  return inflight
}

function schedule(spec: StoreSpec): void {
  if (!userId) return
  if (timers.push[spec.store]) window.clearTimeout(timers.push[spec.store])
  timers.push[spec.store] = window.setTimeout(() => { delete timers.push[spec.store]; void push(spec) }, PUSH_DELAY_MS)
}

function scan(): void {
  if (!meta) return
  for (const spec of STORE_SPECS) {
    const raw = ls.get(spec.key)
    if (raw !== null && sig(raw) !== meta.stores[spec.store]?.sig) schedule(spec)
  }
}

function stop(): void {
  userId = null
  meta = null
  for (const t of Object.values(timers.push)) window.clearTimeout(t)
  if (timers.scan) window.clearInterval(timers.scan)
  if (timers.pull) window.clearInterval(timers.pull)
  timers = { push: {}, scan: null, pull: null }
  teardown.forEach((fn) => fn())
  teardown = []
  setStatus({ state: 'off', detail: null })
}

/**
 * Called by AuthProvider whenever the signed-in user changes. `null` stops mirroring (sign-out); the browser keeps
 * its copies. A user id starts it: listeners on every store's event, a pull now, a scan every 20 s, a pull every 5 min
 * and when the tab comes back into view.
 */
export function startUserState(user: string | null): void {
  if (typeof window === 'undefined') return
  if (user === userId) return
  stop()
  if (!user || !getStoredToken()) return
  userId = user
  meta = loadMeta(user)
  for (const spec of STORE_SPECS) {
    const on = () => { if (!applying) schedule(spec) }
    for (const ev of spec.events) { window.addEventListener(ev, on); teardown.push(() => window.removeEventListener(ev, on)) }
  }
  teardown.push(myCard.subscribe(() => { if (!applying) schedule(STORE_SPECS[2]) }))
  teardown.push(replayCard.subscribe(() => { if (!applying) schedule(STORE_SPECS[3]) }))
  const onVisible = () => { if (document.visibilityState === 'visible') void pull() }
  document.addEventListener('visibilitychange', onVisible)
  teardown.push(() => document.removeEventListener('visibilitychange', onVisible))
  timers.scan = window.setInterval(scan, SCAN_EVERY_MS)
  timers.pull = window.setInterval(() => void pull(), PULL_EVERY_MS)
  void pull()
}

/** For the Account page: whether this browser is mirroring to the account, and when it last did. */
export function useUserStateStatus(): SyncStatus {
  return useSyncExternalStore(
    (w) => { watchers.add(w); return () => { watchers.delete(w) } },
    () => status,
    () => status,
  )
}

/** Test hook. */
export function _resetUserStateForTests(): void { stop(); status = { state: 'off', at: null, detail: null } }
