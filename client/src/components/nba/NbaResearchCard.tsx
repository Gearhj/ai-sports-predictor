/**
 * One imported NBA research forecast.
 *
 * TWO MODELS, NEVER BLENDED
 * -------------------------
 * Ridge expected points and the Elo win probability come from different models.
 * The card labels each and never implies a shared simulation: a reader who
 * assumes the probability came from the points would read a joint distribution
 * that was never computed, and the two are allowed to favour different sides.
 *
 * WHAT IT WILL NOT DRAW
 * ---------------------
 * No distribution, no interval, no Over/Under. `drawCount` is zero and the
 * package says those are unavailable — deriving one from the 225.72-point
 * expected total would invent the thing the handoff forbids. The card states
 * they are unavailable rather than leaving a blank that reads as a chart which
 * failed to load.
 */
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { NbaResearchForecast } from '../../types/nbaResearch'

function pct(fraction: number | null | undefined): string {
  // The single multiplication by 100 in the whole path.
  if (fraction == null) return '—'
  return `${(fraction * 100).toFixed(2)}%`
}

function points(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

export function NbaResearchCard({ forecast }: { forecast: NbaResearchForecast }) {
  const [open, setOpen] = useState(false)
  const away = forecast.teams.away?.abbreviation ?? 'Away'
  const home = forecast.teams.home?.abbreviation ?? 'Home'
  const captured = new Date(forecast.captured_schedule.tipoff_utc)

  return (
    <article className="nbares" data-testid={`nba-research-${forecast.forecast_id}`}>
      <header className="nbares__head">
        <span className="nbares__label" data-testid="nba-research-label">
          {forecast.label ?? 'Early research forecast'}
        </span>
        <h3>{away} at {home}</h3>
        <small className="bx-dim" data-testid="nba-captured-schedule">
          Captured schedule:{' '}
          {captured.toLocaleString(undefined, {
            dateStyle: 'long', timeStyle: 'short', timeZone: 'UTC',
          })} UTC
        </small>
      </header>

      <div className="nbares__outputs">
        <section data-testid="nba-ridge">
          <h4>Ridge expected points</h4>
          <p className="nbares__numbers">
            <span>{away} <b className="bx-num">{points(forecast.ridge_expected_points.away)}</b></span>
            <span>{home} <b className="bx-num">{points(forecast.ridge_expected_points.home)}</b></span>
          </p>
          <small className="bx-dim">
            {forecast.ridge_expected_points.model} · {forecast.ridge_expected_points.units}
          </small>
        </section>

        <section data-testid="nba-elo">
          <h4>Elo win probability</h4>
          <p className="nbares__numbers">
            <span>{away} <b className="bx-num">{pct(forecast.elo_win_probability.away)}</b></span>
            <span>{home} <b className="bx-num">{pct(forecast.elo_win_probability.home)}</b></span>
          </p>
          <small className="bx-dim">
            A separate model output — not derived from the expected points, and
            the two may favour different sides.
          </small>
        </section>
      </div>

      <p className="nbares__unavailable" data-testid="nba-unavailable">
        {forecast.distributions.note}
      </p>

      {forecast.current_schedule.differs_from_captured && (
        <p className="nbares__note" role="note" data-testid="nba-schedule-revision">
          AISP now holds a different scheduled tip-off for this game. Both are
          kept: the forecast's captured schedule is shown above and is not
          rewritten.
        </p>
      )}

      <div className="nbares__more">
        <button
          type="button" className="nbares__toggle" aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          data-testid="nba-research-toggle"
        >
          <span>Limitations and inputs</span>
          <ChevronDown size={13} aria-hidden="true"
            className={open ? 'nbares__chev is-open' : 'nbares__chev'} />
        </button>
        {open && (
          <div className="nbares__panel" data-testid="nba-research-detail">
            <p className="nbares__note">
              <b>Rest support.</b> Both clubs' raw 190-day gaps were marked
              SEASON_OPENER_NO_ORDINARY_REST. Treated rest and rest difference
              are null, and the rest-known and back-to-back indicators are zero.
              These are not claims of 190 days of physical rest.
            </p>
            <p className="nbares__note">
              <b>Missing inputs.</b> Player availability and strength, starters,
              lineups and injuries were not available to this forecast.
            </p>
            <p className="nbares__note">
              <b>Training limitations.</b> Historical training revisions bound
              what this model could learn. The original source and model
              bindings travel with the forecast and are unchanged.
            </p>
            <dl className="nbares__facts">
              <div><dt>Forecast</dt><dd><code>{forecast.forecast_id}</code></dd></div>
              <div><dt>Engine</dt><dd>{forecast.engine_id}</dd></div>
              <div><dt>Configuration</dt><dd>{forecast.configuration_identity}</dd></div>
              <div><dt>Source cutoff</dt><dd>{forecast.clocks.source_cutoff_utc}</dd></div>
              <div><dt>Forecast created</dt><dd>{forecast.clocks.forecast_created_utc}</dd></div>
              <div><dt>Delivered to AISP</dt><dd>{forecast.clocks.exchange_delivered_utc}</dd></div>
              <div>
                <dt>Producer published</dt>
                <dd>
                  {forecast.clocks.producer_published_utc
                    ?? <span className="bx-dim">
                         null — the producer had not published locally
                       </span>}
                </dd>
              </div>
              <div><dt>ESPN game</dt><dd>{forecast.source_game_id}</dd></div>
            </dl>
            <p className="nbares__note">
              Research only. This forecast creates no account, wager, bankroll,
              staking policy or scheduler entry.
            </p>
          </div>
        )}
      </div>
    </article>
  )
}
