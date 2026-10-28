/**
 * THE CALIBRATION GALAXY (Performance, plan page list): "when AISP says 70%, does it happen 70% of the time?" as a
 * night sky. Every scored game is a star (bright = the favourite won, dim = it lost) and each 5-point band of
 * confidence collapses into a glowing core at (what it said, what happened). The diagonal is the truth line: a core
 * below it is overconfident. Toggle the market's galaxy on the same games to see who is more honest. Reads the
 * Overview payload the page already loads (same cache key), so nothing new is fetched. NHL is scored in its own block
 * and has no per-game rows here yet.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { bandsOf, honesty, starsOf, verdictOf, type Band, type Source } from './galaxy'
import { useScoredGames } from './useScoredGames'
import './galaxy.css'

const W = 1000, H = 440, M = { l: 58, r: 20, t: 18, b: 40 }
const x = d3.scaleLinear().domain([0.5, 1]).range([M.l, W - M.r])
const y = d3.scaleLinear().domain([0.25, 1]).range([H - M.b, M.t])
type Mode = 'MODEL' | 'MARKET' | 'BOTH'
const TINT: Record<Source, { hit: string; miss: string; core: string }> = {
  MODEL: { hit: '#fde68a', miss: 'rgba(251,113,133,.55)', core: '#fbbf24' },
  MARKET: { hit: '#bfdbfe', miss: 'rgba(148,163,184,.45)', core: '#93c5fd' },
}
const pts = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} pts`

export function CalibrationGalaxy() {
  const res = useScoredGames()
  const games = res.data?.games ?? []
  const [sport, setSport] = useState('ALL')
  const [mode, setMode] = useState<Mode>('MODEL')
  const [focus, setFocus] = useState<Band | null>(null)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('aisp-galaxy-open') !== '0' } catch { return true } })
  const toggle = () => setOpen((v) => { try { localStorage.setItem('aisp-galaxy-open', v ? '0' : '1') } catch { /* private window */ } return !v })
  const sports = useMemo(() => ['ALL', ...Array.from(new Set(games.map((g) => g.sport))).sort()], [games])
  const paired = mode === 'BOTH'
  const model = useMemo(() => bandsOf(starsOf(games, 'MODEL', sport, paired)), [games, sport, paired])
  const market = useMemo(() => bandsOf(starsOf(games, 'MARKET', sport, paired)), [games, sport, paired])
  const shown: [Source, Band[]][] = mode === 'MODEL' ? [['MODEL', model]] : mode === 'MARKET' ? [['MARKET', market]] : [['MARKET', market], ['MODEL', model]]
  const vm = verdictOf(model), vk = verdictOf(bandsOf(starsOf(games, 'MARKET', sport, true))), vmp = verdictOf(bandsOf(starsOf(games, 'MODEL', sport, true)))
  const svg = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const el = svg.current
    if (!el || !open) return
    const s = d3.select(el); s.selectAll('*').remove()
    const defs = s.append('defs')
    for (const src of ['MODEL', 'MARKET'] as Source[]) {
      const g = defs.append('radialGradient').attr('id', `gx-core-${src}`)
      g.append('stop').attr('offset', '0%').attr('stop-color', '#fff').attr('stop-opacity', 0.95)
      g.append('stop').attr('offset', '35%').attr('stop-color', TINT[src].core).attr('stop-opacity', 0.75)
      g.append('stop').attr('offset', '100%').attr('stop-color', TINT[src].core).attr('stop-opacity', 0)
    }
    // Background dust (decorative, seeded).
    let seed = 7; const r = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
    s.append('g').selectAll('circle').data(d3.range(140)).join('circle').attr('class', 'gx-dust')
      .attr('cx', () => M.l + r() * (W - M.l - M.r)).attr('cy', () => M.t + r() * (H - M.t - M.b)).attr('r', () => 0.4 + r() * 0.9)
    // Zones and the truth line.
    s.append('path').attr('class', 'gx-zone gx-zone--over').attr('d', `M${x(0.5)},${y(0.5)} L${x(1)},${y(1)} L${x(1)},${y(0.25)} L${x(0.5)},${y(0.25)} Z`)
    s.append('text').attr('class', 'gx-zone-label').attr('x', x(0.97)).attr('y', y(0.3)).attr('text-anchor', 'end').text('OVERCONFIDENT · said more than happened')
    s.append('text').attr('class', 'gx-zone-label gx-zone-label--under').attr('x', x(0.52)).attr('y', y(0.95)).text('UNDERCONFIDENT · happened more than said')
    s.append('line').attr('class', 'gx-truth').attr('x1', x(0.5)).attr('y1', y(0.5)).attr('x2', x(1)).attr('y2', y(1))
    s.append('text').attr('class', 'gx-truth-label').attr('x', x(0.93)).attr('y', y(0.93) - 8).attr('text-anchor', 'end').text('TRUTH LINE')
    s.append('g').attr('class', 'gx-ax').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(5).tickFormat((d) => `${Math.round(+d * 100)}%`))
    s.append('g').attr('class', 'gx-ax').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(6).tickFormat((d) => `${Math.round(+d * 100)}%`))
    s.append('text').attr('class', 'gx-cap').attr('x', (M.l + W - M.r) / 2).attr('y', H - 6).attr('text-anchor', 'middle').text('WHAT IT SAID — the favourite’s chance')
    s.append('text').attr('class', 'gx-cap').attr('transform', `translate(14,${(M.t + H - M.b) / 2}) rotate(-90)`).attr('text-anchor', 'middle').text('WHAT HAPPENED')
    // Big bang: every star flies from the centre of the sky to its band's spiral.
    const cx = (M.l + W - M.r) / 2, cy = (M.t + H - M.b) / 2
    for (const [src, bands] of shown) {
      const layer = s.append('g')
      for (const b of bands) {
        const bx = x(b.said), by = y(b.happened)
        layer.append('line').attr('class', `gx-miss ${b.happened < b.said ? 'is-over' : 'is-under'}`).attr('x1', bx).attr('y1', by).attr('x2', bx).attr('y2', y(b.said)).attr('opacity', 0)
          .transition().delay(900).duration(500).attr('opacity', 1)
        layer.append('g').selectAll('circle').data(b.stars).join('circle').attr('class', 'gx-star')
          .attr('fill', (d) => (d.hit ? TINT[src].hit : TINT[src].miss)).attr('r', 0).attr('cx', cx).attr('cy', cy)
          .transition().delay((_, i) => i * 1.5).duration(1000).ease(d3.easeCubicOut)
          .attr('r', (d) => (d.hit ? 1.7 : 1.3))
          .attr('cx', (_, i) => bx + Math.cos(i * 2.39996) * 3.1 * Math.sqrt(i + 1))
          .attr('cy', (_, i) => by + Math.sin(i * 2.39996) * 3.1 * Math.sqrt(i + 1))
        const core = layer.append('g').attr('class', 'gx-core').style('cursor', 'pointer').on('mouseenter', () => setFocus(b)).on('click', () => setFocus(b))
        core.append('circle').attr('cx', bx).attr('cy', by).attr('r', 0).attr('fill', `url(#gx-core-${src})`)
          .transition().delay(700).duration(700).attr('r', 12 + Math.sqrt(b.n) * 1.6)
        if (src === 'MODEL' || mode === 'MARKET') {
          core.append('text').attr('class', 'gx-core-label').attr('x', bx).attr('y', by - 16 - Math.sqrt(b.n) * 1.6).attr('text-anchor', 'middle').attr('opacity', 0)
            .text(`${Math.round(b.said * 100)}→${Math.round(b.happened * 100)}`).transition().delay(1100).duration(400).attr('opacity', 1)
        }
      }
    }
  }, [open, mode, sport, model, market]) // eslint-disable-line react-hooks/exhaustive-deps

  const lead = vm.confident
  return (
    <section className="gx" aria-label="The Calibration Galaxy">
      <header className="gx__head">
        <h2>THE CALIBRATION GALAXY</h2>
        <span>When AISP says 70%, does it happen 70% of the time? Every star is a scored game.</span>
        <button type="button" className="gx__fold" aria-expanded={open} onClick={toggle}>{open ? 'Collapse ▴' : 'Open the galaxy ▾'}</button>
      </header>
      {open && (
        <>
          {res.loading && !games.length && <p className="gx__muted">Charting the sky…</p>}
          {res.error && !games.length && <p className="gx__muted">Scored games are not available right now.</p>}
          {games.length > 0 && (
            <>
              <div className="gx__controls">
                <div className="gx__chips" role="group" aria-label="Sport">
                  {sports.map((s) => <button key={s} type="button" aria-pressed={sport === s} onClick={() => { setSport(s); setFocus(null) }}>{s === 'ALL' ? 'All sports' : s}</button>)}
                </div>
                <div className="gx__chips" role="group" aria-label="Whose galaxy">
                  {([['MODEL', 'AISP model'], ['MARKET', 'The market'], ['BOTH', 'Both · same games']] as [Mode, string][]).map(([k, l]) =>
                    <button key={k} type="button" className={`gx__mode gx__mode--${k.toLowerCase()}`} aria-pressed={mode === k} onClick={() => { setMode(k); setFocus(null) }}>{l}</button>)}
                </div>
              </div>
              <p className="gx__verdict">
                {vm.n === 0 ? 'No scored games for this sport yet.' : <>
                  Across <b>{vm.n}</b> games AISP’s model is <b className={`is-${honesty(vm.gap)}`}>{honesty(vm.gap)}</b> ({pts(vm.gap)}).
                  {lead && <> When it was surest ({Math.round(lead.lo * 100)}%+), it said <b>{Math.round(lead.said * 100)}%</b> and the favourite won <b>{Math.round(lead.happened * 100)}%</b> ({lead.n} games).</>}
                  {vk.n > 0 && <> On the {vk.n} games with a recorded market price: model <b className={`is-${honesty(vmp.gap)}`}>{pts(vmp.gap)}</b>, market <b className={`is-${honesty(vk.gap)}`}>{pts(vk.gap)}</b>.</>}
                </>}
              </p>
              <svg ref={svg} className="gx__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Calibration galaxy: stated chance against how often the favourite won" />
              <p className="gx__focus">
                {focus ? <>Band <b>{Math.round(focus.lo * 100)}–{Math.min(100, Math.round(focus.hi * 100))}%</b>: {focus.n} games · said <b>{(focus.said * 100).toFixed(1)}%</b> · happened <b>{(focus.happened * 100).toFixed(1)}%</b> · {focus.happened < focus.said ? 'overconfident' : 'underconfident'} by <b>{Math.abs((focus.happened - focus.said) * 100).toFixed(1)} pts</b>. With {focus.n} games, about ±{Math.round(100 / Math.sqrt(focus.n))} pts is noise.</>
                  : <>Hover a glowing core for its numbers. Bright stars: the favourite won. Dim stars: it lost. A red line drops from a core to the truth line when it said more than happened.</>}
              </p>
            </>
          )}
        </>
      )}
    </section>
  )
}
