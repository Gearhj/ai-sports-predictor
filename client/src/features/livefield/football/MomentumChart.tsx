/**
 * The momentum engine: expected points added (EPA) for both offenses, play by play.
 * Top: each team's running total of points its offense has added beyond what the situation was worth.
 * Bottom: every snap as a bar (up = the offense helped itself, down = it hurt itself).
 * It redraws with every play. Display only, never a wager signal.
 */
import { useMemo, useState } from 'react'
import { visibleColor, type FbTeam } from './footballData'
import type { EpaPlay } from './ep'

const W = 1000
const TOP = { y0: 26, h: 200 }
const BAR = { y0: 250, h: 74 }

const fmt = (v: number, d = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}`

interface Props { epa: EpaPlay[]; away: FbTeam; home: FbTeam; shownId: string | null; live: boolean; note: string; wpaById?: Map<string, number>; onPick?: (id: string) => void; highlight?: Set<string> | null }

export function MomentumChart({ epa, away, home, shownId, live, note, wpaById, onPick, highlight = null }: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const calc = useMemo(() => {
    const n = epa.length
    let a = 0
    let h = 0
    const cumA: number[] = []
    const cumH: number[] = []
    epa.forEach((p) => { if (p.team === away.id) a += p.epa; else h += p.epa; cumA.push(a); cumH.push(h) })
    const maxAbs = Math.max(6, ...cumA.map(Math.abs), ...cumH.map(Math.abs))
    const barMax = Math.max(3, ...epa.map((p) => Math.abs(p.epa)))
    const x = (i: number) => 40 + (n <= 1 ? 0 : (i / (n - 1)) * (W - 80))
    const yTop = (c: number) => TOP.y0 + TOP.h / 2 - (c / maxAbs) * (TOP.h / 2 - 8)
    const shownAt = Math.max(0, shownId ? epa.findIndex((p) => p.id === shownId) : n - 1)
    const stat = (team: FbTeam) => {
      const mine = epa.filter((p) => p.team === team.id)
      const total = mine.reduce((s, p) => s + p.epa, 0)
      return { n: mine.length, total, per: mine.length ? total / mine.length : 0, success: mine.length ? mine.filter((p) => p.epa > 0).length / mine.length : 0, boom: mine.filter((p) => p.epa >= 2).length }
    }
    const quarters: Array<{ i: number; q: number }> = []
    let last: number | null = null
    epa.forEach((p, i) => { if (p.period != null && p.period !== last) { quarters.push({ i, q: p.period }); last = p.period } })
    return { n, cumA, cumH, maxAbs, barMax, x, yTop, shownAt, sA: stat(away), sH: stat(home), quarters }
  }, [epa, away, home, shownId])

  const ca = visibleColor(away, '#3fd8c4')
  const ch = visibleColor(home, '#ffb547')
  if (calc.n < 2) {
    return (
      <section className="lf-card fb-mom" aria-label="Momentum">
        <h3>Who is winning the snaps?</h3>
        <p className="lf-muted lf-small">The chart starts after the first few scrimmage plays.</p>
      </section>
    )
  }
  const path = (vals: number[]) => vals.map((v, i) => `${i ? 'L' : 'M'} ${calc.x(i).toFixed(1)} ${calc.yTop(v).toFixed(1)}`).join(' ')
  const area = (vals: number[]) => `${path(vals)} L ${calc.x(calc.n - 1).toFixed(1)} ${calc.yTop(0).toFixed(1)} L ${calc.x(0).toFixed(1)} ${calc.yTop(0).toFixed(1)} Z`
  const barW = Math.max(2, Math.min(14, ((W - 80) / calc.n) * 0.72))
  const shown = epa[calc.shownAt]
  const shownTeam = shown.team === away.id ? away : home
  const sc = shown.team === away.id ? calc.cumA[calc.shownAt] : calc.cumH[calc.shownAt]
  const lineColor = shown.team === away.id ? ca : ch
  const prevSame = (() => { for (let i = calc.shownAt - 1; i >= 0; i -= 1) if (epa[i].team === shown.team) return i; return -1 })()

  return (
    <section className="lf-card fb-mom" aria-label="Momentum">
      <header className="fb-mom__head">
        <div>
          <h3>Who is winning the snaps? {live ? <span className="lf-pill lf-pill--test">LIVE</span> : null}</h3>
          <p className="lf-muted lf-small">Each offense's running total of expected points added. Up means it is beating what the situation is worth.</p>
        </div>
        <div className="fb-mom__teams">
          {[{ t: away, c: ca, s: calc.sA }, { t: home, c: ch, s: calc.sH }].map(({ t, c, s }) => (
            <div key={t.id} className="fb-mom__team" style={{ ['--tc' as string]: c }}>
              <b>{t.abbr}</b>
              <strong className={s.total >= 0 ? 'is-up' : 'is-down'}>{fmt(s.total)}</strong>
              <small>{fmt(s.per, 2)} per play · {Math.round(s.success * 100)}% of snaps helped · {s.boom} big</small>
            </div>
          ))}
        </div>
      </header>
      <div className="fb-mom__plot">
      <svg className="fb-mom__svg" viewBox={`0 0 ${W} 340`} role="img" aria-label={`Running expected points added: ${away.abbr} ${fmt(calc.sA.total)}, ${home.abbr} ${fmt(calc.sH.total)}`}>
        <defs>
          <linearGradient id="fbm-a" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={ca} stopOpacity=".45" /><stop offset="1" stopColor={ca} stopOpacity=".02" /></linearGradient>
          <linearGradient id="fbm-h" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={ch} stopOpacity=".45" /><stop offset="1" stopColor={ch} stopOpacity=".02" /></linearGradient>
        </defs>
        {[-1, 0, 1].map((k) => (
          <g key={k}>
            <line x1="40" x2={W - 40} y1={calc.yTop((k * calc.maxAbs) / 1.0)} y2={calc.yTop((k * calc.maxAbs) / 1.0)} stroke="rgba(255,255,255,.1)" strokeWidth={k === 0 ? 2 : 1} />
            <text x="34" y={calc.yTop(k * calc.maxAbs) + 4} textAnchor="end" className="fb-mom__tick">{k === 0 ? '0' : fmt(k * calc.maxAbs, 0)}</text>
          </g>
        ))}
        {calc.quarters.map((q) => (
          <g key={q.q}>
            <line x1={calc.x(q.i)} x2={calc.x(q.i)} y1={TOP.y0 - 6} y2={BAR.y0 + BAR.h + 6} stroke="rgba(255,255,255,.12)" strokeDasharray="3 6" />
            <text x={calc.x(q.i) + 6} y={TOP.y0 + 4} className="fb-mom__tick">{q.q > 4 ? 'OT' : `Q${q.q}`}</text>
          </g>
        ))}
        <path d={area(calc.cumA)} fill="url(#fbm-a)" />
        <path d={area(calc.cumH)} fill="url(#fbm-h)" />
        <path d={path(calc.cumA)} fill="none" stroke={ca} strokeWidth="3.5" strokeLinejoin="round" strokeLinecap="round" />
        <path d={path(calc.cumH)} fill="none" stroke={ch} strokeWidth="3.5" strokeLinejoin="round" strokeLinecap="round" />
        {epa.map((p, i) => {
          if (!p.scoring && !p.turnover) return null
          const v = p.team === away.id ? calc.cumA[i] : calc.cumH[i]
          return (
            <g key={p.id} className="fb-mom__event" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onPick?.(p.id)} style={{ cursor: 'pointer' }}>
              <circle cx={calc.x(i)} cy={calc.yTop(v) - 9} r="15" fill="transparent" />
              <text x={calc.x(i)} y={calc.yTop(v) - 3} textAnchor="middle" className="fb-mom__glyph">{p.turnover && !p.scoring ? '✖' : '★'}</text>
            </g>
          )
        })}
        {/* the moment being shown: a draw-in of the newest stretch of that team's line, a pulsing dot and a cursor */}
        {prevSame >= 0 ? (
          <path key={shown.id} className="fb-mom__draw" pathLength={1} fill="none" stroke={lineColor} strokeWidth="6" strokeLinecap="round"
            d={`M ${calc.x(prevSame).toFixed(1)} ${calc.yTop(shown.team === away.id ? calc.cumA[prevSame] : calc.cumH[prevSame]).toFixed(1)} L ${calc.x(calc.shownAt).toFixed(1)} ${calc.yTop(sc).toFixed(1)}`} />
        ) : null}
        <line x1={calc.x(calc.shownAt)} x2={calc.x(calc.shownAt)} y1={TOP.y0 - 6} y2={BAR.y0 + BAR.h + 6} stroke={lineColor} strokeOpacity=".7" strokeWidth="2" />
        <circle key={`d${shown.id}`} className="fb-mom__dot" cx={calc.x(calc.shownAt)} cy={calc.yTop(sc)} r="8" fill={lineColor} />
        <circle className="fb-mom__ring" cx={calc.x(calc.shownAt)} cy={calc.yTop(sc)} r="8" fill="none" stroke={lineColor} strokeWidth="3" />
        {/* every snap as a bar */}
        <line x1="40" x2={W - 40} y1={BAR.y0 + BAR.h / 2} y2={BAR.y0 + BAR.h / 2} stroke="rgba(255,255,255,.18)" />
        {epa.map((p, i) => {
          const h = (Math.abs(p.epa) / calc.barMax) * (BAR.h / 2 - 3)
          const up = p.epa >= 0
          const c = p.team === away.id ? ca : ch
          const isShown = i === calc.shownAt
          return (
            <rect key={p.id} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onPick?.(p.id)} style={{ cursor: 'pointer' }} className={isShown ? 'fb-mom__bar is-shown' : 'fb-mom__bar'} x={calc.x(i) - barW / 2} y={up ? BAR.y0 + BAR.h / 2 - h : BAR.y0 + BAR.h / 2} width={barW} height={Math.max(1.5, h)} rx="1.5" fill={c} opacity={highlight ? (highlight.has(p.id) ? 1 : 0.12) : isShown ? 1 : up ? 0.75 : 0.45}>
              <title>{`Q${p.period} ${p.clock} · ${p.team === away.id ? away.abbr : home.abbr} · ${p.kind} ${p.yards >= 0 ? '+' : ''}${p.yards} yds · ${fmt(p.epa, 2)} expected points`}</title>
            </rect>
          )
        })}
        <text x="34" y={BAR.y0 + 8} textAnchor="end" className="fb-mom__tick">best</text>
        <text x="34" y={BAR.y0 + BAR.h} textAnchor="end" className="fb-mom__tick">worst</text>
      </svg>
      {hover != null && epa[hover] ? (() => {
        const p = epa[hover]
        const tm = p.team === away.id ? away : home
        const w = wpaById?.get(p.id)
        const v = p.team === away.id ? calc.cumA[hover] : calc.cumH[hover]
        return (
          <div className="fb-mom__tip" style={{ left: `${Math.min(72, Math.max(2, (calc.x(hover) / W) * 100 - 14))}%`, top: `${Math.max(0, (calc.yTop(v) / 340) * 100 + 8)}%` }}>
            <b style={{ color: p.team === away.id ? ca : ch }}>{tm.abbr} · Q{p.period} {p.clock}{p.scoring ? ' · SCORE' : p.turnover ? ' · TURNOVER' : ''}</b>
            <span>{p.text}</span>
            <em>{p.yards >= 0 ? '+' : ''}{p.yards} yds · <i className={p.epa >= 0 ? 'is-up' : 'is-down'}>{fmt(p.epa, 2)} expected points</i>{w != null ? <> · win prob. <i className={w >= 0 ? 'is-up' : 'is-down'}>{fmt(w, 1)} pts</i></> : null}</em>
            <small>{tm.abbr} offense running total {fmt(v)} · click to replay</small>
          </div>
        )
      })() : null}
      </div>
      <p className="fb-mom__last">
        <b style={{ color: lineColor }}>{shownTeam.abbr}</b> · Q{shown.period} {shown.clock} · this snap was worth <b className={shown.epa >= 0 ? 'is-up' : 'is-down'}>{fmt(shown.epa, 2)}</b> expected points ({shown.kind}{shown.yards ? `, ${shown.yards >= 0 ? '+' : ''}${shown.yards} yds` : ''}).
      </p>
      <p className="lf-muted lf-small">{note}</p>
    </section>
  )
}
