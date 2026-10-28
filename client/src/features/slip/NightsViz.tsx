/**
 * My Picks visuals (owner 10-26: "the graphics are basic … should be modern, eye-catching graphics like the rest of
 * the site"). Two pictures of the same 10,000 simulated nights:
 *
 *   NightGrid    1,000 glowing tiles, each one sampled night (every tenth), lit in the order the nights ran. Colour = how that night
 *                ended (rose = behind, amber = about even, emerald = ahead), so a one-pick slip reads as "62 of every
 *                100 nights light green" and a multi-pick slip shows the whole spread. A glowing ridge of profit sits
 *                underneath with the break-even line.
 *   SeasonPaths  play this slip every night for a 60-night season, 60 times: every season is a thin glowing line,
 *                the middle 80% is a band and the median season is gold. Replaces the Galton Board.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SimResult } from '../flow/slip'
import './nights.css'

const money = (v: number) => `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(2)}`
const TILES = 1000, COLS = 50

/** Tile colour for a night's profit on a diverging scale around break-even. */
function tileColor(v: number, lo: number, hi: number): string {
  if (Math.abs(v) < 0.005) return '#fbbf24'
  return v > 0 ? d3.interpolateRgb('#10b981', '#6ee7b7')(Math.min(1, v / (hi || 1))) : d3.interpolateRgb('#e11d48', '#fb7185')(Math.min(1, v / (lo || -1)))
}

export function NightGrid({ sim, shown }: { sim: SimResult; shown: number }) {
  const W = 900, cell = (W - 20) / COLS, rows = TILES / COLS, GH = rows * cell + 10
  const RH = 120, H = GH + RH + 26
  // each tile = one night (every tenth), in run order, so the lit share matches the 10,000 and fills as they run
  const tiles = useMemo(() => Array.from({ length: TILES }, (_, t) => sim.pl[t * 10] ?? 0), [sim])
  const outcomes = useMemo(() => {
    const m = new Map<number, number>()
    for (let i = 0; i < sim.n; i++) { const v = Math.round(sim.pl[i] * 100) / 100; m.set(v, (m.get(v) ?? 0) + 1) }
    return m.size <= 6 ? [...m.entries()].sort((a, b) => b[0] - a[0]) : null
  }, [sim])
  const lit = Math.floor((shown / sim.n) * TILES)
  const ahead = tiles.slice(0, Math.max(1, lit)).filter((v) => v > 0.005).length
  const ridge = useMemo(() => {
    const lo = sim.worst, hi = sim.best
    if (hi - lo < 0.01 || outcomes) return null
    const bins = d3.bin().domain([lo, hi]).thresholds(36)(Array.from(sim.pl))
    return { lo, hi, bins, max: d3.max(bins, (b) => b.length) ?? 1 }
  }, [sim, outcomes])
  const ref = useRef<SVGGElement | null>(null)
  useEffect(() => {
    if (!ref.current || !ridge) return
    const x = d3.scaleLinear().domain([ridge.lo, ridge.hi]).range([10, W - 10])
    const y = d3.scaleLinear().domain([0, ridge.max]).range([GH + RH, GH + 14])
    const g = d3.select(ref.current); g.selectAll('*').remove()
    const area = d3.area<d3.Bin<number, number>>().x((b) => x(((b.x0 ?? 0) + (b.x1 ?? 0)) / 2)).y0(GH + RH).y1((b) => y(b.length)).curve(d3.curveCatmullRom)
    g.append('path').datum(ridge.bins).attr('d', area).attr('fill', 'url(#nv-ridge)').attr('stroke', 'url(#nv-ridge-line)').attr('stroke-width', 2).attr('class', 'nv-ridge')
    if (ridge.lo < 0 && ridge.hi > 0) {
      g.append('line').attr('x1', x(0)).attr('x2', x(0)).attr('y1', GH + 8).attr('y2', GH + RH).attr('class', 'nv-even')
      g.append('text').attr('x', x(0)).attr('y', GH + RH + 16).attr('text-anchor', 'middle').attr('class', 'nv-ax nv-ax--even').text('break even')
    }
    g.append('line').attr('x1', x(sim.p50)).attr('x2', x(sim.p50)).attr('y1', GH + 14).attr('y2', GH + RH).attr('class', 'nv-med')
    for (const v of [ridge.lo, ridge.hi]) g.append('text').attr('x', x(v)).attr('y', GH + RH + 16).attr('text-anchor', v === ridge.lo ? 'start' : 'end').attr('class', 'nv-ax').text(money(v))
    // the ridge's gradient follows the diverging colours along the profit axis
    const grad = d3.select(ref.current.ownerSVGElement).select('#nv-ridge-line')
    grad.selectAll('stop').remove()
    const zero = Math.max(0, Math.min(1, (0 - ridge.lo) / (ridge.hi - ridge.lo)))
    grad.append('stop').attr('offset', 0).attr('stop-color', '#fb7185'); grad.append('stop').attr('offset', zero).attr('stop-color', '#fbbf24'); grad.append('stop').attr('offset', 1).attr('stop-color', '#34d399')
  }, [ridge, sim, GH])
  return (
    <>
    <svg viewBox={`0 0 ${W} ${ridge ? H : GH + 4}`} className="nv-grid" role="img" aria-label={`Ten thousand simulated nights as a thousand tiles: ${Math.round((ahead / Math.max(1, Math.min(lit, TILES))) * 100)}% of nights finish ahead so far`}>
      <defs>
        <filter id="nv-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <linearGradient id="nv-ridge" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#a78bfa" stopOpacity=".55" /><stop offset="1" stopColor="#a78bfa" stopOpacity="0" /></linearGradient>
        <linearGradient id="nv-ridge-line" x1="0" x2="1" y1="0" y2="0" />
      </defs>
      <g filter="url(#nv-glow)">
        {tiles.map((v, i) => {
          const on = i < lit
          return <rect key={i} x={10 + (i % COLS) * cell + 1} y={5 + Math.floor(i / COLS) * cell + 1} width={cell - 2} height={cell - 2} rx={2.5}
            fill={on ? tileColor(v, sim.worst, sim.best) : '#16223a'} opacity={on ? 0.95 : 0.6} className={on && i > lit - 30 ? 'nv-tile is-fresh' : 'nv-tile'} />
        })}
      </g>
      <g ref={ref} />
    </svg>
    {outcomes && (
      <div className="nv-legend">
        {outcomes.map(([v, c]) => <span key={v}><i style={{ background: tileColor(v, sim.worst, sim.best) }} /><b>{money(v)}</b><small>{((c / sim.n) * 100).toFixed(1)}% of nights</small></span>)}
      </div>
    )}
    </>
  )
}

/** Play the slip every night for SEASON nights, SEASONS times; every season is a path of the running total. */
export function SeasonPaths({ model, market }: { model: SimResult; market: SimResult }) {
  const [mode, setMode] = useState<'model' | 'market'>('model')
  const [seed, setSeed] = useState(11)
  const sim = mode === 'model' ? model : market
  const SEASON = 60, SEASONS = 60, W = 900, H = 300, M = { l: 72, r: 118, t: 16, b: 28 }
  const paths = useMemo(() => {
    let s = seed >>> 0
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
    return Array.from({ length: SEASONS }, () => { let t = 0; const p = [0]; for (let k = 0; k < SEASON; k++) { t += sim.pl[Math.floor(rnd() * sim.n)]; p.push(t) } return p })
  }, [sim, seed])
  const finals = paths.map((p) => p[SEASON]).sort((a, b) => a - b)
  const up = finals.filter((v) => v > 0).length
  const ref = useRef<SVGSVGElement | null>(null)
  useEffect(() => {
    if (!ref.current) return
    const all = paths.flat()
    const x = d3.scaleLinear().domain([0, SEASON]).range([M.l, W - M.r])
    const y = d3.scaleLinear().domain(d3.extent(all) as [number, number]).nice().range([H - M.b, M.t])
    const s = d3.select(ref.current); s.selectAll('*').remove()
    const defs = s.append('defs')
    defs.append('filter').attr('id', 'nv-line-glow').append('feGaussianBlur').attr('stdDeviation', 1.6)
    s.append('g').attr('class', 'nv-ax').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => `${+v < 0 ? '−' : +v > 0 ? '+' : ''}$${Math.abs(+v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`))
    s.append('g').attr('class', 'nv-ax').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(6).tickFormat((v) => (+v === 0 ? 'start' : `night ${v}`)))
    s.append('line').attr('class', 'nv-even').attr('x1', M.l).attr('x2', W - M.r).attr('y1', y(0)).attr('y2', y(0))
    // the middle-80% band and the median season
    const q = (k: number, p: number) => { const col = paths.map((r) => r[k]).sort((a, b) => a - b); return col[Math.round(p * (col.length - 1))] }
    const band = d3.area<number>().x((k) => x(k)).y0((k) => y(q(k, 0.1))).y1((k) => y(q(k, 0.9))).curve(d3.curveMonotoneX)
    s.append('path').datum(d3.range(SEASON + 1)).attr('d', band).attr('class', 'nv-band')
    const line = d3.line<number>().x((_, k) => x(k)).y((v) => y(v)).curve(d3.curveMonotoneX)
    const g = s.append('g')
    paths.forEach((p, i) => {
      const end = p[SEASON]
      const path = g.append('path').datum(p).attr('d', line).attr('class', 'nv-path').attr('stroke', end > 0 ? '#34d399' : '#fb7185')
      const len = (path.node() as SVGPathElement).getTotalLength()
      path.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len).transition().delay(i * 18).duration(1100).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
    })
    const med = d3.range(SEASON + 1).map((k) => q(k, 0.5))
    s.append('path').datum(med).attr('d', line).attr('class', 'nv-median').attr('opacity', 0).transition().delay(1300).duration(500).attr('opacity', 1)
    s.append('text').attr('class', 'nv-medlab').attr('x', W - M.r + 6).attr('y', y(med[SEASON]) + 4).text(`median ${money(med[SEASON])}`).attr('opacity', 0).transition().delay(1500).attr('opacity', 1)
  }, [paths]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="sl-card nv-seasons" aria-label="Sixty seasons">
      <header className="sl-card__head">
        <h2>🎢 60 Seasons</h2><small className="rc-sub">play this slip every night for {SEASON} nights, sixty times over</small>
        <span className="sl-wtabs sp-mode">
          <button type="button" className={mode === 'model' ? 'is-on' : ''} onClick={() => setMode('model')}>If AISP is right</button>
          <button type="button" className={mode === 'market' ? 'is-on' : ''} onClick={() => setMode('market')}>If the market is right</button>
          <button type="button" onClick={() => setSeed((v) => v + 1)}>↻ New seasons</button>
        </span>
      </header>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="nv-paths" role="img" aria-label={`${up} of ${SEASONS} simulated ${SEASON}-night seasons finish ahead`} />
      <p className="nv-foot"><b className={up >= SEASONS / 2 ? 'is-up' : 'is-down'}>{up} of {SEASONS}</b> seasons finish ahead · middle 80% end between <b>{money(finals[Math.round(0.1 * (SEASONS - 1))])}</b> and <b>{money(finals[Math.round(0.9 * (SEASONS - 1))])}</b> · gold = the median season. Play money: a picture of luck and edge, not a wager signal.</p>
    </section>
  )
}
