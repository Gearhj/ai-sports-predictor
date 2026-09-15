/**
 * The day on one axis: every game's price-collection window, T-30..T-15
 * decision window, first pitch/kickoff, forecast availability, price state and
 * recorded position, grouped by sport, with a live "now" rule.
 *
 * Built from `/command-center/markets` (schedule, price state, checkpoints,
 * controlling-forecast time) and `/command-center/decisions` (positions and
 * governed evaluations). Nothing is inferred beyond those rows: a game with no
 * controlling forecast shows a hollow start mark, not a guessed one.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  PRICE_STATE_LABEL, SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, etTime, gameHref,
  type CoverageGame, type DecisionItem, type SportCode,
} from '../../services/commandCenter'
import { ChartTable, Tooltip, activate, useReducedMotion, useTooltip, useWidth } from './chartKit'

interface Lane {
  sport: SportCode
  game: CoverageGame
  positions: DecisionItem[]
  evaluation: DecisionItem | null
}

const ROW = 22
const HEAD = 24

export const DailyTimeline = memo(function DailyTimeline({ games, decisions, idleSports = {} }: {
  games: CoverageGame[]
  decisions: DecisionItem[]
  /** Per-sport text for a sport with no game on this date (from readiness). */
  idleSports?: Partial<Record<SportCode, string>>
}) {
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axisRef = useRef<SVGGElement | null>(null)

  const lanes = useMemo(() => {
    const byGame = new Map<string, DecisionItem[]>()
    for (const item of decisions) byGame.set(item.game_id, [...(byGame.get(item.game_id) ?? []), item])
    return games
      .map((game): Lane | null => {
        const sport = asSport(game.league)
        if (!sport) return null
        const items = byGame.get(game.game_id) ?? []
        const evaluation = [...items].sort((a, b) => (b.recommended_at ?? '').localeCompare(a.recommended_at ?? ''))[0] ?? null
        return { sport, game, positions: items.filter((i) => i.position), evaluation }
      })
      .filter((lane): lane is Lane => lane !== null)
      .sort((a, b) => SPORT_CODES.indexOf(a.sport) - SPORT_CODES.indexOf(b.sport)
        || (a.game.starts_at ?? '').localeCompare(b.game.starts_at ?? ''))
  }, [games, decisions])

  const groups = SPORT_CODES.map((sport) => ({ sport, lanes: lanes.filter((l) => l.sport === sport) }))
  const labelWidth = width < 560 ? 76 : 112
  const margin = { left: labelWidth, right: 14, top: 26, bottom: 8 }
  let y = margin.top
  const placed = groups.map((group) => {
    const top = y
    y += HEAD + Math.max(1, group.lanes.length) * ROW
    return { ...group, top }
  })
  const height = y + margin.bottom

  const times = lanes.flatMap((l) => [l.game.checkpoints.collection_opens_at, l.game.starts_at])
    .filter(Boolean).map((t) => new Date(t as string))
  const extent = d3.extent(times) as [Date | undefined, Date | undefined]
  const now = new Date()
  const lo = extent[0] ?? d3.timeHour.offset(now, -2)
  const hi = extent[1] ? d3.timeHour.offset(extent[1], 1) : d3.timeHour.offset(now, 10)
  const x = d3.scaleTime().domain([lo, hi]).range([margin.left, Math.max(margin.left + 80, width - margin.right)])

  useEffect(() => {
    if (!axisRef.current) return
    const format = (d: Date | d3.NumberValue) => new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour: 'numeric' }).format(d as Date)
    const axis = d3.axisTop(x).ticks(width < 560 ? 4 : 8).tickFormat(format).tickSizeOuter(0)
    const selection = d3.select(axisRef.current)
    ;(reduced ? selection : selection.transition().duration(300)).call(axis as never)
  }, [x, width, reduced])

  const tipFor = (lane: Lane) => {
    const g = lane.game
    return (
      <>
        <b>{g.matchup ?? 'Game'}</b> · {SPORT_NAMES[lane.sport]}
        <dl>
          <dt>Starts</dt><dd>{etTime(g.starts_at)}</dd>
          <dt>Forecast</dt><dd>{g.model ? `${g.model.engine_id} · ${etTime(g.model.created_at, true)}` : 'Controlling forecast not published'}</dd>
          <dt>Price</dt><dd>{PRICE_STATE_LABEL[g.price_state]}{g.books ? ` · ${g.books} book${g.books === 1 ? '' : 's'}` : ''}</dd>
          <dt>Collection</dt><dd>opens {etTime(g.checkpoints.collection_opens_at)}</dd>
          <dt>Decision</dt><dd>{etTime(g.checkpoints.decision_window_opens_at)} – {etTime(g.checkpoints.decision_window_closes_at)}</dd>
          <dt>Evaluation</dt><dd>{lane.evaluation ? `${lane.evaluation.recommendation.replace(/_/g, ' ').toLowerCase()} (${lane.evaluation.phase.replace(/_/g, ' ').toLowerCase()})` : 'Not evaluated'}</dd>
          <dt>Position</dt><dd>{lane.positions.length ? lane.positions.map((p) => `${p.selection} ${p.american != null && p.american > 0 ? '+' : ''}${p.american ?? ''}`).join(', ') : 'None'}</dd>
        </dl>
      </>
    )
  }

  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Daily timeline of ${lanes.length} games`}>
        <g ref={axisRef} className="cc-axis" transform={`translate(0, ${margin.top - 6})`} />
        {placed.map((group) => (
          <g key={group.sport} transform={`translate(0, ${group.top})`}>
            <rect x={0} y={0} width={width} height={HEAD - 4} className="cc-lanehead" />
            <circle cx={9} cy={(HEAD - 4) / 2} r={4} fill={SPORT_COLORS[group.sport]} />
            <text x={18} y={(HEAD - 4) / 2 + 4} className="cc-lanehead__text">
              {SPORT_NAMES[group.sport]}{group.lanes.length ? ` · ${group.lanes.length}` : ''}
            </text>
            {group.lanes.length === 0 && (
              <text x={labelWidth} y={HEAD + ROW / 2} className="cc-muted-text">
                {idleSports[group.sport] ?? 'No game on this date'}
              </text>
            )}
            {group.lanes.map((lane, index) => {
              const g = lane.game
              const cy = HEAD + index * ROW + ROW / 2
              const start = g.starts_at ? x(new Date(g.starts_at)) : null
              const collect = x(new Date(g.checkpoints.collection_opens_at))
              const open = x(new Date(g.checkpoints.decision_window_opens_at))
              const close = x(new Date(g.checkpoints.decision_window_closes_at))
              const go = () => navigate(gameHref(lane.sport, g.game_id))
              return (
                <g key={g.game_id} className="cc-lane" tabIndex={0} role="link"
                  aria-label={`${g.matchup}, starts ${etTime(g.starts_at)}, ${PRICE_STATE_LABEL[g.price_state]}${lane.positions.length ? ', position recorded' : ''}`}
                  onMouseMove={(e) => show(e, tipFor(lane))} onMouseLeave={hide}
                  onFocus={(e) => show(e, tipFor(lane))} onBlur={hide} {...activate(go)}>
                  <rect x={0} y={cy - ROW / 2} width={width} height={ROW} className="cc-lane__hit" />
                  <text x={labelWidth - 8} y={cy + 4} textAnchor="end" className="cc-lane__label">{g.matchup}</text>
                  <line x1={collect} x2={close} y1={cy} y2={cy} className={`cc-collect cc-collect--${g.price_state}`} />
                  <rect x={open} y={cy - 5} width={Math.max(3, close - open)} height={10} rx={2}
                    className="cc-window" fill={SPORT_COLORS[lane.sport]} />
                  {start != null && (
                    <circle cx={start} cy={cy} r={5.5}
                      className={`cc-start ${g.model ? 'cc-start--forecast' : 'cc-start--none'} cc-start--${g.price_state}`}
                      style={{ ['--lane' as string]: SPORT_COLORS[lane.sport] }} />
                  )}
                  {lane.positions.length > 0 && start != null && (
                    <path d={d3.symbol(d3.symbolStar, 70)() ?? ''} transform={`translate(${start + 13}, ${cy})`} className="cc-position" />
                  )}
                </g>
              )
            })}
          </g>
        ))}
        {now >= lo && now <= hi && (
          <g className="cc-now" transform={`translate(${x(now)}, 0)`}>
            <line y1={margin.top - 4} y2={height - margin.bottom} />
            <text y={margin.top - 14} textAnchor="middle">now</text>
          </g>
        )}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend" aria-label="Timeline legend">
        <li><i className="cc-key cc-key--collect" />Price collection window</li>
        <li><i className="cc-key cc-key--window" />Decision window T-30 to T-15</li>
        <li><i className="cc-key cc-key--forecast" />Start · forecast published</li>
        <li><i className="cc-key cc-key--none" />Start · no controlling forecast</li>
        <li><i className="cc-key cc-key--current" />Current price</li>
        <li><span className="cc-key cc-key--star">★</span>Position recorded</li>
      </ul>
      <ChartTable caption="Games, windows and states">
        <thead><tr><th>Sport</th><th>Game</th><th>Starts</th><th>Decision window</th><th>Forecast</th><th>Price</th><th>Position</th></tr></thead>
        <tbody>
          {lanes.map((lane) => (
            <tr key={lane.game.game_id}>
              <td>{lane.sport}</td><td>{lane.game.matchup}</td><td>{etTime(lane.game.starts_at)}</td>
              <td>{etTime(lane.game.checkpoints.decision_window_opens_at)} – {etTime(lane.game.checkpoints.decision_window_closes_at)}</td>
              <td>{lane.game.model ? 'Published' : 'Not published'}</td>
              <td>{PRICE_STATE_LABEL[lane.game.price_state]}</td>
              <td>{lane.positions.length ? lane.positions.map((p) => p.selection).join(', ') : 'None'}</td>
            </tr>
          ))}
        </tbody>
      </ChartTable>
    </div>
  )
})
