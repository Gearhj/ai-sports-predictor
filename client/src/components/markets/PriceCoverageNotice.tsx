/**
 * Why the prices are not here — per game, and for the slate as a whole.
 *
 * WHAT THIS REPLACES
 * ------------------
 * Ten games rendering an identical blank, which reads as "the sportsbooks have
 * not posted these markets". On 2026-09-14 that reading was false in every
 * case: eighteen NFL objects had been booked to the MLB lane the previous
 * evening, so the collector was refused all day and never asked a sportsbook
 * anything at all.
 *
 * THE ONE CLAIM IT WILL NOT MAKE
 * ------------------------------
 * That a market was not offered. AISP can only observe what it requested and
 * received. Only PROVIDER_RETURNED_NO_FULL_GAME_MARKET says anything about a
 * sportsbook, and it is the one reason backed by a returned event.
 */
import type { PriceCoverage, PriceReason } from '../../types/priceCoverage'

const LABEL: Record<PriceReason, string> = {
  PRICED: 'Priced',
  COLLECTION_WINDOW_NOT_OPEN: 'Collection window not open',
  DAILY_PROVIDER_ALLOCATION_EXHAUSTED: 'Daily provider allocation exhausted',
  EVENT_NOT_SELECTED_UNDER_TODAYS_ALLOCATION: 'Not selected under today’s allocation',
  PROVIDER_RETURNED_NO_FULL_GAME_MARKET: 'Provider returned no full-game market',
  INCOMPLETE_TWO_SIDED_QUOTE: 'Incomplete two-sided quote',
  IDENTITY_MAPPING_FAILED: 'Identity mapping failed',
  QUOTE_EXPIRED: 'Quote expired',
  INTAKE_FAILURE: 'Intake failure',
}

/** Only one of these is a statement about a sportsbook. */
const TONE: Record<PriceReason, string> = {
  PRICED: 'pricecov__tag--good',
  COLLECTION_WINDOW_NOT_OPEN: 'pricecov__tag--wait',
  DAILY_PROVIDER_ALLOCATION_EXHAUSTED: 'pricecov__tag--warn',
  EVENT_NOT_SELECTED_UNDER_TODAYS_ALLOCATION: 'pricecov__tag--warn',
  PROVIDER_RETURNED_NO_FULL_GAME_MARKET: 'pricecov__tag--neutral',
  INCOMPLETE_TWO_SIDED_QUOTE: 'pricecov__tag--warn',
  IDENTITY_MAPPING_FAILED: 'pricecov__tag--bad',
  QUOTE_EXPIRED: 'pricecov__tag--wait',
  INTAKE_FAILURE: 'pricecov__tag--bad',
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

export function PriceCoverageNotice({ coverage }: { coverage: PriceCoverage | null }) {
  if (!coverage || coverage.games.length === 0) return null
  const missing = coverage.games.filter((game) => game.reason !== 'PRICED')

  return (
    <div className="pricecov" data-testid="price-coverage">
      <p className="pricecov__headline" data-testid="price-coverage-headline">
        {coverage.summary.headline}
      </p>
      <small className="bx-dim">{coverage.window.description}</small>

      {coverage.lane.objects_misattributed_today > 0 && (
        <p className="pricecov__flag" role="note" data-testid="price-coverage-flag">
          {coverage.lane.objects_misattributed_today} object
          {coverage.lane.objects_misattributed_today === 1 ? '' : 's'} charged to
          the MLB lane today were requested for another league
          ({coverage.lane.misattributed_requests_today} request
          {coverage.lane.misattributed_requests_today === 1 ? '' : 's'}). They are
          excluded from this lane’s daily count; they still count against the
          monthly balance, because they were genuinely spent.
        </p>
      )}

      {missing.length > 0 && (
        <ul className="pricecov__list">
          {missing.map((game) => (
            <li key={game.game_id} data-testid={`price-reason-${game.game_id}`}>
              <span className="pricecov__game">
                <b>{game.teams.away ?? '—'} @ {game.teams.home ?? '—'}</b>
                <small>{clock(game.starts_at)}</small>
              </span>
              <span className={`pricecov__tag ${TONE[game.reason]}`}>
                {LABEL[game.reason]}
              </span>
              <span className="pricecov__why">{game.explanation}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
