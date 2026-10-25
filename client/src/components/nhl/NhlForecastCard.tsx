/**
 * One NHL game: research forecast (regulation 3-way and final 2-way, kept apart),
 * goal estimates with their horizon, goalie assumptions, provenance, forecast age,
 * and -- for a finished game -- the regulation result, the OT/SO indicator and the
 * official final, each with its own source.
 *
 * Prices and wagering are the server's own statements (``markets``, ``wagering``):
 * "No NHL market prices collected" while no NHL paper era is open, and since the
 * era opened (2026-10-16) The Odds API collection and the virtual paper account.
 * A decision or position is shown only as the checkpoint RECORDED it; this card
 * never derives an edge, a pick or a recommendation itself.
 */
import type React from 'react'
import { Link } from 'react-router-dom'
import type { NhlForecast, NhlGameRow, NhlGoalie, NhlResult } from '../../types/nhlResearch'

const ET = 'America/New_York'
export const pct = (p: number | null | undefined, d = 1) => (p == null ? '—' : `${(p * 100).toFixed(d)}%`)
const goals = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

export function etDateTime(iso: string | null | undefined): string {
  if (!iso) return 'time TBD'
  return new Date(iso).toLocaleString('en-US', { timeZone: ET, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' ET'
}

export function age(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min old`
  if (seconds < 86400 * 2) return `${(seconds / 3600).toFixed(seconds < 36000 ? 1 : 0)} h old`
  return `${Math.round(seconds / 86400)} days old`
}

function lead(published: string | null, start: string | null): string {
  if (!published || !start) return '—'
  const h = (new Date(start).getTime() - new Date(published).getTime()) / 3600000
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`
}

const GAME_TYPE: Record<string, string> = { PRESEASON: 'Preseason', REGULAR: 'Regular season', PLAYOFF: 'Playoffs' }
const CLASS_LABEL: Record<string, string> = {
  PREVIEW_FIXTURE: 'PREVIEW FIXTURE · invented test data',
  SYNTHETIC_FIXTURE: 'SYNTHETIC · Model Studio example, not a forecast',
}

export function FixtureBadge({ row }: { row: Pick<NhlGameRow, 'is_fixture' | 'record_class'> }) {
  if (!row.is_fixture) return null
  const label = CLASS_LABEL[row.record_class ?? ''] ?? 'PREVIEW FIXTURE · invented test data'
  return <span className="nhl-badge nhl-badge--fixture" data-testid="nhl-fixture-badge">{label}</span>
}

export function StatusLabel({ row }: { row: NhlGameRow }) {
  const tone = row.result ? 'final' : row.status === 'live' ? 'live'
    : row.status === 'played_awaiting_result' ? 'awaiting' : 'scheduled'
  return <span className={`nhl-status nhl-status--${tone}`} data-testid="nhl-status">{row.result ? row.result.status_label : row.status_label}</span>
}

function Bar({ parts }: { parts: { key: string; label: string; value: number; tone: string }[] }) {
  return (
    <div className="nhl-bar" role="img" aria-label={parts.map((p) => `${p.label} ${pct(p.value)}`).join(', ')}>
      {parts.map((p) => (
        <span key={p.key} className={`nhl-bar__seg nhl-bar__seg--${p.tone}`} style={{ flexGrow: Math.max(p.value, 0.0001) }}>
          {p.value >= 0.12 ? pct(p.value, 0) : ''}
        </span>
      ))}
    </div>
  )
}

function GoalieLine({ side, team, g }: { side: string; team: string; g: NhlGoalie }) {
  return (
    <li className="nhl-goalie" data-testid={`nhl-goalie-${side}`}>
      <span className={`nhl-goalie__status nhl-goalie__status--${g.status.toLowerCase()}`}>{g.status.toLowerCase()}</span>
      <span><b>{team}</b> · {g.goalieName ?? (g.status === 'UNKNOWN' ? 'goalie not known' : 'goalie not named')}</span>
      <small>{g.statusSource ? `${g.statusSource.replace(/_/g, ' ').toLowerCase()} · observed ${etDateTime(g.statusObservedAtUtc)}` : 'no goalie source'}</small>
    </li>
  )
}

export function NhlForecastBlock({ row, forecast }: { row: NhlGameRow; forecast: NhlForecast }) {
  const home = row.home?.abbreviation ?? 'Home'
  const away = row.away?.abbreviation ?? 'Away'
  const reg = forecast.probabilities.regulation
  const fin = forecast.probabilities.final
  return (
    <div className="nhl-forecast" data-testid="nhl-forecast">
      <div className="nhl-forecast__meta">
        <span>Model <b>{forecast.model_id}</b> · v{forecast.model_version}</span>
        <span data-testid="nhl-forecast-age">{forecast.published_before_start === false
          ? 'published after puck drop'
          : row.result || row.status !== 'scheduled'
            ? `published ${lead(forecast.clocks.published_utc ?? forecast.clocks.created_utc, row.start_utc)} before puck drop`
            : age(forecast.age_seconds)} · published {etDateTime(forecast.clocks.published_utc ?? forecast.clocks.created_utc)}
          {forecast.clocks.published_utc == null && ' (unpublished example)'}</span>
        {forecast.published_before_start === false && (
          <span className="nhl-forecast__late" data-testid="nhl-forecast-late">
            Published after this game started — shown for the record, never scored.
          </span>)}
        <span>Research only · goalie features {forecast.provenance.goalie_feature_mode.replace(/_/g, '-').toLowerCase()}</span>
      </div>

      <div className="nhl-forecast__cols">
      <div className="nhl-forecast__col">
      <section className="nhl-prob" aria-label="Regulation result probabilities" data-testid="nhl-regulation">
        <h4>Regulation · 60:00 · 3-way</h4>
        {reg ? (
          <>
            <Bar parts={[
              { key: 'away', label: `${away} win in regulation`, value: reg.away, tone: 'away' },
              { key: 'tie', label: 'Tied after 60:00', value: reg.tie, tone: 'tie' },
              { key: 'home', label: `${home} win in regulation`, value: reg.home, tone: 'home' }]} />
            <p className="nhl-prob__legend">{away} {pct(reg.away)} · tie {pct(reg.tie)} · {home} {pct(reg.home)}
              <small> sums to {pct(reg.away + reg.tie + reg.home, 2)}</small></p>
          </>
        ) : (
          <p className="nhl-notmodelled" data-testid="nhl-regulation-not-modelled">Regulation not modelled. This model supplies only the final winner probability; no regulation or tie probability is shown or inferred.</p>
        )}
      </section>

      <section className="nhl-prob" aria-label="Final result probabilities" data-testid="nhl-final">
        <h4>Final · including overtime and shootout · 2-way</h4>
        <Bar parts={[
          { key: 'away', label: `${away} wins`, value: fin.away, tone: 'away' },
          { key: 'home', label: `${home} wins`, value: fin.home, tone: 'home' }]} />
        <p className="nhl-prob__legend">{away} {pct(fin.away)} · {home} {pct(fin.home)} <small> sums to {pct(fin.away + fin.home, 2)} · an NHL final never ties</small></p>
        {(forecast.probabilities.overtime != null || forecast.probabilities.shootout != null) && (
          <p className="nhl-prob__legend">P(overtime) {pct(forecast.probabilities.overtime)} · P(shootout) {forecast.probabilities.shootout == null ? 'not supplied' : pct(forecast.probabilities.shootout)}</p>
        )}
      </section>

      </div>
      <div className="nhl-forecast__col">
      <section className="nhl-goals" aria-label="Expected goals" data-testid="nhl-goals">
        <h4>Expected goals</h4>
        <dl>
          <div><dt>Regulation (60:00)</dt><dd>{forecast.goals.regulation ? `${away} ${goals(forecast.goals.regulation.away)} – ${goals(forecast.goals.regulation.home)} ${home}` : 'not modelled'}</dd></div>
          <div><dt>Final (OT goal counts; shootout adds one goal to the winner)</dt><dd>{forecast.goals.final ? `${away} ${goals(forecast.goals.final.away)} – ${goals(forecast.goals.final.home)} ${home}` : 'not modelled'}</dd></div>
        </dl>
      </section>

      <section aria-label="Goalie assumptions">
        <h4>Goalie assumptions</h4>
        <ul className="nhl-goalies">
          <GoalieLine side="away" team={away} g={forecast.goalies.away} />
          <GoalieLine side="home" team={home} g={forecast.goalies.home} />
        </ul>
        {forecast.missing_inputs.length > 0 && (
          <p className="nhl-missing">Missing inputs: {forecast.missing_inputs.map((m) => m.replace(/_/g, ' ').toLowerCase()).join(' · ')}</p>
        )}
      </section>
      </div>
      </div>
    </div>
  )
}

/** Forecast ids, hashes, clocks and provenance: collapsed by default. */
export function NhlTechnicalDetails({ row, children }: { row: NhlGameRow; children?: React.ReactNode }) {
  const f = row.forecast
  return (
    <details className="nhl-tech" data-testid="nhl-technical-details">
      <summary>Technical details</summary>
      <dl>
        <div><dt>Game id</dt><dd>{row.canonical_game_id}{row.aisp_game_id ? ` · AISP ${row.aisp_game_id}` : ''}</dd></div>
        {f && <>
          <div><dt>Forecast id</dt><dd>{f.forecast_id}</dd></div>
          <div><dt>Object sha256</dt><dd>{f.object_sha256}</dd></div>
          <div><dt>Revisions held</dt><dd>{row.revision_count}{f.supersedes_forecast_id ? ` · supersedes ${f.supersedes_forecast_id}` : ''}</dd></div>
          <div><dt>Provenance</dt><dd>{f.provenance.evidence_mode} · goalie features {f.provenance.goalie_feature_mode} · producer evidence {f.provenance.producer_evidence_status} ({f.provenance.producer_evidence_ref.sha256.slice(0, 16)}…, {f.provenance.producer_evidence_ref.bytes} B)</dd></div>
          <div><dt>Clocks (UTC)</dt><dd>source cutoff {f.clocks.source_cutoff_utc ?? '—'} · retrieved {f.clocks.source_retrieved_utc ?? '—'} · created {f.clocks.created_utc ?? '—'} · published {f.clocks.published_utc ?? '— (unpublished)'} ({f.clocks.publication_clock_source}) · AISP available {f.clocks.available_as_of ?? '—'} · imported {f.clocks.imported_at ?? '—'}</dd></div>
        </>}
        {row.result && <div><dt>Result sha256</dt><dd>{row.result.result_sha256}</dd></div>}
      </dl>
      {children}
    </details>
  )
}

export function NhlResultBlock({ row, result }: { row: NhlGameRow; result: NhlResult }) {
  const home = row.home?.abbreviation ?? 'Home'
  const away = row.away?.abbreviation ?? 'Away'
  const regLabel = result.regulation.result === 'TIE' ? 'tied' : `${result.regulation.result === 'HOME' ? home : away} won`
  return (
    <section className="nhl-result" aria-label="Result" data-testid="nhl-result">
      <h4>Result</h4>
      <dl>
        <div><dt>Regulation (60:00)</dt><dd data-testid="nhl-result-regulation">{away} {result.regulation.away_goals} – {result.regulation.home_goals} {home} · {regLabel}</dd></div>
        <div><dt>Decided in</dt><dd data-testid="nhl-result-decided">{result.decided_in === 'REGULATION' ? 'Regulation' : result.decided_in === 'OVERTIME' ? 'Overtime' : `Shootout (${away} ${result.shootout?.away_scored ?? '?'} – ${result.shootout?.home_scored ?? '?'} ${home} in attempts)`}</dd></div>
        <div><dt>Official final</dt><dd data-testid="nhl-result-final"><b>{away} {result.final.away_score} – {result.final.home_score} {home}</b> · {result.status_label}{result.decided_in === 'SHOOTOUT' ? ' · shootout winner credited one goal' : ''}</dd></div>
        <div><dt>Sources</dt><dd><small>regulation: {result.regulation.source} · final: {result.final.source}</small></dd></div>
      </dl>
    </section>
  )
}

const money = (v: number | null | undefined, signed = false) => (v == null ? '—'
  : `${signed && v > 0 ? '+' : v < 0 ? '−' : ''}$${Math.abs(v).toFixed(2)}`)
const odds = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)

/** Prices and wagering as the server states them now, plus this game's recorded paper decision and position. */
export function NhlNoMarket({ row }: { row: Pick<NhlGameRow, 'markets' | 'wagering' | 'paper' | 'home' | 'away'> }) {
  const paper = row.paper
  const side = (s: string | null | undefined) => (s === 'HOME' ? row.home?.abbreviation : s === 'AWAY' ? row.away?.abbreviation : s) ?? '—'
  return (
    <>
      <p className="nhl-nomarket" role="note" data-testid="nhl-no-market">
        {row.markets.statement} {row.wagering.statement}
      </p>
      {paper && (
        <p className="nhl-paper" role="note" data-testid="nhl-paper">
          <b>Paper checkpoint {etDateTime(paper.decision.recorded_at)}:</b>{' '}
          {paper.decision.recommendation.replace(/_/g, ' ').toLowerCase()}
          {paper.decision.reason ? ` (${paper.decision.reason.replace(/_/g, ' ').toLowerCase()})` : ''}
          {paper.decision.american_odds != null && <> · {side(paper.decision.selection)} {odds(paper.decision.american_odds)}{paper.decision.sportsbook ? ` at ${paper.decision.sportsbook}` : ''}</>}
          {paper.position
            ? <> · position {money(paper.position.stake)} · {paper.position.status === 'SETTLED'
              ? `${(paper.position.result ?? 'settled').toLowerCase()} ${money(paper.position.profit_loss, true)}` : 'open'}</>
            : ' · no position'}
          {' '}· virtual paper only
        </p>
      )}
    </>
  )
}

export function NhlForecastCard({ row, link = true, technical }: { row: NhlGameRow; link?: boolean; technical?: React.ReactNode }) {
  const matchup = `${row.away?.name ?? row.away?.abbreviation ?? 'Away'} at ${row.home?.name ?? row.home?.abbreviation ?? 'Home'}`
  return (
    <article className={`nhl-card${row.is_fixture ? ' nhl-card--fixture' : ''}`} data-testid={`nhl-card-${row.source_game_id}`}>
      <header className="nhl-card__head">
        <div>
          <span className="nhl-card__eyebrow">NHL · {GAME_TYPE[row.game_type ?? ''] ?? 'Game'} {row.season ?? ''} · {etDateTime(row.start_utc)}</span>
          <h3 className="nhl-card__matchup">{link ? <Link to={`/nhl/games/${row.source_game_id}`}>{matchup}</Link> : matchup}</h3>
          {(row.revision_count > 1 || row.models.length > 1) && <small className="nhl-card__ids">{row.revision_count > 1 ? `${row.revision_count} forecast revisions` : ''}{row.models.length > 1 ? ` · ${row.models.length} models` : ''}</small>}
        </div>
        <div className="nhl-card__state">
          <StatusLabel row={row} />
          <span className={`nhl-forecast-label${row.forecast ? '' : ' nhl-forecast-label--pending'}`} data-testid="nhl-forecast-label">
            {row.forecast ? row.forecast_label : 'Forecast pending'}</span>
          {row.score && <span className="nhl-card__score" data-testid="nhl-score">{row.away?.abbreviation} {row.score.away} – {row.score.home} {row.home?.abbreviation} <small>goals</small></span>}
          <FixtureBadge row={row} />
        </div>
      </header>
      {row.forecast
        ? <NhlForecastBlock row={row} forecast={row.forecast} />
        : <p className="nhl-noforecast" data-testid="nhl-no-forecast"><b>Forecast pending.</b> No NHL Gen1 forecast has been published for this game yet; nothing is shown in its place.</p>}
      {row.result && <NhlResultBlock row={row} result={row.result} />}
      <NhlNoMarket row={row} />
      <NhlTechnicalDetails row={row}>{technical}</NhlTechnicalDetails>
    </article>
  )
}
