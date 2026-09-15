/**
 * A region's honest freshness line: when the SERVER generated what is shown,
 * whether a background refresh is running, and whether the last one failed.
 * Cached or restored content keeps its original "as of" -- it is never
 * restamped as current.
 */
import { useEffect, useState } from 'react'

export interface RegionResource {
  asOf: number | null
  refreshing: boolean
  error: string | null
  restored?: boolean
  loading?: boolean
  data?: unknown
  refresh?: () => void
}

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 45) return 'just now'
  if (s < 90 * 60) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)} h ago`
}

export function AsOf({ resource, label }: { resource: RegionResource; label?: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 20_000)
    return () => window.clearInterval(timer)
  }, [])
  if (resource.data == null) {
    return resource.error
      ? <span className="cc-asof cc-asof--error" role="status">Unable to load{label ? ` ${label}` : ''} · {resource.error}
        {resource.refresh && <button type="button" onClick={resource.refresh}>Retry</button>}</span>
      : <span className="cc-asof" role="status">Loading{label ? ` ${label}` : ''}…</span>
  }
  const at = resource.asOf ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(resource.asOf) : null
  return (
    <span className={`cc-asof${resource.error ? ' cc-asof--error' : ''}`} role="status" aria-live="polite">
      {at ? `As of ${at} ET (${ago(now - (resource.asOf as number))})` : 'As of an unrecorded time'}
      {resource.restored && !resource.refreshing && !resource.error ? ' · last saved view' : ''}
      {resource.refreshing ? ' · refreshing…' : ''}
      {resource.error ? ' · unable to refresh, showing the last successful view' : ''}
      {resource.error && resource.refresh && <button type="button" onClick={resource.refresh}>Retry</button>}
    </span>
  )
}
