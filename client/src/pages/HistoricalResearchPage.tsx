import { useCallback, useState, type FormEvent } from 'react'
import { AlertTriangle, Archive, CheckCircle2, ChevronLeft, ChevronRight, CircleSlash2, Database, Filter, ShieldCheck } from 'lucide-react'
import { DataStatePanel, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { HistoricalGameExplorerRow, HistoricalPopulationSummary, HistoricalResearchData, ResourceState } from '../types/domain'

export function HistoricalResearchPage() {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.history)

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page historical-research-page">
        <HistoryHeader />
        <HistoricalEvidenceBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel
          state={state}
          emptyTitle="No governed historical warehouse is published"
          emptyDetail="The API is connected, but no strict historical dataset manifest is available. No historical results have been inferred."
          unavailableTitle="Historical research is unavailable"
          unavailableDetail="The historical summary API could not be reached. Today's live desk remains a separate evidence stream."
        />
      </div>
    )
  }

  const strict = data.strict_population
  const enhanced = data.enhanced_population
  const canonical = data.canonical_summary
  const coverage = strict?.required_family_coverage ?? []

  return (
    <div className="page historical-research-page">
      <HistoryHeader />
      <HistoricalEvidenceBanner state={state} loading={loading} error={error} onRetry={refresh} data={data} />

      <section className="metric-grid metric-grid--six" aria-label="Historical warehouse summary">
        <MetricCard label="Official games" value={formatCount(canonical.official_games)} detail="Canonical 2025 LIVE finals" />
        <MetricCard label="Strict rows" value={strict?.row_count.toLocaleString() ?? 'N/A'} detail="Primary Tier A population" />
        <MetricCard label="Team-game rows" value={formatCount(canonical.team_game_rows)} detail="Canonical two-sided facts" />
        <MetricCard label="Player-game rows" value={formatCount(canonical.player_game_rows)} detail="Canonical participation facts" />
        <MetricCard label="Mean strict coverage" value={canonical.strict_feature_coverage == null ? 'N/A' : formatCoverage(canonical.strict_feature_coverage)} detail="Frozen A2 feature denominator" />
        <MetricCard label="Leakage checks" value={canonical.leakage_status} detail="Target, cutoff, market and lineage gates" />
      </section>

      {canonical.availability !== 'AVAILABLE' && (
        <div className="history-integrity-warning" role="alert"><AlertTriangle size={16} /><span>Canonical summary facts are withheld because integrity could not be proven. {canonical.integrity_issues.join(' ')}</span></div>
      )}

      {canonical.availability === 'AVAILABLE'
        ? <HistoricalGameExplorer sourceKeys={canonical.source_keys} />
        : <Panel title="Historical Game Explorer" eyebrow="CANONICAL SNAPSHOTS ONLY" className="section-panel"><p className="muted">Game-level summaries are withheld until canonical warehouse integrity passes.</p></Panel>}

      <Panel
        title="Strict and enhanced populations"
        eyebrow="POPULATIONS ARE NEVER POOLED"
        className="section-panel history-populations-panel"
      >
        <div className="history-population-grid">
          <PopulationCard population={strict} fallback="The governed strict population is unavailable." />
          <PopulationCard population={enhanced} fallback="The enhanced research shadow is unavailable." />
        </div>
      </Panel>

      <div className="analysis-grid">
        <Panel title="Point-in-time contract" eyebrow="TEMPORAL AND SCHEMA PROVENANCE">
          <dl className="history-contract-list">
            <div><dt>Temporal scope</dt><dd>Historical retrospective reconstruction</dd></div>
            <div><dt>Cutoff policy</dt><dd>{data.cutoff_policy_version || 'Unavailable'}</dd></div>
            <div><dt>Schema</dt><dd>{strict?.schema_version || 'Unavailable'}</dd></div>
            <div><dt>Feature version</dt><dd>{strict?.feature_version || 'Unavailable'}</dd></div>
            <div><dt>Schema hash</dt><dd className="mono history-checksum">{strict?.schema_hash || 'Unavailable'}</dd></div>
            <div><dt>Manifest version</dt><dd>{strict?.manifest_version || 'Unavailable'}</dd></div>
          </dl>
        </Panel>
        <Panel
          title="Readiness audit"
          eyebrow="AUDIT-ONLY - NO MODEL FIT"
          action={<EvidenceStatus status={strict?.readiness.status || 'UNAVAILABLE'} />}
        >
          {strict ? (
            <div className="history-readiness">
              <div className="history-readiness-grid">
                <span><strong>{strict.readiness.distinct_event_dates}</strong> event dates</span>
                <span><strong>{strict.readiness.development_date_groups}</strong> development dates</span>
                <span><strong>{strict.readiness.holdout_date_groups}</strong> holdout dates</span>
                <span><strong>{strict.readiness.holdout_rows.toLocaleString()}</strong> held-out rows</span>
                <span><strong>{strict.readiness.rolling_origin_folds}</strong> possible folds</span>
              </div>
              <EvidenceInvocationFacts data={data} />
              <p className="history-evaluation-boundary"><strong>This A2 manifest audit publishes no model metrics.</strong> These counts describe materialization-time dataset readiness. Model evaluation is reported separately on Performance when available.</p>
              <FailedChecks checks={strict.readiness.failed_checks} />
            </div>
          ) : <p className="muted">No strict readiness audit is available.</p>}
        </Panel>
      </div>

      <Panel title="Required feature-family coverage" eyebrow="STRICT POPULATION THRESHOLDS" className="section-panel">
        <TableScroll ariaLabel="Required historical feature-family coverage">
          <table className="data-table history-coverage-table">
            <thead><tr><th>Feature family</th><th>Observed coverage</th><th>Required</th><th>Result</th></tr></thead>
            <tbody>
              {coverage.map((item) => (
                <tr key={item.key}>
                  <td><strong>{item.label}</strong></td>
                  <td>{item.coverage == null ? 'Unavailable' : formatCoverage(item.coverage)}</td>
                  <td>{formatCoverage(item.required)}</td>
                  <td><span className={`history-result history-result--${item.passed ? 'pass' : 'fail'}`}>{item.passed ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}{item.passed ? 'Pass' : 'Below threshold'}</span></td>
                </tr>
              ))}
              {!coverage.length && <tr><td colSpan={4} className="empty-row">No feature-family coverage audit is available.</td></tr>}
            </tbody>
          </table>
        </TableScroll>
      </Panel>

      <Panel title="Limits on interpretation" eyebrow="WHAT THIS EVIDENCE DOES NOT CLAIM" className="section-panel">
        <ul className="history-limitations">
          {data.limitations.map((limitation) => <li key={limitation}><CircleSlash2 size={16} /><span>{limitation}</span></li>)}
        </ul>
        <p className="page-footnote">This page summarizes canonical historical feature snapshots and governed A2 manifest facts. It exposes no targets, outcomes, artifact locations, raw provider payloads, live forecasts, or production-promotion claim.</p>
      </Panel>
    </div>
  )
}

function HistoryHeader() {
  return (
    <PageHeader
      eyebrow="MLB - GOVERNED RETROSPECTIVE EVIDENCE"
      title="Historical MLB Research Warehouse"
      description="2025 point-in-time reconstruction and Gen-1 research evidence - never today's live forecasts."
      actions={<span className="model-chip"><Archive size={14} /> GATE 3A - A2</span>}
    />
  )
}

function HistoricalEvidenceBanner({
  state,
  loading,
  error,
  onRetry,
  data,
}: {
  state: ResourceState
  loading: boolean
  error: string | null
  onRetry: () => void
  data?: HistoricalResearchData
}) {
  const integrityWarning = data?.availability === 'INTEGRITY_WARNING' || data?.availability === 'PARTIAL'
  const tone = state === 'unavailable' || integrityWarning ? 'unavailable' : state === 'empty' ? 'empty' : state === 'loading' ? 'checking' : 'historical'
  const label = state === 'unavailable'
    ? 'HISTORICAL EVIDENCE UNAVAILABLE'
    : state === 'empty'
      ? 'NO GOVERNED HISTORICAL MANIFEST'
      : state === 'loading'
        ? 'CHECKING HISTORICAL EVIDENCE'
        : integrityWarning
          ? 'HISTORICAL EVIDENCE INTEGRITY WARNING'
        : 'GOVERNED HISTORICAL EVIDENCE - NOT LIVE FORECASTS'
  const detail = state === 'unavailable'
    ? 'The summary API could not be reached; no evidence was substituted.'
    : state === 'empty'
      ? 'The API is connected, but no eligible historical manifest is published.'
      : state === 'loading'
        ? 'Waiting for a persisted manifest summary.'
        : integrityWarning
          ? 'One or more governed populations or canonical counts failed integrity checks; affected claims are withheld.'
        : `${data?.season ?? 2025} retrospective reconstruction. ${loading ? 'Refreshing the manifest summary.' : 'Current forecasts remain on Today.'}`
  const Icon = state === 'unavailable' || integrityWarning ? AlertTriangle : state === 'loading' ? Database : ShieldCheck
  return (
    <div className={`data-banner data-banner--${tone} history-evidence-banner`} role="status">
      <span><Icon size={14} /> {label}</span>
      <span>{detail}</span>
      {state === 'unavailable' && <button type="button" className="text-button" onClick={onRetry} title={error || 'Retry historical summary'}>Retry</button>}
    </div>
  )
}

function HistoricalGameExplorer({ sourceKeys }: { sourceKeys: string[] }) {
  const defaultSource = sourceKeys[0] ?? ''
  const [teamDraft, setTeamDraft] = useState('')
  const [filters, setFilters] = useState<{
    page: number
    pageSize: number
    team: string
    month?: number
    tier: 'A' | 'B'
    source: string
  }>({ page: 1, pageSize: 25, team: '', tier: 'A', source: defaultSource })
  const loader = useCallback(
    (signal: AbortSignal) => endpoints.historyGames({
      page: filters.page,
      pageSize: filters.pageSize,
      team: filters.team || undefined,
      month: filters.month,
      tier: filters.tier,
      source: filters.source || undefined,
    }, signal),
    [filters],
  )
  const { data, state, loading, error, refresh } = useApiResource(loader)

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFilters((current) => ({ ...current, page: 1, team: teamDraft.trim() }))
  }

  const hasRows = Boolean(data?.rows.length)
  return (
    <Panel
      title="Historical Game Explorer"
      eyebrow="PAGINATED PREGAME SNAPSHOTS - TARGETS EXCLUDED"
      className="section-panel history-explorer-panel"
      action={<span className="info-label">{data?.total_rows.toLocaleString() ?? '0'} matching rows</span>}
    >
      <form className="history-explorer-filters" onSubmit={applyFilters} aria-label="Historical game filters">
        <label><span>Team</span><input value={teamDraft} onChange={(event) => setTeamDraft(event.target.value)} placeholder="Name or abbreviation" /></label>
        <label><span>Month</span><select value={filters.month ?? ''} onChange={(event) => setFilters((current) => ({ ...current, page: 1, month: event.target.value ? Number(event.target.value) : undefined }))}><option value="">All months</option>{monthOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>Evidence tier</span><select value={filters.tier} onChange={(event) => setFilters((current) => ({ ...current, page: 1, tier: event.target.value as 'A' | 'B' }))}><option value="A">Tier A strict</option><option value="B">Tier B enhanced</option></select></label>
        <label><span>Source</span><select value={filters.source} onChange={(event) => setFilters((current) => ({ ...current, page: 1, source: event.target.value }))}><option value="">All governed sources</option>{sourceKeys.map((source) => <option key={source} value={source}>{source}</option>)}</select></label>
        <button type="submit" className="secondary-button"><Filter size={14} /> Apply filters</button>
      </form>

      {state === 'unavailable' && <div className="history-integrity-warning" role="alert"><AlertTriangle size={16} /><span>Historical game summaries could not be loaded. {error || 'No rows were substituted.'}</span><button type="button" className="text-button" onClick={refresh}>Retry</button></div>}
      {data && data.availability !== 'AVAILABLE' && <div className="history-integrity-warning" role="alert"><AlertTriangle size={16} /><span>{data.status_reason}</span></div>}
      {loading && !data && <p className="muted" role="status">Loading governed game summaries...</p>}
      {data && data.availability === 'AVAILABLE' && !hasRows && <p className="muted" role="status">{data.status_reason}</p>}
      {data && data.availability === 'AVAILABLE' && hasRows && (
        <>
          <TableScroll ariaLabel="Historical game explorer results">
            <table className="data-table history-explorer-table">
              <thead><tr><th>Date</th><th>Matchup</th><th>Pregame cutoff</th><th>Tier / source</th><th>Completeness</th><th>Starter / lineup</th><th>Key team priors</th><th>Run-strength snapshot</th><th>Market</th></tr></thead>
              <tbody>{data.rows.map((row) => <HistoricalGameRow row={row} key={`${row.game_id}-${row.quality_tier}`} />)}</tbody>
            </table>
          </TableScroll>
          <nav className="history-explorer-pagination" aria-label="Historical game pages">
            <button type="button" className="secondary-button" disabled={filters.page <= 1 || loading} onClick={() => setFilters((current) => ({ ...current, page: current.page - 1 }))}><ChevronLeft size={14} /> Previous</button>
            <span>Page {data.page} of {Math.max(data.total_pages, 1)}</span>
            <button type="button" className="secondary-button" disabled={filters.page >= data.total_pages || loading} onClick={() => setFilters((current) => ({ ...current, page: current.page + 1 }))}>Next <ChevronRight size={14} /></button>
          </nav>
        </>
      )}
      <p className="page-footnote">Rows come from frozen canonical feature snapshots. Missing market evidence remains visible as unavailable and never suppresses a game.</p>
    </Panel>
  )
}

function HistoricalGameRow({ row }: { row: HistoricalGameExplorerRow }) {
  return (
    <tr>
      <td>{row.event_date}</td>
      <td><strong>{row.matchup}</strong></td>
      <td><span>{formatTimestamp(row.cutoff_at)}</span><small className="history-cell-detail">{row.cutoff_policy_version}</small></td>
      <td><span>Tier {row.quality_tier}</span><small className="history-cell-detail">{row.source_keys.join(', ')}</small></td>
      <td><strong>{formatCoverage(row.feature_completeness)}</strong><small className="history-cell-detail">{row.present_feature_count}/{row.total_feature_count} values - quality {row.admission_quality_score.toFixed(1)}</small></td>
      <td><span>Starter: {compactStatus(row.starter_state)}</span><small className="history-cell-detail">Lineup: {compactStatus(row.lineup_state)}</small></td>
      <td><span>{formatTeamPrior(row.away_team_prior.abbreviation, row.away_team_prior.elo_rating, row.away_team_prior.runs_per_game)}</span><small className="history-cell-detail">{formatTeamPrior(row.home_team_prior.abbreviation, row.home_team_prior.elo_rating, row.home_team_prior.runs_per_game)}</small></td>
      <td><span>Away O/P: {formatNumber(row.run_strength.away_offense)} / {formatNumber(row.run_strength.away_run_prevention)}</span><small className="history-cell-detail">Home O/P: {formatNumber(row.run_strength.home_offense)} / {formatNumber(row.run_strength.home_run_prevention)}</small></td>
      <td>{row.market_available === true ? 'Available' : 'Unavailable'}</td>
    </tr>
  )
}

const monthOptions = [
  [3, 'March'], [4, 'April'], [5, 'May'], [6, 'June'], [7, 'July'], [8, 'August'], [9, 'September'], [10, 'October'],
] as const

function PopulationCard({ population, fallback }: { population: HistoricalPopulationSummary | null; fallback: string }) {
  if (!population) return <article className="history-population-card history-population-card--missing"><Database size={20} /><p>{fallback}</p></article>
  const strict = population.role === 'PRIMARY_STRICT'
  return (
    <article className={`history-population-card history-population-card--${strict ? 'strict' : 'enhanced'}`}>
      <header>
        <div><span>{strict ? 'PRIMARY STRICT' : 'RESEARCH-ONLY SHADOW'}</span><h3>{strict ? 'Tier A strict' : 'Tier B enhanced'}</h3></div>
        <span className={`history-integrity history-integrity--${population.integrity_status.toLowerCase()}`}>{population.integrity_status}</span>
      </header>
      <p>{strict ? 'This observed-as-of Tier A population is the only historical source admitted to Gen-1 research.' : 'Broader reconstructed Tier B evidence stays isolated and is excluded from Gen-1 evaluation.'}</p>
      <dl>
        <div><dt>Rows / games</dt><dd>{population.row_count.toLocaleString()} / {population.game_count.toLocaleString()}</dd></div>
        <div><dt>Quality tier</dt><dd>{population.quality_tiers.join(', ')}</dd></div>
        <div><dt>Window</dt><dd>{population.event_start_date} to {population.event_end_date}</dd></div>
        <div><dt>Built</dt><dd>{formatTimestamp(population.built_at)}</dd></div>
      </dl>
      {population.integrity_issues.length > 0 && <FailedChecks checks={population.integrity_issues} label="Integrity findings" />}
    </article>
  )
}

function EvidenceInvocationFacts({ data }: { data: HistoricalResearchData }) {
  const facts = [
    ['Model training invoked', data.model_training_invoked],
    ['Model fitting invoked', data.model_fitting_invoked],
    ['Holdout metrics inspected', data.holdout_metrics_inspected],
    ['Promotion invoked', data.promotion_invoked],
  ] as const
  return <dl className="history-invocation-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value == null ? 'Not persisted' : value ? 'Yes' : 'No'}</dd></div>)}</dl>
}

function FailedChecks({ checks, label = 'Failed checks' }: { checks: string[]; label?: string }) {
  return (
    <div className="history-failed-checks">
      <strong>{label}</strong>
      {checks.length ? <ul>{checks.map((check) => <li key={check}>{compactStatus(check)}</li>)}</ul> : <span>None reported</span>}
    </div>
  )
}

function EvidenceStatus({ status }: { status: string }) {
  const ready = status === 'READY_FOR_GATE_3B_MODEL_DEVELOPMENT'
  return <span className={`evaluation-status evaluation-status--${ready ? 'available' : 'insufficient_data'}`}>{compactStatus(status)}</span>
}

function compactStatus(value: string) {
  return value.replaceAll('_', ' ')
}

function formatCoverage(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function formatCount(value: number | null) {
  return value == null ? 'N/A' : value.toLocaleString()
}

function formatNumber(value: number | null) {
  return value == null ? 'N/A' : value.toFixed(2)
}

function formatTeamPrior(team: string, elo: number | null, runs: number | null) {
  return `${team} - Elo ${elo == null ? 'N/A' : elo.toFixed(0)}, R/G ${formatNumber(runs)}`
}
