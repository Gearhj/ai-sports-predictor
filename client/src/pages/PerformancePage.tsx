import { Award, CalendarRange, CircleHelp, Scale } from 'lucide-react'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { DataModeBanner, DataStatePanel, formatPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoPerformance } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'

export function PerformancePage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.performance, demo ? { demoData: demoPerformance } : {})
  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
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
  const metric = (value: number | null, digits: number) => hasEvaluation && value != null ? value.toFixed(digits) : 'N/A'

  const calibrationOption = {
    color: [chartTheme.teal, chartTheme.muted],
    tooltip: { trigger: 'axis' as const },
    legend: { bottom: 0, data: ['Observed frequency', 'Perfect calibration'], textStyle: { color: chartTheme.muted } },
    grid: { top: 20, left: 48, right: 18, bottom: 52 },
    xAxis: { type: 'category' as const, name: 'Predicted win probability', data: data.calibration.map((point) => `${point.predicted}%`), axisLine: { lineStyle: { color: chartTheme.grid } }, axisLabel: { color: chartTheme.muted } },
    yAxis: { type: 'value' as const, name: 'Observed %', axisLabel: { formatter: '{value}%', color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    series: [
      { name: 'Observed frequency', type: 'line' as const, data: data.calibration.map((point) => point.observed), symbolSize: 9, lineStyle: { width: 3 } },
      { name: 'Perfect calibration', type: 'line' as const, data: data.calibration.map((point) => point.predicted), symbol: 'none', lineStyle: { type: 'dashed' as const, width: 1.5 } },
    ],
  }

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
      <PageHeader eyebrow={demo ? 'ILLUSTRATIVE PERFORMANCE EXAMPLE' : 'PUBLIC MODEL AUDIT'} title="Model Performance" description={demo ? 'Example metrics demonstrate the audit interface; they are not live or historical results.' : 'Scoring every published forecast—not just the winners.'} actions={<span className="model-chip"><Award size={14} /> {data.model_status || 'DEVELOPMENT'} · {data.model_version}</span>} />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="metric-grid metric-grid--four">
        <MetricCard label="Evaluated games" value={data.evaluated_games.toLocaleString()} detail="Chronological holdout" />
        <MetricCard label="Brier score" value={metric(data.brier_score, 3)} detail={hasEvaluation ? 'Lower is better' : 'Awaiting settled sample'} trend={hasEvaluation ? 'positive' : 'neutral'} />
        <MetricCard label="Log loss" value={metric(data.log_loss, 3)} detail={hasEvaluation ? 'Probability penalty' : 'Awaiting settled sample'} />
        <MetricCard label="Winner accuracy" value={hasEvaluation ? formatPct(data.accuracy) : 'N/A'} detail={hasEvaluation ? 'Not the primary metric' : 'Awaiting settled sample'} />
        <MetricCard label="Run differential MAE" value={metric(data.run_mae, 2)} detail={hasEvaluation ? 'Home minus away error' : 'Awaiting settled sample'} />
        <MetricCard label="Total runs MAE" value={metric(data.total_mae, 2)} detail={hasEvaluation ? 'Combined score error' : 'Awaiting settled sample'} />
        <MetricCard label="vs market Brier" value={hasMarketBenchmark && data.versus_market_brier != null ? data.versus_market_brier.toFixed(3) : 'N/A'} detail={hasMarketBenchmark ? 'Negative is better' : 'Historical market sample unavailable'} trend={hasMarketBenchmark && (data.versus_market_brier ?? 0) < 0 ? 'positive' : 'neutral'} />
      </section>

      <div className="principle-strip"><Scale size={18} /><div><strong>Calibration before bravado.</strong><span>A well-calibrated 60% forecast should win about 60% of the time. Accuracy alone cannot tell us whether probabilities are trustworthy.</span></div></div>

      <div className="analysis-grid">
        <Panel title="Forecast calibration" eyebrow="PREDICTED VS OBSERVED" action={<span className="info-label"><CircleHelp size={13} /> Nearer the diagonal is better</span>}>
          {hasEvaluation && data.calibration.length ? <AnalyticsChart option={calibrationOption} height={330} ariaLabel="Calibration curve comparing predicted and observed win probability" /> : <EmptyEvaluation />}
        </Panel>
        <Panel title="Rolling probability error" eyebrow="MONTHLY BRIER SCORE" action={<span className="info-label"><CalendarRange size={13} /> Season to date</span>}>
          {hasEvaluation && data.rolling.length ? <AnalyticsChart option={rollingOption} height={330} ariaLabel="ASP and market Brier score by month" /> : <EmptyEvaluation />}
        </Panel>
      </div>

      <div className="analysis-grid analysis-grid--wide-left">
        <Panel title="Model registry scorecard" eyebrow="COMPONENT TRANSPARENCY">
          {hasEvaluation ? <TableScroll><table className="data-table"><thead><tr><th>Model</th><th>Brier</th><th>Log loss</th><th>Run differential MAE</th><th>Status</th></tr></thead><tbody>{data.model_components.map((model) => <tr key={model.model}><td><strong>{model.model}</strong></td><td>{model.brier == null ? 'N/A' : model.brier.toFixed(3)}</td><td>{model.log_loss == null ? 'N/A' : model.log_loss.toFixed(3)}</td><td>{model.run_mae == null ? 'N/A' : model.run_mae.toFixed(2)}</td><td><span className={`model-status model-status--${model.status.toLowerCase()}`}>{model.status}</span></td></tr>)}</tbody></table></TableScroll> : <EmptyEvaluation />}
        </Panel>
        <Panel title="Confidence audit" eyebrow="DOES CONFIDENCE HOLD UP?">
          <div className="confidence-audit">
            {hasEvaluation && data.by_confidence.length ? data.by_confidence.map((bucket) => <article key={bucket.bucket}><div><strong>{bucket.bucket}</strong><span>{bucket.forecasts} forecasts</span></div><b>{formatPct(bucket.accuracy)}</b><small>Brier {bucket.brier == null ? 'N/A' : bucket.brier.toFixed(3)}</small></article>) : <EmptyEvaluation compact />}
          </div>
        </Panel>
      </div>

      <Panel title="Evaluation protocol" eyebrow="WHAT THESE NUMBERS MEAN" className="section-panel methodology-inline">
        <div><strong>Time-ordered evaluation</strong><p>Training, validation, and test windows move forward chronologically. Future observations are never included in pregame features.</p></div>
        <div><strong>Immutable forecasts</strong><p>Metrics use the published forecast snapshot appropriate to the evaluation policy. Older versions are retained.</p></div>
        <div><strong>Market benchmark</strong><p>Market comparisons use timestamp-matched, no-vig consensus probabilities only when observed data exist.</p></div>
      </Panel>
    </div>
  )
}

function EmptyEvaluation({ compact = false }: { compact?: boolean }) {
  return <div className={`empty-evaluation ${compact ? 'empty-evaluation--compact' : ''}`}><strong>Awaiting settled sample</strong><span>ASP will publish this metric only after eligible games have final outcomes.</span></div>
}
