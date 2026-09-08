/**
 * Why the Market Board has no current sportsbook prices.
 *
 * The rule this component exists to enforce: a reachable AISP API is NOT
 * evidence that market data is fresh. Those are two different facts and they
 * are stated separately here — the board can be perfectly healthy while
 * ingestion has been blocked for days.
 *
 * A historical observation is always shown with its own date and labelled as
 * historical. It is never presented as a current quote.
 */
import { AlertTriangle, ArrowRight, Clock3 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { formatTimestamp } from '../services/format'
import type { MarketFeedStatus } from '../types/domain'

export function MarketFeedNotice({ status, hasRows }: {
  status: MarketFeedStatus | null | undefined
  hasRows: boolean
}) {
  if (!status) return null
  // Nothing to explain when prices are flowing and current.
  if (!status.ingestion_blocked && hasRows && !status.latest_observation_is_historical) return null

  const tone = status.ingestion_blocked ? 'blocked' : 'stale'
  return (
    <section className={`market-feed-notice market-feed-notice--${tone}`} role="status" aria-live="polite">
      <div className="market-feed-notice__head">
        <AlertTriangle size={16} aria-hidden="true" />
        <strong>
          {status.ingestion_blocked
            ? 'Sportsbook ingestion is blocked'
            : 'No current sportsbook prices'}
        </strong>
      </div>

      <p className="market-feed-notice__reason">{status.reason}</p>

      <dl className="market-feed-notice__facts">
        <div>
          <dt>Last successful observation</dt>
          <dd>
            {status.latest_observation_at
              ? (
                <>
                  <Clock3 size={13} aria-hidden="true" />{' '}
                  {formatTimestamp(status.latest_observation_at)}
                  {status.latest_observation_is_historical && (
                    <span className="market-feed-notice__historical">
                      historical — not a current quote
                    </span>
                  )}
                </>
              )
              : <span className="unavailable">None recorded</span>}
          </dd>
        </div>
        <div>
          <dt>Provider credit at that reading</dt>
          <dd>
            {status.quota_remaining == null
              ? <span className="unavailable">Not reported</span>
              : `${status.quota_remaining} remaining · floor ${status.quota_floor}`}
            {status.quota_observed_at && (
              <small>read {formatTimestamp(status.quota_observed_at)}</small>
            )}
          </dd>
        </div>
        <div>
          <dt>Bounded recovery</dt>
          <dd>
            {status.recovery_probe_available
              ? 'One recovery probe is permitted on the next odds refresh.'
              : status.recovery_blocked_reason ?? 'Not currently permitted.'}
          </dd>
        </div>
      </dl>

      <p className="market-feed-notice__foot">
        Prediction-market quotes are collected separately and may still be current.{' '}
        <Link className="text-link" to="/market-pulse">
          Crowd &amp; Market Pulse <ArrowRight size={13} aria-hidden="true" />
        </Link>
      </p>
    </section>
  )
}
