/** A one-time welcome on Home for a first visit: one line pointing at the map. Gone once seen or dismissed. */
import { useState } from 'react'
import { Link } from 'react-router-dom'

const KEY = 'aisp.start.seen'

function seen(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

export function StartNudge() {
  const [hidden, setHidden] = useState(seen)
  if (hidden) return null
  const dismiss = () => { try { localStorage.setItem(KEY, '1') } catch { /* private window */ } setHidden(true) }
  return (
    <aside className="st-nudge" aria-label="New to AISP">
      <i aria-hidden="true">✺</i>
      <p><b>First time here?</b> AISP has a lot on it. The 60-second map shows every page and three routes through it.</p>
      <Link to="/start" className="st-nudge__go">Show me the map →</Link>
      <button type="button" onClick={dismiss} aria-label="Dismiss">×</button>
    </aside>
  )
}
