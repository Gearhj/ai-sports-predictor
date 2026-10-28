/**
 * THE SEASON TIME MACHINE (Record, plan page list): drag the scrubber (or press play) to rewind AISP's paper season.
 * Every settled bet lands as a particle in the order it settled (up = win, down = loss, size = stake, colour = sport)
 * while the bankroll line redraws to that moment and the record and profit roll with it. From the ledger the page
 * already reads; nothing new is fetched.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { usd } from '../pulse/pulseData'
import './timemachine.css'

export interface TmBet { at: string; sport: string; stake: number; profit: number; label: string }
const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
const W = 1000, H = 260, M = { l: 52, r: 16, t: 14, b: 26 }

export function TimeMachine({ bets, start = 1000 }: { bets: TmBet[]; start?: number }) {
  const rows = useMemo(() => [...bets].filter((b) => Number.isFinite(Date.parse(b.at))).sort((a, b) => Date.parse(a.at) - Date.parse(b.at)), [bets])
  const [i, setI] = useState(rows.length)
  const [playing, setPlaying] = useState(false)
  // Collapsible (owner 10-26); remembered in this browser, open by default.
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('aisp-tm-open') !== '0' } catch { return true } })
  const toggle = () => { setOpen((v) => { try { localStorage.setItem('aisp-tm-open', v ? '0' : '1') } catch { /* private window */ } return !v }); setPlaying(false) }
  const svg = useRef<SVGSVGElement | null>(null)
  useEffect(() => { setI(rows.length) }, [rows.length])
  useEffect(() => {
    if (!playing) return undefined
    const t = window.setInterval(() => setI((n) => { if (n >= rows.length) { setPlaying(false); return n } return n + Math.max(1, Math.round(rows.length / 160)) }), 60)
    return () => window.clearInterval(t)
  }, [playing, rows.length])

  const path = useMemo(() => { let bank = start; return rows.map((r) => { bank += r.profit; return { t: Date.parse(r.at), bank, r } }) }, [rows, start])
  const shown = path.slice(0, Math.min(i, path.length))
  const now = shown[shown.length - 1]
  const wins = shown.filter((p) => p.r.profit > 0).length, losses = shown.filter((p) => p.r.profit < 0).length

  useEffect(() => {
    const el = svg.current
    if (!el || !path.length) return
    const s = d3.select(el); s.selectAll('*').remove()
    const x = d3.scaleTime().domain(d3.extent(path, (p) => p.t) as [number, number]).range([M.l, W - M.r])
    const ext = d3.extent([start, ...path.map((p) => p.bank)]) as [number, number]
    const y = d3.scaleLinear().domain([ext[0] - 5, ext[1] + 5]).nice().range([H - M.b, M.t])
    s.append('g').attr('class', 'tm-axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(6).tickFormat(d3.timeFormat('%b %d') as never))
    s.append('g').attr('class', 'tm-axis').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => `$${d3.format(',')(v as number)}`))
    s.append('line').attr('x1', M.l).attr('x2', W - M.r).attr('y1', y(start)).attr('y2', y(start)).attr('class', 'tm-start')
    if (!shown.length) return
    const area = d3.area<(typeof path)[number]>().x((p) => x(p.t)).y0(y(start)).y1((p) => y(p.bank)).curve(d3.curveStepAfter)
    const line = d3.line<(typeof path)[number]>().x((p) => x(p.t)).y((p) => y(p.bank)).curve(d3.curveStepAfter)
    s.append('path').datum(shown).attr('d', area).attr('class', now.bank >= start ? 'tm-area is-up' : 'tm-area is-down')
    s.append('path').datum(shown).attr('d', line).attr('class', 'tm-line')
    s.append('g').selectAll('circle').data(shown).join('circle')
      .attr('cx', (p) => x(p.t)).attr('cy', (p) => y(p.bank) + (p.r.profit > 0 ? -7 : 7)).attr('r', (p) => Math.max(1.6, Math.min(5, Math.sqrt(p.r.stake))))
      .attr('fill', (p) => COLOR[p.r.sport] ?? '#94a3b8').attr('opacity', (_, k) => (k > shown.length - 12 ? 1 : 0.55))
    s.append('circle').attr('cx', x(now.t)).attr('cy', y(now.bank)).attr('r', 6).attr('class', 'tm-head')
  }, [path, shown.length, start]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!rows.length) return null
  const day = now ? new Date(now.t).toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' }) : 'Start'
  return (
    <section className="sl-card tm" aria-label="Season time machine">
      <header className="tm__head">
        <h2>The Season Time Machine</h2>
        <span className="tm__date">{day}</span>
        <span className="tm__stat"><b>{wins}–{losses}</b> record</span>
        <span className="tm__stat"><b className={(now?.bank ?? start) >= start ? 'is-up' : 'is-down'}>{usd((now?.bank ?? start) - start, true)}</b> paper P/L</span>
        <span className="tm__stat"><b>{usd(now?.bank ?? start)}</b> bankroll</span>
        <button type="button" className="tm__fold" onClick={toggle} aria-expanded={open}>{open ? 'Collapse ▴' : 'Show ▾'}</button>
        {open ? <button type="button" className="tm__play" onClick={() => { if (i >= rows.length) setI(0); setPlaying((v) => !v) }}>{playing ? '⏸ Pause' : i >= rows.length ? '▶ Replay the season' : '▶ Play'}</button> : null}
      </header>
      {open ? <>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="tm__svg" role="img" aria-label={`Bankroll ${usd(now?.bank ?? start)} after ${shown.length} settled bets`} />
      <input className="tm__scrub" type="range" min={0} max={rows.length} value={Math.min(i, rows.length)} onChange={(e) => { setPlaying(false); setI(Number(e.target.value)) }} aria-label="Scrub through the season" />
      <p className="tm__fine">{shown.length} of {rows.length} settled paper bets · each dot is one bet in the order it settled (above the line = win, below = loss, size = stake, colour = sport) · {now ? `latest: ${now.r.label}` : 'drag to begin'}</p>
      </> : null}
    </section>
  )
}
