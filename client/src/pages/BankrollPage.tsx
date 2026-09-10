import { useEffect, useMemo, useState } from 'react'
import { Beaker, BookOpenText, CalendarClock, ChevronDown, ChevronRight, ShieldAlert, TrendingDown } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { EquityCurveChart, runningHighWater } from '../charts/EquityCurveChart'
import { DataModeBanner, DataStatePanel, formatCurrency, formatPct, formatSignedCurrency, formatSignedPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoBankroll } from '../data/demo'
import { ExperimentFunnel } from '../components/ExperimentFunnel'
import { QualificationFunnel } from '../components/QualificationFunnel'
import { OperationalDateNav } from '../components/OperationalDateNav'
import { operationalToday, resolveOperationalDate } from '../services/operationalDate'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { usePublicPolicy } from '../hooks/usePublicPolicy'
import type { BankrollData, EquityCurvePoint, ExperimentComparison, QualificationFunnel as FunnelData } from '../types/domain'

type PositionRow = BankrollData['positions'][number]
type ExperimentKey = 'v1' | 'v2'

/** "AISP_PRACTICAL_PREGAME_POLICY_V2_1" → "V2.1"; null when no version suffix. */
export function policyVersionLabel(policy: string | null | undefined): string | null {
  const m = policy?.match(/_V(\d+(?:_\d+)*)$/)
  return m ? `V${m[1].replace(/_/g, '.')}` : null
}

/**
 * The bare /experiment route resolves to the ACTIVE practical experiment
 * (V2.1). `?experiment=v1` selects the strict control. `?experiment=v2` is
 * accepted as an explicit alias for the challenger so older links keep working.
 */
export function resolveExperimentKey(param: string | null): ExperimentKey {
  return param === 'v1' ? 'v1' : 'v2'
}

/** CLV with up to three decimals and no trailing zeros; null is never rendered as zero. */
export function formatClv(value: number | null | undefined): string {
  if (value == null) return 'Unavailable'
  const text = Number(value.toFixed(3)).toString()
  return `${value > 0 ? '+' : ''}${text}%`
}

const formatProbability = (value: number | null | undefined) => (value == null ? '—' : `${(value * 100).toFixed(1)}%`)
const formatEdgePts = (value: number | null | undefined) => (value == null ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)} pts`)
const formatPrice = (value: number | null | undefined) => (value == null ? 'Unavailable' : `${value > 0 ? '+' : ''}${value}`)
const plClass = (value: number) => (value > 0 ? 'positive' : value < 0 ? 'negative' : '')

export type TodayResultState = 'POSITIONS_FROZEN' | 'PROVISIONAL_CANDIDATES' | 'AWAITING_FINAL_EVIDENCE' | 'NO_BET' | 'NO_GAMES' | 'LOADING' | 'UNAVAILABLE'

/**
 * Today's experiment result, derived only from the point-in-time funnel the
 * page already fetched and the ledger. Every state is a valid experiment
 * result; a zero-position day is never an error.
 */
export function deriveTodayResult(funnel: FunnelData | null | undefined, positionsOnDate: number): { state: TodayResultState; title: string; detail: string } {
  if (funnel === undefined) {
    return { state: 'LOADING', title: 'SCREENING EVIDENCE LOADING', detail: 'The point-in-time screening evidence for this date is still loading. No position is inferred until it arrives.' }
  }
  if (!funnel || !Array.isArray(funnel.steps)) {
    return { state: 'UNAVAILABLE', title: 'EXPERIMENT RESULT UNAVAILABLE', detail: 'The screening evidence for this date could not be loaded. No position is inferred.' }
  }
  const scheduled = funnel.steps.find((s) => s.key === 'scheduled_games')?.count ?? 0
  const frozen = Math.max(funnel.positions_created ?? 0, positionsOnDate)
  if (scheduled === 0) return { state: 'NO_GAMES', title: 'NO GAMES', detail: 'No games are scheduled for this operating date, so no decision could be made.' }
  if (frozen > 0) {
    return { state: 'POSITIONS_FROZEN', title: 'POSITIONS FROZEN', detail: `${frozen} immutable virtual position(s) were frozen at the final pregame evaluation. They appear in the ledger below and settle exactly once.` }
  }
  if ((funnel.qualified_selections ?? 0) > 0) {
    return { state: 'PROVISIONAL_CANDIDATES', title: 'PROVISIONAL CANDIDATES', detail: `${funnel.qualified_selections} selection(s) qualified at an earlier pregame phase. Nothing is a position until the final pregame evaluation confirms it; candidates can still be rejected.` }
  }
  const awaiting = funnel.diagnostics?.awaiting_final_pregame_evaluation ?? 0
  if (awaiting > 0) {
    return { state: 'AWAITING_FINAL_EVIDENCE', title: 'AWAITING FINAL EVIDENCE', detail: `${awaiting} game(s) have not reached their final pregame evaluation. No position exists yet and none is promised.` }
  }
  return { state: 'NO_BET', title: 'NO BET — POLICY CONDITIONS NOT MET', detail: 'No position today. A zero-position day is a valid experiment result.' }
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
  const v2Label = policyVersionLabel(v2.policy) ?? 'V2'
  const model = (row: typeof v1) => (row.model_name ? `${row.model_name} ${row.model_version ?? ''}`.trim() : 'unavailable')
  const m1 = v1.decision_metrics
  const m2 = v2.decision_metrics
  const clvSample = (row: typeof v1) => {
    if (!row.settled || row.average_clv == null) return '—'
    const n = row.clv_sample_size
    return n == null ? formatClv(row.average_clv) : `${formatClv(row.average_clv)} · ${n} of ${row.settled} settled`
  }
  const rows: Array<[string, string, string]> = [
    ['Role', v1.policy_label ?? 'Strict control', v2.policy_label ?? `Policy challenger — ${v2Label}`],
    ['Status', v1.status, `${v2.status}${v2.status === 'SCHEDULED' ? ` · starts ${v2.effective_date}` : ''}`],
    ['Forecasting model', model(v1), model(v2)],
    ['Decision policy', v1.policy, v2.policy],
    ['Staking policy', v1.staking_policy, v2.staking_policy],
    ['Start date', v1.effective_date, v2.effective_date],
    ['Games evaluated', String(m1?.games_evaluated ?? v1.games_evaluated), String(m2?.games_evaluated ?? v2.games_evaluated)],
    ['Candidate evaluations (per phase)', String(m1?.candidate_evaluations ?? v1.decisions), String(m2?.candidate_evaluations ?? v2.decisions)],
    ['Qualified evaluations', String(m1?.qualified_evaluations ?? v1.qualified_decisions), String(m2?.qualified_evaluations ?? v2.qualified_decisions)],
    ['Qualified selections (distinct)', m1 ? String(m1.qualified_selections) : '—', m2 ? String(m2.qualified_selections) : '—'],
    ['Frozen positions', String(m1?.frozen_positions ?? v1.positions), String(m2?.frozen_positions ?? v2.positions)],
    ['Settled positions', String(v1.settled), String(v2.settled)],
    ['Record', v1.settled ? (v1.record ?? '—') : 'No settled positions', v2.settled ? (v2.record ?? '—') : 'No settled positions'],
    ['Total stake (settled + open)', v1.total_risked == null ? '—' : formatCurrency(v1.total_risked), v2.total_risked == null ? '—' : formatCurrency(v2.total_risked)],
    ['Average stake', v1.average_stake == null ? '—' : formatCurrency(v1.average_stake), v2.average_stake == null ? '—' : formatCurrency(v2.average_stake)],
    ['Net P/L (settled only)', v1.settled ? formatSignedCurrency(v1.profit_loss ?? 0) : '—', v2.settled ? formatSignedCurrency(v2.profit_loss ?? 0) : '—'],
    ['Current bankroll', formatCurrency(v1.current_bankroll), formatCurrency(v2.current_bankroll)],
    ['CLV sample (settled only)', clvSample(v1), clvSample(v2)],
  ]
  return (
    <Panel title={`V1 vs ${v2Label}`} eyebrow="STRICT CONTROL VS PRACTICAL POLICY CHALLENGER · SAME MODEL · SEPARATE LEDGERS — NEVER POOLED" className="section-panel">
      <TableScroll ariaLabel="Experiment comparison">
        <table className="data-table">
          <thead><tr><th scope="col">Metric</th><th scope="col">{v1.name}</th><th scope="col">{v2.name}</th></tr></thead>
          <tbody>
            {rows.map(([label, a, b]) => (
              <tr key={label}><th scope="row"><strong>{label}</strong></th><td>{a}</td><td>{b}</td></tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="pulse-health-meta">
        {v2.policy_relationship ?? 'The challenger changes only the decision policy; the forecasting model is shared.'}
        {' '}Candidate evaluations count every pregame phase snapshot and are not positions; qualified selections are distinct game–selection pairs; frozen positions are the only rows that can ever settle.
        {m2?.evidence_note ? ` ${v2Label}: ${m2.evidence_note}` : ''}
      </p>
      <p className="pulse-health-meta">Result and CLV columns populate only after legitimate settlement. No profitability is claimed for either experiment.</p>
    </Panel>
  )
}

function ResultPill({ result }: { result: PositionRow['result'] }) {
  return <span className={`result-pill result-pill--${result.toLowerCase()}`}>{result}</span>
}

function LedgerRow({ position, demo, expanded, onToggle }: { position: PositionRow; demo: boolean; expanded: boolean; onToggle: () => void }) {
  const detailId = `ledger-detail-${position.id}`
  return (
    <>
      <tr>
        <td className="ledger-table__seq">{position.settlement_sequence != null ? `#${position.settlement_sequence}` : '—'}</td>
        <td>{position.date}</td>
        <td>{position.matchup ?? '—'}</td>
        <td><Link to={`${demo ? '/demo' : ''}/games/${position.game_id}`}><strong>{position.selection}</strong></Link></td>
        <td>{position.market}</td>
        <td>{formatPrice(position.price)}</td>
        <td>{formatCurrency(position.stake)}</td>
        <td><ResultPill result={position.result} /></td>
        <td className={plClass(position.profit_loss)}>{position.result === 'OPEN' ? '—' : formatSignedCurrency(position.profit_loss)}</td>
        <td>{position.bankroll_after == null ? '—' : formatCurrency(position.bankroll_after)}</td>
        <td>{position.result === 'OPEN' ? '—' : formatPrice(position.closing_price)}</td>
        <td>{position.result === 'OPEN' ? '—' : formatClv(position.clv)}</td>
        <td>{formatProbability(position.model_probability)}</td>
        <td>{formatProbability(position.market_probability)}</td>
        <td>{formatEdgePts(position.adjusted_edge)}</td>
        <td>
          <button type="button" className="ledger-expand" aria-expanded={expanded} aria-controls={detailId} onClick={onToggle}>
            {expanded ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
            {expanded ? 'Hide' : 'Details'}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="ledger-detail-row" id={detailId}>
          <td colSpan={16}>
            <dl className="ledger-detail">
              <div><dt>Position ID</dt><dd>{position.id}</dd></div>
              <div><dt>Decision ID</dt><dd>{position.decision_id ?? 'Unavailable'}</dd></div>
              <div><dt>Decision phase</dt><dd>{position.decision_phase ?? 'Unavailable'}</dd></div>
              <div><dt>Recommended at</dt><dd>{position.recommended_at ?? 'Unavailable'}</dd></div>
              <div><dt>Settled at</dt><dd>{position.settled_at ?? (position.result === 'OPEN' ? 'Not settled' : 'Unavailable')}</dd></div>
              <div><dt>Sportsbook at decision</dt><dd>{position.sportsbook ?? 'Unavailable'}</dd></div>
              <div><dt>Raw edge</dt><dd>{formatEdgePts(position.raw_edge)}</dd></div>
              <div><dt>Beat the close</dt><dd>{position.beat_close == null ? 'No closing quote recorded' : position.beat_close ? 'Yes' : 'No'}</dd></div>
              <div><dt>Game</dt><dd><Link to={`${demo ? '/demo' : ''}/games/${position.game_id}`}>{position.game_id}</Link></dd></div>
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function PositionLedger({ positions, demo, ariaLabel = 'Complete position ledger' }: { positions: PositionRow[]; demo: boolean; ariaLabel?: string }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  // Chronological: the ledger reads oldest → newest so the sequence numbers
  // match the equity curve point-for-point.
  const ordered = useMemo(() => [...positions].sort((a, b) => {
    const sa = a.settlement_sequence ?? Number.MAX_SAFE_INTEGER
    const sb = b.settlement_sequence ?? Number.MAX_SAFE_INTEGER
    if (sa !== sb) return sa - sb
    return a.date.localeCompare(b.date)
  }), [positions])
  return (
    <TableScroll ariaLabel={ariaLabel}>
      <table className="data-table positions-table ledger-table">
        <caption className="visually-hidden">Every position of this experiment in settlement order. Losses are never removed.</caption>
        <thead>
          <tr>
            <th scope="col">#</th><th scope="col">Date</th><th scope="col">Matchup</th><th scope="col">Selection</th><th scope="col">Market</th>
            <th scope="col">Decision price</th><th scope="col">Stake</th><th scope="col">Result</th><th scope="col">P/L</th><th scope="col">Bankroll after</th>
            <th scope="col">Closing price</th><th scope="col">CLV</th><th scope="col">Model prob.</th><th scope="col">Market prob.</th><th scope="col">Adjusted edge</th><th scope="col">Decision identity</th>
          </tr>
        </thead>
        <tbody>
          {ordered.length ? ordered.map((position) => (
            <LedgerRow
              key={position.id}
              position={position}
              demo={demo}
              expanded={Boolean(expanded[position.id])}
              onToggle={() => setExpanded((prev) => ({ ...prev, [position.id]: !prev[position.id] }))}
            />
          )) : (
            <tr><td colSpan={16} className="empty-row">No virtual positions have been frozen. A zero-position day is a valid experiment result.</td></tr>
          )}
        </tbody>
      </table>
    </TableScroll>
  )
}

function EquityCurve({ curve, experimentKey, hasSettled }: { curve: EquityCurvePoint[]; experimentKey: ExperimentKey; hasSettled: boolean }) {
  if (curve.length <= 1 || !hasSettled) {
    return (
      <p className="empty-hint">
        {experimentKey === 'v1'
          ? 'No V1 positions have settled. The opening bankroll is not a performance result.'
          : 'No positions have settled. The opening bankroll is not a performance result.'}
      </p>
    )
  }
  // The table below still reports the peak per row; the chart derives its own.
  const highWater = runningHighWater(curve)
  return (
    <>
      <EquityCurveChart
        curve={curve}
        height={400}
        formatCurrency={formatCurrency}
        formatSignedCurrency={formatSignedCurrency}
      />
      <TableScroll ariaLabel="Settlement path">
        <table className="data-table equity-path-table">
          <caption className="visually-hidden">Bankroll after each settlement in chronological settlement order</caption>
          <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">Settlement</th><th scope="col">Result</th><th scope="col">P/L</th><th scope="col">Bankroll after</th><th scope="col">High-water mark</th><th scope="col">Drawdown</th></tr></thead>
          <tbody>
            {curve.map((point, index) => (
              <tr key={`${point.sequence ?? index}-${point.date}`}>
                <td>{point.sequence == null ? index : point.sequence === 0 ? 'Open' : `#${point.sequence}`}</td>
                <td>{point.date}</td>
                <td>{point.sequence === 0 || point.sequence == null ? 'Opening bankroll' : point.selection ?? '—'}</td>
                <td>{point.result ? <ResultPill result={point.result} /> : '—'}</td>
                <td className={plClass(point.profit_loss ?? 0)}>{point.sequence === 0 || point.profit_loss == null ? '—' : formatSignedCurrency(point.profit_loss)}</td>
                <td>{formatCurrency(point.bankroll)}</td>
                <td>{formatCurrency(highWater[index])}</td>
                <td>{point.drawdown == null ? formatCurrency(highWater[index] - point.bankroll) : formatCurrency(point.drawdown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </>
  )
}

function ExperimentBadge() {
  return <span className="experiment-badge experiment-badge--research"><Beaker size={16} /><span><strong>VIRTUAL RESEARCH EXPERIMENT</strong><small>NO REAL WAGERS</small></span></span>
}

function ExperimentSelector({ experimentKey, challengerVersion, onSelect }: { experimentKey: ExperimentKey; challengerVersion: string; onSelect: (next: ExperimentKey) => void }) {
  return (
    <div className="experiment-selector" role="tablist" aria-label="Experiment selector">
      <button
        type="button"
        role="tab"
        aria-selected={experimentKey === 'v2'}
        className="experiment-selector__tab"
        onClick={() => onSelect('v2')}
      >
        <strong>{challengerVersion} — Practical Pregame Policy Challenger</strong>
        <small>Active practical experiment · same forecast model</small>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={experimentKey === 'v1'}
        className="experiment-selector__tab"
        onClick={() => onSelect('v1')}
      >
        <strong>V1 — Strict Control</strong>
        <small>Control · same forecast model</small>
      </button>
    </div>
  )
}

export function BankrollPage({ demo = false }: { demo?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const experimentKey = resolveExperimentKey(searchParams.get('experiment'))
  const loader = useMemo(
    () => (signal?: AbortSignal) => endpoints.bankroll(signal, experimentKey),
    [experimentKey],
  )
  const { data, state, loading, error, refresh } = useApiResource(loader, demo ? { demoData: demoBankroll } : {})
  const { policy } = usePublicPolicy()
  const funnelDate = resolveOperationalDate(searchParams.get('date'))
  const [funnel, setFunnel] = useState<FunnelData | null | undefined>(undefined)
  const setFunnelDate = (next: string) => {
    const params = new URLSearchParams(searchParams)
    params.set('date', next)
    setSearchParams(params)
  }
  // The selection is URL-bound so refresh, back/forward and shared links all
  // resolve to the same experiment. The bare route is the active practical
  // experiment; V1 is explicit.
  const setExperiment = (next: ExperimentKey) => {
    const params = new URLSearchParams(searchParams)
    if (next === 'v1') params.set('experiment', 'v1')
    else params.delete('experiment')
    setSearchParams(params)
  }
  const experimentIsActive = demo || data?.experiment_status === 'ACTIVE' || data?.experiment_status === 'SCHEDULED'
  // While the selection changes, the resource hook still holds the previous
  // experiment's payload. A payload for another experiment is never rendered
  // under the selected tab: it is treated as loading until the right one lands.
  const stale = !demo && Boolean(data?.experiment_key) && data?.experiment_key !== experimentKey
  // The challenger's version label is derived from the policy the backend
  // reports (…_POLICY_V2_1 → "V2.1"), never hard-coded to a stale "V2".
  const challengerVersion = policyVersionLabel(experimentKey === 'v2' && !stale ? data?.policy : null) ?? 'V2.1'

  const selector = !demo && <ExperimentSelector experimentKey={experimentKey} challengerVersion={challengerVersion} onSelect={setExperiment} />
  const headerEyebrow = demo
    ? 'ILLUSTRATIVE EXPERIMENT EXAMPLE'
    : experimentKey === 'v2'
      ? `PRACTICAL PREGAME POLICY CHALLENGER — ${challengerVersion}`
      : 'STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT'
  const contractSentence = demo
    ? 'Example positions demonstrate the ledger; these are not live or historical results.'
    : 'A $1,000 virtual bankroll follows one frozen forecasting model under one frozen decision policy: every position is recorded before first pitch, settled exactly once, and never edited or removed.'

  if (!data || stale || state === 'unavailable' || state === 'loading' || (!experimentIsActive && (state === 'empty' || data.experiment_status === 'NOT_STARTED'))) {
    const shownState = stale ? 'loading' : !demo && data?.experiment_status === 'NOT_STARTED' ? 'empty' : state
    return (
      <div className="page bankroll-page">
        <PageHeader eyebrow={headerEyebrow} title="Can AI Beat the Sportsbooks?" description={contractSentence} actions={<ExperimentBadge />} />
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
  const betting = data.betting
  const totalRisked = betting?.total_risked ?? data.positions.reduce((sum, position) => sum + position.stake, 0)
  const hasSettled = betting?.has_settled ?? (data.settled_positions ?? 0) > 0
  const closingCoverage = data.closing_quote_coverage
  const clvSampleSize = closingCoverage?.positions_with_closing_quote ?? 0
  const legitimateClv = hasSettled && clvSampleSize > 0 && betting?.closing_line_value != null
  const openPositions = data.positions.filter((position) => position.result === 'OPEN')
  const openCount = data.open_positions ?? openPositions.length
  const settledCount = data.settled_positions ?? data.positions.filter((position) => position.result !== 'OPEN').length
  const netPl = betting?.net_profit_loss ?? data.total_return
  // Settled performance and open exposure are never mixed: ROI divides settled
  // P/L by the stake of settled positions only; open stakes are exposure.
  const openExposure = openPositions.reduce((sum, position) => sum + position.stake, 0)
  const settledRisked = Math.round((totalRisked - openExposure) * 100) / 100
  const roi = hasSettled && settledRisked > 0 ? Math.round((netPl / settledRisked) * 1000) / 10 : null
  const record = betting?.record ?? data.record
  const averageStake = data.positions.length ? totalRisked / data.positions.length : null
  const maxDrawdownDollars = betting?.max_drawdown ?? null
  const maxDrawdownPct = Math.abs(data.max_drawdown)
  const positionsOnDate = data.positions.filter((position) => position.date === funnelDate).length
  const todayResult = demo ? null : deriveTodayResult(funnel, positionsOnDate)
  const evidenceStage = data.decision_metrics?.evidence_stage ?? null
  const chainReconciled = betting?.bankroll_chain_reconciled ?? true
  const statusChip = data.experiment_status === 'SCHEDULED'
    ? <span className="experiment-chip experiment-chip--scheduled">Scheduled</span>
    : <span className="experiment-chip experiment-chip--active">Active</span>

  return (
    <div className="page bankroll-page">
      {/* A. Hero: title, role, contract sentence, honest badges */}
      <PageHeader eyebrow={headerEyebrow} title="Can AI Beat the Sportsbooks?" description={contractSentence} actions={<ExperimentBadge />} />
      {selector}
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
      <div className="experiment-badge-row" aria-label="Experiment status badges">
        {statusChip}
        <span className="experiment-chip experiment-chip--research">Virtual research experiment</span>
        {data.experiment_role === 'POLICY_CHALLENGER' && (
          <span className="experiment-chip experiment-chip--model" title={data.policy_relationship ?? ''}>SAME MODEL · DIFFERENT DECISION POLICY</span>
        )}
        {data.experiment_role === 'STRICT_CONTROL' && (
          <span className="experiment-chip experiment-chip--model">Strict control · same forecast model as the challenger</span>
        )}
        {evidenceStage && <span className="experiment-chip experiment-chip--evidence">{evidenceStage.replace(/_/g, ' ')}</span>}
        <span className="experiment-chip">No real wagers</span>
      </div>
      {hasSettled ? (
        <p className="experiment-sample-note" role="note">
          <strong>{settledCount} settled position{settledCount === 1 ? '' : 's'} {settledCount === 1 ? 'is' : 'are'} far too few to distinguish skill from noise.</strong> No profitability claim is made. Every figure below is a bookkeeping fact about this virtual ledger, not evidence of an edge.
        </p>
      ) : data.experiment_status === 'SCHEDULED' ? (
        <div className="experiment-collecting-state" role="status">
          <CalendarClock size={19} />
          <div><strong>SCHEDULED · PROSPECTIVE START</strong><span>The practical challenger is registered and starts prospectively — no retroactive decision or position will ever be created for an earlier slate.</span></div>
        </div>
      ) : (
        <div className="experiment-collecting-state" role="status">
          <CalendarClock size={19} />
          <div><strong>ACTIVE · COLLECTING DATA</strong><span>The experiment is live. ROI, yield, and closing-line performance remain unpublished until eligible positions settle. The opening bankroll is not a performance result.</span></div>
        </div>
      )}

      {/* B. Primary scoreboard — bookkeeping facts only */}
      <section className="metric-grid metric-grid--six experiment-scoreboard experiment-kpi-primary" aria-label="Bankroll and position counts">
        <MetricCard label="Starting bankroll" value={formatCurrency(data.starting_bankroll, 0)} detail={`Opened ${data.experiment_start_date ?? data.experiment_start?.slice(0, 10) ?? 'unavailable'}`} />
        <MetricCard label="Current bankroll" value={formatCurrency(data.current_bankroll)} detail={hasSettled ? 'After every settlement, no deposits or withdrawals' : `Opened at ${formatCurrency(data.starting_bankroll, 0)}`} />
        <MetricCard label="Net P/L" value={hasSettled ? formatSignedCurrency(netPl) : '—'} detail={hasSettled ? 'Settled positions only' : 'Awaiting settled positions'} trend={!hasSettled ? 'neutral' : netPl > 0 ? 'positive' : netPl < 0 ? 'negative' : 'neutral'} />
        <MetricCard
          label="ROI on risked capital"
          value={hasSettled && roi != null ? <span className="metric-help" title="ROI = net P/L ÷ total amount risked on frozen positions. The bankroll is never the denominator, and open positions are excluded.">{formatSignedPct(roi)}</span> : '—'}
          detail={hasSettled ? `Net P/L ÷ ${formatCurrency(settledRisked)} settled stake — open exposure excluded, never ÷ bankroll` : 'Published only after settlement'}
          trend={!hasSettled || roi == null ? 'neutral' : roi > 0 ? 'positive' : roi < 0 ? 'negative' : 'neutral'}
        />
        <MetricCard label="Settled positions" value={settledCount} detail={settledCount ? 'Each settled exactly once' : 'No settled positions yet'} />
        <MetricCard label="Open positions" value={openCount} detail={openCount ? `${formatCurrency(openExposure)} open exposure · not in P/L, record or ROI` : 'No open virtual positions'} />
      </section>
      <section className="metric-grid metric-grid--six experiment-scoreboard" aria-label="Record, risk and closing-line sample">
        <MetricCard label="Record" value={hasSettled ? record : '—'} detail={hasSettled ? 'Wins – losses – pushes' : 'No settled positions'} />
        <MetricCard label="Amount risked" value={formatCurrency(settledRisked)} detail={openCount ? `${settledCount} settled · ${formatCurrency(openExposure)} open exposure shown separately` : `${settledCount} settled position${settledCount === 1 ? '' : 's'}`} />
        <MetricCard label="Average stake" value={averageStake == null ? '—' : formatCurrency(averageStake)} detail={averageStake == null ? 'No positions' : `Total stake ${formatCurrency(totalRisked)} ÷ ${data.positions_count} frozen position${data.positions_count === 1 ? '' : 's'}`} />
        <MetricCard label="Max drawdown" value={hasSettled ? (maxDrawdownDollars != null ? formatCurrency(maxDrawdownDollars) : formatPct(maxDrawdownPct)) : '—'} detail={hasSettled ? `${formatPct(maxDrawdownPct)} of opening bankroll · peak-to-trough in settlement order` : 'Awaiting settled positions'} trend={hasSettled && (maxDrawdownDollars ?? maxDrawdownPct) > 0 ? 'negative' : 'neutral'} />
        <MetricCard label="Current drawdown" value={hasSettled ? formatPct(data.current_drawdown ?? 0) : '—'} detail={hasSettled ? `From high-water mark ${formatCurrency(data.peak_bankroll)}` : 'Awaiting settled positions'} trend={hasSettled && (data.current_drawdown ?? 0) > 0 ? 'negative' : 'neutral'} />
        <MetricCard
          label="CLV sample"
          value={legitimateClv ? formatClv(betting!.closing_line_value) : 'Unavailable'}
          detail={legitimateClv
            ? `${clvSampleSize} of ${closingCoverage?.settled_positions ?? settledCount} settled with a closing quote · missing quotes never count as zero`
            : 'No legitimate closing sample'}
          trend="neutral"
        />
      </section>
      {!chainReconciled && (
        <p className="experiment-sample-note" role="alert"><strong>Ledger reconciliation warning.</strong> The running bankroll does not reproduce every persisted settlement balance. Figures are shown from the persisted chain; investigate before quoting them.</p>
      )}

      {/* C. Frozen experiment contract (identity strip) */}
      <dl className="experiment-contract-strip" aria-label="Frozen experiment contract">
        <div><dt>Experiment</dt><dd>{data.experiment_name ?? (demo ? 'Illustrative experiment example' : 'Unavailable')}<small>{data.experiment_role === 'POLICY_CHALLENGER' ? 'Role · policy challenger' : data.experiment_role === 'STRICT_CONTROL' ? 'Role · strict control' : 'Role · unavailable'}</small></dd></div>
        <div>
          <dt>Forecasting model</dt>
          <dd>
            Model {data.model_name ?? 'unavailable'} {data.model_version ?? ''}{data.model_status ? ` (${data.model_status})` : ''}
            <small>{data.model_identity_source ? `identity from ${data.model_identity_source}` : 'model identity unavailable'}</small>
          </dd>
        </div>
        <div><dt>Decision policy</dt><dd>{data.policy || 'Unavailable'}<small>{data.policy_relationship ?? 'The policy decides; the model forecasts.'}</small></dd></div>
        <div><dt>Staking policy</dt><dd>{data.staking_policy ?? policy.bankroll.label ?? 'Unavailable'}<small>{data.policy_kelly_fraction != null ? `${data.policy_kelly_fraction * 100}% Kelly · max ${data.policy_max_stake_percentage ?? '—'}% per position` : 'Sizing configuration unavailable'}</small></dd></div>
        <div><dt>Start date</dt><dd>{data.experiment_start_date ?? 'Unavailable'}<small>Registered {data.experiment_start ?? 'unavailable'}</small></dd></div>
        <div><dt>Opening bankroll</dt><dd>{formatCurrency(data.starting_bankroll, 0)}<small>{data.no_bet_days ?? 0} NO-BET day{(data.no_bet_days ?? 0) === 1 ? '' : 's'} · every position stays in the public ledger</small></dd></div>
        <details className="experiment-contract-strip__drawer">
          <summary>Checksums &amp; audit identity</summary>
          <dl>
            <div><dt>Experiment ID</dt><dd>{data.experiment_id ?? 'Unavailable'}</dd></div>
            <div><dt>Policy checksum</dt><dd>{data.policy_checksum ?? 'Not recorded'}</dd></div>
            <div><dt>Code identity</dt><dd>{data.code_commit ?? 'Unavailable'}</dd></div>
            <div><dt>Controlling engine</dt><dd>{data.controlling_engine ?? 'Unavailable'}</dd></div>
            <div><dt>Supported markets</dt><dd>{data.supported_markets?.join(', ') || 'Unavailable'}</dd></div>
            <div><dt>Edge threshold</dt><dd>{data.policy_edge_threshold_percentage != null ? `${data.policy_edge_threshold_percentage}%` : 'Unavailable'}</dd></div>
          </dl>
        </details>
      </dl>

      {/* D. Bankroll equity curve — advances only on settlements */}
      <Panel title="Bankroll after each settlement" eyebrow="EQUITY CURVE · STARTS AT THE OPENING BANKROLL · ADVANCES ONLY WHEN A POSITION SETTLES" className="equity-panel">
        <EquityCurve curve={data.equity_curve} experimentKey={experimentKey} hasSettled={hasSettled} />
      </Panel>

      {/* E. Complete position ledger */}
      {openPositions.length > 0 && (
        <Panel title="Open positions" eyebrow="AWAITING SETTLEMENT" className="section-panel">
          <PositionLedger positions={openPositions} demo={demo} ariaLabel="Open positions awaiting settlement" />
        </Panel>
      )}
      <Panel title="Every position" eyebrow="NO CHERRY-PICKING · IMMUTABLE POSITION HISTORY" className="section-panel" action={<span className="info-label"><BookOpenText size={13} /> Losses are never removed</span>}>
        <PositionLedger positions={data.positions} demo={demo} />
        <p className="ledger-legend">
          Decision price, stake, model probability, market (no-vig) probability and adjusted edge are recorded when the position is frozen. Result, P/L, bankroll after, closing price and CLV are recorded once at settlement. “Unavailable” means no closing quote was captured; it is never treated as zero.
        </p>
      </Panel>

      {/* F. Decision metrics + V1 vs V2.1 comparison (separate ledgers, never pooled) */}
      {data.decision_metrics && (
        <section className="metric-grid metric-grid--four experiment-decision-metrics" aria-label="Decision metrics">
          <MetricCard label="Candidate evaluations" value={data.decision_metrics.candidate_evaluations} detail={`${data.decision_metrics.games_evaluated} distinct games · one per pregame phase`} />
          <MetricCard label="Qualified evaluations" value={data.decision_metrics.qualified_evaluations} detail={`${data.decision_metrics.qualified_selections} distinct qualified selections`} />
          <MetricCard label="Frozen positions" value={data.decision_metrics.frozen_positions} detail="Immutable virtual positions created" />
          <MetricCard label="Evidence stage" value={data.decision_metrics.evidence_stage.replace(/_/g, ' ')} detail={data.decision_metrics.evidence_note} trend="neutral" />
        </section>
      )}
      {!demo && <ExperimentComparisonPanel />}

      {/* G. Today's experiment result */}
      {todayResult && (
        <div className={`experiment-result-card experiment-result-card--${todayResult.state.toLowerCase()}`} role="status" aria-label="Experiment result for the selected date">
          <strong>{funnelDate === today ? "TODAY'S EXPERIMENT RESULT" : `EXPERIMENT RESULT · ${funnelDate}`} · {todayResult.title}</strong>
          <p>{todayResult.detail}</p>
        </div>
      )}

      {/* H0. Why so few positions — the whole-experiment funnel, stated plainly.
          It sits above the per-day screening funnel because it answers the
          question a reader actually arrives with, and because leaving it
          collapsed invites the two wrong conclusions it exists to prevent. */}
      {!demo && <ExperimentFunnel />}

      {/* H. Screening funnel — compact and lower, fully auditable when expanded */}
      {!demo && (
        <details className="experiment-collapsible experiment-funnel">
          <summary>How today's slate was screened <small>Point-in-time qualification funnel · {funnelDate}</small></summary>
          <QualificationFunnel
            date={funnelDate}
            policy={experimentKey}
            onData={setFunnel}
            nav={<OperationalDateNav date={funnelDate} onChange={setFunnelDate} ariaLabel="Qualification funnel date" />}
          />
        </details>
      )}

      {/* Daily decision log and monthly ledger (secondary evidence) */}
      <details className="experiment-collapsible">
        <summary>Daily decision log</summary>
        <Panel title="Latest daily decisions" eyebrow="DAILY POINT-IN-TIME EVIDENCE" className="section-panel">
          <TableScroll ariaLabel="Daily decisions"><table className="data-table"><thead><tr><th scope="col">Date</th><th scope="col">Positions</th><th scope="col">Settled</th><th scope="col">Amount risked</th><th scope="col">Daily P/L</th><th scope="col">Bankroll after</th><th scope="col">Decision</th></tr></thead><tbody>
            {daily.length ? daily.map((row) => <tr key={row.date}><td><strong>{row.date}</strong></td><td>{row.positions}</td><td>{row.settled}</td><td>{formatCurrency(row.amount_risked)}</td><td className={plClass(row.profit_loss)}>{formatSignedCurrency(row.profit_loss)}</td><td>{formatCurrency(row.bankroll_after)}</td><td>{row.no_bet ? <span><strong>NO BET</strong>{row.no_bet_reasons.length ? <small className="daily-decision-reasons">{row.no_bet_reasons.join(' · ')}</small> : null}</span> : 'POSITION DAY'}</td></tr>) : <tr><td colSpan={7} className="empty-row">No daily decision inventory has been published yet. The active experiment is collecting evidence.</td></tr>}
          </tbody></table></TableScroll>
        </Panel>
        <Panel title="Monthly return" eyebrow="COMPLETE SEASON LEDGER" className="section-panel">
          {data.monthly.length ? <div className="monthly-list">{data.monthly.map((month) => <div key={month.month}><span><strong>{month.month}</strong><small>{month.positions} positions · {month.record}</small></span><b className={month.profit_loss >= 0 ? 'positive' : 'negative'}>{formatSignedCurrency(month.profit_loss)}</b><em>{month.positions ? formatSignedPct(month.roi) : '—'}</em></div>)}</div> : <p className="empty-hint">Monthly performance will appear only after positions settle.</p>}
        </Panel>
      </details>

      {/* I. Methodology, immutability, staking policy, identity, disclaimer */}
      <details className="experiment-collapsible experiment-methodology">
        <summary>How this experiment works</summary>
        <Panel title="Methodology" eyebrow="ONE MODEL · ONE POLICY · ONE VIRTUAL LEDGER">
          <ul>
            <li>The forecasting model is frozen for the life of the experiment. The V1 control and the {challengerVersion} challenger use the same model and differ only in decision policy.</li>
            <li>Every scheduled game is screened at fixed pregame phases. A selection becomes a position only at the final pregame evaluation, using the quote and forecast recorded at that moment.</li>
            <li>Stakes are sized from the current virtual bankroll by the staking policy; no stake ever exceeds the published caps, and there is no loss recovery.</li>
            <li>Net P/L and ROI use settled positions only. ROI divides net P/L by the amount actually risked, never by the bankroll.</li>
            <li>Closing-line value is reported only for positions with a captured closing quote. Missing quotes are shown as unavailable, never imputed as zero.</li>
            <li>Sample sizes this small cannot separate skill from noise. The page publishes bookkeeping facts, not a performance claim.</li>
          </ul>
        </Panel>
      </details>
      <details className="experiment-collapsible experiment-methodology">
        <summary>Why the ledger cannot be edited</summary>
        <Panel title="Immutability" eyebrow="FROZEN AT DECISION · SETTLED EXACTLY ONCE">
          <ul>
            <li>Each position carries the identity of the decision that created it, the forecast it used, the sportsbook quote at the decision, and the phase in which it was frozen.</li>
            <li>Settlement writes one result per position with the bankroll before and after; the equity curve above is that chain replayed in settlement order.</li>
            <li>Nothing is deleted or re-scored. Losses, pushes and voids stay in the ledger with the same visibility as wins.</li>
            <li>Opening any page of this application is read-only; the experiment is advanced only by the scheduled evaluation and settlement jobs.</li>
          </ul>
        </Panel>
      </details>
      <details className="experiment-collapsible">
        <summary>Staking policy</summary>
        <Panel title="Staking policy" eyebrow="PROVISIONAL · PHASE 1">
          <div className="policy-card"><ShieldAlert size={21} /><div><strong>{data.staking_policy ?? data.policy ?? policy.bankroll.label}</strong><p>{data.policy_detail}</p></div></div>
          <dl className="policy-details"><div><dt>Sizing method</dt><dd>{data.policy_kelly_fraction != null ? `${data.policy_kelly_fraction * 100}% Kelly` : policy.bankroll.kellyFraction != null ? `${policy.bankroll.kellyFraction * 100}% Kelly` : 'Configuration unavailable'}</dd></div><div><dt>Maximum stake</dt><dd>{data.policy_max_stake_percentage != null ? `${data.policy_max_stake_percentage}% of current bankroll` : policy.bankroll.maximumStakePercentage != null ? `${policy.bankroll.maximumStakePercentage}% of current bankroll` : 'Configuration unavailable'}</dd></div><div><dt>Loss recovery</dt><dd>None</dd></div><div><dt>Real-money execution</dt><dd>Not supported</dd></div></dl>
        </Panel>
      </details>
      <details className="experiment-collapsible">
        <summary>Experiment identity &amp; audit metadata</summary>
        <Panel title="Experiment identity" eyebrow="FROZEN AT EXPERIMENT START">
          <dl className="policy-details">
            <div><dt>Name</dt><dd>{data.experiment_name ?? (demo ? 'Illustrative experiment example' : 'Unavailable')}</dd></div>
            <div><dt>Experiment ID</dt><dd>{data.experiment_id ?? 'Unavailable'}</dd></div>
            <div><dt>Start date</dt><dd>{data.experiment_start_date ?? 'Unavailable'}</dd></div>
            <div><dt>Registered</dt><dd>{data.experiment_start ?? 'Unavailable'}</dd></div>
            <div><dt>Decision policy</dt><dd>{data.policy ?? 'Unavailable'}</dd></div>
            <div><dt>Staking policy</dt><dd>{data.staking_policy ?? 'Unavailable'}</dd></div>
            <div><dt>Policy checksum</dt><dd>{data.policy_checksum ?? 'Not recorded'}</dd></div>
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
