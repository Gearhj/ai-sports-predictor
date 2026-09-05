import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Radio, ShieldAlert } from 'lucide-react'
import { DataModeBanner, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { OperationalDateNav } from '../components/OperationalDateNav'
import { MarketMovementView, MarketProbabilityComparison, ProviderCoverageMatrix, buildComparisonRows } from '../components/MarketPulseVisuals'
import { formatDate } from '../services/format'
import { resolveOperationalDate } from '../services/operationalDate'
import { demoMarketPulse } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import type { GameEvidenceState, MarketPulseBoard, MarketPulseRow, ObserverJobStatus } from '../types/domain'

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
  QUOTED_REVIEWED: 'Quoted · reviewed pregame',
  QUOTED_UNREVIEWED: 'Quoted · pregame, unreviewed',
  QUOTED_STRUCTURALLY_EXCLUDED: 'Quoted · excluded (not pregame)',
  MAPPED_NOT_QUOTED: 'Listed · not quoted',
  MAPPING_REVIEW_REQUIRED: 'Mapping review',
  MAPPING_REJECTED: 'Mapping rejected',
  NO_MAPPED_MARKET: 'No market mapped',
  PROVIDER_ERROR: 'Provider error',
  PROVIDER_DISABLED: 'Provider disabled',
  NO_PROVIDER_ENABLED: 'No provider enabled',
}

// Disjoint GAME-level evidence states: every canonical game sits in exactly one.
export const EVIDENCE_STATES: Array<{ key: GameEvidenceState; label: string; detail: string }> = [
  { key: 'REVIEWED_PREGAME', label: 'Reviewed pregame quote', detail: 'Semantics approved · final pregame observation' },
  { key: 'PREGAME_UNREVIEWED', label: 'Pregame quote · awaiting review', detail: 'Valid pregame observation · research-only' },
  { key: 'EXCLUDED_IN_PLAY', label: 'In-play / final · excluded', detail: 'No valid pregame observation captured' },
  { key: 'LISTED_NOT_QUOTED', label: 'Listed · not quoted', detail: 'Mapped, contracts listed, nothing persisted' },
  { key: 'NO_MAPPED_MARKET', label: 'No mapped market', detail: 'Unmapped, review required or rejected' },
  { key: 'PROVIDER_UNAVAILABLE', label: 'Provider unavailable / error', detail: 'Disabled or last discovery failed' },
]

const FALLBACK_EVIDENCE: Record<string, GameEvidenceState> = {
  QUOTED_REVIEWED: 'REVIEWED_PREGAME', QUOTED_UNREVIEWED: 'PREGAME_UNREVIEWED', QUOTED_STRUCTURALLY_EXCLUDED: 'EXCLUDED_IN_PLAY',
  MAPPED_NOT_QUOTED: 'LISTED_NOT_QUOTED', MAPPING_REVIEW_REQUIRED: 'NO_MAPPED_MARKET', MAPPING_REJECTED: 'NO_MAPPED_MARKET',
  NO_MAPPED_MARKET: 'NO_MAPPED_MARKET', PROVIDER_ERROR: 'PROVIDER_UNAVAILABLE', PROVIDER_DISABLED: 'PROVIDER_UNAVAILABLE',
  NO_PROVIDER_ENABLED: 'PROVIDER_UNAVAILABLE',
}

/** Game-level evidence state (backend authority; derived from coverage for older payloads). */
export function evidenceStateOf(row: MarketPulseRow): GameEvidenceState {
  return row.evidence_state ?? FALLBACK_EVIDENCE[row.coverage_state ?? 'NO_MAPPED_MARKET'] ?? 'NO_MAPPED_MARKET'
}

/** Disjoint counts by game-level state; they sum to the canonical slate by construction. */
export function evidenceCounts(rows: MarketPulseRow[]): Record<GameEvidenceState, number> {
  const out = Object.fromEntries(EVIDENCE_STATES.map((s) => [s.key, 0])) as Record<GameEvidenceState, number>
  for (const row of rows) out[evidenceStateOf(row)] += 1
  return out
}

const EXCLUSION_LABEL: Record<string, string> = {
  player_props: 'Player props',
  futures_or_season: 'Futures / season markets',
  partial_game_or_derivative: 'Partial-game / derivative markets',
  combo_or_multivariate: 'Combo / multivariate markets',
  other_league_or_sport: 'Other leagues / sports',
  alternate_lines: 'Alternate lines',
  unsupported_sport: 'Unsupported sports',
  non_governed_market_type: 'Non-governed market types',
  missing_identifiers: 'Missing identifiers',
  missing_outcome_tokens: 'Missing outcome tokens',
  unmatched_outcome_labels: 'Unmatched outcome labels',
  unparseable_event_ticker: 'Unparseable event tickers',
  unregistered_team_alias: 'Unregistered team aliases',
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

function ProviderHealthTable({ data }: { data: MarketPulseBoard }) {
  return (
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
              <td>{p.connection_mode}{p.provider === 'KALSHI' ? ' · public, no key' : p.provider === 'POLYMARKET' ? ' · public, no key' : ''}</td>
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
  )
}

function MarketPulseHealthPanel({ data, onRetry }: { data: MarketPulseBoard; onRetry: () => void }) {
  const cls = data.board_state === 'PROVIDER_DEGRADED' ? 'pulse-health-panel pulse-health-panel--warn' : 'pulse-health-panel'
  return (
    <div className={cls} role="status">
      <h2>{STATE_TITLE[data.board_state] ?? 'Market pulse status'}</h2>
      <p className="pulse-health-meta">Operating date: <strong>{formatDate(data.date)}</strong></p>
      <p className="pulse-health-reason">{data.state_reason}</p>
      <ProviderHealthTable data={data} />
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

/** Overlapping, per-provider audit counts — kept out of the headline on purpose. */
function AuditDetail({ data, rows }: { data: MarketPulseBoard; rows: MarketPulseRow[] }) {
  const coverage = data.coverage ?? {}
  const canonical = data.canonical_games ?? rows.length
  const byProvider = data.coverage_by_provider ?? {}
  const providers = Object.keys(byProvider)
  const states = Object.keys(COVERAGE_LABEL)
  return (
    <details className="pulse-audit-drawer">
      <summary>Audit detail — overlapping provider counts, health and jobs</summary>
      <section className="metric-grid metric-grid--four" aria-label="Overlapping provider measures">
        <MetricCard label="Quoted (any observation)" value={`${coverage.quoted_any ?? 0} / ${canonical}`} detail="Overlapping measure · includes in-play/final" />
        <MetricCard label="Semantics review pending" value={`${coverage.semantics_review_pending ?? data.semantics_review_count ?? 0}`} detail="Overlapping measure · counted per provider" />
        <MetricCard label="Decision-eligible quotes" value={`${coverage.decision_eligible ?? 0} / ${canonical}`} detail="Reviewed AND robust/usable · overlapping measure" />
        <MetricCard label="Cross-provider consensus" value={`${data.summary?.consensus_available ?? 0} / ${canonical}`} detail="≥2 reviewed pregame providers · overlapping measure" />
      </section>
      {providers.length > 0 && (
        <TableScroll ariaLabel="Per-provider coverage counts">
          <table className="data-table pulse-health-table">
            <caption className="visually-hidden">Coverage state counts per provider (each provider column sums to the canonical slate)</caption>
            <thead><tr><th scope="col">Coverage state</th>{providers.map((p) => <th key={p} scope="col">{p}</th>)}</tr></thead>
            <tbody>
              {states.map((s) => (
                <tr key={s}><th scope="row">{COVERAGE_LABEL[s]}</th>{providers.map((p) => <td key={p}>{byProvider[p]?.[s] ?? 0}</td>)}</tr>
              ))}
              <tr><th scope="row">Total (= canonical games)</th>{providers.map((p) => <td key={p}><b>{Object.values(byProvider[p] ?? {}).reduce((a, b) => a + b, 0)}</b></td>)}</tr>
            </tbody>
          </table>
        </TableScroll>
      )}
      <ProviderHealthTable data={data} />
      <ExcludedActivity excluded={data.excluded_summary} />
      <ObserverJobsTable jobs={data.jobs ?? []} />
      <p className="pulse-health-meta">
        Next scheduled discovery: {fmt(data.next_discovery_at)} · Public display: {data.public_display_enabled ? 'enabled' : 'gated (LOCAL_PRIVATE_RESEARCH)'} ·
        Public reads make no provider call.
      </p>
    </details>
  )
}

export function MarketPulsePage({ demo = false }: { demo?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const slateDate = resolveOperationalDate(searchParams.get('date'))
  const sport = searchParams.get('sport')
  const [selectedGame, setSelectedGame] = useState<string | null>(searchParams.get('game'))

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
      description="Point-in-time comparison of AISP forecasts, sportsbook consensus, and prediction-market pregame beliefs."
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
  const canonical = data.canonical_games ?? rows.length
  const counts = evidenceCounts(rows)
  const evidenceSum = EVIDENCE_STATES.reduce((sum, s) => sum + counts[s.key], 0)
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
  const comparison = buildComparisonRows(rows)
  const defaultGame = selectedGame && rows.some((r) => r.game_id === selectedGame)
    ? selectedGame
    : (comparison.find((c) => c.marks.length > 0)?.game_id ?? rows[0].game_id)
  const providerCell = (row: MarketPulseRow, provider: 'POLYMARKET' | 'KALSHI', value: number | null) => {
    const p = row.providers.find((x) => x.provider === provider)
    if (value != null && p) {
      const unreviewed = p.semantics_status !== 'SEMANTICS_MATCHED'
      const kind = p.observation_kind === 'FINAL_PREGAME' ? 'final pregame' : (p.observation_kind ?? '').toLowerCase().replace('_', ' ')
      const title = `${kind}${p.timeline_cut ? ` · ${p.timeline_cut.replace(/_/g, ' ').toLowerCase()}` : ''} · observed ${p.observed_at ?? '—'}${p.latest_in_play ? ' · latest observation in-play (retained separately)' : ''}`
      return unreviewed
        ? <span className="pulse-unreviewed" title={`Unreviewed research observation — not decision eligible · ${title}`}>{pct(value)} <small>unreviewed</small></span>
        : <span title={title}>{pct(value)}</span>
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
          Prices are the final valid pregame observation per provider; in-play and final quotes are retained but never shown as pregame.
          {data.public_display_enabled ? '' : ' Public display is gated (LOCAL_PRIVATE_RESEARCH).'}
        </p>
      </div>

      <div className={`pulse-state-banner pulse-state-banner--${data.board_state.toLowerCase()}`} role="status" aria-label="Observer state">
        <strong>{stateTitle}</strong>
        <span>{data.state_reason}</span>
      </div>

      {/* Disjoint game-level evidence states: each canonical game exactly once. */}
      <section className="pulse-evidence-grid" aria-label="Game evidence states">
        {EVIDENCE_STATES.map((s) => (
          <article key={s.key} className={`pulse-evidence-card pulse-evidence-card--${s.key.toLowerCase()}`} data-state={s.key}>
            <span className="metric-label">{s.label}</span>
            <strong>{counts[s.key]} / {canonical}</strong>
            <small>{s.detail}</small>
          </article>
        ))}
      </section>
      <p className={`pulse-coverage-reconciliation${evidenceSum === canonical ? '' : ' pulse-coverage-reconciliation--mismatch'}`} role="note" aria-label="Coverage reconciliation">
        <strong>Each game counted once:</strong>{' '}
        {EVIDENCE_STATES.map((s) => `${counts[s.key]} ${s.label.toLowerCase()}`).join(' · ')}
        {' '}= <b>{evidenceSum} of {canonical}</b>{evidenceSum === canonical ? ' — reconciles.' : ' — DOES NOT RECONCILE.'}
        {' '}Overlapping provider-level counts (quoted-any, semantics pending, decision-eligible, consensus) live in the audit detail below.
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
        <MetricCard label="Semantic template decisions pending" value={`${data.semantic_template_decisions_pending ?? '—'}`} detail="One decision per provider contract family, never per game · see /prediction-markets/semantic-templates" />
      </section>

      {data.sports_available.length > 0 && (
        <div className="tab-bar" role="tablist" aria-label="Sport filter">
          <button role="tab" className={sport == null ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(null)}>All sports</button>
          {data.sports_available.map((s) => (
            <button key={s} role="tab" className={sport === s ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(s)}>{s}</button>
          ))}
        </div>
      )}

      <MarketProbabilityComparison rows={rows} />
      <ProviderCoverageMatrix rows={rows} />
      {!demo && <MarketMovementView rows={rows} selectedGameId={defaultGame} onSelect={setSelectedGame} />}

      <Panel title="Cross-market probability board" eyebrow="AISP · SPORTSBOOK · POLYMARKET · KALSHI · FINAL PREGAME OBSERVATION PER PROVIDER" action={<span className="info-label"><Radio size={13} /> Home win probability</span>}>
        <TableScroll ariaLabel="Cross-market probability comparison">
          <table className="data-table market-pulse-table">
            <thead>
              <tr>
                <th>Game</th><th>AISP</th><th>Sportsbook</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th>
                <th>AISP–Mkt</th><th>Poly–Kalshi</th><th>5m</th><th>30m</th><th>Quality</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
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
                    {row.providers.some((p) => p.latest_in_play || p.in_play) && <span className="pill pill--inplay">IN PLAY</span>}
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

      <AuditDetail data={data} rows={rows} />

      <p className="page-footnote">
        Prices are official-API observations. A missing value is shown as “—”, “No consensus”, or an honest status — never a
        fabricated zero. Prediction-market data is never used as a plate-appearance or model predictor.
      </p>
    </div>
  )
}

export default MarketPulsePage
