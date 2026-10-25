/**
 * NHL inside Model Performance.
 *
 *   * NhlPerformanceTiles -- Detailed view: one model entry is either REGULATION_3WAY
 *     (3-way Brier 0-2, 3-way log loss) or FINAL_2WAY (2-way Brier, log loss, AUC).
 *     Never pooled; regulation is scored only where the model supplied it.
 *   * NhlOverviewSection -- Overview: the server's NHL block, real forecasts and
 *     preview fixtures kept apart.
 *   * NhlNoPositions -- the NHL paper positions as Wager Results reads them (since the era
 *     opened 2026-10-16), or the server's explicit "no positions" notice when it sends one.
 */
import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import type { NhlOverviewBlock, NhlOverviewPopulation } from '../../types/nhlResearch'
import { NhlTestDataToggle } from '../../pages/NhlResearchPage'
import './nhl.css'

const f = (v: number | null | undefined, d = 3) => (v == null ? '—' : v.toFixed(d))
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

export interface NhlModelEntry {
  engine_id: string
  outcome_definition?: string
  record_class?: string
  cohorts: { ALL: NhlMetrics }
  games: NhlScoredGame[]
}
interface NhlMetrics {
  n: number
  accuracy: number | null
  correct_winners: number
  log_loss: number | null
  brier_three_class: number | null
  brier_two_way: number | null
  log_loss_two_way: number | null
  auc: number | null
  regulation_ties?: number
  decided_in?: Record<string, number>
  regulation_not_modelled?: number
}
interface NhlScoredGame {
  evaluation_id: string
  nhl_game_id?: string
  start_utc: string | null
  home: string | null
  away: string | null
  final: { home: number; away: number }
  regulation?: { home: number; away: number }
  decided_in?: string
  probabilities?: Record<string, number>
  predicted_outcome: string | null
  actual_outcome: string | null
  winner_correct: boolean | null
  log_loss: number | null
  brier_three_class?: number | null
  brier_two_way?: number | null
  is_fixture?: boolean
}

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' }) : '—')

export function NhlPerformanceTiles({ model }: { model: NhlModelEntry }) {
  const m = model.cohorts.ALL
  const regulation = model.outcome_definition === 'REGULATION_3WAY'
  return (
    <div className="nhl-perf" data-testid={`nhl-perf-${regulation ? 'regulation' : 'final'}`}>
      <dl className="cc-perf__tiles">
        <div className="cc-perf__tile--wide"><dt>Outcome scored</dt>
          <dd>{regulation ? 'Regulation · 3-way' : 'Final · 2-way'}</dd>
          <small>{regulation ? 'HOME / TIE / AWAY at 60:00; a tie goes to OT/SO' : 'HOME / AWAY including overtime and shootout; never tied'} · n={m.n}</small></div>
        {regulation ? (
          <>
            <div><dt>Brier · 3-way</dt><dd>{f(m.brier_three_class)}</dd><small>range 0–2, summed over home/tie/away</small></div>
            <div><dt>Log loss · 3-way</dt><dd>{f(m.log_loss)}</dd><small>probability of the 60:00 result</small></div>
            <div><dt>Accuracy</dt><dd>{pct(m.accuracy)}</dd><small>{m.correct_winners} of {m.n} · most likely of 3</small></div>
            <div><dt>Regulation ties</dt><dd>{m.regulation_ties ?? 0}</dd><small>observed in the scored games</small></div>
          </>
        ) : (
          <>
            <div><dt>Brier · 2-way</dt><dd>{f(m.brier_two_way)}</dd><small>range 0–1</small></div>
            <div><dt>Log loss · 2-way</dt><dd>{f(m.log_loss_two_way)}</dd><small>probability of the final winner</small></div>
            <div><dt>AUC</dt><dd>{f(m.auc)}</dd><small>{m.auc == null ? 'needs both outcomes' : 'home win'}</small></div>
            <div><dt>Accuracy</dt><dd>{pct(m.accuracy)}</dd><small>{m.correct_winners} of {m.n} winners</small></div>
            <div><dt>Decided in</dt><dd>{m.decided_in ? `${m.decided_in.REGULATION ?? 0} / ${m.decided_in.OVERTIME ?? 0} / ${m.decided_in.SHOOTOUT ?? 0}` : '—'}</dd><small>regulation / OT / SO</small></div>
            {(m.regulation_not_modelled ?? 0) > 0 && (
              <div><dt>Regulation not modelled</dt><dd>{m.regulation_not_modelled}</dd><small>scored on the final only, never as zero</small></div>
            )}
          </>
        )}
        <div className="cc-perf__tile--wide"><dt>Model − market</dt><dd>—</dd><small>Not computed for NHL forecast scoring; NHL prices and paper positions are read in Wager Results.</small></div>
      </dl>
    </div>
  )
}

export function NhlScoredLedger({ model }: { model: NhlModelEntry }) {
  const regulation = model.outcome_definition === 'REGULATION_3WAY'
  return (
    <section className="cc-mpanel" aria-label="NHL scored games" data-testid="nhl-ledger">
      <h3>Scored NHL games · {model.games.length} · {regulation ? 'regulation 3-way' : 'final 2-way'}</h3>
      <div className="cc-table__scroll">
        <table className="cc-board cc-perf__ledger">
          <thead><tr><th>Date</th><th>Game</th><th>Regulation</th><th>Final</th><th>Decided</th><th>{regulation ? 'P(home/tie/away)' : 'P(home)'}</th><th>Actual</th><th>{regulation ? 'Brier 3-way' : 'Brier 2-way'}</th><th>Log loss</th></tr></thead>
          <tbody>
            {model.games.map((g) => (
              <tr key={g.evaluation_id}>
                <td>{day(g.start_utc)}</td>
                <td>{g.nhl_game_id ? <Link className="cc-linkish" to={`/nhl/games/${g.nhl_game_id.replace('nhl:game:', '')}`}>{g.away} @ {g.home}</Link> : `${g.away} @ ${g.home}`}{g.is_fixture ? ' · fixture' : ''}</td>
                <td>{g.regulation ? `${g.regulation.away}–${g.regulation.home}` : '—'}</td>
                <td>{g.final.away}–{g.final.home}</td>
                <td>{g.decided_in === 'OVERTIME' ? 'OT' : g.decided_in === 'SHOOTOUT' ? 'SO' : 'REG'}</td>
                <td>{regulation && g.probabilities ? `${pct(g.probabilities.home)} / ${pct(g.probabilities.tie)} / ${pct(g.probabilities.away)}` : pct(g.probabilities?.home)}</td>
                <td>{g.actual_outcome}</td>
                <td>{f(regulation ? g.brier_three_class : g.brier_two_way)}</td>
                <td>{f(g.log_loss)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Population({ title, pop, fixture }: { title: string; pop: NhlOverviewPopulation; fixture?: boolean }) {
  return (
    <div className="nhl-perf__pair">
      <div className="nhl-perf__box">
        <h4>{title} · Regulation 3-way (60:00)</h4>
        <dl>
          <dt>Scored games</dt><dd>{pop.regulation_3way.n}</dd>
          <dt>Brier · 3-way (0–2)</dt><dd>{f(pop.regulation_3way.brier_three_way)}</dd>
          <dt>Log loss · 3-way</dt><dd>{f(pop.regulation_3way.log_loss_three_way)}</dd>
          <dt>Accuracy</dt><dd>{pct(pop.regulation_3way.accuracy)}</dd>
          <dt>Regulation not modelled</dt><dd>{pop.regulation_3way.not_modelled}</dd>
        </dl>
        <p>{pop.regulation_3way.outcome}{fixture ? ' Fixture data: not evidence.' : ''}</p>
      </div>
      <div className="nhl-perf__box">
        <h4>{title} · Final 2-way (incl. OT/SO)</h4>
        <dl>
          <dt>Scored games</dt><dd>{pop.final_2way.n}</dd>
          <dt>Brier · 2-way (0–1)</dt><dd>{f(pop.final_2way.brier_two_way)}</dd>
          <dt>Log loss · 2-way</dt><dd>{f(pop.final_2way.log_loss_two_way)}</dd>
          <dt>Accuracy</dt><dd>{pct(pop.final_2way.accuracy)}</dd>
          <dt>Decided REG / OT / SO</dt><dd>{pop.final_2way.decided_in.REGULATION ?? 0} / {pop.final_2way.decided_in.OVERTIME ?? 0} / {pop.final_2way.decided_in.SHOOTOUT ?? 0}</dd>
        </dl>
        <p>{pop.final_2way.outcome}</p>
      </div>
    </div>
  )
}

export function NhlOverviewSection({ block }: { block: NhlOverviewBlock }) {
  return (
    <section className="cc-mpanel mpo__section" aria-label="NHL research scores" data-testid="mpo-nhl">
      <h3>NHL · research forecasts, regulation and final scored separately</h3>
      <p className="cc-note">{block.statement} {block.forecast_games} NHL games with a forecast · {block.finals} finals · {block.scored} real forecasts scored.</p>
      <NhlTestDataToggle hidden={block.include_test_data ? 0 : block.test_data_rows} />
      {block.scored > 0
        ? <Population title="Real forecasts" pop={block} />
        : <p className="cc-note" data-testid="mpo-nhl-none">No real NHL Gen1 forecast has been scored yet: a forecast is scored only when it was published before the start and the game has an official final.</p>}
      {block.include_test_data && block.preview_fixture.scored > 0 && <Population title="PREVIEW FIXTURE (invented, not evidence)" pop={block.preview_fixture} fixture />}
      <p className="cc-note">{block.markets.statement} {block.wagering.statement} <Link to="/performance?panel=detailed&sport=NHL">Detailed NHL analysis</Link></p>
    </section>
  )
}

interface WagerProbe {
  rows: { status?: string }[]
  research_only_sports?: Record<string, { statement: string; official_positions: number }>
}

/** NHL paper positions in one line: counted from Wager Results, never assumed to be none. */
export function NhlNoPositions() {
  const loader = useCallback(async (signal: AbortSignal) =>
    apiRequest<WagerProbe>('/research/wager-results?sport=NHL', {}, signal), [])
  const { data, error } = useApiResource<WagerProbe>(loader, { key: cacheKey('wager-results-nhl', {}), staleTimeMs: STALE_TIME.history })
  const notice = data?.research_only_sports?.NHL
  const settled = data ? data.rows.filter((r) => r.status === 'SETTLED').length : 0
  return (
    <section className="cc-mpanel" aria-label="NHL wager results" data-testid="nhl-no-positions">
      <h3>NHL · virtual paper positions</h3>
      {notice
        ? <p><b>{notice.statement}</b></p>
        : <p><b>{data ? `${data.rows.length} NHL paper position${data.rows.length === 1 ? '' : 's'} recorded (${data.rows.length - settled} open, ${settled} settled).` : error ? 'NHL positions are unavailable.' : 'Reading NHL positions…'}</b>{' '}
          <Link to="/performance?panel=wagers&sport=NHL">Open NHL Wager Results</Link></p>}
      <p className="cc-note">Virtual paper only, decided under AISP_NHL_PAPER_POLICY_V1. Forecasts are scored separately in the Overview and Detailed views.</p>
    </section>
  )
}
