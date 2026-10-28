/**
 * The player HUD in the one left menu (so it is on every page): level ring, title, XP bar, day streak and
 * trophy count, linking to the Player Card. It also records the visit (page + day) and hosts the trophy /
 * level-up toasts, rendered into document.body so they show even where the menu is hidden (phones).
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation } from 'react-router-dom'
import { markProgressSeen, trackVisit, useProgress, type Badge } from './progress'
import { chime, levelUp, setSound, useSoundOn } from './sound'
import './progress.css'

interface Toast { id: string; icon: string; title: string; sub: string; kind: 'badge' | 'level' }

export function PlayerHud({ compact = false }: { compact?: boolean }) {
  const { pathname } = useLocation()
  const p = useProgress()
  useEffect(() => { trackVisit(pathname) }, [pathname])
  const pctInto = Math.min(100, (100 * p.into) / p.need)
  const sound = useSoundOn()
  const got = p.badges.filter((b) => b.got).length
  if (compact) {
    return (
      <Link to="/player" className="ph-ring ph-ring--compact" title={`Level ${p.level} ${p.title} · ${p.xp.toLocaleString()} XP`} aria-label={`Player card: level ${p.level}`} style={{ '--ph': `${pctInto}%` } as React.CSSProperties}>
        <b>{p.level}</b>
        <ProgressToasts />
      </Link>
    )
  }
  return (
    <>
      <Link to="/player" className={`ph${pathname === '/player' ? ' is-on' : ''}`} aria-label={`Player card: level ${p.level} ${p.title}, ${p.xp} XP`}>
        <span className="ph-ring" style={{ '--ph': `${pctInto}%` } as React.CSSProperties}><b>{p.level}</b></span>
        <span className="ph-body">
          <span className="ph-top"><b>{p.title}</b><small>{p.xp.toLocaleString()} XP</small></span>
          <span className="ph-bar" title={`${p.need - p.into} XP to level ${p.level + 1}`}><i style={{ width: `${pctInto}%` }} /></span>
          <span className="ph-meta">
            <small title="Days in a row">🔥 {p.streak.current}d</small>
            <small title="Trophies">🏆 {got}/{p.badges.length}</small>
            <small title={`${p.need - p.into} XP to level ${p.level + 1}`}>LV{p.level + 1} −{p.need - p.into}</small>
          </span>
        </span>
      </Link>
      <button type="button" className={`ph-sound${sound ? ' is-on' : ''}`} onClick={() => setSound(!sound)} aria-pressed={sound} title={sound ? 'Moment sounds on' : 'Turn on moment sounds (trophies, level-ups, hits, goal horns)'}>{sound ? '🔊 Sound on' : '🔈 Sound off'}</button>
      <ProgressToasts />
    </>
  )
}

/**
 * Toasts live in a module-level queue (each page mounts its own menu, so component state would be wiped by
 * navigation). Each toast carries its own expiry; whichever host is mounted shows the live ones.
 */
let QUEUE: Array<Toast & { until: number }> = []
const TOAST_EVENT = 'aisp-progress-toast'
const TOAST_MS = 5200
function pushToasts(list: Toast[]): void {
  const now = Date.now()
  QUEUE = [...QUEUE.filter((t) => t.until > now && !list.some((n) => n.id === t.id)), ...list.map((t) => ({ ...t, until: now + TOAST_MS }))]
  window.dispatchEvent(new CustomEvent(TOAST_EVENT))
}

/** Watches the derived progress; any trophy or level not yet seen gets one toast, then is marked seen. */
function ProgressToasts() {
  const { pathname } = useLocation()
  const p = useProgress()
  const [, bump] = useState(0)
  const gotIds = p.badges.filter((b) => b.got).map((b) => b.id)
  const key = `${gotIds.join(',')}|${p.level}`
  useEffect(() => {
    const on = () => bump((n) => n + 1)
    window.addEventListener(TOAST_EVENT, on)
    const t = window.setInterval(() => { if (QUEUE.some((x) => x.until <= Date.now())) { QUEUE = QUEUE.filter((x) => x.until > Date.now()); on() } }, 500)
    return () => { window.removeEventListener(TOAST_EVENT, on); window.clearInterval(t) }
  }, [])
  useEffect(() => {
    // First ever load: adopt the current state silently (no flood of toasts for things earned before this feature).
    if (p.seen.level === 0) { markProgressSeen(gotIds, p.level); return }
    const fresh: Badge[] = p.badges.filter((b) => b.got && !p.seen.badges.includes(b.id))
    const out: Toast[] = fresh.map((b) => ({ id: `b:${b.id}`, icon: b.icon, title: `Trophy unlocked: ${b.name}`, sub: b.how, kind: 'badge' }))
    // The Arena plays its own full-screen level-up; elsewhere the toast carries it.
    if (p.level > p.seen.level && pathname !== '/arena') out.unshift({ id: `l:${p.level}`, icon: '⬆', title: `Level ${p.level}: ${p.title}`, sub: `${p.xp.toLocaleString()} XP`, kind: 'level' })
    if (out.length) { pushToasts(out); if (out.some((t) => t.kind === 'level')) levelUp(); else chime() }
    if (out.length || p.level !== p.seen.level) markProgressSeen(gotIds, Math.max(p.level, p.seen.level))
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const live = QUEUE.filter((t) => t.until > Date.now())
  if (typeof document === 'undefined' || !live.length) return null
  return createPortal(
    <div className="ph-toasts" role="status" aria-live="polite">
      {live.map((t) => (
        <Link key={t.id} to="/player" className={`ph-toast ph-toast--${t.kind}`} style={{ animationDelay: `0s, ${Math.max(0, t.until - Date.now() - 400)}ms` }}>
          <i aria-hidden="true">{t.icon}</i>
          <span><b>{t.title}</b><small>{t.sub}</small></span>
        </Link>
      ))}
    </div>,
    document.body,
  )
}
