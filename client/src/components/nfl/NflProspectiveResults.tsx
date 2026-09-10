/**
 * NFL prospective results — what the forecast said, and what happened.
 *
 * THE THING THIS SECTION EXISTS TO SHOW
 * ------------------------------------
 * Not "the model is good". With one game that claim is unavailable at any
 * price. What one game CAN show is exactly where the forecast was right and
 * exactly where it was wrong, which is a different and more useful thing: the
 * first NE @ SEA result got the winner right and the margin nearly exact while
 * missing the scoring level by nineteen points, and those are three separate
 * findings that a single accuracy percentage would have flattened into one.
 *
 * SO THE LAYOUT IS ERROR-FIRST
 * ----------------------------
 * Each game shows predicted against actual for the score, the margin and the
 * total side by side, with the error drawn to scale. A reader should be able
 * to see the margin bar sitting on zero and the total bar running off to the
 * right without reading a number.
 *
 * WHAT IT REFUSES TO DO
 * ---------------------
 *  - pool models: every aggregate belongs to one engine and version, and n
 *    sits beside it, because a mean over one game is a fact about one game;
 *  - call an operator-entered final provider-verified;
 *  - show a market comparison that does not exist. NE @ SEA has no persisted
 *    pre-kickoff quote at all, and the honest state says so rather than
 *    reconstructing a price from a model probability.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, Target, XCircle } from 'lucide-react'
import type { NflProspectiveResults, ProspectiveGame, ProspectiveModel } from '../../types/nflProspective'

const number = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)

const signed = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}`

const percent = (value: number | null | undefined, digits = 1): string =>
  value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(digits)}%`

const stamp = (iso: string | null): string => {
  if (!iso) return 'not recorded'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'not recorded'
    : new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
    }).format(date)
}

export function NflProspectiveSection({ data }: { data: NflProspectiveResults }) {
  const [engine, setEngine] = useState<string | null>(null)
  const model = useMemo<ProspectiveModel | null>(() => {
    if (data.models.length === 0) return null
    return data.models.find((entry) => entry.engine_id === engine) ?? data.models[0]
  }, [data.models, engine])

  if (!model) {
    return (
      <section className="np" aria-label="NFL prospective results">
        <header className="np__head">
          <span className="eyebrow">NFL · PROSPECTIVE RESULTS</span>
          <h3>Forecasts scored against final results</h3>
        </header>
        <p className="np__empty">{data.requirement}</p>
        <Inventory data={data} />
      </section>
    )
  }

  const board = model.scoreboard
  const n = board.scored_games

  return (
    <section className="np" aria-label="NFL prospective results">
      <header className="np__head">
        <div>
          <span className="eyebrow">NFL · PROSPECTIVE RESULTS</span>
          <h3>Forecasts scored against final results</h3>
          <p className="np__lede">
            Every score below comes from the forecast that was published <strong>before</strong>{' '}
            kickoff, frozen at that moment and compared with the final. Research only — NFL
            creates no position and holds no wagering authority.
          </p>
        </div>
        {data.models.length > 1 && (
          <label className="np__selector">
            Model
            <select value={model.engine_id} onChange={(event) => setEngine(event.target.value)}>
              {data.models.map((entry) => (
                <option key={entry.engine_id} value={entry.engine_id}>
                  {entry.model_label} · {entry.scored_games} scored
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      <p className="np__sample">
        <strong>n = {n}</strong> scored game{n === 1 ? '' : 's'} for {model.model_label}.{' '}
        {data.sample_note} The first formal review comes at{' '}
        {data.review_checkpoint_games} settled selections per sport; these are shown from the
        first game because hiding evidence until then would be its own distortion.
      </p>

      <div className="np__scoreboard">
        <Stat label="Scored games" value={String(board.scored_games)} />
        <Stat label="Correct winners"
          value={`${board.correct_winners} of ${board.scored_games}`}
          sub={percent(board.winner_accuracy, 0)} />
        <Stat label="Avg log loss" value={number(board.average_log_loss, 4)} hint="lower is better" />
        <Stat label="Avg Brier" value={number(board.average_brier_score, 4)} hint="three-outcome, lower is better" />
        <Stat label="Margin MAE" value={number(board.margin_mae, 2)} sub="points" />
        <Stat label="Total MAE" value={number(board.total_mae, 2)} sub="points" />
        <Stat label="Team-score MAE" value={number(board.team_score_mae, 3)} sub="points per team" />
        <Stat label="Market comparisons"
          value={`${board.market_comparison_sample} of ${board.scored_games}`}
          sub={board.market_comparison_sample === 0 ? 'no comparable quote' : 'games with a quote'} />
      </div>

      {board.distribution_sample > 0 && (
        <div className="np__distribution">
          <h4>Distribution scoring <small>{board.distribution_sample} game{board.distribution_sample === 1 ? '' : 's'} with authentic saved draws</small></h4>
          <div className="np__scoreboard np__scoreboard--compact">
            <Stat label="CRPS margin" value={number(board.average_crps_margin, 3)} hint="lower is better" />
            <Stat label="CRPS total" value={number(board.average_crps_total, 3)} hint="lower is better" />
            <Stat label="Energy score" value={number(board.average_energy_score, 3)} hint="joint, lower is better" />
            {board.interval_coverage && (['50', '80', '90'] as const).map((level) => (
              <Stat key={level} label={`${level}% margin interval`}
                value={`${board.interval_coverage!.margin[level].contained} of ${board.interval_coverage!.margin[level].of}`}
                sub={`total ${board.interval_coverage!.total[level].contained} of ${board.interval_coverage!.total[level].of}`} />
            ))}
          </div>
          {board.interval_coverage?.note && <p className="np__note">{board.interval_coverage.note}</p>}
        </div>
      )}

      <div className="np__games">
        {model.games.map((game) => <GameCard key={game.evaluation_id} game={game} />)}
      </div>

      <Inventory data={data} />
    </section>
  )
}

function Stat({ label, value, sub, hint }: { label: string; value: string; sub?: string; hint?: string }) {
  return (
    <div className="np-stat">
      <small>{label}</small>
      <strong>{value}</strong>
      {sub && <span>{sub}</span>}
      {hint && <em>{hint}</em>}
    </div>
  )
}

function GameCard({ game }: { game: ProspectiveGame }) {
  const [open, setOpen] = useState(false)
  const away = game.away?.abbreviation ?? 'AWAY'
  const home = game.home?.abbreviation ?? 'HOME'
  const correct = game.outcome.winner_correct
  return (
    <article className={`np-game${correct ? ' np-game--correct' : ' np-game--wrong'}`}
      aria-label={`${away} at ${home} result`}>
      <header className="np-game__head">
        <div className="np-game__matchup">
          <span className="np-game__teams">
            {game.away?.name ?? away} <i>at</i> {game.home?.name ?? home}
          </span>
          <span className="np-game__final">
            {away} <b>{game.final.away}</b> · {home} <b>{game.final.home}</b>
          </span>
        </div>
        <span className={`np-game__verdict${correct ? ' is-correct' : ' is-wrong'}`}>
          {correct ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
          {correct ? 'Winner correct' : 'Winner wrong'}
        </span>
      </header>

      <p className="np-game__frozen">
        Frozen {stamp(game.forecast.frozen_published_utc)}
        {game.forecast.hours_before_kickoff != null
          ? ` · ${game.forecast.hours_before_kickoff}h before kickoff` : ''}
        {' · '}{game.forecast.engine_id} {game.forecast.snapshot_type} v{game.forecast.snapshot_version}
        {!game.result.provider_verified && (
          <span className="np-game__provenance">
            <AlertTriangle size={12} aria-hidden="true" /> final {game.result.source.toLowerCase().replace('_', ' ')}, not provider-verified
          </span>
        )}
      </p>

      <div className="np-game__errors">
        <ErrorBar label="Home score" predicted={game.scores.predicted_home}
          actual={game.final.home} error={game.scores.home_error} scale={24} unit="pts" />
        <ErrorBar label="Away score" predicted={game.scores.predicted_away}
          actual={game.final.away} error={game.scores.away_error} scale={24} unit="pts" />
        <ErrorBar label="Margin" predicted={game.margin.predicted}
          actual={game.margin.actual} error={game.margin.error} scale={24} unit="pts" />
        <ErrorBar label="Total" predicted={game.total.predicted}
          actual={game.total.actual} error={game.total.error} scale={24} unit="pts" />
      </div>

      <div className="np-game__outcome">
        <div>
          <small>Probability given to what happened</small>
          <strong>{percent(game.outcome.probability_of_actual)}</strong>
        </div>
        <div>
          <small>Log loss</small>
          <strong>{number(game.outcome.log_loss, 4)}</strong>
        </div>
        <div>
          <small>Brier</small>
          <strong>{number(game.outcome.brier_score, 4)}</strong>
        </div>
        {game.outcome.published_probabilities && (
          <div className="np-game__triple">
            <small>Published probabilities</small>
            <strong>
              {away} {percent(game.outcome.published_probabilities.away, 1)} ·{' '}
              tie {percent(game.outcome.published_probabilities.tie, 1)} ·{' '}
              {home} {percent(game.outcome.published_probabilities.home, 1)}
            </strong>
          </div>
        )}
      </div>

      {game.distribution.available ? (
        <DistributionStrip game={game} />
      ) : (
        <p className="np-game__unavailable">
          <AlertTriangle size={13} aria-hidden="true" /> Distribution scoring unavailable.{' '}
          {game.distribution.reason}
        </p>
      )}

      <p className={`np-game__market${game.market.available ? '' : ' is-unavailable'}`}>
        <Target size={13} aria-hidden="true" />
        {game.market.available
          ? 'Market comparison available — see the evidence drawer.'
          : `Market comparison unavailable. ${game.market.reason}`}
      </p>

      <details className="np-game__evidence" open={open}
        onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
        <summary>Forecast, result and excluded snapshots <ChevronDown size={13} aria-hidden="true" /></summary>
        <div className="np-game__evidence-body">
          <p><strong>Forecast identity</strong> <code>{game.forecast.identity}</code></p>
          <p><strong>Evidence timestamp</strong> {stamp(game.forecast.evidence_utc)} ·{' '}
            <strong>imported</strong> {stamp(game.forecast.imported_utc)}</p>
          <p><strong>Result identity</strong> <code>{game.result.identity}</code> ·{' '}
            observed {stamp(game.result.observed_utc)}</p>
          {game.distribution.available && (
            <p><strong>Distribution</strong> {game.distribution.draws?.toLocaleString()} saved draws ·
              {' '}CRPS home {number(game.distribution.crps_home, 3)} · away {number(game.distribution.crps_away, 3)} ·
              {' '}margin {number(game.distribution.crps_margin, 3)} · total {number(game.distribution.crps_total, 3)} ·
              {' '}energy {number(game.distribution.energy_score, 3)}</p>
          )}
          <p>
            <strong>{game.snapshots_considered ?? 0} snapshot(s) considered</strong>,{' '}
            {game.excluded_snapshots.length} excluded. Ten stored snapshots of one forecast are one
            game, not ten.
          </p>
          {game.excluded_snapshots.length > 0 && (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Snapshot</th><th scope="col">Published</th><th scope="col">Excluded because</th>
                  </tr>
                </thead>
                <tbody>
                  {game.excluded_snapshots.map((entry) => (
                    <tr key={`${entry.snapshot_type}-${entry.snapshot_version}-${entry.published_utc}`}>
                      <th scope="row">{entry.snapshot_type} v{entry.snapshot_version}</th>
                      <td>{stamp(entry.published_utc)}</td>
                      <td>{entry.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>
    </article>
  )
}

/**
 * Predicted against actual, with the error drawn to a shared scale.
 *
 * The shared scale is the point: a margin error of 0.19 and a total error of
 * 19.69 must not both fill their bar, or the picture would say the forecast
 * was equally wrong about both.
 */
function ErrorBar({
  label, predicted, actual, error, scale, unit,
}: {
  label: string; predicted: number | null; actual: number
  error: number | null; scale: number; unit: string
}) {
  const magnitude = error == null ? 0 : Math.min(Math.abs(error), scale)
  const width = (magnitude / scale) * 50
  const tooBig = error != null && Math.abs(error) > scale
  return (
    <div className="np-error">
      <div className="np-error__head">
        <small>{label}</small>
        <span>
          <b>{number(predicted, 1)}</b> predicted · <b>{actual}</b> actual
        </span>
      </div>
      <div className="np-error__track" role="img"
        aria-label={`${label}: ${error == null ? 'no error published' : `${signed(error, 2)} ${unit}`}`}>
        <span className="np-error__zero" />
        {error != null && (
          <span className={`np-error__bar${error > 0 ? ' is-over' : ' is-under'}`}
            style={{
              width: `${width}%`,
              left: error > 0 ? '50%' : `${50 - width}%`,
            }} />
        )}
        {tooBig && <span className="np-error__clip" aria-hidden="true">▸</span>}
      </div>
      <span className={`np-error__value${error != null && Math.abs(error) >= 10 ? ' is-large' : ''}`}>
        {signed(error, 2)} {unit}
        {error != null && error > 0 ? ' (over)' : error != null && error < 0 ? ' (under)' : ''}
      </span>
    </div>
  )
}

function DistributionStrip({ game }: { game: ProspectiveGame }) {
  const coverage = game.distribution.interval_coverage
  if (!coverage) return null
  return (
    <div className="np-game__intervals">
      {(['margin', 'total'] as const).map((quantity) => {
        const actual = quantity === 'margin' ? game.margin.actual : game.total.actual
        const widest = coverage[quantity]['90']
        const span = Math.max(1, widest.upper - widest.lower)
        const position = ((actual - widest.lower) / span) * 100
        return (
          <div className="np-interval" key={quantity}>
            <small>{quantity === 'margin' ? 'Margin' : 'Total'} intervals</small>
            <div className="np-interval__track">
              {(['90', '80', '50'] as const).map((level) => {
                const band = coverage[quantity][level]
                return (
                  <span key={level} className={`np-interval__band is-${level}`}
                    style={{
                      left: `${((band.lower - widest.lower) / span) * 100}%`,
                      width: `${((band.upper - band.lower) / span) * 100}%`,
                    }} />
                )
              })}
              <span className="np-interval__actual"
                style={{ left: `${Math.min(100, Math.max(0, position))}%` }}
                title={`actual ${actual}`} />
            </div>
            <span className="np-interval__legend">
              {(['50', '80', '90'] as const).map((level) => (
                <em key={level} className={coverage[quantity][level].contains ? 'is-in' : 'is-out'}>
                  {level}% {coverage[quantity][level].contains ? 'hit' : 'miss'}
                </em>
              ))}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function Inventory({ data }: { data: NflProspectiveResults }) {
  return (
    <details className="np__inventory">
      <summary>What NFL forecast evidence exists, and what of it can be scored</summary>
      <div className="np__inventory-body">
        <p>
          Only a <strong>numerical forecast</strong> — something that publishes outcome
          probabilities and expected scores — can be scored as a prospective forecast.
        </p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Model</th><th scope="col">Version</th><th scope="col">Snapshots</th>
                <th scope="col">Events</th><th scope="col">Scoreable</th>
              </tr>
            </thead>
            <tbody>
              {data.inventory.numerical_forecast_models.map((entry) => (
                <tr key={`${entry.engine_id}-${entry.engine_version}`}>
                  <th scope="row">{entry.engine_id}</th>
                  <td>{entry.engine_version}</td>
                  <td>{entry.snapshots.toLocaleString()}</td>
                  <td>{entry.events}</td>
                  <td>yes</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          The evidence below describes those forecasts rather than competing with them, so none
          of it is scored as a forecast.
        </p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Kind</th><th scope="col">Engine</th>
                <th scope="col">Records</th><th scope="col">Events</th>
              </tr>
            </thead>
            <tbody>
              {data.inventory.supporting_evidence.map((entry) => (
                <tr key={`${entry.kind}-${entry.engine_id}`}>
                  <th scope="row">{entry.kind.replace(/_/g, ' ').toLowerCase()}</th>
                  <td>{entry.engine_id ?? '—'}</td>
                  <td>{entry.records}</td>
                  <td>{entry.events}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="np__note">
          {data.inventory.nfl_games_with_a_final} NFL game(s) currently carry a final score.
          Scoring version {data.scoring_version}.
        </p>
      </div>
    </details>
  )
}
