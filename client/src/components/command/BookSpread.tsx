/**
 * One game's persisted sportsbook quotes, book by book: each book's no-vig home
 * probability (moneyline) as a dot, the consensus as a rule, the model's
 * two-way home probability as a separate marked rule, and the spread of books
 * as a range. Staleness fades a book rather than hiding it, and the page says
 * which are current. Model output is drawn as a model marker, never as a book.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef } from 'react'
import {
  PRICE_STATE_LABEL, relative, type CoverageGame, type CoverageQuote,
} from '../../services/commandCenter'
import { ChartTable, Tooltip, useTooltip, useWidth } from './chartKit'

const american = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const shortBook = (name: string | null) => (name ?? 'Unknown book').replace(/\s*\(.*\)\s*$/, '')

export const BookSpread = memo(function BookSpread({ game, market }: { game: CoverageGame; market: 'MONEYLINE' | 'SPREAD' | 'TOTAL' }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axis = useRef<SVGGElement | null>(null)
  const quotes = useMemo(() => game.quotes.filter((q) => q.market === market), [game, market])
  const home = game.home?.abbreviation ?? 'Home'
  const away = game.away?.abbreviation ?? 'Away'

  // Moneyline plots probability; spread and total plot the line itself.
  const valueOf = (q: CoverageQuote): number | null => market === 'MONEYLINE' ? (q.no_vig_home ?? null) : q.line
  const plotted = quotes.filter((q) => valueOf(q) != null)
  const rowH = 26
  const m = { top: 30, right: 20, bottom: 12, left: width < 560 ? 92 : 150 }
  const height = m.top + Math.max(1, plotted.length) * rowH + m.bottom
  const values = plotted.map((q) => valueOf(q) as number)
  const modelValue = market === 'MONEYLINE' ? game.model?.home_two_way ?? null : null
  const domainValues = [...values, ...(modelValue != null ? [modelValue] : [])]
  const [lo, hi] = d3.extent(domainValues) as [number | undefined, number | undefined]
  const span = lo == null || hi == null ? 1 : Math.max(market === 'MONEYLINE' ? 0.04 : 1, hi - lo)
  const x = d3.scaleLinear()
    .domain(lo == null || hi == null ? [0, 1] : [lo - span * 0.25, hi + span * 0.25])
    .range([m.left, width - m.right])
  const consensus = market === 'MONEYLINE' ? game.consensus_no_vig_home : (values.length ? d3.median(values) ?? null : null)

  useEffect(() => {
    if (!axis.current) return
    const fmt = market === 'MONEYLINE' ? d3.format('.0%') : d3.format('+.1f')
    d3.select(axis.current).call(d3.axisTop(x).ticks(width < 560 ? 4 : 6).tickFormat((v) => fmt(v as number)) as never)
  }, [x, width, market])

  if (quotes.length === 0) {
    return (
      <p className="cc-empty">
        {PRICE_STATE_LABEL[game.price_state]} for {game.matchup} ({market.toLowerCase()}).{' '}
        Collection opens {relative(game.checkpoints.collection_opens_at)}; the decision window opens{' '}
        {relative(game.checkpoints.decision_window_opens_at)}.
      </p>
    )
  }

  const tipFor = (q: CoverageQuote) => (
    <>
      <b>{shortBook(q.sportsbook)}</b> · {q.provider} ({q.classification.toLowerCase()})
      <dl>
        {market === 'MONEYLINE'
          ? <><dt>{away}</dt><dd>{american(q.sides.AWAY?.american)}</dd><dt>{home}</dt><dd>{american(q.sides.HOME?.american)}</dd><dt>No-vig {home}</dt><dd>{pct(q.no_vig_home)}</dd></>
          : market === 'SPREAD'
            ? <><dt>{home} line</dt><dd>{q.line}</dd><dt>Prices</dt><dd>{american(q.sides.HOME?.american)} / {american(q.sides.AWAY?.american)}</dd></>
            : <><dt>Total</dt><dd>{q.line}</dd><dt>Over / under</dt><dd>{american(q.sides.OVER?.american)} / {american(q.sides.UNDER?.american)}</dd></>}
        <dt>Observed</dt><dd>{relative(q.observed_at)} (older side)</dd>
        <dt>Freshness</dt><dd>{q.fresh ? 'Current' : 'Stale · reference only'}</dd>
      </dl>
    </>
  )

  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img"
        aria-label={`${quotes.length} sportsbook quotes for ${game.matchup}, ${market.toLowerCase()}`}>
        <g ref={axis} className="cc-axis" transform={`translate(0, ${m.top - 8})`} />
        {values.length > 1 && (
          <rect x={x(d3.min(values)!)} width={Math.max(2, x(d3.max(values)!) - x(d3.min(values)!))}
            y={m.top - 4} height={plotted.length * rowH + 4} className="cc-range" />
        )}
        {consensus != null && (
          <g>
            <line x1={x(consensus)} x2={x(consensus)} y1={m.top - 4} y2={height - m.bottom} className="cc-consensus" />
          </g>
        )}
        {modelValue != null && (
          <g>
            <line x1={x(modelValue)} x2={x(modelValue)} y1={m.top - 4} y2={height - m.bottom} className="cc-modelline" />
          </g>
        )}
        {plotted.map((q, i) => {
          const cy = m.top + i * rowH + rowH / 2
          return (
            <g key={`${q.sportsbook_key}-${q.market}`} tabIndex={0} className="cc-book"
              aria-label={`${shortBook(q.sportsbook)}: ${market === 'MONEYLINE' ? `${away} ${american(q.sides.AWAY?.american)}, ${home} ${american(q.sides.HOME?.american)}` : `line ${q.line}`}, ${q.fresh ? 'current' : 'stale'}`}
              onMouseMove={(e) => show(e, tipFor(q))} onMouseLeave={hide}
              onFocus={(e) => show(e, tipFor(q))} onBlur={hide}>
              <rect x={0} y={cy - rowH / 2} width={width} height={rowH} className="cc-lane__hit" />
              <text x={m.left - 10} y={cy + 4} textAnchor="end" className="cc-lane__label">{shortBook(q.sportsbook)}</text>
              <circle cx={x(valueOf(q) as number)} cy={cy} r={6} className={q.fresh ? 'cc-bookdot' : 'cc-bookdot cc-bookdot--stale'} />
              {market === 'MONEYLINE' && width >= 560 && (
                <text x={x(valueOf(q) as number) + 11} y={cy + 4} className="cc-muted-text">
                  {american(q.sides.AWAY?.american)} / {american(q.sides.HOME?.american)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key cc-key--book" />Book (faded = stale, reference only)</li>
        {consensus != null && <li><i className="cc-key cc-key--consensus" />{market === 'MONEYLINE' ? `Consensus no-vig ${home} ${pct(consensus)}` : `Median line ${consensus}`}</li>}
        {modelValue != null && <li><i className="cc-key cc-key--model" />Model {home} {pct(modelValue)} (model output, not a price)</li>}
        {game.best.HOME && game.best.AWAY && <li>Best current: {away} {american(game.best.AWAY.american)} ({shortBook(game.best.AWAY.sportsbook)}) · {home} {american(game.best.HOME.american)} ({shortBook(game.best.HOME.sportsbook)})</li>}
      </ul>
      <ChartTable caption={`${game.matchup} ${market.toLowerCase()} quotes by book`}>
        <thead><tr><th>Book</th><th>Provider</th><th>{market === 'TOTAL' ? 'Over / under' : `${away} / ${home}`}</th><th>Line</th><th>No-vig {home}</th><th>Observed</th><th>Current</th></tr></thead>
        <tbody>
          {quotes.map((q) => (
            <tr key={`${q.sportsbook_key}-${q.market}`}>
              <td>{shortBook(q.sportsbook)}</td><td>{q.provider} · {q.classification.toLowerCase()}</td>
              <td>{market === 'TOTAL' ? `${american(q.sides.OVER?.american)} / ${american(q.sides.UNDER?.american)}` : `${american(q.sides.AWAY?.american)} / ${american(q.sides.HOME?.american)}`}</td>
              <td>{q.line ?? '—'}</td><td>{pct(q.no_vig_home)}</td><td>{relative(q.observed_at)}</td><td>{q.fresh ? 'Yes' : 'No'}</td>
            </tr>
          ))}
        </tbody>
      </ChartTable>
    </div>
  )
})
