import { Beaker, BookOpenText, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react'
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

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading' || (!demo && data.experiment_status === 'NOT_STARTED')) {
    const shownState = !demo && data?.experiment_status === 'NOT_STARTED' ? 'empty' : state
    return (
      <div className="page bankroll-page">
        <PageHeader eyebrow="THE OFFICIAL ASP EXPERIMENT" title="Can AI Beat the Sportsbooks?" description="A transparent virtual-bankroll forecasting experiment." actions={<span className="experiment-badge"><Beaker size={14} /> SIMULATION ONLY</span>} />
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
      <PageHeader eyebrow={demo ? 'ILLUSTRATIVE EXPERIMENT EXAMPLE' : 'THE OFFICIAL ASP EXPERIMENT'} title="Can AI Beat the Sportsbooks?" description={demo ? 'Example positions demonstrate the ledger; these are not live or historical results.' : 'A transparent $1,000 virtual-bankroll forecasting experiment.'} actions={<span className="experiment-badge"><Beaker size={14} /> SIMULATION ONLY</span>} />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="experiment-hero">
        <div><span>Current virtual bankroll</span><strong>{formatCurrency(data.current_bankroll)}</strong><small>Opened at {formatCurrency(data.starting_bankroll, 0)}</small></div>
        <div className="experiment-hero__return"><TrendingUp size={21} /><span><strong>{formatSignedCurrency(data.total_return)}</strong><small>Total simulated return</small></span></div>
        <p>No deposits. No withdrawals. No deleted losses. Every official ASP position remains in the public ledger.</p>
      </section>

      <section className="metric-grid metric-grid--five">
        <MetricCard label="ROI on stake" value={formatSignedPct(data.roi)} detail="Settled positions" trend={data.roi > 0 ? 'positive' : data.roi < 0 ? 'negative' : 'neutral'} />
        <MetricCard label="Peak bankroll" value={formatCurrency(data.peak_bankroll)} detail="Season high-water mark" />
        <MetricCard label="Max drawdown" value={formatPct(data.max_drawdown)} detail="Peak-to-trough" trend="negative" />
        <MetricCard label="Official record" value={data.record} detail="Win–loss–push" />
        <MetricCard label="Positions" value={data.positions_count} detail="Full history retained" />
      </section>

      <Panel title="Bankroll equity curve" eyebrow="CUMULATIVE SIMULATED PERFORMANCE" className="equity-panel">
        <AnalyticsChart option={equityOption} height={380} ariaLabel="ASP virtual bankroll over time compared with opening bankroll" />
      </Panel>

      <div className="analysis-grid experiment-details">
        <Panel title="Staking policy" eyebrow="PROVISIONAL · PHASE 1">
          <div className="policy-card"><ShieldAlert size={21} /><div><strong>{data.policy || policy.bankroll.label}</strong><p>{data.policy_detail}</p></div></div>
          <dl className="policy-details"><div><dt>Sizing method</dt><dd>{data.policy_kelly_fraction != null ? `${data.policy_kelly_fraction * 100}% Kelly` : policy.bankroll.kellyFraction != null ? `${policy.bankroll.kellyFraction * 100}% Kelly` : 'Configuration unavailable'}</dd></div><div><dt>Maximum stake</dt><dd>{data.policy_max_stake_percentage != null ? `${data.policy_max_stake_percentage}% of current bankroll` : policy.bankroll.maximumStakePercentage != null ? `${policy.bankroll.maximumStakePercentage}% of current bankroll` : 'Configuration unavailable'}</dd></div><div><dt>Loss recovery</dt><dd>None</dd></div><div><dt>Real-money execution</dt><dd>Not supported</dd></div></dl>
        </Panel>
        <Panel title="Monthly return" eyebrow="COMPLETE SEASON LEDGER">
          <div className="monthly-list">{data.monthly.map((month) => <div key={month.month}><span><strong>{month.month}</strong><small>{month.positions} positions · {month.record}</small></span><b className={month.profit_loss >= 0 ? 'positive' : 'negative'}>{formatSignedCurrency(month.profit_loss)}</b><em>{formatSignedPct(month.roi)}</em></div>)}</div>
        </Panel>
      </div>

      <Panel title="Every official ASP position" eyebrow="IMMUTABLE POSITION HISTORY" className="section-panel" action={<span className="info-label"><BookOpenText size={13} /> No cherry-picking</span>}>
        <TableScroll><table className="data-table positions-table"><thead><tr><th>Date</th><th>Position</th><th>Market</th><th>Price</th><th>Stake</th><th>Result</th><th>P/L</th><th>Closing line value</th></tr></thead><tbody>{data.positions.map((position) => <tr key={position.id}><td>{position.date}</td><td><Link to={`${demo ? '/demo' : ''}/games/${position.game_id}`}><strong>{position.selection}</strong></Link></td><td>{position.market}</td><td>{position.price > 0 ? '+' : ''}{position.price}</td><td>{formatCurrency(position.stake)}</td><td><span className={`result-pill result-pill--${position.result.toLowerCase()}`}>{position.result}</span></td><td className={position.profit_loss > 0 ? 'positive' : position.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(position.profit_loss)}</td><td>{position.clv === null ? 'Unavailable' : formatSignedPct(position.clv)}</td></tr>)}</tbody></table></TableScroll>
      </Panel>

      <div className="responsible-callout"><TrendingDown size={18} /><p><strong>A positive historical simulation does not promise future profit.</strong> This experiment evaluates a forecasting process under explicit assumptions. It does not provide personalized financial or wagering advice, and ASP never encourages chasing losses.</p></div>
    </div>
  )
}
