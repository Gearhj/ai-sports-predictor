/**
 * The day's agenda on one compact axis.
 *
 * One lane per sport (sport colour). Within a lane, event kinds keep fixed
 * tiers and symbols: forecast ◆, position ★ and settlement ▲ on the top tier;
 * the collection window (thin bar) and decision window (thick bar) in the
 * middle; game start ● at the bottom. Status is a treatment, not a colour:
 * done = filled, scheduled = outlined, expected = dashed outline, open = filled
 * with a ring, missed = red outline. Milestones of the same kind at the same
 * minute are one mark with a count, so a 15-game slate stays legible.
 *
 * Every mark is focusable; hover or focus shows the exact time, sport, games
 * and status. A table view carries the same information.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef } from 'react'
import { SPORT_CODES, SPORT_COLORS, SPORT_NAMES, type SportCode } from '../../services/commandCenter'
import { KIND_LABEL, type AgendaEvent, type AgendaKind, type AgendaStatus } from '../../services/agenda'
import { ChartTable, Tooltip, useReducedMotion, useTooltip, useWidth } from './chartKit'

interface Mark {
  key: string
  sport: SportCode
  kind: AgendaKind
  status: AgendaStatus
  at: number
  until?: number
  events: AgendaEvent[]
}

const MIN = 60_000
const TIER: Record<AgendaKind, number> = { forecast: 0, position: 0, settlement: 0, collection: 1, decision: 2, game: 2 }
const et = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(ms)
const STATUS_TEXT: Record<AgendaStatus, string> = { done: 'Done', open: 'Open now', scheduled: 'Scheduled', expected: 'Expected', missed: 'Missed' }

export const AgendaTimeline = memo(function AgendaTimeline({ events, now, idle = {} }: {
  events: AgendaEvent[]
  now: number
  /** Next-game text for a sport with nothing on today's axis. */
  idle?: Partial<Record<SportCode, string>>
}) {
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axisRef = useRef<SVGGElement | null>(null)

  const marks = useMemo(() => {
    const groups = new Map<string, Mark>()
    for (const e of events) {
      const key = `${e.sport}|${e.kind}|${e.status}|${Math.round(e.at / MIN)}|${e.until ? Math.round(e.until / MIN) : ''}`
      const g = groups.get(key)
      if (g) g.events.push(e)
      else groups.set(key, { key, sport: e.sport, kind: e.kind, status: e.status, at: e.at, until: e.until, events: [e] })
    }
    return [...groups.values()]
  }, [events])

  const lanes = SPORT_CODES.filter((s) => marks.some((m) => m.sport === s) || idle[s])
  const laneH = 50
  const idleH = 26
  const laneHeight = (s: SportCode) => (marks.some((k) => k.sport === s) ? laneH : idleH)
  const laneTop = (i: number) => lanes.slice(0, i).reduce((sum, s) => sum + laneHeight(s), 0)
  const labelW = width < 560 ? 44 : 118
  const m = { top: 22, right: 14, bottom: 4 }
  const height = m.top + Math.max(laneH, lanes.reduce((sum, s) => sum + laneHeight(s), 0)) + m.bottom + 16
  const times = marks.flatMap((k) => [k.at, k.until ?? k.at])
  const lo = Math.min(now, d3.min(times) ?? now) - 30 * MIN
  const hi = Math.max(now + 60 * MIN, d3.max(times) ?? now) + 20 * MIN
  const x = d3.scaleTime().domain([lo, hi]).range([labelW, Math.max(labelW + 60, width - m.right)])

  useEffect(() => {
    if (!axisRef.current) return
    const axis = d3.axisTop(x).ticks(width < 560 ? 4 : 10)
      .tickFormat((d) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric' }).format(d as Date))
      .tickSizeOuter(0)
    const sel = d3.select(axisRef.current)
    ;(reduced ? sel : sel.transition().duration(250)).call(axis as never)
  }, [x, width, reduced])

  const tipFor = (mk: Mark) => (
    <>
      <b>{KIND_LABEL[mk.kind]}</b> · {SPORT_NAMES[mk.sport]}
      <dl>
        <dt>Time</dt><dd>{et(mk.at)}{mk.until ? `–${et(mk.until)}` : ''} ET</dd>
        <dt>Status</dt><dd>{mk.events[0].label} ({STATUS_TEXT[mk.status].toLowerCase()})</dd>
        <dt>{mk.events.length > 1 ? `${mk.events.length} games` : 'Game'}</dt>
        <dd>{mk.events.slice(0, 6).map((e) => e.matchup).join(', ')}{mk.events.length > 6 ? ` +${mk.events.length - 6}` : ''}</dd>
        {mk.events.length === 1 && <><dt>Detail</dt><dd>{mk.events[0].detail}</dd></>}
      </dl>
    </>
  )

  return (
    <div className="cc-chart cc-agenda" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Today's agenda: ${events.length} milestones across ${lanes.length} sports`}>
        <g ref={axisRef} className="cc-axis" transform={`translate(0, ${m.top - 4})`} />
        {lanes.map((sport, li) => {
          const top = m.top + laneTop(li)
          const laneMarks = marks.filter((k) => k.sport === sport)
          const h = laneHeight(sport)
          const windows = laneMarks.filter((k) => k.kind === 'collection')
          const band = windows.length ? [d3.min(windows, (k) => k.at)!, d3.max(windows, (k) => k.until ?? k.at)!] : null
          return (
            <g key={sport} transform={`translate(0, ${top})`}>
              <rect x={0} y={2} width={width} height={h - 4} rx={8} className="cc-agenda__lane" />
              <rect x={0} y={2} width={4} height={h - 4} rx={2} fill={SPORT_COLORS[sport]} />
              <text x={10} y={h / 2 + 4} className="cc-agenda__sport">{width < 560 ? sport : SPORT_NAMES[sport]}</text>
              {laneMarks.length === 0 && idle[sport] && (
                <text x={labelW + 6} y={h / 2 + 4} className="cc-muted-text">{idle[sport]}</text>
              )}
              {band && (
                <rect x={x(band[0])} y={22} width={Math.max(4, x(band[1]) - x(band[0]))} height={6} rx={3}
                  fill={SPORT_COLORS[sport]} className="cc-agenda__band" />
              )}
              {laneMarks.map((mk) => {
                const cy = 12 + TIER[mk.kind] * 14
                const color = SPORT_COLORS[sport]
                const cls = `cc-am cc-am--${mk.status}`
                const aria = `${KIND_LABEL[mk.kind]}, ${SPORT_NAMES[sport]}, ${et(mk.at)}${mk.until ? ` to ${et(mk.until)}` : ''}, ${mk.events.length} game${mk.events.length === 1 ? '' : 's'}, ${STATUS_TEXT[mk.status]}`
                const handlers = { onMouseMove: (e: React.MouseEvent) => show(e, tipFor(mk)), onMouseLeave: hide, onFocus: (e: React.FocusEvent) => show(e, tipFor(mk)), onBlur: hide }
                let shape: JSX.Element
                if (mk.kind === 'collection') {
                  // Each collection opening is a tick on the sport's collection band.
                  shape = <rect x={x(mk.at) - 1.5} y={19} width={3} height={12} rx={1.5}
                    fill={mk.status === 'done' || mk.status === 'open' ? color : 'var(--cc-agenda-bg, #fff)'} stroke={color} />
                } else if (mk.kind === 'decision') {
                  const x0 = x(mk.at)
                  const w = Math.max(4, x(mk.until ?? mk.at) - x0)
                  shape = <rect x={x0} y={33} width={w} height={8} rx={4}
                    fill={mk.status === 'scheduled' || mk.status === 'expected' ? 'var(--cc-agenda-bg, #fff)' : color} stroke={color} />
                } else {
                  const sym = mk.kind === 'forecast' ? d3.symbolDiamond : mk.kind === 'position' ? d3.symbolStar : mk.kind === 'settlement' ? d3.symbolTriangle : d3.symbolCircle
                  const size = mk.kind === 'game' ? 70 : 64
                  shape = <path d={d3.symbol(sym, size)() ?? ''} transform={`translate(${x(mk.at)}, ${cy})`}
                    fill={mk.status === 'done' || mk.status === 'open' ? color : 'var(--cc-agenda-bg, #fff)'} stroke={color} />
                }
                return (
                  <g key={mk.key} className={cls} tabIndex={0} aria-label={aria} {...handlers}>
                    {shape}
                    {mk.status === 'open' && mk.kind !== 'collection' && mk.kind !== 'decision' && <circle cx={x(mk.at)} cy={cy} r={9} className="cc-am__ring" stroke={color} />}
                    {mk.events.length > 1 && mk.kind !== 'decision' && mk.kind !== 'collection' && (
                      <text x={x(mk.at) + 7} y={cy - 5} className="cc-am__count">{mk.events.length}</text>
                    )}
                  </g>
                )
              })}
            </g>
          )
        })}
        {now >= lo && now <= hi && (
          <g className="cc-now" transform={`translate(${x(now)}, 0)`}>
            <line y1={m.top - 2} y2={height - 16} />
            <rect x={-34} y={height - 16} width={68} height={15} rx={7} className="cc-now__pill" />
            <text y={height - 5} textAnchor="middle" className="cc-now__text">now {et(now)}</text>
          </g>
        )}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend cc-agenda__legend" aria-label="Agenda legend">
        <li><span className="cc-glyph">◆</span>Forecast</li>
        <li><i className="cc-key cc-key--collect-bar" />Collection window</li>
        <li><i className="cc-key cc-key--decision-bar" />Decision window</li>
        <li><span className="cc-glyph">●</span>Game start</li>
        <li><span className="cc-glyph">★</span>Position</li>
        <li><span className="cc-glyph">▲</span>Settlement</li>
        <li className="cc-legend__status">Filled = done · outline = scheduled · dashed = expected · red = missed</li>
      </ul>
      <ChartTable caption="Today's agenda">
        <thead><tr><th>Time (ET)</th><th>Sport</th><th>Event</th><th>Games</th><th>Status</th></tr></thead>
        <tbody>
          {marks.sort((a, b) => a.at - b.at).map((mk) => (
            <tr key={mk.key}>
              <td>{et(mk.at)}{mk.until ? `–${et(mk.until)}` : ''}</td><td>{mk.sport}</td><td>{mk.events[0].label}</td>
              <td>{mk.events.map((e) => e.matchup).join(', ')}</td><td>{STATUS_TEXT[mk.status]}</td>
            </tr>
          ))}
        </tbody>
      </ChartTable>
    </div>
  )
})
