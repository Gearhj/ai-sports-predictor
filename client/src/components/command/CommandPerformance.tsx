/**
 * Model Performance: consolidated model-quality evidence, one sport and one
 * model identity at a time.
 *
 *   * A dark header: sport and model selectors, the publication-clock cohort,
 *     the evidence period and sample, then AUC, log loss, Brier (convention
 *     stated), accuracy, margin / total MAE and the model-vs-market deltas.
 *   * One D3 area with four views: calibration, performance over time, model
 *     versus market, and margin / total error distribution.
 *   * A compact ledger of recent scored games.
 *
 * Everything comes from `/research/model-performance?league=`, computed from
 * stored outcome evaluations. Sports are never pooled or ranked together: the
 * read model takes one league, and the page never places two sports' numbers on
 * one scale.
 */
import * as d3 from 'd3'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { SPORT_CODES, SPORT_COLORS, SPORT_NAMES, etTime, expectSchema, gameHref, type SportCode } from '../../services/commandCenter'
import { ChartTable, Tooltip, useTooltip, useWidth } from './chartKit'
import { AsOf } from './RegionState'
import './command.css'

interface Metrics {
  n: number
  period: { first_start_utc: string | null; last_start_utc: string | null }
  accuracy: number | null
  correct_winners: number
  log_loss: number | null
  brier_three_class: number | null
  two_way_n: number
  brier_two_way: number | null
  log_loss_two_way: number | null
  auc: number | null
  margin_mae: number | null
  total_mae: number | null
  margin_errors: number[]
  total_errors: number[]
  market: null | {
    n: number; model_brier_two_way: number; market_brier_two_way: number; delta_brier: number
    model_log_loss_two_way: number; market_log_loss_two_way: number; delta_log_loss: number
  }
  calibration: { lower: number; upper: number; n: number; mean_predicted: number; observed: number }[]
  series: {
    date: string; games: number; cumulative_n: number; cumulative_brier_two_way: number; cumulative_log_loss_two_way: number
    cumulative_market_n: number; cumulative_model_brier_matched: number | null; cumulative_market_brier_matched: number | null
  }[]
}
interface ScoredGame {
  evaluation_id: string
  canonical_game_id: string | null
  start_utc: string | null
  home: string | null
  away: string | null
  final: { home: number; away: number }
  home_probability_two_way: number | null
  predicted_outcome: string | null
  actual_outcome: string | null
  winner_correct: boolean | null
  log_loss: number | null
  margin_error: number | null
  total_error: number | null
  market_no_vig_home: number | null
  market_book: string | null
  publication_clock: string | null
}
interface ModelEvidence {
  engine_id: string
  engine_version: string
  model_label: string
  scoring_version: string
  snapshot_types: string[]
  evidence_classification: string
  cohorts: { ALL: Metrics; VERIFIED_PUBLICATION: Metrics }
  cohort_note: string
  games: ScoredGame[]
}
interface Performance {
  schema_version: string
  generated_at_utc: string
  league: string
  evidence_as_of_utc: string | null
  conventions: Record<string, string>
  models: ModelEvidence[]
  requirement: string | null
}

type Cohort = 'ALL' | 'VERIFIED_PUBLICATION'
type View = 'CALIBRATION' | 'TIME' | 'MARKET' | 'ERRORS'
const VERIFIED = 'VERIFIED_INDEPENDENT_PUBLICATION'
const f = (v: number | null | undefined, d = 3) => (v == null ? '—' : v.toFixed(d))
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const signed = (v: number | null | undefined, d = 3) => {
  if (v == null) return '—'
  const text = Math.abs(v).toFixed(d)
  return Number(text) === 0 ? text : `${v > 0 ? '+' : '−'}${text}`
}
const dateOnly = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' }) : '—')

export function CommandPerformance({ initialSport }: { initialSport?: SportCode }) {
  const [sport, setSport] = useState<SportCode>(initialSport ?? 'MLB')
  const loader = useCallback(async (signal: AbortSignal) =>
    expectSchema(await apiRequest<Performance>(`/research/model-performance?league=${sport}`, {}, signal), 'AISP_MODEL_PERFORMANCE_V1'), [sport])
  const resource = useApiResource<Performance>(loader, { key: cacheKey('model-performance', { league: sport }), staleTimeMs: STALE_TIME.slate })
  const data = resource.data?.league === sport ? resource.data : null
  const [engine, setEngine] = useState<string | null>(null)
  const [cohort, setCohort] = useState<Cohort>('ALL')
  const [view, setView] = useState<View>('CALIBRATION')
  const [showAll, setShowAll] = useState(false)
  const model = data?.models.find((m) => `${m.engine_id}|${m.engine_version}` === engine) ?? data?.models[0] ?? null
  const metrics = model?.cohorts[cohort] ?? null
  const games = useMemo(() => (model?.games ?? []).filter((g) => cohort === 'ALL' || g.publication_clock === VERIFIED), [model, cohort])
  const color = SPORT_COLORS[sport]

  return (
    <section className="cc-perf" aria-label="Model performance">
      <section className="cc-dark cc-perf__head" aria-label="Model quality summary" style={{ ['--sport' as string]: color }}>
        <header className="cc-perf__top">
          <div>
            <h1 className="cc-kicker cc-perf__title">Model performance · {SPORT_NAMES[sport]} · prospective research scores</h1>
            <h2>{model ? model.model_label : `${SPORT_NAMES[sport]} forecasts`}</h2>
            <span className="cc-perf__ident">{model ? `${model.engine_version} · ${model.scoring_version}` : ''}</span>
          </div>
          <div className="cc-perf__asof">
            {data?.evidence_as_of_utc && <span>Evidence as of {etTime(data.evidence_as_of_utc, true)}</span>}
            <AsOf resource={resource} label="scores" />
          </div>
        </header>

        <div className="cc-perf__controls">
          <div className="cc-tabs cc-tabs--dark" role="tablist" aria-label="Sport">
            {SPORT_CODES.map((code) => (
              <button key={code} role="tab" type="button" aria-selected={sport === code}
                style={{ ['--sport' as string]: SPORT_COLORS[code] }}
                onClick={() => { setSport(code); setEngine(null); setShowAll(false) }}>
                <i className="cc-dot" />{SPORT_NAMES[code]}
              </button>
            ))}
          </div>
          {data && data.models.length > 0 && (
            <label className="cc-perf__select">
              <span>Model</span>
              <select value={model ? `${model.engine_id}|${model.engine_version}` : ''} onChange={(e) => setEngine(e.target.value)}>
                {data.models.map((m) => (
                  <option key={`${m.engine_id}|${m.engine_version}`} value={`${m.engine_id}|${m.engine_version}`}>
                    {m.model_label} · {m.engine_version.slice(-12)} · n={m.cohorts.ALL.n}
                  </option>
                ))}
              </select>
            </label>
          )}
          {model && (
            <div className="cc-filters" role="group" aria-label="Evidence cohort">
              <button type="button" aria-pressed={cohort === 'ALL'} onClick={() => setCohort('ALL')}>All scored · {model.cohorts.ALL.n}</button>
              <button type="button" aria-pressed={cohort === 'VERIFIED_PUBLICATION'} onClick={() => setCohort('VERIFIED_PUBLICATION')}>Verified publication clock · {model.cohorts.VERIFIED_PUBLICATION.n}</button>
            </div>
          )}
        </div>

        {!data
          ? <p className="cc-muted" role="status">{resource.error ? `Performance evidence is unavailable: ${resource.error}` : 'Loading scored forecasts…'}</p>
          : !model || !metrics
            ? <p className="cc-perf__none">{data.requirement ?? `No ${SPORT_NAMES[sport]} forecast has been scored.`}</p>
            : (
              <dl className="cc-perf__tiles">
                <div className="cc-perf__tile--wide"><dt>Evidence period · sample</dt>
                  <dd>{metrics.n} games</dd>
                  <small>{dateOnly(metrics.period.first_start_utc)} – {dateOnly(metrics.period.last_start_utc)} · {model.snapshot_types.join(', ').toLowerCase()} · {cohort === 'ALL' ? 'all scored' : 'verified clock only'}</small></div>
                <div><dt>AUC</dt><dd>{f(metrics.auc)}</dd><small>{metrics.auc == null ? 'undefined: needs both outcomes' : `home win, n=${metrics.two_way_n}`}</small></div>
                <div><dt>Log loss</dt><dd>{f(metrics.log_loss)}</dd><small>as scored · two-way {f(metrics.log_loss_two_way)}</small></div>
                <div><dt>Brier · three-class</dt><dd>{f(metrics.brier_three_class)}</dd><small>range 0–2 · two-way {f(metrics.brier_two_way)}</small></div>
                <div><dt>Accuracy</dt><dd>{pct(metrics.accuracy)}</dd><small>{metrics.correct_winners} of {metrics.n} winners</small></div>
                <div><dt>{sport === 'MLB' ? 'Run margin' : 'Margin'} MAE</dt><dd>{f(metrics.margin_mae, 2)}</dd><small>total MAE {f(metrics.total_mae, 2)}</small></div>
                <div className="cc-perf__tile--wide"><dt>Model − market · same games</dt>
                  <dd className={metrics.market ? (metrics.market.delta_brier < 0 ? 'is-up' : metrics.market.delta_brier > 0 ? 'is-down' : '') : ''}>
                    {metrics.market ? `${signed(metrics.market.delta_brier)} Brier` : '—'}</dd>
                  <small>{metrics.market ? `${signed(metrics.market.delta_log_loss)} log loss · n=${metrics.market.n} · two-way, negative favours the model` : 'no matched sportsbook quote'}</small></div>
              </dl>
            )}
        {model && <p className="cc-perf__note">{model.cohort_note} Descriptive only; nothing here promotes a model, changes a threshold or creates a position. Metrics are defined within {SPORT_NAMES[sport]} and are not comparable across sports.</p>}
      </section>

      {model && metrics && (
        <section className="cc-mpanel cc-perf__area" aria-label="Performance charts">
          <div className="cc-perf__areahead">
            <div className="cc-tabs" role="tablist" aria-label="Chart view">
              {([['CALIBRATION', 'Calibration'], ['TIME', 'Over time'], ['MARKET', 'Model vs market'], ['ERRORS', 'Score error']] as const).map(([key, label]) => (
                <button key={key} role="tab" type="button" aria-selected={view === key} onClick={() => setView(key)}>{label}</button>
              ))}
            </div>
            <span className="cc-note" style={{ margin: 0 }}>{VIEW_NOTE[view]}</span>
          </div>
          {view === 'CALIBRATION' && <CalibrationChart metrics={metrics} color={color} />}
          {view === 'TIME' && <TimeChart metrics={metrics} color={color} />}
          {view === 'MARKET' && <MarketScatter games={games} color={color} />}
          {view === 'ERRORS' && <ErrorHistogram metrics={metrics} sport={sport} color={color} />}
        </section>
      )}

      {model && games.length > 0 && (
        <section className="cc-mpanel" aria-label="Recent scored games">
          <h3>Recent scored games · {games.length}</h3>
          <div className="cc-table__scroll">
            <table className="cc-board cc-perf__ledger">
              <thead><tr><th>Date</th><th>Game</th><th>Final</th><th>Model home</th><th>Market home</th><th>Winner</th><th>Log loss</th><th>Margin err</th><th>Total err</th><th>Clock</th></tr></thead>
              <tbody>
                {(showAll ? games : games.slice(0, 10)).map((g) => (
                  <tr key={g.evaluation_id}>
                    <td>{dateOnly(g.start_utc)}</td>
                    <td>{g.canonical_game_id ? <Link className="cc-linkish" to={gameHref(sport, g.canonical_game_id)}>{g.away} @ {g.home}</Link> : `${g.away} @ ${g.home}`}</td>
                    <td>{g.final.away}–{g.final.home}</td>
                    <td>{pct(g.home_probability_two_way)}</td>
                    <td>{pct(g.market_no_vig_home)}</td>
                    <td className={g.winner_correct ? 'is-up' : 'is-down'}>{g.winner_correct ? 'Correct' : 'Missed'}</td>
                    <td>{f(g.log_loss)}</td>
                    <td>{signed(g.margin_error, 1)}</td>
                    <td>{signed(g.total_error, 1)}</td>
                    <td>{g.publication_clock === VERIFIED ? 'verified' : 'qualified'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {games.length > 10 && <button type="button" className="cc-more" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show fewer' : `Show all ${games.length}`}</button>}
        </section>
      )}

      {data && (
        <details className="cc-below cc-perf__defs">
          <summary>Metric definitions</summary>
          <dl>{Object.entries(data.conventions).map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd>{v}</dd></div>)}</dl>
        </details>
      )}
    </section>
  )
}

const VIEW_NOTE: Record<View, string> = {
  CALIBRATION: 'Two-way home-win probability, 10 bins · marker area is sample size',
  TIME: 'Cumulative two-way Brier by date · lower is better',
  MARKET: 'Each game: model vs sportsbook no-vig home probability',
  ERRORS: 'Predicted minus actual',
}

const CalibrationChart = memo(function CalibrationChart({ metrics, color }: { metrics: Metrics; color: string }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)
  const height = 300
  const m = { top: 12, right: 16, bottom: 34, left: 46 }
  const x = d3.scaleLinear().domain([0, 1]).range([m.left, width - m.right])
  const y = d3.scaleLinear().domain([0, 1]).range([height - m.bottom, m.top])
  const r = d3.scaleSqrt().domain([0, d3.max(metrics.calibration, (b) => b.n) ?? 1]).range([0, 16])
  useEffect(() => {
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(5).tickFormat((v) => d3.format('.0%')(v as number)) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5).tickFormat((v) => d3.format('.0%')(v as number)) as never)
  }, [x, y])
  if (!metrics.calibration.length) return <p className="cc-muted">No two-way probabilities to calibrate.</p>
  const tipFor = (b: Metrics['calibration'][number]) => <><b>{pct(b.lower, 0)}–{pct(b.upper, 0)} forecasts</b><dl><dt>Games</dt><dd>{b.n}</dd><dt>Mean forecast</dt><dd>{pct(b.mean_predicted)}</dd><dt>Home won</dt><dd>{pct(b.observed)}</dd></dl></>
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Calibration across ${metrics.calibration.length} bins`}>
        <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} className="cc-perf__diag" />
        <text x={width - m.right} y={height - 4} textAnchor="end" className="cc-muted-text">Forecast home-win probability</text>
        <text x={m.left + 4} y={m.top + 10} className="cc-muted-text">Observed home-win rate</text>
        <path d={d3.line<Metrics['calibration'][number]>().x((b) => x(b.mean_predicted)).y((b) => y(b.observed))(metrics.calibration) ?? ''} fill="none" stroke={color} strokeWidth={1.5} opacity={0.5} />
        {metrics.calibration.map((b) => (
          <circle key={b.lower} cx={x(b.mean_predicted)} cy={y(b.observed)} r={Math.max(4, r(b.n))} fill={color} fillOpacity={0.55} stroke={color}
            className="cc-perf__mark" tabIndex={0} aria-label={`${b.n} games forecast ${pct(b.mean_predicted)}; home won ${pct(b.observed)}`}
            onMouseMove={(e) => show(e, tipFor(b))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(b))} onBlur={hide} />
        ))}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ChartTable caption="Calibration bins">
        <thead><tr><th>Bin</th><th>Games</th><th>Mean forecast</th><th>Observed</th></tr></thead>
        <tbody>{metrics.calibration.map((b) => <tr key={b.lower}><td>{pct(b.lower, 0)}–{pct(b.upper, 0)}</td><td>{b.n}</td><td>{pct(b.mean_predicted)}</td><td>{pct(b.observed)}</td></tr>)}</tbody>
      </ChartTable>
    </div>
  )
})

const TimeChart = memo(function TimeChart({ metrics, color }: { metrics: Metrics; color: string }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)
  const height = 300
  const m = { top: 12, right: 16, bottom: 34, left: 50 }
  const pts = metrics.series.map((p) => ({ ...p, t: new Date(`${p.date}T12:00:00Z`) }))
  const vals = pts.flatMap((p) => [p.cumulative_brier_two_way, p.cumulative_market_brier_matched]).filter((v): v is number => v != null)
  const x = d3.scaleTime().domain(d3.extent(pts, (p) => p.t) as [Date, Date]).range([m.left, width - m.right])
  const y = d3.scaleLinear().domain([Math.max(0, (d3.min(vals) ?? 0.2) - 0.02), (d3.max(vals) ?? 0.3) + 0.02]).range([height - m.bottom, m.top])
  useEffect(() => {
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(Math.min(pts.length, 7)).tickFormat((v) => d3.timeFormat('%b %d')(v as Date)) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5) as never)
  }, [x, y, pts.length])
  if (pts.length < 2) return <p className="cc-muted">Scores span a single day so far; a trend needs at least two.</p>
  const line = (key: 'cumulative_brier_two_way' | 'cumulative_market_brier_matched') =>
    d3.line<(typeof pts)[number]>().defined((p) => p[key] != null).x((p) => x(p.t)).y((p) => y(p[key] as number))(pts) ?? ''
  const tipFor = (p: (typeof pts)[number]) => <><b>{dateOnly(`${p.date}T16:00:00Z`)}</b><dl><dt>Games that day</dt><dd>{p.games}</dd><dt>Cumulative n</dt><dd>{p.cumulative_n}</dd><dt>Model Brier (two-way)</dt><dd>{f(p.cumulative_brier_two_way)}</dd><dt>Log loss (two-way)</dt><dd>{f(p.cumulative_log_loss_two_way)}</dd>{p.cumulative_market_n > 0 && <><dt>Matched market n</dt><dd>{p.cumulative_market_n}</dd><dt>Model · market Brier</dt><dd>{f(p.cumulative_model_brier_matched)} · {f(p.cumulative_market_brier_matched)}</dd></>}</dl></>
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Cumulative Brier over ${pts.length} days`}>
        <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        <path d={line('cumulative_market_brier_matched')} fill="none" stroke="var(--bx-text, #10203a)" strokeWidth={1.8} strokeDasharray="5 3" />
        <path d={line('cumulative_brier_two_way')} fill="none" stroke={color} strokeWidth={2.5} />
        {pts.map((p) => (
          <circle key={p.date} cx={x(p.t)} cy={y(p.cumulative_brier_two_way)} r={4.5} fill={color} className="cc-perf__mark" tabIndex={0}
            aria-label={`${p.date}: cumulative Brier ${f(p.cumulative_brier_two_way)} over ${p.cumulative_n} games`}
            onMouseMove={(e) => show(e, tipFor(p))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(p))} onBlur={hide} />
        ))}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key" style={{ background: color }} />Model, all games in cohort</li>
        <li><i className="cc-key cc-key--model" style={{ borderTopColor: 'var(--bx-text, #10203a)' }} />Sportsbook no-vig, matched games only</li>
      </ul>
      <ChartTable caption="Cumulative scores by date">
        <thead><tr><th>Date</th><th>Games</th><th>Cumulative n</th><th>Brier (2-way)</th><th>Log loss (2-way)</th><th>Market Brier (matched)</th></tr></thead>
        <tbody>{pts.map((p) => <tr key={p.date}><td>{p.date}</td><td>{p.games}</td><td>{p.cumulative_n}</td><td>{f(p.cumulative_brier_two_way)}</td><td>{f(p.cumulative_log_loss_two_way)}</td><td>{f(p.cumulative_market_brier_matched)}</td></tr>)}</tbody>
      </ChartTable>
    </div>
  )
})

const MarketScatter = memo(function MarketScatter({ games, color }: { games: ScoredGame[]; color: string }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)
  const rows = games.filter((g) => g.market_no_vig_home != null && g.home_probability_two_way != null)
  const height = 300
  const m = { top: 12, right: 16, bottom: 34, left: 46 }
  const vals = rows.flatMap((g) => [g.market_no_vig_home as number, g.home_probability_two_way as number])
  const lo = Math.max(0, (d3.min(vals) ?? 0.3) - 0.05)
  const hi = Math.min(1, (d3.max(vals) ?? 0.7) + 0.05)
  const x = d3.scaleLinear().domain([lo, hi]).range([m.left, width - m.right])
  const y = d3.scaleLinear().domain([lo, hi]).range([height - m.bottom, m.top])
  useEffect(() => {
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(5).tickFormat((v) => d3.format('.0%')(v as number)) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5).tickFormat((v) => d3.format('.0%')(v as number)) as never)
  }, [x, y])
  if (!rows.length) return <p className="cc-muted">No scored game in this cohort has a matched sportsbook quote.</p>
  const tipFor = (g: ScoredGame) => <><b>{g.away} @ {g.home}</b> · {dateOnly(g.start_utc)}<dl><dt>Model home</dt><dd>{pct(g.home_probability_two_way)}</dd><dt>Market home (no-vig)</dt><dd>{pct(g.market_no_vig_home)}</dd><dt>Final</dt><dd>{g.final.away}–{g.final.home} · {g.actual_outcome === 'HOME' ? 'home won' : 'away won'}</dd><dt>Quote</dt><dd>{g.market_book ?? '—'} · last pre-start, not a closing line</dd></dl></>
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={`Model versus market for ${rows.length} games`}>
        <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        <line x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} className="cc-perf__diag" />
        <text x={width - m.right} y={height - 4} textAnchor="end" className="cc-muted-text">Sportsbook no-vig home probability</text>
        <text x={m.left + 4} y={m.top + 10} className="cc-muted-text">Model home probability</text>
        {rows.map((g) => (
          <circle key={g.evaluation_id} cx={x(g.market_no_vig_home as number)} cy={y(g.home_probability_two_way as number)} r={5.5}
            fill={g.actual_outcome === 'HOME' ? color : 'transparent'} stroke={color} strokeWidth={1.8} className="cc-perf__mark" tabIndex={0}
            aria-label={`${g.away} at ${g.home}: model ${pct(g.home_probability_two_way)}, market ${pct(g.market_no_vig_home)}, ${g.actual_outcome === 'HOME' ? 'home won' : 'away won'}`}
            onMouseMove={(e) => show(e, tipFor(g))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(g))} onBlur={hide} />
        ))}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key cc-key--forecast" style={{ background: color }} />Home won</li>
        <li><i className="cc-key cc-key--none" style={{ borderColor: color }} />Away won</li>
        <li>Above the diagonal: model rated the home side higher than the market</li>
      </ul>
      <ChartTable caption="Model and market by game">
        <thead><tr><th>Game</th><th>Model</th><th>Market</th><th>Result</th></tr></thead>
        <tbody>{rows.map((g) => <tr key={g.evaluation_id}><td>{g.away} @ {g.home}</td><td>{pct(g.home_probability_two_way)}</td><td>{pct(g.market_no_vig_home)}</td><td>{g.actual_outcome}</td></tr>)}</tbody>
      </ChartTable>
    </div>
  )
})

const ErrorHistogram = memo(function ErrorHistogram({ metrics, sport, color }: { metrics: Metrics; sport: SportCode; color: string }) {
  const [kind, setKind] = useState<'margin' | 'total'>('margin')
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const xAxis = useRef<SVGGElement | null>(null)
  const yAxis = useRef<SVGGElement | null>(null)
  const values = kind === 'margin' ? metrics.margin_errors : metrics.total_errors
  const height = 280
  const m = { top: 12, right: 16, bottom: 34, left: 40 }
  const extent = d3.extent(values) as [number, number]
  const span = Math.max(Math.abs(extent[0] ?? 1), Math.abs(extent[1] ?? 1))
  const step = sport === 'MLB' ? 1 : 3
  const x = d3.scaleLinear().domain([-Math.ceil(span / step) * step - step, Math.ceil(span / step) * step + step]).range([m.left, width - m.right])
  const bins = d3.bin().domain(x.domain() as [number, number]).thresholds(d3.range(x.domain()[0], x.domain()[1] + step, step))(values)
  const y = d3.scaleLinear().domain([0, d3.max(bins, (b) => b.length) ?? 1]).nice().range([height - m.bottom, m.top])
  useEffect(() => {
    if (xAxis.current) d3.select(xAxis.current).call(d3.axisBottom(x).ticks(9) as never)
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(4).tickFormat(d3.format('d')) as never)
  }, [x, y])
  const unit = sport === 'MLB' ? 'runs' : 'points'
  const mean = values.length ? d3.mean(values)! : null
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <div className="cc-filters" role="group" aria-label="Error quantity" style={{ marginBottom: 6 }}>
        <button type="button" aria-pressed={kind === 'margin'} onClick={() => setKind('margin')}>Margin (home − away)</button>
        <button type="button" aria-pressed={kind === 'total'} onClick={() => setKind('total')}>Total</button>
      </div>
      {!values.length ? <p className="cc-muted">No score projection was scored for this cohort.</p> : (
        <svg width={width} height={height} role="img" aria-label={`${kind} error distribution over ${values.length} games`}>
          <g ref={xAxis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
          <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
          {bins.map((b, i) => b.length > 0 && (
            <rect key={i} x={x(b.x0 as number) + 1} y={y(b.length)} width={Math.max(1, x(b.x1 as number) - x(b.x0 as number) - 2)} height={y(0) - y(b.length)}
              fill={color} fillOpacity={0.7} className="cc-perf__mark" tabIndex={0}
              aria-label={`${b.length} games with ${kind} error from ${b.x0} to ${b.x1} ${unit}`}
              onMouseMove={(e) => show(e, <><b>{b.x0} to {b.x1} {unit}</b><dl><dt>Games</dt><dd>{b.length}</dd></dl></>)} onMouseLeave={hide}
              onFocus={(e) => show(e, <><b>{b.x0} to {b.x1} {unit}</b><dl><dt>Games</dt><dd>{b.length}</dd></dl></>)} onBlur={hide} />
          ))}
          <line x1={x(0)} x2={x(0)} y1={m.top} y2={height - m.bottom} className="cc-perf__zero" />
          {mean != null && <text x={width - m.right} y={m.top + 10} textAnchor="end" className="cc-muted-text">mean {signed(mean, 2)} · MAE {f(kind === 'margin' ? metrics.margin_mae : metrics.total_mae, 2)} {unit} · n={values.length}</text>}
          <text x={width - m.right} y={height - 4} textAnchor="end" className="cc-muted-text">Predicted minus actual ({unit})</text>
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  )
})
