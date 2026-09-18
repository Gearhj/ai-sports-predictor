/**
 * Model probability against the no-vig market probability, one mark per
 * governed evaluation that actually had both numbers. Above the diagonal the
 * model rates the selection higher than the market (a positive edge); below,
 * lower. Evaluations with no quote have no market probability and are counted,
 * not plotted -- a point on this chart always means a real persisted price.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  SPORT_COLORS, SPORT_NAMES, gameHref, type DecisionGroup, type DecisionItem, type SportCode,
} from '../../services/commandCenter'
import { ChartTable, Tooltip, activate, useReducedMotion, useTooltip, useWidth } from './chartKit'
import { advantageOverNoVig, controllingTest, ptsText, testThresholdText, testValueText } from '../../services/decisionEdges'

const GROUP_LABEL: Record<DecisionGroup, string> = {
  READY: 'Ready now', WATCHING: 'Watching', POSITION: 'Position', PASSED: 'Passed', CLOSED: 'Closed',
}
const pct = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

export const EdgeMap = memo(function EdgeMap({ items }: { items: DecisionItem[] }) {
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const [sport, setSport] = useState<SportCode | 'ALL'>('ALL')
  const [group, setGroup] = useState<DecisionGroup | 'ALL'>('ALL')
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)

  const plotted = useMemo(() => items.filter((i) =>
    i.model_probability != null && i.market_probability != null
    && (sport === 'ALL' || i.sport === sport) && (group === 'ALL' || i.group === group)), [items, sport, group])
  const unplotted = items.filter((i) => i.model_probability == null || i.market_probability == null).length
  const sports = Array.from(new Set(items.map((i) => i.sport)))

  const size = Math.min(width, 520)
  const height = Math.max(240, Math.min(420, size))
  const m = { top: 12, right: 14, bottom: 38, left: 46 }
  const lo = Math.max(0, (d3.min(plotted, (d) => Math.min(d.model_probability!, d.market_probability!)) ?? 0.2) - 0.05)
  const hi = Math.min(1, (d3.max(plotted, (d) => Math.max(d.model_probability!, d.market_probability!)) ?? 0.8) + 0.05)
  const x = d3.scaleLinear().domain([lo, hi]).range([m.left, width - m.right])
  const y = d3.scaleLinear().domain([lo, hi]).range([height - m.bottom, m.top])

  useEffect(() => {
    const fmt = d3.format('.0%')
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(width < 500 ? 4 : 6).tickFormat((v) => fmt(v as number)) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5).tickFormat((v) => fmt(v as number)) as never)
  }, [x, y, width])

  const tipFor = (d: DecisionItem) => (
    <>
      <b>{d.selection}</b> · {d.matchup} · {SPORT_NAMES[d.sport]}
      <dl>
        <dt>Model</dt><dd>{pct(d.model_probability)}</dd>
        <dt>Market (no-vig)</dt><dd>{pct(d.market_probability)}</dd>
        <dt>Model − no-vig</dt><dd>{ptsText(advantageOverNoVig(d))}</dd>
        {controllingTest(d) && <><dt>Policy test</dt><dd>{testValueText(controllingTest(d)!)} ({testThresholdText(controllingTest(d)!)})</dd></>}
        <dt>Price</dt><dd>{d.american == null ? '—' : `${d.american > 0 ? '+' : ''}${d.american}`}{d.sportsbook ? ` · ${d.sportsbook}` : ''}</dd>
        <dt>Evaluation</dt><dd>{d.recommendation.replace(/_/g, ' ').toLowerCase()} · {d.phase.replace(/_/g, ' ').toLowerCase()}</dd>
        <dt>Status</dt><dd>{GROUP_LABEL[d.group]}{d.position?.stake != null ? ` · $${d.position.stake.toFixed(2)}` : ''}</dd>
      </dl>
    </>
  )

  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <div className="cc-filters" role="group" aria-label="Filter the edge map">
        {(['ALL', ...sports] as const).map((s) => (
          <button key={s} type="button" aria-pressed={sport === s} onClick={() => setSport(s as SportCode | 'ALL')}>
            {s === 'ALL' ? 'All sports' : SPORT_NAMES[s as SportCode]}
          </button>
        ))}
        <span className="cc-filters__sep" aria-hidden="true" />
        {(['ALL', 'READY', 'POSITION', 'PASSED', 'CLOSED'] as const).map((g) => (
          <button key={g} type="button" aria-pressed={group === g} onClick={() => setGroup(g)}>
            {g === 'ALL' ? 'Every status' : GROUP_LABEL[g]}
          </button>
        ))}
      </div>
      {plotted.length === 0 ? (
        <p className="cc-empty">
          No evaluation in this view has both a model probability and a persisted market price yet
          {unplotted ? ` (${unplotted} evaluation${unplotted === 1 ? '' : 's'} waiting on a price).` : '.'}
        </p>
      ) : (
        <svg width={width} height={height} role="img"
          aria-label={`${plotted.length} evaluations plotted: model probability against market probability`}>
          <defs>
            <linearGradient id="cc-edge-up" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0" stopColor="var(--bx-good)" stopOpacity="0" />
              <stop offset="1" stopColor="var(--bx-good)" stopOpacity=".10" />
            </linearGradient>
          </defs>
          <path d={`M${x(lo)},${y(lo)} L${x(hi)},${y(hi)} L${x(lo)},${y(hi)} Z`} fill="url(#cc-edge-up)" />
          <line x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} className="cc-diagonal" />
          <text x={x(lo) + 6} y={y(hi) + 14} className="cc-muted-text">Model above market</text>
          <text x={x(hi) - 6} y={y(lo) - 8} textAnchor="end" className="cc-muted-text">Model below market</text>
          <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
          <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
          <text x={(width + m.left) / 2} y={height - 4} textAnchor="middle" className="cc-axis-title">Market probability (no-vig)</text>
          <text transform={`translate(12, ${(height - m.bottom) / 2}) rotate(-90)`} textAnchor="middle" className="cc-axis-title">Model probability</text>
          {plotted.map((d) => {
            const positive = (d.model_probability ?? 0) >= (d.market_probability ?? 0)
            const go = () => navigate(gameHref(d.sport, d.game_id))
            return (
              <g key={d.decision_id} tabIndex={0} role="link" className="cc-dot"
                transform={`translate(${x(d.market_probability!)}, ${y(d.model_probability!)})`}
                aria-label={`${d.selection}, ${d.matchup}: model ${pct(d.model_probability)}, market ${pct(d.market_probability)}, ${GROUP_LABEL[d.group]}`}
                onMouseMove={(e) => show(e, tipFor(d))} onMouseLeave={hide}
                onFocus={(e) => show(e, tipFor(d))} onBlur={hide} {...activate(go)}>
                <line x1={0} x2={0} y1={0} y2={y(d.market_probability!) - y(d.model_probability!)}
                  className={positive ? 'cc-stem cc-stem--up' : 'cc-stem cc-stem--down'} />
                <circle r={d.position ? 7 : 5} fill={SPORT_COLORS[d.sport]}
                  className={`cc-dot__mark cc-dot__mark--${d.group}${reduced ? '' : ' cc-anim'}`} />
              </g>
            )
          })}
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
      <p className="cc-note">
        Larger rings are recorded positions. Stems run from the market line to the model; green above, red below.
        {unplotted ? ` ${unplotted} evaluation${unplotted === 1 ? ' has' : 's have'} no persisted price and ${unplotted === 1 ? 'is' : 'are'} not plotted.` : ''}
      </p>
      <ChartTable caption="Evaluations with model and market probabilities">
        <thead><tr><th>Sport</th><th>Game</th><th>Selection</th><th>Model</th><th>Market</th><th>Model − no-vig</th><th>Status</th></tr></thead>
        <tbody>
          {plotted.map((d) => (
            <tr key={d.decision_id}>
              <td>{d.sport}</td><td>{d.matchup}</td><td>{d.selection}</td>
              <td>{pct(d.model_probability)}</td><td>{pct(d.market_probability)}</td>
              <td>{ptsText(advantageOverNoVig(d))}</td><td>{GROUP_LABEL[d.group]}</td>
            </tr>
          ))}
        </tbody>
      </ChartTable>
    </div>
  )
})
