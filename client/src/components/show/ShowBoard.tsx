/**
 * The day-before Show Board: the working screen for the 5:00 PM ET recording.
 *
 *   1. Show readiness -- slate, freeze countdown, counts, draft/frozen status.
 *   2. The planned show clock, with the dormant steps named as such.
 *   3. Top preview opportunities -- up to four valid Preview model-market
 *      differences, never padded.
 *   4. Talking-point facts derived from stored evidence.
 *   5. The full rundown by sport and kickoff, with per-game evidence on demand.
 *   6. Preview versus final, once final-pregame evidence exists.
 *   7. Snapshot identity and authority disclosures (also the print footer).
 *
 * Everything shown is SHOW PREVIEW research. Nothing here is a final wagering
 * decision, and "NO BET" never appears: that is a final-pregame disposition.
 */
import { ArrowRight, Printer } from 'lucide-react'
import { memo, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { TeamLogo } from '../TeamLogo'
import { ProbabilityRail } from '../command/ProbabilityRail'
import { AsOf } from '../command/RegionState'
import { LazyDetails } from '../command/LazyDetails'
import { gameHref, SPORT_COLORS, SPORT_NAMES, type SportCode } from '../../services/commandCenter'
import { forSide, rangeForSide } from '../../services/marketMath'
import {
  useShowBoardGame, type PreviewFinalRow, type ShowLeg, type ShowBoardResponse, type ShowGame, type ShowSport,
} from '../../services/showBoard'
import type { useApiResource } from '../../hooks/useApiResource'
import './show.css'

const TZ = 'America/New_York'
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const pts = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} pts`)
const signedPct = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const line = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const book = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const clock = (iso: string | null | undefined) => (iso ? new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) : '—')
const stamp = (iso: string | null | undefined) => (iso ? new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) + ' ET' : '—')
const longDate = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
const shiftDay = (day: string, by: number) => { const d = new Date(`${day}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + by); return d.toISOString().slice(0, 10) }
/** A started or finished game's preview state is history, not something still pending. */
export function pastTense(label: string, started: boolean): string {
  if (!started) return label
  return label
    .replace('Preview forecast pending', 'No preview forecast was published')
    .replace('No current preview price held', 'no preview price was held')
}
const SPORT_ORDER: ShowSport[] = ['MLB', 'NFL', 'NBA', 'CFB']
const availability = (state: string, what: string) => (!state || state === 'NOT_APPLICABLE' ? '' : ` · ${what} ${state.toLowerCase()}`)

function useMinute(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

function countdown(targetIso: string, now: number): string {
  const minutes = Math.round((Date.parse(targetIso) - now) / 60_000)
  if (minutes <= 0) return 'passed'
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h >= 48) return `in ${Math.round(h / 24)} days`
  return h ? `in ${h}h ${String(m).padStart(2, '0')}m` : `in ${m}m`
}

export function ShowBoard({ resource, date, onDate }: {
  resource: ReturnType<typeof useApiResource<ShowBoardResponse>>
  date: string | null
  onDate: (next: string | null) => void
}) {
  const now = useMinute()
  const data = resource.data
  if (!data) {
    return (
      <section className="sb" aria-busy="true">
        <div className="sb-hero sb-hero--loading"><span className="sb-kicker">Show Prep · SHOW PREVIEW — NOT FINAL</span>
          <h1>{resource.error ? 'The Show Board is unavailable' : 'Loading the Show Board…'}</h1>
          {resource.error && <p className="sb-muted">{resource.error}</p>}</div>
      </section>
    )
  }
  const board = data.board
  const slate = data.slate_date
  return (
    <section className="sb" aria-label="Show Board">
      <Readiness data={data} now={now} date={date} onDate={onDate} resource={resource} />
      <ShowClock data={data} now={now} />
      <TopOpportunities data={data} />
      <TalkingPoints data={data} />
      <Rundown data={data} slate={slate} />
      <PreviewVersusFinal rows={data.preview_vs_final} />
      <footer className="sb-footer" aria-label="Snapshot identity and authority">
        <dl>
          <div><dt>Status</dt><dd>{data.mode_label}</dd></div>
          <div><dt>Snapshot</dt><dd>{data.snapshot ? <code title={data.snapshot.snapshot_sha256}>{data.snapshot.snapshot_sha256.slice(0, 16)}…</code> : 'Not frozen'}{data.snapshot && !data.snapshot.verified ? ' · identity check failed' : ''}</dd></div>
          <div><dt>{data.mode === 'FROZEN' ? 'Frozen' : 'Evidence as of'}</dt><dd>{stamp(data.mode === 'FROZEN' ? data.snapshot?.frozen_utc : board.evidence_as_of_utc)}</dd></div>
          <div><dt>Target slate</dt><dd>{longDate(slate)}</dd></div>
        </dl>
        <p>{board.authority.statement}</p>
        <p className="sb-muted">Sources: persisted AISP sportsbook observations (each price shows its own capture time) and preview forecasts published under AISP_SHOW_PREVIEW_FORECAST_V1. Day-before provider collection is {data.flags.provider_collection_enabled ? 'enabled' : 'disabled'}; this page made {data.provider_calls_made} provider calls.</p>
        {board.disclosures.filter((d) => !d.game_key).map((d) => <p key={d.code} className="sb-muted">{d.message}.</p>)}
      </footer>
    </section>
  )
}

function Readiness({ data, now, date, onDate, resource }: {
  data: ShowBoardResponse; now: number; date: string | null; onDate: (next: string | null) => void
  resource: ReturnType<typeof useApiResource<ShowBoardResponse>>
}) {
  const r = data.board.readiness
  const frozen = data.mode === 'FROZEN'
  const freezePassed = Date.parse(data.planned_freeze_utc) <= now
  return (
    <header className="sb-hero">
      <div className="sb-hero__top">
        <div>
          <span className="sb-kicker">Show Prep · <b>SHOW PREVIEW — NOT FINAL</b></span>
          <h1>Show board · {longDate(data.slate_date)}</h1>
          <p className="sb-hero__sub">
            <span className={`sb-status sb-status--${data.mode}`}>{data.mode_label}</span>
            <span>Not a final wagering decision</span>
          </p>
        </div>
        <div className="sb-hero__side">
          <nav className="sb-daynav" aria-label="Slate date">
            <button type="button" onClick={() => onDate(shiftDay(data.slate_date, -1))} aria-label="Previous slate">‹</button>
            <span>{new Date(`${data.slate_date}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</span>
            <button type="button" onClick={() => onDate(shiftDay(data.slate_date, 1))} aria-label="Next slate">›</button>
          </nav>
          {date && date !== data.default_slate_date && <button type="button" className="sb-link" onClick={() => onDate(null)}>Next show slate</button>}
          <button type="button" className="sb-print" onClick={() => window.print()}><Printer size={14} /> Print rundown</button>
          <AsOf resource={resource} label="board" />
        </div>
      </div>
      <dl className="sb-stats">
        <div className="sb-stats__freeze">
          <dt>{frozen ? 'Frozen' : 'Planned freeze'}</dt>
          <dd>{frozen ? clock(data.snapshot?.frozen_utc) : clock(data.planned_freeze_utc)}</dd>
          <small>{frozen ? stamp(data.snapshot?.frozen_utc) : freezePassed ? 'freeze time passed · not frozen' : `${countdown(data.planned_freeze_utc, now)} · ${stamp(data.planned_freeze_utc)}`}</small>
        </div>
        <div><dt>Scheduled games</dt><dd>{r.games}</dd><small>{SPORT_ORDER.filter((s) => r.by_sport[s]).map((s) => `${s} ${r.by_sport[s]}`).join(' · ') || 'none registered'}</small></div>
        <div><dt>Preview forecasts</dt><dd className={r.preview_forecasts ? '' : 'is-gold'}>{r.preview_forecasts}</dd><small>{r.preview_forecasts ? 'published' : 'Preview forecast pending'}</small></div>
        <div><dt>Complete markets</dt><dd className={r.complete_markets ? '' : 'is-gold'}>{r.complete_markets}</dd><small>{r.complete_markets ? 'moneyline, both sides' : 'no current preview price held'}</small></div>
        <div><dt>Both ready</dt><dd className={r.both ? 'is-up' : ''}>{r.both}</dd><small>{r.valid_comparisons} valid comparisons</small></div>
        <div><dt>Missing evidence</dt><dd className={r.missing_evidence_games ? 'is-gold' : 'is-up'}>{r.missing_evidence_games}</dd><small>games not fully ready</small></div>
      </dl>
    </header>
  )
}

function stepState(step: ShowBoardResponse['board']['clock'][number], data: ShowBoardResponse): string {
  if (step.key === 'RECORDING') return 'James & Scott'
  if (step.provider_calls) return data.flags.provider_collection_enabled ? 'enabled' : 'disabled · no provider calls'
  if (!step.implemented) return 'not automated'
  if (step.key === 'BUILD') return 'read-only · builds on view'
  if (!data.flags.show_preview_enabled) return data.mode === 'FROZEN' && step.key === 'FREEZE' ? 'frozen' : 'off · workflow disabled'
  if (step.key === 'FREEZE') return data.mode === 'FROZEN' ? 'frozen' : data.flags.auto_freeze_enabled ? 'automatic' : 'administrator only'
  return 'ready'
}

function ShowClock({ data, now }: { data: ShowBoardResponse; now: number }) {
  return (
    <section className="sb-clock" aria-label="Show-production clock">
      <ol>
        {data.board.clock.filter((s) => s.local_time).map((step) => {
          const passed = step.at_utc ? Date.parse(step.at_utc) <= now : false
          const dormant = !step.implemented || (step.provider_calls && !data.flags.provider_collection_enabled)
            || ((step.key === 'FREEZE' || step.key === 'FORECAST_IMPORT') && !data.flags.show_preview_enabled)
          return (
            <li key={step.key} className={`${passed ? 'is-passed' : ''} ${dormant ? 'is-dormant' : ''} ${step.key === 'FREEZE' ? 'is-freeze' : ''}`}
              title={step.note}>
              <b>{new Date(`2000-01-01T${step.local_time}:00`).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}</b>
              <span>{step.label}</span>
              <small>{stepState(step, data)}</small>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function TopOpportunities({ data }: { data: ShowBoardResponse }) {
  const byKey = useMemo(() => new Map(data.board.games.map((g) => [g.game_key, g])), [data.board.games])
  const top = data.board.ranking.slice(0, 4).map((r) => ({ rank: r.rank, game: byKey.get(r.game_key)! })).filter((x) => x.game)
  return (
    <section className="sb-top" aria-labelledby="sb-top-title">
      <header className="sb-section__head">
        <h2 id="sb-top-title">Top preview opportunities</h2>
        <span className="sb-muted">Largest Preview model-market differences · research, not a wager</span>
      </header>
      {top.length === 0
        ? <p className="sb-empty">No valid preview comparison yet. A comparison needs a preview forecast and a complete, current preview moneyline for the same game — {data.board.readiness.preview_forecasts} forecasts and {data.board.readiness.complete_markets} complete markets so far.</p>
        : <div className={`sb-cards sb-cards--n${top.length}`} role="list">{top.map(({ rank, game }) => <OpportunityCard key={game.game_key} rank={rank} game={game} />)}</div>}
    </section>
  )
}

const OpportunityCard = memo(function OpportunityCard({ rank, game }: { rank: number; game: ShowGame }) {
  const c = game.comparison!
  const f = game.forecast!
  const ml = game.market_summary.moneyline
  const side = c.selection
  const color = SPORT_COLORS[game.sport as SportCode]
  const disclosure = game.missing.find((m) => ['PROBABLE_STARTERS', 'LINEUPS_NOT_CONFIRMED'].includes(m.code)) ?? game.missing[0]
  return (
    <article role="listitem" className="sb-card" style={{ ['--sport' as string]: color }} aria-label={`${rank}. ${game.matchup}, ${c.team} preview difference ${pts(c.difference)}`}>
      <header className="sb-card__top">
        <span className="sb-card__rank">{rank}</span>
        <span className="sb-card__sport"><i />{game.sport}</span>
        <span className="sb-card__tag">SHOW PREVIEW — NOT FINAL</span>
      </header>
      <div className="sb-card__teams">
        {(['AWAY', 'HOME'] as const).map((s) => {
          const team = s === 'HOME' ? game.home : game.away
          return (
            <span key={s} className={`sb-team${side === s ? ' is-selected' : ''}`}>
              <TeamLogo league={game.sport} abbreviation={team.abbreviation} size={30} /><b>{team.abbreviation}</b>
            </span>
          )
        })}
      </div>
      <p className="sb-card__when">{clock(game.kickoff_utc)} ET · projected {game.away.abbreviation} {f.expected_score.away.toFixed(1)} – {f.expected_score.home.toFixed(1)} {game.home.abbreviation}</p>
      <div className="sb-card__pick">
        <span>Model selection</span><b>{c.team} ML</b><strong>{am(c.american)}</strong>
      </div>
      <p className="sb-card__book">{book(c.sportsbook)} · {c.provider} {c.classification.toLowerCase()} · price captured {stamp(c.price_observed_utc)}</p>
      <dl className="sb-card__nums">
        <div><dt>Model</dt><dd>{pct(c.model_probability)}</dd></div>
        <div><dt>No-vig</dt><dd>{pct(c.no_vig_probability)}</dd></div>
        <div><dt title="Preview model-market difference">Preview diff.</dt><dd className="is-up">{pts(c.difference)}</dd></div>
        <div><dt>Exp. return</dt><dd>{signedPct(c.expected_return)}</dd></div>
      </dl>
      <ProbabilityRail label={`${c.team} win probability (preview)`} model={c.model_probability}
        consensus={ml ? forSide(ml.consensus_no_vig_home, side) : null} consensusCurrent
        range={ml ? rangeForSide(ml.range_no_vig_home, side) : null}
        bookProbability={c.no_vig_probability} bookLabel={`${book(c.sportsbook)} no-vig`} color={color} />
      <p className="sb-card__times">Forecast {stamp(c.forecast_created_utc)}{availability(f.starters, 'starters')}{availability(f.lineups, 'lineups')}</p>
      {disclosure && <p className="sb-card__warn">{disclosure.message}</p>}
      {game.canonical_game_id && <Link className="sb-card__link" to={gameHref(game.sport, game.canonical_game_id)}>Analyze game <ArrowRight size={13} /></Link>}
    </article>
  )
})

function TalkingPoints({ data }: { data: ShowBoardResponse }) {
  const points = data.board.talking_points
  return (
    <section className="sb-points" aria-labelledby="sb-points-title">
      <header className="sb-section__head"><h2 id="sb-points-title">Talking-point facts</h2><span className="sb-muted">Derived from stored evidence only</span></header>
      {points.length === 0 ? <p className="sb-empty">No fact can be stated yet from stored evidence.</p>
        : <ul>{points.map((p) => <li key={p.code}>{p.text}</li>)}</ul>}
    </section>
  )
}

function Rundown({ data, slate }: { data: ShowBoardResponse; slate: string }) {
  const groups = SPORT_ORDER.map((sport) => ({ sport, games: data.board.games.filter((g) => g.sport === sport) })).filter((g) => g.games.length)
  return (
    <section className="sb-rundown" aria-labelledby="sb-rundown-title">
      <header className="sb-section__head"><h2 id="sb-rundown-title">Full show rundown</h2><span className="sb-muted">{data.board.games.length} games · by sport and kickoff</span></header>
      {groups.length === 0 && <p className="sb-empty">No game is registered for this slate date.</p>}
      {groups.map(({ sport, games }) => (
        <div key={sport} className="sb-group" style={{ ['--sport' as string]: SPORT_COLORS[sport as SportCode] }}>
          <h3><i />{SPORT_NAMES[sport as SportCode]} <span>{games.length}</span></h3>
          <div className="sb-table" role="table" aria-label={`${sport} rundown`}>
            <div className="sb-row sb-row--head" role="row">
              <span role="columnheader">Start</span><span role="columnheader">Game</span><span role="columnheader">Readiness</span>
              <span role="columnheader">Model lean · projected</span><span role="columnheader">Moneyline</span>
              <span role="columnheader">Spread</span><span role="columnheader">Total</span><span role="columnheader">Final decision window</span>
            </div>
            {games.map((g) => <RundownRow key={g.game_key} game={g} slate={slate} />)}
          </div>
        </div>
      ))}
    </section>
  )
}

function RundownRow({ game, slate }: { game: ShowGame; slate: string }) {
  const ml = game.market_summary.moneyline
  const spread = game.market_summary.spread
  const total = game.market_summary.total
  const f = game.forecast
  // The lean and moneyline cells already say forecast pending / market not posted.
  const started = game.kickoff_utc != null && Date.parse(game.kickoff_utc) <= Date.now()
  const key = game.missing.filter((m) => !['NO_COMPLETE_SPREAD', 'NO_COMPLETE_TOTAL', 'PREVIEW_FORECAST_AWAITING_PRODUCER', 'PRICE_NOT_CAPTURED', 'STALE_PREVIEW_PRICE', 'PARTIAL_MARKET'].includes(m.code)).slice(0, 2)
  return (
    <div className="sb-rowwrap" role="rowgroup">
      <div className={`sb-row sb-row--${game.readiness}`} role="row">
        <span role="cell" className="sb-row__time">{clock(game.kickoff_utc)}</span>
        <span role="cell" className="sb-row__game">
          <b>{game.matchup}</b>
          {game.analysis_href ? <Link to={game.analysis_href}>Analyze</Link>
            : game.canonical_game_id ? <Link to={gameHref(game.sport, game.canonical_game_id)}>Analyze</Link> : <small>not in AISP schedule</small>}
        </span>
        <span role="cell"><span className={`sb-ready sb-ready--${game.readiness}`}>{pastTense(game.readiness_label, started)}</span></span>
        <span role="cell">{f ? <><b title="Win probability with ties excluded">{f.lean.team} {pct(f.lean.probability_two_way, 0)}{game.sport === 'MLB' ? '' : ' (no tie)'}</b><small>{game.away.abbreviation} {f.expected_score.away.toFixed(1)} – {f.expected_score.home.toFixed(1)} {game.home.abbreviation}</small></>
          : game.research_forecast
            ? <><small className="is-gold">No Show Preview</small><small data-testid="sb-research-forecast" title={game.research_forecast.label}>
                Research forecast: {game.research_forecast.pick} {pct(game.research_forecast.joint_probability, 0)} · {game.away.abbreviation} {game.research_forecast.ridge_expected_points.away.toFixed(1)} – {game.research_forecast.ridge_expected_points.home.toFixed(1)} {game.home.abbreviation} (research only)
              </small></>
            : <small className="is-gold">{started ? 'No preview forecast was published' : 'Preview forecast awaiting producer'}</small>}</span>
        <span role="cell">{ml && ml.complete_books
          ? <><b>{game.away.abbreviation} {am(ml.best.AWAY?.american)} · {game.home.abbreviation} {am(ml.best.HOME?.american)}</b><small>Price captured at {clock(ml.captured_utc)} · {ml.complete_books} books</small></>
          : <small className="is-gold">{ml?.stale_books ? `Stale only · captured ${stamp(game.markets.MONEYLINE.latest_capture_utc)}` : ml?.partial_books ? 'One side only' : game.missing.some((m) => m.code === 'SCHEDULE_NOT_REGISTERED') ? 'Not bindable · not in AISP schedule' : 'No preview price held'}</small>}</span>
        <span role="cell">{spread ? <><b>{game.home.abbreviation} {line(spread.median_line)}</b><small>{spread.books} books</small></> : <small>—</small>}</span>
        <span role="cell">{total ? <><b>{total.median_line}</b><small>{f ? `model ${f.projected_total.toFixed(1)}` : `${total.books} books`}</small></> : <small>—</small>}</span>
        <span role="cell">{game.final_decision_window ? `${clock(game.final_decision_window.opens_utc)}–${clock(game.final_decision_window.closes_utc)}` : '—'}</span>
      </div>
      {(key.length > 0 || game.comparison) && (
        <p className="sb-row__notes">
          {game.comparison && <span className="is-up">Preview model-market difference {pts(game.comparison.difference)} on {game.comparison.team}</span>}
          {key.map((m) => <span key={m.code}>{m.message}</span>)}
          <span className="sb-muted">RFF: not computed at the preview horizon</span>
        </p>
      )}
      {/* What the model must be shown with, never abbreviated away. A number
          read as more than it is does more damage on air than a missing one. */}
      {game.disclosures.length > 0 && (
        <p className="sb-row__disclosures">
          <b>Read this with the forecast:</b>
          {game.disclosures.map((d) => <span key={d.code}>{d.message}</span>)}
        </p>
      )}
      <LazyDetails className="sb-evidence" summary={`Evidence · ${game.matchup}`}>
        <GameEvidence slate={slate} gameKey={game.game_key} />
      </LazyDetails>
    </div>
  )
}

function GameEvidence({ slate, gameKey }: { slate: string; gameKey: string }) {
  const detail = useShowBoardGame(slate, gameKey)
  if (!detail.data) return <p className="sb-muted">{detail.error ? `Evidence unavailable: ${detail.error}` : 'Loading evidence…'}</p>
  const markets = detail.data.game.markets
  const rows = (['MONEYLINE', 'SPREAD', 'TOTAL'] as const).flatMap((kind) => (markets[kind] ?? []).map((p) => ({ kind, ...p })))
  if (!rows.length) return <p className="sb-muted">No sportsbook observation was captured for this game before {stamp(detail.data.evidence_as_of_utc)}.</p>
  return (
    <div className="sb-evtable">
      <table>
        <caption className="cc-sr">Every captured pair for this game</caption>
        <thead><tr><th>Market</th><th>Sportsbook · provider</th><th>Legs (price · line · observed)</th><th>Status</th><th>Settlement</th><th>Request</th></tr></thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={`${p.kind}-${p.sportsbook}-${p.provider}-${p.line}-${i}`}>
              <td>{p.kind === 'SPREAD' ? 'Spread / run line' : p.kind === 'TOTAL' ? 'Total' : 'Moneyline'}</td>
              <td>{book(p.sportsbook)}<small>{p.provider} · {p.classification.toLowerCase()}</small></td>
              <td>{p.legs.map((leg: ShowLeg) => <small key={leg.side}>{leg.side} {am(leg.american)}{leg.point != null ? ` (${line(leg.point)})` : ''} · {stamp(leg.observed_utc)}</small>)}</td>
              <td>{p.complete ? (p.stale ? 'Complete · stale' : 'Complete') : 'Incomplete'}</td>
              <td>{p.settlement.toLowerCase().replace(/_/g, ' ')}</td>
              <td>{p.source_request ? <small>{p.source_request.endpoint_key} · {p.source_request.objects_in_request} objects · matched by retrieval time</small> : <small>—</small>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PreviewVersusFinal({ rows }: { rows: PreviewFinalRow[] }) {
  const shown = rows.filter((r) => r.final || r.outcome)
  return (
    <section className="sb-pvf" aria-labelledby="sb-pvf-title">
      <header className="sb-section__head"><h2 id="sb-pvf-title">Preview versus final</h2><span className="sb-muted">Information, not a scorecard: a change between horizons is expected</span></header>
      {shown.length === 0 ? <p className="sb-empty">Final-pregame evidence has not been recorded for this slate yet.</p> : (
        <div className="sb-evtable">
          <table>
            <thead><tr><th>Game</th><th>Preview selection · model · price</th><th>Final selection · model · price</th><th>Change</th><th>Final disposition</th><th>Position</th><th>Outcome</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.game_key}>
                  <td><b>{r.matchup}</b><small>{r.sport}</small></td>
                  <td>{r.preview.selection ? `${r.preview.selection} · ${pct(r.preview.model_probability)} · ${am(r.preview.american)}` : r.preview.forecast_status === 'AWAITING_PRODUCER' ? 'Preview forecast awaiting producer' : '—'}
                    <small>{r.preview.price_observed_utc ? `captured ${stamp(r.preview.price_observed_utc)}` : r.preview.consensus_no_vig_home != null ? `preview home no-vig ${pct(r.preview.consensus_no_vig_home)}` : ''}</small></td>
                  <td>{r.final ? `${r.final.selection ?? '—'} · ${pct(r.final.model_probability)} · ${am(r.final.american)}` : '—'}<small>{r.final?.price_observed_utc ? `captured ${stamp(r.final.price_observed_utc)}` : ''}</small></td>
                  <td>{r.changes ? [
                    r.changes.selection_changed ? 'Selection changed' : null,
                    r.changes.model_probability_change != null ? `model ${pts(r.changes.model_probability_change)}` : null,
                    r.changes.price_change_american != null ? `price ${r.changes.price_change_american > 0 ? '+' : ''}${r.changes.price_change_american}` : null,
                  ].filter(Boolean).join(' · ') || 'No change' : '—'}</td>
                  <td>{r.final ? r.final.disposition.replace(/_/g, ' ').toLowerCase() : 'not yet decided'}</td>
                  <td>{r.position ? `${r.position.status.toLowerCase()} · $${r.position.stake.toFixed(2)}${r.position.result ? ` · ${r.position.result}` : ''}` : 'none recorded'}</td>
                  <td>{r.outcome ? `${r.outcome.away_score}–${r.outcome.home_score} · ${r.outcome.winner}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
