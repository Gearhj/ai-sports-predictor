import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Filter } from 'lucide-react'
import { Panel } from './ui'
import { PregameEvidenceBadges } from './PregameEvidenceBadges'
import { endpoints } from '../services/api'
import { formatDate } from '../services/format'
import type { QualificationFunnel as FunnelData } from '../types/domain'

/**
 * Transparent, READ-ONLY qualification funnel for the $1,000 experiment.
 * It never changes a threshold or creates a position; it shows exactly why the
 * selected operating date's slate did or did not produce virtual positions.
 */
export function QualificationFunnel({
  date,
  compact = false,
  nav,
  policy = 'v1',
}: {
  date: string
  compact?: boolean
  nav?: ReactNode
  policy?: 'v1' | 'v2'
}) {
  const [data, setData] = useState<FunnelData | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The challenger label follows the policy version the backend actually
  // evaluated ("…_POLICY_V2_1" → V2.1) so the funnel never claims a stale "V2".
  const versionSuffix = data?.policy?.match(/_V(\d+(?:_\d+)*)$/)
  const challengerVersion = versionSuffix ? `V${versionSuffix[1].replace(/_/g, '.')}` : 'V2.1'
  const eyebrowLabel = policy === 'v2'
    ? `${challengerVersion} PRACTICAL POLICY CHALLENGER · SAME MODEL`
    : '$1,000 EXPERIMENT · V1 STRICT CONTROL'

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    setError(null)
    endpoints
      .qualificationFunnel(date, controller.signal, policy)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e?.message ?? e)))
    return () => {
      active = false
      controller.abort()
    }
  }, [date, policy])

  if (error) return <Panel title="Qualification funnel" eyebrow={`${eyebrowLabel} · ${formatDate(date)}`}>{nav}<p className="empty-hint">{error}</p></Panel>
  if (!data || !Array.isArray(data.steps)) return null

  const first = data.steps[0]?.count || 1
  const steps = compact ? data.steps.filter((s) => ['scheduled_games', 'valid_forecast', 'fresh_quote', 'readiness', 'starter', 'qualified', 'positions_created'].includes(s.key)) : data.steps
  const scheduled = data.steps.find((s) => s.key === 'scheduled_games')?.count ?? 0
  const evaluated = data.steps.find((s) => s.key === 'valid_forecast')?.count ?? 0

  // Distinguish the honest zero states: nothing scheduled, scheduled but not
  // evaluated, evaluated with no qualifiers, and qualified with no position.
  let slateStatus: string
  if (scheduled === 0) slateStatus = 'No games are scheduled for this operating date.'
  else if (evaluated === 0) slateStatus = `${scheduled} game(s) scheduled — no evaluated forecasts for this date yet.`
  else if (data.qualified_selections === 0) slateStatus = `${scheduled} game(s) evaluated — no selections qualified. A no-position day is an honest experiment result.`
  else if (data.positions_created === 0) slateStatus = `${data.qualified_selections} selection(s) qualified but no position was created (exposure caps or final-pregame cutoff).`
  else slateStatus = `${data.positions_created} position(s) created from ${data.qualified_selections} qualified selection(s).`

  return (
    <Panel
      title="Qualification funnel"
      eyebrow={`${eyebrowLabel} · ${formatDate(data.date)}`}
      className="section-panel"
      action={<span className="info-label"><Filter size={13} /> Point-in-time · read-only</span>}
    >
      {nav}
      {policy === 'v2' && data.experiment_registered === false && (
        <p className="pulse-health-meta">Read-only {challengerVersion} policy preview — the challenger experiment is not yet registered; no decision or position is created.</p>
      )}
      {policy === 'v2' && data.experiment_registered && data.effective_date && (
        <p className="pulse-health-meta">{challengerVersion} effective from {formatDate(data.effective_date)} (prospective only — earlier slates are previews). Same forecasting model as V1; only the decision policy differs.</p>
      )}
      <p className="qual-funnel__status">{slateStatus}</p>
      <div className="qual-funnel">
        {steps.map((step) => (
          <div key={step.key} className="qual-funnel__step">
            <span className="qual-funnel__label">{step.label}</span>
            <span className="qual-funnel__bar" style={{ width: `${Math.max(2, (step.count / first) * 100)}%` }} />
            <span className="qual-funnel__value">{step.count}</span>
          </div>
        ))}
      </div>
      <p className="pulse-health-meta">
        Qualified selections: <b>{data.qualified_selections}</b> · Positions created: <b>{data.positions_created}</b>
        {data.exposure_cap_exclusions > 0 ? ` · Excluded by exposure caps: ${data.exposure_cap_exclusions}` : ''}
      </p>
      {data.top_rejection_reasons.length > 0 && (
        <p className="pulse-health-meta">
          Top blockers:{' '}
          {data.top_rejection_reasons.slice(0, compact ? 2 : 5).map((r) => `${r.reason} (${r.count})`).join(' · ')}
        </p>
      )}
      {data.starter_evidence_reconciliation && (
        <p className="pulse-health-meta" data-testid="starter-evidence-reconciliation">
          Starter evidence at the evaluation reference:{' '}
          {(['PASS', 'STALE', 'MISSING', 'UNSTABLE', 'CONFLICT', 'EVENT', 'NOT_EVALUATED'] as const)
            .filter((k) => (data.starter_evidence_reconciliation?.[k] ?? 0) > 0)
            .map((k) => `${k.toLowerCase().replace('_', ' ')} ${data.starter_evidence_reconciliation?.[k]}`)
            .join(' · ')}
          {' '}(sums to {scheduled}). {data.evidence_reference_policy ?? ''}
        </p>
      )}
      {data.diagnostics && (
        <p className="pulse-health-meta">
          Diagnostics: awaiting final-pregame evaluation {data.diagnostics.awaiting_final_pregame_evaluation} ·
          {' '}starter instability {data.diagnostics.blocked_only_by_starter_instability} ·
          {' '}lineup conflict {data.diagnostics.blocked_only_by_lineup_conflict} ·
          {' '}model-required input {data.diagnostics.blocked_only_by_model_required_input} ·
          {' '}formerly blocked by duplicate feature-completeness gate {data.diagnostics.previously_blocked_by_duplicate_feature_completeness_gate} ·
          {' '}unsupported market {data.diagnostics.unsupported_market} ·
          {' '}edge/EV/Kelly {data.diagnostics.edge_ev_kelly_failure} ·
          {' '}portfolio caps {data.diagnostics.portfolio_cap_failure}
        </p>
      )}
      {!compact && (
        <details className="opportunity-blockers">
          <summary><span className="opportunity-blockers__label">Per-game evidence</span><span className="opportunity-blockers__count">{data.games.length} games</span></summary>
          <ol className="opportunity-blockers__detail">
            {data.games.map((g) => (
              <li key={g.game_id}>
                {g.matchup} · <b>{g.recommendation_state}</b>
                {g.lifecycle ? ` · ${g.lifecycle}` : ''}
                {g.tier ? ` · ${g.tier}` : ''}
                {g.failed_gate ? ` · first blocker: ${g.failed_gate}` : ''}
                {g.starter_state_home ? ` · starters ${g.starter_state_home}/${g.starter_state_away ?? '—'}` : ''}
                {g.lineup_state ? ` · lineup ${g.lineup_state}` : ''}
                {g.adjusted_edge != null ? ` · adj edge ${(g.adjusted_edge * 100).toFixed(1)} pts` : ''}
                {g.readiness != null ? ` · readiness ${Math.round(g.readiness)}` : ''}
                {g.pregame_evidence && <PregameEvidenceBadges evidence={g.pregame_evidence} />}
              </li>
            ))}
          </ol>
        </details>
      )}
    </Panel>
  )
}

export default QualificationFunnel
