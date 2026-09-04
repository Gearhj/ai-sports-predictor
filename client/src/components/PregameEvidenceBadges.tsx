import type { PregameEvidence, PregameStarterEvidence } from '../types/domain'
import { formatTimestamp } from '../services/format'

/**
 * Compact, honest pregame evidence: source status, observation age at the
 * evaluation reference, freshness verdict, lineup source state and the exact
 * starter block reason. Expandable detail; never a raw diagnostic wall.
 *
 *   Starter: PROBABLE · observed 42m before reference · FRESH
 *   Lineup:  NOT YET PUBLISHED
 */
function freshnessClass(f: string): string {
  if (f === 'FRESH') return 'evidence-badge evidence-badge--fresh'
  if (f === 'STALE' || f === 'INVALID_FUTURE_DATED') return 'evidence-badge evidence-badge--stale'
  return 'evidence-badge evidence-badge--missing'
}

function StarterBadge({ side, label }: { side: PregameStarterEvidence; label: string }) {
  return (
    <span className={freshnessClass(side.freshness)} title={side.reasons.join('; ') || side.label}>
      {label} {side.status ?? 'NOT EVALUATED'}
      {side.age_minutes_at_reference != null ? ` · observed ${ageText(side.age_minutes_at_reference)} before reference` : ' · no observation'}
      {' · '}{side.freshness}
    </span>
  )
}

export function ageText(minutes: number): string {
  const m = Math.round(minutes)
  if (m < 0) return 'future-dated'
  const h = Math.floor(m / 60)
  const r = m % 60
  return h ? `${h}h ${String(r).padStart(2, '0')}m` : `${r}m`
}

export function PregameEvidenceBadges({ evidence, compact = false }: { evidence: PregameEvidence; compact?: boolean }) {
  const category = evidence.starter_block_category
  const blocked = category !== 'PASS' && category !== 'NOT_EVALUATED'
  return (
    <span className="pregame-evidence" data-testid="pregame-evidence">
      <StarterBadge side={evidence.starter_home} label="Home starter" />
      <StarterBadge side={evidence.starter_away} label="Away starter" />
      <span className={`evidence-badge evidence-badge--lineup`} title={evidence.lineup.reasons?.join('; ') ?? ''}>{evidence.lineup.label}</span>
      {blocked && (
        <span className="evidence-badge evidence-badge--block" title={evidence.starter_block_reason ?? ''}>
          Blocked: {category}
        </span>
      )}
      {!compact && (
        <details className="pregame-evidence__detail">
          <summary>Evidence detail</summary>
          <dl>
            <div><dt>Source</dt><dd>{evidence.starter_home.source ?? evidence.starter_away.source ?? 'unavailable'}</dd></div>
            <div><dt>Home observed</dt><dd>{evidence.starter_home.observed_at ? formatTimestamp(evidence.starter_home.observed_at) : 'none'}</dd></div>
            <div><dt>Away observed</dt><dd>{evidence.starter_away.observed_at ? formatTimestamp(evidence.starter_away.observed_at) : 'none'}</dd></div>
            <div><dt>Reference</dt><dd>{formatTimestamp(evidence.reference_at)}{evidence.reference_is_cutoff ? ' (final cutoff)' : ' (evaluation instant; cutoff not yet reached)'}</dd></div>
            <div><dt>Cutoff</dt><dd>{formatTimestamp(evidence.cutoff_at)}</dd></div>
            {evidence.lineup.home && <div><dt>Home lineup</dt><dd>{evidence.lineup.home.label}{evidence.lineup.home.observed_at ? ` · ${formatTimestamp(evidence.lineup.home.observed_at)}` : ''}</dd></div>}
            {evidence.lineup.away && <div><dt>Away lineup</dt><dd>{evidence.lineup.away.label}{evidence.lineup.away.observed_at ? ` · ${formatTimestamp(evidence.lineup.away.observed_at)}` : ''}</dd></div>}
            {evidence.starter_block_reason && <div><dt>Block reason</dt><dd>{evidence.starter_block_reason}</dd></div>}
            <div><dt>Note</dt><dd>{evidence.source_note}</dd></div>
          </dl>
        </details>
      )}
    </span>
  )
}

export default PregameEvidenceBadges
