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
import { ArrowLeft, ChevronRight, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { DiamondTheater } from '../components/DiamondTheater'
import { V18SimulationTrack, type V18View } from '../components/V18SimulationTrack'
import { ScenarioLab } from '../components/ScenarioLab'
import { DataModeBanner, DataStatePanel, PageHeader } from '../components/ui'
import { SimulationProvenance } from '../components/SimulationProvenance'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import {
  OUTCOME_LABELS,
  centralScores,
  gameStory,
  modelMarketDifference,
  percentOrUnavailable,
  probabilityComparison,
} from '../services/simulationStory'
import type { MarketPulseGameDetail } from '../types/domain'
import type { ShadowComparison } from '../types/shadow'
import type { GameSimulationData } from '../types/simulation'

const AXIS = { color: '#6d7d91', fontSize: 11 }

export function GameSimulationPage() {
  const { gameId = '' } = useParams()

  // Which engine's simulation the reader asked for. With no explicit track the
  // page follows the controlling engine: V18's saved simulation when one
  // exists, Gen 1 otherwise. `?track=gen1` and `?track=v18` still pin a track.
  // Read before the resources, because both are part of their cache identity.
  const [params, setParams] = useSearchParams()
  const trackParam = params.get('track')
  const requestedTrack: 'gen1' | 'v18' | 'auto' = trackParam === 'v18' ? 'v18' : trackParam === 'gen1' ? 'gen1' : 'auto'
  // The visualisation selector is separate from the engine selector. An
  // existing ?track=v18 link keeps opening the Broadcast Replay.
  const requestedView: V18View = (() => {
    const value = params.get('view')
    return value === 'geometry' || value === 'population' ? value : 'broadcast'
  })()
  //: An explicitly selected historical snapshot, when the reader asked for one.
  const horizon = params.get('as_of')

  const simulationResource = useApiResource(
    useMemo(() => (signal?: AbortSignal) => endpoints.gameSimulation(gameId, signal), [gameId]),
    {
      // Track, view and horizon are part of the identity: the same game shows
      // a different engine's numbers on each, and a cache that ignored them
      // would serve one under the other's heading.
      key: cacheKey('game/simulation', {
        gameId, track: requestedTrack, view: requestedView, horizon,
      }),
      staleTimeMs: STALE_TIME.game,
    },
  )
  const pulseResource = useApiResource(
    useMemo(() => (signal?: AbortSignal) => endpoints.marketPulseGame(gameId, signal), [gameId]),
    { key: cacheKey('game/market-pulse', { gameId, horizon }), staleTimeMs: STALE_TIME.game },
  )
  const [shadowTracks, setShadowTracks] = useState<ShadowComparison | null>(null)
  const [shadowSettled, setShadowSettled] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let live = true
    setShadowSettled(false)
    endpoints
      // The second parameter is `configuration`. Passing the signal there sent
      // `?configuration=[object AbortSignal]` and left the request unabortable.
      .gameShadowForecasts(gameId, null, controller.signal)
      .then((payload) => {
        if (!live) return
        setShadowTracks(
          payload?.schema_version === 'AISP_MLB_V18_SHADOW_COMPARISON_V1' ? payload : null,
        )
        setShadowSettled(true)
      })
      .catch(() => { if (live) { setShadowTracks(null); setShadowSettled(true) } })
    return () => { live = false; controller.abort() }
  }, [gameId])

  const shadow = shadowTracks?.shadows?.[0] ?? null
  const availableV18Views = shadow?.available_views ?? {
    broadcast: Boolean(shadow?.sequential_path_events),
    geometry: true,
    population: true,
  }
  const activeView: V18View = availableV18Views[requestedView]
    ? requestedView
    : 'broadcast'
  const unavailableViews = (['geometry', 'population'] as const)
    .filter((key) => !availableV18Views[key])
  const simulation = simulationResource.data as GameSimulationData | null
  const pulse = pulseResource.data as MarketPulseGameDetail | null
  const row = pulse?.row ?? null

  const homeTeam = row?.home_abbrev ?? row?.home_team ?? 'Home'
  const awayTeam = row?.away_abbrev ?? row?.away_team ?? 'Away'

  const comparison = useMemo(() => probabilityComparison(simulation, row), [simulation, row])

  // A V18 request must not be blocked by Gen 1's own availability.
  const gen1Unavailable = !simulation
    || simulationResource.state === 'unavailable'
    || simulationResource.state === 'loading'
  // Until the V18 lookup settles, an automatic choice is not yet known; showing
  // Gen 1 first and then swapping engines would flash the wrong model.
  const awaitingChoice = requestedTrack === 'auto' && !shadowSettled
  if (awaitingChoice || (gen1Unavailable && !(requestedTrack !== 'gen1' && shadow))) {
    return (
      <div className="page simulation-page">
        <PageHeader
          eyebrow="MONTE CARLO · DIAMOND SIMULATOR"
          title="Monte Carlo Diamond Simulator"
          description="Every value is replayed from the official forecast's own simulation."
        />
        <DataModeBanner
          state={awaitingChoice ? 'loading' : simulationResource.state}
          loading={awaitingChoice || simulationResource.loading}
          error={simulationResource.error}
          onRetry={simulationResource.refresh}
        />
        <DataStatePanel
          state={awaitingChoice ? 'loading' : simulationResource.state}
          emptyTitle="No simulation evidence is available"
          emptyDetail="This game has no official forecast, so there is no simulation to replay."
          unavailableTitle="The simulator is unavailable"
          unavailableDetail="The persisted-read API could not be reached. No illustrative simulation was substituted."
          action={<Link className="secondary-button" to={`/games/${gameId}`}>Back to game analysis</Link>}
        />
      </div>
    )
  }

  // Which track can actually be shown. A v18 request without a usable shadow
  // falls back to Gen 1 with an honest explanation rather than an empty page.
  const bothTracks = Boolean(shadow) && Boolean(simulation)
  const v18Requested = requestedTrack === 'v18'
  const track: 'gen1' | 'v18' = requestedTrack !== 'gen1' && shadow ? 'v18' : 'gen1'
  const v18Refused = v18Requested && !shadow

  const story = simulation
    ? gameStory({ simulation, homeTeam, awayTeam, decisionStatus: null, decisionReason: null })
    : ''

  return (
    <div className={`page simulation-page${track === 'v18' ? ' simulation-page--theater' : ''}`}>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/labs">Interactive labs</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <Link to={`/games/${gameId}`}>{awayTeam} @ {homeTeam}</Link>
        <ChevronRight size={13} aria-hidden="true" />
        <span aria-current="page">
          {track === 'v18'
            ? (activeView === 'geometry' ? 'V18 Play Geometry'
              : activeView === 'population' ? 'V18 simulation population'
                : 'V18 Diamond Theater')
            : 'Gen 1 outcome distribution'}
        </span>
      </nav>

      <PageHeader
        eyebrow={track === 'v18'
          ? (activeView === 'geometry'
            ? 'RESEARCH SHADOW · MODELLED BALL AND RUNNER GEOMETRY'
            : activeView === 'population'
              ? 'RESEARCH SHADOW · SIMULATED POPULATION'
              : 'RESEARCH-SHADOW SEQUENTIAL GAME SIMULATION')
          : 'GEN 1 BASELINE · FINAL-SCORE DISTRIBUTION'}
        title={track === 'v18'
          ? (activeView === 'geometry' ? 'V18 Play Geometry'
            : activeView === 'population' ? 'V18 Simulation Population'
              : 'V18 Monte Carlo Diamond Theater')
          : 'Gen 1 baseline · Final-score distribution'}
        description={`${awayTeam} @ ${homeTeam}`}
        actions={(
          <Link className="secondary-button" to={`/games/${gameId}`}>
            <ArrowLeft size={14} /> Game analysis
          </Link>
        )}
      />

      {bothTracks && (
        <nav className="track-selector" aria-label="Simulation track">
          {([
            // "Official" was fixed wording from when Gen 1 controlled funded
            // decisions. Which era controls is a live fact and is stated by the
            // provenance strip below, not baked into a tab label.
            ['gen1', 'Gen 1 baseline · Final-score distribution'],
            ['v18', 'V18 · Saved game simulation'],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-current={track === key ? 'page' : undefined}
              className={track === key ? 'is-active' : ''}
              onClick={() => setParams({ track: key }, { replace: true })}
            >
              {label}
            </button>
          ))}
        </nav>
      )}

      <p className={`track-note${track === 'v18' ? ' track-note--compact' : ''}`} role="note">
        Switching tracks changes only which engine's evidence is displayed. Neither
        the Gen 1 baseline nor the V18 forecast is altered here.
      </p>

      {/* A "LIVE DATA" banner here meant only that the API answered, beside a
          saved simulation that is not live coverage of anything. What a reader
          needs instead is which artifact is on screen and which era actually
          controls funded decisions -- two separate facts. */}
      <SimulationProvenance
        track={track}
        shadow={shadow}
        simulation={simulation}
        refreshing={simulationResource.refreshing}
        error={simulationResource.error}
        onRetry={simulationResource.refresh}
      />

      {v18Refused && (
        <section className="evidence-unavailable-panel" role="status">
          <h2>No research-shadow replay exists for this game</h2>
          <p>
            No V18 research-shadow forecast is registered for this game, so there is
            no persisted path to replay. Nothing has been reconstructed from Gen 1 in
            its place.
          </p>
          <Link className="secondary-button" to={`/games/${gameId}/simulation`}>
            Show the official Gen 1 distribution
          </Link>
        </section>
      )}

      {track === 'v18' && shadow && (
        <>
          {/* Every mode stays visible. An unavailable one is disabled with the
              reason it is unavailable, because a mode that silently disappears
              is indistinguishable from one that never existed -- and these two
              are unavailable for a specific, reportable reason. */}
          <nav className="view-selector view-selector--modes" aria-label="Simulator view">
            {([
              ['broadcast', 'Broadcast Replay',
               'Replays one saved simulated game plate appearance by plate appearance: score, bases, outs and the feed.'],
              ['geometry', 'Play Geometry',
               'Schematic ball and runner paths for each saved play.'],
              ['population', 'Simulation Population',
               'Every simulated game behind the forecast, not one path.'],
            ] as const).map(([key, label, description]) => {
              const available = Boolean(availableV18Views[key])
              const active = activeView === key
              return (
                <button
                  key={key}
                  type="button"
                  disabled={!available}
                  aria-label={label}
                  aria-current={active ? 'page' : undefined}
                  aria-describedby={`mode-desc-${key}`}
                  className={`view-selector__mode${active ? ' is-active' : ''}${available ? '' : ' is-unavailable'}`}
                  onClick={() => setParams(
                    key === 'broadcast' ? { track: 'v18' } : { track: 'v18', view: key },
                    { replace: true },
                  )}
                >
                  <b>{label}</b>
                  <small id={`mode-desc-${key}`}>{description}</small>
                  {!available && (
                    <small className="view-selector__why">
                      This forecast does not include this view. Nothing is reconstructed
                      in its place.
                    </small>
                  )}
                </button>
              )
            })}
          </nav>

          {/* The field names stay available, but below the control rather than
              inside it. A reader deciding whether to click a mode needs one
              sentence; whoever is chasing why the supplement is absent -- or
              writing the producer handoff -- needs the exact flag. */}
          {unavailableViews.length > 0 && (
            <details className="mode-technical">
              <summary>Technical detail: unavailable views</summary>
              <ul>
                {unavailableViews.map((key) => (
                  <li key={key}>
                    <code>available_views.{key} = false</code>, derived from{' '}
                    <code>
                      lineage.{key === 'geometry' ? 'geometry.events' : 'populationGeometry.summary'}
                    </code>
                    {' '}on <code>{shadow?.forecast_id ?? 'this forecast'}</code>. The producer
                    published no {key === 'geometry' ? 'play-geometry' : 'population-geometry'}{' '}
                    supplement for it. (The population <em>statistics</em> below come from the
                    forecast itself and are unaffected.)
                  </li>
                ))}
              </ul>
            </details>
          )}

          <V18SimulationTrack
            shadow={shadow}
            awayTeam={awayTeam}
            homeTeam={homeTeam}
            gameId={gameId}
            view={activeView}
            eventStatus={shadowTracks?.event_status}
          />
        </>
      )}

      {track === 'gen1' && simulation && (
      <>
      <p className="track-scope" role="note">
        Gen 1 draws final scores directly. It has no innings, plate appearances,
        runners, outs or pitching changes, so nothing on this track is a play-by-play.
      </p>
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
                detail="Gen 1 baseline forecast"
              />
              <Metric
                label={`${awayTeam} win probability`}
                value={percentOrUnavailable(simulation.official_win_probability.away * 100)}
                detail="Gen 1 baseline forecast"
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

          {/* 3 — WHAT-IF SCENARIO LAB (hypothetical, never official) */}
          <ScenarioLab gameId={gameId} homeTeam={homeTeam} awayTeam={awayTeam} />

          {/* 4 — REPRESENTATIVE OUTCOMES (compact, no longer the main animation) */}
          <RepresentativeOutcomes simulation={simulation} homeTeam={homeTeam} awayTeam={awayTeam} />

          {/* 5 — OUTCOME DISTRIBUTIONS */}
          <section className="analysis-section" aria-labelledby="distributions-heading">
            <h2 id="distributions-heading">Outcome distributions</h2>
            <p className="analysis-section__sub">
              The field shows the complete outcome population; the charts below provide its exact
              numerical distribution. Every chart is computed from the same{' '}
              {simulation.inputs?.simulations.toLocaleString()} draws, replayed and verified
              against the stored forecast.
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

          {/* 6 — MARKET COMPARISON */}
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

          {/* 7 — METHODOLOGY */}
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
      </>
      )}
    </div>
  )
}

/**
 * Representative exact scores, kept as a compact reference section.
 * These are real draws chosen by fixed rules — not a playlist to step through.
 */
function RepresentativeOutcomes({
  simulation, homeTeam, awayTeam,
}: { simulation: GameSimulationData; homeTeam: string; awayTeam: string }) {
  const outcomes = (simulation.representative_outcomes ?? []).filter((o) => o.available)
  if (outcomes.length === 0) return null
  return (
    <section className="analysis-section" aria-labelledby="representative-heading">
      <h2 id="representative-heading">Representative outcomes</h2>
      <p className="analysis-section__sub">
        Individual simulated results picked from the population by fixed rules, each citing the
        draw it came from. Representative simulated outcomes—not predictions of the exact game.
      </p>
      <ul className="representative-list">
        {outcomes.map((outcome) => (
          <li key={outcome.key}>
            <span className="representative-list__label">{OUTCOME_LABELS[outcome.key]}</span>
            <strong>{awayTeam} {outcome.away_runs} — {homeTeam} {outcome.home_runs}</strong>
            <small>{outcome.criterion}</small>
            <small className="representative-list__meta">
              draw #{outcome.draw_index?.toLocaleString()} ·{' '}
              {(outcome.share_of_simulations * 100).toFixed(2)}% of all simulations
            </small>
          </li>
        ))}
      </ul>
    </section>
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
