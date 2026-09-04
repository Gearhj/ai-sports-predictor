import { useEffect, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Radio, ShieldAlert } from 'lucide-react'
import { DataModeBanner, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { OperationalDateNav } from '../components/OperationalDateNav'
import { formatDate } from '../services/format'
import { operationalToday, resolveOperationalDate } from '../services/operationalDate'
import { demoMarketPulse } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import type { MarketPulseBoard, MarketPulseRow, ObserverJobStatus } from '../types/domain'

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function signedPts(value: number | null | undefined): string {
  if (value == null) return '—'
  const p = value * 100
  return `${p > 0 ? '+' : ''}${p.toFixed(1)} pts`
}

function tierClass(tier: string | null): string {
  return `pill pill--${(tier || 'unknown').toLowerCase()}`
}

// Exact observer states. API_UNAVAILABLE is a frontend-only state rendered
// when the AISP backend itself cannot be reached.
const STATE_TITLE: Record<string, string> = {
  OBSERVER_DISABLED: 'Observer disabled',
  PROVIDERS_DISABLED: 'Providers disabled',
  DISCOVERY_PENDING: 'Discovery pending',
  PROVIDERS_HEALTHY_ZERO_ELIGIBLE: 'Providers healthy — zero eligible markets',
  RESEARCH_OBSERVATIONS_UNREVIEWED: 'Research observations — semantics unreviewed',
  ELIGIBLE_MARKETS_AVAILABLE: 'Eligible markets available',
  PROVIDER_DEGRADED: 'Provider degraded',
}

// Per-provider coverage states for one canonical game (why a column is blank).
const COVERAGE_LABEL: Record<string, string> = {
  QUOTED_REVIEWED: 'Quoted · reviewed',
  QUOTED_UNREVIEWED: 'Quoted · unreviewed',
  QUOTED_STRUCTURALLY_EXCLUDED: 'Quoted · excluded (not pregame)',
  MAPPED_NOT_QUOTED: 'Listed · not quoted',
  MAPPING_REVIEW_REQUIRED: 'Mapping review',
  MAPPING_REJECTED: 'Mapping rejected',
  NO_MAPPED_MARKET: 'No market mapped',
  PROVIDER_DISABLED: 'Provider disabled',
  NO_PROVIDER_ENABLED: 'No provider enabled',
}

const EXCLUSION_LABEL: Record<string, string> = {
  player_props: 'Player props',
  futures_or_season: 'Futures / season markets',
  partial_game_or_derivative: 'Partial-game / derivative markets',
  other_league_or_sport: 'Other leagues / sports',
  alternate_lines: 'Alternate lines',
  unsupported_sport: 'Unsupported sports',
  non_governed_market_type: 'Non-governed market types',
  missing_identifiers: 'Missing identifiers',
  missing_outcome_tokens: 'Missing outcome tokens',
  unmatched_outcome_labels: 'Unmatched outcome labels',
  malformed_record: 'Malformed records',
  incomplete_order_books: 'Incomplete order books',
  thin_markets: 'Thin markets',
  unresolved_mappings: 'Unresolved mappings',
  rules_mismatch: 'Rules mismatch',
}

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

function ObserverJobsTable({ jobs }: { jobs: ObserverJobStatus[] }) {
  if (!jobs.length) return null
  return (
    <TableScroll ariaLabel="Observer job registration">
      <table className="data-table pulse-health-table">
        <thead>
          <tr><th>Job</th><th>Enabled</th><th>Cadence</th><th>Last run</th><th>Last success</th><th>Next run</th><th>Inspected</th><th>Persisted</th><th>Last error</th></tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.name}>
              <td><strong>{job.name}</strong></td>
              <td>{job.enabled ? (job.scheduler_running ? 'Yes' : 'Armed (scheduler off)') : 'No'}</td>
              <td>{job.cadence_minutes}m</td>
              <td>{fmt(job.last_run_at)}{job.last_status ? ` · ${job.last_status}` : ''}</td>
              <td>{fmt(job.last_success_at)}</td>
              <td>{fmt(job.next_run_at)}</td>
              <td>{job.records_inspected}</td>
              <td>{job.records_persisted}</td>
              <td>{job.last_error ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

function ExcludedActivity({ excluded }: { excluded: Record<string, number> | undefined }) {
  const entries = Object.entries(excluded ?? {}).filter(([, n]) => n > 0)
  return (
    <div className="excluded-activity">
      <h3>Excluded market activity</h3>
      {entries.length === 0 ? (
        <p className="pulse-health-meta">No inspected-but-excluded markets recorded for the latest discovery cycle.</p>
      ) : (
        <ul>
          {entries.map(([reason, n]) => (
            <li key={reason}><span>{EXCLUSION_LABEL[reason] ?? reason}</span><b>{n}</b></li>
          ))}
        </ul>
      )}
    </div>
  )
}

function MarketPulseHealthPanel({ data, onRetry }: { data: MarketPulseBoard; onRetry: () => void }) {
  const cls = data.board_state === 'PROVIDER_DEGRADED' ? 'pulse-health-panel pulse-health-panel--warn' : 'pulse-health-panel'
  return (
    <div className={cls} role="status">
      <h2>{STATE_TITLE[data.board_state] ?? 'Market pulse status'}</h2>
      <p className="pulse-health-meta">Operating date: <strong>{formatDate(data.date)}</strong></p>
      <p className="pulse-health-reason">{data.state_reason}</p>
      <TableScroll ariaLabel="Provider health">
        <table className="data-table pulse-health-table">
          <thead>
            <tr><th>Provider</th><th>Enabled</th><th>Access</th><th>Last discovery</th><th>Last snapshot</th><th>Raw markets</th><th>Events</th><th>Markets</th><th>Eligible</th><th>Mapped</th><th>Unresolved</th><th>Last job</th></tr>
          </thead>
          <tbody>
            {data.providers.map((p) => (
              <tr key={p.provider}>
                <td><strong>{p.provider}</strong></td>
                <td>{p.enabled ? 'Yes' : 'No'}</td>
                <td>{p.connection_mode}</td>
                <td>{fmt(p.last_discovery_at)}</td>
                <td>{fmt(p.last_snapshot_at)}</td>
                <td>{p.raw_markets_inspected ?? '—'}</td>
                <td>{p.events_inspected}</td>
                <td>{p.markets_inspected}</td>
                <td>{p.eligible_game_markets}</td>
                <td>{p.mapped_markets}</td>
                <td>{p.unresolved_markets}</td>
                <td>{p.last_job_status ?? '—'}{p.last_error ? ` · ${p.last_error}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <ExcludedActivity excluded={data.excluded_summary} />
      <ObserverJobsTable jobs={data.jobs ?? []} />
      <p className="pulse-health-meta">
        Semantics review pending: {data.semantics_review_count} · Next scheduled discovery: {fmt(data.next_discovery_at)} ·
        Public display: {data.public_display_enabled ? 'enabled' : 'gated (LOCAL_PRIVATE_RESEARCH)'}
      </p>
      <p className="pulse-health-meta">No demo data is substituted. Public reads make no provider call.</p>
      <button className="retry-button" onClick={onRetry}>Retry</button>
    </div>
  )
}

export function MarketPulsePage({ demo = false }: { demo?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const slateDate = resolveOperationalDate(searchParams.get('date'))
  const sport = searchParams.get('sport')

  // The URL is the source of truth for the selected operating date.
  useEffect(() => {
    if (searchParams.get('date') !== slateDate) {
      const next = new URLSearchParams(searchParams)
      next.set('date', slateDate)
      setSearchParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slateDate])

  const setDate = (next: string) => {
    const params = new URLSearchParams(searchParams)
    params.set('date', next)
    setSearchParams(params)
  }
  const setSport = (next: string | null) => {
    const params = new URLSearchParams(searchParams)
    if (next) params.set('sport', next)
    else params.delete('sport')
    setSearchParams(params)
  }

  const loader = useMemo(
    () => (signal?: AbortSignal) => endpoints.marketPulse(slateDate, signal, sport),
    [slateDate, sport],
  )
  const { data, state, loading, error, refresh } = useApiResource(loader, demo ? { demoData: demoMarketPulse } : {})

  const header = (
    <PageHeader
      eyebrow="RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE"
      title="ASP Crowd & Market Pulse"
      description="Real-time comparison of AISP forecasts, sportsbook consensus, and prediction-market beliefs."
      actions={<span className="page-actions__date">{formatDate(data?.date ?? slateDate)}</span>}
    />
  )
  const dateNav = <OperationalDateNav date={slateDate} onChange={setDate} ariaLabel="Market pulse operating date" />

  // A. API_UNAVAILABLE — the AISP backend itself cannot be reached (network/5xx/404).
  if (state === 'loading' || state === 'unavailable' || !data) {
    return (
      <div className="page market-pulse-page">
        {header}
        {dateNav}
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        {state === 'unavailable' && (
          <div className="pulse-health-panel pulse-health-panel--error" role="alert">
            <h2>API unavailable</h2>
            <p>The AISP backend could not be reached at the configured API base. This is a connectivity problem, not a
              statement about market availability. Confirm one authoritative backend is running and the frontend points to it,
              then retry.</p>
            <button className="retry-button" onClick={refresh}>Retry</button>
          </div>
        )}
      </div>
    )
  }

  // B–F — backend healthy but no canonical game row to show; render the exact observer state.
  if (data.rows.length === 0) {
    return (
      <div className="page market-pulse-page">
        {header}
        {dateNav}
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <MarketPulseHealthPanel data={data} onRetry={refresh} />
      </div>
    )
  }

  // The board is seeded from the canonical slate: every scheduled game is a
  // row even when no provider has quoted it, and the row says why.
  const rows = data.rows
  const coverage = data.coverage ?? {}
  const canonical = data.canonical_games ?? rows.length
  // Exclusive coverage states: every canonical game sits in exactly one, so
  // they sum to the canonical slate. Counts come from the backend when present
  // and otherwise from the rows themselves (same authority either way).
  const count = (key: string) => coverage[key] ?? rows.filter((r) => r.coverage_state === key).length
  const quotedReviewed = count('QUOTED_REVIEWED')
  const quotedUnreviewed = count('QUOTED_UNREVIEWED')
  const quotedExcluded = count('QUOTED_STRUCTURALLY_EXCLUDED')
  const listedNotQuoted = count('MAPPED_NOT_QUOTED')
  const noMarket = count('NO_MAPPED_MARKET')
  const mappingReview = count('MAPPING_REVIEW_REQUIRED')
  const mappingRejected = count('MAPPING_REJECTED')
  const noProvider = count('PROVIDER_DISABLED') + count('NO_PROVIDER_ENABLED')
  const exclusiveSum = quotedReviewed + quotedUnreviewed + quotedExcluded + listedNotQuoted + noMarket + mappingReview + mappingRejected + noProvider
  const semanticsPending = coverage.semantics_review_pending ?? 0
  const bestDisagreement = rows.reduce<MarketPulseRow | null>((best, r) => {
    if (r.diff_aisp_prediction == null) return best
    if (!best || Math.abs(r.diff_aisp_prediction) > Math.abs(best.diff_aisp_prediction ?? 0)) return r
    return best
  }, null)
  const bestUnreviewed = rows.reduce<MarketPulseRow | null>((best, r) => {
    if (r.diff_aisp_prediction_unreviewed == null) return best
    if (!best || Math.abs(r.diff_aisp_prediction_unreviewed) > Math.abs(best.diff_aisp_prediction_unreviewed ?? 0)) return r
    return best
  }, null)
  const stateTitle = STATE_TITLE[data.board_state] ?? data.board_state
  const providerCell = (row: MarketPulseRow, provider: 'POLYMARKET' | 'KALSHI', value: number | null) => {
    if (value != null) {
      const unreviewed = row.providers.find((p) => p.provider === provider)?.semantics_status !== 'SEMANTICS_MATCHED'
      return unreviewed
        ? <span className="pulse-unreviewed" title="Unreviewed research observation — not decision eligible">{pct(value)} <small>unreviewed</small></span>
        : pct(value)
    }
    const cov = row.provider_coverage?.find((c) => c.provider === provider)
    if (!cov) return '—'
    return <span className={`pill pill--coverage pill--${cov.state.toLowerCase()}`} title={cov.reason}>{COVERAGE_LABEL[cov.state] ?? cov.state}</span>
  }

  return (
    <div className="page market-pulse-page">
      {header}
      {dateNav}
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <div className="research-note" role="note">
        <ShieldAlert size={16} />
        <p>
          This is a read-only market-observation layer. Prediction-market prices are shown for research only — they never
          change a forecast, ranking, recommendation, or the $1,000 experiment, and there is no trading control anywhere.
          {data.public_display_enabled ? '' : ' Public display is gated (LOCAL_PRIVATE_RESEARCH).'}
        </p>
      </div>

      <div className={`pulse-state-banner pulse-state-banner--${data.board_state.toLowerCase()}`} role="status" aria-label="Observer state">
        <strong>{stateTitle}</strong>
        <span>{data.state_reason}</span>
      </div>

      <section className="metric-grid metric-grid--four" aria-label="Canonical slate coverage">
        <MetricCard label="Canonical games" value={`${rows.length} / ${canonical}`} detail="Every scheduled game is a row" />
        <MetricCard
          label="Quoted"
          value={`${quotedReviewed + quotedUnreviewed + quotedExcluded} / ${canonical}`}
          detail={`${quotedReviewed} reviewed · ${quotedUnreviewed} unreviewed · ${quotedExcluded} excluded (not pregame)`}
        />
        <MetricCard
          label="Listed, not quoted"
          value={`${listedNotQuoted} / ${canonical}`}
          detail={`${noMarket} no market mapped · ${mappingReview} mapping review`}
        />
        <MetricCard label="Cross-provider consensus" value={`${data.summary?.consensus_available ?? 0} / ${canonical}`} detail="≥2 reviewed providers · overlapping measure" />
      </section>
      <p className={`pulse-coverage-reconciliation${exclusiveSum === canonical ? '' : ' pulse-coverage-reconciliation--mismatch'}`} role="note" aria-label="Coverage reconciliation">
        <strong>Each game counted once:</strong>{' '}
        {quotedReviewed} quoted & reviewed · {quotedUnreviewed} quoted, semantics pending · {quotedExcluded} quoted but structurally excluded (final / in-play / no usable price)
        {' '}· {listedNotQuoted} listed, not quoted · {mappingReview} mapping review · {mappingRejected} mapping rejected · {noMarket} no market mapped · {noProvider} no provider
        {' '}= <b>{exclusiveSum} of {canonical}</b>{exclusiveSum === canonical ? ' — reconciles.' : ' — DOES NOT RECONCILE.'}
        {' '}Decision-eligible, consensus and semantics-review counts are overlapping measures and are labelled as such.
      </p>
      <section className="metric-grid metric-grid--two" aria-label="Gaps and review">
        <MetricCard
          label="Biggest AISP–market gap (reviewed)"
          value={bestDisagreement ? signedPts(bestDisagreement.diff_aisp_prediction) : '—'}
          detail={bestDisagreement
            ? `${bestDisagreement.away_abbrev} @ ${bestDisagreement.home_abbrev}`
            : bestUnreviewed
              ? `No reviewed quote. Unreviewed observation: ${signedPts(bestUnreviewed.diff_aisp_prediction_unreviewed)} (${bestUnreviewed.away_abbrev} @ ${bestUnreviewed.home_abbrev}) — not decision eligible`
              : 'No reviewed quote'}
          trend="neutral"
        />
        <MetricCard label="Semantics review pending" value={`${semanticsPending}`} detail={`${data.summary?.review_required ?? 0} mapping review · human approval required · overlapping measure (per provider)`} />
      </section>

      {data.sports_available.length > 0 && (
        <div className="tab-bar" role="tablist" aria-label="Sport filter">
          <button role="tab" className={sport == null ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(null)}>All sports</button>
          {data.sports_available.map((s) => (
            <button key={s} role="tab" className={sport === s ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(s)}>{s}</button>
          ))}
        </div>
      )}

      <Panel title="Cross-market probability board" eyebrow="AISP · SPORTSBOOK · POLYMARKET · KALSHI" action={<span className="info-label"><Radio size={13} /> Home win probability</span>}>
        <TableScroll ariaLabel="Cross-market probability comparison">
          <table className="data-table market-pulse-table">
            <thead>
              <tr>
                <th>Game</th><th>AISP</th><th>Sportsbook</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th>
                <th>AISP–Mkt</th><th>Poly–Kalshi</th><th>5m</th><th>30m</th><th>Quality</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={12} className="empty-row">No mapped prediction-market games for this date.</td></tr>}
              {rows.map((row) => (
                <tr key={row.game_id}>
                  <td>
                    <Link to={`${demo ? '/demo' : ''}/market-pulse/${row.game_id}`}>
                      <strong>{row.away_abbrev ?? '—'} @ {row.home_abbrev ?? '—'}</strong>
                    </Link>
                    <small>{row.sport}</small>
                  </td>
                  <td>{pct(row.aisp_home_probability)}</td>
                  <td>{pct(row.sportsbook_home_probability)}</td>
                  <td>{providerCell(row, 'POLYMARKET', row.polymarket_home_probability)}</td>
                  <td>{providerCell(row, 'KALSHI', row.kalshi_home_probability)}</td>
                  <td>{row.consensus_eligible ? pct(row.research_consensus_home_probability) : <span title={row.consensus_note ?? ''}>No consensus</span>}</td>
                  <td>
                    {row.diff_aisp_prediction != null
                      ? signedPts(row.diff_aisp_prediction)
                      : row.diff_aisp_prediction_unreviewed != null
                        ? <span className="pulse-unreviewed" title="Unreviewed research observation — not decision eligible">{signedPts(row.diff_aisp_prediction_unreviewed)} <small>unreviewed</small></span>
                        : '—'}
                  </td>
                  <td>{signedPts(row.diff_polymarket_kalshi)}</td>
                  <td>{signedPts(row.movement_5m)}</td>
                  <td>{signedPts(row.movement_30m)}</td>
                  <td><span className={tierClass(row.best_quality_tier)}>{row.best_quality_tier ?? (row.coverage_state === 'NO_MAPPED_MARKET' || row.coverage_state == null ? 'UNMAPPED' : 'NO QUOTE')}</span></td>
                  <td>
                    <span className={`pill pill--${row.mapping_status.toLowerCase()}`}>{row.mapping_status}</span>
                    {row.providers.some((p) => p.in_play) && <span className="pill pill--inplay">IN PLAY</span>}
                    {row.display_note && <small className="pulse-row-note">{row.display_note}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </Panel>

      <Panel title="Research market consensus" eyebrow="NOT A MODEL INPUT · NOT A WAGER RECOMMENDATION">
        <ul className="pulse-consensus-list">
          {rows.map((row) => (
            <li key={row.game_id}>
              <strong>{row.away_abbrev} @ {row.home_abbrev}</strong>
              <span>
                {row.consensus_eligible
                  ? `Median ${pct(row.research_consensus_home_probability)} · dispersion ${signedPts(row.cross_market_dispersion)}`
                  : (row.consensus_note ?? 'No consensus')}
                {row.provider_coverage?.length
                  ? ` · ${row.provider_coverage.map((c) => `${c.provider}: ${COVERAGE_LABEL[c.state] ?? c.state}`).join(' · ')}`
                  : ''}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <p className="page-footnote">
        Prices are official-API observations. A missing value is shown as “—”, “No consensus”, or an honest status — never a
        fabricated zero. Prediction-market data is never used as a plate-appearance or model predictor.
      </p>
    </div>
  )
}

export default MarketPulsePage
