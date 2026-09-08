/**
 * One game, readable in a few seconds.
 *
 * Hierarchy is fixed and deliberate: matchup and time, then a plain-language
 * status, then the forecast, then the sportsbook evidence, then the decision
 * reason, then the next checkpoint. Exact technical evidence stays behind a
 * details disclosure so the summary never becomes a wall of governance text.
 *
 * Display honesty rules enforced here:
 *   - a missing price renders as "Not available", never as 0 or 0%
 *   - an edge appears only when both probabilities behind it exist
 *   - a countdown appears only when a real future timestamp exists
 *   - a within-sport rank is shown only for a game that was actually evaluated
 */
import { ArrowRight, Clock3 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { formatTime, formatTimestamp } from '../services/format'
import { edgeDisplay, fairAmericanOdds, type DerivedDecision, type DecisionGroup } from '../services/dailyDecision'
import { formatMoneyline, formatPct } from './ui'
import type { NormalizedSelection } from '../types/multisport'

const GROUP_TONE: Record<DecisionGroup, string> = {
  READY: 'ready',
  WATCHING: 'watching',
  PASSED: 'passed',
  CLOSED: 'closed',
}

/** Groups whose games were genuinely evaluated, so a rank can't be mistaken for a tip. */
const RANKABLE_GROUPS = new Set<DecisionGroup>(['READY'])

function countdown(startsAt: string | null, now: number): string | null {
  if (!startsAt) return null
  const start = Date.parse(startsAt)
  if (Number.isNaN(start)) return null
  const minutes = Math.round((start - now) / 60000)
  if (minutes <= 0) return null
  if (minutes < 60) return `in ${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return `in ${hours}h${rest ? ` ${rest}m` : ''}`
}

export function GameDecisionCard({
  selection,
  decision,
  now,
}: {
  selection: NormalizedSelection
  decision: DerivedDecision
  now: number
}) {
  const tone = GROUP_TONE[decision.group]
  const edge = edgeDisplay(selection)
  const fair = fairAmericanOdds(selection.model_probability)
  const startCountdown = countdown(selection.starts_at, now)
  const showRank = selection.sport_rank != null && RANKABLE_GROUPS.has(decision.group)
  const headingId = `game-${selection.event_id}-${selection.policy_version ?? 'default'}`

  return (
    <article className={`decision-card decision-card--${tone}`} aria-labelledby={headingId}>
      <header className="decision-card__head">
        <div className="decision-card__identity">
          <h3 id={headingId}>{selection.event_label}</h3>
          <p className="decision-card__when">
            {selection.starts_at ? formatTime(selection.starts_at) : 'Start time not recorded'}
            {startCountdown && <span className="decision-card__countdown"> · {startCountdown}</span>}
          </p>
        </div>
        <div className="decision-card__tags">
          <span className={`sport-code sport-code--${selection.league_code.toLowerCase()}`}>
            {selection.league_code}
          </span>
          {showRank && (
            <span className="decision-card__rank">
              #{selection.sport_rank} in {selection.league_code}
            </span>
          )}
        </div>
      </header>

      {/* Status carries a text label, not colour alone. */}
      <p className={`decision-card__status decision-card__status--${tone}`}>{decision.status}</p>
      {decision.detail && <p className="decision-card__detail">{decision.detail}</p>}

      <dl className="decision-card__facts">
        <div>
          <dt>ASP forecast</dt>
          <dd>
            {selection.model_probability == null
              ? <span className="unavailable">Not available</span>
              : (
                <>
                  {formatPct(selection.model_probability)}
                  {fair != null && <small> · fair {formatMoneyline(fair)}</small>}
                </>
              )}
          </dd>
        </div>
        <div>
          <dt>Sportsbook</dt>
          <dd>
            {selection.price_american == null
              ? <span className="unavailable">Not available</span>
              : (
                <>
                  {formatMoneyline(selection.price_american)}
                  {selection.no_vig_probability != null && (
                    <small> · no-vig {formatPct(selection.no_vig_probability)}</small>
                  )}
                </>
              )}
          </dd>
        </div>
        <div>
          <dt>{edge ? edge.label : 'Edge'}</dt>
          <dd>
            {edge
              ? <span className={edge.valuePercent >= 0 ? 'edge-positive' : 'edge-negative'}>
                  {edge.valuePercent > 0 ? '+' : ''}{edge.valuePercent.toFixed(1)}%
                </span>
              : <span className="unavailable">Not available</span>}
          </dd>
        </div>
        <div>
          <dt>Virtual stake</dt>
          <dd>
            {selection.virtual_stake == null
              ? <span className="unavailable">None</span>
              : `$${selection.virtual_stake.toFixed(2)}`}
          </dd>
        </div>
      </dl>

      <footer className="decision-card__foot">
        {decision.checkpoint
          ? (
            <span className="decision-card__checkpoint">
              <Clock3 size={13} aria-hidden="true" />
              {decision.checkpoint.label}: {formatTimestamp(decision.checkpoint.at)}
            </span>
          )
          : <span className="decision-card__checkpoint decision-card__checkpoint--none">No scheduled checkpoint</span>}
        {selection.detail_href && (
          <Link className="decision-card__link" to={selection.detail_href}>
            Full analysis <ArrowRight size={13} aria-hidden="true" />
          </Link>
        )}
      </footer>

      <details className="decision-card__evidence">
        <summary>Evidence detail</summary>
        <dl>
          <div><dt>Decision state</dt><dd>{selection.decision_state}</dd></div>
          <div><dt>Funding state</dt><dd>{selection.funding_state}</dd></div>
          <div><dt>Market</dt><dd>{selection.market_label ?? 'Not evaluated'}</dd></div>
          <div><dt>Selection</dt><dd>{selection.selection_label ?? '—'}</dd></div>
          <div><dt>Book</dt><dd>{selection.sportsbook ?? 'None recorded'}</dd></div>
          <div><dt>Evidence tier</dt><dd>{selection.evidence_tier ?? 'Not recorded'}</dd></div>
          <div><dt>Experiment</dt><dd>{selection.experiment_name ?? 'None'}</dd></div>
          <div><dt>Policy</dt><dd>{selection.policy_version ?? 'None'}</dd></div>
          <div><dt>Engine</dt><dd>{selection.source_label ?? 'Not recorded'}</dd></div>
          <div><dt>Forecast lifecycle</dt><dd>{selection.lifecycle_stage ?? 'Not recorded'}</dd></div>
          <div><dt>Event status</dt><dd>{selection.event_status ?? 'Not recorded'}</dd></div>
          <div>
            <dt>Decision recorded</dt>
            <dd>{selection.decision_timestamp ? formatTimestamp(selection.decision_timestamp) : 'Not recorded'}</dd>
          </div>
        </dl>
        {selection.outstanding_gates.length > 0 && (
          <p className="decision-card__gates">
            <strong>Outstanding gates:</strong> {selection.outstanding_gates.join(' · ')}
          </p>
        )}
        {selection.status_note && <p className="decision-card__note">{selection.status_note}</p>}
      </details>
    </article>
  )
}
