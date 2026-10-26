/**
 * THE HEAT, shared by every sport's Live View (owner 10-25: "make these types of features part of every sport's Live
 * View ... a hot player has an aura or a flame around them or the ball is on fire").
 *
 * HeatSpotlight: the "HE'S ON FIRE!" row. Fire = flames behind the face; warm = heating up; ice = locked in (a blue
 * aura for a dealing pitcher, a wall of a goalie, a lockdown defender). A card that reaches fire while the page is
 * open flashes. Takeover: the 2K-style full-width card (huge face, the word, tonight's line) with a confetti burst.
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import './heat.css'

export type HeatLevel = 'fire' | 'warm' | 'ice'
export interface HeatItem { id: string; name: string; face: string | null; color: string; level: HeatLevel; title: string; sub: string; logo?: string | null }

export function Flames({ ice = false }: { ice?: boolean }) {
  return <i className={`heat-flames${ice ? ' is-ice' : ''}`} aria-hidden="true"><i /><i /><i /></i>
}

export function HeatSpotlight({ items, label = 'Who is hot' }: { items: HeatItem[]; label?: string }) {
  const seen = useRef<Set<string> | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const fireKey = items.filter((i) => i.level !== 'warm').map((i) => `${i.id}:${i.title}`).join('|')
  useEffect(() => {
    const now = new Set(fireKey ? fireKey.split('|') : [])
    if (seen.current) for (const k of now) if (!seen.current.has(k)) { setFlash(k.split(':')[0]); window.setTimeout(() => setFlash(null), 5000) }
    seen.current = now
  }, [fireKey])
  if (!items.length) return null
  const order = { fire: 0, ice: 1, warm: 2 } as const
  return (
    <section className="heat" aria-label={label}>
      {[...items].sort((a, b) => order[a.level] - order[b.level]).slice(0, 4).map((i) => (
        <div key={`${i.id}-${i.title}`} className={`heat__card is-${i.level}${flash === i.id ? ' is-flash' : ''}`} style={{ ['--c' as string]: i.color } as CSSProperties}>
          <span className="heat__face">{i.face ? <img src={i.face} alt="" /> : <b>{i.name.slice(0, 1)}</b>}{i.level !== 'warm' ? <Flames ice={i.level === 'ice'} /> : null}</span>
          <span className="heat__txt"><b>{i.title}</b><em>{i.name}</em><small>{i.sub}</small></span>
          {i.logo ? <img src={i.logo} alt="" className="heat__logo" /> : null}
        </div>
      ))}
    </section>
  )
}

export interface TakeoverProps { word: string; name: string; face: string | null; logo?: string | null; color: string; fire?: boolean; stats: Array<[string, string]> }

/** The 2K card: slams in, holds, slides out (about 3 s). Render it inside a position:relative box. */
export function Takeover({ word, name, face, logo, color, fire = false, stats }: TakeoverProps) {
  return (
    <div className={`heat-take${fire ? ' is-fire' : ''}`} style={{ ['--c' as string]: color } as CSSProperties} aria-live="assertive">
      <div className="heat-take__slab">
        {logo ? <img src={logo} alt="" className="heat-take__logo" /> : null}
        {face ? <img src={face} alt="" className="heat-take__face" /> : null}
        {fire ? <Flames /> : null}
      </div>
      <div className="heat-take__txt">
        <b>{word}</b>
        <em>{name}</em>
        <span>{stats.map(([v, l]) => <i key={l}>{v}<small>{l}</small></i>)}</span>
      </div>
      <Confetti color={color} />
    </div>
  )
}

export function Confetti({ color }: { color: string }) {
  const bits = useRef(Array.from({ length: 40 }, (_, k) => ({ x: (Math.random() - 0.5) * 900, y: -Math.random() * 380 - 60, r: Math.random() * 720, d: Math.random() * 0.25, c: k % 3 === 0 ? '#fde047' : k % 3 === 1 ? '#ffffff' : color })))
  return (
    <span className="heat-confetti" aria-hidden="true">
      {bits.current.map((b, k) => <i key={k} style={{ ['--x' as string]: `${b.x}px`, ['--y' as string]: `${b.y}px`, ['--r' as string]: `${b.r}deg`, animationDelay: `${b.d}s`, background: b.c } as CSSProperties} />)}
    </span>
  )
}

/** Fires `onNew` once for each new key after the first render (live events only, never on page load). */
export function useNewKey(key: string | null | undefined, onNew: () => void) {
  const seen = useRef<string | null | undefined>(undefined)
  const cb = useRef(onNew); cb.current = onNew
  useEffect(() => {
    if (key == null) return
    if (seen.current === undefined) { seen.current = key; return }
    if (key !== seen.current) { seen.current = key; cb.current() }
  }, [key])
}

/** A takeover that shows for `ms` and then clears itself. */
export function useTakeover(ms = 3200): [TakeoverProps | null, (t: TakeoverProps) => void] {
  const [t, setT] = useState<TakeoverProps | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])
  return [t, (next) => { if (timer.current) window.clearTimeout(timer.current); setT(next); timer.current = window.setTimeout(() => setT(null), ms) }]
}
