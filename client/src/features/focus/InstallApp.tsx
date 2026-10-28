import { useState } from 'react'
import { useInstall } from './pwa'

/** "Get the app" chip in the Focus header; hidden once installed or where the browser cannot install. */
export function InstallApp() {
  const { state, install } = useInstall()
  const [tip, setTip] = useState(false)
  if (state === 'installed' || state === 'none') return null
  return (
    <span className="fm-install">
      <button type="button" onClick={() => (state === 'prompt' ? void install() : setTip((v) => !v))} aria-expanded={state === 'ios' ? tip : undefined}>
        <i aria-hidden="true">📲</i>Get the app
      </button>
      {tip && state === 'ios' ? <small role="status">Tap <b>Share</b> <span aria-hidden="true">⎋</span> then <b>Add to Home Screen</b>. AISP opens in Focus, full screen.</small> : null}
    </span>
  )
}
