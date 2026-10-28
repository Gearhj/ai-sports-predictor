/**
 * THE UPSET ATLAS (History, plan page list): every game this season where AISP's model made a favourite and the
 * underdog won. Bubbles across the season (higher and bigger = the surer AISP was), solid when the market had the same
 * favourite (everyone missed it), red-ringed when AISP alone was fooled. Click a bubble to open the game. Beside it:
 * the giant killers, AISP's kryptonite, and how often its favourites really fall at each confidence. Reads the same
 * scored-games response as the Calibration Galaxy.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { gameHref } from '../../services/commandCenter'
import { tally, upsetRates, upsetsOf, type Upset } from './upsets'
import { useScoredGames } from './useScoredGames'
import './galaxy.css'

const W = 1000, H = 330, M = { l: 52, r: 18, t: 16, b: 30 }
const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }

export function UpsetAtlas() {
  const res = useScoredGames()
  const games = res.data?.games ?? []
  const navigate = useNavigate()
  const [sport, setSport] = useState('ALL')
  const [min, setMin] = useState(0.6)
  const [focus, setFocus] = useState<Upset | null>(null)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('aisp-atlas-open') !== '0' } catch { return true } })
  const toggle = () => setOpen((v) => { try { localStorage.setItem('aisp-atlas-open', v ? '0' : '1') } catch { /* private window */ } return !v })
  const sports = useMemo(() => ['ALL', ...Array.from(new Set(games.map((g) => g.sport))).sort()], [games])
  const { upsets, eligible } = useMemo(() => upsetsOf(games, min, sport), [games, min, sport])
  const killers = useMemo(() => tally(upsets, 'winner'), [upsets])
  const krypto = useMemo(() => tally(upsets, 'loser'), [upsets])
  const rates = useMemo(() => upsetRates(games, sport), [games, sport])
  const alone = upsets.filter((u) => u.alone).length
  const svg = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const el = svg.current
    if (!el || !open) return
    const s = d3.select(el); s.selectAll('*').remove()
    const rows = upsets.filter((u) => u.at && Number.isFinite(Date.parse(u.at)))
    if (!rows.length) return
    const ext = d3.extent(rows, (u) => Date.parse(u.at!)) as [number, number]
    const x = d3.scaleTime().domain([ext[0] - 86400000, ext[1] + 86400000]).range([M.l, W - M.r])
    const y = d3.scaleLinear().domain([min, Math.max(0.9, d3.max(rows, (u) => u.said) ?? 0.9)]).range([H - M.b, M.t + 10]).nice()
    s.append('g').attr('class', 'gx-ax').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(6).tickFormat((d) => d3.timeFormat('%b %-d')(d as Date)))
    s.append('g').attr('class', 'gx-ax').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((d) => `${Math.round(+d * 100)}%`))
    s.append('text').attr('class', 'gx-cap').attr('transform', `translate(14,${(M.t + H - M.b) / 2}) rotate(-90)`).attr('text-anchor', 'middle').text('HOW SURE AISP WAS')
    // Shockwaves: the surest upsets pulse.
    const g = s.append('g')
    const sim = d3.forceSimulation(rows.map((u) => ({ u, x: x(Date.parse(u.at!)), y: y(u.said), r: 4 + (u.said - 0.5) * 34 })) as (d3.SimulationNodeDatum & { u: Upset; r: number })[])
      .force('x', d3.forceX((d: any) => x(Date.parse(d.u.at))).strength(0.9))
      .force('y', d3.forceY((d: any) => y(d.u.said)).strength(0.9))
      .force('c', d3.forceCollide((d: any) => d.r + 1)).stop()
    for (let i = 0; i < 160; i++) sim.tick()
    const nodes = sim.nodes()
    const dot = g.selectAll('g').data(nodes).join('g').attr('class', (d) => `ua-bub${d.u.alone ? ' is-alone' : ''}`).attr('transform', (d) => `translate(${d.x},${d.y})`).style('cursor', 'pointer')
      .on('mouseenter', (_, d) => setFocus(d.u)).on('click', (_, d) => navigate(gameHref(d.u.sport, d.u.gameId)))
    dot.filter((d) => d.u.said >= 0.75).append('circle').attr('class', 'ua-wave').attr('r', (d) => d.r).attr('stroke', (d) => COLOR[d.u.sport] ?? '#e2e8f0')
    dot.append('circle').attr('r', 0).attr('fill', (d) => COLOR[d.u.sport] ?? '#e2e8f0').attr('fill-opacity', (d) => (d.u.alone ? 0.25 : 0.8))
      .attr('stroke', (d) => (d.u.alone ? '#fb7185' : 'rgba(7,13,24,.8)')).attr('stroke-width', (d) => (d.u.alone ? 2 : 1))
      .transition().delay((_, i) => i * 12).duration(500).ease(d3.easeBackOut).attr('r', (d) => d.r)
  }, [open, upsets, min, navigate])

  return (
    <section className="gx ua" aria-label="The Upset Atlas">
      <header className="gx__head">
        <h2>THE UPSET ATLAS</h2>
        <span>Every time AISP’s favourite went down this season — and whether the market saw it coming.</span>
        <button type="button" className="gx__fold" aria-expanded={open} onClick={toggle}>{open ? 'Collapse ▴' : 'Open the atlas ▾'}</button>
      </header>
      {open && (
        <>
          {res.loading && !games.length && <p className="gx__muted">Plotting the upsets…</p>}
          {res.error && !games.length && <p className="gx__muted">Scored games are not available right now.</p>}
          {games.length > 0 && (
            <>
              <div className="gx__controls">
                <div className="gx__chips" role="group" aria-label="Sport">
                  {sports.map((s) => <button key={s} type="button" aria-pressed={sport === s} onClick={() => { setSport(s); setFocus(null) }}>{s === 'ALL' ? 'All sports' : s}</button>)}
                </div>
                <div className="gx__chips" role="group" aria-label="How sure">
                  {[0.6, 0.65, 0.7, 0.75].map((v) => <button key={v} type="button" aria-pressed={min === v} onClick={() => { setMin(v); setFocus(null) }}>AISP {Math.round(v * 100)}%+ sure</button>)}
                </div>
              </div>
              <p className="gx__verdict">
                <b>{upsets.length}</b> upsets in <b>{eligible}</b> games where AISP was {Math.round(min * 100)}%+ sure ({eligible ? Math.round((upsets.length / eligible) * 100) : 0}%).
                {' '}<b className="is-overconfident">{alone}</b> fooled AISP alone (the market had the other side); the rest fooled everyone.
              </p>
              <div className="ua__grid">
                <svg ref={svg} className="gx__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Upsets across the season by how sure AISP was" />
                <aside className="ua__side">
                  <h3>GIANT KILLERS <small>won as AISP’s underdog</small></h3>
                  <ol>{killers.map((t) => <li key={`${t.sport}${t.team}`}><i style={{ background: COLOR[t.sport] }} />{t.team}<small>{t.sport}</small><b>{t.n}</b></li>)}</ol>
                  <h3>AISP’S KRYPTONITE <small>lost as AISP’s favourite</small></h3>
                  <ol>{krypto.map((t) => <li key={`${t.sport}${t.team}`}><i style={{ background: COLOR[t.sport] }} />{t.team}<small>{t.sport}</small><b>{t.n}</b></li>)}</ol>
                </aside>
              </div>
              <div className="ua__rates">
                {rates.map((r) => (
                  <div key={r.lo} className="ua__rate">
                    <small>AISP {Math.round(r.lo * 100)}–{r.hi > 1 ? '100' : Math.round(r.hi * 100)}% sure</small>
                    <b>{r.n ? Math.round((r.upsets / r.n) * 100) : 0}%</b>
                    <span>upset · should be ≈{Math.round((1 - (r.lo + Math.min(r.hi, 0.95)) / 2) * 100)}% · {r.upsets} of {r.n}</span>
                  </div>
                ))}
              </div>
              <p className="gx__focus">
                {focus ? <><b>{focus.winner}</b> beat {focus.loser} ({focus.sport}{focus.at ? `, ${new Date(focus.at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : ''}). AISP had {focus.loser} at <b>{Math.round(focus.said * 100)}%</b>{focus.marketSaid != null ? <>; the market had them at <b>{Math.round(focus.marketSaid * 100)}%</b>{focus.alone ? ' — AISP alone was fooled' : ''}</> : '; no market price was recorded'}. Click to open the game.</>
                  : <>Hover a bubble for the story; click it to open the game. Solid = the market missed it too. Red ring = AISP alone was fooled.</>}
              </p>
            </>
          )}
        </>
      )}
    </section>
  )
}
