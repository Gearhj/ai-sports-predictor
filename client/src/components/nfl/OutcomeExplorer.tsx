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
import { useEffect, useMemo, useState } from 'react'
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

export function OutcomeExplorer({ explorer, homeTeam, awayTeam, engineLabel, marketSpread, marketTotal, representativeFinals = [], onReplayRepresentative }: {
  explorer: NflExplorer
  homeTeam: string
  awayTeam: string
  engineLabel: string
  marketSpread?: number | null
  marketTotal?: number | null
  representativeFinals?: Array<{ homeScore: number; awayScore: number; label?: string; simulationIndex?: number }>
  onReplayRepresentative?: (simulationIndex: number) => void
}) {
  const cells = explorer.joint_score_distribution
  const [spreadLine, setSpreadLine] = useState<number>(() => -Math.round(explorer.expected.margin_home * 2) / 2)
  const [totalLine, setTotalLine] = useState<number>(() => Math.round(explorer.expected.total * 2) / 2)
  const [sampleIndex, setSampleIndex] = useState(0)
  const [selectedCell, setSelectedCell] = useState<NflExplorer['joint_score_distribution'][number] | null>(null)
  useEffect(() => { setSelectedCell(null); setSampleIndex(0); setSpreadLine(-Math.round(explorer.expected.margin_home * 2) / 2); setTotalLine(Math.round(explorer.expected.total * 2) / 2) }, [explorer])

  const spread = useMemo(() => spreadQuery(cells, spreadLine), [cells, spreadLine])
  const total = useMemo(() => totalQuery(cells, totalLine), [cells, totalLine])
  const sample = useMemo(
    () => selectedCell ?? sampledOutcome(cells, explorer.simulation.seed ?? 0, sampleIndex),
    [cells, explorer.simulation.seed, sampleIndex, selectedCell],
  )
  // The field shows whichever outcome is current, so a representative match is
  // reported for a drawn score too, not only for a hand-picked one.
  const matching = representativeFinals.find(
    (g) => g.homeScore === sample.home && g.awayScore === sample.away) ?? null

  /*
   * The distribution, accumulating.
   *
   * M1 publishes a joint distribution rather than a sequence of plays, so the
   * thing worth watching build is the distribution itself. Cells are revealed
   * in descending probability, which is the order that shows the mass
   * concentrating; the readout states how much of it is on screen.
   *
   * Nothing is sampled or invented here. These are the producer's own cells,
   * shown a few at a time.
   */
  const ordered = useMemo(
    () => [...cells].sort((a, b) => b.probability - a.probability), [cells],
  )
  const [revealed, setRevealed] = useState(0)
  const [building, setBuilding] = useState(true)
  useEffect(() => { setRevealed(0); setBuilding(true) }, [ordered])
  useEffect(() => {
    if (!building || ordered.length === 0) return
    let done = false
    let last = performance.now()
    let carry = 0
    const timer = window.setInterval(() => {
      if (done) return
      const now = performance.now()
      // Fill in about two seconds regardless of how many distinct scores the
      // engine produced, so a 500-cell and a 50-cell distribution both read.
      carry += Math.min(0.25, (now - last) / 1000) * (ordered.length / 2)
      last = now
      const whole = Math.floor(carry)
      if (whole <= 0) return
      carry -= whole
      setRevealed((current) => {
        const next = Math.min(ordered.length, current + whole)
        if (next >= ordered.length) { done = true; window.clearInterval(timer); setBuilding(false) }
        return next
      })
    }, 16)
    return () => { done = true; window.clearInterval(timer) }
  }, [building, ordered])

  const shown = useMemo(() => ordered.slice(0, revealed), [ordered, revealed])
  const massShown = useMemo(
    () => shown.reduce((sum, cell) => sum + cell.probability, 0), [shown],
  )

  const marked = sample
  // What the current selection (or the drawn outcome) means on the other two
  // charts, so a choice made in one place is visible in all three.
  const selectedMargin = marked ? marked.home - marked.away : null
  const selectedTotal = marked ? marked.home + marked.away : null

  const heatmap = useMemo(() => {
    const maxHome = Math.max(...cells.map((c) => c.home))
    const maxAway = Math.max(...cells.map((c) => c.away))
    const cap = Math.max(maxHome, maxAway)
    const data = shown.filter((c) => c.home <= cap && c.away <= cap).map((c) => [c.away, c.home, c.probability])
    return {
      animation: false,
      grid: { left: 44, right: 16, top: 12, bottom: 40 },
      tooltip: {
        formatter: (p: { value: [number, number, number] }) =>
          `${homeTeam} ${p.value[1]} – ${awayTeam} ${p.value[0]}<br/>${pct(p.value[2], 3)} · click to select`,
      },
      xAxis: { type: 'category', name: `${awayTeam} points`, nameLocation: 'middle', nameGap: 26, data: Array.from({ length: cap + 1 }, (_, i) => i), axisLabel: { ...AXIS, interval: 6 } },
      yAxis: { type: 'category', name: `${homeTeam} points`, nameLocation: 'middle', nameGap: 30, data: Array.from({ length: cap + 1 }, (_, i) => i), axisLabel: { ...AXIS, interval: 6 } },
      visualMap: { show: false, min: 0, max: Math.max(...cells.map((c) => c.probability)), inRange: { color: ['#eef4f9', '#9cc7e8', '#2f7bb5', '#0d3e66'] } },
      series: [{
        type: 'heatmap', data,
        emphasis: { itemStyle: { borderColor: '#0b1729', borderWidth: 1 } },
        markPoint: marked ? {
          symbol: 'circle', symbolSize: 15, animation: false,
          itemStyle: { color: 'transparent', borderColor: '#ff8f3c', borderWidth: 3 },
          label: { show: false },
          data: [{ coord: [marked.away, marked.home] }],
        } : undefined,
      }],
    }
  }, [cells, shown, homeTeam, awayTeam, marked])

  const marginChart = useMemo(() => ({
    grid: { left: 40, right: 12, top: 12, bottom: 36 },
    animation: false,
    tooltip: { triggerOn: 'mousemove|click', formatter: (p: { value: number; name: string }) => `${homeTeam} by ${p.name}: ${pct(p.value, 3)}` },
    xAxis: { type: 'category', data: explorer.margin_distribution.map((m) => m.margin), axisLabel: { ...AXIS, interval: 4 }, name: `${homeTeam} minus ${awayTeam}`, nameLocation: 'middle', nameGap: 24 },
    yAxis: { type: 'value', axisLabel: { ...AXIS, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
    series: [{
      type: 'bar', barCategoryGap: '15%',
      // The selected score's own margin is highlighted rather than annotated,
      // so a selection in the heatmap is visible in this chart too.
      data: explorer.margin_distribution.map((m) => ({
        value: m.probability,
        itemStyle: {
          color: selectedMargin === m.margin
            ? '#ff8f3c'
            : m.margin > 0 ? '#ffb454' : m.margin < 0 ? '#4fb3ff' : '#8a98a7',
          borderColor: selectedMargin === m.margin ? '#0b1729' : 'transparent',
          borderWidth: selectedMargin === m.margin ? 1 : 0,
        },
      })),
    }],
  }), [explorer.margin_distribution, homeTeam, awayTeam, selectedMargin])

  const totalChart = useMemo(() => ({
    grid: { left: 40, right: 12, top: 12, bottom: 36 },
    animation: false,
    tooltip: { triggerOn: 'mousemove|click', formatter: (p: { value: number; name: string }) => `Total ${p.name}: ${pct(p.value, 3)}` },
    xAxis: { type: 'category', data: explorer.total_distribution.map((m) => m.total), axisLabel: { ...AXIS, interval: 4 }, name: 'Combined points', nameLocation: 'middle', nameGap: 24 },
    yAxis: { type: 'value', axisLabel: { ...AXIS, formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
    series: [{
      type: 'bar', barCategoryGap: '15%',
      data: explorer.total_distribution.map((m) => ({
        value: m.probability,
        itemStyle: { color: selectedTotal === m.total ? '#ff8f3c' : '#1c7c8a' },
      })),
    }],
  }), [explorer.total_distribution, selectedTotal])

  const q = explorer.quantiles
  const step = (value: number, delta: number) => Math.round((value + delta) * 2) / 2
  const chooseCell = (p: { value?: [number, number, number] }) => {
    if (p.value) setSelectedCell(cells.find((c) => c.away === p.value![0] && c.home === p.value![1]) ?? null)
  }

  return (
    <section className="explorer explorer--dominant" aria-label="Outcome Explorer">
      <p className="explorer__basis">
        <Dices size={14} aria-hidden="true" /> {engineLabel} · {simulationLabel(explorer.simulation)} · {explorer.cells.toLocaleString()} distinct final scores
      </p>
      <p className="explorer__guide">
        This engine did not play one game — it produced a whole distribution of final scores,
        and that distribution is what you are looking at. Watch it build, then select any cell:
        the margin, the total, the spread and total answers and the sampled scoreboard below all
        follow your choice. There are no play paths here, and none are borrowed from another engine.
      </p>

      <div className="metric-grid metric-grid--six">
        <MetricCard label={`${homeTeam} win`} value={pct(explorer.outcomes.home)} detail={`Expected ${explorer.expected.home.toFixed(1)} points`} />
        <MetricCard label={`${awayTeam} win`} value={pct(explorer.outcomes.away)} detail={`Expected ${explorer.expected.away.toFixed(1)} points`} />
        <MetricCard label="Tie" value={pct(explorer.outcomes.tie)} detail="Modelled final-score tie, not overtime" />
        <MetricCard label="Expected margin" value={`${explorer.expected.margin_home >= 0 ? '+' : ''}${explorer.expected.margin_home.toFixed(1)}`} detail={`${homeTeam} minus ${awayTeam}`} />
        <MetricCard label="Expected total" value={explorer.expected.total.toFixed(1)} detail="Combined points" />
        <MetricCard label="Likely margin range" value={`${q.margin.p25 ?? '—'} to ${q.margin.p75 ?? '—'}`} detail={`Middle half · 90%: ${q.margin.p05 ?? '—'} to ${q.margin.p95 ?? '—'}`} />
      </div>

      <div className="explorer__charts">
        <article className="explorer__chart explorer__chart--wide explorer__chart--hero">
          <div className="explorer__chartHead">
            <div>
              <h3>Every simulated final score</h3>
              <p className="muted">
                Select a cell to inspect its exact probability, and to mark that margin and total
                on the two charts below. Every plotted score belongs to this engine.
              </p>
            </div>
            <div className="explorer__build">
              <button
                type="button" className="secondary-button"
                onClick={() => { setRevealed(0); setBuilding(true) }}
              >Rebuild</button>
              <span role="status">
                <b>{shown.length.toLocaleString()}</b> of{' '}
                <b>{ordered.length.toLocaleString()}</b> distinct final scores ·{' '}
                <b>{pct(massShown, 1)}</b> of the probability mass
              </span>
            </div>
          </div>
          <ReactECharts option={heatmap} onEvents={{ click: chooseCell }} style={{ height: 420 }} opts={{ renderer: 'svg' }} notMerge />
          <div className="nfl-score-shortcuts" aria-label="Likely score shortcuts">{explorer.most_likely_scores.slice(0, 8).map((s) => <button type="button" key={`${s.home}-${s.away}`} aria-pressed={selectedCell?.home === s.home && selectedCell?.away === s.away} onClick={() => setSelectedCell(s)}>{homeTeam} {s.home}–{awayTeam} {s.away} · {pct(s.probability, 2)}</button>)}</div>
          <details className="nfl-lab-evidence"><summary>Choose any exact score with the keyboard</summary><label>Exact final score <select aria-label="Exact final score" value={selectedCell ? `${selectedCell.home}:${selectedCell.away}` : ''} onChange={(e) => { const [home, away] = e.target.value.split(':').map(Number); setSelectedCell(cells.find((c) => c.home === home && c.away === away) ?? null) }}><option value="">Choose a recorded score</option>{cells.map((c) => <option key={`${c.home}:${c.away}`} value={`${c.home}:${c.away}`}>{homeTeam} {c.home}–{awayTeam} {c.away} · {pct(c.probability, 3)}</option>)}</select></label></details>
          {selectedCell && <div className="nfl-cell-selection" role="status"><strong>{homeTeam} {selectedCell.home}–{awayTeam} {selectedCell.away} · {pct(selectedCell.probability, 3)}</strong><span>{matching ? `Matches a supplied sampled final${matching.label ? `: ${matching.label}` : ''}.` : 'No supplied representative game has this exact final.'}</span>{matching?.simulationIndex != null && onReplayRepresentative && <button className="secondary-button" type="button" onClick={() => onReplayRepresentative(matching.simulationIndex!)}>Replay this representative</button>}</div>}
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

      <div className="explorer__stage explorer__stage--secondary">
        <FootballField
          awayTeam={awayTeam} homeTeam={homeTeam}
          awayScore={sample.away} homeScore={sample.home}
          caption={selectedCell ? 'Selected final score — not a sequence of plays' : 'One outcome drawn from the distribution — not a sequence of plays'}
        >
          <span>
            {selectedCell ? 'You selected this final score. ' : 'This is a draw from the distribution. '}
            <strong>{pct(sample.probability, 3)}</strong> of simulated games end exactly {homeTeam}{' '}
            {sample.home}–{awayTeam} {sample.away}.
            {matching ? ` It matches a supplied representative game${matching.label ? `: ${matching.label}.` : '.'}` : ''}
          </span>
          <button className="primary-button explorer__draw" type="button"
            onClick={() => { setSelectedCell(null); setSampleIndex((v) => v + 1) }}>
            <Dices size={15} aria-hidden="true" /> Draw another outcome
          </button>
        </FootballField>
      </div>

      <div className="explorer__queries">
        <article className="explorer__query">
          <h3>Point spread query <small className="explorer__scenario">Modeled reference scenario · not a sportsbook quote</small></h3>
          <div className="explorer__line">
            <label htmlFor="spread-line">{homeTeam} line <em>(hypothetical)</em></label>
            <button type="button" className="secondary-button" onClick={() => setSpreadLine((v) => step(v, -0.5))} aria-label="Lower the line">−</button>
            <input id="spread-line" type="number" step="0.5" value={spreadLine} onChange={(e) => setSpreadLine(Number(e.target.value))} />
            <button type="button" className="secondary-button" onClick={() => setSpreadLine((v) => step(v, 0.5))} aria-label="Raise the line">+</button>
          </div>
          <div className="nfl-key-lines" aria-label="NFL key spread lines">{[-7, -3, 3, 7].map((line) => <button key={line} type="button" aria-pressed={spreadLine === line} onClick={() => setSpreadLine(line)}>{homeTeam} {line > 0 ? '+' : ''}{line}</button>)}{marketSpread != null && <button type="button" onClick={() => setSpreadLine(marketSpread)}>Current market {marketSpread > 0 ? '+' : ''}{marketSpread}</button>}</div>
          <dl className="explorer__result">
            <div><dt>{homeTeam} {spreadLine > 0 ? `+${spreadLine}` : spreadLine} covers</dt><dd>{pct(spread.home_cover)}</dd></div>
            <div><dt>{awayTeam} {-spreadLine > 0 ? `+${-spreadLine}` : -spreadLine} covers</dt><dd>{pct(spread.away_cover)}</dd></div>
            <div><dt>Push</dt><dd>{pct(spread.push)}</dd></div>
          </dl>
        </article>
        <article className="explorer__query">
          <h3>Total points query <small className="explorer__scenario">Modeled reference scenario · not a sportsbook quote</small></h3>
          <div className="explorer__line">
            <label htmlFor="total-line">Total line <em>(hypothetical)</em></label>
            <button type="button" className="secondary-button" onClick={() => setTotalLine((v) => step(v, -0.5))} aria-label="Lower the total">−</button>
            <input id="total-line" type="number" step="0.5" value={totalLine} onChange={(e) => setTotalLine(Number(e.target.value))} />
            <button type="button" className="secondary-button" onClick={() => setTotalLine((v) => step(v, 0.5))} aria-label="Raise the total">+</button>
          </div>
          {marketTotal != null && <div className="nfl-key-lines"><button type="button" onClick={() => setTotalLine(marketTotal)}>Current market total {marketTotal}</button></div>}
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
