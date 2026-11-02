/** The FULL / FOCUS switch (owner 10-27: make it impossible to miss). On the full site it floats as a glowing pill at
 *  the bottom of the screen (sweep 11-01: pinned top-right it covered the header's date arrow, bell and Sign in);
 *  inside Focus mode it sits in the Focus top bar. */
import { setMode, useMode } from './mode'
import './focus.css'

export function ModeToggle({ floating = false }: { floating?: boolean }) {
  const mode = useMode()
  return (
    <div className={`fm-toggle${floating ? ' fm-toggle--float' : ''}${mode === 'focus' ? ' is-focus' : ''}`} role="group" aria-label="View: Full site or Focus mode">
      <span className="fm-toggle__label" aria-hidden="true">VIEW</span>
      <button type="button" aria-pressed={mode === 'full'} onClick={() => setMode('full')} title="Everything: models, markets, research">Full</button>
      <button type="button" aria-pressed={mode === 'focus'} onClick={() => setMode('focus')} title="Just your games and wagers, built for your phone">✦ Focus</button>
      <i aria-hidden="true" />
    </div>
  )
}
