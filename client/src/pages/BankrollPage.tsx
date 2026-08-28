import { Beaker, BookOpenText, CalendarClock, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react'
import { Link } from 'react-router-dom'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { DataModeBanner, DataStatePanel, formatCurrency, formatPct, formatSignedCurrency, formatSignedPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoBankroll } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { usePublicPolicy } from '../hooks/usePublicPolicy'

export function BankrollPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.bankroll, demo ? { demoData: demoBankroll } : {})
  const { policy } = usePublicPolicy()
  const experimentIsActive = demo || data?.experiment_status === 'ACTIVE'

  if (!data || state === 'unavailable' || state === 'loading' || (!experimentIsActive && (state === 'empty' || data.experiment_status === 'NOT_STARTED'))) {
    const shownState = !demo && data?.experiment_status === 'NOT_STARTED' ? 'empty' : state
    return (
      <div className="page bankroll-page">
        <PageHeader eyebrow="THE OFFICIAL ASP EXPERIMENT" title="Can AI Beat the Sportsbooks?" description="A transparent virtual-bankroll forecasting experiment." actions={<ExperimentBadge />} />
        <DataModeBanner state={shownState} loading={loading} error={error} onRetry={refresh} />
        {shownState === 'empty' && (
          <section className="metric-grid metric-grid--three empty-bankroll-summary" aria-label="Official experiment opening state">
            <MetricCard label="Starting bankroll" value={formatCurrency(data?.starting_bankroll ?? 1000, 0)} detail="Official opening baseline" />
            <MetricCard label="Current bankroll" value={formatCurrency(data?.current_bankroll ?? 1000, 0)} detail="No live return recorded" />
            <MetricCard label="Official positions" value={data?.positions_count ?? 0} detail="No positions published" />
          </section>
        )}
        <DataStatePanel state={shownState} emptyTitle="The official experiment has not started" emptyDetail="No official live positions have been published. The opening bankroll is not a performance result." />
      </div>
    )
  }

  const daily = [...(data.daily ?? [])].sort((left, right) => right.date.localeCompare(left.date))
  const latestDay = daily[0]
  const totalRisked = data.betting?.total_risked ?? data.positions.reduce((sum, position) => sum + position.stake, 0)
  const hasSettled = data.betting?.has_settled ?? (data.settled_positions ?? 0) > 0
  const closingCoverage = data.closing_quote_coverage
  const legitimateClv = hasSettled && (closingCoverage?.positions_with_closing_quote ?? 0) > 0 && data.betting?.closing_line_value != null

  const equityOption = {
    color: [chartTheme.teal, chartTheme.muted],
    tooltip: { trigger: 'axis' as const, valueFormatter: (value: unknown) => formatCurrency(Number(value)) },
    legend: { bottom: 0, data: ['ASP virtual bankroll', 'Opening bankroll'], textStyle: { color: chartTheme.muted } },
    grid: { top: 20, left: 62, right: 20, bottom: 48 },
    xAxis: { type: 'category' as const, boundaryGap: false, data: data.equity_curve.map((point) => point.date), axisLabel: { color: chartTheme.muted }, axisLine: { lineStyle: { color: chartTheme.grid } } },
    yAxis: { type: 'value' as const, min: Math.min(950, ...data.equity_curve.map((point) => point.bankroll - 20)), axisLabel: { formatter: '${value}', color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    series: [
      { name: 'ASP virtual bankroll', type: 'line' as const, smooth: .25, data: data.equity_curve.map((point) => point.bankroll), symbolSize: 7, lineStyle: { width: 3 }, areaStyle: { opacity: .08 } },
      { name: 'Opening bankroll', type: 'line' as const, data: data.equity_curve.map((point) => point.benchmark), symbol: 'none', lineStyle: { type: 'dashed' as const, width: 1.5 } },
    ],
  }

  return (
    <div className="page bankroll-page">
      <PageHeader eyebrow={demo ? 'ILLUSTRATIVE EXPERIMENT EXAMPLE' : 'THE OFFICIAL ASP EXPERIMENT'} title="Can AI Beat the Sportsbooks?" description={demo ? 'Example positions demonstrate the ledger; these are not live or historical results.' : 'A transparent $1,000 virtual-bankroll forecasting experiment.'} actions={<ExperimentBadge />} />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="experiment-hero">
        <div><span>Current virtual bankroll</span><strong>{formatCurrency(data.current_bankroll)}</strong><small>Opened at {formatCurrency(data.starting_bankroll, 0)}</small></div>
        <div className="experiment-hero__return"><TrendingUp size={21} /><span><strong>{formatSignedCurrency(data.total_return)}</strong><small>Total simulated return</small></span></div>
        <p>No deposits. No withdrawals. No deleted losses. Every position remains in the public ledger.</p>
        <div className="experiment-hero__meta">
          <strong>{data.experiment_name ?? (demo ? 'Illustrative experiment example' : 'Experiment name unavailable')}</strong>
          <small>
            ID {data.experiment_id ?? 'unavailable'} · Started {data.experiment_start ?? 'unavailable'} · Engine {data.controlling_engine ?? 'unavailable'}
          </small>
          <small>{data.settled_positions ?? 0} settled / {data.open_positions ?? 0} open · {data.no_bet_days ?? 0} NO-BET days</small>
        </div>
      </section>

      <section className="metric-grid metric-grid--five">
        <MetricCard label="Amount risked" value={formatCurrency(totalRisked)} detail={`${data.positions_count} virtual positions`} />
        <MetricCard label="Daily P/L" value={latestDay ? formatSignedCurrency(latestDay.profit_loss) : '—'} detail={latestDay ? latestDay.date : 'Collecting daily evidence'} trend={!latestDay ? 'neutral' : latestDay.profit_loss > 0 ? 'positive' : latestDay.profit_loss < 0 ? 'negative' : 'neutral'} />
        <MetricCard label="Current drawdown" value={formatPct(data.current_drawdown ?? 0)} detail="From current high-water mark" trend="negative" />
        <MetricCard label="Max drawdown" value={formatPct(data.max_drawdown)} detail="Peak-to-trough" trend="negative" />
        <MetricCard label="ROI on stake" value={hasSettled ? formatSignedPct(data.roi) : '—'} detail={hasSettled ? 'Settled positions' : 'Awaiting settled positions'} trend={!hasSettled ? 'neutral' : data.roi > 0 ? 'positive' : data.roi < 0 ? 'negative' : 'neutral'} />
      </section>

      {!hasSettled && (
        <div className="experiment-collecting-state" role="status">
          <CalendarClock size={19} />
          <div><strong>ACTIVE · COLLECTING DATA</strong><span>The experiment is live. ROI, yield, and closing-line performance remain unpublished until eligible positions settle.</span></div>
        </div>
      )}

      <Panel title="Bankroll equity curve" eyebrow="CUMULATIVE SIMULATED PERFORMANCE" className="equity-panel">
        <AnalyticsChart option={equityOption} height={380} ariaLabel="ASP virtual bankroll over time compared with opening bankroll" />
      </Panel>

      <div className="analysis-grid experiment-details">
        <Panel title="Staking policy" eyebrow="PROVISIONAL · PHASE 1">
          <div className="policy-card"><ShieldAlert size={21} /><div><strong>{data.policy || policy.bankroll.label}</strong><p>{data.policy_detail}</p></div></div>
          <dl className="policy-details"><div><dt>Sizing method</dt><dd>{data.policy_kelly_fraction != null ? `${data.policy_kelly_fraction * 100}% Kelly` : policy.bankroll.kellyFraction != null ? `${policy.bankroll.kellyFraction * 100}% Kelly` : 'Configuration unavailable'}</dd></div><div><dt>Maximum stake</dt><dd>{data.policy_max_stake_percentage != null ? `${data.policy_max_stake_percentage}% of current bankroll` : policy.bankroll.maximumStakePercentage != null ? `${policy.bankroll.maximumStakePercentage}% of current bankroll` : 'Configuration unavailable'}</dd></div><div><dt>Loss recovery</dt><dd>None</dd></div><div><dt>Real-money execution</dt><dd>Not supported</dd></div></dl>
        </Panel>
        <Panel title="Experiment identity" eyebrow="FROZEN AT EXPERIMENT START">
          <dl className="policy-details">
            <div><dt>Name</dt><dd>{data.experiment_name ?? (demo ? 'Illustrative experiment example' : 'Unavailable')}</dd></div>
            <div><dt>Experiment ID</dt><dd>{data.experiment_id ?? 'Unavailable'}</dd></div>
            <div><dt>Start timestamp</dt><dd>{data.experiment_start ?? 'Unavailable'}</dd></div>
            <div><dt>Controlling engine</dt><dd>{data.controlling_engine ?? 'Unavailable'}</dd></div>
            <div><dt>Supported markets</dt><dd>{data.supported_markets?.join(', ') || 'Unavailable'}</dd></div>
            <div><dt>Code identity</dt><dd>{data.code_commit ?? 'Unavailable'}</dd></div>
          </dl>
        </Panel>
      </div>

      <Panel title="Latest daily decisions" eyebrow="DAILY POINT-IN-TIME EVIDENCE" className="section-panel">
        <TableScroll><table className="data-table"><thead><tr><th>Date</th><th>Positions</th><th>Settled</th><th>Amount risked</th><th>Daily P/L</th><th>Bankroll after</th><th>Decision</th></tr></thead><tbody>
          {daily.length ? daily.map((row) => <tr key={row.date}><td><strong>{row.date}</strong></td><td>{row.positions}</td><td>{row.settled}</td><td>{formatCurrency(row.amount_risked)}</td><td className={row.profit_loss > 0 ? 'positive' : row.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(row.profit_loss)}</td><td>{formatCurrency(row.bankroll_after)}</td><td>{row.no_bet ? <span><strong>NO BET</strong>{row.no_bet_reasons.length ? <small className="daily-decision-reasons">{row.no_bet_reasons.join(' · ')}</small> : null}</span> : 'POSITION DAY'}</td></tr>) : <tr><td colSpan={7} className="empty-row">No daily decision inventory has been published yet. The active experiment is collecting evidence.</td></tr>}
        </tbody></table></TableScroll>
      </Panel>

      <section className="metric-grid metric-grid--three" aria-label="Position and closing quote coverage">
        <MetricCard label="Open positions" value={data.open_positions ?? 0} detail="Awaiting eligible final results" />
        <MetricCard label="Settled positions" value={data.settled_positions ?? 0} detail="Settled exactly once" />
        <MetricCard label="Closing quote coverage" value={closingCoverage?.percentage == null ? 'Unavailable' : formatPct(closingCoverage.percentage)} detail={closingCoverage?.percentage == null ? 'No legitimate closing sample' : `${closingCoverage.positions_with_closing_quote} of ${closingCoverage.settled_positions} settled positions`} />
      </section>

      <Panel title="Monthly return" eyebrow="COMPLETE SEASON LEDGER" className="section-panel">
        {data.monthly.length ? <div className="monthly-list">{data.monthly.map((month) => <div key={month.month}><span><strong>{month.month}</strong><small>{month.positions} positions · {month.record}</small></span><b className={month.profit_loss >= 0 ? 'positive' : 'negative'}>{formatSignedCurrency(month.profit_loss)}</b><em>{month.positions ? formatSignedPct(month.roi) : '—'}</em></div>)}</div> : <p className="empty-hint">Monthly performance will appear only after positions settle.</p>}
      </Panel>

      <Panel title="Every official ASP position" eyebrow="IMMUTABLE POSITION HISTORY" className="section-panel" action={<span className="info-label"><BookOpenText size={13} /> No cherry-picking</span>}>
        <TableScroll><table className="data-table positions-table"><thead><tr><th>Date</th><th>Position</th><th>Market</th><th>Price</th><th>Stake</th><th>Result</th><th>P/L</th><th>Closing line value</th></tr></thead><tbody>{data.positions.length ? data.positions.map((position) => <tr key={position.id}><td>{position.date}</td><td><Link to={`${demo ? '/demo' : ''}/games/${position.game_id}`}><strong>{position.selection}</strong></Link></td><td>{position.market}</td><td>{position.price > 0 ? '+' : ''}{position.price}</td><td>{formatCurrency(position.stake)}</td><td><span className={`result-pill result-pill--${position.result.toLowerCase()}`}>{position.result}</span></td><td className={position.profit_loss > 0 ? 'positive' : position.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(position.profit_loss)}</td><td>{position.clv === null ? 'Unavailable' : formatSignedPct(position.clv)}</td></tr>) : <tr><td colSpan={8} className="empty-row">No virtual positions have been frozen. A zero-position day is a valid experiment result.</td></tr>}</tbody></table></TableScroll>
      </Panel>

      {legitimateClv && <p className="experiment-clv-note">Aggregate legitimate CLV: <strong>{formatSignedPct(data.betting!.closing_line_value!)}</strong>. Missing closing quotes are not imputed.</p>}

      <div className="responsible-callout"><TrendingDown size={18} /><p><strong>A positive historical simulation does not promise future profit.</strong> This experiment evaluates a forecasting process under explicit assumptions. It does not provide personalized financial or wagering advice, and ASP never encourages chasing losses.</p></div>
    </div>
  )
}

function ExperimentBadge() {
  return <span className="experiment-badge experiment-badge--research"><Beaker size={16} /><span><strong>VIRTUAL RESEARCH EXPERIMENT</strong><small>NO REAL WAGERS</small></span></span>
}
