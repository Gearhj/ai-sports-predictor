/**
 * FOURTH DOWN / DECISION EDGE. Two halves:
 *  - the DECISION MAP: every fourth down at once, field position across and yards to go down, each square
 *    coloured by the call the league tables favour (go, field goal, punt) and shaded by how clear-cut it is;
 *    the situation on the field is pinned on it.
 *  - the CALL: the three options as big expected-points bars on one scale, the conversion / make odds, the
 *    best option badged, and what the coach actually did.
 * League averages (nflverse 2015-2026) through the same expected-points model as the momentum chart.
 * Expected points, not win probability: late in a close game the right call can differ. Never a wager signal.
 */
import { useMemo, useState, type MouseEvent } from 'react'
import { advise, type FourthTables } from './fourth'
import type { EpGrid } from './ep'

export interface EdgeState { own: number; distance: number; team: string; did: 'go' | 'punt' | 'fg' | null; label: string; mode: 'now' | 'if-stalls' | 'last' }

const fmt = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}`
const COLOR = { go: '#22c55e', fg: '#f59e0b', punt: '#64748b' } as const
const NAME = { go: 'Go for it', fg: 'Field goal', punt: 'Punt' } as const
const MAXD = 15

interface Props { ep: EpGrid | null; tables: FourthTables | null; state: EdgeState | null; isNfl: boolean }

export function DecisionEdge({ ep, tables, state, isNfl }: Props) {
  const advice = useMemo(() => (ep && tables && state ? advise(ep, tables, state.own, state.distance) : null), [ep, tables, state])
  // the whole map: own 1..99 across, distance 1..15 down
  const map = useMemo(() => {
    if (!ep || !tables) return null
    const cells: Array<{ own: number; d: number; key: 'go' | 'fg' | 'punt'; margin: number }> = []
    for (let d = 1; d <= MAXD; d += 1) {
      for (let own = 1; own <= 99; own += 1) {
        if (own + d > 100) continue
        const a = advise(ep, tables, own, d)
        const sorted = [...a.options].sort((x, y) => y.ep - x.ep)
        cells.push({ own, d, key: a.best.key, margin: sorted.length > 1 ? sorted[0].ep - sorted[1].ep : 1 })
      }
    }
    return cells
  }, [ep, tables])
  const W = 640; const H = 250; const L = 34; const T = 14; const PW = W - L - 8; const PH = H - T - 34
  const cw = PW / 99; const ch = PH / MAXD
  const X = (own: number) => L + (own - 1) * cw
  const Y = (d: number) => T + (d - 1) * ch
  const yard = (own: number) => (own === 50 ? '50' : own < 50 ? `OWN ${own}` : `OPP ${100 - own}`)

  const opts = advice ? [...advice.options] : []
  const lo = Math.min(-3, ...opts.map((o) => o.ep)); const hi = Math.max(3, ...opts.map((o) => o.ep))
  const bx = (v: number) => ((v - lo) / (hi - lo)) * 100
  const best = advice?.best ?? null
  const chosen = state?.did ? opts.find((o) => o.key === state.did) : undefined
  const gap = chosen && best ? best.ep - chosen.ep : 0
  const second = advice ? [...advice.options].sort((a, b) => b.ep - a.ep)[1] : undefined
  const verdict = !advice || !best ? null : state?.did == null
    ? (second && best.ep - second.ep < 0.3 ? `Coin flip: ${NAME[best.key].toLowerCase()} by a hair (${(best.ep - second.ep).toFixed(1)} pts).` : `The numbers ${second && best.ep - second.ep > 1 ? 'strongly ' : ''}say ${NAME[best.key].toLowerCase()}${second ? `, worth ${(best.ep - second.ep).toFixed(1)} more points than ${NAME[second.key].toLowerCase()}` : ''}.`)
    : gap < 0.15 ? `Right call: ${state.team} chose ${NAME[state.did].toLowerCase()}, the best option on the board.` : `${state.team} chose ${NAME[state.did].toLowerCase()}; ${NAME[best.key].toLowerCase()} was worth ${gap.toFixed(1)} more expected points.`
  const pinD = state ? Math.min(MAXD, Math.max(1, state.distance)) : null
  const [hv, setHv] = useState<{ own: number; d: number } | null>(null)
  const hvAdvice = useMemo(() => (hv && ep && tables ? advise(ep, tables, hv.own, hv.d) : null), [hv, ep, tables])
  const onMove = (e: MouseEvent<SVGSVGElement>) => { const r = e.currentTarget.getBoundingClientRect(); const x = ((e.clientX - r.left) * W) / r.width; const y = ((e.clientY - r.top) * H) / r.height; const own = Math.floor((x - L) / cw) + 1; const d = Math.floor((y - T) / ch) + 1; if (own >= 1 && own <= 99 && d >= 1 && d <= MAXD && own + d <= 100) setHv({ own, d }); else setHv(null) }

  return (
    <section className="lf-card de" aria-label="Fourth down decision edge">
      <header className="de__head">
        <h3 title={`Expected points from league averages (${tables?.trainedOn.split(':')[0] ?? 'nflverse'}), not win probability: late in a close game the best call can differ.${isNfl ? '' : ' NFL tables applied to college.'} Never a wager signal.`}>FOURTH DOWN / DECISION EDGE</h3>
        {state ? <span className="de__sit"><b>4th &amp; {state.distance}</b> at the {yard(state.own).toLowerCase()} <em>{state.mode === 'if-stalls' ? 'if this snap fails' : state.mode === 'last' ? state.label : 'now'}</em></span> : <span className="lf-muted lf-small">every fourth down, at a glance</span>}
      </header>
      <div className="de__body">
        <div className="de__mapwrap">
          {map ? (
            <svg viewBox={`0 0 ${W} ${H}`} className="de__map" onMouseMove={onMove} onMouseLeave={() => setHv(null)} role="img" aria-label="Fourth-down decision map: the league-favoured call for every field position and distance">
              {map.map((c) => <rect key={`${c.own}-${c.d}`} x={X(c.own)} y={Y(c.d)} width={cw + 0.4} height={ch + 0.4} fill={COLOR[c.key]} opacity={0.28 + Math.min(0.62, c.margin * 0.35)} />)}
              {[10, 20, 30, 40, 50, 60, 70, 80, 90].map((o) => <g key={o}><line x1={X(o) + cw / 2} x2={X(o) + cw / 2} y1={T} y2={T + PH} stroke="rgba(0,0,0,.35)" /><text x={X(o) + cw / 2} y={T + PH + 14} textAnchor="middle" className="de__ax">{o === 50 ? '50' : o < 50 ? o : 100 - o}</text></g>)}
              <text x={L} y={H - 4} className="de__ax de__ax--b">◀ OWN END ZONE</text>
              <text x={W - 8} y={H - 4} textAnchor="end" className="de__ax de__ax--b">OPPONENT END ZONE ▶</text>
              {[1, 5, 10, 15].map((d) => <text key={d} x={L - 6} y={Y(d) + ch / 2 + 4} textAnchor="end" className="de__ax">{d}</text>)}
              <text x={10} y={T + PH / 2} className="de__ax de__ax--b" transform={`rotate(-90 10 ${T + PH / 2})`} textAnchor="middle">YARDS TO GO</text>
              {hv ? <rect x={X(hv.own) - 1} y={Y(hv.d) - 1} width={cw + 2} height={ch + 2} fill="none" stroke="#fff" strokeWidth="2" /> : null}
              {state && pinD ? (
                <g className="de__pin">
                  <line x1={X(state.own) + cw / 2} x2={X(state.own) + cw / 2} y1={T} y2={T + PH} stroke="#fff" strokeOpacity=".7" strokeDasharray="3 3" />
                  <line x1={L} x2={L + PW} y1={Y(pinD) + ch / 2} y2={Y(pinD) + ch / 2} stroke="#fff" strokeOpacity=".7" strokeDasharray="3 3" />
                  <circle cx={X(state.own) + cw / 2} cy={Y(pinD) + ch / 2} r="11" className="de__pinring" />
                  <circle cx={X(state.own) + cw / 2} cy={Y(pinD) + ch / 2} r="5" fill="#fff" />
                  <text x={Math.min(W - 70, X(state.own) + 16)} y={Math.max(T + 12, Y(pinD) - 6)} className="de__pinlbl">{state.mode === 'last' ? 'LAST CALL' : 'HERE'}</text>
                </g>
              ) : null}
            </svg>
          ) : <p className="lf-muted lf-small">Loading the fourth-down tables…</p>}
          <p className="de__hover">{hv && hvAdvice ? <><b>4th &amp; {hv.d} at the {yard(hv.own).toLowerCase()}:</b> {[...hvAdvice.options].sort((a, b) => b.ep - a.ep).map((o, i) => <span key={o.key} className={i === 0 ? 'is-best' : ''} style={{ color: COLOR[o.key] }}>{NAME[o.key]} {fmt(o.ep)}</span>)}</> : <span className="lf-muted">Hover the map to read any fourth down.</span>}</p>
          <div className="de__legend"><span><i style={{ background: COLOR.go }} />Go for it</span><span><i style={{ background: COLOR.fg }} />Field goal</span><span><i style={{ background: COLOR.punt }} />Punt</span><small>darker = clearer call</small></div>
        </div>
        <div className="de__call">
          {advice && best ? (
            <>
              {opts.sort((a, b) => b.ep - a.ep).map((o) => (
                <div key={o.key} className={`de__opt${o === best ? ' is-best' : ''}${state?.did === o.key ? ' is-chosen' : ''}`} style={{ ['--oc' as string]: COLOR[o.key] }}>
                  <span className="de__optname">{o.key === 'fg' ? o.label.replace('-yard field goal', '-yd FG') : NAME[o.key]}{o === best ? <i className="de__badge">BEST</i> : null}{state?.did === o.key ? <i className="de__badge de__badge--chose">CHOSEN</i> : null}</span>
                  <b className="de__ep">{fmt(o.ep)}<small> pts</small></b>
                  <span className="de__track"><u style={{ left: `${Math.min(bx(0), bx(o.ep))}%`, width: `${Math.abs(bx(o.ep) - bx(0))}%` }} /><em style={{ left: `${bx(0)}%` }} /></span>
                  <small className="de__detail">{o.detail}</small>
                </div>
              ))}
              {verdict ? <p className={`de__verdict${chosen && gap >= 0.15 ? ' is-off' : ''}`}>{verdict}</p> : null}
            </>
          ) : <p className="lf-muted lf-small">{ep && tables ? 'Pins the next fourth down on the map (and on third down, "if this snap fails").' : 'Loading…'}</p>}
        </div>
      </div>
    </section>
  )
}
