import { useEffect, useMemo, useState } from 'react'
import { Beaker, BookOpenText, CalendarClock, ShieldAlert, TrendingDown } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { DataModeBanner, DataStatePanel, formatCurrency, formatPct, formatSignedCurrency, formatSignedPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoBankroll } from '../data/demo'
import { QualificationFunnel } from '../components/QualificationFunnel'
import { OperationalDateNav } from '../components/OperationalDateNav'
import { operationalToday, resolveOperationalDate } from '../services/operationalDate'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { usePublicPolicy } from '../hooks/usePublicPolicy'
import type { BankrollData, ExperimentComparison } from '../types/domain'

type PositionRow = BankrollData['positions'][number]

/** "AISP_PRACTICAL_PREGAME_POLICY_V2_1" → "V2.1"; null when no version suffix. */
export function policyVersionLabel(policy: string | null | undefined): string | null {
  const m = policy?.match(/_V(\d+(?:_\d+)*)$/)
  return m ? `V${m[1].replace(/_/g, '.')}` : null
}

function ExperimentComparisonPanel() {
  const [data, setData] = useState<ExperimentComparison | null>(null)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    endpoints.experimentComparison(controller.signal).then((d) => active && setData(d)).catch(() => undefined)
    return () => {
      active = false
      controller.abort()
    }
  }, [])
  if (!data || (data.experiments?.length ?? 0) < 2) return null
  const v1 = data.experiments.find((row) => row.key === 'v1')
  const v2 = data.experiments.find((row) => row.key === 'v2')
  if (!v1 || !v2) return null
  const num = (value: number | null | undefined, suffix = '') => (value == null ? '—' : `${value}${suffix}`)
  const v2Label = policyVersionLabel(v2.policy) ?? 'V2'
  const model = (row: typeof v1) => (row.model_name ? `${row.model_name} ${row.model_version ?? ''}`.trim() : 'unavailable')
  const m1 = v1.decision_metrics
  const m2 = v2.decision_metrics
  const rows: Array<[string, string, string]> = [
    ['Status', v1.status, `${v2.status}${v2.status === 'SCHEDULED' ? ` · starts ${v2.effective_date}` : ''}`],
    ['Role', v1.policy_label ?? 'Strict control', v2.policy_label ?? `Policy challenger — ${v2Label}`],
    ['Forecasting model', model(v1), model(v2)],
    ['Decision policy', v1.policy, v2.policy],
    ['Bankroll', formatCurrency(v1.current_bankroll), formatCurrency(v2.current_bankroll)],
    ['Games evaluated', String(m1?.games_evaluated ?? v1.games_evaluated), String(m2?.games_evaluated ?? v2.games_evaluated)],
    ['Candidate evaluations (per phase)', String(m1?.candidate_evaluations ?? v1.decisions), String(m2?.candidate_evaluations ?? v2.decisions)],
    ['Qualified evaluations', String(m1?.qualified_evaluations ?? v1.qualified_decisions), String(m2?.qualified_evaluations ?? v2.qualified_decisions)],
    ['Qualified selections (distinct)', m1 ? String(m1.qualified_selections) : '—', m2 ? String(m2.qualified_selections) : '—'],
    ['Frozen positions', String(m1?.frozen_positions ?? v1.positions), String(m2?.frozen_positions ?? v2.positions)],
    ['Average raw edge', num(v1.average_raw_edge, ' pts'), num(v2.average_raw_edge, ' pts')],
    ['Average adjusted edge', num(v1.average_adjusted_edge, ' pts'), num(v2.average_adjusted_edge, ' pts')],
    ['Average stake', v1.average_stake == null ? '—' : formatCurrency(v1.average_stake), v2.average_stake == null ? '—' : formatCurrency(v2.average_stake)],
    ['Record', v1.settled ? (v1.record ?? '—') : 'No settled positions', v2.settled ? (v2.record ?? '—') : 'No settled positions'],
    ['P/L (settled only)', v1.settled ? formatSignedCurrency(v1.profit_loss ?? 0) : '—', v2.settled ? formatSignedCurrency(v2.profit_loss ?? 0) : '—'],
    ['Average CLV (settled only)', v1.settled && v1.average_clv != null ? `${v1.average_clv}%` : '—', v2.settled && v2.average_clv != null ? `${v2.average_clv}%` : '—'],
  ]
  return (
    <Panel title={`V1 vs ${v2Label}`} eyebrow="STRICT CONTROL VS PRACTICAL POLICY CHALLENGER · SAME MODEL · SEPARATE LEDGERS — NEVER POOLED" className="section-panel">
      <TableScroll ariaLabel="Experiment comparison">
        <table className="data-table">
          <thead><tr><th>Metric</th><th>{v1.name}</th><th>{v2.name}</th></tr></thead>
          <tbody>
            {rows.map(([label, a, b]) => (
              <tr key={label}><td><strong>{label}</strong></td><td>{a}</td><td>{b}</td></tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="pulse-health-meta">
        {v2.policy_relationship ?? 'The challenger changes only the decision policy; the forecasting model is shared.'}
        {' '}Candidate evaluations count every pregame phase snapshot and are not positions.
        {m2?.evidence_note ? ` ${v2Label}: ${m2.evidence_note}` : ''}
      </p>
      <p className="pulse-health-meta">Result and CLV columns populate only after legitimate settlement. No profitability is claimed.</p>
    </Panel>
  )
}

function PositionTable({ positions, ariaLabel }: { positions: PositionRow[]; ariaLabel: string }) {
  return (
    <TableScroll ariaLabel={ariaLabel}>
      <table className="data-table positions-table">
        <thead><tr><th>Date</th><th>Position</th><th>Market</th><th>Price</th><th>Stake</th><th>Result</th><th>P/L</th><th>Closing line value</th></tr></thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.id}>
              <td>{position.date}</td>
              <td><Link to={`/games/${position.game_id}`}><strong>{position.selection}</strong></Link></td>
              <td>{position.market}</td>
              <td>{position.price > 0 ? '+' : ''}{position.price}</td>
              <td>{formatCurrency(position.stake)}</td>
              <td><span className={`result-pill result-pill--${position.result.toLowerCase()}`}>{position.result}</span></td>
              <td className={position.profit_loss > 0 ? 'positive' : position.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(position.profit_loss)}</td>
              <td>{position.clv === null ? 'Unavailable' : formatSignedPct(position.clv)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

export function BankrollPage({ demo = false }: { demo?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const experimentKey: 'v1' | 'v2' = searchParams.get('experiment') === 'v2' ? 'v2' : 'v1'
  const loader = useMemo(
    () => (signal?: AbortSignal) => endpoints.bankroll(signal, experimentKey),
    [experimentKey],
  )
  const { data, state, loading, error, refresh } = useApiResource(loader, demo ? { demoData: demoBankroll } : {})
  const { policy } = usePublicPolicy()
  const funnelDate = resolveOperationalDate(searchParams.get('date'))
  const setFunnelDate = (next: string) => {
    const params = new URLSearchParams(searchParams)
    params.set('date', next)
    setSearchParams(params)
  }
  const setExperiment = (next: 'v1' | 'v2') => {
    const params = new URLSearchParams(searchParams)
    if (next === 'v2') params.set('experiment', 'v2')
    else params.delete('experiment')
    setSearchParams(params)
  }
  const experimentIsActive = demo || data?.experiment_status === 'ACTIVE' || data?.experiment_status === 'SCHEDULED'
  // The challenger's version label is derived from the policy the backend
  // reports (…_POLICY_V2_1 → "V2.1"), never hard-coded to a stale "V2".
  const challengerVersion = policyVersionLabel(experimentKey === 'v2' ? data?.policy : null) ?? 'V2.1'

  const selector = !demo && (
    <div className="tab-bar" role="tablist" aria-label="Experiment selector">
      <button role="tab" aria-selected={experimentKey === 'v1'} className={experimentKey === 'v1' ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setExperiment('v1')}>Strict control — V1</button>
      <button role="tab" aria-selected={experimentKey === 'v2'} className={experimentKey === 'v2' ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setExperiment('v2')}>Practical pregame policy challenger — {challengerVersion}</button>
    </div>
  )
  const headerEyebrow = demo
    ? 'ILLUSTRATIVE EXPERIMENT EXAMPLE'
    : experimentKey === 'v2'
      ? `PRACTICAL PREGAME POLICY CHALLENGER — ${challengerVersion} · SAME MODEL · DIFFERENT DECISION POLICY`
      : 'THE OFFICIAL ASP EXPERIMENT · STRICT CONTROL — V1'

  if (!data || state === 'unavailable' || state === 'loading' || (!experimentIsActive && (state === 'empty' || data.experiment_status === 'NOT_STARTED'))) {
    const shownState = !demo && data?.experiment_status === 'NOT_STARTED' ? 'empty' : state
    return (
      <div className="page bankroll-page">
        <PageHeader eyebrow={headerEyebrow} title="Can AI Beat the Sportsbooks?" description="A transparent virtual-bankroll forecasting experiment." actions={<ExperimentBadge />} />
        {selector}
        <DataModeBanner state={shownState} loading={loading} error={error} onRetry={refresh} />
        {shownState === 'empty' && (
          <section className="metric-grid metric-grid--three empty-bankroll-summary" aria-label="Official experiment opening state">
            <MetricCard label="Starting bankroll" value={formatCurrency(data?.starting_bankroll ?? 1000, 0)} detail="Official opening baseline" />
            <MetricCard label="Current bankroll" value={formatCurrency(data?.current_bankroll ?? 1000, 0)} detail="No live return recorded" />
            <MetricCard label="Official positions" value={data?.positions_count ?? 0} detail="No positions published" />
          </section>
        )}
        <DataStatePanel
          state={shownState}
          emptyTitle={experimentKey === 'v2' ? `The ${challengerVersion} practical policy challenger is not registered` : 'The official experiment has not started'}
          emptyDetail={experimentKey === 'v2'
            ? 'The practical pregame policy challenger has not been registered. When registered it starts prospectively on the next Eastern operational date with its own separate $1,000 virtual bankroll, using the same forecasting model as the V1 control under a different decision policy.'
            : 'No official live positions have been published. The opening bankroll is not a performance result.'}
        />
      </div>
    )
  }

  const daily = [...(data.daily ?? [])].sort((left, right) => right.date.localeCompare(left.date))
  const today = operationalToday()
  const todayRow = daily.find((row) => row.date === today)
  const totalRisked = data.betting?.total_risked ?? data.positions.reduce((sum, position) => sum + position.stake, 0)
  const hasSettled = data.betting?.has_settled ?? (data.settled_positions ?? 0) > 0
  const closingCoverage = data.closing_quote_coverage
  const legitimateClv = hasSettled && (closingCoverage?.positions_with_closing_quote ?? 0) > 0 && data.betting?.closing_line_value != null
  const openPositions = data.positions.filter((position) => position.result === 'OPEN')
  const settledPositions = data.positions.filter((position) => position.result !== 'OPEN')
  const openCount = data.open_positions ?? openPositions.length
  const settledCount = data.settled_positions ?? settledPositions.length

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
      {/* 1. Title + research disclosure */}
      <PageHeader eyebrow={headerEyebrow} title="Can AI Beat the Sportsbooks?" description={demo ? 'Example positions demonstrate the ledger; these are not live or historical results.' : 'A transparent $1,000 virtual-bankroll forecasting experiment. No deposits. No withdrawals. No deleted losses.'} actions={<ExperimentBadge />} />
      {selector}
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      {/* 2. Bankroll / open / settled — the first visible decision area */}
      <section className="metric-grid metric-grid--three experiment-kpi-primary" aria-label="Bankroll and position counts">
        <MetricCard label="Current bankroll" value={formatCurrency(data.current_bankroll)} detail={`Opened at ${formatCurrency(data.starting_bankroll, 0)}`} />
        <MetricCard label="Open positions" value={openCount} detail={openCount ? 'Awaiting eligible final results' : 'No open virtual positions'} />
        <MetricCard label="Settled positions" value={settledCount} detail={settledCount ? 'Settled exactly once' : 'No settled positions yet'} />
      </section>

      {/* 3. Risked / P&L / return + drawdown / CLV */}
      <section className="metric-grid metric-grid--three" aria-label="Risk and return">
        <MetricCard label="Amount risked" value={formatCurrency(totalRisked)} detail={`${data.positions_count} virtual positions`} />
        <MetricCard label="Today's P/L" value={todayRow ? formatSignedCurrency(todayRow.profit_loss) : '—'} detail={todayRow ? todayRow.date : 'No settled activity today'} trend={!todayRow ? 'neutral' : todayRow.profit_loss > 0 ? 'positive' : todayRow.profit_loss < 0 ? 'negative' : 'neutral'} />
        <MetricCard label="Total simulated return" value={formatSignedCurrency(data.total_return)} detail={hasSettled ? `ROI on stake ${formatSignedPct(data.roi)}` : 'Awaiting settled positions'} trend={!hasSettled ? 'neutral' : data.total_return > 0 ? 'positive' : data.total_return < 0 ? 'negative' : 'neutral'} />
      </section>
      <section className="metric-grid metric-grid--three" aria-label="Drawdown and closing-line coverage">
        <MetricCard label="Current drawdown" value={formatPct(data.current_drawdown ?? 0)} detail="From current high-water mark" trend="negative" />
        <MetricCard label="Max drawdown" value={formatPct(data.max_drawdown)} detail="Peak-to-trough" trend="negative" />
        <MetricCard label="Closing-line coverage" value={closingCoverage?.percentage == null ? 'Unavailable' : formatPct(closingCoverage.percentage)} detail={closingCoverage?.percentage == null ? 'No legitimate closing sample' : `${closingCoverage.positions_with_closing_quote} of ${closingCoverage.settled_positions} settled positions`} />
      </section>

      {/* 4. Current experiment state */}
      {data.experiment_status === 'SCHEDULED' ? (
        <div className="experiment-collecting-state" role="status">
          <CalendarClock size={19} />
          <div><strong>SCHEDULED · PROSPECTIVE START</strong><span>The practical challenger is registered and starts prospectively — no retroactive decision or position will ever be created for an earlier slate.</span></div>
        </div>
      ) : !hasSettled && (
        <div className="experiment-collecting-state" role="status">
          <CalendarClock size={19} />
          <div><strong>ACTIVE · COLLECTING DATA</strong><span>The experiment is live. ROI, yield, and closing-line performance remain unpublished until eligible positions settle.</span></div>
        </div>
      )}
      <div className="experiment-state-strip">
        <strong>{data.experiment_name ?? (demo ? 'Illustrative experiment example' : 'Experiment name unavailable')}</strong>
        {data.experiment_role === 'POLICY_CHALLENGER' && (
          <span className="experiment-identity-badge" title={data.policy_relationship ?? ''}>SAME MODEL · DIFFERENT DECISION POLICY</span>
        )}
        <small>ID {data.experiment_id ?? 'unavailable'} · Started {data.experiment_start ?? 'unavailable'} · Engine {data.controlling_engine ?? 'unavailable'} · Policy {data.policy ?? 'unavailable'}</small>
        <small>
          Model {data.model_name ?? 'unavailable'} {data.model_version ?? ''}{data.model_status ? ` (${data.model_status})` : ''}
          {data.model_identity_source ? ` · identity from ${data.model_identity_source}` : ''}
        </small>
        <small>{settledCount} settled / {openCount} open · {data.no_bet_days ?? 0} NO-BET days · Every position remains in the public ledger.</small>
      </div>

      {/* 4a. Distinct decision metrics — candidate evaluations are NOT positions */}
      {data.decision_metrics && (
        <section className="metric-grid metric-grid--four experiment-decision-metrics" aria-label="Decision metrics">
          <MetricCard label="Candidate evaluations" value={data.decision_metrics.candidate_evaluations} detail={`${data.decision_metrics.games_evaluated} distinct games · one per pregame phase`} />
          <MetricCard label="Qualified evaluations" value={data.decision_metrics.qualified_evaluations} detail={`${data.decision_metrics.qualified_selections} distinct qualified selections`} />
          <MetricCard label="Frozen positions" value={data.decision_metrics.frozen_positions} detail="Immutable virtual positions created" />
          <MetricCard label="Evidence stage" value={data.decision_metrics.evidence_stage.replace(/_/g, ' ')} detail={data.decision_metrics.evidence_note} trend="neutral" />
        </section>
      )}

      {/* 4b. V1 vs V2 comparison (separate ledgers, never pooled) */}
      {!demo && <ExperimentComparisonPanel />}

      {/* 5. Selected-date qualification funnel */}
      {!demo && (
        <QualificationFunnel
          date={funnelDate}
          policy={experimentKey}
          nav={<OperationalDateNav date={funnelDate} onChange={setFunnelDate} ariaLabel="Qualification funnel date" />}
        />
      )}

      {/* 6. Open positions (when nonempty) */}
      {openPositions.length > 0 && (
        <Panel title="Open positions" eyebrow="AWAITING SETTLEMENT" className="section-panel">
          <PositionTable positions={openPositions} ariaLabel="Open virtual positions" />
        </Panel>
      )}

      {/* 7. Latest settled positions (when nonempty) */}
      {settledPositions.length > 0 && (
        <Panel title="Latest settled positions" eyebrow="MOST RECENT SETTLEMENTS" className="section-panel">
          <PositionTable positions={settledPositions.slice(0, 10)} ariaLabel="Latest settled positions" />
        </Panel>
      )}

      {/* 8. Bankroll equity curve */}
      <Panel title="Bankroll equity curve" eyebrow="CUMULATIVE SIMULATED PERFORMANCE" className="equity-panel">
        {data.equity_curve.length > 1
          ? <AnalyticsChart option={equityOption} height={380} ariaLabel="ASP virtual bankroll over time compared with opening bankroll" />
          : <p className="empty-hint">The equity curve appears after the first settled position. The opening bankroll is not a performance result.</p>}
      </Panel>

      {/* 9. Daily decisions */}
      <Panel title="Latest daily decisions" eyebrow="DAILY POINT-IN-TIME EVIDENCE" className="section-panel">
        <TableScroll><table className="data-table"><thead><tr><th>Date</th><th>Positions</th><th>Settled</th><th>Amount risked</th><th>Daily P/L</th><th>Bankroll after</th><th>Decision</th></tr></thead><tbody>
          {daily.length ? daily.map((row) => <tr key={row.date}><td><strong>{row.date}</strong></td><td>{row.positions}</td><td>{row.settled}</td><td>{formatCurrency(row.amount_risked)}</td><td className={row.profit_loss > 0 ? 'positive' : row.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(row.profit_loss)}</td><td>{formatCurrency(row.bankroll_after)}</td><td>{row.no_bet ? <span><strong>NO BET</strong>{row.no_bet_reasons.length ? <small className="daily-decision-reasons">{row.no_bet_reasons.join(' · ')}</small> : null}</span> : 'POSITION DAY'}</td></tr>) : <tr><td colSpan={7} className="empty-row">No daily decision inventory has been published yet. The active experiment is collecting evidence.</td></tr>}
        </tbody></table></TableScroll>
      </Panel>

      {/* 10. Monthly performance */}
      <Panel title="Monthly return" eyebrow="COMPLETE SEASON LEDGER" className="section-panel">
        {data.monthly.length ? <div className="monthly-list">{data.monthly.map((month) => <div key={month.month}><span><strong>{month.month}</strong><small>{month.positions} positions · {month.record}</small></span><b className={month.profit_loss >= 0 ? 'positive' : 'negative'}>{formatSignedCurrency(month.profit_loss)}</b><em>{month.positions ? formatSignedPct(month.roi) : '—'}</em></div>)}</div> : <p className="empty-hint">Monthly performance will appear only after positions settle.</p>}
      </Panel>

      {/* 11. Immutable complete position history */}
      <Panel title="Every official ASP position" eyebrow="IMMUTABLE POSITION HISTORY" className="section-panel" action={<span className="info-label"><BookOpenText size={13} /> No cherry-picking</span>}>
        <TableScroll><table className="data-table positions-table"><thead><tr><th>Date</th><th>Position</th><th>Market</th><th>Price</th><th>Stake</th><th>Result</th><th>P/L</th><th>Closing line value</th></tr></thead><tbody>{data.positions.length ? data.positions.map((position) => <tr key={position.id}><td>{position.date}</td><td><Link to={`${demo ? '/demo' : ''}/games/${position.game_id}`}><strong>{position.selection}</strong></Link></td><td>{position.market}</td><td>{position.price > 0 ? '+' : ''}{position.price}</td><td>{formatCurrency(position.stake)}</td><td><span className={`result-pill result-pill--${position.result.toLowerCase()}`}>{position.result}</span></td><td className={position.profit_loss > 0 ? 'positive' : position.profit_loss < 0 ? 'negative' : ''}>{formatSignedCurrency(position.profit_loss)}</td><td>{position.clv === null ? 'Unavailable' : formatSignedPct(position.clv)}</td></tr>) : <tr><td colSpan={8} className="empty-row">No virtual positions have been frozen. A zero-position day is a valid experiment result.</td></tr>}</tbody></table></TableScroll>
      </Panel>

      {legitimateClv && <p className="experiment-clv-note">Aggregate legitimate CLV: <strong>{formatSignedPct(data.betting!.closing_line_value!)}</strong>. Missing closing quotes are not imputed.</p>}

      {/* 12. Staking policy (collapsible; accessible but never prime placement) */}
      <details className="experiment-collapsible">
        <summary>Staking policy</summary>
        <Panel title="Staking policy" eyebrow="PROVISIONAL · PHASE 1">
          <div className="policy-card"><ShieldAlert size={21} /><div><strong>{data.policy || policy.bankroll.label}</strong><p>{data.policy_detail}</p></div></div>
          <dl className="policy-details"><div><dt>Sizing method</dt><dd>{data.policy_kelly_fraction != null ? `${data.policy_kelly_fraction * 100}% Kelly` : policy.bankroll.kellyFraction != null ? `${policy.bankroll.kellyFraction * 100}% Kelly` : 'Configuration unavailable'}</dd></div><div><dt>Maximum stake</dt><dd>{data.policy_max_stake_percentage != null ? `${data.policy_max_stake_percentage}% of current bankroll` : policy.bankroll.maximumStakePercentage != null ? `${policy.bankroll.maximumStakePercentage}% of current bankroll` : 'Configuration unavailable'}</dd></div><div><dt>Loss recovery</dt><dd>None</dd></div><div><dt>Real-money execution</dt><dd>Not supported</dd></div></dl>
        </Panel>
      </details>

      {/* 13. Experiment identity + audit metadata (collapsible) */}
      <details className="experiment-collapsible">
        <summary>Experiment identity &amp; audit metadata</summary>
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
      </details>

      <div className="responsible-callout"><TrendingDown size={18} /><p><strong>A positive historical simulation does not promise future profit.</strong> This experiment evaluates a forecasting process under explicit assumptions. It does not provide personalized financial or wagering advice, and ASP never encourages chasing losses.</p></div>
    </div>
  )
}

function ExperimentBadge() {
  return <span className="experiment-badge experiment-badge--research"><Beaker size={16} /><span><strong>VIRTUAL RESEARCH EXPERIMENT</strong><small>NO REAL WAGERS</small></span></span>
}
