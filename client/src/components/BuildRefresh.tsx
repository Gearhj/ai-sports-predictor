/**
 * Keeps a long-open tab on the deployed build.
 *
 * The app navigates without reloading, so a tab opened before a deployment
 * keeps running the old code (and its old lazy chunks) indefinitely. This reads
 * the build identity the backend serves beside index.html when the tab opens,
 * checks it again every few minutes and whenever the tab becomes visible, and,
 * once it has changed, reloads on the next page change and offers a Reload
 * button meanwhile. A failed chunk load reloads once as well.
 */
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

const CHECK_MS = 5 * 60_000
const defaultFetch: typeof fetch = (...args) => fetch(...args)
const defaultReload = () => window.location.reload()
// Unit tests render the app without a deployed build; they opt in with `enabled`.
const ENABLED_BY_DEFAULT = import.meta.env.MODE !== 'test'

export async function readBuildCommit(fetcher: typeof fetch = defaultFetch): Promise<string | null> {
  try {
    const response = await fetcher('/build-identity.json', { cache: 'no-store' })
    if (!response.ok) return null
    const body = await response.json() as { commit?: unknown }
    return typeof body.commit === 'string' ? body.commit : null
  } catch {
    return null
  }
}

export function BuildRefresh({ fetcher = defaultFetch, reload = defaultReload, enabled = ENABLED_BY_DEFAULT }: {
  fetcher?: typeof fetch; reload?: () => void; enabled?: boolean
}) {
  const { pathname, search } = useLocation()
  const loaded = useRef<string | null>(null)
  const [stale, setStale] = useState(false)
  const first = useRef(true)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const check = async () => {
      const commit = await readBuildCommit(fetcher)
      if (cancelled || !commit) return
      if (loaded.current == null) loaded.current = commit
      else if (commit !== loaded.current) setStale(true)
    }
    void check()
    const timer = window.setInterval(check, CHECK_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') void check() }
    const onChunkError = () => reload()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('vite:preloadError', onChunkError)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('vite:preloadError', onChunkError)
    }
  }, [fetcher, reload, enabled])

  // A page change after a deployment loads the new build instead.
  useEffect(() => {
    if (first.current) { first.current = false; return }
    if (stale) reload()
  }, [pathname, search]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!stale) return null
  return (
    <div className="build-refresh" role="status">
      AISP has been updated.
      <button type="button" onClick={reload}>Reload</button>
    </div>
  )
}
