/**
 * The probability river: MLB's live home win probability after every at-bat,
 * drawn against two flat pregame lines, AISP's model and the sportsbook
 * no-vig consensus. Three sources, three labels. Display only.
 */
import { useMemo } from 'react'
import { biggestSwing, pct, type WpPoint } from './liveFieldData'

const W = 1000
const H = 320
const PAD = { l: 56, r: 24, t: 18, b: 34 }

export function River({ points, modelHome, marketHome, homeAbbr, awayAbbr, live, unit = 'inning', compact = false }: {
  unit?: 'inning' | 'quarter'
  compact?: boolean
  points: readonly WpPoint[]
  modelHome: number | null
  marketHome: number | null
  homeAbbr: string
  awayAbbr: string
  live: boolean
}) {
  const geom = useMemo(() => {
    const n = Math.max(points.length, 2)
    const x = (i: number) => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r)
    const y = (p: number) => PAD.t + (1 - p) * (H - PAD.t - PAD.b)
    const path = points.map((p, i) => `${i ? 'L' : 'M'} ${x(i).toFixed(1)} ${y(p.home).toFixed(1)}`).join(' ')
    const area = points.length ? `${path} L ${x(points.length - 1).toFixed(1)} ${y(0.5).toFixed(1)} L ${x(0).toFixed(1)} ${y(0.5).toFixed(1)} Z` : ''
    const innings: { i: number; label: string }[] = []
    let last: string | null = null
    points.forEach((p, i) => {
      const key = p.inning != null ? `${p.inning}${p.half === 'bottom' ? 'b' : 't'}` : null
      if (key && key !== last && p.half === 'top') innings.push({ i, label: String(p.inning) })
      last = key
    })
    return { x, y, path, area, innings }
  }, [points])
  const now = points.length ? points[points.length - 1].home : null
  const swing = biggestSwing(points)
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  // key moments: the largest swings that carry a label (a score or turnover), up to four, never overlapping
  const moments = (() => {
    const out: Array<{ i: number; label: string; delta: number }> = []
    for (let i = 1; i < points.length; i += 1) { const d = (points[i].home - points[i - 1].home) * 100; if (points[i].event) out.push({ i, label: `${points[i].event} ${d >= 0 ? '+' : ''}${Math.round(d)}%`, delta: d }) }
    out.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    const kept: typeof out = []
    for (const m of out) { if (kept.length >= 4) break; if (kept.every((k) => Math.abs(k.i - m.i) > Math.max(3, points.length / 12))) kept.push(m) }
    return kept.sort((a, b) => a.i - b.i)
  })()

  return (
    <section className={`lf-river${compact ? ' lf-river--compact' : ''}`} aria-label="Win probability">
      <header className="lf-river__head">
        <div>
          <h3>Win probability river</h3>
          <p className="lf-muted">Live line: {unit === 'quarter' ? 'ESPN, after every play' : 'MLB Stats API, after every at-bat'} · flat lines: pregame AISP model and sportsbook no-vig consensus</p>
        </div>
        <div className="lf-river__now">
          <span className="lf-river__big">{now != null ? pct(now) : '—'}</span>
          <span className="lf-muted">{homeAbbr} live{live ? '' : ' (not in play)'}</span>
        </div>
      </header>
      <div className="lf-river__legend">
        <span><i className="lf-sw lf-sw--live" /> live (MLB)</span>
        <span><i className="lf-sw lf-sw--model" /> AISP model pregame {pct(modelHome, 1)}</span>
        <span><i className="lf-sw lf-sw--market" /> market pregame {marketHome == null ? 'not stored' : pct(marketHome, 1)}</span>
      </div>
      <svg className="lf-river__svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Home win probability now ${now != null ? pct(now) : 'unknown'}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={geom.y(t)} y2={geom.y(t)} stroke="rgba(255,255,255,.08)" strokeWidth={t === 0.5 ? 2 : 1} />
            <text x={PAD.l - 8} y={geom.y(t) + 4} textAnchor="end" className="lf-river__tick">{Math.round(t * 100)}%</text>
          </g>
        ))}
        <text x={W - PAD.r} y={PAD.t + 12} textAnchor="end" className="lf-river__side">{homeAbbr} ▲</text>
        <text x={W - PAD.r} y={H - PAD.b - 6} textAnchor="end" className="lf-river__side">{awayAbbr} ▼</text>
        {modelHome != null ? <line x1={PAD.l} x2={W - PAD.r} y1={geom.y(modelHome)} y2={geom.y(modelHome)} className="lf-river__model" /> : null}
        {marketHome != null ? <line x1={PAD.l} x2={W - PAD.r} y1={geom.y(marketHome)} y2={geom.y(marketHome)} className="lf-river__market" /> : null}
        {geom.innings.map((inn) => (
          <g key={inn.i}>
            <line x1={geom.x(inn.i)} x2={geom.x(inn.i)} y1={PAD.t} y2={H - PAD.b} stroke="rgba(255,255,255,.1)" />
            <text x={geom.x(inn.i) + 4} y={H - PAD.b + 16} className="lf-river__tick">{inn.label}</text>
          </g>
        ))}
        {points.length > 1 ? <path d={geom.area} className="lf-river__area" /> : null}
        {points.length > 1 ? <path d={geom.path} className="lf-river__line" /> : null}
        {moments.map((m, k) => { const x = geom.x(m.i); const y = geom.y(points[m.i].home); const up = k % 2 === 0; return (<g key={m.i} className="lf-river__moment-mark"><line x1={x} x2={x} y1={y} y2={up ? y - 34 : y + 34} stroke="rgba(255,215,106,.7)" strokeWidth="1.5" /><circle cx={x} cy={y} r="5" fill="#ffd76a" stroke="#000" /><text x={x} y={up ? y - 40 : y + 48} textAnchor="middle" className="lf-river__mlabel">{m.label}</text></g>) })}
        {points.length ? <circle cx={geom.x(points.length - 1)} cy={geom.y(points[points.length - 1].home)} r="7" className="lf-river__dot" /> : null}
        {swing && Math.abs(swing.delta) >= 0.08 ? (
          <g>
            <circle cx={geom.x(points.indexOf(swing.to))} cy={geom.y(swing.to.home)} r="11" className="lf-river__swing" />
          </g>
        ) : null}
        {!points.length ? <text x={W / 2} y={H / 2} textAnchor="middle" className="lf-river__empty">{live ? 'Waiting for the first at-bat' : 'The river starts at first pitch'}</text> : null}
      </svg>
      {swing && Math.abs(swing.delta) >= 0.08 ? (
        <p className="lf-river__moment">
          <b>Biggest recent swing:</b> {swing.to.event ?? 'a play'} {unit === 'quarter' ? `in the ${ordinal(swing.to.inning)} quarter` : `in the ${swing.to.half === 'bottom' ? 'bottom' : 'top'} of the ${ordinal(swing.to.inning)}`} moved {homeAbbr} {swing.delta > 0 ? 'up' : 'down'} {Math.abs(swing.delta * 100).toFixed(0)} points
          ({pct(swing.from.home)} → {pct(swing.to.home)}).
        </p>
      ) : null}
    </section>
  )
}

export function ordinal(n: number | null | undefined): string {
  if (n == null) return '—'
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`
}
