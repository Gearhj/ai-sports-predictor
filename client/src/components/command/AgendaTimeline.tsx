/**
 * "Today on one axis": the day's milestones per sport on one time axis.
 *
 * Each sport lane has three bands -- milestones (forecast ◆, position ★,
 * settlement ▲), windows (collection tick, decision bar) and game starts -- and
 * `services/agendaLayout` clusters coinciding milestones and packs each band
 * into as many sub-rows as it needs, so a full MLB slate gets a taller lane
 * instead of overprinting. Games starting together are one pill
 * ("4 games · 6:40 PM"); hover or focus lists them with their exact times, and
 * Enter or a click pins the list with links to each game.
 *
 * Status is a treatment, not a colour: done and open are filled, scheduled is
 * outlined, expected is dashed, missed is outlined in red.
 *
 * Below 600px the chart becomes a chronological agenda built from the same
 * events rather than a squeezed axis.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { SPORT_CODES, SPORT_COLORS, SPORT_NAMES, gameHref, type SportCode } from '../../services/commandCenter'
import { KIND_LABEL, type AgendaEvent, type AgendaStatus } from '../../services/agenda'
import { clusterEvents, layoutLane, rowY, type Cluster, type PlacedCluster } from '../../services/agendaLayout'
import { ChartTable, Tooltip, activate, useReducedMotion, useTooltip, useWidth } from './chartKit'

const MIN = 60_000
const et = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(ms)
const STATUS_TEXT: Record<AgendaStatus, string> = { done: 'Done', open: 'Open now', scheduled: 'Scheduled', expected: 'Expected', missed: 'Missed' }
const GLYPH: Record<string, string> = { forecast: '◆', position: '★', settlement: '▲', game: '●', collection: '▮', decision: '▬' }

export const AgendaTimeline = memo(function AgendaTimeline({ events, now, idle = {}, decisionsHref = '/decisions' }: {
  events: AgendaEvent[]
  now: number
  /** Next-game text for a sport with nothing on today's axis. */
  idle?: Partial<Record<SportCode, string>>
  decisionsHref?: string
}) {
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axisRef = useRef<SVGGElement | null>(null)
  const [pinned, setPinned] = useState<Cluster | null>(null)

  const clusters = useMemo(() => clusterEvents(events), [events])
  const narrow = width < 600
  const lanesPresent = SPORT_CODES.filter((s) => clusters.some((c) => c.sport === s) || idle[s])
  const labelW = 112
  const m = { top: 22, right: 12, bottom: 20 }
  const times = clusters.flatMap((c) => [c.at, c.until ?? c.at])
  const lo = Math.min(now, d3.min(times) ?? now) - 30 * MIN
  const hi = Math.max(now + 60 * MIN, d3.max(times) ?? now) + 25 * MIN
  const x = useMemo(() => d3.scaleTime().domain([lo, hi]).range([labelW, Math.max(labelW + 60, width - m.right)]), [lo, hi, width]) // eslint-disable-line react-hooks/exhaustive-deps
  const layouts = useMemo(() => lanesPresent.map((sport) => layoutLane(sport, clusters, (ms) => x(ms), { right: width - m.right })),
    [clusters, x, width, lanesPresent.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps
  const tops = layouts.reduce<number[]>((acc, l, i) => [...acc, i === 0 ? 0 : acc[i - 1] + layouts[i - 1].height + 4], [])
  const height = m.top + (layouts.length ? tops[tops.length - 1] + layouts[layouts.length - 1].height : 40) + m.bottom

  useEffect(() => {
    if (!axisRef.current || narrow) return
    const axis = d3.axisTop(x).ticks(d3.timeHour.every(width < 900 ? 2 : 1) as never)
      .tickFormat((d) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric' }).format(d as Date))
      .tickSizeOuter(0)
    const sel = d3.select(axisRef.current)
    ;(reduced ? sel : sel.transition().duration(250)).call(axis as never)
  }, [x, narrow, reduced, width])

  useEffect(() => {
    if (!pinned) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPinned(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pinned])

  const tipFor = (c: Cluster) => (
    <>
      <b>{KIND_LABEL[c.kind]}</b> · {SPORT_NAMES[c.sport]}
      <dl>
        <dt>Status</dt><dd>{c.events[0].label} ({STATUS_TEXT[c.status].toLowerCase()})</dd>
        {c.events.slice(0, 8).map((e) => (
          <FragmentRow key={e.key} time={`${et(e.at)}${e.until ? `–${et(e.until)}` : ''}`} text={e.matchup} />
        ))}
        {c.events.length > 8 && <><dt>+{c.events.length - 8}</dt><dd>more · press Enter to list all</dd></>}
        {c.events.length === 1 && <><dt>Detail</dt><dd>{c.events[0].detail}</dd></>}
      </dl>
    </>
  )

  if (narrow) {
    return (
      <div className="cc-agenda cc-agenda--list" ref={(node) => { ref.current = node; frame.current = node }}>
        <AgendaList clusters={clusters} now={now} idle={idle} decisionsHref={decisionsHref} />
      </div>
    )
  }

  return (
    <div className="cc-chart cc-agenda" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Today's agenda: ${events.length} milestones across ${lanesPresent.length} sports`}>
        <g ref={axisRef} className="cc-axis" transform={`translate(0, ${m.top - 4})`} />
        {layouts.map((layout, li) => {
          const top = m.top + tops[li]
          const sport = layout.sport
          const color = SPORT_COLORS[sport]
          const windows = layout.clusters.filter((c) => c.kind === 'collection')
          const band = windows.length ? [d3.min(windows, (k) => k.at)!, d3.max(windows, (k) => k.until ?? k.at)!] : null
          return (
            <g key={sport} transform={`translate(0, ${top})`}>
              <rect x={0} y={0} width={width} height={layout.height} rx={8} className="cc-agenda__lane" />
              <rect x={0} y={0} width={4} height={layout.height} rx={2} fill={color} />
              <text x={10} y={Math.min(layout.height / 2 + 4, 18)} className="cc-agenda__sport">{SPORT_NAMES[sport]}</text>
              {layout.clusters.length > 0 && <text x={10} y={Math.min(layout.height / 2 + 4, 18) + 14} className="cc-muted-text">
                {new Set(layout.clusters.filter((c) => c.kind === 'game').flatMap((c) => c.events.map((e) => e.gameId))).size} games</text>}
              {layout.clusters.length === 0 && idle[sport] && (
                <text x={labelW + 6} y={layout.height / 2 + 4} className="cc-muted-text">{idle[sport]}</text>
              )}
              {band && <rect x={x(band[0])} y={rowY(layout, 'win', 0) - 2} width={Math.max(4, x(band[1]) - x(band[0]))} height={4} rx={2}
                fill={color} className="cc-agenda__band" />}
              {layout.clusters.map((c) => (
                <ClusterMark key={c.key} c={c} y={rowY(layout, c.band, c.row)} x={x(c.at)} color={color}
                  pinned={pinned?.key === c.key}
                  onTip={(e) => show(e, tipFor(c))} onHide={hide}
                  onPin={() => setPinned((p) => (p?.key === c.key ? null : c))} />
              ))}
            </g>
          )
        })}
        {now >= lo && now <= hi && (
          <g className="cc-now" transform={`translate(${x(now)}, 0)`}>
            <line y1={m.top - 2} y2={height - m.bottom + 2} />
            <rect x={-34} y={height - m.bottom + 3} width={68} height={15} rx={7} className="cc-now__pill" />
            <text y={height - m.bottom + 14} textAnchor="middle" className="cc-now__text">now {et(now)}</text>
          </g>
        )}
      </svg>
      <Tooltip tip={tip} width={width} />
      {pinned && <PinnedList cluster={pinned} decisionsHref={decisionsHref} onClose={() => setPinned(null)} />}
      <ul className="cc-legend cc-agenda__legend" aria-label="Agenda legend">
        <li><span className="cc-glyph">◆</span>Forecast</li>
        <li><i className="cc-key cc-key--collect-bar" />Collection</li>
        <li><i className="cc-key cc-key--decision-bar" />Decision window</li>
        <li><span className="cc-glyph">●</span>Game · pill = several games</li>
        <li><span className="cc-glyph">★</span>Position</li>
        <li><span className="cc-glyph">▲</span>Settlement</li>
        <li className="cc-legend__status">Filled = done · hollow = upcoming</li>
      </ul>
      <ChartTable caption="Today's agenda">
        <thead><tr><th>Time (ET)</th><th>Sport</th><th>Event</th><th>Games</th><th>Status</th></tr></thead>
        <tbody>
          {[...events].sort((a, b) => a.at - b.at).map((e) => (
            <tr key={e.key}>
              <td>{et(e.at)}{e.until ? `–${et(e.until)}` : ''}</td><td>{e.sport}</td><td>{e.label}</td>
              <td>{e.matchup}</td><td>{STATUS_TEXT[e.status]}</td>
            </tr>
          ))}
        </tbody>
      </ChartTable>
    </div>
  )
})

function FragmentRow({ time, text }: { time: string; text: string }) {
  return <><dt>{time}</dt><dd>{text}</dd></>
}

function ClusterMark({ c, x, y, color, pinned, onTip, onHide, onPin }: {
  c: PlacedCluster; x: number; y: number; color: string; pinned: boolean
  onTip: (e: { clientX?: number; clientY?: number; currentTarget: Element }) => void; onHide: () => void; onPin: () => void
}) {
  const filled = c.status === 'done' || c.status === 'open'
  const bg = 'var(--cc-agenda-bg, #fff)'
  const n = c.events.length
  const aria = `${KIND_LABEL[c.kind]}, ${SPORT_NAMES[c.sport]}, ${n > 1 ? `${n} games, ` : `${c.events[0].matchup}, `}${et(c.at)}${c.until ? ` to ${et(c.until)}` : ''}, ${STATUS_TEXT[c.status]}. Press Enter to list games.`
  let shape: JSX.Element
  if (c.kind === 'collection') {
    shape = <rect x={x - 1.5} y={y - 6} width={3} height={12} rx={1.5} fill={filled ? color : bg} stroke={color} />
  } else if (c.kind === 'decision') {
    shape = <rect x={c.x0} y={y - 4} width={Math.max(4, c.x1 - c.x0)} height={8} rx={4} fill={filled ? color : bg} stroke={color} />
  } else if (c.kind === 'game' && n > 1) {
    shape = (
      <>
        <rect x={c.x0} y={y - 9} width={c.x1 - c.x0} height={18} rx={9} fill={filled ? color : bg} stroke={color} className="cc-am__pill" />
        <circle cx={x} cy={y} r={3} fill={filled ? bg : color} />
        <text x={c.x0 + 13} y={y + 4} className={`cc-am__label${filled ? ' is-filled' : ''}`}>{c.label}</text>
      </>
    )
  } else {
    const sym = c.kind === 'forecast' ? d3.symbolDiamond : c.kind === 'position' ? d3.symbolStar : c.kind === 'settlement' ? d3.symbolTriangle : d3.symbolCircle
    shape = (
      <>
        <path d={d3.symbol(sym, c.kind === 'game' ? 70 : 60)() ?? ''} transform={`translate(${x}, ${y})`} fill={filled ? color : bg} stroke={color} />
        {c.label && <text x={c.x1 < x + 10 ? x - 9 : x + 9} y={y + 4} textAnchor={c.x1 < x + 10 ? 'end' : 'start'} className="cc-am__label cc-am__label--plain">{c.label}</text>}
      </>
    )
  }
  return (
    <g className={`cc-am cc-am--${c.status}${pinned ? ' is-pinned' : ''}`} tabIndex={0} role="button" aria-label={aria} aria-expanded={pinned}
      onMouseMove={onTip} onMouseLeave={onHide} onFocus={onTip} onBlur={onHide} {...activate(onPin)}>
      <rect x={c.x0 - 3} y={y - 10} width={c.x1 - c.x0 + 6} height={20} fill="transparent" />
      {shape}
    </g>
  )
}

function PinnedList({ cluster, decisionsHref, onClose }: { cluster: Cluster; decisionsHref: string; onClose: () => void }) {
  return (
    <div className="cc-agenda__pinned" role="region" aria-label={`${KIND_LABEL[cluster.kind]} details`} style={{ ['--sport' as string]: SPORT_COLORS[cluster.sport] }}>
      <header>
        <b>{KIND_LABEL[cluster.kind]} · {SPORT_NAMES[cluster.sport]} · {cluster.events.length} game{cluster.events.length === 1 ? '' : 's'}</b>
        <button type="button" onClick={onClose} aria-label="Close list">×</button>
      </header>
      <ul>
        {cluster.events.map((e) => (
          <li key={e.key}>
            <span className="cc-agenda__pinned-time">{et(e.at)}{e.until ? `–${et(e.until)}` : ''}</span>
            <Link to={gameHref(e.sport, e.gameId)}>{e.matchup}</Link>
            <span className="cc-muted">{e.label}</span>
            {(e.kind === 'decision' || e.kind === 'position') && <Link to={decisionsHref}>Decision</Link>}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Mobile: the same clusters as a chronological list, upcoming first. */
function AgendaList({ clusters, now, idle, decisionsHref }: { clusters: Cluster[]; now: number; idle: Partial<Record<SportCode, string>>; decisionsHref: string }) {
  const [showAll, setShowAll] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const upcoming = clusters.filter((c) => c.status !== 'done' && (c.until ?? c.at) >= now - 5 * MIN).sort((a, b) => a.at - b.at)
  const done = clusters.filter((c) => c.status === 'done')
  const shown = showAll ? upcoming : upcoming.slice(0, 8)
  return (
    <div className="cc-agenda-list">
      <ol>
        {shown.length === 0 && <li className="cc-muted">Nothing else scheduled today.</li>}
        {shown.map((c) => (
          <li key={c.key} style={{ ['--sport' as string]: SPORT_COLORS[c.sport] }} className={`cc-agenda-list__item cc-am--${c.status}`}>
            <button type="button" aria-expanded={open === c.key} onClick={() => setOpen((o) => (o === c.key ? null : c.key))}>
              <span className="cc-agenda-list__time">{et(c.at)}</span>
              <span className="cc-agenda-list__glyph" aria-hidden="true">{GLYPH[c.kind]}</span>
              <span className="cc-agenda-list__body">
                <b>{KIND_LABEL[c.kind]}{c.events.length > 1 ? ` · ${c.events.length} games` : ''}</b>
                <small>{c.sport} · {c.events.length > 1 ? c.events.slice(0, 3).map((e) => e.matchup).join(', ') + (c.events.length > 3 ? '…' : '') : c.events[0].matchup} · {STATUS_TEXT[c.status].toLowerCase()}</small>
              </span>
            </button>
            {open === c.key && (
              <ul className="cc-agenda-list__games">
                {c.events.map((e) => (
                  <li key={e.key}><span>{et(e.at)}</span><Link to={gameHref(e.sport, e.gameId)}>{e.matchup}</Link>
                    {e.kind === 'decision' && <Link to={decisionsHref}>Decision</Link>}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {upcoming.length > 8 && <button type="button" className="cc-more" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show fewer' : `Show all ${upcoming.length}`}</button>}
      <p className="cc-note">{done.length} milestone group{done.length === 1 ? '' : 's'} completed{Object.values(idle).length ? ` · ${Object.entries(idle).map(([s, t]) => `${s}: ${t}`).join(' · ')}` : ''}</p>
    </div>
  )
}
