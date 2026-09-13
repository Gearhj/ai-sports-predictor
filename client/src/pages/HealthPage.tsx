import { useState } from 'react'
import { Activity, Database, Gauge, KeyRound, Play, RefreshCw, ServerCog, TriangleAlert } from 'lucide-react'
import { DataModeBanner, DataStatePanel, MetricCard, PageHeader, Panel, StatusPill, TableScroll } from '../components/ui'
import { LiveShadowStatusCard } from '../components/LiveShadowStatusCard'
import { OddsCollectionPanel } from '../components/OddsCollectionPanel'
import { demoHealth } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME } from '../services/queryCache'
import { useAuth } from '../hooks/useAuth'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { HistoricalWarehouseHealth } from '../types/domain'

const adminJobs = [
  ['refresh-schedule', 'Refresh today’s schedule'],
  ['refresh-mlb-data', 'Refresh MLB data'],
  ['refresh-statcast', 'Refresh Statcast'],
  ['refresh-odds', 'Refresh odds'],
  ['refresh-weather', 'Refresh weather'],
  ['rebuild-features', 'Rebuild features'],
  ['generate-forecasts', 'Generate forecasts'],
  ['run-simulations', 'Run simulations'],
  ['publish-official-positions', 'Publish official virtual positions'],
  ['settle-games', 'Settle completed games'],
  ['recalculate-performance', 'Recalculate performance'],
] as const

export function HealthPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.health, demo
    ? { demoData: demoHealth }
    : { key: 'health', staleTimeMs: STALE_TIME.operational, refreshIntervalMs: STALE_TIME.operational })
  const { user } = useAuth()
  const [runningJob, setRunningJob] = useState<string | null>(null)
  const [jobMessage, setJobMessage] = useState<string | null>(null)

  const apiConnected = !loading && !error && state !== 'unavailable'
  const canRunJobs = !demo && (state === 'live' || state === 'empty') && apiConnected && user?.role === 'ADMIN'

  async function runJob(job: string, label: string) {
    if (!canRunJobs) {
      setJobMessage(!apiConnected ? 'A healthy API connection is required. No job was run.' : 'Administrator access is required to run pipeline jobs.')
      return
    }
    setRunningJob(job)
    setJobMessage(null)
    try {
      const response = await endpoints.runJob(job)
      setJobMessage(`${label} accepted · ${response.job_id || response.id || response.status}`)
      refresh()
    } catch (reason) {
      setJobMessage(reason instanceof Error ? reason.message : 'The job could not be started.')
    } finally {
      setRunningJob(null)
    }
  }

  if (!data || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page health-page">
        <PageHeader eyebrow="OPERATIONS · ADMIN" title="Data Health" description="Database and provider evidence are reported separately." actions={<span className="admin-chip"><ServerCog size={14} /> READ-ONLY</span>} />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        {!demo && <LiveShadowStatusCard />}
        <DataStatePanel state={state} emptyTitle="Database connected; no live sports evidence" emptyDetail="Provider, schedule, forecast, and job evidence will appear only after the live pipeline persists records." />
      </div>
    )
  }

  return (
    <div className="page health-page">
      <PageHeader eyebrow={demo ? 'ILLUSTRATIVE OPERATIONS EXAMPLE' : 'OPERATIONS · ADMIN'} title="Data Health" description={demo ? 'Static source cards demonstrate the operations interface. No controls can run.' : 'Source freshness, pipeline status, and controlled manual operations.'} actions={<span className="admin-chip"><ServerCog size={14} /> {demo ? 'DEMO · CONTROLS DISABLED' : user?.role === 'ADMIN' ? 'ADMIN SESSION' : 'READ-ONLY'}</span>} />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
      {!demo && <LiveShadowStatusCard />}

      <div className="section-heading">
        <div><p className="eyebrow">CURRENT LIVE DATA</p><h2>Current Live Data</h2></div>
      </div>

      <section className="metric-grid metric-grid--three health-provenance" aria-label="System evidence status">
        <MetricCard label="Database" value={(data.database_status || 'unknown').toUpperCase()} detail="Application persistence" />
        <MetricCard label="Sports data" value={(data.sports_data_status || 'unknown').toUpperCase()} detail="Persisted sports records" />
        <MetricCard label="Provider evidence" value={(data.provider_evidence_status || 'none').toUpperCase()} detail="Observed source provenance" />
      </section>

      <section className="health-overview">
        <div className="health-score" style={{ '--score': `${data.overall_score * 3.6}deg` } as React.CSSProperties}><div><strong>{data.overall_score}</strong><span>/ 100</span><small>Overall health</small></div></div>
        <div className="metric-grid metric-grid--four">
          <MetricCard label="Games discovered" value={data.games_discovered} detail={data.missing_games == null ? 'Unmatched-game count unavailable' : `${data.missing_games} unmatched games`} />
          <MetricCard label="Latest forecast" value={data.latest_forecast} detail={data.model_version} />
          <MetricCard label="Odds quota remaining" value={data.odds_quota_remaining ?? '—'} detail={data.odds_polling_enabled ? 'Polling enabled' : 'Polling disabled'} />
          <MetricCard label="Critical sources" value={`${data.sources.filter((source) => source.status === 'ready').length}/${data.sources.length}`} detail="Reporting ready" />
        </div>
      </section>

      <HistoricalWarehousePanel warehouse={data.historical_warehouse} />

      {!demo && <OddsCollectionPanel />}

      <div className="source-grid-cards">
        {data.sources.map((source) => (
          <article className="source-card" key={source.id}>
            <header><span className="source-card__icon">{source.id === 'database' ? <Database size={18} /> : <Activity size={18} />}</span><StatusPill status={source.status} /></header>
            <h2>{source.name}</h2><p>{source.detail}</p>
            <dl><div><dt>Last success</dt><dd>{source.last_success}</dd></div><div><dt>Records</dt><dd>{source.records.toLocaleString()}</dd></div><div><dt>Last failure</dt><dd>{source.last_failure || 'None recorded'}</dd></div></dl>
          </article>
        ))}
      </div>

      <div className="analysis-grid analysis-grid--wide-left">
        <Panel title="Recent pipeline jobs" eyebrow="EXECUTION HISTORY">
          <TableScroll><table className="data-table job-table"><thead><tr><th>Job</th><th>Status</th><th>Started</th><th>Duration</th><th>Records</th><th>ID</th></tr></thead><tbody>{data.recent_jobs.map((job) => <tr key={job.id}><td><strong>{job.job}</strong></td><td><span className={`job-status job-status--${job.status}`}>{job.status}</span></td><td>{job.started_at}</td><td>{job.duration}</td><td>{job.records.toLocaleString()}</td><td className="mono muted">{job.id}</td></tr>)}{data.recent_jobs.length === 0 && <tr><td colSpan={6} className="muted">No pipeline jobs recorded.</td></tr>}</tbody></table></TableScroll>
        </Panel>
        <Panel title="System notes" eyebrow="OPERATIONAL SIGNALS">
          <div className="system-notes"><div><Gauge size={17} /><p><strong>Quota conservation {data.odds_polling_enabled ? 'monitoring' : 'active'}</strong><span>{data.odds_polling_enabled ? 'Odds polling is enabled; monitor provider quota.' : 'Development odds polling is disabled by configuration.'}</span></p></div><div><RefreshCw size={17} /><p><strong>Forecast registry status</strong><span>Latest registered forecast: {data.latest_forecast}.</span></p></div><div className={(data.missing_games ?? 0) > 0 ? 'warning' : ''}><TriangleAlert size={17} /><p><strong>Canonical game matching</strong><span>{data.missing_games == null ? 'Unmatched-game measurement is unavailable.' : data.missing_games > 0 ? `${data.missing_games} game records remain unmatched.` : `All ${data.games_discovered} discovered games are matched.`}</span></p></div></div>
        </Panel>
      </div>

      <Panel title="Manual pipeline controls" eyebrow="ADMINISTRATOR ONLY" className="section-panel" action={<span className="info-label"><KeyRound size={13} /> Audited actions</span>}>
        <div className="admin-controls">
          {adminJobs.map(([job, label]) => <button type="button" key={job} disabled={!canRunJobs || Boolean(runningJob)} onClick={() => void runJob(job, label)} title={!canRunJobs ? 'Requires a connected API and ADMIN session' : label}><Play size={13} fill="currentColor" />{label}{runningJob === job && <RefreshCw size={13} className="spin" />}</button>)}
        </div>
        {!canRunJobs && <p className="admin-lock-note"><KeyRound size={14} /> {demo ? 'Illustrative mode is permanently read-only. No pipeline job can run.' : !apiConnected ? 'Controls require a healthy live API connection.' : 'Controls are visible for operational transparency. Sign in as an administrator to run jobs.'}</p>}
        {jobMessage && <div className="job-message" role="status">{jobMessage}</div>}
      </Panel>
    </div>
  )
}

function HistoricalWarehousePanel({ warehouse }: { warehouse?: HistoricalWarehouseHealth | null }) {
  const status = warehouse?.availability ?? 'UNAVAILABLE'
  return (
    <Panel
      title="Historical Research Warehouse"
      eyebrow="RETROSPECTIVE DATASET HEALTH"
      className="section-panel historical-health-panel"
      action={<span className={`evaluation-status evaluation-status--${status === 'AVAILABLE' ? 'available' : 'not_run'}`}>{status.replaceAll('_', ' ')}</span>}
    >
      <div className="historical-health-boundary"><ShieldBoundaryIcon /><div><strong>Excluded from live health score</strong><span>Historical reconstruction health does not change the current provider freshness score above.</span></div></div>
      {warehouse ? (
        <>
          <div className="metric-grid metric-grid--four historical-health-metrics">
            <MetricCard label="Strict rows" value={warehouse.strict_rows.toLocaleString()} detail="Tier A primary population" />
            <MetricCard label="Enhanced rows" value={warehouse.enhanced_rows.toLocaleString()} detail="Tier B research-only shadow" />
            <MetricCard label="Mean strict coverage" value={warehouse.mean_strict_feature_coverage == null ? 'N/A' : `${(warehouse.mean_strict_feature_coverage * 100).toFixed(2)}%`} detail="Frozen A2 feature denominator" />
            <MetricCard label="Historical integrity" value={warehouse.integrity_status} detail={`Leakage checks: ${warehouse.leakage_status}`} />
            <MetricCard label="Historical Statcast" value={warehouse.statcast_status} detail="Separate from current source freshness" />
            <MetricCard label="Historical market" value={warehouse.historical_market_status} detail="No closing-line evidence imputed" />
            <MetricCard label="Dataset readiness" value={warehouse.readiness_status.replaceAll('_', ' ')} detail="Not model performance" />
            <MetricCard label="Warehouse built" value={warehouse.built_at ? formatTimestamp(warehouse.built_at) : 'Not recorded'} detail={`${warehouse.season} retrospective reconstruction`} />
          </div>
          <p className="historical-health-detail">{warehouse.detail}</p>
          {warehouse.failed_checks.length > 0 && <p className="historical-health-failures"><strong>Open checks:</strong> {warehouse.failed_checks.map((check) => check.replaceAll('_', ' ')).join(', ')}</p>}
        </>
      ) : <p className="muted">No governed historical warehouse summary is available. No readiness claim is inferred.</p>}
    </Panel>
  )
}

function ShieldBoundaryIcon() {
  return <Database size={18} aria-hidden="true" />
}
