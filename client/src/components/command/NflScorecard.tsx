/**
 * Model Performance · NFL scorecard.
 *
 * Leads with what a reader needs: how many eligible forecasts have settled,
 * how many are still open or could not be scored, review progress, and a
 * predicted-versus-final chart. Each game then shows its win probability, the
 * result and the score errors in plain language; the technical evidence
 * (forecast revision, package and population, final-result revision, scoring
 * receipt, any independent settlement record) sits in a collapsed audit drawer.
 *
 * Model scoring only. Decision-policy results, the virtual bankroll and My Card
 * are separate evaluations and are not shown here.
 */
import * as d3 from 'd3'
import { memo, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { etTime, gameHref, SPORT_COLORS } from '../../services/commandCenter'
import { productionLabel, useNflScorecard, type NflCohort, type ScoredNflGame } from '../../services/nflScorecard'
import { ChartTable, Tooltip, useTooltip, useWidth } from './chartKit'
import { AsOf } from './RegionState'

const pct = (v: number | null | undefined, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const num = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))
const signed = (v: number | null | undefined, d = 1) => {
  if (v == null) return '—'
  const text = Math.abs(v).toFixed(d)
  // A value that rounds to zero is shown without a sign ("0.0", never "−0.0").
  return Number(text) === 0 ? text : `${v > 0 ? '+' : '−'}${text}`
}
const short = (sha: string | null | undefined) => (sha ? `${sha.slice(0, 12)}…` : '—')
const OUTCOME: Record<string, string> = { HOME: 'home win', AWAY: 'away win', TIE: 'tie' }
const REASON: Record<string, string> = {
  GAME_POSTPONED: 'postponed', GAME_CANCELED: 'cancelled',
  NO_PRE_KICKOFF_FORECAST: 'no forecast published before kickoff', SCORING_PENDING: 'final recorded, scoring pending',
}

export function NflScorecard() {
  const resource = useNflScorecard()
  const data = resource.data
  const [modelKey, setModelKey] = useState<string | null>(null)
  if (!data) {
    return <section className="cc-mpanel ns" aria-label="NFL scorecard"><p className="cc-muted" role="status">
      {resource.error ? `The NFL scorecard is unavailable: ${resource.error}` : 'Loading the NFL scorecard…'}</p></section>
  }
  const cohort = data.cohorts.find((c) => `${c.model_id}|${c.model_revision}` === modelKey) ?? data.cohorts[0] ?? null
  const coverage = data.coverage
  return (
    <section className="cc-mpanel ns" aria-labelledby="ns-title" style={{ ['--sport' as string]: SPORT_COLORS.NFL }}>
      <header className="ns__head">
        <div>
          <h3 id="ns-title">NFL scorecard</h3>
          <p className="ns__authority">{productionLabel(data.production_status)}</p>
        </div>
        <AsOf resource={resource} label="scorecard" />
      </header>

      <dl className="ns__coverage" aria-label="Forecast coverage">
        <div><dt>Settled and scored</dt><dd>{coverage.settled_and_scored}</dd><small>of {coverage.eligible_games_started} forecast games that have kicked off</small></div>
        <div><dt>Awaiting a final</dt><dd>{coverage.open_awaiting_final}</dd><small>scored automatically once the final is recorded</small></div>
        <div><dt>Could not be scored</dt><dd>{coverage.unscored}</dd>
          <small>{coverage.unscored ? Object.entries(coverage.unscored_reasons).map(([k, v]) => `${v} ${REASON[k] ?? k.toLowerCase()}`).join(' · ') : 'none'}</small></div>
        <div><dt>Upcoming</dt><dd>{coverage.upcoming_with_forecast}</dd><small>future games with a stored forecast</small></div>
      </dl>

      <p className="ns__warning" role="note">{data.small_sample_warning}</p>

      {!cohort
        ? <p className="cc-muted">No NFL forecast has been scored yet. A forecast is scored once its game has an authoritative final.</p>
        : (
          <>
            {data.cohorts.length > 1 && (
              <label className="ns__select"><span>Model version</span>
                <select value={`${cohort.model_id}|${cohort.model_revision}`} onChange={(e) => setModelKey(e.target.value)}>
                  {data.cohorts.map((c) => <option key={`${c.model_id}|${c.model_revision}`} value={`${c.model_id}|${c.model_revision}`}>{c.model_label} · {c.model_revision} · {c.settled_games} games</option>)}
                </select>
              </label>
            )}
            <CohortSummary cohort={cohort} />
            <ErrorChart games={cohort.games} />
            <GameList games={cohort.games} />
            <details className="ns__more">
              <summary>Spread and total lines, paper view and benchmark</summary>
              <LinesAndViews cohort={cohort} benchmark={data.benchmark.statement} />
            </details>
          </>
        )}
    </section>
  )
}

function CohortSummary({ cohort }: { cohort: NflCohort }) {
  const s = cohort.summary
  const next = cohort.review.next_checkpoint
  const progress = next ? Math.min(1, cohort.settled_games / next) : 1
  return (
    <>
      <div className="ns__review" aria-label="Review progress">
        <div className="ns__reviewtext">
          <b>{cohort.settled_games} settled {cohort.settled_games === 1 ? 'game' : 'games'}</b>
          <span>{cohort.model_label} · {cohort.model_revision} · weeks {cohort.weeks.join(', ') || '—'} · {cohort.dates.length} game {cohort.dates.length === 1 ? 'date' : 'dates'}</span>
          <span>{next ? `First formal review at ${next} games · ${next - cohort.settled_games} to go` : 'All registered review checkpoints reached'}</span>
        </div>
        <div className="ns__bar" role="progressbar" aria-valuemin={0} aria-valuemax={next ?? cohort.settled_games}
          aria-valuenow={cohort.settled_games} aria-label={next ? `${cohort.settled_games} of ${next} games toward review` : 'Review checkpoints reached'}>
          <i style={{ width: `${progress * 100}%` }} />
        </div>
      </div>
      <dl className="ns__tiles">
        <div><dt>Winner called</dt><dd>{s.correct_winners} of {s.games}</dd><small>{pct(s.winner_accuracy)} · {s.ties} tie{s.ties === 1 ? '' : 's'}</small><p>{cohort.explanations.winner_accuracy}</p></div>
        <div><dt>Log loss</dt><dd>{num(s.log_loss, 3)}</dd><small>lower is better · 0.69 is a coin flip</small><p>{cohort.explanations.log_loss}</p></div>
        <div><dt>Brier score</dt><dd>{num(s.brier_score, 3)}</dd><small>0 perfect · 2 worst</small><p>{cohort.explanations.brier_score}</p></div>
        <div><dt>Score error</dt><dd>{num(s.pooled_score_mae, 1)} pts</dd><small>home {num(s.home_score_mae, 1)} · away {num(s.away_score_mae, 1)}</small><p>{cohort.explanations.score_errors}</p></div>
        <div><dt>Margin error</dt><dd>{num(s.margin_mae, 1)} pts</dd><small>average miss · bias {signed(s.mean_margin_error)}</small></div>
        <div><dt>Total error</dt><dd>{num(s.total_mae, 1)} pts</dd><small>average miss · bias {signed(s.mean_total_error)}</small></div>
      </dl>
      <p className="cc-note">{cohort.calibration.available ? 'Calibration is available for this cohort.' : cohort.calibration.reason} {cohort.review.note}</p>
    </>
  )
}

/** Predicted versus final margin per game: the distance is the miss. */
const ErrorChart = memo(function ErrorChart({ games }: { games: ScoredNflGame[] }) {
  const [kind, setKind] = useState<'margin' | 'total'>('margin')
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const axis = useRef<SVGGElement | null>(null)
  const rows = games.filter((g) => g.predicted[kind] != null)
  const rowH = 22
  const m = { top: 8, right: 16, bottom: 30, left: 92 }
  const height = m.top + rows.length * rowH + m.bottom
  const values = rows.flatMap((g) => [g.predicted[kind] as number, g.final[kind]])
  const lo = kind === 'margin' ? Math.min(-7, d3.min(values) ?? 0) : Math.min(20, d3.min(values) ?? 20)
  const hi = kind === 'margin' ? Math.max(7, d3.max(values) ?? 0) : Math.max(60, d3.max(values) ?? 60)
  const x = d3.scaleLinear().domain([lo, hi]).nice().range([m.left, Math.max(m.left + 80, width - m.right)])
  useEffect(() => {
    if (axis.current) d3.select(axis.current).call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(width / 90))) as never)
  }, [x, width])
  if (!rows.length) return null
  const label = kind === 'margin' ? 'home minus away' : 'both teams'
  const tipFor = (g: ScoredNflGame) => (
    <><b>{g.matchup}</b><dl>
      <dt>Predicted {kind}</dt><dd>{num(g.predicted[kind], 1)}</dd>
      <dt>Final {kind}</dt><dd>{g.final[kind]}</dd>
      <dt>Miss</dt><dd>{signed(g.errors[kind])} pts</dd>
    </dl></>
  )
  return (
    <div className="cc-chart ns__chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <div className="ns__charthead">
        <h4>Predicted versus final {kind}</h4>
        <div className="cc-filters" role="group" aria-label="Chart quantity">
          <button type="button" aria-pressed={kind === 'margin'} onClick={() => setKind('margin')}>Margin</button>
          <button type="button" aria-pressed={kind === 'total'} onClick={() => setKind('total')}>Total</button>
        </div>
      </div>
      <svg width={width} height={height} role="img" aria-label={`Predicted and final ${kind} (${label}) for ${rows.length} games`}>
        {kind === 'margin' && <line x1={x(0)} x2={x(0)} y1={m.top} y2={height - m.bottom} className="cc-perf__zero" />}
        {rows.map((g, i) => {
          const y = m.top + i * rowH + rowH / 2
          const p = x(g.predicted[kind] as number)
          const a = x(g.final[kind])
          return (
            <g key={g.evaluation_id} className="ns__row" tabIndex={0} aria-label={`${g.matchup}: predicted ${num(g.predicted[kind], 1)}, final ${g.final[kind]}`}
              onMouseMove={(e) => show(e, tipFor(g))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(g))} onBlur={hide}>
              <text x={m.left - 8} y={y + 4} textAnchor="end" className="ns__label">{g.matchup}</text>
              <line x1={Math.min(p, a)} x2={Math.max(p, a)} y1={y} y2={y} className="ns__miss" />
              <circle cx={p} cy={y} r={5} className="ns__pred" />
              <circle cx={a} cy={y} r={5} className="ns__final" />
            </g>
          )
        })}
        <g ref={axis} className="cc-axis" transform={`translate(0, ${height - m.bottom})`} />
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="ns__key ns__key--pred" />Predicted ({label})</li>
        <li><i className="ns__key ns__key--final" />Final</li>
        <li>The bar between them is the miss</li>
      </ul>
      <ChartTable caption={`Predicted and final ${kind}`}>
        <thead><tr><th>Game</th><th>Predicted</th><th>Final</th><th>Miss</th></tr></thead>
        <tbody>{rows.map((g) => <tr key={g.evaluation_id}><td>{g.matchup}</td><td>{num(g.predicted[kind], 1)}</td><td>{g.final[kind]}</td><td>{signed(g.errors[kind])}</td></tr>)}</tbody>
      </ChartTable>
    </div>
  )
})

function GameList({ games }: { games: ScoredNflGame[] }) {
  const [all, setAll] = useState(false)
  const ordered = [...games].sort((a, b) => (b.kickoff_utc ?? '').localeCompare(a.kickoff_utc ?? ''))
  const shown = all ? ordered : ordered.slice(0, 8)
  return (
    <section aria-label="Scored NFL games">
      <h4 className="ns__h4">Scored games · {games.length}</h4>
      <ol className="ns__games">
        {shown.map((g) => <GameRow key={g.evaluation_id} game={g} />)}
      </ol>
      {games.length > 8 && <button type="button" className="cc-more" onClick={() => setAll((v) => !v)}>{all ? 'Show fewer' : `Show all ${games.length}`}</button>}
    </section>
  )
}

function GameRow({ game: g }: { game: ScoredNflGame }) {
  const home = g.home?.abbreviation ?? 'Home'
  const away = g.away?.abbreviation ?? 'Away'
  const winner = g.final.outcome === 'HOME' ? home : g.final.outcome === 'AWAY' ? away : 'Tie'
  const revision = g.audit.forecast_revision
  const final = g.audit.final_result
  return (
    <li className="ns__game">
      <div className="ns__gamehead">
        <span className="ns__when">{g.kickoff_utc ? etTime(g.kickoff_utc, true) : '—'}{g.week ? ` · Week ${g.week}` : ''}</span>
        {g.game_id ? <Link className="cc-linkish ns__matchup" to={gameHref('NFL', g.game_id)}>{away} at {home}</Link> : <b className="ns__matchup">{away} at {home}</b>}
        <span className={`ns__verdict ${g.scores.winner_correct ? 'is-up' : 'is-down'}`}>{g.scores.winner_correct ? 'Winner called' : 'Winner missed'}</span>
      </div>
      <dl className="ns__facts">
        <div><dt>Forecast</dt><dd>{home} {pct(g.probabilities.home)} · {away} {pct(g.probabilities.away)} · tie {pct(g.probabilities.tie, 1)}</dd></div>
        <div><dt>Result</dt><dd>{winner === 'Tie' ? 'Tie' : `${winner} won`} · {OUTCOME[g.final.outcome] ?? g.final.outcome} was given {pct(g.scores.probability_of_actual)}</dd></div>
        <div><dt>Score</dt><dd>predicted {away} {num(g.predicted.away, 1)} – {home} {num(g.predicted.home, 1)} · final {away} {g.final.away} – {home} {g.final.home}{g.final.overtime ? ' (OT)' : ''}</dd></div>
        <div><dt>Misses</dt><dd>margin {signed(g.errors.margin)} · total {signed(g.errors.total)} · log loss {num(g.scores.log_loss, 3)} · Brier {num(g.scores.brier_score, 3)}</dd></div>
      </dl>
      <details className="ns__audit">
        <summary>Audit</summary>
        <dl>
          <div><dt>Forecast revision</dt><dd>{revision ? `${revision.snapshot_type} v${revision.snapshot_version ?? '—'} · published ${revision.published_utc ? etTime(revision.published_utc, true) : '—'} · ${revision.horizon?.hours_before_kickoff ?? '—'} h before kickoff` : '—'}</dd></div>
          <div><dt>Forecast id</dt><dd className="ns__mono">{revision?.forecast_id ?? g.audit.scoring_receipt.forecast_identity}</dd></div>
          <div><dt>Model</dt><dd className="ns__mono">{revision?.engine_id ?? revision?.model_id} · {revision?.engine_version ?? revision?.model_revision}</dd></div>
          <div><dt>Package</dt><dd className="ns__mono" title={revision?.package_sha256 ?? ''}>{short(revision?.package_sha256)}</dd></div>
          <div><dt>Population</dt><dd className="ns__mono" title={g.audit.population_sha256 ?? ''}>{short(g.audit.population_sha256)}{revision?.population_verification && !revision.population_verification.verified ? ` · not verified: ${revision.population_verification.detail}` : ''}</dd></div>
          <div><dt>Why this revision</dt><dd>{(revision?.reason ?? revision?.controlling_reason ?? '').replace(/_/g, ' ').toLowerCase() || '—'}</dd></div>
          <div><dt>Kickoff</dt><dd>{g.audit.kickoff_utc ? etTime(g.audit.kickoff_utc, true) : '—'}</dd></div>
          <div><dt>Final result</dt><dd>{final ? `${final.source} · tier ${final.tier}${final.overtime == null ? '' : final.overtime ? ' · overtime' : ' · regulation'}` : '—'}</dd></div>
          {final && final.revisions.length > 1 && (
            <div><dt>Result revisions</dt><dd><ul className="ns__list">{final.revisions.map((r) => <li key={r.result_identity}>{r.score} · {r.source}{r.current ? ' · current' : ''}</li>)}</ul></dd></div>
          )}
          <div><dt>Scoring receipt</dt><dd className="ns__mono">{g.audit.scoring_receipt.evaluation_id} · {g.audit.scoring_receipt.scoring_version}</dd></div>
          {g.audit.scoring_receipt.replaces.length > 0 && (
            <div><dt>Replaces</dt><dd><ul className="ns__list">{g.audit.scoring_receipt.replaces.map((r) => <li key={r.evaluation_id}>{r.score} · {r.reason.replace(/_/g, ' ').toLowerCase()}</li>)}</ul></dd></div>
          )}
          <div><dt>Mass check</dt><dd>{g.mass_reconciliation.reconciled ? 'probabilities sum to one' : `residual ${g.mass_reconciliation.residual ?? '—'}`}</dd></div>
          {g.paper_view && (
            <div><dt>Paper view</dt><dd>{g.paper_view.same_revision ? 'same revision as scored' : `separate revision · log loss ${num(g.paper_view.log_loss, 3)}`}</dd></div>
          )}
          <div><dt>Settlement record</dt><dd>{g.audit.settlement_records.length
            ? g.audit.settlement_records.map((p) => `${p.status}${p.result ? ` · ${p.result}` : ''}`).join('; ')
            : 'none — no experiment position on this game'}</dd></div>
        </dl>
      </details>
    </li>
  )
}

function LinesAndViews({ cohort, benchmark }: { cohort: NflCohort; benchmark: string }) {
  const entries = Object.entries(cohort.lines)
  return (
    <div className="ns__lines">
      {entries.length === 0
        ? <p className="cc-muted">No stored pre-kickoff spread or total line to score yet.</p>
        : (
          <div className="cc-table__scroll"><table className="cc-board">
            <thead><tr><th>Line</th><th>Games</th><th>Integer lines</th><th>Pushes</th><th>Log loss</th><th>Brier</th></tr></thead>
            <tbody>{entries.map(([market, v]) => (
              <tr key={market}><td>{market === 'SPREAD' ? 'Spread (home side)' : 'Total (over)'}</td><td>{v.scored}</td><td>{v.integer_lines}</td><td>{v.pushes}</td><td>{num(v.mean_log_loss, 3)}</td><td>{num(v.mean_brier_score, 3)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      <p className="cc-note">Lines are scored over win, loss and push using the forecast's own push probability at the last stored pre-kickoff line; a push is never dropped or counted as a loss. {entries[0]?.[1].basis ?? ''}</p>
      <p className="cc-note"><b>Paper research view.</b> {cohort.paper_view.games} game(s) · {cohort.paper_view.same_revision_as_research} scored the same revision · log loss {num(cohort.paper_view.log_loss, 3)}. {cohort.paper_view.note}</p>
      <p className="cc-note"><b>Benchmark.</b> {benchmark}</p>
    </div>
  )
}
