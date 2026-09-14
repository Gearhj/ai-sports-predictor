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
import type { ForecastState, PriceCoverage, PriceReason } from '../../types/priceCoverage'

/** Forecast coverage is reported BESIDE price coverage, never merged into it. */
const FORECAST_LABEL: Record<ForecastState, string> = {
  FORECAST_PRESENT: 'V18 forecast',
  FORECAST_NOT_YET_PUBLISHED: 'V18 not yet published',
  FORECAST_AWAITING_IMPORT: 'V18 awaiting import',
  FORECAST_CUTOFF_PASSED_UNSUPPORTED: 'V18 cutoff passed',
}

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

      {/* The slate at a glance. Prices and forecasts are counted separately
          because they fail for unrelated reasons: a game can have a price and
          no forecast, or the reverse, and each needs a different person. */}
      <dl className="pricecov__stats" data-testid="price-coverage-stats">
        <div><dt>Games</dt><dd>{coverage.summary.games}</dd></div>
        <div><dt>Any stored price</dt><dd>{coverage.summary.with_any_stored_price}</dd></div>
        <div><dt>Decision-fresh pair</dt><dd>{coverage.summary.with_decision_fresh_pair}</dd></div>
        <div><dt>Scheduled to collect</dt><dd>{coverage.summary.scheduled_for_collection}</dd></div>
        <div><dt>Excluded by capacity</dt><dd>{coverage.summary.excluded_by_capacity}</dd></div>
        <div><dt>Controlling forecast</dt><dd>{coverage.summary.with_controlling_forecast}</dd></div>
        <div>
          <dt>Service-day allowance</dt>
          <dd>{coverage.lane.spent_today} used · {coverage.lane.room} left of {coverage.lane.daily_allowance}</dd>
        </div>
        <div>
          <dt>Next collection</dt>
          <dd>{coverage.summary.next_collection_at
            ? clock(coverage.summary.next_collection_at) : 'none scheduled'}</dd>
        </div>
      </dl>

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
              <span className="pricecov__why">
                {game.reason === 'COLLECTION_WINDOW_NOT_OPEN'
                  ? `Collection scheduled for ${clock(game.window_opens_at)}.`
                  : game.explanation}
                {' '}
                <span className="bx-dim">· {FORECAST_LABEL[game.forecast_state]}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
