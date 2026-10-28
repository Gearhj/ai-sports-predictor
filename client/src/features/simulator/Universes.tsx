/**
 * 10,000 UNIVERSES (10X plan, Game Simulator: "simulated games stream in as particles that settle into score bins
 * while you watch, then the most likely final pops out").
 *
 * Every particle is one game drawn from the model's own stored distribution (makeSampler over the stored cells; the
 * browser models nothing). It falls into its column of home margin, the columns grow into the model's real shape,
 * live counters run beside it, and at 10,000 the most common final in this run pops out next to the model's own
 * numbers. Replay draws a fresh 10,000: the run-to-run wobble is luck, not the model changing.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play, RotateCcw, Zap } from 'lucide-react'
import { teamIdentity } from '../../services/teamIdentity'
import { makeSampler, type Cell, type SimModel } from './simData'

const N = 10_000
const SPAN: Record<string, number> = { NFL: 35, CFB: 40, NBA: 30, NHL: 6, MLB: 8 }
const H = 340
type Speed = 1 | 4
const lumOf = (hex: string) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return 1
  const n = parseInt(m[1], 16)
  return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255
}
/** Team colours on a navy well: a dark primary (Green Bay's #203731) gives way to the club's secondary (gold) when
 * that reads, else the primary is lifted toward white. */
export function teamColor(sport: string, abbr: string, primary: string): string {
  if (lumOf(primary) >= 0.3) return primary
  const second = teamIdentity(sport, abbr).secondary
  return second && lumOf(second) >= 0.3 ? second : readable(primary)
}
function readable(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
  if (lum >= 0.38) return `#${m[1]}`
  const t = Math.min(0.6, (0.42 - lum) * 1.4)
  r = Math.round(r + (255 - r) * t); g = Math.round(g + (255 - g) * t); b = Math.round(b + (255 - b) * t)
  return `rgb(${r}, ${g}, ${b})`
}
interface Drop { x: number; y: number; vy: number; tx: number; bin: number; c: string }

export function Universes({ m, colors: raw }: { m: SimModel; colors: { away: string; home: string } }) {
  const colors = useMemo(() => ({ away: teamColor(m.sport, m.away.abbr, raw.away), home: teamColor(m.sport, m.home.abbr, raw.home) }), [m.sport, m.away.abbr, m.home.abbr, raw.away, raw.home])
  const wrap = useRef<HTMLDivElement | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const sampler = useMemo(() => makeSampler(m.cells), [m.cells])
  const span = SPAN[m.sport] ?? 30
  const bins = span * 2 + 1
  // the model's own margin shape sets the y scale, so the columns grow to their true final height
  const expectedMax = useMemo(() => {
    const per = new Array(bins).fill(0)
    for (const c of m.cells) per[Math.max(0, Math.min(bins - 1, c.home - c.away + span))] += c.p
    return Math.max(...per) * N * 1.08
  }, [m.cells, bins, span])
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const [speed, setSpeed] = useState<Speed>(1)
  const [playing, setPlaying] = useState(true)
  const [run, setRun] = useState(0)
  const [stats, setStats] = useState({ n: 0, home: 0, away: 0, tie: 0, top: null as null | { cell: Cell; count: number } })
  const state = useRef({ n: 0, counts: new Array(bins).fill(0) as number[], drops: [] as Drop[], home: 0, away: 0, tie: 0, cells: new Map<string, { cell: Cell; count: number }>() })
  const speedRef = useRef(speed); speedRef.current = speed
  const playRef = useRef(playing); playRef.current = playing

  useEffect(() => {
    const s = { n: 0, counts: new Array(bins).fill(0) as number[], drops: [] as Drop[], home: 0, away: 0, tie: 0, cells: new Map<string, { cell: Cell; count: number }>() }
    state.current = s
    setStats({ n: 0, home: 0, away: 0, tie: 0, top: null })
    const cv = canvas.current, box = wrap.current
    if (!cv || !box) return undefined
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    let W = box.clientWidth
    const size = () => { W = box.clientWidth; cv.width = W * dpr; cv.height = H * dpr; cv.style.width = `${W}px`; cv.style.height = `${H}px` }
    size()
    const ctx = cv.getContext('2d')
    if (!ctx) return undefined
    const pad = { l: 16, r: 16, t: 26, b: 28 }
    const colX = (b: number) => pad.l + ((b + 0.5) / bins) * (W - pad.l - pad.r)
    const colW = () => (W - pad.l - pad.r) / bins
    const yOf = (count: number) => H - pad.b - (count / expectedMax) * (H - pad.t - pad.b)
    const colorOf = (b: number) => (b > span ? colors.home : b < span ? colors.away : '#94a3b8')
    const land = (cell: Cell) => {
      const k = `${cell.away}-${cell.home}`
      const hit = s.cells.get(k)
      if (hit) hit.count += 1; else s.cells.set(k, { cell, count: 1 })
      if (cell.home > cell.away) s.home += 1; else if (cell.away > cell.home) s.away += 1; else s.tie += 1
    }
    const spawn = (count: number) => {
      for (let i = 0; i < count && s.n < N; i++) {
        const cell = sampler()
        const bin = Math.max(0, Math.min(bins - 1, cell.home - cell.away + span))
        s.n += 1
        land(cell)
        if (reduced) { s.counts[bin] += 1; continue }
        s.drops.push({ x: W / 2 + (Math.random() - 0.5) * W * 0.5, y: pad.t - 10 - Math.random() * 30, vy: 1 + Math.random() * 2, tx: colX(bin) + (Math.random() - 0.5) * colW() * 0.6, bin, c: colorOf(bin) })
      }
    }
    let raf = 0, last = 0, acc = 0, tick = 0
    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      // zero line and axis
      ctx.strokeStyle = 'rgba(148,163,184,.35)'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(colX(span), pad.t - 6); ctx.lineTo(colX(span), H - pad.b); ctx.stroke()
      ctx.fillStyle = '#94a3b8'; ctx.font = '600 11px system-ui'; ctx.textAlign = 'center'
      const step = span >= 30 ? 10 : span >= 8 ? 2 : 1
      for (let v = -span; v <= span; v += step) ctx.fillText(v > 0 ? `+${v}` : `${v}`, colX(v + span), H - 9)
      ctx.textAlign = 'left'; ctx.fillText(`◀ ${m.away.abbr} wins by more`, pad.l, 14)
      ctx.textAlign = 'right'; ctx.fillText(`${m.home.abbr} wins by more ▶`, W - pad.r, 14)
      // columns
      const cw = colW()
      for (let b = 0; b < bins; b++) {
        if (!s.counts[b]) continue
        const y = yOf(s.counts[b])
        ctx.fillStyle = colorOf(b)
        ctx.globalAlpha = 0.9
        ctx.fillRect(colX(b) - cw * 0.42, y, cw * 0.84, H - pad.b - y)
      }
      ctx.globalAlpha = 1
      // falling universes
      for (const d of s.drops) {
        ctx.fillStyle = d.c
        ctx.beginPath(); ctx.arc(d.x, d.y, 2.2, 0, Math.PI * 2); ctx.fill()
      }
    }
    const frame = (t: number) => {
      const dt = last ? Math.min(50, t - last) : 16
      last = t
      if (playRef.current && s.n < N) {
        acc += (N / (speedRef.current === 4 ? 3000 : 12000)) * dt
        const k = Math.floor(acc); acc -= k
        spawn(k)
      }
      // physics: fall, ease sideways toward the column, settle on its stack
      const keep: Drop[] = []
      for (const d of s.drops) {
        d.vy += 0.35; d.y += d.vy; d.x += (d.tx - d.x) * 0.12
        if (d.y >= yOf(s.counts[d.bin] + 1)) s.counts[d.bin] += 1
        else keep.push(d)
      }
      s.drops = keep
      draw()
      if (++tick % 6 === 0 || (s.n >= N && !s.drops.length)) {
        let top: { cell: Cell; count: number } | null = null
        for (const v of s.cells.values()) if (!top || v.count > top.count) top = v
        setStats({ n: s.n, home: s.home, away: s.away, tie: s.tie, top })
      }
      if (s.n < N || s.drops.length) raf = window.requestAnimationFrame(frame)
    }
    if (reduced) { spawn(N); draw(); let top: { cell: Cell; count: number } | null = null; for (const v of s.cells.values()) if (!top || v.count > top.count) top = v; setStats({ n: s.n, home: s.home, away: s.away, tie: s.tie, top }) }
    else raf = window.requestAnimationFrame(frame)
    const ro = new ResizeObserver(() => { size(); draw() })
    ro.observe(box)
    return () => { window.cancelAnimationFrame(raf); ro.disconnect() }
  }, [run, sampler, bins, span, expectedMax, colors.home, colors.away, m.away.abbr, m.home.abbr, reduced])

  const done = stats.n >= N
  const share = (x: number) => (stats.n ? `${((x / stats.n) * 100).toFixed(1)}%` : '—')
  const top = stats.top
  return (
    <section className="uni" aria-label="10,000 universes">
      <header className="uni__head">
        <div>
          <p className="sim-eyebrow"><Zap size={13} /> 10,000 universes</p>
          <h2>Play tonight 10,000 times</h2>
          <p className="sim-note">Each dot is one game drawn from the model’s stored simulations, landing on its winning margin. The columns grow into the model’s shape; the counters are this run’s luck.</p>
        </div>
        <div className="uni__ctrl">
          <button type="button" className="uni__btn" onClick={() => setPlaying((p) => !p)} disabled={done}>{playing && !done ? <><Pause size={14} /> Pause</> : <><Play size={14} /> Play</>}</button>
          <span className="uni__seg" role="group" aria-label="Speed">{([1, 4] as Speed[]).map((v) => <button key={v} type="button" aria-pressed={speed === v} className={speed === v ? 'is-on' : ''} onClick={() => setSpeed(v)}>{v}×</button>)}</span>
          <button type="button" className="uni__btn uni__btn--go" onClick={() => { setPlaying(true); setRun((r) => r + 1) }}><RotateCcw size={14} /> New 10,000</button>
        </div>
      </header>
      <div className="uni__stage">
        <div className="uni__canvas" ref={wrap}><canvas ref={canvas} role="img" aria-label={`Simulated home margins, ${stats.n} of ${N} universes`} /></div>
        <aside className="uni__side" aria-live="polite">
          <div className="uni__count"><small>UNIVERSES</small><b>{stats.n.toLocaleString()}</b><i><em style={{ width: `${(stats.n / N) * 100}%` }} /></i></div>
          <div className="uni__wins">
            <span style={{ ['--c' as string]: colors.away }}><small>{m.away.abbr} wins</small><b>{share(stats.away)}</b></span>
            {stats.tie > 0 && <span style={{ ['--c' as string]: '#94a3b8' }}><small>tie</small><b>{share(stats.tie)}</b></span>}
            <span style={{ ['--c' as string]: colors.home }}><small>{m.home.abbr} wins</small><b>{share(stats.home)}</b></span>
          </div>
          <p className="uni__model">Model’s own split: {m.away.abbr} {(m.win.away * 100).toFixed(1)}% · {m.home.abbr} {(m.win.home * 100).toFixed(1)}%</p>
          <div className={`uni__final${done ? ' is-done' : ''}`}>
            <small>{done ? 'MOST LIKELY FINAL' : 'Most common so far'}</small>
            <b>{top ? <>{m.away.abbr} {top.cell.away}<em>–</em>{top.cell.home} {m.home.abbr}</> : '…'}</b>
            <span>{top ? `${top.count.toLocaleString()} of ${stats.n.toLocaleString()} universes (${((top.count / Math.max(1, stats.n)) * 100).toFixed(1)}%)` : 'the first universes are landing'}</span>
          </div>
        </aside>
      </div>
    </section>
  )
}
