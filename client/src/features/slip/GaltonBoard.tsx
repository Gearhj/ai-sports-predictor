/**
 * THE GALTON BOARD (My Picks, plan page list): your slip as a bean machine. Each peg row is one leg; each ball is one
 * simulated night that bounces RIGHT where that leg won and LEFT where it lost (won with the model's probability, or
 * the market's), so the bins fill into the real shape of your night: how many legs come in. Under each bin: the
 * share of nights and the average play-money result when exactly that many legs win.
 * Play money, model or no-vig market probabilities; not a wager signal.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { decimalOf, type SimLeg } from '../flow/slip'
import { usd } from '../pulse/pulseData'

const BALLS = 600
const SIMS = 10_000

/** Share of nights and average P/L for each number of winning legs (seeded Monte Carlo). */
export function binsOf(legs: readonly SimLeg[], n = SIMS, seed = 11): Array<{ wins: number; share: number; avg: number }> {
  let s = seed >>> 0
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
  const count = new Array(legs.length + 1).fill(0), sum = new Array(legs.length + 1).fill(0)
  for (let i = 0; i < n; i++) {
    let w = 0, pl = 0
    for (const l of legs) { if (rnd() < l.p) { w++; pl += l.stake * (decimalOf(l.american) - 1) } else pl -= l.stake }
    count[w]++; sum[w] += pl
  }
  return count.map((c, w) => ({ wins: w, share: c / n, avg: c ? sum[w] / c : 0 }))
}

export function GaltonBoard({ model, market }: { model: SimLeg[]; market: SimLeg[] | null }) {
  const [mode, setMode] = useState<'model' | 'market'>('model')
  const [run, setRun] = useState(0)
  const legs = (mode === 'market' && market?.length ? market : model).slice(0, 12)
  const L = legs.length
  const bins = useMemo(() => binsOf(legs), [JSON.stringify(legs)]) // eslint-disable-line react-hooks/exhaustive-deps
  const ref = useRef<HTMLCanvasElement | null>(null)
  const [landed, setLanded] = useState(0)

  useEffect(() => {
    const cv = ref.current
    if (!cv || !L) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    let s = (run * 7919 + 13) >>> 0
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
    // Pre-roll every ball's path: its leg-by-leg outcomes.
    const paths = Array.from({ length: BALLS }, () => legs.map((l) => rnd() < l.p))
    const stacks = new Array(L + 1).fill(0)
    type Ball = { path: boolean[]; t: number }
    const live: Ball[] = []
    let next = 0, raf = 0, done = 0
    const W = cv.clientWidth, H = cv.clientHeight, dpr = window.devicePixelRatio || 1
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    const top = 26, rowH = Math.min(46, (H * 0.5) / Math.max(1, L)), binTop = top + rowH * (L + 0.6), dx = Math.min(72, (W - 40) / (L + 2))
    const cx = W / 2
    const xAt = (row: number, rights: number) => cx + (rights - row / 2) * dx
    const binW = dx * 0.86
    const maxStack = Math.max(...bins.map((b) => b.share)) * BALLS
    const unit = Math.max(0.5, (H - binTop - 34) / Math.max(1, maxStack))
    const draw = () => {
      g.clearRect(0, 0, W, H)
      // pegs: one row per leg
      g.fillStyle = 'rgba(148,163,184,.55)'
      for (let r = 0; r < L; r++) for (let k = 0; k <= r; k++) { g.beginPath(); g.arc(xAt(r, k), top + r * rowH + rowH * 0.5, 4, 0, Math.PI * 2); g.fill() }
      // bins
      for (let b = 0; b <= L; b++) {
        const x = xAt(L, b)
        g.fillStyle = '#0b1220'; g.fillRect(x - binW / 2, binTop, binW, H - binTop - 30)
        const h = stacks[b] * unit
        const grad = g.createLinearGradient(0, H - 30 - h, 0, H - 30)
        const good = bins[b].avg > 0
        grad.addColorStop(0, good ? '#6ee7b7' : '#fda4af'); grad.addColorStop(1, good ? '#059669' : '#be123c')
        g.fillStyle = grad; g.fillRect(x - binW / 2, H - 30 - h, binW, h)
        g.fillStyle = '#94a3b8'; g.font = '700 10px ui-monospace, monospace'; g.textAlign = 'center'
        g.fillText(`${b}/${L}`, x, H - 16)
      }
      // falling balls
      for (const ball of live) {
        const row = Math.min(L, ball.t)
        const whole = Math.floor(row), frac = row - whole
        const rightsBefore = ball.path.slice(0, whole).filter(Boolean).length
        const goRight = ball.path[whole] ?? false
        const x0 = xAt(whole, rightsBefore), x1 = xAt(Math.min(L, whole + 1), rightsBefore + (goRight ? 1 : 0))
        const x = whole >= L ? x0 : x0 + (x1 - x0) * frac
        const y = top + row * rowH + rowH * 0.5 - Math.sin(frac * Math.PI) * 4
        g.fillStyle = '#fde68a'; g.beginPath(); g.arc(x, y, 4.2, 0, Math.PI * 2); g.fill()
      }
    }
    const step = () => {
      for (let i = 0; i < (reduced ? BALLS : 4) && next < BALLS; i++) live.push({ path: paths[next++], t: 0 })
      for (const ball of live) ball.t += reduced ? L + 1 : 0.22
      for (let i = live.length - 1; i >= 0; i--) {
        if (live[i].t >= L + 0.5) { stacks[live[i].path.filter(Boolean).length]++; live.splice(i, 1); done++ }
      }
      draw()
      setLanded(done)
      if (done < BALLS) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [run, JSON.stringify(legs)]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!L) return null
  const best = bins.reduce((a, b) => (b.share > a.share ? b : a), bins[0])
  return (
    <section className="sl-card gb" aria-label="Galton board of your slip">
      <header className="gb__head">
        <h2>The Galton Board</h2>
        <span className="gb__sub">{BALLS} nights drop through {L} peg row{L > 1 ? 's' : ''}: right when a leg wins, left when it loses</span>
        <div className="gb__tabs" role="tablist">
          <button role="tab" aria-selected={mode === 'model'} className={mode === 'model' ? 'is-on' : ''} onClick={() => { setMode('model'); setRun((n) => n + 1) }}>AISP model</button>
          <button role="tab" aria-selected={mode === 'market'} className={mode === 'market' ? 'is-on' : ''} disabled={!market?.length} onClick={() => { setMode('market'); setRun((n) => n + 1) }}>Market</button>
          <button type="button" onClick={() => setRun((n) => n + 1)}>↻ Drop again</button>
        </div>
      </header>
      <div className="gb__body">
        <canvas ref={ref} className="gb__canvas" aria-hidden="true" />
        <ul className="gb__bins">
          {bins.map((b) => (
            <li key={b.wins} className={b.avg > 0 ? 'is-up' : 'is-down'}>
              <b>{b.wins} of {L} win</b><span>{(b.share * 100).toFixed(1)}% of nights</span><em>{usd(b.avg, true)}</em>
            </li>
          ))}
        </ul>
      </div>
      <p className="gb__fine">{landed < BALLS ? `Dropping… ${landed}/${BALLS}` : `Most common night: ${best.wins} of ${L} legs win (${(best.share * 100).toFixed(0)}%).`} Shares and averages come from 10,000 simulated nights; legs are treated as independent. Play money, not a wager signal.</p>
    </section>
  )
}
