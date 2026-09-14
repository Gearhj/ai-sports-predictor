/**
 * Parlay Watch: research combinations, with the estimate labelled as an estimate.
 *
 * WHAT THIS SCREEN IS CAREFUL ABOUT
 * ---------------------------------
 *  - **"Every leg wins" is not "the ticket pays".** Both are shown, with their
 *    own labels, whenever a leg can push. Showing only the second would flatter
 *    the ticket; showing only the first would understate what it actually
 *    returns after a push removes a leg.
 *  - **An estimated price is not an offer.** A combined price built from a
 *    book's individual leg odds is badged Estimated and says, in words, that the
 *    book may not offer it. A real recorded combined quote would be badged
 *    differently; AISP holds none today, and the screen says that rather than
 *    letting the estimate pass for one.
 *  - **Independence is an approximation.** It is printed on the screen, not
 *    buried in a tooltip, because every combined number above depends on it.
 *  - **Recording is deliberate.** The button freezes a ticket; it is not a
 *    side effect of browsing, and the server re-derives every number rather
 *    than trusting what is on screen.
 */
import { useState } from 'react'
import { AlertTriangle, Check, Info, Trophy, Zap } from 'lucide-react'
import { RecordedTickets } from './RecordedTickets'
import type { RecordedParlayTicket } from '../../types/totals'
import type {
  ParlayTicketCandidate, ParlayWatch,
} from '../../types/totals'

const FILTERS: { key: ParlayWatch['market_filter']; label: string; hint: string }[] = [
  { key: 'ALL', label: 'All markets', hint: 'Moneylines, Overs and Unders.' },
  { key: 'OVERS_ONLY', label: 'Overs only', hint: 'Every leg is an Over.' },
  { key: 'TOTALS_ONLY', label: 'Totals only', hint: 'Overs and Unders, no moneylines.' },
  { key: 'MIXED', label: 'Mixed', hint: 'At least two different markets on the ticket.' },
]

const FEATURE_LABEL: Record<string, string> = {
  HIGHEST_ESTIMATED_CHANCE: 'Highest estimated chance',
  BEST_ESTIMATED_VALUE: 'Best estimated value',
}

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : `${value.toFixed(digits)}%`
}

function signedUnit(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(3)}`
}

function american(value: number | null): string {
  if (value == null) return '—'
  return value > 0 ? `+${value}` : `${value}`
}

function clock(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString([], {
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

function TicketCard({
  ticket, onRecord, recording, recorded,
}: {
  ticket: ParlayTicketCandidate
  onRecord: (ticket: ParlayTicketCandidate) => void
  recording: boolean
  recorded: string | null
}) {
  const price = ticket.price
  const estimated = price.price_provenance === 'ESTIMATED_FROM_SAME_BOOK_LEG_ODDS'
  return (
    <article className="parlay__ticket" data-testid="parlay-ticket">
      {ticket.feature_labels && (
        <div className="parlay__features">
          {ticket.feature_labels.map((label) => (
            <span key={label} className="parlay__feature">
              {label === 'HIGHEST_ESTIMATED_CHANCE'
                ? <Trophy size={12} aria-hidden="true" />
                : <Zap size={12} aria-hidden="true" />}
              {FEATURE_LABEL[label]}
            </span>
          ))}
        </div>
      )}
      {ticket.feature_note && <p className="parlay__feature-note">{ticket.feature_note}</p>}

      <ol className="parlay__legs">
        {ticket.legs.map((leg) => (
          <li key={`${leg.game_id}-${leg.selection}`}>
            <b>{leg.selection}</b>
            <span className="parlay__leg-price">{american(leg.american_odds)}</span>
            <span className="parlay__leg-prob">
              {pct(leg.win_probability_pct)} win
              {leg.push_possible && ` · ${pct(leg.push_probability_pct)} push`}
            </span>
          </li>
        ))}
      </ol>

      <dl className="parlay__numbers">
        <div>
          <dt>Ticket pays</dt>
          <dd data-testid="parlay-pays">{pct(price.ticket_pays_probability_pct, 2)}</dd>
        </div>
        <div>
          <dt>Every leg wins</dt>
          <dd>{pct(price.all_legs_win_probability_pct, 2)}</dd>
        </div>
        {price.push_possible && (
          <div>
            <dt>Stake returned</dt>
            <dd>{pct(price.stake_returned_probability_pct, 2)}</dd>
          </div>
        )}
        <div>
          <dt>Combined price</dt>
          <dd>
            {price.combined_decimal_odds.toFixed(2)}
            <span className={`parlay__badge ${estimated ? 'parlay__badge--estimated' : ''}`}>
              {estimated ? 'Estimated' : 'Recorded quote'}
            </span>
          </dd>
        </div>
        <div>
          <dt>Payout on $1</dt>
          <dd>${price.payout_multiple_if_all_win.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Estimated value</dt>
          <dd data-testid="parlay-value">{signedUnit(price.estimated_net_return_per_unit)}</dd>
        </div>
        <div>
          <dt>Model fair price</dt>
          <dd>
            {price.model_fair_decimal_odds != null
              ? price.model_fair_decimal_odds.toFixed(2) : '—'}
          </dd>
        </div>
        <div>
          <dt>Earliest cutoff</dt>
          <dd>{clock(ticket.earliest_cutoff_utc)}</dd>
        </div>
      </dl>

      <p className="parlay__book">
        All legs priced at <b>{ticket.sportsbook}</b>.
        {price.push_possible && ' A pushed leg is removed and the ticket reprices; it is not a loss.'}
      </p>

      {recorded ? (
        <p className="parlay__recorded" role="status">
          <Check size={13} aria-hidden="true" /> {recorded}
        </p>
      ) : (
        <button
          type="button"
          className="parlay__record"
          disabled={recording}
          onClick={() => onRecord(ticket)}
          data-testid="parlay-record"
        >
          {recording ? 'Recording…' : 'Record virtual ticket ($1)'}
        </button>
      )}
    </article>
  )
}

export function ParlayWatchView({
  watch, legCount, onLegCount, marketFilter, onMarketFilter, onRecord,
  recordingKey, recordedByKey, recordError, recorded,
}: {
  watch: ParlayWatch | null
  /** Tickets already recorded, each leg shown beside its game's score. */
  recorded?: RecordedParlayTicket[]
  legCount: 2 | 3
  onLegCount: (next: 2 | 3) => void
  marketFilter: ParlayWatch['market_filter']
  onMarketFilter: (next: ParlayWatch['market_filter']) => void
  onRecord: (ticket: ParlayTicketCandidate) => void
  recordingKey: string | null
  recordedByKey: Record<string, string>
  recordError: string | null
}) {
  const [showAll, setShowAll] = useState(false)
  if (!watch) return <p className="parlay__empty">Parlay Watch unavailable.</p>

  const featured = watch.featured
  const rest = watch.tickets.filter(
    (ticket) => !featured.some((f) => f.ticket_key === ticket.ticket_key))

  return (
    <section className="parlay" data-testid="parlay-watch">
      {/* Recorded tickets first: a reader who already has one is here to
          follow it, not to look for another. */}
      {recorded !== undefined && (
        <div className="parlay__recorded-block">
          <h3 className="parlay__subhead">Recorded tickets</h3>
          <RecordedTickets tickets={recorded} />
        </div>
      )}
      <div className="parlay__controls">
        <div className="parlay__legs-toggle" role="group" aria-label="Legs per ticket">
          {[2, 3].map((count) => (
            <button
              key={count}
              type="button"
              aria-pressed={legCount === count}
              className={legCount === count ? 'is-active' : undefined}
              onClick={() => onLegCount(count as 2 | 3)}
            >
              {count} legs
            </button>
          ))}
        </div>
        <div className="parlay__filters" role="group" aria-label="Market filter">
          {FILTERS.map((filter) => (
            <button
              key={filter.key}
              type="button"
              title={filter.hint}
              aria-pressed={marketFilter === filter.key}
              className={marketFilter === filter.key ? 'is-active' : undefined}
              onClick={() => onMarketFilter(filter.key)}
              data-testid={`parlay-filter-${filter.key}`}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      <p className="parlay__inventory" data-testid="parlay-inventory">
        {watch.legs_available} eligible legs across {watch.leg_inventory.games} games ·{' '}
        {watch.candidate_count} priced combinations
        {watch.truncated && ` (showing the best ${watch.tickets.length})`}
        {' · '}
        {watch.legs_from_index_verified_forecasts} leg
        {watch.legs_from_index_verified_forecasts === 1 ? '' : 's'} from an
        index-verified forecast
      </p>

      <p className="parlay__assumption" role="note" data-testid="parlay-independence">
        <Info size={13} aria-hidden="true" /> {watch.independence_note}
      </p>
      <p className="parlay__assumption" role="note" data-testid="parlay-estimated-note">
        <AlertTriangle size={13} aria-hidden="true" /> {watch.estimated_price_note}
      </p>

      {recordError && (
        <p className="parlay__error" role="alert">{recordError}</p>
      )}

      {featured.length > 0 ? (
        <div className="parlay__grid" data-testid="parlay-featured">
          {featured.map((ticket) => (
            <TicketCard
              key={ticket.ticket_key}
              ticket={ticket}
              onRecord={onRecord}
              recording={recordingKey === ticket.ticket_key}
              recorded={recordedByKey[ticket.ticket_key] ?? null}
            />
          ))}
        </div>
      ) : (
        <p className="parlay__empty" data-testid="parlay-empty">
          No eligible combination exists for this slate right now.
          {watch.legs_available > 0 && watch.leg_inventory.games < legCount && (
            ` Only ${watch.leg_inventory.games} game${watch.leg_inventory.games === 1 ? '' : 's'}`
            + ` still has a usable leg, and a ticket needs ${legCount} distinct games.`
          )}
        </p>
      )}

      {featured.length === 1 && watch.candidate_count > 1 && (
        <p className="parlay__single" role="note">
          One combination leads on both measures, so one is featured rather than two.
        </p>
      )}

      {rest.length > 0 && (
        <details className="parlay__more" open={showAll}
                 onToggle={(event) => setShowAll(event.currentTarget.open)}>
          <summary>{rest.length} more combinations</summary>
          <div className="parlay__grid">
            {rest.map((ticket) => (
              <TicketCard
                key={ticket.ticket_key}
                ticket={ticket}
                onRecord={onRecord}
                recording={recordingKey === ticket.ticket_key}
                recorded={recordedByKey[ticket.ticket_key] ?? null}
              />
            ))}
          </div>
        </details>
      )}

      {watch.excluded_games.length > 0 && (
        <details className="parlay__excluded">
          <summary>
            {watch.excluded_games.length} games excluded, and why
          </summary>
          <ul>
            {watch.excluded_games.map((row) => (
              <li key={row.game_id}>
                <b>{row.matchup}</b> — {row.detail}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
