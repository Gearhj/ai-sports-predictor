import { Award, CalendarRange, CircleHelp, Scale } from 'lucide-react'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { coversDiagonal, ReliabilityDiagram, wilsonInterval } from '../charts/ReliabilityDiagram'
import { DataModeBanner, DataStatePanel, formatPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoPerformance } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME } from '../services/queryCache'
import { endpoints } from '../services/api'
import type { HistoricalDatasetPerformanceSummary, OfflineEvaluation, OfflineEvaluationMetrics, OfflineEvaluationWindow } from '../types/domain'

export function PerformancePage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.performance, demo
    ? { demoData: demoPerformance }
    : { key: 'performance', staleTimeMs: STALE_TIME.operational })
  const hasOfflineEvaluation = Boolean(data?.offline_evaluation)
  const hasHistoricalDataset = Boolean(data?.historical_dataset)

  if (!data || state === 'unavailable' || state === 'loading' || (state === 'empty' && !hasOfflineEvaluation && !hasHistoricalDataset)) {
    return (
      <div className="page performance-page">
        <PageHeader eyebrow="PUBLIC MODEL AUDIT" title="Model Performance" description="Metrics appear only after eligible settled live games exist." />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel state={state} emptyTitle="Awaiting an eligible settled sample" emptyDetail="No live evaluation metrics are available yet. ASP will not fill this view with illustrative results." />
      </div>
    )
  }

  const hasEvaluation = data.evaluated_games > 0 && (data.evaluation_available ?? true)
  const hasMarketBenchmark = data.market_benchmark_available ?? (hasEvaluation && data.versus_market_brier != null && data.versus_market_brier !== 0)
  const hasSettledBetting = data.betting?.has_settled === true
  const hasLegitimateClv = hasSettledBetting && data.betting?.closing_line_value != null
  const metric = (value: number | null, digits: number) => hasEvaluation && value != null ? value.toFixed(digits) : 'N/A'

  const rollingOption = {
    color: [chartTheme.teal, chartTheme.orange],
    tooltip: { trigger: 'axis' as const },
    legend: { bottom: 0, data: ['ASP Brier', 'Market Brier'], textStyle: { color: chartTheme.muted } },
    grid: { top: 20, left: 50, right: 18, bottom: 48 },
    xAxis: { type: 'category' as const, data: data.rolling.map((point) => point.date), axisLine: { lineStyle: { color: chartTheme.grid } }, axisLabel: { color: chartTheme.muted } },
    yAxis: { type: 'value' as const, axisLabel: { formatter: (value: number) => value.toFixed(2), color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    series: [
      { name: 'ASP Brier', type: 'line' as const, smooth: true, data: data.rolling.map((point) => point.asp_brier), symbolSize: 7, lineStyle: { width: 3 } },
      { name: 'Market Brier', type: 'line' as const, smooth: true, data: data.rolling.map((point) => point.market_brier), symbolSize: 6, lineStyle: { width: 2, type: 'dashed' as const } },
    ],
  }

  return (
    <div className="page performance-page">
      <PageHeader
        eyebrow={demo ? 'ILLUSTRATIVE PERFORMANCE EXAMPLE' : 'PUBLIC MODEL AUDIT'}
        title="Model Performance"
        description={demo ? 'Example metrics demonstrate the audit interface; they are not live or historical results.' : 'Scoring every published forecast—not just the winners.'}
        actions={<span className="model-chip"><Award size={14} /> {data.model_status || 'DEVELOPMENT'} · {data.model_version}</span>}
      />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <HistoricalDatasetPanel dataset={data.historical_dataset} demo={demo} />

      <OfflineEvaluationPanel evaluation={data.offline_evaluation} demo={demo} />

      <section className="performance-section-heading" aria-labelledby="forward-performance-heading">
        <p className="eyebrow">LIVE FORWARD PERFORMANCE</p>
        <h2 id="forward-performance-heading">Published forecast record</h2>
        <p>This section scores immutable pregame forecasts after eligible games settle. It is operational evidence, separate from the offline chronological evaluation above.</p>
      </section>

      {!hasEvaluation && (
        <div className="performance-collecting-state" role="status">
          <CircleHelp size={19} />
          <div><strong>COLLECTING DATA</strong><span>No eligible live games have settled. Forward log loss, Brier score, calibration, and winner accuracy are intentionally unpublished.</span></div>
        </div>
      )}

      <section className="metric-grid metric-grid--four" aria-label="Forward performance summary">
        <MetricCard label="Evaluated games" value={data.evaluated_games.toLocaleString()} detail="Settled published forecasts" />
        <MetricCard label="Brier score" value={metric(data.brier_score, 3)} detail={hasEvaluation ? 'Lower is better' : 'Awaiting settled sample'} trend={hasEvaluation ? 'positive' : 'neutral'} />
        <MetricCard label="Log loss" value={metric(data.log_loss, 3)} detail={hasEvaluation ? 'Probability penalty' : 'Awaiting settled sample'} />
        <MetricCard label="Winner accuracy" value={hasEvaluation ? formatPct(data.accuracy) : 'N/A'} detail={hasEvaluation ? 'Not the primary metric' : 'Awaiting settled sample'} />
        <MetricCard label="Run differential MAE" value={metric(data.run_mae, 2)} detail={hasEvaluation ? 'Home minus away error' : 'Awaiting settled sample'} />
        <MetricCard label="Total runs MAE" value={metric(data.total_mae, 2)} detail={hasEvaluation ? 'Combined score error' : 'Awaiting settled sample'} />
        <MetricCard label="vs market Brier" value={hasMarketBenchmark && data.versus_market_brier != null ? data.versus_market_brier.toFixed(3) : 'N/A'} detail={hasMarketBenchmark ? 'Negative is better' : 'Historical market sample unavailable'} trend={hasMarketBenchmark && (data.versus_market_brier ?? 0) < 0 ? 'positive' : 'neutral'} />
      </section>

      <div className="principle-strip"><Scale size={18} /><div><strong>Calibration before bravado.</strong><span>A well-calibrated 60% forecast should win about 60% of the time. Accuracy alone cannot tell us whether probabilities are trustworthy.</span></div></div>

      <Panel
        title="Forecast calibration"
        eyebrow="PREDICTED VS OBSERVED"
        className="calibration-panel"
        action={<span className="info-label"><CircleHelp size={13} /> Marker area is sample size</span>}
      >
        {hasEvaluation && data.calibration.length ? (
          <div className="calibration-layout">
            <ReliabilityDiagram buckets={data.calibration} height={430} />
            <div className="calibration-readout">
              <h3>What each bucket rests on</h3>
              <table className="data-table calibration-table">
                <caption className="visually-hidden">
                  Predicted and observed win probability per calibration bucket, with its
                  sample size and 95% Wilson interval
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Predicted</th>
                    <th scope="col">Observed</th>
                    <th scope="col">Games</th>
                    <th scope="col">95% interval</th>
                  </tr>
                </thead>
                <tbody>
                  {data.calibration.map((bucket) => {
                    const [low, high] = wilsonInterval(bucket.observed, bucket.count)
                    return (
                      <tr key={bucket.predicted}>
                        <td>{bucket.predicted.toFixed(1)}%</td>
                        <td>{bucket.observed.toFixed(1)}%</td>
                        <td>{bucket.count}</td>
                        <td className={coversDiagonal(bucket) ? 'calibration-table__ok' : 'calibration-table__off'}>
                          {low.toFixed(0)}–{high.toFixed(0)}%
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="chart-footnote">
                {(() => {
                  const total = data.calibration.reduce((sum, bucket) => sum + bucket.count, 0)
                  const thin = data.calibration.filter((bucket) => bucket.count < 10).length
                  const consistent = data.calibration.filter(coversDiagonal).length
                  return (
                    <>
                      Across {total} evaluated {total === 1 ? 'game' : 'games'}, the observed rate
                      differs measurably from the forecast in{' '}
                      {data.calibration.length - consistent} of {data.calibration.length} buckets
                      at 95%. The remaining {consistent} are estimates this sample cannot separate
                      from their forecast — which is not the same as showing them well calibrated.
                      {thin > 0 && (
                        <> {thin} {thin === 1 ? 'bucket rests' : 'buckets rest'} on fewer than ten
                        games, where the interval is wide enough to cover almost any forecast.</>
                      )}
                    </>
                  )
                })()}
              </p>
            </div>
          </div>
        ) : <EmptyEvaluation />}
      </Panel>

      <div className="analysis-grid analysis-grid--single">
        <Panel title="Rolling probability error" eyebrow="MONTHLY BRIER SCORE" action={<span className="info-label"><CalendarRange size={13} /> Season to date</span>}>
          {hasEvaluation && data.rolling.length ? <AnalyticsChart option={rollingOption} height={330} ariaLabel="ASP and market Brier score by month" /> : <EmptyEvaluation />}
        </Panel>
      </div>

      <div className="analysis-grid analysis-grid--wide-left">
        <Panel title="Model registry scorecard" eyebrow="COMPONENT TRANSPARENCY">
          {hasEvaluation ? <TableScroll><table className="data-table"><thead><tr><th>Model</th><th>Brier</th><th>Log loss</th><th>Run differential MAE</th><th>Status</th></tr></thead><tbody>{data.model_components.map((model) => <tr key={model.model}><td><strong>{model.model}</strong></td><td>{formatEvaluationMetric(model.brier)}</td><td>{formatEvaluationMetric(model.log_loss)}</td><td>{formatEvaluationMetric(model.run_mae, 2)}</td><td><span className={`model-status model-status--${model.status.toLowerCase()}`}>{model.status}</span></td></tr>)}</tbody></table></TableScroll> : <EmptyEvaluation />}
        </Panel>
        <Panel title="Confidence audit" eyebrow="DOES CONFIDENCE HOLD UP?">
          <div className="confidence-audit">
            {hasEvaluation && data.by_confidence.length ? data.by_confidence.map((bucket) => <article key={bucket.bucket}><div><strong>{bucket.bucket}</strong><span>{bucket.forecasts} forecasts</span></div><b>{formatPct(bucket.accuracy)}</b><small>Brier {formatEvaluationMetric(bucket.brier)}</small></article>) : <EmptyEvaluation compact />}
          </div>
        </Panel>
      </div>

      {data.betting && (
        <>
          <section className="performance-section-heading" aria-labelledby="betting-performance-heading">
            <p className="eyebrow">BETTING PERFORMANCE · $1,000 EXPERIMENT</p>
            <h2 id="betting-performance-heading">Virtual bankroll record</h2>
            <p>Virtual research only — no real wagers. Positions freeze from FINAL_PREGAME QUALIFIED_BET decisions and settle from real game finals.</p>
          </section>
          {!hasSettledBetting && (
            <div className="performance-collecting-state" role="status">
              <CircleHelp size={19} />
              <div><strong>COLLECTING SETTLEMENT EVIDENCE</strong><span>ROI, yield, and closing-line value remain unpublished until eligible virtual positions settle with legitimate evidence.</span></div>
            </div>
          )}
          <section className="metric-grid metric-grid--four" aria-label="Betting performance summary">
            <MetricCard label="Current bankroll" value={`$${data.betting.current_bankroll.toFixed(2)}`} detail={`Opened at $${data.betting.starting_bankroll.toFixed(0)}`} />
            <MetricCard label="Net P/L" value={`${data.betting.net_profit_loss >= 0 ? '+' : ''}$${data.betting.net_profit_loss.toFixed(2)}`} detail={`$${data.betting.total_risked.toFixed(2)} risked`} trend={data.betting.net_profit_loss > 0 ? 'positive' : data.betting.net_profit_loss < 0 ? 'negative' : 'neutral'} />
            <MetricCard label="ROI" value={hasSettledBetting && data.betting.roi != null ? `${data.betting.roi > 0 ? '+' : ''}${data.betting.roi.toFixed(2)}%` : '—'} detail={hasSettledBetting ? 'Return on settled stake' : 'Awaiting settled positions'} />
            <MetricCard label="Yield" value={hasSettledBetting && data.betting.yield_pct != null ? `${data.betting.yield_pct > 0 ? '+' : ''}${data.betting.yield_pct.toFixed(2)}%` : '—'} detail={hasSettledBetting ? 'Net return on settled risk' : 'Awaiting settled positions'} />
            <MetricCard label="Positions" value={`${data.betting.positions}`} detail={`${data.betting.settled} settled · ${data.betting.open} open`} />
            <MetricCard label="Record" value={data.betting.record} detail="Win–loss–push" />
            <MetricCard label="Max drawdown" value={`$${data.betting.max_drawdown.toFixed(2)}`} detail={`Longest losing streak ${data.betting.longest_losing_streak}`} trend="negative" />
            <MetricCard label="Closing line value" value={hasLegitimateClv ? `${data.betting.closing_line_value! > 0 ? '+' : ''}${data.betting.closing_line_value!.toFixed(2)}%` : '—'} detail={hasLegitimateClv && data.betting.beat_close_percentage != null ? `Beat close ${data.betting.beat_close_percentage}%` : 'No legitimate closing sample'} />
            <MetricCard label="Void / push" value={`${data.betting.voids} / ${data.betting.pushes}`} detail="Excluded from ROI" />
          </section>
          <Panel title="Betting breakdowns" eyebrow="TIER · MARKET · ENGINE · MONTH">
            {hasSettledBetting ? (
              <TableScroll><table className="data-table"><thead><tr><th>Dimension</th><th>Key</th><th>Positions</th><th>Record</th><th>P/L</th><th>ROI</th></tr></thead><tbody>
                {['tier', 'market', 'engine', 'month'].flatMap((dim) => (data.betting?.breakdowns[dim] ?? []).map((row) => (
                  <tr key={`${dim}-${row.key}`}><td>{dim}</td><td>{row.key}</td><td>{row.positions}</td><td>{row.record}</td><td className={row.profit_loss > 0 ? 'positive' : row.profit_loss < 0 ? 'negative' : ''}>{`${row.profit_loss >= 0 ? '+' : ''}$${row.profit_loss.toFixed(2)}`}</td><td>{row.roi != null ? `${row.roi.toFixed(1)}%` : '—'}</td></tr>
                )))}
              </tbody></table></TableScroll>
            ) : <p className="empty-hint">Breakdowns populate once positions settle.</p>}
          </Panel>
        </>
      )}

      <Panel title="Evaluation protocol" eyebrow="WHAT THESE NUMBERS MEAN" className="section-panel methodology-inline">
        <div><strong>Separate evidence streams</strong><p>Offline holdout results and live forward results are labeled independently. Neither is substituted for the other.</p></div>
        <div><strong>Immutable forecasts</strong><p>Forward metrics use the published forecast snapshot appropriate to the evaluation policy. Older versions are retained.</p></div>
        <div><strong>Matched benchmarks</strong><p>Comparisons use the same eligible games and timestamp-matched inputs. Missing market history is reported as unavailable.</p></div>
      </Panel>
    </div>
  )
}

function HistoricalDatasetPanel({ dataset, demo }: { dataset?: HistoricalDatasetPerformanceSummary | null; demo: boolean }) {
  const available = Boolean(dataset && dataset.availability !== 'UNAVAILABLE')
  return (
    <Panel
      title="Historical dataset readiness"
      eyebrow="DATASET EVIDENCE - NOT MODEL PERFORMANCE"
      className="historical-performance-panel"
      action={<span className={`evaluation-status evaluation-status--${available ? 'insufficient_data' : 'not_run'}`}>{dataset ? formatStatus(dataset.readiness_status) : 'UNAVAILABLE'}</span>}
    >
      {!dataset ? (
        <div className="evaluation-state evaluation-state--not_run" role="status">
          <strong>No governed historical dataset summary</strong>
          <span>This absence is not interpreted as a successful evaluation or zero error.</span>
        </div>
      ) : (
        <div className="historical-performance-body">
          <p>{demo ? 'Illustrative dataset-readiness metadata; it is not historical evidence.' : dataset.status_reason}</p>
          <dl className="evaluation-meta">
            <div><dt>Population</dt><dd>Tier A strict</dd></div>
            <div><dt>Season</dt><dd>{dataset.season}</dd></div>
            <div><dt>Rows</dt><dd>{dataset.row_count.toLocaleString()}</dd></div>
            <div><dt>Event dates</dt><dd>{dataset.distinct_event_dates}</dd></div>
            <div><dt>Held-out rows</dt><dd>{dataset.holdout_rows.toLocaleString()}</dd></div>
            <div><dt>Possible folds</dt><dd>{dataset.rolling_origin_folds}</dd></div>
          </dl>
          <div className="historical-performance-guardrail">
            <strong>The A2 dataset-readiness panel publishes no model metrics.</strong>
            <span>At A2 materialization: training invoked: {formatBooleanFact(dataset.model_training_invoked)}; fitting invoked: {formatBooleanFact(dataset.model_fitting_invoked)}; holdout metrics inspected: {formatBooleanFact(dataset.holdout_metrics_inspected)}.</span>
          </div>
          {dataset.failed_checks.length > 0 && <p className="historical-performance-failures"><strong>Failed checks:</strong> {dataset.failed_checks.map(formatStatus).join(', ')}</p>}
        </div>
      )}
    </Panel>
  )
}

function OfflineEvaluationPanel({ evaluation, demo }: { evaluation?: OfflineEvaluation | null; demo: boolean }) {
  const status = evaluation?.status ?? 'NOT_RUN'
  const available = status === 'AVAILABLE'
  const baselines = evaluation?.baselines ?? []
  const statusReason = evaluation?.status_reason || defaultStatusReason(status)

  return (
    <Panel
      title="Offline chronological evaluation"
      eyebrow="GOVERNED MODEL EVIDENCE"
      className="offline-evaluation-panel"
      action={<span className={`evaluation-status evaluation-status--${status.toLowerCase()}`}>{formatStatus(status)}</span>}
    >
      <div className="offline-evaluation-body">
        <div className={`evaluation-state evaluation-state--${status.toLowerCase()}`} role="status">
          <strong>{available ? (demo ? 'Illustrative evaluation artifact' : 'Evaluation artifact available') : formatStatus(status)}</strong>
          <span>{statusReason}</span>
        </div>

        {evaluation && (
          <dl className="evaluation-meta">
            <div><dt>Model version</dt><dd>{evaluation.model_version || 'N/A'}</dd></div>
            <div><dt>Manifest version</dt><dd>{evaluation.manifest_version || 'N/A'}</dd></div>
            <div><dt>Feature version</dt><dd>{evaluation.feature_version || 'N/A'}</dd></div>
            <div><dt>Dataset checksum</dt><dd>{evaluation.dataset_checksum ? <Checksum value={evaluation.dataset_checksum} label="Dataset checksum" /> : 'N/A'}</dd></div>
            <div><dt>Feature-set checksum</dt><dd>{evaluation.feature_set_checksum ? <Checksum value={evaluation.feature_set_checksum} label="Feature-set checksum" /> : 'N/A'}</dd></div>
            <div><dt>Gen-1 research decision</dt><dd>{evaluation.research_decision ? formatStatus(evaluation.research_decision) : 'N/A'}</dd></div>
            <div><dt>Evaluation policy</dt><dd>{evaluation.evaluation_policy_version || 'N/A'}</dd></div>
          </dl>
        )}

        {evaluation?.research_decision && (
          <section className="evaluation-research-decision" aria-labelledby="research-decision-heading">
            <div><span>GEN-1 RESEARCH STATUS - SEPARATE FROM PRODUCTION PROMOTION</span><h3 id="research-decision-heading">{formatStatus(evaluation.research_decision)}</h3></div>
            <p>This classification governs research comparison only. It does not publish a production champion or authorize live use.</p>
          </section>
        )}

        {available && evaluation && (
          <>
            <section className="evaluation-subsection" aria-labelledby="temporal-windows-heading">
              <div className="evaluation-subsection__heading"><div><span>ROLLING-ORIGIN DESIGN</span><h3 id="temporal-windows-heading">Temporal windows</h3></div><strong>{evaluation.rolling_origin_folds} folds</strong></div>
              <div className="evaluation-window-grid">
                <EvaluationWindowCard label="Training" window={evaluation.train_window} />
                <EvaluationWindowCard label="Validation" window={evaluation.validation_window} />
                <EvaluationWindowCard label="Locked holdout" window={evaluation.holdout_window} />
              </div>
            </section>

            <section className="evaluation-subsection" aria-labelledby="split-results-heading">
              <div className="evaluation-subsection__heading"><div><span>VALIDATION VS HOLDOUT</span><h3 id="split-results-heading">Evaluation results</h3></div></div>
              <TableScroll>
                <table className="data-table evaluation-table">
                  <thead><tr><th>Split</th><th>Games</th><th>Brier</th><th>Log loss</th><th>ECE</th><th>Run MAE</th></tr></thead>
                  <tbody>
                    <EvaluationMetricRow label="Validation" window={evaluation.validation_window} metrics={evaluation.validation_metrics} />
                    <EvaluationMetricRow label="Locked holdout" window={evaluation.holdout_window} metrics={evaluation.holdout_metrics} />
                  </tbody>
                </table>
              </TableScroll>
            </section>

            <section className="evaluation-subsection" aria-labelledby="baseline-results-heading">
              <div className="evaluation-subsection__heading"><div><span>MATCHED HOLDOUT SAMPLE</span><h3 id="baseline-results-heading">Baselines</h3></div></div>
              <TableScroll>
                <table className="data-table evaluation-table">
                  <thead><tr><th>Baseline</th><th>Games</th><th>Brier</th><th>Log loss</th><th>ECE</th><th>Run MAE</th></tr></thead>
                  <tbody>
                    {baselines.length ? baselines.map((baseline) => (
                      <tr key={baseline.name}>
                        <td><strong>{baseline.name}</strong></td>
                        <td>{baseline.sample_size.toLocaleString()}</td>
                        <td>{formatEvaluationMetric(baseline.brier_score)}</td>
                        <td>{formatEvaluationMetric(baseline.log_loss)}</td>
                        <td>{formatEvaluationMetric(baseline.expected_calibration_error)}</td>
                        <td>{formatEvaluationMetric(baseline.run_mae, 2)}</td>
                      </tr>
                    )) : <tr><td colSpan={6} className="empty-row">No matched baseline evidence was published.</td></tr>}
                  </tbody>
                </table>
              </TableScroll>
            </section>
          </>
        )}

        {evaluation && <PromotionDecision evaluation={evaluation} />}
      </div>
    </Panel>
  )
}

function EvaluationWindowCard({ label, window }: { label: string; window: OfflineEvaluationWindow | null }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{window ? `${window.start_date} to ${window.end_date}` : 'N/A'}</strong>
      <small>{window ? `${window.games.toLocaleString()} games` : 'Window not published'}</small>
    </article>
  )
}

function EvaluationMetricRow({ label, window, metrics }: { label: string; window: OfflineEvaluationWindow | null; metrics: OfflineEvaluationMetrics | null }) {
  return (
    <tr>
      <td><strong>{label}</strong></td>
      <td>{window ? window.games.toLocaleString() : 'N/A'}</td>
      <td>{formatEvaluationMetric(metrics?.brier_score)}</td>
      <td>{formatEvaluationMetric(metrics?.log_loss)}</td>
      <td>{formatEvaluationMetric(metrics?.expected_calibration_error)}</td>
      <td>{formatEvaluationMetric(metrics?.run_mae, 2)}</td>
    </tr>
  )
}

function PromotionDecision({ evaluation }: { evaluation: OfflineEvaluation }) {
  const promotion = evaluation.promotion
  const decision = promotion?.decision ?? 'PENDING'
  return (
    <section className={`evaluation-promotion evaluation-promotion--${decision.toLowerCase()}`} aria-labelledby="promotion-decision-heading">
      <div><span>EXPLICIT PROMOTION GATE</span><h3 id="promotion-decision-heading">{formatStatus(decision)}</h3></div>
      <p>{promotion?.reason || 'No promotion decision has been published.'}</p>
      <small>Criteria: {promotion?.criteria_version || 'N/A'}</small>
    </section>
  )
}

function defaultStatusReason(status: OfflineEvaluation['status']) {
  if (status === 'INSUFFICIENT_DATA') return 'The eligible historical sample does not meet the published evaluation policy. Metrics and promotion claims remain blocked.'
  if (status === 'FAILED') return 'The evaluation did not complete. Partial metrics are not published as evidence.'
  if (status === 'AVAILABLE') return 'Validation and locked-holdout results were published from a governed evaluation artifact.'
  return 'No governed offline evaluation artifact has been published. This does not imply zero error or successful validation.'
}

function formatStatus(status: string) {
  return status.replaceAll('_', ' ')
}

function formatEvaluationMetric(value: number | null | undefined, digits = 3) {
  return value == null ? 'N/A' : value.toFixed(digits)
}

function formatBooleanFact(value: boolean | null) {
  return value == null ? 'not persisted' : value ? 'yes' : 'no'
}

function Checksum({ value, label }: { value: string; label: string }) {
  const compact = value.length > 24 ? `${value.slice(0, 12)}...${value.slice(-8)}` : value
  return <code className="evaluation-checksum" title={value} aria-label={`${label}: ${value}`}>{compact}</code>
}

function EmptyEvaluation({ compact = false }: { compact?: boolean }) {
  return <div className={`empty-evaluation ${compact ? 'empty-evaluation--compact' : ''}`}><strong>Awaiting settled sample</strong><span>ASP will publish this metric only after eligible games have final outcomes.</span></div>
}
