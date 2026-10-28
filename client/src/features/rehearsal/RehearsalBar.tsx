/**
 * The REHEARSAL control bar: shown on every page (from the one left menu) while a rehearsal runs, so it is never
 * mistaken for a real night. Simulated clock, pause, speed, skip ahead, the live You Call It rehearsal for the
 * night's NFL game, re-seal today's Drop, and stop.
 */
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { operationalToday } from '../../services/operationalDate'
import { usePulse } from '../pulse/pulseData'
import { jump, setSpeed, stopRehearsal, togglePause, useRehearsal } from './rehearsal'
import './rehearsal.css'

const clock = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(ms))

export function RehearsalBar() {
  const r = useRehearsal()
  if (!r.active || typeof document === 'undefined') return null
  return createPortal(<Bar r={r} />, document.body)
}

function Bar({ r }: { r: ReturnType<typeof useRehearsal> }) {
  const pulse = usePulse(operationalToday())
  const nfl = pulse.games.filter((g) => g.day === r.day && g.sport === 'NFL').sort((a, b) => Date.parse(b.startsAt) - Date.parse(a.startsAt))[0]
  const reseal = () => { const d = operationalToday(); try { localStorage.removeItem(`aisp-drop-opened-${d}`); localStorage.removeItem(`aisp-drop-collapsed-${d}`) } catch { /* private window */ } window.location.assign('/') }
  return (
    <div className="rh" role="region" aria-label="Rehearsal controls">
      <b className="rh__tag">🎬 REHEARSAL</b>
      <span className="rh__clock">{r.day} replayed · <b>{clock(r.now)} ET</b>{r.paused ? ' · paused' : ` · ×${r.speed}`}</span>
      <button type="button" onClick={togglePause}>{r.paused ? '▶ Play' : '⏸ Pause'}</button>
      <select value={r.speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
        {[24, 72, 144, 288].map((s) => <option key={s} value={s}>×{s}</option>)}
      </select>
      <button type="button" onClick={() => jump(30)}>+30 min</button>
      <button type="button" onClick={() => jump(120)}>+2 h</button>
      {nfl ? <Link to={`/live?demo=nfl:${r.day.replace(/-/g, '')}:${nfl.away.abbr}:${nfl.home.abbr}&rehearse=1`}>🏈 You Call It live</Link> : null}
      <Link to="/arena">⚔ Arena</Link>
      <Link to="/game-night">♥ Sweat Meter</Link>
      <button type="button" onClick={reseal} title="Re-seal today's Drop so you can open the pack again">↺ Re-seal The Drop</button>
      <button type="button" className="rh__stop" onClick={stopRehearsal}>■ Stop</button>
    </div>
  )
}
