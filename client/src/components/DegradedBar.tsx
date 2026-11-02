/**
 * One line at the top of the site when the backend says it is up but not working (scale plan Phase 1, item 5).
 * Reads /healthz every minute; quiet while it answers "ok". Not shown in tests, and the first look waits a few
 * seconds so a page is never judged before it has loaded.
 */
import { useEffect, useState } from 'react'
import { TriangleAlert } from 'lucide-react'
import { API_BASE_URL } from '../services/api'

interface Health { status: 'ok' | 'degraded'; checks: Record<string, string> }

const HEALTHZ = `${API_BASE_URL.replace(/\/api\/v1$/, '')}/healthz`
const EVERY_MS = 60_000
const FIRST_MS = 5_000

function describe(h: Health | null): string {
  if (!h) return 'AISP is not answering right now. Pages may be stale; nothing you do here is lost.'
  const bad = Object.entries(h.checks).filter(([, v]) => !['reachable', 'running', 'disabled'].includes(v)).map(([k]) => k.replace('_', ' '))
  return `AISP is having trouble (${bad.join(', ') || 'degraded'}). Pages may be stale or behind; nothing you do here is lost.`
}

export function DegradedBar() {
  const [text, setText] = useState<string | null>(null)
  useEffect(() => {
    if (import.meta.env.MODE === 'test') return undefined
    let dead = false
    const look = async () => {
      try {
        const r = await fetch(HEALTHZ, { cache: 'no-store' })
        const h = (await r.json()) as Health
        if (!dead) setText(h.status === 'ok' ? null : describe(h))
      } catch {
        if (!dead) setText(describe(null))
      }
    }
    const first = window.setTimeout(look, FIRST_MS)
    const every = window.setInterval(look, EVERY_MS)
    return () => { dead = true; window.clearTimeout(first); window.clearInterval(every) }
  }, [])
  if (!text) return null
  return <div className="degraded-bar" role="status"><TriangleAlert size={14} /> {text}</div>
}
