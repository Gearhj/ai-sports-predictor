/**
 * Adapts one governed decision onto the shared `SportEventCard` shell.
 *
 * This is the only place that knows how a `NormalizedSelection` becomes a
 * generic sport event, which is what keeps the Daily Command Center from being
 * a baseball page with other sports bolted on. The shell owns the layout and
 * the two display-honesty rules (missing values are stated, planned sports show
 * no forecast); this adapter owns the mapping and adds the decision-specific
 * facts: edge, virtual stake, checkpoint and technical evidence.
 *
 * Nothing here re-decides anything. Group, status and checkpoint all arrive
 * already derived from persisted authority in `services/dailyDecision`.
 */
import { Clock3 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { SportEventCard, type SportEvent } from './SportEventCard'
import { formatMoneyline, formatPct } from './ui'
import { edgeDisplay, fairAmericanOdds, type GroupedGame } from '../services/dailyDecision'
import { formatTimestamp } from '../services/format'
import { labIsAvailable, type MarketFamily, type ResolvedSport } from '../services/sportExperience'
import type { NormalizedSelection } from '../types/multisport'

/** Group -> card tone and reader-facing label. */
const TONE = {
  READY: { tone: 'ready', label: 'Ready' },
  WATCHING: { tone: 'watching', label: 'Watching' },
  PASSED: { tone: 'passed', label: 'Passed' },
  CLOSED: { tone: 'closed', label: 'Closed for decisions' },
} as const

/**
 * Splits a persisted event label into participants without assuming baseball.
 *
 * "ATL @ PHI" and "A vs B" both split; anything else is returned as one
 * participant so the card shows the recorded label rather than inventing an
 * opponent named TBD.
 */
export function participantsFromLabel(label: string): Array<{ name: string }> {
  const parts = label.split(/\s+(?:@|vs\.?|v\.)\s+/i).map((part) => part.trim()).filter(Boolean)
  if (parts.length === 2) return [{ name: parts[0] }, { name: parts[1] }]
  return [{ name: label.trim() || 'Not recorded' }]
}

/** Maps the persisted market label onto the normalised family the shell shows. */
export function marketFamilyOf(selection: NormalizedSelection): MarketFamily {
  const label = (selection.market_label ?? '').toUpperCase()
  if (label.includes('SPREAD') || label.includes('RUN_LINE') || label.includes('RUNLINE')
    || label.includes('PUCK') || label.includes('HANDICAP')) return 'HANDICAP'
  if (label.includes('TOTAL') || label.includes('OVER') || label.includes('UNDER')) return 'TOTAL'
  if (label.includes('METHOD')) return 'METHOD'
  if (label.includes('ROUND')) return 'ROUND'
  return 'WINNER'
}

/** Short human age used as the card's evidence line. Never guesses. */
export function evidenceAge(at: string | null, now: number): string | null {
  if (!at) return null
  const value = Date.parse(at)
  if (Number.isNaN(value)) return null
  const minutes = Math.round((now - value) / 60000)
  if (minutes < 0) return formatTimestamp(at)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function toSportEvent(item: GroupedGame, now: number): SportEvent {
  const { selection, decision } = item
  const meta = TONE[decision.group]
  const startedLabel = selection.event_status
    ? selection.event_status.charAt(0) + selection.event_status.slice(1).toLowerCase()
    : 'Status not recorded'
  return {
    eventId: `${selection.event_id}:${selection.policy_version ?? 'none'}`,
    leagueCode: selection.league_code,
    participants: participantsFromLabel(selection.event_label),
    startsAt: selection.starts_at,
    statusLabel: startedLabel,
    forecastProbability: selection.model_probability,
    marketProbability: selection.no_vig_probability,
    marketFamily: marketFamilyOf(selection),
    readinessLabel: meta.label,
    readinessTone: meta.tone,
    evidenceFreshness: evidenceAge(selection.decision_timestamp, now),
    detailHref: selection.detail_href,
  }
}

export function DecisionEventCard({ item, sport, now }: {
  item: GroupedGame
  sport: ResolvedSport
  now: number
}) {
  const { selection, decision } = item
  const edge = edgeDisplay(selection)
  const fair = fairAmericanOdds(selection.model_probability)
  const event = toSportEvent(item, now)
  // A rank is shown only where a game was genuinely evaluated and qualified,
  // so it can never read as a tip on an unevaluated game.
  const showRank = selection.sport_rank != null && decision.group === 'READY'
  const labHref = labIsAvailable(sport) && sport.lab?.routeTemplate && selection.detail_href
    ? `${selection.detail_href}/simulation`
    : null

  return (
    <SportEventCard
      sport={sport}
      event={event}
      badge={showRank ? (
        <span className="sport-event-card__rank">#{selection.sport_rank} in {selection.league_code}</span>
      ) : null}
      status={(
        <>
          {/* Headline and supporting sentence stay separate elements: the
              headline alone must read correctly for its situation. */}
          <span className="sport-event-card__status-line">{decision.status}</span>
          {decision.detail && <small>{decision.detail}</small>}
        </>
      )}
      extraFacts={(
        <>
          {/* The decision-frozen forecast is often absent because the decision
              never got a priceable quote. The current model forecast is a
              different, independently persisted fact, so it gets its own row
              with its own time rather than being written into the decision. */}
          {selection.model_probability == null
            && selection.current_forecast_probability != null && (
            <div>
              <dt>Current model forecast</dt>
              <dd>
                {formatPct(selection.current_forecast_probability)}
                <small>
                  {selection.current_forecast_generated_at
                    ? `as of ${formatTimestamp(selection.current_forecast_generated_at)}`
                    : 'time not recorded'}
                  {selection.current_forecast_model
                    ? ` · ${selection.current_forecast_model}`
                    : ''}
                  {' · not the forecast frozen into this decision'}
                </small>
              </dd>
            </div>
          )}
          <div>
            <dt>Sportsbook</dt>
            <dd>
              {selection.price_american == null
                ? <span className="unavailable">Not available</span>
                : formatMoneyline(selection.price_american)}
            </dd>
          </div>
          <div>
            {/* An edge exists only when both probabilities behind it exist. */}
            <dt>{edge ? edge.label : 'Edge'}</dt>
            <dd>
              {edge
                ? (
                  <span className={edge.valuePercent >= 0 ? 'edge-positive' : 'edge-negative'}>
                    {edge.valuePercent > 0 ? '+' : ''}{edge.valuePercent.toFixed(1)}%
                  </span>
                )
                : <span className="unavailable">Not available</span>}
            </dd>
          </div>
          <div>
            <dt>Virtual stake</dt>
            <dd>
              {selection.virtual_stake == null
                ? <span className="unavailable">No position</span>
                : `$${selection.virtual_stake.toFixed(2)}`}
            </dd>
          </div>
        </>
      )}
      footer={(
        <>
          {/* A checkpoint is shown only when a real future timestamp exists. */}
          {decision.checkpoint && (
            <span className="sport-event-card__checkpoint">
              <Clock3 size={13} aria-hidden="true" />
              {decision.checkpoint.label}: {formatTimestamp(decision.checkpoint.at)}
            </span>
          )}
          {labHref && (
            <Link className="sport-event-card__lab" to={labHref}>
              {sport.lab?.name.split('— ')[1] ?? 'Interactive Lab'}
            </Link>
          )}
        </>
      )}
      /*
       * The read model does not persist per-participant roles (starting
       * pitcher, quarterback, goalie) on a decision, so the shared sport slot
       * is left empty rather than filled with placeholders. It stays available
       * for a sport that does persist them.
       */
      evidence={(
        <>
          <dl>
            <div><dt>Decision state</dt><dd>{selection.decision_state}</dd></div>
            <div><dt>Funding state</dt><dd>{selection.funding_state}</dd></div>
            <div><dt>Market</dt><dd>{selection.market_label ?? 'Not evaluated'}</dd></div>
            <div><dt>Selection</dt><dd>{selection.selection_label ?? 'Not recorded'}</dd></div>
            <div><dt>Book</dt><dd>{selection.sportsbook ?? 'None recorded'}</dd></div>
            <div><dt>Evidence tier</dt><dd>{selection.evidence_tier ?? 'Not recorded'}</dd></div>
            <div><dt>Experiment</dt><dd>{selection.experiment_name ?? 'None'}</dd></div>
            <div><dt>Policy</dt><dd>{selection.policy_version ?? 'None'}</dd></div>
            <div><dt>Engine</dt><dd>{selection.source_label ?? 'Not recorded'}</dd></div>
            <div><dt>Event status</dt><dd>{selection.event_status ?? 'Not recorded'}</dd></div>
            <div>
              <dt>Model forecast</dt>
              <dd>
                {selection.model_probability == null
                  ? 'Not available'
                  : `${formatPct(selection.model_probability)}${fair != null ? ` · fair ${formatMoneyline(fair)}` : ''}`}
              </dd>
            </div>
            <div>
              <dt>Decision recorded</dt>
              <dd>{selection.decision_timestamp ? formatTimestamp(selection.decision_timestamp) : 'Not recorded'}</dd>
            </div>
          </dl>
          {selection.outstanding_gates.length > 0 && (
            <p className="sport-event-card__gates">
              <strong>Outstanding gates:</strong> {selection.outstanding_gates.join(' · ')}
            </p>
          )}
        </>
      )}
    />
  )
}
