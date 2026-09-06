/**
 * Monte Carlo Diamond Simulator.
 *
 * Hierarchy is deliberate: the game conclusion first, then the diamond
 * experience, then outcome distributions, then market comparison, then
 * methodology. Every number shown comes from a verified deterministic replay
 * of the official forecast's own simulation, or from an observed market quote
 * that names its source. Nothing is generated in the browser.
 */
import ReactECharts from 'echarts-for-react'
import { ArrowLeft, ShieldCheck } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { DiamondTheater } from '../components/DiamondTheater'
import { DataModeBanner, DataStatePanel, PageHeader } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import {
  centralScores,
  gameStory,
  modelMarketDifference,
  percentOrUnavailable,
  probabilityComparison,
} from '../services/simulationStory'
import type { MarketPulseGameDetail } from '../types/domain'
import type { GameSimulationData } from '../types/simulation'

const AXIS = { color: '#6d7d91', fontSize: 11 }

export function GameSimulationPage() {
  const { gameId = '' } = useParams()
  const simulationResource = useApiResource(
    useMemo(() => (signal?: AbortSignal) => endpoints.gameSimulation(gameId, signal), [gameId]),
  )
  const pulseResource = useApiResource(
    useMemo(() => (signal?: AbortSignal) => endpoints.marketPulseGame(gameId, signal), [gameId]),
  )

  const simulation = simulationResource.data as GameSimulationData | null
  const pulse = pulseResource.data as MarketPulseGameDetail | null
  const row = pulse?.row ?? null

  const homeTeam = row?.home_abbrev ?? row?.home_team ?? 'Home'
  const awayTeam = row?.away_abbrev ?? row?.away_team ?? 'Away'

  const comparison = useMemo(() => probabilityComparison(simulation, row), [simulation, row])

  if (!simulation || simulationResource.state === 'unavailable' || simulationResource.state === 'loading') {
    return (
      <div className="page simulation-page">
        <PageHeader
          eyebrow="MONTE CARLO · DIAMOND SIMULATOR"
          title="Monte Carlo Diamond Simulator"
          description="Every value is replayed from the official forecast's own simulation."
        />
        <DataModeBanner
          state={simulationResource.state}
          loading={simulationResource.loading}
          error={simulationResource.error}
          onRetry={simulationResource.refresh}
        />
        <DataStatePanel
          state={simulationResource.state}
          emptyTitle="No simulation evidence is available"
          emptyDetail="This game has no official forecast, so there is no simulation to replay."
          unavailableTitle="The simulator is unavailable"
          unavailableDetail="The persisted-read API could not be reached. No illustrative simulation was substituted."
          action={<Link className="secondary-button" to={`/games/${gameId}`}>Back to game analysis</Link>}
        />
      </div>
    )
  }

  const story = gameStory({
    simulation, homeTeam, awayTeam,
    decisionStatus: null, decisionReason: null,
  })

  return (
    <div className="page simulation-page">
      <PageHeader
        eyebrow="MONTE CARLO · DIAMOND SIMULATOR"
        title="Monte Carlo Diamond Simulator"
        description={`${awayTeam} @ ${homeTeam}`}
        actions={(
          <Link className="secondary-button" to={`/games/${gameId}`}>
            <ArrowLeft size={14} /> Game analysis
          </Link>
        )}
      />

      <DataModeBanner
        state={simulationResource.state}
        loading={simulationResource.loading}
        error={simulationResource.error}
        onRetry={simulationResource.refresh}
      />

      {!simulation.simulation_available ? (
        <section className="evidence-unavailable-panel" role="status">
          <h2>Simulation unavailable for this forecast</h2>
          <p>{simulation.unavailable_reason}</p>
          <p className="muted">{simulation.play_by_play.reason}</p>
        </section>
      ) : (
        <>
          {/* 1 — GAME CONCLUSION */}
          <section className="quick-view" aria-labelledby="quick-view-heading">
            <h2 id="quick-view-heading" className="sr-only">Quick view</h2>
            <p className="quick-view__story">{story}</p>
            <div className="quick-view__grid">
              <Metric
                label={`${homeTeam} win probability`}
                value={percentOrUnavailable(simulation.official_win_probability.home * 100)}
                detail="Official forecast"
              />
              <Metric
                label={`${awayTeam} win probability`}
                value={percentOrUnavailable(simulation.official_win_probability.away * 100)}
                detail="Official forecast"
              />
              <Metric
                label="Expected total runs"
                value={simulation.means ? simulation.means.total_runs.toFixed(2) : 'Not available'}
                detail={simulation.means
                  ? `${simulation.means.away_runs.toFixed(2)} away · ${simulation.means.home_runs.toFixed(2)} home`
                  : undefined}
              />
              <Metric
                label="Extra-innings chance"
                value={percentOrUnavailable((simulation.extra_inning_probability ?? 0) * 100)}
                detail="Level after regulation"
              />
              <Metric
                label="Simulations"
                value={(simulation.inputs?.simulations ?? 0).toLocaleString()}
                detail={`Seed ${simulation.inputs?.seed}`}
              />
              <Metric
                label="Forecast generated"
                value={formatTimestamp(simulation.identity.generated_at)}
                detail={`Version ${simulation.identity.forecast_version} · ${simulation.identity.lifecycle}`}
              />
            </div>
            <p className="model-identity" role="note">
              <ShieldCheck size={15} aria-hidden="true" />
              <span>
                <strong>{simulation.identity.model_name ?? 'Model'} {simulation.identity.model_version ?? ''} — development model.</strong>{' '}
                {simulation.identity.development_note}
              </span>
            </p>
          </section>

          {/* 2 — DIAMOND EXPERIENCE */}
          <DiamondTheater simulation={simulation} homeTeam={homeTeam} awayTeam={awayTeam} />

          {/* 3 — OUTCOME DISTRIBUTIONS */}
          <section className="analysis-section" aria-labelledby="distributions-heading">
            <h2 id="distributions-heading">Outcome distributions</h2>
            <p className="analysis-section__sub">
              Every chart below is computed from the {simulation.inputs?.simulations.toLocaleString()} draws
              of the official simulation, replayed and verified against the stored forecast.
            </p>
            <div className="chart-grid">
              <ChartCard
                title="Final-score heatmap"
                hint="How often each exact final score occurred. Rows are home runs, columns away runs."
              >
                <ScoreHeatmap simulation={simulation} homeTeam={homeTeam} awayTeam={awayTeam} />
              </ChartCard>
              <ChartCard
                title="Total runs"
                hint="How often the two teams combined for each run total."
              >
                <DistributionChart data={simulation.total_runs_distribution} colour="#087f7a" name="Total runs" />
              </ChartCard>
              <ChartCard
                title={`${homeTeam} runs`}
                hint="Distribution of runs scored by the home team."
              >
                <DistributionChart data={simulation.home_runs_distribution} colour="#286f9e" name="Home runs" />
              </ChartCard>
              <ChartCard
                title={`${awayTeam} runs`}
                hint="Distribution of runs scored by the away team."
              >
                <DistributionChart data={simulation.away_runs_distribution} colour="#a97018" name="Away runs" />
              </ChartCard>
            </div>
            <MostLikelyScores simulation={simulation} homeTeam={homeTeam} awayTeam={awayTeam} />
          </section>

          {/* 4 — MARKET COMPARISON */}
          <section className="analysis-section" aria-labelledby="market-heading">
            <h2 id="market-heading">Model versus market</h2>
            <p className="analysis-section__sub">
              All values are the <strong>home team&rsquo;s</strong> win probability on one scale.
              Kalshi and Polymarket are observed markets, not model predictions, and never carry
              decision authority.
            </p>
            <table className="comparison-table">
              <caption className="sr-only">Probability comparison by source</caption>
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Type</th>
                  <th scope="col">{homeTeam} win probability</th>
                  <th scope="col">Difference vs ASP</th>
                  <th scope="col">Basis</th>
                </tr>
              </thead>
              <tbody>
                {comparison.map((entry) => {
                  const difference = entry.key === 'asp' ? null : modelMarketDifference(comparison, entry.key)
                  return (
                    <tr key={entry.key}>
                      <th scope="row">{entry.label}</th>
                      <td>{entry.kind === 'model' ? 'Model' : 'Observed market'}</td>
                      <td>
                        {entry.probability == null
                          ? <span className="unavailable">Not available</span>
                          : percentOrUnavailable(entry.probability)}
                        {entry.observedAgeSeconds != null && (
                          <small> · observed {Math.round(entry.observedAgeSeconds / 60)}m ago</small>
                        )}
                      </td>
                      <td>
                        {difference == null
                          ? <span className="unavailable">—</span>
                          : `${difference > 0 ? '+' : ''}${difference.toFixed(1)} pts`}
                      </td>
                      <td className="comparison-table__basis">
                        {entry.unavailableReason ?? entry.basis}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>

          {/* 5 — METHODOLOGY */}
          <section className="analysis-section" aria-labelledby="method-heading">
            <h2 id="method-heading">How this was produced</h2>
            <dl className="method-grid">
              <div>
                <dt>Simulator</dt>
                <dd>{simulation.inputs?.simulator}</dd>
              </div>
              <div>
                <dt>Replay verification</dt>
                <dd>
                  {simulation.replay_validation?.exact
                    ? `Exact match across ${simulation.replay_validation.compared_fields} fields (max difference ${simulation.replay_validation.max_absolute_delta}).`
                    : `Replay differs from the stored forecast by ${simulation.replay_validation?.max_absolute_delta}.`}
                </dd>
              </div>
              <div>
                <dt>Official vs simulation win rate</dt>
                <dd>
                  Official {percentOrUnavailable(simulation.official_win_probability.home * 100)} ·
                  {' '}simulation only {percentOrUnavailable((simulation.simulation_win_probability?.home ?? 0) * 100)}.
                  The published probability is a governed blend of model components and the
                  simulation, so the two differ by design.
                </dd>
              </div>
              <div>
                <dt>Monte Carlo sampling error</dt>
                <dd>
                  ±{((simulation.sampling_uncertainty?.standard_error ?? 0) * 100 * 1.96).toFixed(2)} pts (95%).
                  {' '}{simulation.sampling_uncertainty?.note}
                </dd>
              </div>
              <div>
                <dt>Play-by-play</dt>
                <dd>{simulation.play_by_play.reason}</dd>
              </div>
              <div>
                <dt>Evidence identity</dt>
                <dd className="mono">{simulation.identity.feature_snapshot_reference ?? 'Not recorded'}</dd>
              </div>
            </dl>
          </section>
        </>
      )}
    </div>
  )
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <article className="sim-metric">
      <span className="sim-metric__label">{label}</span>
      <strong className="sim-metric__value">{value}</strong>
      {detail && <small className="sim-metric__detail">{detail}</small>}
    </article>
  )
}

function ChartCard({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <article className="chart-card">
      <header>
        <h3>{title}</h3>
        <p>{hint}</p>
      </header>
      {children}
    </article>
  )
}

/** Bar chart over a run-count histogram. Overflow buckets are kept visible. */
function DistributionChart({
  data, colour, name,
}: { data: Record<string, number> | undefined; colour: string; name: string }) {
  if (!data) {
    return <p className="evidence-unavailable" role="status">This distribution is not available for this forecast.</p>
  }
  const keys = Object.keys(data).filter((k) => !k.startsWith('<')).sort((a, b) => {
    if (a.startsWith('>')) return 1
    if (b.startsWith('>')) return -1
    return Number(a) - Number(b)
  })
  const option = {
    grid: { left: 44, right: 12, top: 16, bottom: 30 },
    xAxis: { type: 'category', data: keys, axisLabel: AXIS, axisLine: { lineStyle: { color: '#dce4ea' } } },
    yAxis: {
      type: 'value',
      axisLabel: { ...AXIS, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: '#eef2f5' } },
    },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: number) => `${(v * 100).toFixed(2)}% of simulations`,
    },
    series: [{ name, type: 'bar', data: keys.map((k) => data[k]), itemStyle: { color: colour, borderRadius: [3, 3, 0, 0] } }],
  }
  return <ReactECharts option={option} style={{ height: 230 }} notMerge lazyUpdate opts={{ renderer: 'canvas' }} />
}

/** Joint final-score heatmap — the reason the replay exists. */
function ScoreHeatmap({
  simulation, homeTeam, awayTeam,
}: { simulation: GameSimulationData; homeTeam: string; awayTeam: string }) {
  const joint = simulation.joint_score_distribution
  if (!joint) {
    return <p className="evidence-unavailable" role="status">The joint score distribution is not available.</p>
  }
  const cells: [number, number, number][] = []
  joint.probabilities.forEach((rowValues, home) => {
    rowValues.forEach((probability, away) => {
      if (probability > 0) cells.push([away, home, probability])
    })
  })
  const max = Math.max(...cells.map((c) => c[2]), 0.0001)
  const option = {
    grid: { left: 46, right: 16, top: 12, bottom: 46 },
    xAxis: { type: 'category', data: joint.away_labels, name: `${awayTeam} runs`, nameLocation: 'middle', nameGap: 26, axisLabel: AXIS, nameTextStyle: AXIS },
    yAxis: { type: 'category', data: joint.home_labels, name: `${homeTeam} runs`, nameLocation: 'middle', nameGap: 30, axisLabel: AXIS, nameTextStyle: AXIS },
    visualMap: {
      min: 0, max, calculable: false, show: false,
      inRange: { color: ['#f4f7f9', '#bfe0dc', '#4ca79f', '#087f7a', '#04413e'] },
    },
    tooltip: {
      formatter: (p: { data: [number, number, number] }) =>
        `${awayTeam} ${joint.away_labels[p.data[0]]} — ${homeTeam} ${joint.home_labels[p.data[1]]}<br/>`
        + `${(p.data[2] * 100).toFixed(2)}% of simulations`,
    },
    series: [{ type: 'heatmap', data: cells, progressive: 0, itemStyle: { borderColor: '#ffffff', borderWidth: 0.5 } }],
  }
  return <ReactECharts option={option} style={{ height: 300 }} notMerge lazyUpdate opts={{ renderer: 'canvas' }} />
}

function MostLikelyScores({
  simulation, homeTeam, awayTeam,
}: { simulation: GameSimulationData; homeTeam: string; awayTeam: string }) {
  const scores = centralScores(simulation, 0.2)
  if (scores.length === 0) return null
  const covered = scores.reduce((sum, s) => sum + s.probability, 0)
  return (
    <div className="likely-scores">
      <h3>Most likely exact scores</h3>
      <p>
        These {scores.length} scores cover {(covered * 100).toFixed(0)}% of all simulations.
      </p>
      <ul>
        {scores.map((score) => (
          <li key={`${score.home}-${score.away}`}>
            <strong>{awayTeam} {score.away} — {homeTeam} {score.home}</strong>
            <span>{(score.probability * 100).toFixed(2)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
