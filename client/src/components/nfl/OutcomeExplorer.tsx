/**
 * NFL Outcome Explorer — the imported joint final-score evidence, explored.
 *
 * Every figure is read off ONE persisted joint distribution: the outcome
 * probabilities, the expected scores, the heatmap, the margin and total
 * distributions, the quantiles and every spread/total query. A hypothetical
 * line changes only which cells are summed; it never changes the forecast.
 *
 * The scoreboard shows a SAMPLED final outcome — a draw from the model's own
 * distribution, labelled as such, because there is no sequential path here.
 */
import ReactECharts from 'echarts-for-react'
import { Dices, Info } from 'lucide-react'
import { useMemo, useState } from 'react'
import { FootballField } from './FootballField'
import { MetricCard } from '../ui'
import type { NflExplorer } from '../../types/nfl'

const AXIS = { color: '#6d7d91', fontSize: 11 }

export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : `${(value * 100).toFixed(digits)}%`
}

export interface SpreadResult { home_cover: number; away_cover: number; push: number }
export interface TotalResult { over: number; under: number; push: number }

/** Home covers when (home − away) + line > 0; an exact zero is a push. */
export function spreadQuery(cells: NflExplorer['joint_score_distribution'], homeLine: number): SpreadResult {
  const out: SpreadResult = { home_cover: 0, away_cover: 0, push: 0 }
  for (const cell of cells) {
    const margin = cell.home - cell.away + homeLine
    if (margin > 0) out.home_cover += cell.probability
    else if (margin < 0) out.away_cover += cell.probability
    else out.push += cell.probability
  }
  return out
}

export function totalQuery(cells: NflExplorer['joint_score_distribution'], line: number): TotalResult {
  const out: TotalResult = { over: 0, under: 0, push: 0 }
  for (const cell of cells) {
    const total = cell.home + cell.away
    if (total > line) out.over += cell.probability
    else if (total < line) out.under += cell.probability
    else out.push += cell.probability
  }
  return out
}

/**
 * Deterministic draw from the distribution: the k-th point of a low-discrepancy
 * sequence seeded by the producer's own seed, mapped through the cumulative
 * mass. Reproducible, and never a new simulation.
 */
export function sampledOutcome(cells: NflExplorer['joint_score_distribution'], seed: number, index: number) {
  const golden = 0.6180339887498949
  const u = ((seed % 1000) / 1000 + (index + 1) * golden) % 1
  let acc = 0
  for (const cell of cells) {
    acc += cell.probability
    if (u <= acc) return cell
  }
  return cells[cells.length - 1]
}

export function simulationLabel(simulation: NflExplorer['simulation']): string {
  if (simulation.kind === 'MONTE_CARLO' && simulation.count) {
    return `${simulation.count.toLocaleString()} Monte Carlo draws${simulation.seed != null ? ` · seed ${simulation.seed}` : ''}`
  }
  if (simulation.kind === 'EXACT_DISTRIBUTION') return 'Exact probability distribution (no draws)'
  if (simulation.kind === 'DETERMINISTIC') return 'Deterministic evaluation (no draws)'
  return 'Simulation count not stated by the producer'
}

export function OutcomeExplorer({ explorer, homeTeam, awayTeam, engineLabel }: {
  explorer: NflExplorer
  homeTeam: string
  awayTeam: string
  engineLabel: string
}) {
  const cells = explorer.joint_score_distribution
  const [spreadLine, setSpreadLine] = useState<number>(() => -Math.round(explorer.expected.margin_home * 2) / 2)
  const [totalLine, setTotalLine] = useState<number>(() => Math.round(explorer.expected.total * 2) / 2)
  const [sampleIndex, setSampleIndex] = useState(0)

  const spread = useMemo(() => spreadQuery(cells, spreadLine), [cells, spreadLine])
  const total = useMemo(() => totalQuery(cells, totalLine), [cells, totalLine])
  const sample = useMemo(
    () => sampledOutcome(cells, explorer.simulation.seed ?? 0, sampleIndex),
    [cells, explorer.simulation.seed, sampleIndex],
  )

  const heatmap = useMemo(() => {
    const maxHome = Math.max(...cells.map((c) => c.home))
    const maxAway = Math.max(...cells.map((c) => c.away))
    const cap = Math.min(60, Math.max(maxHome, maxAway))
    const data = cells.filter((c) => c.home <= cap && c.away <= cap).map((c) => [c.away, c.home, c.probability])
    return {
      grid: { left: 44, right: 16, top: 12, bottom: 40 },
      tooltip: {
        formatter: (p: { value: [number, number, number] }) =>
          `${homeTeam} ${p.value[1]} – ${awayTeam} ${p.value[0]}<br/>${pct(p.value[2], 2)}`,
      },
      xAxis: { type: 'category', name: `${awayTeam} points`, nameLocation: 'middle', nameGap: 26, data: Array.from({ length: cap + 1 }, (_, i) => i), axisLabel: { ...AXIS, interval: 6 } },
      yAxis: { type: 'category', name: `${homeTeam} points`, nameLocation: 'middle', nameGap: 30, data: Array.from({ length: cap + 1 }, (_, i) => i), axisLabel: { ...AXIS, interval: 6 } },
      visualMap: { show: false, min: 0, max: Math.max(...cells.map((c) => c.probability)), inRange: { color: ['#eef4f9', '#9cc7e8', '#2f7bb5', '#0d3e66'] } },
      series: [{ type: 'heatmap', data, emphasis: { itemStyle: { borderColor: '#0b1729', borderWidth: 1 } } }],
    }
  }, [cells, homeTeam, awayTeam])

  const marginChart = useMemo(() => ({
    grid: { left: 40, right: 12, top: 12, bottom: 36 },
    tooltip: { formatter: (p: { value: number; name: string }) => `${homeTeam} by ${p.name}: ${pct(p.value, 2)}` },
    xAxis: { type: 'category', data: explorer.margin_distribution.map((m) => m.margin), axisLabel: { ...AXIS, interval: 4 }, name: `${homeTeam} minus ${awayTeam}`, nameLocation: 'middle', nameGap: 24 },
    yAxis: { type: 'value', axisLabel: { ...AXIS, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
    series: [{ type: 'bar', data: explorer.margin_distribution.map((m) => ({ value: m.probability, itemStyle: { color: m.margin > 0 ? '#ffb454' : m.margin < 0 ? '#4fb3ff' : '#8a98a7' } })), barCategoryGap: '15%' }],
  }), [explorer.margin_distribution, homeTeam, awayTeam])

  const totalChart = useMemo(() => ({
    grid: { left: 40, right: 12, top: 12, bottom: 36 },
    tooltip: { formatter: (p: { value: number; name: string }) => `Total ${p.name}: ${pct(p.value, 2)}` },
    xAxis: { type: 'category', data: explorer.total_distribution.map((m) => m.total), axisLabel: { ...AXIS, interval: 4 }, name: 'Combined points', nameLocation: 'middle', nameGap: 24 },
    yAxis: { type: 'value', axisLabel: { ...AXIS, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
    series: [{ type: 'bar', data: explorer.total_distribution.map((m) => m.probability), itemStyle: { color: '#1c7c8a' }, barCategoryGap: '15%' }],
  }), [explorer.total_distribution])

  const q = explorer.quantiles
  const step = (value: number, delta: number) => Math.round((value + delta) * 2) / 2

  return (
    <section className="explorer" aria-label="Outcome Explorer">
      <p className="explorer__basis">
        <Dices size={14} aria-hidden="true" /> {engineLabel} · {simulationLabel(explorer.simulation)} · {explorer.cells.toLocaleString()} distinct final scores
      </p>

      <div className="metric-grid metric-grid--six">
        <MetricCard label={`${homeTeam} win`} value={pct(explorer.outcomes.home)} detail={`Expected ${explorer.expected.home.toFixed(1)} points`} />
        <MetricCard label={`${awayTeam} win`} value={pct(explorer.outcomes.away)} detail={`Expected ${explorer.expected.away.toFixed(1)} points`} />
        <MetricCard label="Tie" value={pct(explorer.outcomes.tie)} detail="Modelled final-score tie, not overtime" />
        <MetricCard label="Expected margin" value={`${explorer.expected.margin_home >= 0 ? '+' : ''}${explorer.expected.margin_home.toFixed(1)}`} detail={`${homeTeam} minus ${awayTeam}`} />
        <MetricCard label="Expected total" value={explorer.expected.total.toFixed(1)} detail="Combined points" />
        <MetricCard label="Likely margin range" value={`${q.margin.p25 ?? '—'} to ${q.margin.p75 ?? '—'}`} detail={`Middle half · 90%: ${q.margin.p05 ?? '—'} to ${q.margin.p95 ?? '—'}`} />
      </div>

      <div className="explorer__stage">
        <FootballField
          awayTeam={awayTeam} homeTeam={homeTeam}
          awayScore={sample.away} homeScore={sample.home}
          caption="Sampled final outcome — no sequential path"
        >
          <span>
            A draw from the model&rsquo;s own distribution ({pct(sample.probability, 2)} of outcomes end exactly {homeTeam} {sample.home}–{awayTeam} {sample.away}).
            No drive, play or route is implied.
          </span>
          <button className="text-button" type="button" onClick={() => setSampleIndex((v) => v + 1)}>
            Draw another sampled outcome
          </button>
        </FootballField>
      </div>

      <div className="explorer__charts">
        <article className="explorer__chart explorer__chart--wide">
          <h3>Joint final-score heatmap</h3>
          <ReactECharts option={heatmap} style={{ height: 320 }} opts={{ renderer: 'svg' }} notMerge />
          <p className="muted">Most likely exact scores: {explorer.most_likely_scores.slice(0, 5).map((s) => `${homeTeam} ${s.home}–${awayTeam} ${s.away} (${pct(s.probability, 2)})`).join(' · ')}</p>
        </article>
        <article className="explorer__chart">
          <h3>Margin distribution</h3>
          <ReactECharts option={marginChart} style={{ height: 220 }} opts={{ renderer: 'svg' }} notMerge />
        </article>
        <article className="explorer__chart">
          <h3>Total distribution</h3>
          <ReactECharts option={totalChart} style={{ height: 220 }} opts={{ renderer: 'svg' }} notMerge />
        </article>
      </div>

      <div className="explorer__queries">
        <article className="explorer__query">
          <h3>Point spread query</h3>
          <div className="explorer__line">
            <label htmlFor="spread-line">{homeTeam} line <em>(hypothetical)</em></label>
            <button type="button" className="secondary-button" onClick={() => setSpreadLine((v) => step(v, -0.5))} aria-label="Lower the line">−</button>
            <input id="spread-line" type="number" step="0.5" value={spreadLine} onChange={(e) => setSpreadLine(Number(e.target.value))} />
            <button type="button" className="secondary-button" onClick={() => setSpreadLine((v) => step(v, 0.5))} aria-label="Raise the line">+</button>
          </div>
          <dl className="explorer__result">
            <div><dt>{homeTeam} {spreadLine > 0 ? `+${spreadLine}` : spreadLine} covers</dt><dd>{pct(spread.home_cover)}</dd></div>
            <div><dt>{awayTeam} {-spreadLine > 0 ? `+${-spreadLine}` : -spreadLine} covers</dt><dd>{pct(spread.away_cover)}</dd></div>
            <div><dt>Push</dt><dd>{pct(spread.push)}</dd></div>
          </dl>
        </article>
        <article className="explorer__query">
          <h3>Total points query</h3>
          <div className="explorer__line">
            <label htmlFor="total-line">Total line <em>(hypothetical)</em></label>
            <button type="button" className="secondary-button" onClick={() => setTotalLine((v) => step(v, -0.5))} aria-label="Lower the total">−</button>
            <input id="total-line" type="number" step="0.5" value={totalLine} onChange={(e) => setTotalLine(Number(e.target.value))} />
            <button type="button" className="secondary-button" onClick={() => setTotalLine((v) => step(v, 0.5))} aria-label="Raise the total">+</button>
          </div>
          <dl className="explorer__result">
            <div><dt>Over {totalLine}</dt><dd>{pct(total.over)}</dd></div>
            <div><dt>Under {totalLine}</dt><dd>{pct(total.under)}</dd></div>
            <div><dt>Push</dt><dd>{pct(total.push)}</dd></div>
          </dl>
        </article>
      </div>
      <p className="explorer__note">
        <Info size={13} aria-hidden="true" /> A hypothetical line changes only the market-threshold question. The forecast, its distribution and its outcome probabilities are unchanged.
        Quantiles — home points {q.home.p05}/{q.home.p50}/{q.home.p95}, away points {q.away.p05}/{q.away.p50}/{q.away.p95}, total {q.total.p05}/{q.total.p50}/{q.total.p95} (5th/50th/95th).
      </p>
    </section>
  )
}
