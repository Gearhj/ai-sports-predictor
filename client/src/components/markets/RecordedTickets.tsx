/**
 * Recorded parlay tickets, each leg beside the score of the game it needs.
 *
 * WHY EVERY LEG GETS ITS OWN SCOREBOARD
 * -------------------------------------
 * A parlay is the one place where "how am I doing" is genuinely hard to read.
 * Two legs winning and one trailing is not two-thirds of a payout; the ticket
 * pays or it does not. So each leg shows its own game's score and its own
 * recorded settlement status, and the ticket's own status is stated separately
 * from all of them.
 *
 * WHAT IT WILL NOT IMPLY
 * ----------------------
 * *   **That a leading leg has won.** Leg status comes from the settlement
 *     record, not from the scoreboard. A leg whose game reads FINAL while its
 *     settlement is still pending shows exactly that, because that is the true
 *     state and hiding it would mean inventing a payout.
 * *   **That the ticket is cashable.** A research ticket is unfunded and says
 *     so; a season-account ticket names the account. Neither is an offer.
 */
import { Layers } from 'lucide-react'
import { GameScoreboard } from '../scoreboard/GameScoreboard'
import { useScoreboard } from '../../hooks/useScoreboard'
import type { GameScoreboard as Scoreboard } from '../../types/scoreboard'
import type { RecordedParlayTicket } from '../../types/totals'

function american(value: number | null | undefined): string {
  if (value == null) return '—'
  return value > 0 ? `+${value}` : `${value}`
}

function resultTone(result: string): string {
  const value = result.toUpperCase()
  if (value === 'WIN') return 'ticket__result--win'
  if (value === 'LOSS') return 'ticket__result--loss'
  return 'ticket__result--pending'
}

function Ticket({
  ticket, games, onRefresh,
}: {
  ticket: RecordedParlayTicket
  games: Record<string, Scoreboard>
  onRefresh: () => void
}) {
  const funded = ticket.funding === 'FUNDED_SEASON_ACCOUNT'
  return (
    <article className="ticket" data-testid={`recorded-ticket-${ticket.id}`}>
      <header className="ticket__head">
        <span className="ticket__title">
          <Layers size={13} aria-hidden="true" /> {ticket.leg_count}-leg parlay
        </span>
        <span className="bx-dim">{ticket.sportsbook}</span>
        <span className="bx-dim" data-testid="ticket-recorded">Recorded {ticketDay(ticket.recorded_at_utc)}</span>
        <span className={`bx-chip ${funded ? '' : 'ticket__chip--research'}`}>
          {funded ? 'Season account' : 'Research · unfunded'}
        </span>
        <span className={`ticket__result ${resultTone(ticket.result)}`}>
          {ticket.result.toUpperCase() === 'PENDING'
            ? 'Not settled'
            : ticket.result.toUpperCase()}
        </span>
      </header>

      <p className="ticket__price">
        Stake <b className="bx-num">${ticket.stake.toFixed(2)}</b> at{' '}
        <b className="bx-num">{ticket.combined_decimal_odds.toFixed(2)}</b>
        {' · potential payout '}
        <b className="bx-num">${ticket.potential_payout.toFixed(2)}</b>
        {!ticket.price_is_an_offer && (
          <span className="bx-dim"> · estimated price, not an offer</span>
        )}
      </p>

      <ol className="ticket__legs">
        {ticket.legs.map((leg) => {
          const game = games[leg.game_id] ?? null
          const pending = leg.result.toUpperCase() === 'PENDING'
          return (
            <li key={`${leg.ordinal}-${leg.game_id}`} data-testid={`ticket-leg-${leg.ordinal}`}>
              <div className="ticket__leg-head">
                <b>{leg.selection}</b>
                <span className="bx-num">{american(leg.american_odds)}</span>
                <span className={`ticket__leg-status ${resultTone(leg.result)}`}>
                  {pending ? 'not settled' : leg.result.toLowerCase()}
                </span>
              </div>
              <GameScoreboard game={game} compact onRefresh={onRefresh} />
              {/* The difference a reader most needs: the game is over, the leg
                  is not yet settled. Stated, not smoothed over. */}
              {game?.is_final && pending && (
                <p className="ticket__leg-note">
                  This game reads final; settlement confirmation is still pending.
                </p>
              )}
              {leg.settled_note && <p className="ticket__leg-note">{leg.settled_note}</p>}
            </li>
          )
        })}
      </ol>

      <p className="ticket__note">
        {ticket.settlement_note
          ?? 'Leg progress is provisional. A leg leading is not a leg won, and a '
            + 'ticket settles only when every leg has.'}
      </p>
    </article>
  )
}

/** The slate a ticket belongs to, in Eastern time, so an older ticket never reads as today's. */
export const ticketDay = (iso: string | null | undefined) => (iso
  ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(iso))
  : 'date not recorded')

export function RecordedTickets({
  tickets,
}: {
  tickets: RecordedParlayTicket[]
}) {
  // One read covering every leg of every ticket on the page.
  const gameIds = tickets.flatMap((ticket) => ticket.legs.map((leg) => leg.game_id))
  const scoreboard = useScoreboard({ gameIds, enabled: gameIds.length > 0 })

  if (tickets.length === 0) {
    return (
      <p className="parlay__empty" data-testid="recorded-tickets-empty">
        No parlay ticket has been recorded yet.
      </p>
    )
  }

  return (
    <section className="ticket__list" data-testid="recorded-tickets">
      {tickets.map((ticket) => (
        <Ticket
          key={ticket.id}
          ticket={ticket}
          games={scoreboard.games}
          onRefresh={scoreboard.refresh}
        />
      ))}
    </section>
  )
}
