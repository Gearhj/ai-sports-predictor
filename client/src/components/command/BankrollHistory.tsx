/**
 * The realized bankroll path: combined, or one line per sport account, with the
 * starting capital as a rule and one marker per settlement. Every point is a
 * persisted settlement from `/command-center/ledger`; open exposure is not
 * drawn as if it had already been won or lost.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  SPORT_COLORS, SPORT_NAMES, etTime, money, signedMoney,
  type BankrollPoint, type Ledger, type LedgerPosition, type SportCode,
} from '../../services/commandCenter'
import { ChartTable, Tooltip, activate, useReducedMotion, useTooltip, useWidth } from './chartKit'

type View = 'COMBINED' | 'SPORTS'

interface Series { key: string; label: string; color: string; start: number; points: (BankrollPoint & { date: Date })[] }

export const BankrollHistory = memo(function BankrollHistory({ ledger, compactHeight, hideTable = false }: {
  ledger: Ledger
  /** Fixed chart height for a hero surface; defaults to the full-size chart. */
  compactHeight?: number
  hideTable?: boolean
}) {
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const [view, setView] = useState<View>('COMBINED')
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)
  const pathRefs = useRef<Record<string, SVGPathElement | null>>({})

  const positions = useMemo(() => {
    const out = new Map<string, LedgerPosition & { sport: SportCode }>()
    for (const sport of ledger.sports) for (const p of sport.positions) out.set(p.position_id, { ...p, sport: sport.sport })
    return out
  }, [ledger])

  const series: Series[] = useMemo(() => {
    const withDate = (points: BankrollPoint[]) => points
      .filter((p) => p.at).map((p) => ({ ...p, date: new Date(p.at as string) }))
    if (view === 'COMBINED') {
      return [{ key: 'ALL', label: 'All four accounts', color: 'var(--bx-accent)',
        start: ledger.totals.starting_capital, points: withDate(ledger.combined_series) }]
    }
    return ledger.sports.map((s) => ({
      key: s.sport, label: SPORT_NAMES[s.sport], color: SPORT_COLORS[s.sport],
      start: s.starting_capital, points: withDate(s.series),
    }))
  }, [ledger, view])

  const now = new Date()
  const allPoints = series.flatMap((s) => s.points)
  const first = d3.min(allPoints, (p) => p.date) ?? d3.timeDay.offset(now, -14)
  const extendTo = now
  const height = compactHeight ?? (width < 560 ? 240 : 300)
  const m = { top: 14, right: 16, bottom: 30, left: width < 560 ? 54 : 64 }
  const x = d3.scaleTime().domain([first, extendTo]).range([m.left, width - m.right])
  const values = series.flatMap((s) => [s.start, ...s.points.map((p) => p.bankroll)])
  const [vmin, vmax] = [d3.min(values) ?? 0, d3.max(values) ?? 1]
  const pad = Math.max(2, (vmax - vmin) * 0.25)
  const y = d3.scaleLinear().domain([vmin - pad, vmax + pad]).nice().range([height - m.bottom, m.top])
  const line = d3.line<{ date: Date; bankroll: number }>().x((p) => x(p.date)).y((p) => y(p.bankroll)).curve(d3.curveStepAfter)

  useEffect(() => {
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(width < 560 ? 3 : 6).tickSizeOuter(0) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(height < 140 ? 3 : 5).tickFormat((v) => `$${d3.format(',.0f')(v as number)}`) as never)
    if (reduced) return
    for (const path of Object.values(pathRefs.current)) {
      if (!path) continue
      const length = path.getTotalLength?.() ?? 0
      if (!length) continue
      d3.select(path).attr('stroke-dasharray', `${length} ${length}`).attr('stroke-dashoffset', length)
        .transition().duration(700).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
        .on('end', function () { d3.select(this).attr('stroke-dasharray', null) })
    }
  }, [x, y, width, reduced, series])

  const tipFor = (s: Series, p: BankrollPoint) => {
    const pos = p.position_id ? positions.get(p.position_id) : null
    if (!pos) {
      return <><b>{s.label}</b><dl><dt>Starting capital</dt><dd>{money(p.bankroll)}</dd></dl></>
    }
    return (
      <>
        <b>{pos.selection}</b> · {pos.matchup} · {SPORT_NAMES[pos.sport]}
        <dl>
          <dt>Entry price</dt><dd>{pos.entry.american == null ? '—' : `${pos.entry.american > 0 ? '+' : ''}${pos.entry.american}`}{pos.entry.sportsbook ? ` · ${pos.entry.sportsbook}` : ''}</dd>
          <dt>Stake</dt><dd>{money(pos.stake)}</dd>
          <dt>Result</dt><dd>{pos.result ?? '—'}</dd>
          <dt>Change</dt><dd>{signedMoney(p.change ?? pos.profit_loss)}</dd>
          <dt>{s.key === 'ALL' ? 'Combined bankroll' : 'Bankroll'}</dt><dd>{money(p.bankroll)}</dd>
          <dt>Settled</dt><dd>{etTime(pos.settled_at, true)}</dd>
        </dl>
      </>
    )
  }

  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <div className="cc-filters" role="group" aria-label="Bankroll view">
        <button type="button" aria-pressed={view === 'COMBINED'} onClick={() => setView('COMBINED')}>Combined</button>
        <button type="button" aria-pressed={view === 'SPORTS'} onClick={() => setView('SPORTS')}>By sport</button>
      </div>
      <svg width={width} height={height} role="img"
        aria-label={`Realized bankroll history, ${view === 'COMBINED' ? 'combined' : 'by sport'}, ${allPoints.filter((p) => p.kind === 'SETTLEMENT').length} settlements`}>
        <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        {series.map((s) => (
          <g key={`start-${s.key}`}>
            <line x1={m.left} x2={width - m.right} y1={y(s.start)} y2={y(s.start)} className="cc-startline" stroke={s.color} />
            {(view === 'COMBINED' || s.key === 'MLB') && (
              <text x={width - m.right} y={y(s.start) - 5} textAnchor="end" className="cc-muted-text">
                Starting capital {money(s.start, 0)}
              </text>
            )}
          </g>
        ))}
        {series.map((s) => {
          const pts = [...s.points, ...(s.points.length ? [{ ...s.points[s.points.length - 1], date: extendTo }] : [])]
          return (
            <g key={s.key}>
              <path ref={(node) => { pathRefs.current[s.key] = node }} d={line(pts) ?? ''} fill="none"
                stroke={s.color} strokeWidth={view === 'COMBINED' ? 2.5 : 2} className="cc-line" />
              {s.points.filter((p) => p.kind === 'SETTLEMENT').map((p) => {
                const pos = p.position_id ? positions.get(p.position_id) : null
                const go = () => pos && navigate(`/experiment?position=${pos.position_id}`)
                const won = (p.change ?? 0) > 0
                return (
                  <circle key={`${s.key}-${p.position_id}`} cx={x(p.date)} cy={y(p.bankroll)} r={4.5}
                    className={`cc-settle ${won ? 'cc-settle--win' : (p.change ?? 0) < 0 ? 'cc-settle--loss' : 'cc-settle--push'}`}
                    tabIndex={0} role="link"
                    aria-label={`${pos?.selection ?? 'Settlement'} ${pos?.result ?? ''}, ${signedMoney(p.change)}, bankroll ${money(p.bankroll)}`}
                    onMouseMove={(e) => show(e, tipFor(s, p))} onMouseLeave={hide}
                    onFocus={(e) => show(e, tipFor(s, p))} onBlur={hide} {...activate(go)} />
                )
              })}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
      {view === 'SPORTS' && (
        <ul className="cc-legend" aria-label="Sports">
          {ledger.sports.map((s) => (
            <li key={s.sport}><i className="cc-key" style={{ background: SPORT_COLORS[s.sport] }} />{SPORT_NAMES[s.sport]} · {money(s.realized_bankroll)}</li>
          ))}
        </ul>
      )}
      {!hideTable && <ChartTable caption="Settlements in order">
        <thead><tr><th>Settled</th><th>Sport</th><th>Game</th><th>Selection</th><th>Entry</th><th>Stake</th><th>Result</th><th>Change</th></tr></thead>
        <tbody>
          {ledger.combined_series.filter((p) => p.kind === 'SETTLEMENT').map((p) => {
            const pos = p.position_id ? positions.get(p.position_id) : null
            return (
              <tr key={p.position_id ?? p.at ?? ''}>
                <td>{etTime(p.at, true)}</td><td>{pos?.sport}</td><td>{pos?.matchup}</td><td>{pos?.selection}</td>
                <td>{pos?.entry.american == null ? '—' : `${pos.entry.american > 0 ? '+' : ''}${pos.entry.american}`}</td>
                <td>{money(pos?.stake)}</td><td>{pos?.result}</td><td>{signedMoney(p.change)}</td>
              </tr>
            )
          })}
        </tbody>
      </ChartTable>}
    </div>
  )
})
