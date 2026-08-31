import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Filter } from 'lucide-react'
import { Panel } from './ui'
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
}: {
  date: string
  compact?: boolean
  nav?: ReactNode
}) {
  const [data, setData] = useState<FunnelData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    setError(null)
    endpoints
      .qualificationFunnel(date, controller.signal)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e?.message ?? e)))
    return () => {
      active = false
      controller.abort()
    }
  }, [date])

  if (error) return <Panel title="Qualification funnel" eyebrow={`$1,000 EXPERIMENT · ${formatDate(date)}`}>{nav}<p className="empty-hint">{error}</p></Panel>
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
      eyebrow={`$1,000 EXPERIMENT · ${formatDate(data.date)}`}
      className="section-panel"
      action={<span className="info-label"><Filter size={13} /> Point-in-time · read-only</span>}
    >
      {nav}
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
      {!compact && (
        <details className="opportunity-blockers">
          <summary><span className="opportunity-blockers__label">Per-game evidence</span><span className="opportunity-blockers__count">{data.games.length} games</span></summary>
          <ol className="opportunity-blockers__detail">
            {data.games.map((g) => (
              <li key={g.game_id}>
                {g.matchup} · <b>{g.recommendation_state}</b>
                {g.failed_gate ? ` · first blocker: ${g.failed_gate}` : ''}
                {g.readiness != null ? ` · readiness ${Math.round(g.readiness)}` : ''}
              </li>
            ))}
          </ol>
        </details>
      )}
    </Panel>
  )
}

export default QualificationFunnel
