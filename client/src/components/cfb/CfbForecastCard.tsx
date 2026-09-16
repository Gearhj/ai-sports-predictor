/**
 * The College Football research card.
 *
 * WHAT LEADS
 * ----------
 * The pick, then the projected score, then the three probabilities. A reader
 * who stops after two seconds should leave with "Pittsburgh, 32–20" and not
 * with a number they mistake for a betting edge.
 *
 * WHY THE 80% RANGE SITS NEXT TO THE 76.92%
 * -----------------------------------------
 * Because a win probability alone reads as confidence about the score. The
 * middle 80% of these simulations runs from Syracuse winning by 10 to
 * Pittsburgh winning by 34 — a spread wide enough that a reader who saw only
 * "76.92%" would be badly calibrated about what the model actually claims.
 * Putting them side by side is the honest presentation.
 *
 * THREE PROBABILITIES, NEVER ONE
 * ------------------------------
 * Ridge, Elo and the joint simulation are separate methods that disagree by
 * nearly ten points here. Each is labelled by the method that produced it.
 * Nothing averages them and nothing calls them a consensus. The distribution
 * section names the joint simulation as its basis out loud, because silently
 * picking one is how three numbers become one that nobody can source.
 *
 * NO PRICE EXISTS
 * ---------------
 * The total scenarios are illustrative. AISP was offered no College Football
 * market, so there is no expected return, no offered odds and no wager here —
 * and the label says so on every row rather than once at the bottom.
 */
import { useState } from 'react'
import type { CfbResearchForecast } from '../../types/cfbResearch'
import { CfbDistribution } from './CfbDistribution'
import { CfbTotalScenarioInput } from './CfbTotalScenarioInput'

/** The single multiplication by 100 on this path. */
function pct(fraction: number): string {
  return `${(fraction * 100).toFixed(2)}%`
}

function points(value: number): string {
  return value.toFixed(2)
}

function kickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  })
}

function band(forecast: CfbResearchForecast, metric: string, level = '80') {
  return forecast.intervals?.[metric]?.[level] ?? null
}

export function CfbForecastCard({ forecast }: { forecast: CfbResearchForecast }) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const { game, expected, pick } = forecast
  const primary = forecast.win_probabilities.find((p) => p.primary)
    ?? forecast.win_probabilities[0]
  const favouredIsHome = pick.team === game.home_team

  return (
    <article className="cfbcard" data-testid="cfb-forecast-card">
      <header className="cfbcard__head">
        <div>
          <span className="cfbcard__eyebrow">{forecast.engine_label}</span>
          <h3 className="cfbcard__matchup">{game.away_team} at {game.home_team}</h3>
          <p className="cfbcard__kick">{kickoff(game.kickoff_utc)}</p>
        </div>
        <span className="cfbcard__badge" data-testid="cfb-research-badge">
          {forecast.authority.status}
        </span>
      </header>

      {(forecast.horizon_label || forecast.canonical_game || forecast.paper_eligibility) && (
        <ul className="cfbcard__status" data-testid="cfb-status">
          {forecast.horizon_label && <li data-testid="cfb-horizon">{forecast.horizon_label}</li>}
          {forecast.canonical_game && <li data-testid="cfb-binding">{forecast.canonical_game.label}</li>}
          {forecast.paper_eligibility && <li data-testid="cfb-eligibility">{forecast.paper_eligibility.label}</li>}
        </ul>
      )}

      {/* The pick. A forecast, and labelled as one. */}
      <p className="cfbcard__pick" data-testid="cfb-pick">{pick.label}</p>

      <div className="cfbcard__score" data-testid="cfb-expected-score">
        <div className={favouredIsHome ? 'cfbcard__team' : 'cfbcard__team cfbcard__team--lead'}>
          <span className="cfbcard__teamname">{game.away_team}</span>
          <b className="bx-num">{points(expected.away_points)}</b>
        </div>
        <div className={favouredIsHome ? 'cfbcard__team cfbcard__team--lead' : 'cfbcard__team'}>
          <span className="cfbcard__teamname">{game.home_team}</span>
          <b className="bx-num">{points(expected.home_points)}</b>
        </div>
      </div>

      <dl className="cfbcard__figures">
        <div><dt>Expected total</dt><dd className="bx-num">{points(expected.total)}</dd></div>
        <div>
          <dt>Expected {game.home_team} margin</dt>
          <dd className="bx-num">{expected.home_margin > 0 ? '+' : ''}{points(expected.home_margin)}</dd>
        </div>
        <div>
          <dt>Final tie probability</dt>
          <dd className="bx-num">{pct(forecast.tie_probability)}</dd>
        </div>
      </dl>

      {/* Three methods. Three rows. Never averaged. */}
      <section className="cfbcard__probs" data-testid="cfb-probabilities">
        <h4>Win probability by method</h4>
        <ul>
          {forecast.win_probabilities.map((entry) => (
            <li key={entry.method} data-testid={`cfb-prob-${entry.method}`}>
              <span className="cfbcard__method">
                {entry.label}
                {entry.primary && <em className="cfbcard__primarytag">primary</em>}
              </span>
              <span className="bx-num">
                {game.home_team} {pct(entry.home)}
              </span>
              <span className="cfbcard__alt bx-num">
                {game.away_team} {pct(entry.away)}
              </span>
              {entry.meaning && <small className="cfbcard__meaning">{entry.meaning}</small>}
            </li>
          ))}
        </ul>
        <p className="cfbcard__note" data-testid="cfb-method-note">
          {forecast.primary_method_note}
        </p>
      </section>

      {/* The number that keeps the probability honest. */}
      {forecast.margin_sentence && (
        <p className="cfbcard__range" data-testid="cfb-margin-range">
          {forecast.margin_sentence}
        </p>
      )}

      <section className="cfbcard__intervals" data-testid="cfb-intervals">
        <h4>Retained 80% intervals</h4>
        <table>
          <thead>
            <tr><th>Metric</th><th>Lower</th><th>Upper</th></tr>
          </thead>
          <tbody>
            {([
              [game.away_team, 'away_score'],
              [game.home_team, 'home_score'],
              ['Total', 'total'],
              [`${game.home_team} margin`, 'home_margin'],
            ] as const).map(([label, metric]) => {
              const range = band(forecast, metric)
              return range ? (
                <tr key={metric} data-testid={`cfb-interval-${metric}`}>
                  <td>{label}</td>
                  <td className="bx-num">{range.lower}</td>
                  <td className="bx-num">{range.upper}</td>
                </tr>
              ) : null
            })}
          </tbody>
        </table>
      </section>

      <CfbDistribution forecast={forecast} />

      <p className="cfbcard__precision" data-testid="cfb-precision">
        {forecast.simulation_precision_note}
      </p>

      <CfbTotalScenarioInput forecast={forecast} />

      {/* Collapsed, because it is context rather than the headline — but it is
          the part that stops the headline being over-read. */}
      <section className="cfbcard__disclosure">
        <button
          type="button"
          className="cfbcard__toggle"
          aria-expanded={detailsOpen}
          onClick={() => setDetailsOpen((open) => !open)}
          data-testid="cfb-disclosure-toggle"
        >
          {detailsOpen ? 'Hide' : 'Show'} “Why {pick.team}?” and limitations
        </button>
        <div hidden={!detailsOpen} data-testid="cfb-disclosure-body">
          <ul className="cfbcard__bullets">
            {forecast.disclosure.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </div>
      </section>

      <section className="cfbcard__authority" data-testid="cfb-authority">
        <span>{forecast.authority.status}</span>
        <span>{forecast.authority.bankroll}</span>
        <span>{forecast.authority.policy}</span>
        <span>{forecast.authority.market}</span>
      </section>

      <section className="cfbcard__technical">
        <button
          type="button"
          className="cfbcard__toggle"
          aria-expanded={technicalOpen}
          onClick={() => setTechnicalOpen((open) => !open)}
          data-testid="cfb-technical-toggle"
        >
          {technicalOpen ? 'Hide' : 'Show'} technical details
        </button>
        <dl hidden={!technicalOpen} className="cfbcard__tech" data-testid="cfb-technical-body">
          <div><dt>Forecast</dt><dd className="cfbcard__mono">{forecast.forecast_id}</dd></div>
          <div><dt>Forecast digest</dt><dd className="cfbcard__mono">{forecast.forecast_sha256}</dd></div>
          <div><dt>Package index</dt><dd className="cfbcard__mono">{forecast.package_index_sha256}</dd></div>
          <div><dt>Configuration</dt><dd className="cfbcard__mono">{forecast.baseline_configuration}</dd></div>
          <div><dt>{game.source_provider} game id</dt><dd className="cfbcard__mono">{game.source_game_id}</dd></div>
          <div><dt>Source cutoff</dt><dd>{forecast.clocks.source_cutoff_utc}</dd></div>
          <div><dt>Forecast created</dt><dd>{forecast.clocks.forecast_created_utc}</dd></div>
          <div><dt>Exchange published</dt><dd>{forecast.clocks.exchange_published_utc ?? '—'}</dd></div>
          <div><dt>Imported by AISP</dt><dd>{forecast.clocks.imported_at_utc}</dd></div>
        </dl>
      </section>

      <footer className="cfbcard__foot">
        <small>{forecast.attribution}</small>
        <small>{forecast.raw_source_policy}</small>
      </footer>
    </article>
  )
}
