/**
 * PLAYER CARD (/player): you as a holographic trading card. Level, title, XP and where it came from, the day
 * streak, your Arena record against AISP, and every trophy (locked ones show how to earn them). The card tilts
 * and shines under the pointer; "Save as image" draws it to a canvas in the browser (no server, no cost).
 */
import { useRef, useState, type CSSProperties, type PointerEvent } from 'react'
import { Link } from 'react-router-dom'
import { AispSide } from '../flow/AispSide'
import { useOptionalUser } from '../../hooks/useAuth'
import { resetProgress, useProgress, type Progress } from './progress'
import '../slate/slate.css'
import './progress.css'

const TIER: Record<string, [string, string]> = {
  ROOKIE: ['#64748b', '#cbd5e1'], SCOUT: ['#0e7490', '#67e8f9'], ANALYST: ['#1d4ed8', '#93c5fd'],
  SHARP: ['#7c3aed', '#c4b5fd'], ORACLE: ['#b45309', '#fde68a'], LEGEND: ['#be123c', '#fda4af'],
}

export function PlayerCardPage() {
  const p = useProgress()
  const user = useOptionalUser()
  const name = (user?.display_name ?? 'You').toUpperCase()
  const [tilt, setTilt] = useState({ x: 0, y: 0, mx: 50, my: 50 })
  const card = useRef<HTMLDivElement | null>(null)
  const [c1, c2] = TIER[p.title] ?? TIER.ROOKIE
  const got = p.badges.filter((b) => b.got)
  const next = p.badges.filter((b) => !b.got).slice(0, 3)
  const hit = p.arena.settled ? Math.round((100 * p.arena.right) / p.arena.settled) : null

  const move = (e: PointerEvent<HTMLDivElement>) => {
    const r = card.current?.getBoundingClientRect()
    if (!r) return
    const mx = ((e.clientX - r.left) / r.width) * 100, my = ((e.clientY - r.top) / r.height) * 100
    setTilt({ x: (mx - 50) / 3.2, y: -(my - 50) / 3.2, mx, my })
  }

  return (
    <div className="hq">
      <AispSide />
      <div className="hq-main">
        <main id="main-content" className="pc-main">
          <section className="pc-stage">
            <div className="pc-tiltbox" onPointerMove={move} onPointerLeave={() => setTilt({ x: 0, y: 0, mx: 50, my: 50 })}>
              <div ref={card} className="pc-card" style={{ '--c1': c1, '--c2': c2, '--mx': `${tilt.mx}%`, '--my': `${tilt.my}%`, transform: `rotateY(${tilt.x}deg) rotateX(${tilt.y}deg)` } as CSSProperties}>
                <div className="pc-card__holo" aria-hidden="true" />
                <header className="pc-card__head"><span>AISP</span><span>SEASON 2026-27</span></header>
                <div className="pc-card__lv"><b>{p.level}</b><small>LEVEL</small></div>
                <div className="pc-card__name">{name}</div>
                <div className="pc-card__title">{p.title}</div>
                <div className="pc-card__xp"><i style={{ width: `${(100 * p.into) / p.need}%` }} /><span>{p.xp.toLocaleString()} XP</span></div>
                <dl className="pc-card__stats">
                  <div><dt>vs AISP</dt><dd>{p.arena.right}–{p.arena.settled - p.arena.right}</dd></div>
                  <div><dt>Hit rate</dt><dd>{hit == null ? '—' : `${hit}%`}</dd></div>
                  <div><dt>Best combo</dt><dd>{p.arena.bestCombo}</dd></div>
                  <div><dt>Machine breaks</dt><dd>{p.arena.beatAisp}</dd></div>
                  <div><dt>Day streak</dt><dd>{p.streak.current}</dd></div>
                  <div><dt>Trophies</dt><dd>{got.length}</dd></div>
                </dl>
                <div className="pc-card__trophies">{got.length ? got.slice(0, 8).map((b) => <i key={b.id} title={b.name}>{b.icon}</i>) : <small>No trophies yet</small>}</div>
                <footer className="pc-card__foot">PLAY ONLY · NO CASH VALUE</footer>
              </div>
            </div>
            <div className="pc-side">
              <h1>Your player card</h1>
              <p className="pc-lede">One level for the whole site. Everything you do here earns XP; the trophies below say what is left to unlock.</p>
              <div className="pc-actions">
                <button type="button" className="pc-btn pc-btn--primary" onClick={() => saveImage(p, name, c1, c2)}>Save as image</button>
                <Link to="/arena" className="pc-btn">Earn XP in The Arena →</Link>
              </div>
              <section className="pc-panel">
                <h2>Where your XP came from</h2>
                <ul className="pc-sources">
                  {p.sources.map((s) => (
                    <li key={s.label}><span>{s.label}</span><i style={{ width: `${p.xp ? (100 * s.xp) / p.xp : 0}%` }} /><b>{s.xp.toLocaleString()}</b></li>
                  ))}
                </ul>
                <p className="pc-fine">+25 XP the first time you open a page · +50 XP per day you visit · +50 XP per pick you lock in My Picks · Arena calls: 100–300 XP when right, ×1.5 blind, +150 for beating AISP.</p>
              </section>
              {next.length ? (
                <section className="pc-panel">
                  <h2>Next up</h2>
                  <ul className="pc-next">{next.map((b) => <li key={b.id}><i>{b.icon}</i><span><b>{b.name}</b><small>{b.how}</small></span></li>)}</ul>
                </section>
              ) : null}
            </div>
          </section>
          <section className="pc-panel">
            <h2>Trophy case <small>{got.length}/{p.badges.length}</small></h2>
            <ul className="pc-trophies">
              {p.badges.map((b) => <li key={b.id} className={b.got ? 'is-got' : ''}><i>{b.icon}</i><b>{b.name}</b><small>{b.how}</small></li>)}
            </ul>
          </section>
          <p className="pc-fine">Saved to your account when you are signed in, so it follows you to any device; signed out, it stays in this browser. <button type="button" className="pc-reset" onClick={() => { if (window.confirm('Reset your XP, streak and site trophies? (Arena calls stay; reset those in The Arena.)')) resetProgress() }}>reset site progress</button></p>
        </main>
      </div>
    </div>
  )
}

/** Draw the card to a 600x840 canvas and download it as a PNG. */
function saveImage(p: Progress, name: string, c1: string, c2: string): void {
  const cv = document.createElement('canvas')
  cv.width = 600; cv.height = 840
  const g = cv.getContext('2d')
  if (!g) return
  const bg = g.createLinearGradient(0, 0, 600, 840)
  bg.addColorStop(0, c1); bg.addColorStop(0.55, '#0b1220'); bg.addColorStop(1, c1)
  g.fillStyle = bg; roundRect(g, 0, 0, 600, 840, 36); g.fill()
  g.strokeStyle = c2; g.lineWidth = 8; roundRect(g, 14, 14, 572, 812, 28); g.stroke()
  g.fillStyle = c2; g.font = '800 22px system-ui'; g.fillText('AISP', 48, 72); g.textAlign = 'right'; g.fillText('SEASON 2026-27', 552, 72); g.textAlign = 'left'
  g.beginPath(); g.arc(300, 220, 96, 0, Math.PI * 2); g.fillStyle = '#0b1220'; g.fill(); g.lineWidth = 10; g.strokeStyle = c2; g.stroke()
  g.fillStyle = '#fff'; g.font = '900 96px system-ui'; g.textAlign = 'center'; g.fillText(String(p.level), 300, 254)
  g.font = '900 52px system-ui'; g.fillText(name.slice(0, 16), 300, 392)
  g.fillStyle = c2; g.font = '800 28px system-ui'; g.fillText(p.title, 300, 436)
  g.fillStyle = '#1e293b'; roundRect(g, 60, 466, 480, 20, 10); g.fill()
  g.fillStyle = c2; roundRect(g, 60, 466, Math.max(20, (480 * p.into) / p.need), 20, 10); g.fill()
  g.fillStyle = '#e2e8f0'; g.font = '700 20px ui-monospace, monospace'; g.fillText(`${p.xp.toLocaleString()} XP`, 300, 520)
  const stats: Array<[string, string]> = [['vs AISP', `${p.arena.right}-${p.arena.settled - p.arena.right}`], ['Best combo', String(p.arena.bestCombo)], ['Machine breaks', String(p.arena.beatAisp)], ['Day streak', String(p.streak.current)]]
  stats.forEach(([k, v], i) => {
    const x = 120 + (i % 2) * 260, y = 580 + Math.floor(i / 2) * 84
    g.fillStyle = '#94a3b8'; g.font = '700 18px system-ui'; g.fillText(k.toUpperCase(), x + 60, y)
    g.fillStyle = '#fff'; g.font = '900 38px system-ui'; g.fillText(v, x + 60, y + 42)
  })
  g.font = '34px system-ui'; g.fillText(p.badges.filter((b) => b.got).slice(0, 10).map((b) => b.icon).join(' ') || ' ', 300, 772)
  g.fillStyle = '#94a3b8'; g.font = '700 14px ui-monospace, monospace'; g.fillText('PLAY ONLY · NO CASH VALUE', 300, 808)
  cv.toBlob((blob) => {
    if (!blob) return
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = `aisp-player-card-lv${p.level}.png`
    document.body.appendChild(a); a.click(); a.remove()
    window.setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }, 'image/png')
}
function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath()
}
