/**
 * MLB Over/Under: what V18 thinks, beside what a book is offering.
 *
 * THE THREE THINGS THIS LAYOUT REFUSES TO BLUR
 * --------------------------------------------
 *  - A model probability and a price. They sit in different columns, the price
 *    column names the book and the age of the quote, and the estimate derived
 *    from both is explicitly labelled model-implied.
 *  - A quoted line and a line the reader typed. A scenario is badged as one and
 *    carries no price at all, because there is no price to carry. It is still
 *    useful: it is how you interrogate the distribution when no book quote is
 *    held, which is the current state of MLB totals.
 *  - Missing and zero. A game with no stored line says so and names what is
 *    missing. Nothing renders 0.0 to fill a gap.
 *
 * THE OVERS-ONLY FILTER
 * ---------------------
 * It hides Under rows. That is all it does. It does not reorder by value,
 * recompute anything, or mark Overs as preferable, and the banner says so in
 * words rather than leaving the reader to infer it from an empty column.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, Info, MinusCircle } from 'lucide-react'
import type { TotalsBoard, TotalsRow, TotalsSelection } from '../../types/totals'

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : `${value.toFixed(digits)}%`
}

function american(value: number | null | undefined): string {
  if (value == null) return '—'
  return value > 0 ? `+${value}` : `${value}`
}

/** A signed per-unit return. Two decimals of a dollar on a one-dollar stake. */
function signedUnit(value: number | null | undefined): string {
  if (value == null) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(3)}`
}

function ageLabel(minutes: number | null | undefined): string {
  if (minutes == null) return 'age unknown'
  if (minutes < 60) return `${Math.round(minutes)}m old`
  const hours = minutes / 60
  if (hours < 48) return `${hours.toFixed(1)}h old`
  return `${Math.round(hours / 24)}d old`
}

function clock(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString([], {
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

/** Why this game cannot be priced, in the reader's language. */
const REASONS: Record<string, string> = {
  NO_V18_FORECAST: 'No V18 forecast exists for this game.',
  V18_NOT_CURRENT:
    'V18 is not the current forecast for this game, so no total is priced. '
    + 'Gen 1 is not substituted — it has no comparable run distribution.',
  NO_TOTAL_RUNS_DISTRIBUTION:
    'V18 is current, but its stored total-runs distribution is absent or unusable.',
  NO_RECORDED_TOTAL_LINE: 'No sportsbook total is recorded for this game.',
}

function SelectionRow({ selection }: { selection: TotalsSelection }) {
  const price = selection.price
  return (
    <tr data-testid={`totals-selection-${selection.side}`}>
      <th scope="row">
        <span className="totals__side">{selection.side}</span>
        <span className="totals__line">{selection.line.toFixed(1)}</span>
        {selection.line_source === 'USER_SELECTED_SCENARIO' && (
          <span className="totals__badge totals__badge--scenario">Scenario</span>
        )}
      </th>
      <td>{pct(selection.win_probability_pct)}</td>
      <td>
        {selection.push_possible
          ? pct(selection.push_probability_pct)
          : <span className="totals__muted" title="A half-run line cannot land on the number">n/a</span>}
      </td>
      <td>{pct(selection.loss_probability_pct)}</td>
      <td>{american(selection.model_fair_american_odds)}</td>
      <td>
        {price
          ? (
            <>
              <b>{american(price.american_odds)}</b>
              <small>
                {price.sportsbook} · {ageLabel(price.quote_age_minutes)}
                {price.stale && ' · stale'}
              </small>
            </>
          )
          : <span className="totals__muted">No price recorded</span>}
      </td>
      <td className={price ? undefined : 'totals__muted'}>
        {price
          ? (
            <>
              <b>{signedUnit(price.model_implied_net_return_per_unit)}</b>
              <small>per $1, model-implied</small>
            </>
          )
          : '—'}
      </td>
    </tr>
  )
}

function GameCard({
  row, onScenario, scenario,
}: {
  row: TotalsRow
  scenario: string
  onScenario: (gameId: string, line: string) => void
}) {
  const { forecast, market } = row
  return (
    <article className="totals__game" data-testid={`totals-game-${row.game_id}`}>
      <header className="totals__head">
        <div>
          <h3>{row.matchup}</h3>
          <p className="totals__when">{clock(row.starts_at)}</p>
        </div>
        <dl className="totals__model">
          <div>
            <dt>V18 expected total</dt>
            <dd data-testid="totals-expected">
              {forecast.expected_total_runs != null
                ? forecast.expected_total_runs.toFixed(2)
                : '—'}
              {forecast.simulation_count != null && (
                <small> from {forecast.simulation_count.toLocaleString()} sims</small>
              )}
            </dd>
          </div>
          <div>
            {/* The heading follows the line's provenance. Leaving it as
                "Sportsbook total" over a line the reader typed would attribute
                their number to a book that never quoted it -- the exact
                confusion the scenario feature exists to avoid. */}
            <dt>
              {market.line_source === 'USER_SELECTED_SCENARIO'
                ? 'Your scenario line'
                : 'Sportsbook total'}
            </dt>
            <dd data-testid="totals-line">
              {market.line != null ? market.line.toFixed(1) : 'Not recorded'}
              {market.line_source === 'RECORDED_SPORTSBOOK_LINE'
                && market.observed_at && (
                <small> · {ageLabel(market.quote_age_minutes)}</small>
              )}
              {market.line_source === 'USER_SELECTED_SCENARIO' && (
                <small> · not a quote</small>
              )}
            </dd>
          </div>
        </dl>
      </header>

      {forecast.unavailable_reason && (
        <p className="totals__unavailable" role="note">
          <AlertTriangle size={13} aria-hidden="true" />{' '}
          {REASONS[forecast.unavailable_reason] ?? forecast.unavailable_reason}
          {forecast.availability_detail && ` ${forecast.availability_detail}`}
        </p>
      )}

      {market.line_note && (
        <p className="totals__scenario-note" role="note">
          <Info size={13} aria-hidden="true" /> {market.line_note}
        </p>
      )}

      {row.selections.length > 0 ? (
        <table className="totals__table">
          <caption className="sr-only">
            Model probabilities and offered prices for {row.matchup}
          </caption>
          <thead>
            <tr>
              <th scope="col">Selection</th>
              <th scope="col">Win %</th>
              <th scope="col">Push %</th>
              <th scope="col">Lose %</th>
              <th scope="col">Model fair</th>
              <th scope="col">Offered</th>
              <th scope="col">Est. return</th>
            </tr>
          </thead>
          <tbody>
            {row.selections.map((selection) => (
              <SelectionRow key={selection.side} selection={selection} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="totals__empty">
          {market.unavailable_reason
            ? REASONS[market.unavailable_reason] ?? market.unavailable_reason
            : 'Nothing to price for this game.'}
          {forecast.distribution_available && market.line == null && (
            ' The model is ready — enter a line below to see its probabilities as a scenario.'
          )}
        </p>
      )}

      {forecast.distribution_available && (
        <label className="totals__scenario">
          <span>Try a line</span>
          <input
            type="number" step="0.5" min="0" max="30"
            placeholder="e.g. 8.5"
            value={scenario}
            aria-label={`Exploratory total for ${row.matchup}`}
            onChange={(event) => onScenario(row.game_id, event.target.value)}
          />
          <small>Labelled a scenario, not a quote. No price attached.</small>
        </label>
      )}

      {row.hidden_by_filter && row.hidden_by_filter.length > 0 && (
        <p className="totals__hidden">
          {row.hidden_by_filter.join(', ')} hidden by the Overs-only filter.
          Its probabilities are unchanged.
        </p>
      )}
    </article>
  )
}

export function MlbTotalsView({
  board, oversOnly, onOversOnly, scenarios, onScenario,
}: {
  board: TotalsBoard | null
  oversOnly: boolean
  onOversOnly: (next: boolean) => void
  scenarios: Record<string, string>
  onScenario: (gameId: string, line: string) => void
}) {
  if (!board) return <p className="totals__empty">Totals unavailable.</p>
  const { coverage, market_availability: availability } = board
  const noLines = coverage.games_with_recorded_line === 0

  return (
    <section className="totals" data-testid="mlb-totals">
      <div className="totals__bar">
        <label className="totals__filter">
          <input
            type="checkbox"
            checked={oversOnly}
            onChange={(event) => onOversOnly(event.target.checked)}
            data-testid="overs-only-toggle"
          />
          <span>Overs only</span>
        </label>
        <p className="totals__coverage" data-testid="totals-coverage">
          {coverage.games} games · {coverage.games_with_v18_distribution} with a V18
          distribution · {coverage.games_with_recorded_line} with a recorded line ·{' '}
          {coverage.selections_with_usable_price} selections with a usable price
        </p>
      </div>

      {board.filter_note && (
        <p className="totals__filter-note" role="note" data-testid="overs-only-note">
          <MinusCircle size={13} aria-hidden="true" /> {board.filter_note}
        </p>
      )}

      {noLines && (
        <p className="totals__gap" role="note" data-testid="totals-market-gap">
          <AlertTriangle size={13} aria-hidden="true" />{' '}
          No sportsbook total is recorded for any game on this slate, so no Over or
          Under price can be shown. {availability.detail} The model side below is
          current and can still be interrogated with a scenario line.
        </p>
      )}

      <div className="totals__list">
        {board.rows.map((row) => (
          <GameCard
            key={row.game_id}
            row={row}
            scenario={scenarios[row.game_id] ?? ''}
            onScenario={onScenario}
          />
        ))}
      </div>

      {board.rows.length === 0 && (
        <p className="totals__empty">No MLB games are scheduled for this date.</p>
      )}
    </section>
  )
}
