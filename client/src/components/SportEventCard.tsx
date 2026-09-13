/**
 * Sport-neutral event card shell with per-sport content slots.
 *
 * The shell owns everything every sport shares: league, participants, start
 * time and status, primary forecast, market comparison, readiness, evidence
 * freshness and the link to full analysis. Anything sport-shaped — starting
 * pitchers, quarterbacks, goalies, weight class — arrives through an adapter
 * slot, so adding a sport never means editing this file.
 *
 * Two rules are enforced here rather than left to callers:
 *
 *  1. A missing price or probability renders as "Not available", never 0.
 *  2. A sport without governed forecast authority cannot display a forecast at
 *     all, so a planned sport can never show a fabricated number.
 */
import { ArrowRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { PrefetchLink } from './PrefetchLink'
import {
  canShowForecasts,
  marketLabel,
  type MarketFamily,
  type ResolvedSport,
} from '../services/sportExperience'

/**
 * One event, described without assuming a home/away team game.
 *
 * `participants` is ordered; for TEAM_HOME_AWAY sports the convention is
 * [away, home], and for INDIVIDUAL_BOUT it is simply the two fighters. Nothing
 * in this contract requires a home side to exist.
 */
export interface SportEvent {
  eventId: string
  leagueCode: string
  participants: Array<{ name: string; abbreviation?: string | null }>
  startsAt: string | null
  statusLabel: string
  /** Home-side (or first-participant) win probability as a percentage. */
  forecastProbability: number | null
  /** Observed market probability on the same scale, when one exists. */
  marketProbability: number | null
  marketFamily: MarketFamily
  readinessLabel: string
  readinessTone: 'ready' | 'watching' | 'passed' | 'closed'
  evidenceFreshness: string | null
  detailHref: string | null
}

function value(probability: number | null): ReactNode {
  // Never 0% for an unobserved source.
  if (probability == null) return <span className="unavailable">Not available</span>
  return `${probability.toFixed(1)}%`
}

export function SportEventCard({
  sport, event, status, badge, children, extraFacts, footer, evidence,
}: {
  sport: ResolvedSport
  event: SportEvent
  /**
   * Plain-language decision status, shown directly under the participants so
   * the reader learns what the platform decided before seeing any number.
   */
  status?: ReactNode
  /** Optional qualifier shown beside the league, e.g. a within-sport rank. */
  badge?: ReactNode
  /** Sport-specific slot: pitchers, quarterbacks, goalies, weight class. */
  children?: ReactNode
  /**
   * Additional <div><dt/><dd/></div> facts appended to the shared fact row —
   * edge, stake and anything else the consumer genuinely has. Rendered only
   * where a forecast may be shown at all.
   */
  extraFacts?: ReactNode
  /** Sport-specific actions, e.g. the MLB Interactive Lab link. */
  footer?: ReactNode
  /** Technical evidence, kept behind a disclosure rather than in the summary. */
  evidence?: ReactNode
}) {
  const forecastsAllowed = canShowForecasts(sport)
  const headingId = `event-${event.eventId}`
  const [first, second] = event.participants

  return (
    <article
      className={`sport-event-card sport-event-card--${event.readinessTone}`}
      aria-labelledby={headingId}
      style={{ ['--sport-accent' as string]: sport.accent.base }}
    >
      <header className="sport-event-card__head">
        <span className="sport-event-card__league">
          <span aria-hidden="true">{sport.icon}</span> {sport.shortName}
        </span>
        <span className="sport-event-card__head-right">
          {badge}
          <span className={`sport-event-card__readiness sport-event-card__readiness--${event.readinessTone}`}>
            {event.readinessLabel}
          </span>
        </span>
      </header>

      <h3 id={headingId} className="sport-event-card__title">
        {/* A single-participant list is a label we could not split; show it as
            given rather than inventing an opponent. */}
        {event.participants.length === 1
          ? (first?.name ?? 'TBD')
          : sport.participantModel === 'INDIVIDUAL_BOUT'
            ? `${first?.name ?? 'TBD'} vs ${second?.name ?? 'TBD'}`
            : `${first?.name ?? 'TBD'} @ ${second?.name ?? 'TBD'}`}
      </h3>

      <p className="sport-event-card__when">
        {sport.terminology.startLabel}
        {': '}
        {event.startsAt ? new Date(event.startsAt).toLocaleString() : 'Not scheduled'}
        {' · '}
        {event.statusLabel}
      </p>

      {status && (
        <p className={`sport-event-card__status sport-event-card__status--${event.readinessTone}`}>
          {status}
        </p>
      )}

      {children && <div className="sport-event-card__slot">{children}</div>}

      {forecastsAllowed ? (
        <dl className="sport-event-card__facts">
          <div>
            <dt>AISP forecast</dt>
            <dd>{value(event.forecastProbability)}</dd>
          </div>
          <div>
            <dt>{marketLabel(sport, event.marketFamily) ?? 'Market'}</dt>
            <dd>{value(event.marketProbability)}</dd>
          </div>
          <div>
            <dt>Evidence</dt>
            <dd>{event.evidenceFreshness ?? <span className="unavailable">Not recorded</span>}</dd>
          </div>
          {extraFacts}
        </dl>
      ) : (
        <p className="sport-event-card__planned" role="status">
          {sport.statusExplanation}
        </p>
      )}

      <footer className="sport-event-card__foot">
        {footer}
        {event.detailHref && forecastsAllowed && (
          <PrefetchLink className="sport-event-card__link" to={event.detailHref}>
            Full analysis <ArrowRight size={13} aria-hidden="true" />
          </PrefetchLink>
        )}
      </footer>

      {evidence && (
        <details className="sport-event-card__evidence">
          <summary>Technical evidence</summary>
          {evidence}
        </details>
      )}
    </article>
  )
}

/**
 * Sport-specific slot content.
 *
 * Each adapter renders only what its sport genuinely has. `details` is supplied
 * by the caller from persisted evidence; an adapter never invents a value, and
 * an absent value is stated rather than filled in.
 */
export interface SportSlotDetails {
  /** MLB starting pitchers / NFL quarterbacks / NHL goalies, in participant order. */
  keyPlayers?: Array<string | null>
  /** MMA only. */
  weightClass?: string | null
  boutOrder?: string | null
}

export function SportSlot({ sport, details }: { sport: ResolvedSport; details: SportSlotDetails }) {
  if (sport.participantModel === 'INDIVIDUAL_BOUT') {
    return (
      <dl className="sport-slot">
        <div>
          <dt>Weight class</dt>
          <dd>{details.weightClass ?? <span className="unavailable">Not available</span>}</dd>
        </div>
        <div>
          <dt>Bout order</dt>
          <dd>{details.boutOrder ?? <span className="unavailable">Not available</span>}</dd>
        </div>
      </dl>
    )
  }

  const role = sport.terminology.keyRoleLabel
  if (!role) return null
  const [firstPlayer, secondPlayer] = details.keyPlayers ?? []
  return (
    <dl className="sport-slot">
      <div>
        <dt>{role}</dt>
        <dd>{firstPlayer ?? <span className="unavailable">Not announced</span>}</dd>
      </div>
      <div>
        <dt>{role}</dt>
        <dd>{secondPlayer ?? <span className="unavailable">Not announced</span>}</dd>
      </div>
    </dl>
  )
}
