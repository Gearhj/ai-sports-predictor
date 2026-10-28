/**
 * THE COMBINE (Shadow Board, plan page list): forecasting prospects run the same four drills on the same priced,
 * scored games — AISP's model, the market, two market-anchored blends (the rule AISP is shadowing) and a home-team
 * baseline. Race bars per drill (crown to the best), a D3 radar of every prospect, and a draft board ordered by log
 * loss. Reads the shared scored-games response; nothing new is fetched.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useScoredGames } from '../galaxy/useScoredGames'
import { DRILLS, PROSPECTS, draftOrder, drillsOf, normalised, pairedOf } from './combine'
import './combine.css'

const COLOR: Record<string, string> = { model: '#fbbf24', market: '#93c5fd', blend50: '#34d399', blend20: '#c084fc', home: '#94a3b8' }
const R = 130, SZ = 340

export function TheCombine() {
  const res = useScoredGames()
  const games = res.data?.games ?? []
  const [sport, setSport] = useState('ALL')
  const [hot, setHot] = useState<string | null>(null)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('aisp-combine-open') !== '0' } catch { return true } })
  const toggle = () => setOpen((v) => { try { localStorage.setItem('aisp-combine-open', v ? '0' : '1') } catch { /* private window */ } return !v })
  const sports = useMemo(() => ['ALL', ...Array.from(new Set(pairedOf(games).map((r) => r.sport))).sort()], [games])
  const rows = useMemo(() => pairedOf(games, sport), [games, sport])
  const results = useMemo(() => PROSPECTS.map((pro) => ({ pro, d: drillsOf(rows, pro) })), [rows])
  const order = draftOrder(results)
  const norm = normalised(results)
  const modelPick = order.findIndex((r) => r.pro.key === 'model') + 1
  const svg = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const el = svg.current
    if (!el || !open || !rows.length) return
    const s = d3.select(el); s.selectAll('*').remove()
    const g = s.append('g').attr('transform', `translate(${SZ / 2},${SZ / 2})`)
    const ang = (i: number) => (Math.PI * 2 * i) / DRILLS.length - Math.PI / 2
    for (const lv of [0.25, 0.5, 0.75, 1]) g.append('polygon').attr('class', 'cb-ring').attr('points', DRILLS.map((_, i) => `${Math.cos(ang(i)) * R * lv},${Math.sin(ang(i)) * R * lv}`).join(' '))
    DRILLS.forEach((dr, i) => {
      g.append('line').attr('class', 'cb-spoke').attr('x2', Math.cos(ang(i)) * R).attr('y2', Math.sin(ang(i)) * R)
      g.append('text').attr('class', 'cb-axis').attr('x', Math.cos(ang(i)) * (R + 18)).attr('y', Math.sin(ang(i)) * (R + 18) + 4).attr('text-anchor', 'middle').text(dr.label)
    })
    for (const r of [...results].reverse()) {
      const pts = DRILLS.map((dr, i) => { const v = 0.12 + 0.88 * (norm[r.pro.key]?.[dr.key] ?? 0); return [Math.cos(ang(i)) * R * v, Math.sin(ang(i)) * R * v] })
      const dim = hot && hot !== r.pro.key
      g.append('polygon').attr('class', 'cb-shape').attr('fill', COLOR[r.pro.key]).attr('stroke', COLOR[r.pro.key])
        .attr('fill-opacity', dim ? 0.02 : hot ? 0.28 : 0.1).attr('stroke-opacity', dim ? 0.2 : 1)
        .attr('points', DRILLS.map(() => '0,0').join(' '))
        .transition().duration(800).ease(d3.easeBackOut).attr('points', pts.map((p) => p.join(',')).join(' '))
    }
  }, [open, rows, results, norm, hot])

  return (
    <section className="cb" aria-label="The Combine">
      <header className="cb__head">
        <h2>THE COMBINE</h2>
        <span>Five forecasters, the same drills, the same {rows.length || '…'} priced games. Who would you draft?</span>
        <button type="button" className="cb__fold" aria-expanded={open} onClick={toggle}>{open ? 'Collapse ▴' : 'Run the drills ▾'}</button>
      </header>
      {open && (
        <>
          {res.loading && !games.length && <p className="cb__muted">Lining up the prospects…</p>}
          {res.error && !games.length && <p className="cb__muted">Scored games are not available right now.</p>}
          {games.length > 0 && (
            <>
              <div className="cb__chips" role="group" aria-label="Sport">
                {sports.map((s) => <button key={s} type="button" aria-pressed={sport === s} onClick={() => setSport(s)}>{s === 'ALL' ? 'All sports' : s}</button>)}
              </div>
              {rows.length === 0 ? <p className="cb__muted">No priced, scored games for this sport yet.</p> : (
                <>
                  <p className="cb__verdict">
                    #1 pick: <b style={{ color: COLOR[order[0].pro.key] }}>{order[0].pro.name}</b>. AISP’s model went <b>#{modelPick}</b>
                    {modelPick > 1 ? <> — behind {order.slice(0, modelPick - 1).map((r) => r.pro.name).join(', ')}.</> : '.'}
                    {' '}Log-loss gaps under about {(0.6 / Math.sqrt(rows.length)).toFixed(3)} on {rows.length} games are within noise.
                  </p>
                  <div className="cb__grid">
                    <div className="cb__drills">
                      {DRILLS.map((dr) => {
                        const best = results.reduce((a, b) => ((dr.better === 'low' ? b.d[dr.key] < a.d[dr.key] : b.d[dr.key] > a.d[dr.key]) ? b : a))
                        return (
                          <div key={dr.key} className="cb__drill">
                            <h3>{dr.label} <small>{dr.help}</small></h3>
                            {results.map((r) => (
                              <div key={r.pro.key} className={`cb__lane${hot && hot !== r.pro.key ? ' is-dim' : ''}`} onMouseEnter={() => setHot(r.pro.key)} onMouseLeave={() => setHot(null)}>
                                <span className="cb__who" style={{ color: COLOR[r.pro.key] }}>{r.pro.name}</span>
                                <i><em style={{ width: `${8 + 92 * (norm[r.pro.key]?.[dr.key] ?? 0)}%`, background: COLOR[r.pro.key] }} /></i>
                                <b>{dr.fmt(r.d[dr.key])}{best.pro.key === r.pro.key && <span className="cb__crown" aria-label="best"> 👑</span>}</b>
                              </div>
                            ))}
                          </div>
                        )
                      })}
                    </div>
                    <div className="cb__side">
                      <svg ref={svg} className="cb__radar" viewBox={`-44 0 ${SZ + 88} ${SZ}`} role="img" aria-label="Radar of every prospect across the four drills (outer = best)" />
                      <ol className="cb__draft">
                        {order.map((r, i) => (
                          <li key={r.pro.key} style={{ animationDelay: `${0.2 + i * 0.18}s`, borderLeftColor: COLOR[r.pro.key] }} onMouseEnter={() => setHot(r.pro.key)} onMouseLeave={() => setHot(null)}>
                            <span className="cb__pick">#{i + 1}</span><b style={{ color: COLOR[r.pro.key] }}>{r.pro.name}</b><small>{r.pro.blurb}</small>
                          </li>
                        ))}
                      </ol>
                    </div>
                  </div>
                  <p className="cb__muted">The blends are the market-anchored rule AISP is shadowing (it bets only when the blended chance beats the price by 2%). Ranked by log loss, the measure the truth report uses. Hover a prospect to highlight it.</p>
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
