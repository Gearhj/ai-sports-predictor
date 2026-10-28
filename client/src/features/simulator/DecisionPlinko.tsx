/**
 * THE DECISION TREE (Game Simulator hero, owner 10-27: "10K balls go down the decision tree Plinko chart and shows
 * how the distribution is formed. Make it look fuckin awesome").
 *
 * Every ball is one game drawn from the model's stored simulations (makeSampler; the browser models nothing). It
 * drops through three questions (plinkoTree.ts), the branches thicken with the flow, the balls pile into eight
 * leaves ordered from "dog wins big" to "favourite covers", and the exact margin distribution forms in the strip
 * underneath, coloured by the leaf group each margin belongs to. FOLLOW ONE BALL narrates a single game through the
 * tree; at 10,000 the most likely story pops. The leaves below are every combination, priced.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Footprints, Pause, Play, RotateCcw, Sparkles } from 'lucide-react'
import { makeSampler, type Cell, type SimModel } from './simData'
import { fairOdds, leaves, route, setupFor, type Leaf, type TreeSetup } from './plinkoTree'

const N = 10_000
const H = 724
type Speed = 1 | 4 | 0
interface Ball { cell: Cell; r: ReturnType<typeof route>; t: number; seg: number; phase: number; follow?: boolean; fall?: number }

const odds = (n: number | null) => (n == null ? '—' : n > 0 ? `+${n}` : `${n}`)
const rgba = (hex: string, a: number) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex)
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}

export function DecisionPlinko({ m, colors }: { m: SimModel; colors: { away: string; home: string } }) {
  const [over, setOver] = useState<{ spread?: number; total?: number }>({})
  const s: TreeSetup = useMemo(() => setupFor(m, over), [m, over])
  const exact = useMemo(() => leaves(m.cells, s), [m.cells, s])
  const favColor = s.fav === 'home' ? colors.home : colors.away
  const dogColor = s.fav === 'home' ? colors.away : colors.home
  const sampler = useMemo(() => makeSampler(m.cells), [m.cells])
  const wrap = useRef<HTMLDivElement | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const [speed, setSpeed] = useState<Speed>(1)
  const [playing, setPlaying] = useState(true)
  const [run, setRun] = useState(0)
  const [hot, setHot] = useState<number | null>(null)
  const [stats, setStats] = useState({ n: 0, counts: new Array(8).fill(0) as number[], done: false })
  const [story, setStory] = useState<string[] | null>(null)
  const speedRef = useRef(speed); speedRef.current = speed
  const playRef = useRef(playing); playRef.current = playing
  const hotRef = useRef(hot); hotRef.current = hot
  const followRef = useRef<(() => void) | null>(null)
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    const cv = canvas.current, box = wrap.current
    if (!cv || !box) return undefined
    const ctx = cv.getContext('2d')
    if (!ctx) return undefined
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    let W = box.clientWidth
    const size = () => { W = Math.max(320, box.clientWidth); cv.width = W * dpr; cv.height = H * dpr; cv.style.width = '100%'; cv.style.height = `${H}px` }
    size()
    // ---- geometry
    const Y = [62, 196, 330, 452]            // root, level 1, level 2, leaf mouths
    const PILE_BOTTOM = 572, HIST_TOP = 640, HIST_BOTTOM = 712
    const nodeX = (level: number, i: number) => W * ((2 * i + 1) / (2 ** (level + 1)))
    const leafX = (i: number) => nodeX(3, i)
    const pathOf = (leaf: number) => [{ x: nodeX(0, 0), y: Y[0] }, { x: nodeX(1, leaf >> 2), y: Y[1] }, { x: nodeX(2, leaf >> 1), y: Y[2] }, { x: leafX(leaf), y: Y[3] }]
    const leafColor = (leaf: number) => (leaf >> 1 >= 2 ? favColor : dogColor)
    const maxLeaf = Math.max(...exact.map((l) => l.p)) * N * 1.12
    // the margin strip: fav margin from -span to +span (dog wins on the left, like the leaves)
    const span = m.sport === 'NHL' ? 6 : m.sport === 'MLB' ? 9 : m.sport === 'NBA' ? 30 : 35
    const hb = span * 2 + 1
    const expectedHist = (() => { const a = new Array(hb).fill(0); for (const c of m.cells) a[Math.max(0, Math.min(hb - 1, route(c, s).margin + span))] += c.p; return Math.max(...a) * N * 1.1 })()
    const bestLeaf = exact.reduce((b, l, i) => (l.p > exact[b].p ? i : b), 0)
    const groupOfMargin = (mg: number) => (mg > s.spread ? 3 : mg > 0 ? 2 : -mg <= s.close ? 1 : 0)

    // ---- state
    const st = { n: 0, counts: new Array(8).fill(0) as number[], flow: new Array(15).fill(0) as number[], hist: new Array(hb).fill(0) as number[], balls: [] as Ball[], splash: [] as { x: number; y: number; vx: number; vy: number; life: number; c: string }[] }
    // flow index: root 0, level1 1..2, level2 3..6, leaf 7..14
    const nodeIndex = (level: number, i: number) => (2 ** level) - 1 + i
    let pegs: { x: number; y: number }[] = []
    const buildPegs = () => {
      pegs = []
      for (let row = 0; row < 3; row++) for (let k = 0; k < 26; k++) {
        const y = Y[row] + 34 + ((Y[row + 1] - Y[row] - 60) * ((k % 3) / 3))
        pegs.push({ x: (W / 26) * (k + 0.5) + (row % 2 ? W / 52 : 0), y })
      }
    }
    buildPegs()
    const land = (b: Ball) => {
      st.counts[b.r.leaf] += 1
      st.hist[Math.max(0, Math.min(hb - 1, b.r.margin + span))] += 1
      const x = leafX(b.r.leaf)
      for (let k = 0; k < (b.follow ? 26 : 2); k++) st.splash.push({ x, y: PILE_BOTTOM - (st.counts[b.r.leaf] / maxLeaf) * (PILE_BOTTOM - Y[3] - 16), vx: (Math.random() - 0.5) * (b.follow ? 6 : 2), vy: -Math.random() * (b.follow ? 5 : 2), life: 1, c: leafColor(b.r.leaf) })
    }
    const spawn = (k: number) => {
      for (let i = 0; i < k && st.n < N; i++) {
        const cell = sampler()
        const r = route(cell, s)
        st.n += 1
        if (speedRef.current === 0 || reduced) {
          st.flow[0]++; st.flow[nodeIndex(1, r.leaf >> 2)]++; st.flow[nodeIndex(2, r.leaf >> 1)]++; st.flow[nodeIndex(3, r.leaf)]++
          land({ cell, r, t: 0, seg: 3, phase: 0 })
          continue
        }
        st.flow[0]++
        st.balls.push({ cell, r, t: 0, seg: 0, phase: Math.random() * Math.PI * 2 })
      }
    }
    followRef.current = () => {
      const cell = sampler()
      const r = route(cell, s)
      st.flow[0]++
      st.balls.push({ cell, r, t: 0, seg: 0, phase: 0, follow: true })
      const fav = s.favAbbr, dog = s.dogAbbr
      const sc = `${m.away.abbr} ${cell.away} – ${cell.home} ${m.home.abbr}`
      setStory([`One simulated game: ${sc}`])
      const steps = [
        r.favWins ? `${fav} wins (by ${r.margin}) → right` : r.margin === 0 ? `Level after regulation → left` : `${dog} wins (by ${-r.margin}) → left`,
        r.favWins ? (r.covers ? `${r.margin} > ${s.spread}: ${fav} COVERS → right` : `${r.margin} ≤ ${s.spread}: no cover → left`) : (r.close ? `within ${s.close}: close game → right` : `by ${-r.margin}: a ${dog} rout → left`),
        r.over ? `${r.total} points > ${s.total}: OVER → right` : `${r.total} points ≤ ${s.total}: UNDER → left`,
      ]
      ;(st as unknown as { followSteps: string[] }).followSteps = steps
    }
    let raf = 0, last = 0, acc = 0, tick = 0
    const bez = (a: { x: number; y: number }, b: { x: number; y: number }, t: number) => {
      const c1 = { x: a.x, y: a.y + (b.y - a.y) * 0.55 }, c2 = { x: b.x, y: b.y - (b.y - a.y) * 0.55 }
      const u = 1 - t
      return { x: u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * b.x, y: u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * b.y }
    }
    const pill = (x: number, y: number, text: string, sub: string, color: string, glow: number) => {
      const fs = Math.max(9, Math.min(13, W / 88))
      ctx.font = `800 ${fs}px system-ui`
      const w = Math.max(ctx.measureText(text).width, ctx.measureText(sub).width) + 26
      ctx.save()
      ctx.shadowColor = color; ctx.shadowBlur = 8 + glow * 18
      ctx.fillStyle = '#0b1220'; ctx.strokeStyle = color; ctx.lineWidth = 1.6
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - 22, w, 44, 12); ctx.fill(); ctx.stroke()
      ctx.restore()
      ctx.textAlign = 'center'; ctx.fillStyle = '#f8fafc'; ctx.fillText(text, x, y - 3)
      ctx.font = `700 ${fs - 2}px ui-monospace, monospace`; ctx.fillStyle = color; ctx.fillText(sub, x, y + 13)
    }
    const draw = (t: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      // pegs
      ctx.fillStyle = 'rgba(148,163,184,.13)'
      for (const p of pegs) { ctx.beginPath(); ctx.arc(p.x, p.y, 1.6, 0, Math.PI * 2); ctx.fill() }
      // edges, thick with flow
      const hotLeaf = hotRef.current
      const edge = (a: { x: number; y: number }, b: { x: number; y: number }, flow: number, color: string, onHot: boolean) => {
        ctx.strokeStyle = onHot ? '#fde68a' : rgba(color, 0.18 + 0.55 * Math.min(1, flow / (N * 0.5)))
        ctx.lineWidth = 1.5 + 26 * (flow / N)
        ctx.lineCap = 'round'
        ctx.beginPath(); ctx.moveTo(a.x, a.y + 20)
        ctx.bezierCurveTo(a.x, a.y + (b.y - a.y) * 0.55, b.x, b.y - (b.y - a.y) * 0.55, b.x, b.y - 22)
        ctx.stroke()
      }
      for (let l = 1; l <= 3; l++) for (let i = 0; i < 2 ** l; i++) {
        const leafLo = i * (8 >> l), leafHi = leafLo + (8 >> l) - 1
        const parent = { x: nodeX(l - 1, i >> 1), y: Y[l - 1] }
        const child = { x: l === 3 ? leafX(i) : nodeX(l, i), y: Y[l] }
        const color = leafLo >> 1 >= 2 ? favColor : dogColor
        edge(parent, child, st.flow[nodeIndex(l, i)], color, hotLeaf != null && hotLeaf >= leafLo && hotLeaf <= leafHi)
        ctx.fillStyle = 'rgba(203,213,225,.55)'; ctx.font = '800 10px system-ui'; ctx.textAlign = 'center'
        const mid = bez(parent, child, 0.45)
        ctx.fillText(i % 2 ? 'YES' : 'NO', mid.x + (i % 2 ? 14 : -14), mid.y)
      }
      // leaf piles
      const lw = W / 8
      for (let i = 0; i < 8; i++) {
        const h = (st.counts[i] / maxLeaf) * (PILE_BOTTOM - Y[3] - 16)
        const x0 = i * lw + 6, color = leafColor(i)
        const grd = ctx.createLinearGradient(0, PILE_BOTTOM - h, 0, PILE_BOTTOM)
        grd.addColorStop(0, rgba(color, i % 2 ? 0.95 : 0.7)); grd.addColorStop(1, rgba(color, 0.25))
        ctx.fillStyle = grd
        ctx.beginPath(); ctx.roundRect(x0, PILE_BOTTOM - h, lw - 12, h, [6, 6, 2, 2]); ctx.fill()
        if (i % 2) { ctx.strokeStyle = rgba('#ffffff', 0.25); ctx.lineWidth = 1; ctx.setLineDash([3, 4]); ctx.strokeRect(x0 + 2, PILE_BOTTOM - h + 2, lw - 16, Math.max(0, h - 4)); ctx.setLineDash([]) }
        if (hotLeaf === i || (st.n >= N && !st.balls.length && i === bestLeaf)) {
          ctx.save(); ctx.shadowColor = '#fde68a'; ctx.shadowBlur = 24 + 10 * Math.sin(t / 220)
          ctx.strokeStyle = '#fde68a'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.roundRect(x0 - 2, Y[3] - 6, lw - 8, PILE_BOTTOM - Y[3] + 8, 10); ctx.stroke(); ctx.restore()
        }
        ctx.fillStyle = '#e2e8f0'; ctx.font = '900 15px ui-monospace, monospace'; ctx.textAlign = 'center'
        ctx.fillText(st.n ? `${((st.counts[i] / Math.max(1, st.n)) * 100).toFixed(1)}%` : '', leafX(i), PILE_BOTTOM - h - 8)
        ctx.font = '800 10px system-ui'; ctx.fillStyle = i % 2 ? '#fde68a' : '#93c5fd'
        ctx.fillText(exact[i].short, leafX(i), PILE_BOTTOM + 14)
      }
      // group labels under the piles
      ctx.font = '800 11px system-ui'; ctx.fillStyle = '#cbd5e1'
      const glabel = [`${s.dogAbbr} by ${s.close + 1}+`, `${s.dogAbbr} close / tie`, `${s.favAbbr} wins, no cover`, `${s.favAbbr} covers −${s.spread}`]
      for (let g = 0; g < 4; g++) ctx.fillText(glabel[g], W * ((2 * g + 1) / 8), PILE_BOTTOM + 32)
      // margin strip: the distribution the tree is forming
      const bw = W / hb
      const hh = HIST_BOTTOM - HIST_TOP
      for (let b = 0; b < hb; b++) {
        if (!st.hist[b]) continue
        const h = Math.min(hh, (st.hist[b] / expectedHist) * hh)
        const g = groupOfMargin(b - span)
        ctx.fillStyle = rgba(g >= 2 ? favColor : dogColor, g === 1 || g === 2 ? 0.65 : 0.95)
        ctx.fillRect(b * bw + 1, HIST_BOTTOM - h, Math.max(1, bw - 2), h)
      }
      ctx.fillStyle = '#64748b'; ctx.font = '700 10px system-ui'; ctx.textAlign = 'left'
      ctx.fillText(`◀ ${s.dogAbbr} wins by more`, 8, HIST_TOP - 6)
      ctx.textAlign = 'center'; ctx.fillStyle = '#94a3b8'; ctx.fillText('THE EXACT FINAL MARGINS, FORMING', W / 2, HIST_TOP - 6); ctx.fillStyle = '#64748b'
      ctx.textAlign = 'right'; ctx.fillText(`${s.favAbbr} wins by more ▶`, W - 8, HIST_TOP - 6)
      // balls
      for (const b of st.balls) {
        const pts = pathOf(b.r.leaf)
        let p: { x: number; y: number }
        if (b.seg < 3) {
          const e = Math.pow(b.t, 1.25)
          p = bez(pts[b.seg], pts[b.seg + 1], e)
          p.x += Math.sin(b.t * Math.PI * 4 + b.phase) * 5 * (1 - b.t) * (b.follow ? 0.3 : 1)
        } else {
          const top = PILE_BOTTOM - (st.counts[b.r.leaf] / maxLeaf) * (PILE_BOTTOM - Y[3] - 16)
          p = { x: pts[3].x + Math.sin(b.phase) * (W / 8) * 0.3, y: Y[3] + (top - Y[3]) * (b.fall ?? 0) }
        }
        const c = leafColor(b.r.leaf)
        if (b.follow) {
          ctx.save(); ctx.shadowColor = '#fde68a'; ctx.shadowBlur = 30
          ctx.fillStyle = '#fff7cc'; ctx.beginPath(); ctx.arc(p.x, p.y, 9, 0, Math.PI * 2); ctx.fill(); ctx.restore()
        } else {
          ctx.fillStyle = c; ctx.beginPath(); ctx.arc(p.x, p.y, 2.4, 0, Math.PI * 2); ctx.fill()
        }
      }
      // splashes
      for (const sp of st.splash) { ctx.globalAlpha = sp.life; ctx.fillStyle = sp.c; ctx.fillRect(sp.x, sp.y, 2, 2) }
      ctx.globalAlpha = 1
      // nodes on top
      const pctOf = (k: number, of: number) => (of ? `${((k / of) * 100).toFixed(1)}%` : '—')
      const f0 = st.flow[0]
      pill(nodeX(0, 0), Y[0], `Does ${s.favAbbr} (the favourite) win?`, `${pctOf(st.flow[2], f0)} yes · ${st.n.toLocaleString()} games`, '#c4b5fd', st.n < N ? 1 : 0.3)
      pill(nodeX(1, 0), Y[1], `${s.dogAbbr} side: close game?`, `${pctOf(st.flow[4], st.flow[1])} within ${s.close}`, dogColor, 0.4)
      pill(nodeX(1, 1), Y[1], `${s.favAbbr} covers −${s.spread}?`, `${pctOf(st.flow[6], st.flow[2])} cover${s.spreadFromBook ? ' · book line' : ''}`, favColor, 0.4)
      for (let i = 0; i < 4; i++) pill(nodeX(2, i), Y[2], `Over ${s.total}?`, pctOf(st.flow[nodeIndex(3, i * 2 + 1)], st.flow[nodeIndex(2, i)]), i >= 2 ? favColor : dogColor, 0.2)
    }
    const frame = (t: number) => {
      const dt = last ? Math.min(50, t - last) : 16
      last = t
      const sp = speedRef.current
      if (playRef.current && st.n < N) {
        if (sp === 0) spawn(N)
        else { acc += (N / (sp === 4 ? 6000 : 22000)) * dt; const k = Math.floor(acc); acc -= k; spawn(k) }
      }
      const segMs = sp === 4 ? 260 : 620
      const keep: Ball[] = []
      for (const b of st.balls) {
        const step = dt / (b.follow ? 1500 : segMs)
        if (b.seg < 3) {
          b.t += step
          if (b.t >= 1) {
            b.seg += 1; b.t = 0
            if (b.seg <= 3) { const lvl = b.seg; st.flow[nodeIndex(lvl, lvl === 1 ? b.r.leaf >> 2 : lvl === 2 ? b.r.leaf >> 1 : b.r.leaf)]++ }
            if (b.follow) {
              const steps = (st as unknown as { followSteps?: string[] }).followSteps ?? []
              setStory((prev) => (prev ? [...prev, steps[b.seg - 1]].filter(Boolean) : prev))
            }
          }
          keep.push(b)
        } else {
          b.fall = Math.min(1, (b.fall ?? 0) + dt / (b.follow ? 700 : 260))
          if (b.fall >= 1) {
            land(b)
            if (b.follow) setStory((prev) => (prev ? [...prev, `Lands in “${exact[b.r.leaf].label}”: ${(exact[b.r.leaf].p * 100).toFixed(1)}% of the model's games`] : prev))
          } else keep.push(b)
        }
      }
      st.balls = keep
      for (const p of st.splash) { p.x += p.vx; p.y += p.vy; p.vy += 0.25; p.life -= 0.04 }
      st.splash = st.splash.filter((p) => p.life > 0)
      draw(t)
      if (++tick % 8 === 0) setStats({ n: st.n, counts: [...st.counts], done: st.n >= N && !st.balls.length })
      raf = window.requestAnimationFrame(frame)
    }
    raf = window.requestAnimationFrame(frame)
    const ro = new ResizeObserver(() => { size(); buildPegs() })
    ro.observe(box)
    return () => { window.cancelAnimationFrame(raf); ro.disconnect(); followRef.current = null }
  }, [run, s, exact, sampler, favColor, dogColor, m, reduced]) // eslint-disable-line react-hooks/exhaustive-deps

  const best = exact.reduce<Leaf>((b, l) => (l.p > b.p ? l : b), exact[0])
  const ranked = [...exact].sort((a, b) => b.p - a.p)
  return (
    <section className="dtree" aria-label="Decision tree: how the 10,000 split">
      <header className="dtree__head">
        <div>
          <p className="dtree__kicker"><Sparkles size={13} /> THE DECISION TREE · 10,000 GAMES</p>
          <h2>Every way {m.away.abbr} @ {m.home.abbr} can end, sorted by the bets you can make</h2>
          <p className="dtree__sub">Each ball is one game from the model’s stored simulations. It answers three questions on the way down: does the favourite win, does it cover or keep it close, does it go over. Branches thicken with the flow; the piles and the strip underneath are the distribution forming.</p>
        </div>
        <div className="dtree__ctrl">
          <label>Spread <b>{s.favAbbr} −</b><input type="number" step={0.5} min={0.5} value={s.spread} onChange={(e) => { setOver((o) => ({ ...o, spread: Number(e.target.value) })); setRun((r) => r + 1) }} /><small>{s.spreadFromBook ? 'book line' : 'model line'}</small></label>
          <label>Total <b>O/U</b><input type="number" step={0.5} min={0.5} value={s.total} onChange={(e) => { setOver((o) => ({ ...o, total: Number(e.target.value) })); setRun((r) => r + 1) }} /><small>{s.totalFromBook ? 'book line' : 'model line'}</small></label>
          <span className="dtree__seg" role="group" aria-label="Speed">{([[1, '1×'], [4, '4×'], [0, 'Instant']] as [Speed, string][]).map(([v, l]) => <button key={l} type="button" aria-pressed={speed === v} className={speed === v ? 'is-on' : ''} onClick={() => setSpeed(v)}>{l}</button>)}</span>
          <button type="button" className="dtree__btn" onClick={() => setPlaying((p) => !p)} disabled={stats.done}>{playing && !stats.done ? <><Pause size={14} /> Pause</> : <><Play size={14} /> Play</>}</button>
          <button type="button" className="dtree__btn" onClick={() => { setStory(null); setRun((r) => r + 1) }}><RotateCcw size={14} /> Drop 10,000 again</button>
          <button type="button" className="dtree__btn dtree__btn--follow" onClick={() => followRef.current?.()}><Footprints size={14} /> Follow one ball</button>
        </div>
      </header>
      <div className="dtree__stage">
        <div className="dtree__canvas" ref={wrap}><canvas ref={canvas} role="img" aria-label={`Decision tree of ${stats.n} simulated games`} /></div>
        {story && (
          <div className="dtree__story" aria-live="polite">
            <b>{story[0]}</b>
            <ol>{story.slice(1).map((line, i) => <li key={i} className={i === story.length - 2 ? 'is-new' : ''}>{line}</li>)}</ol>
          </div>
        )}
        {stats.done && (
          <div className="dtree__reveal" role="status">
            <small>THE MOST LIKELY STORY</small>
            <b>{best.label}</b>
            <span>{(best.p * 100).toFixed(1)}% of the model’s games · fair {odds(fairOdds(best.p))}{best.top ? ` · most likely score ${m.away.abbr} ${best.top.away}–${best.top.home} ${m.home.abbr}` : ''}</span>
          </div>
        )}
      </div>
      <div className="dtree__leaves">
        <h3>Every combination, priced <small>the model’s exact share of its {m.draws.toLocaleString()} stored games, this run’s count, and the fair price · hover to light its path</small></h3>
        <div className="dtree__grid">
          {exact.map((l) => {
            const runP = stats.n ? stats.counts[l.leaf] / stats.n : null
            const rank = ranked.indexOf(l) + 1
            return (
              <button key={l.leaf} type="button" className={`dtree__leaf${hot === l.leaf ? ' is-hot' : ''}${rank === 1 ? ' is-top' : ''}`}
                style={{ ['--lc' as string]: l.group >= 2 ? favColor : dogColor }} onMouseEnter={() => setHot(l.leaf)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(l.leaf)} onBlur={() => setHot(null)}>
                <span className="dtree__rank">#{rank}</span>
                <span className="dtree__lab">{l.label}</span>
                <b>{(l.p * 100).toFixed(1)}%</b>
                <span className="dtree__meta">fair <em>{odds(fairOdds(l.p))}</em> · run {runP == null ? '—' : `${(runP * 100).toFixed(1)}%`}</span>
                <span className="dtree__score">{l.top ? `most likely ${m.away.abbr} ${l.top.away}–${l.top.home} ${m.home.abbr}` : 'no games here'}</span>
                <i><em style={{ width: `${Math.min(100, (l.p / ranked[0].p) * 100)}%` }} /></i>
              </button>
            )
          })}
        </div>
        <p className="dtree__note">Fair prices are what each outcome is worth if the model is right, before any book margin. A combination is a same-game parlay’s true chance: books price those with extra margin, so compare before you take one. Research only; nothing here places a bet.</p>
      </div>
    </section>
  )
}
