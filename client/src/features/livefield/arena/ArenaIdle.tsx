/**
 * BETWEEN PLAYS (owner 11-01: "about 30 seconds of game play repeats over and over ... it makes me question the
 * validity of the animations"). The court and the rink never pretend: a play moves on its own only when ESPN posts it.
 * Shared by the NBA arena and the NHL rink:
 *   - INSTANT REPLAY badge: the only way a past play moves again, and it says so (and how long ago it happened).
 *   - GAME SO FAR board: during a timeout, a review, an intermission, or any quiet stretch, the venue shows the
 *     night's numbers instead of looping. Slides rotate every few seconds; tap a dot to hold one.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import type { ArenaPlay } from './arenaData'

/** A play ESPN logs when the game itself is stopped. */
export function stoppageOf(p: ArenaPlay | null | undefined): { kind: 'timeout' | 'end' | 'review' | 'delay' | 'intermission'; label: string } | null {
  const t = `${p?.type ?? ''} ${p?.text ?? ''}`
  if (!p || !t.trim()) return null
  if (/intermission/i.test(t)) return { kind: 'intermission', label: 'INTERMISSION' }
  if (/end of (the )?(game|period|quarter|half|1st|2nd|3rd|4th|overtime)|period end|end period|end of regulation/i.test(t)) return { kind: 'end', label: 'END OF PERIOD' }
  if (/timeout|time out/i.test(t)) return { kind: 'timeout', label: /official/i.test(t) ? 'OFFICIAL TIMEOUT' : 'TIMEOUT' }
  if (/instant replay|review|challenge/i.test(t)) return { kind: 'review', label: 'UNDER REVIEW' }
  if (/delay|stoppage|injur/i.test(t)) return { kind: 'delay', label: 'STOPPAGE' }
  return null
}

/** Date.now(), refreshed every `ms` while `on`; frozen otherwise. */
export function useTicker(on: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return undefined
    setNow(Date.now())
    const t = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(t)
  }, [on, ms])
  return now
}

/** "38s ago" / "1m 20s ago" / "12m ago" / "4h 23m ago" from ESPN's wall clock on the play, or null without one. */
export function agoText(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return null
  const s = Math.max(0, Math.round((now - at) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60); const r = s % 60
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m ago`
  return m < 10 && r ? `${m}m ${r}s ago` : `${m}m ago`
}

export function ReplayBadge({ when, auto, what }: { when: string | null; auto: boolean; what?: string | null }) {
  return (
    <div className="ar-replay" role="status" aria-live="polite">
      <b><i aria-hidden="true">⟲</i> INSTANT REPLAY</b>
      <small>{what ? `${what} · ` : ''}{auto ? 'the moment, once more' : 'at your request'}{when ? ` · ${when}` : ''}</small>
    </div>
  )
}

export interface Slide { key: string; title: string; body: ReactNode }

/** The venue's own board while nothing is happening: one labelled reason, a few slides, a visible timer. */
export function GameSoFar({ label, note, slides, accent, every = 7000 }: { label: string; note?: string | null; slides: Slide[]; accent?: string; every?: number }) {
  const [i, setI] = useState(0)
  const [hold, setHold] = useState(false)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (hold || slides.length < 2) return undefined
    const t = window.setInterval(() => { setI((k) => (k + 1) % slides.length); setTick((n) => n + 1) }, every)
    return () => window.clearInterval(t)
  }, [hold, slides.length, every])
  const slide = slides[Math.min(i, slides.length - 1)]
  if (!slide) return null
  return (
    <div className="ar-sofar" style={{ ['--c' as string]: accent ?? '#2dd4bf' } as CSSProperties} role="region" aria-label="Game so far">
      <header>
        <b>{label}</b>
        <span>GAME SO FAR{note ? ` · ${note}` : ''}</span>
        {slides.length > 1 ? (
          <nav aria-label="Slides">{slides.map((s, k) => <button key={s.key} type="button" className={k === i ? 'is-on' : ''} aria-label={s.title} onClick={() => { setI(k); setHold(true) }} />)}</nav>
        ) : null}
      </header>
      <div className="ar-sofar__slide" key={`${slide.key}:${tick}`}>
        <h4>{slide.title}</h4>
        {slide.body}
      </div>
      {!hold && slides.length > 1 ? <i className="ar-sofar__bar" key={`bar:${tick}`} style={{ animationDuration: `${every}ms` }} aria-hidden="true" /> : null}
    </div>
  )
}
