import { useCallback, useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { MarketPulsePanel } from '../components/MarketPulsePanel'
import { ArrowLeft, CheckCircle2, CloudSun, Database, Info, MapPin, ShieldCheck, Sparkles, ArrowRight} from 'lucide-react'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { ComparisonTable } from '../components/ComparisonTable'
import { GameLifecyclePanel } from '../components/GameLifecycle'
import {
  DataModeBanner,
  DataStatePanel,
  formatMoneyline,
  formatNumber,
  formatPct,
  Panel,
  ScoreBar,
  StatusPill,
  TableScroll,
  TeamLogo,
} from '../components/ui'
import { getDemoGame } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { usePublicPolicy } from '../hooks/usePublicPolicy'
import { endpoints } from '../services/api'
import { formatCompactTimestamp, formatTime, formatTimestamp } from '../services/format'

export function GameAnalysisPage({ demo = false }: { demo?: boolean }) {
  const { gameId = 'demo-nym-atl' } = useParams()
  const demoData = useMemo(() => demo ? (() => {
    try { return getDemoGame(gameId) } catch { return null }
  })() : null, [demo, gameId])
  const loader = useCallback((signal: AbortSignal) => {
    if (demo) {
      return demoData
        ? Promise.resolve(demoData)
        : Promise.reject(new Error('Illustrative game not found'))
    }
    return endpoints.game(gameId, signal)
  }, [demo, demoData, gameId])
  const { data, state, loading, error, refresh } = useApiResource(loader, demoData ? { demoData } : {})
  const { policy } = usePublicPolicy()
  const missingLiveGame = !demo && Boolean(error?.toLowerCase().includes('not found'))
  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    const missingDemo = demo && !demoData
    const shownState = missingDemo ? 'unavailable' : state
    return (
      <div className="page game-page">
        <Link className="back-link" to={demo ? '/demo' : '/'}><ArrowLeft size={15} /> {demo ? 'Illustrative forecast' : 'Today’s forecasts'}</Link>
        <DataModeBanner state={missingDemo ? 'demo' : shownState} loading={loading} error={error} onRetry={missingDemo || missingLiveGame ? undefined : refresh} />
        <DataStatePanel state={shownState} emptyTitle="This game has no published live forecast" emptyDetail="The game exists, but ASP has not published an eligible pregame forecast." unavailableTitle={missingDemo ? 'Illustrative game not found' : missingLiveGame ? 'Game not found' : 'Live game analysis unavailable'} unavailableDetail={missingDemo ? 'This demo route has no canonical illustrative game with that ID.' : missingLiveGame ? 'No live game exists with this identifier.' : 'The game could not be loaded. No other matchup or fixture was substituted.'} />
      </div>
    )
  }
  const { game } = data
  const forecastAvailable = game.forecast_available ?? (!game.status.toUpperCase().includes('FORECAST UNAVAILABLE') && game.home_win_probability != null && game.away_win_probability != null)
  const marketAvailable = game.market_available ?? game.books_reporting > 0
  const homeProbability = game.home_win_probability ?? 0
  const awayProbability = game.away_win_probability ?? 0
  const favorite = homeProbability >= awayProbability ? game.home_team : game.away_team
  const favoriteProbability = Math.max(homeProbability, awayProbability)
  const edgeTeam = game.edge_side === 'home' ? game.home_team : game.edge_side === 'away' ? game.away_team : null
  const topDriver = data.drivers[0]
  const windDirection = data.weather.wind_direction?.trim()
  const windDirectionLabel = windDirection && windDirection.toLowerCase() !== 'unavailable'
    ? /^-?\d+(\.\d+)?$/.test(windDirection)
      ? ` at ${formatNumber(Number(windDirection), 0)}°`
      : ` ${windDirection.toUpperCase()}`
    : ''
  const weatherSummary = data.weather.temperature_f == null && data.weather.wind_mph == null && data.weather.humidity == null
    ? data.weather.description || 'Weather unavailable'
    : [
        data.weather.temperature_f == null ? null : `${formatNumber(data.weather.temperature_f, 0)}°F`,
        data.weather.wind_mph == null ? null : `${formatNumber(data.weather.wind_mph, 0)} mph${windDirectionLabel}`,
        data.weather.humidity == null ? null : `${formatNumber(data.weather.humidity, 0)}% humidity`,
      ].filter(Boolean).join(' · ')

  const runDistributionOption = {
    color: [game.away_team.color, game.home_team.color],
    tooltip: { trigger: 'axis' as const },
    legend: { bottom: 0, data: [game.away_team.abbreviation, game.home_team.abbreviation], textStyle: { color: chartTheme.muted } },
    grid: { top: 15, left: 42, right: 16, bottom: 48 },
    xAxis: { type: 'category' as const, data: data.run_distribution.map((point) => point.runs), name: 'Runs', axisLine: { lineStyle: { color: chartTheme.grid } }, axisLabel: { color: chartTheme.muted } },
    yAxis: { type: 'value' as const, name: 'Probability %', axisLine: { show: false }, splitLine: { lineStyle: { color: chartTheme.grid } }, axisLabel: { color: chartTheme.muted } },
    series: [
      { name: game.away_team.abbreviation, type: 'line' as const, smooth: true, symbol: 'circle', symbolSize: 6, data: data.run_distribution.map((point) => point.away), areaStyle: { opacity: .06 } },
      { name: game.home_team.abbreviation, type: 'line' as const, smooth: true, symbol: 'circle', symbolSize: 6, data: data.run_distribution.map((point) => point.home), areaStyle: { opacity: .08 } },
    ],
  }

  const forecastHistoryOption = {
    color: [chartTheme.teal, chartTheme.muted],
    tooltip: { trigger: 'axis' as const },
    legend: { bottom: 0, data: ['ASP forecast', 'No-vig market'], textStyle: { color: chartTheme.muted } },
    grid: { top: 18, left: 44, right: 20, bottom: 48 },
    xAxis: { type: 'category' as const, data: data.forecast_history.map((point) => point.time), axisLine: { lineStyle: { color: chartTheme.grid } }, axisLabel: { color: chartTheme.muted } },
    yAxis: { type: 'value' as const, axisLabel: { formatter: '{value}%', color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    series: [
      { name: 'ASP forecast', type: 'line' as const, data: data.forecast_history.map((point) => point.home_probability), smooth: true, symbolSize: 8, lineStyle: { width: 3 } },
      { name: 'No-vig market', type: 'line' as const, data: data.forecast_history.map((point) => point.market_probability), smooth: true, symbolSize: 6, lineStyle: { width: 2, type: 'dashed' as const } },
    ],
  }

  return (
    <div className="page game-page">
      <Link className="back-link" to={demo ? '/demo' : '/'}><ArrowLeft size={15} /> {demo ? 'Illustrative forecast' : 'Today’s forecasts'}</Link>
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="game-hero">
        <h1 className="sr-only">{game.away_team.name} at {game.home_team.name} game analysis</h1>
        <header className="game-hero__meta">
          <span>{formatTime(game.start_time)} · {game.status}</span>
          <span><MapPin size={13} /> {game.venue}</span>
          <span><CloudSun size={14} /> {weatherSummary}</span>
        </header>
        <div className="game-hero__matchup">
          <TeamHero team={game.away_team} pitcher={game.away_pitcher} side="Away" />
          <div className="game-hero__forecast">
            <span className="eyebrow">ASP PROJECTED SCORE</span>
            <div className="projected-score"><strong>{forecastAvailable ? formatNumber(game.projected_away_runs) : '—'}</strong><i>—</i><strong>{forecastAvailable ? formatNumber(game.projected_home_runs) : '—'}</strong></div>
            <span>{data.forecast_version}</span>
          </div>
          <TeamHero team={game.home_team} pitcher={game.home_pitcher} side="Home" reverse />
        </div>
        {!demo && (
          <Link className="simulator-entry" to={`/games/${game.id}/simulation`}>
            <span>
              <strong>Open the Monte Carlo Diamond Simulator</strong>
              <small>Explore the full distribution of simulated outcomes behind this forecast</small>
            </span>
            <ArrowRight size={16} aria-hidden="true" />
          </Link>
        )}
        {forecastAvailable ? <div className="game-hero__probability">
          <div style={{ width: `${awayProbability}%` }}><span>{game.away_team.abbreviation} {formatPct(awayProbability)}</span></div>
          <div style={{ width: `${homeProbability}%` }}><span>{formatPct(homeProbability)} {game.home_team.abbreviation}</span></div>
        </div> : <div className="game-hero__unavailable">Forecast unavailable · required pregame inputs are incomplete</div>}
        <footer className="game-hero__footer">
          <span><Database size={14} /> Data as of {formatTimestamp(data.data_as_of)}</span>
          <span>{data.model_version}</span>
        </footer>
      </section>

      <GameLifecyclePanel game={game} />

      <nav className="analysis-tabs" aria-label="Analysis sections">
        <a href="#forecast">Forecast</a><a href="#market">Market</a><a href="#matchup">Matchup</a><a href="#simulation">Simulation</a><a href="#history">History</a><a href="#audit">Data audit</a>
      </nav>

      <section className="forecast-summary" id="forecast">
        <article><span>Home win</span><strong>{forecastAvailable ? formatPct(game.home_win_probability) : '—'}</strong><small>{marketAvailable ? `Market ${formatPct(game.market_home_probability)}` : 'Market unavailable'}</small></article>
        <article><span>Fair home moneyline</span><strong>{forecastAvailable ? formatMoneyline(game.fair_home_moneyline) : '—'}</strong><small>{marketAvailable ? `Best current ${formatMoneyline(game.best_home_moneyline)}` : 'Market unavailable'}</small></article>
        <article><span>Model edge</span><strong className={game.model_edge != null && game.model_edge > 0 ? 'positive' : ''}>{forecastAvailable && marketAvailable && game.model_edge != null ? `+${game.model_edge.toFixed(1)} pts` : '—'}</strong><small>{forecastAvailable && marketAvailable ? game.opportunity : 'Not classified'}</small></article>
        <article><span>Projected total</span><strong>{forecastAvailable ? formatNumber(game.projected_total) : '—'}</strong><small>{marketAvailable ? `Market ${formatNumber(game.market_total)}` : 'Market unavailable'}</small></article>
        <article><span>Projected run differential</span><strong>{forecastAvailable && game.projected_run_difference != null ? `${game.projected_run_difference > 0 ? '+' : ''}${formatNumber(game.projected_run_difference)}` : '—'}</strong><small>Home minus away</small></article>
        <article><span>Model uncertainty</span><strong>{forecastAvailable && game.uncertainty != null ? formatPct(game.uncertainty * 100) : '—'}</strong><small>Probability dispersion</small></article>
        <article><ScoreBar value={game.confidence} label="Forecast confidence" tone="blue" /></article>
        <article><ScoreBar value={game.data_readiness} label="Data readiness" tone="teal" /></article>
      </section>

      <div className="analysis-grid analysis-grid--wide-left">
        <Panel title="Model vs. market" eyebrow="WIN PROBABILITY">
          <div className="probability-rows">
            {data.probability_comparison.map((row) => (
              <div className="probability-row" key={row.label}>
                <span>{row.label}</span>
                <div className="probability-row__track"><i style={{ width: `${row.away}%` }}>{row.away.toFixed(1)}%</i><b style={{ width: `${row.home}%` }}>{row.home.toFixed(1)}%</b></div>
              </div>
            ))}
          </div>
          <div className="chart-legend"><span><i className="dot dot--away" /> {game.away_team.abbreviation}</span><span><i className="dot dot--home" /> {game.home_team.abbreviation}</span></div>
        </Panel>
        <Panel title="Forecast interpretation" eyebrow="MODEL SUMMARY" className="narrative-panel">
          <div className="narrative-label"><Sparkles size={15} /> Deterministic explanation</div>
          <p>{forecastAvailable ? <>ASP makes <strong>{favorite.name}</strong> the {formatPct(favoriteProbability)} side. {edgeTeam && marketAvailable && game.model_edge != null ? `${edgeTeam.name} carries a ${game.model_edge.toFixed(1)} percentage-point difference versus the current no-vig market.` : 'A complete two-sided market comparison is not currently available.'} {topDriver ? `The largest reported model driver is ${topDriver.label.toLowerCase()}: ${topDriver.detail}` : 'No model-driver explanation is available for this forecast version.'}</> : <>ASP has not published a forecast because required pregame inputs are incomplete. No probability, fair line, or model edge is inferred.</>}</p>
          <p className="muted">This summary describes model associations, not causal certainty.</p>
        </Panel>
      </div>

      <Panel title="Sportsbook comparison" eyebrow="CURRENT MONEYLINE" action={<span className="update-label">{game.books_reporting} books · updated {game.last_update}</span>} className="section-panel" >
        <TableScroll>
          <table className="data-table market-table" id="market">
            <thead><tr><th>Sportsbook</th><th>{game.away_team.abbreviation}</th><th>{game.home_team.abbreviation}</th><th>Observed</th></tr></thead>
            <tbody>
              {data.market_prices.map((price) => (
                <tr key={price.book}><td><strong>{price.book}</strong></td><td className={price.is_best_away ? 'best-price' : ''}>{formatMoneyline(price.away)}{price.is_best_away && <small> BEST</small>}</td><td className={price.is_best_home ? 'best-price' : ''}>{formatMoneyline(price.home)}{price.is_best_home && <small> BEST</small>}</td><td className="muted">{formatCompactTimestamp(price.observed_at)}</td></tr>
              ))}
              <tr className="table-summary"><td><strong>ASP fair price</strong></td><td>{forecastAvailable ? formatMoneyline(game.fair_away_moneyline) : '—'}</td><td>{forecastAvailable ? formatMoneyline(game.fair_home_moneyline) : '—'}</td><td>{data.model_version}</td></tr>
            </tbody>
          </table>
        </TableScroll>
      </Panel>

      {!demo && <MarketPulsePanel gameId={gameId} demo={demo} />}

      <div className="analysis-grid" id="matchup">
        <Panel title="Starting pitchers" eyebrow="EXPECTED RUN PREVENTION"><ComparisonTable away={game.away_team} home={game.home_team} rows={data.pitcher_metrics} /></Panel>
        <Panel title="Offensive profile" eyebrow="SEASON + RECENT FORM"><ComparisonTable away={game.away_team} home={game.home_team} rows={data.offense_metrics} /></Panel>
        <Panel title="Bullpen state" eyebrow="AVAILABILITY ADJUSTED"><ComparisonTable away={game.away_team} home={game.home_team} rows={data.bullpen_metrics} /></Panel>
        <Panel title="Statcast matchup" eyebrow="CONTACT QUALITY"><ComparisonTable away={game.away_team} home={game.home_team} rows={data.statcast_metrics} /></Panel>
      </div>

      <div className="analysis-grid analysis-grid--wide-left" id="simulation">
        <Panel title="Simulated run distribution" eyebrow={data.simulation.iterations > 0 ? `${data.simulation.iterations.toLocaleString()} ITERATIONS` : 'SIMULATION UNAVAILABLE'}>
          <AnalyticsChart option={runDistributionOption} height={310} ariaLabel="Probability distribution of simulated runs for both teams" />
        </Panel>
        <Panel title="Simulation readout" eyebrow="OUTCOME FREQUENCIES">
          <div className="simulation-readout">
            <div><span>Most likely score</span><strong>{data.simulation.most_likely_score}</strong></div>
            <div><span>{game.home_team.abbreviation} −1.5 cover</span><strong>{data.simulation.iterations > 0 ? formatPct(data.simulation.home_cover_probability) : '—'}</strong></div>
            <div><span>Over {marketAvailable ? formatNumber(game.market_total) : '—'}</span><strong>{data.simulation.iterations > 0 && marketAvailable ? formatPct(data.simulation.over_probability) : '—'}</strong></div>
            <p><Info size={14} /> Frequencies reflect model inputs and distributional assumptions; they are not outcome guarantees.</p>
          </div>
        </Panel>
      </div>

      <div className="analysis-grid">
        <Panel title="Model drivers" eyebrow="HOME WIN PROBABILITY IMPACT">
          <div className="drivers">
            {data.drivers.map((driver) => (
              <div className="driver" key={driver.label}>
                <div><strong>{driver.label}</strong><span className={driver.impact >= 0 ? 'positive' : 'negative'}>{driver.impact >= 0 ? '+' : ''}{driver.impact.toFixed(1)} pts</span></div>
                <div className="driver__axis"><span className={driver.impact >= 0 ? 'driver__bar driver__bar--positive' : 'driver__bar driver__bar--negative'} style={{ width: `${Math.min(Math.abs(driver.impact) / 4 * 48, 50)}%` }} /></div>
                <p>{driver.detail}</p>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Ensemble components" eyebrow="TRANSPARENT WEIGHTS">
          <TableScroll><table className="data-table compact-table"><thead><tr><th>Component</th><th>Home win</th><th>Margin</th><th>Weight</th></tr></thead><tbody>{data.model_components.map((model) => {
            const modelName = model.model.toLowerCase()
            const configuredWeight = modelName.includes('baseline')
              ? policy.componentWeights.baseline
              : modelName.includes('run')
                ? policy.componentWeights.run
                : modelName.includes('boost')
                  ? policy.componentWeights.boosting
                  : null
            const weight = model.weight ?? configuredWeight
            return <tr key={model.model}><td><strong>{model.model}</strong></td><td>{formatPct(model.home_probability)}</td><td>{model.expected_margin > 0 ? '+' : ''}{model.expected_margin.toFixed(2)}</td><td>{weight == null ? 'Configuration unavailable' : `${weight}%`}</td></tr>
          })}</tbody></table></TableScroll>
        </Panel>
      </div>

      <div className="analysis-grid analysis-grid--wide-left" id="history">
        <Panel title="Forecast history" eyebrow="IMMUTABLE VERSIONS">
          <AnalyticsChart option={forecastHistoryOption} height={310} ariaLabel="History of ASP and market home win probability" />
        </Panel>
        <Panel title="Version ledger" eyebrow="PUBLISHED SNAPSHOTS">
          <div className="version-list">
            {data.forecast_history.map((version, index) => (
              <div key={version.version}><CheckCircle2 size={15} /><span><strong>{version.version}</strong><small>{formatCompactTimestamp(version.time)}{version.lineup_status ? ` · Lineup ${version.lineup_status}` : ''}{version.rescore_reason ? ` · ${version.rescore_reason}` : ''}</small></span><b>{formatPct(version.home_probability)}</b>{index === data.forecast_history.length - 1 && <em>Current</em>}</div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="Data audit" eyebrow="SOURCE FRESHNESS & COMPLETENESS" className="section-panel" action={<span className="score-chip">{game.data_readiness == null ? 'Readiness unavailable' : `${Math.round(game.data_readiness)}/100 ready`}</span>}>
        <div className="readiness-breakdown" aria-label="Readiness component detail">
          {(game.readiness_detail ?? []).map((component) => (
            <article key={component.key}>
              <div><StatusPill status={readinessState(component.status)} />{component.critical && <span className="critical-input">Critical</span>}</div>
              <strong>{component.label}</strong>
              <span>{component.detail}</span>
              <small>{component.score == null || component.weight == null ? 'Score not recorded' : `${component.score.toFixed(1)} / ${component.weight.toFixed(1)} points`}</small>
            </article>
          ))}
          {!(game.readiness_detail ?? []).length && <p className="readiness-breakdown__empty">No component-level readiness evidence is recorded for this forecast.</p>}
        </div>
        <div className="audit-grid" id="audit">
          {data.data_audit.map((source) => (
            <article key={source.source}><StatusPill status={source.status} /><strong>{source.source}</strong><span>{source.detail}</span><small>{source.updated}</small></article>
          ))}
        </div>
      </Panel>

      <p className="page-footnote">All values on this page are pregame estimates tied to the forecast version shown. Published versions are retained. No model driver should be interpreted as proof of causality.</p>
    </div>
  )
}

function readinessState(status: string): 'ready' | 'watch' | 'stale' | 'missing' {
  const normalized = status.toLowerCase()
  if (normalized === 'ready' || normalized === 'stale' || normalized === 'missing') return normalized
  return 'watch'
}

function TeamHero({
  team,
  pitcher,
  side,
  reverse = false,
}: {
  team: { abbreviation: string; city: string; name: string; record: string; color: string }
  pitcher: { name: string; hand: 'RHP' | 'LHP' | null; confirmed: boolean }
  side: string
  reverse?: boolean
}) {
  const displayName = team.name.toLowerCase().startsWith(team.city.toLowerCase())
    ? team.name
    : `${team.city} ${team.name}`
  return (
    <div className={`team-hero ${reverse ? 'team-hero--reverse' : ''}`}>
      <TeamLogo abbreviation={team.abbreviation} color={team.color} size="lg" />
      <div><span>{side} · {team.record}</span><h2><strong>{displayName}</strong></h2><small>{pitcher.name}{pitcher.hand ? ` · ${pitcher.hand}` : ''} {pitcher.confirmed ? <><ShieldCheck size={12} /> confirmed</> : '· probable'}</small></div>
    </div>
  )
}
