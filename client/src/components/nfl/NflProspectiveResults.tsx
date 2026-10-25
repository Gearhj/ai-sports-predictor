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
import { AlertTriangle, CheckCircle2, ChevronDown, Radio, Target, XCircle } from 'lucide-react'
import type { NflProspectiveResults, ProspectiveGame, ProspectiveModel } from '../../types/nflProspective'
import { TeamLogo } from '../TeamLogo'
import { CalibrationChart, PredictedVsActualChart } from '../charts/d3Charts'
import { describeError, domainWithZero, errorDirection, errorTone, signedNumber } from '../../services/errorWording'
import { teamIdentity } from '../../services/teamIdentity'

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

      {model.games[0] && <Scorecard game={model.games[0]} n={n} />}

      <p className="np__sample">
        <strong>n = {n}</strong> scored game{n === 1 ? '' : 's'} for {model.model_label}.{' '}
        {data.sample_note} The first formal review comes at{' '}
        {data.review_checkpoint_games} settled selections per sport; these are shown from the
        first game because hiding evidence until then would be its own distortion.
      </p>

      {data.superseded_evaluations > 0 && (
        <p className="np__note">{data.provenance_note}</p>
      )}

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

      <Calibration games={model.games} label={model.model_label} />

      <div className="np__games">
        <h4 className="np__section-title">Every scored game</h4>
        {model.games.map((game) => <GameCard key={game.evaluation_id} game={game} />)}
      </div>

      <Inventory data={data} />
    </section>
  )
}

/**
 * The headline scorecard: what the forecast said, what happened, in one look.
 *
 * The whole NE @ SEA story has three parts and a reader should get all three
 * without doing arithmetic: the winner was right, the margin was almost exact,
 * and the scoring level was badly overestimated. Those are three different
 * findings, so they get three separate readouts -- and the predicted-against-
 * actual chart puts every quantity on ONE shared scale, which is what makes a
 * 0.19-point miss and a 19.69-point miss look as different as they are.
 */
/**
 * The headline card's sentence, derived from THIS game.
 *
 * It used to be a fixed string written for the first NE @ SEA result ("right
 * winner, margin almost exact, scoring level overestimated"), so ATL @ GB -- a
 * wrong winner, a 26.6-point margin miss and a total UNDERestimated by 5.5 --
 * was described as the opposite of what happened. Every clause now reads the
 * game's own winner, margin error and total error (predicted minus actual).
 */
export function scorecardStory(game: ProspectiveGame, n: number): string {
  const home = teamName(game.home)
  const away = teamName(game.away)
  const winner = game.final.home > game.final.away ? home : game.final.away > game.final.home ? away : null
  const predictedWinner = game.outcome.predicted === 'HOME' ? home : game.outcome.predicted === 'AWAY' ? away : 'a tie'
  const winnerClause = game.final.home === game.final.away
    ? `The game ended in a tie; the model had favoured ${predictedWinner}`
    : game.outcome.winner_correct
      ? `The model picked the right winner (${winner})`
      : `The model picked ${predictedWinner}, but ${winner} won`
  const marginSize = errorTone(game.margin.error, 'margin', 'NFL')
  const marginClause = game.margin.error == null ? 'published no margin'
    : marginSize === 'small' ? `got the margin within ${Math.abs(game.margin.error).toFixed(1)} points`
      : `missed the margin by ${Math.abs(game.margin.error).toFixed(1)} points (predicted ${signedNumber(game.margin.predicted, 1)} for ${home}, actual ${signedNumber(game.margin.actual, 0)})`
  const totalClause = game.total.error == null ? 'published no total'
    : errorDirection(game.total.error) === 'exact' ? 'matched the total exactly'
      : `${errorDirection(game.total.error) === 'over' ? 'overestimated' : 'underestimated'} the total by ${Math.abs(game.total.error).toFixed(1)} points (predicted ${number(game.total.predicted, 1)}, actual ${game.total.actual})`
  return `${winnerClause}. It ${marginClause}, and ${totalClause}. Those are three separate findings. `
    + `With ${n === 1 ? 'one game' : `${n} games`} none of them establishes whether the model is good.`
}

/** The name a team goes by now (the teams table still holds relocated franchises' old names). */
function teamName(team: ProspectiveGame['home']): string {
  if (!team?.abbreviation) return team?.name ?? 'Unknown team'
  const identity = teamIdentity('NFL', team.abbreviation)
  return identity.known ? identity.name : (team.name ?? team.abbreviation)
}

const TONE_CLASS = { small: 'is-good', medium: 'is-warn', large: 'is-bad' } as const

function Scorecard({ game, n }: { game: ProspectiveGame; n: number }) {
  const away = game.away?.abbreviation ?? 'AWAY'
  const home = game.home?.abbreviation ?? 'HOME'
  const correct = game.outcome.winner_correct
  const marginTone = errorTone(game.margin.error, 'margin', 'NFL')
  const totalTone = errorTone(game.total.error, 'total', 'NFL')
  const chartRows = [
    { key: 'home', label: `${home} score`, predicted: game.scores.predicted_home ?? 0, actual: game.final.home, unit: 'pts' },
    { key: 'away', label: `${away} score`, predicted: game.scores.predicted_away ?? 0, actual: game.final.away, unit: 'pts' },
    { key: 'margin', label: 'Margin', predicted: game.margin.predicted ?? 0, actual: game.margin.actual, unit: 'pts' },
    { key: 'total', label: 'Total', predicted: game.total.predicted ?? 0, actual: game.total.actual, unit: 'pts' },
  ]
  return (
    <section className={`np-card${correct ? ' is-correct' : ' is-wrong'}`}
      aria-label="Most recent scored NFL forecast">
      <header className="np-card__head">
        <span className="np-card__eyebrow">
          Most recent scored game{n === 1 ? ' · the only one so far' : ''}
        </span>
        <span className={`np-card__verdict${correct ? ' is-correct' : ' is-wrong'}`}>
          {correct ? <CheckCircle2 size={17} aria-hidden="true" /> : <XCircle size={17} aria-hidden="true" />}
          {correct ? 'Winner correct' : 'Winner wrong'}
        </span>
      </header>

      <div className="np-card__score">
        <div className="np-card__team">
          <TeamLogo league="NFL" abbreviation={away} size={72} />
          <div className="np-card__name">
            <b>{teamName(game.away)}</b>
            <small>predicted {number(game.scores.predicted_away, 1)}</small>
          </div>
          <strong className="np-card__points">{game.final.away}</strong>
        </div>
        <span className="np-card__at">FINAL</span>
        <div className="np-card__team np-card__team--home">
          <strong className="np-card__points">{game.final.home}</strong>
          <div className="np-card__name">
            <b>{teamName(game.home)}</b>
            <small>predicted {number(game.scores.predicted_home, 1)}</small>
          </div>
          <TeamLogo league="NFL" abbreviation={home} size={72} />
        </div>
      </div>

      <div className="np-card__verdicts">
        <div>
          <small>Winner</small>
          <strong className={correct ? 'is-good' : 'is-bad'}>{correct ? 'Right' : 'Wrong'}</strong>
          <span>{percent(game.outcome.probability_of_actual)} given to what happened</span>
        </div>
        {/* Colour follows the SIZE of the miss, never its sign. */}
        <div>
          <small>Margin error</small>
          <strong className={marginTone ? TONE_CLASS[marginTone] : ''} data-testid="np-margin-error">{signed(game.margin.error, 2)}</strong>
          <span>{number(game.margin.predicted, 2)} predicted, {game.margin.actual} actual · {describeError(game.margin.error)} (home − away)</span>
        </div>
        <div>
          <small>Total error</small>
          <strong className={totalTone ? TONE_CLASS[totalTone] : ''} data-testid="np-total-error">{signed(game.total.error, 2)}</strong>
          <span>{number(game.total.predicted, 2)} predicted, {game.total.actual} actual · {describeError(game.total.error)}</span>
        </div>
      </div>

      {/* The domain spans below zero whenever a margin is negative (an away win). */}
      <PredictedVsActualChart
        rows={chartRows}
        domain={domainWithZero(chartRows.flatMap((row) => [row.predicted, row.actual]), 50)} />

      <p className="np-card__story" data-testid="np-story">{scorecardStory(game, n)}</p>
      <ResultProvenance result={game.result} />
    </section>
  )
}

/**
 * Calibration, drawn honestly at the sample we actually have.
 *
 * Forecasts are bucketed by the probability they gave the home side and
 * plotted against how often the home side actually won. At n=1 that is one dot
 * with an interval covering most of the square, which is the correct picture
 * of knowing nothing yet -- so it ships from the first game with the interval
 * and the count visible, rather than being withheld until the number flatters
 * us.
 *
 * The interval is Wilson at 95%, which behaves at n=1 where the normal
 * approximation would collapse to zero width and imply certainty.
 */
function Calibration({ games, label }: { games: ProspectiveGame[]; label: string }) {
  const bins = useMemo(() => {
    const edges = [0, 0.2, 0.4, 0.6, 0.8, 1]
    return edges.slice(0, -1).map((low, index) => {
      const high = edges[index + 1]
      const inBin = games.filter((game) => {
        const probability = game.outcome.published_probabilities?.home
        if (probability == null) return false
        return probability >= low && (index === edges.length - 2 ? probability <= high : probability < high)
      })
      const wins = inBin.filter((game) => game.outcome.actual === 'HOME').length
      const count = inBin.length
      const forecast = count === 0
        ? (low + high) / 2
        : inBin.reduce((sum, game) => sum + (game.outcome.published_probabilities?.home ?? 0), 0) / count
      return {
        label: `${Math.round(low * 100)}-${Math.round(high * 100)}%`,
        forecast,
        observed: count === 0 ? null : wins / count,
        n: count,
        ...wilson(wins, count),
      }
    })
  }, [games])
  const scored = games.length
  return (
    <section className="np__calibration" aria-label="Calibration">
      <div>
        <h4 className="np__section-title">Is the model&apos;s confidence honest?</h4>
        <p className="np__lede">
          Every scored forecast is grouped by the probability it gave the home team, then plotted
          against how often the home team actually won. A perfectly calibrated model sits on the
          dashed diagonal. Dot size is the number of games in that bucket; the vertical bar is the
          95% interval around the observed rate.
        </p>
        <p className="np__note">
          With {scored} scored game{scored === 1 ? '' : 's'} for {label}, that interval spans almost
          the whole range. That is the correct reading, not a rendering fault: this diagram cannot
          yet tell a well-calibrated model from a badly calibrated one, and it is drawn at this size
          so the uncertainty is visible rather than left to be assumed.
        </p>
      </div>
      <CalibrationChart bins={bins} size={340} />
    </section>
  )
}

/** Wilson score interval. Unlike the normal approximation it behaves at n=1. */
function wilson(successes: number, n: number): { low: number | null; high: number | null } {
  if (n === 0) return { low: null, high: null }
  const z = 1.96
  const rate = successes / n
  const denominator = 1 + (z * z) / n
  const centre = (rate + (z * z) / (2 * n)) / denominator
  const spread = (z / denominator) * Math.sqrt((rate * (1 - rate)) / n + (z * z) / (4 * n * n))
  return { low: Math.max(0, centre - spread), high: Math.min(1, centre + spread) }
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
            {teamName(game.away)} <i>at</i> {teamName(game.home)}
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
        {' '}<ResultProvenance result={game.result} />
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
          <p><strong>Result provenance</strong> {game.result.label} — {game.result.note}</p>
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
 * Where the final came from, in the words that fit it.
 *
 * Three states, and the middle one is the reason this is a component rather
 * than a boolean. A human typing a box score is unverified and gets a warning.
 * The governed provider is verified and needs no badge at all. The free
 * automatic feed is neither: it is trustworthy and hands-off, but it is not
 * the governed provider, and stamping "verified" on it would launder a
 * community mirror into a guarantee we have not earned.
 */
function ResultProvenance({ result }: { result: ProspectiveGame['result'] }) {
  if (result.provider_verified) return null
  const source = result.source.toLowerCase().replace(/_/g, ' ')
  return (
    <span className={`np-game__provenance${result.automated ? ' is-automated' : ' is-manual'}`}
      title={result.note}>
      {result.automated
        ? <Radio size={12} aria-hidden="true" />
        : <AlertTriangle size={12} aria-hidden="true" />}
      final from {source} — {result.automated ? 'automatic, not provider-verified' : 'operator entered, not verified'}
      {result.superseded_count > 0 && ` · replaced ${result.superseded_count} earlier evaluation${result.superseded_count === 1 ? '' : 's'}`}
    </span>
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
          // Colour by the size of the miss; the side of the zero line already shows the direction.
          <span className={`np-error__bar is-${errorTone(error, label === 'Total' ? 'total' : label === 'Margin' ? 'margin' : 'score', 'NFL')}`}
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
