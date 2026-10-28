/** The FULL / FOCUS switch, pinned to the top-right corner of every page (owner 10-27: make it impossible to miss). */
import { setMode, useMode } from './mode'
import './focus.css'

export function ModeToggle() {
  const mode = useMode()
  return (
    <div className={`fm-toggle${mode === 'focus' ? ' is-focus' : ''}`} role="group" aria-label="View: Full site or Focus mode">
      <span className="fm-toggle__label" aria-hidden="true">VIEW</span>
      <button type="button" aria-pressed={mode === 'full'} onClick={() => setMode('full')} title="Everything: models, markets, research">Full</button>
      <button type="button" aria-pressed={mode === 'focus'} onClick={() => setMode('focus')} title="Just your games and wagers, built for your phone">✦ Focus</button>
      <i aria-hidden="true" />
    </div>
  )
}
