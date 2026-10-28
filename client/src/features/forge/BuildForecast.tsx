/**
 * BUILD A FORECAST (Methodology, plan page list): the methodology as something you do. Stack the inputs a forecast is
 * made of (team strength, home advantage, rest, a key player out) and watch the home win chance assemble step by step
 * on a D3 waterfall; then enter the book's two prices and AISP's real moneyline gates run in order and stamp the
 * verdict: BET (with the quarter-Kelly stake), PASS, or RECORD ONLY. Teaching numbers; nothing is saved or sent.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { SPORTS, SPREAD, decide, stepsOf, type Build, type Sport } from './forge'
import './forge.css'

const W = 640, H = 260, M = { l: 44, r: 12, t: 14, b: 54 }
const am = (v: number) => (v > 0 ? `+${v}` : `${v}`)

export function BuildForecast() {
  const [b, setB] = useState<Build>({ sport: 'NFL', strength: 3, homeField: true, rest: 0, homeOut: false, awayOut: false })
  const [homePrice, setHomePrice] = useState(-150)
  const [awayPrice, setAwayPrice] = useState(130)
  const S = SPREAD[b.sport]
  const steps = useMemo(() => stepsOf(b), [b])
  const pHome = steps[steps.length - 1].p
  const d = useMemo(() => decide(b.sport, pHome, homePrice, awayPrice), [b.sport, pHome, homePrice, awayPrice])
  const svg = useRef<SVGSVGElement | null>(null)
  const run = `${b.sport}|${pHome.toFixed(4)}|${homePrice}|${awayPrice}`
  const set = (patch: Partial<Build>) => setB((v) => ({ ...v, ...patch }))
  const pickSport = (sport: Sport) => setB({ sport, strength: Math.round(SPREAD[sport].rating / 5 * 10) / 10, homeField: true, rest: 0, homeOut: false, awayOut: false })

  useEffect(() => {
    const el = svg.current
    if (!el) return
    const s = d3.select(el); s.selectAll('*').remove()
    const x = d3.scaleBand().domain(steps.map((t) => t.key).concat('final')).range([M.l, W - M.r]).padding(0.28)
    const y = d3.scaleLinear().domain([0, 1]).range([H - M.b, M.t])
    s.append('g').attr('class', 'fg-ax').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat((v) => `${Math.round(+v * 100)}%`))
    s.append('line').attr('class', 'fg-half').attr('x1', M.l).attr('x2', W - M.r).attr('y1', y(0.5)).attr('y2', y(0.5))
    steps.forEach((t, i) => {
      const prev = i === 0 ? 0.5 : steps[i - 1].p
      const lo = Math.min(prev, t.p), hi = Math.max(prev, t.p)
      const bar = s.append('rect').attr('class', `fg-bar ${t.p >= prev ? 'is-up' : 'is-down'}${i === 0 ? ' is-start' : ''}`).attr('x', x(t.key)!).attr('width', x.bandwidth()).attr('rx', 4)
        .attr('y', y(prev)).attr('height', 0)
      bar.transition().delay(i * 160).duration(380).attr('y', y(hi)).attr('height', Math.max(i === 0 ? 3 : 1.5, y(lo) - y(hi)))
      if (i > 0) s.append('line').attr('class', 'fg-link').attr('x1', x(steps[i - 1].key)! + x.bandwidth()).attr('x2', x(t.key)!).attr('y1', y(prev)).attr('y2', y(prev))
      s.append('text').attr('class', 'fg-lab').attr('x', x(t.key)! + x.bandwidth() / 2).attr('y', H - M.b + 14).attr('text-anchor', 'middle').text(t.short)
      if (i > 0 && Math.abs(t.p - prev) > 0.0005) s.append('text').attr('class', 'fg-delta').attr('x', x(t.key)! + x.bandwidth() / 2).attr('y', y(hi) - 5).attr('text-anchor', 'middle').attr('opacity', 0)
        .text(`${t.p >= prev ? '+' : '−'}${Math.abs((t.p - prev) * 100).toFixed(1)}`).transition().delay(i * 160 + 300).attr('opacity', 1)
    })
    const fin = s.append('rect').attr('class', 'fg-bar is-final').attr('x', x('final')!).attr('width', x.bandwidth()).attr('rx', 4).attr('y', y(0)).attr('height', 0)
    fin.transition().delay(steps.length * 160).duration(500).ease(d3.easeBackOut).attr('y', y(pHome)).attr('height', y(0) - y(pHome))
    s.append('text').attr('class', 'fg-lab fg-lab--final').attr('x', x('final')! + x.bandwidth() / 2).attr('y', H - M.b + 14).attr('text-anchor', 'middle').text('YOUR NUMBER')
    s.append('text').attr('class', 'fg-final').attr('x', x('final')! + x.bandwidth() / 2).attr('y', y(pHome) - 6).attr('text-anchor', 'middle').text(`${(pHome * 100).toFixed(1)}%`)
    s.append('text').attr('class', 'fg-cap').attr('x', M.l).attr('y', H - 8).text('HOME WIN CHANCE, BUILT ONE INPUT AT A TIME')
  }, [steps, pHome])

  return (
    <section className="fg" aria-label="Build a Forecast">
      <header className="fg__head">
        <h2>BUILD A FORECAST</h2>
        <span>Make the number yourself, then run it through AISP’s real betting gates.</span>
      </header>
      <div className="fg__chips" role="group" aria-label="Sport">
        {SPORTS.map((s) => <button key={s} type="button" aria-pressed={b.sport === s} onClick={() => pickSport(s)}>{s}</button>)}
      </div>
      <div className="fg__body">
        <div className="fg__inputs">
          <h3>1 · The inputs <small>in {S.unit} of margin</small></h3>
          <label className="fg__slider"><span>Team strength (home minus away) <em>{b.strength > 0 ? '+' : ''}{b.strength} {S.unit}</em></span>
            <input type="range" min={-S.rating} max={S.rating} step={S.rating / 40} value={b.strength} onChange={(e) => set({ strength: Math.round(+e.target.value * 100) / 100 })} />
            <small>From power ratings: how much better the home team is on a neutral field.</small></label>
          <label className="fg__check"><input type="checkbox" checked={b.homeField} onChange={(e) => set({ homeField: e.target.checked })} /> Home advantage <small>+{S.home} {S.unit}</small></label>
          <label className="fg__slider"><span>Rest & travel <em>{b.rest > 0 ? '+' : ''}{b.rest} {S.unit}</em></span>
            <input type="range" min={-S.out / 2} max={S.out / 2} step={S.out / 20} value={b.rest} onChange={(e) => set({ rest: Math.round(+e.target.value * 100) / 100 })} />
            <small>Back-to-backs, short weeks, long flights.</small></label>
          <label className="fg__check"><input type="checkbox" checked={b.homeOut} onChange={(e) => set({ homeOut: e.target.checked })} /> Home {S.outLabel} out <small>−{S.out} {S.unit}</small></label>
          <label className="fg__check"><input type="checkbox" checked={b.awayOut} onChange={(e) => set({ awayOut: e.target.checked })} /> Away {S.outLabel} out <small>+{S.out} {S.unit}</small></label>
        </div>
        <svg ref={svg} className="fg__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Waterfall: the home win chance builds to ${(pHome * 100).toFixed(1)}%`} />
      </div>
      <div className="fg__body fg__body--decide">
        <div className="fg__inputs">
          <h3>2 · The book’s prices <small>moneyline, US book</small></h3>
          <label className="fg__price">Home <input type="number" step={5} value={homePrice} onChange={(e) => setHomePrice(Number(e.target.value) || -110)} /></label>
          <label className="fg__price">Away <input type="number" step={5} value={awayPrice} onChange={(e) => setAwayPrice(Number(e.target.value) || -110)} /></label>
          <p className="fg__muted">Remove the book’s cut and the market says home <b>{((d.side === 'HOME' ? d.market : 1 - d.market) * 100).toFixed(1)}%</b>. Your number likes the <b>{d.side === 'HOME' ? 'home' : 'away'}</b> side at {am(d.price)}.</p>
        </div>
        <div className="fg__gates" key={run}>
          <h3>3 · AISP’s gates, in order</h3>
          <ol>
            {d.gates.map((g, i) => (
              <li key={g.key} className={g.pass ? 'is-pass' : 'is-fail'} style={{ animationDelay: `${0.15 + i * 0.35}s` }}>
                <i>{g.pass ? '✓' : '✕'}</i><b>{g.label}</b><small>{g.detail}</small>
              </li>
            ))}
          </ol>
          <div className={`fg__stamp fg__stamp--${d.verdict.toLowerCase()}`} style={{ animationDelay: `${0.25 + d.gates.length * 0.35}s` }}>
            {d.verdict === 'BET' ? `BET $${d.stake.toFixed(2)}` : d.verdict === 'PASS' ? 'PASS' : 'RECORD ONLY'}
          </div>
          <p className="fg__muted">{d.verdict === 'BET' ? 'AISP would record this paper position. It never places a real bet; you decide at your own book.'
            : d.verdict === 'PASS' ? 'No bet: the price already says what your number says, or the stake would be under $1.'
              : 'AISP writes the decision down and scores it later, with no stake, until the sport or the gap earns trust.'}</p>
        </div>
      </div>
    </section>
  )
}
