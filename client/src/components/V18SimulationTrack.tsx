/**
 * The V18 research-shadow simulator track.
 *
 * Two clearly separated kinds of evidence, in this order:
 *
 *   1. POPULATION SUMMARY — conclusions across all 5,000 completed V18
 *      simulations. These are V18's own frozen results. Gen 1's 20,000-draw
 *      figures belong to a different engine and never appear here.
 *   2. REPRESENTATIVE PATH — one of those simulations, replayed. It is a
 *      member of the population, not a summary of it, and not a prediction.
 *
 * There are no scenario controls in this track: AISP is not authorized to
 * execute the V18 bundle, so a recomputation would need a new Model Studio
 * artifact. That is stated rather than hidden behind a disabled button.
 */
import { useEffect, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { SequentialReplay } from './SequentialReplay'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { ShadowForecastView, ShadowPath } from '../types/shadow'

function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? 'Not available' : `${value.toFixed(digits)}%`
}

function ratio(value: number | null | undefined): string {
  return value == null ? 'Not available' : `${(value * 100).toFixed(1)}%`
}

function num(value: number | null | undefined, digits = 2): string {
  return value == null ? 'Not available' : value.toFixed(digits)
}

export function V18SimulationTrack({ shadow, awayTeam, homeTeam, gameId }: {
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
  gameId: string
}) {
  const [path, setPath] = useState<ShadowPath | null>(null)
  const [pathState, setPathState] = useState<'loading' | 'ready' | 'unavailable'>('loading')

  useEffect(() => {
    const controller = new AbortController()
    let live = true
    endpoints
      .shadowForecastPath(shadow.forecast_id, controller.signal)
      .then((payload) => {
        if (!live) return
        const usable = payload?.schema_version === 'AISP_MLB_V18_SEQUENTIAL_PATH_V1'
          && Array.isArray(payload.paths) && (payload.paths[0]?.length ?? 0) > 0
        setPath(usable ? payload : null)
        setPathState(usable ? 'ready' : 'unavailable')
      })
      .catch(() => { if (live) { setPath(null); setPathState('unavailable') } })
    return () => { live = false; controller.abort() }
  }, [shadow.forecast_id])

  const population = shadow.population
  const interval = shadow.monte_carlo_95_half_width

  return (
    <div className="v18-track">
      <section className="analysis-section" aria-labelledby="v18-population-heading">
        <div className="section-heading">
          <div>
            <span className="eyebrow">POPULATION SUMMARY · ALL SIMULATIONS</span>
            <h2 id="v18-population-heading">
              Conclusions across {population?.simulation_count?.toLocaleString() ?? '—'} V18 simulations
            </h2>
          </div>
          <p className="v18-track__note">
            Frozen results from the imported bundle. These are not Gen 1 values.
          </p>
        </div>

        <div className="v18-population">
          <article><span>{homeTeam} win</span><strong>{pct(population?.home_win_probability)}</strong>
            <small>{interval != null ? `±${(interval * 100).toFixed(2)} pts (95% sampling)` : 'sampling interval not recorded'}</small></article>
          <article><span>{awayTeam} win</span><strong>{pct(population?.away_win_probability)}</strong>
            <small>complement of the home probability</small></article>
          <article><span>Expected runs</span>
            <strong>{num(population?.expected_away_runs)} – {num(population?.expected_home_runs)}</strong>
            <small>{awayTeam} – {homeTeam}</small></article>
          <article><span>Expected total</span><strong>{num(population?.expected_total_runs)}</strong>
            <small>runs per game</small></article>
          <article><span>Run differential</span><strong>{num(population?.expected_run_differential)}</strong>
            <small>home minus away</small></article>
          <article><span>One-run game</span><strong>{ratio(population?.one_run_probability)}</strong>
            <small>share of simulations</small></article>
          <article><span>Extra innings</span><strong>{ratio(population?.extra_inning_probability)}</strong>
            <small>share of simulations</small></article>
          <article><span>Monte Carlo error</span>
            <strong>{population?.monte_carlo_standard_error != null
              ? `±${(population.monte_carlo_standard_error * 100).toFixed(2)} pts` : 'Not available'}</strong>
            <small>standard error · excludes model uncertainty</small></article>
        </div>

        <dl className="v18-identity">
          <div><dt>Forecast time</dt>
            <dd>{shadow.generated_at ? formatTimestamp(shadow.generated_at) : 'Not recorded'}</dd></div>
          <div><dt>Lineup state</dt>
            <dd>{shadow.lineup_modes?.join(' · ').replaceAll('_', ' ').toLowerCase() ?? 'Not recorded'}</dd></div>
          <div><dt>Model &amp; bundle</dt><dd>{shadow.bundle_id ?? 'Not recorded'}</dd></div>
          <div><dt>Bundle checksum</dt>
            <dd>{shadow.bundle_sha256 ? `${shadow.bundle_sha256.slice(0, 16)}…` : 'Not recorded'}</dd></div>
          <div><dt>Settlement</dt>
            <dd>
              {shadow.settlement_state === 'SETTLED' && shadow.settlement
                ? `Settled ${shadow.settlement.awayRuns ?? '—'}–${shadow.settlement.homeRuns ?? '—'}`
                : 'Pending — no final outcome recorded yet'}
              <small>{shadow.settlement_note}</small>
            </dd></div>
          <div><dt>Wagering authority</dt><dd>None — research shadow only</dd></div>
        </dl>

        {shadow.limitations && shadow.limitations.length > 0 && (
          <details className="v18-limitations">
            <summary>Known limitations ({shadow.limitations.length})</summary>
            <ul>{shadow.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          </details>
        )}
      </section>

      <section className="analysis-section" aria-labelledby="v18-replay-heading">
        <h2 id="v18-replay-heading" className="sr-only">Representative path replay</h2>
        {pathState === 'loading' && <p className="muted" role="status">Loading the persisted path…</p>}
        {pathState === 'unavailable' && (
          <p className="replay__empty" role="status">
            No sequential path is persisted for this research-shadow forecast, so
            there is nothing to replay. Nothing has been reconstructed in its place.
          </p>
        )}
        {pathState === 'ready' && path && (
          <SequentialReplay path={path} shadow={shadow} awayTeam={awayTeam} homeTeam={homeTeam} />
        )}
      </section>

      <section className="analysis-section v18-scenario-note" aria-labelledby="v18-scenario-heading">
        <h2 id="v18-scenario-heading">
          <FlaskConical size={16} aria-hidden="true" /> Why there is no scenario lab here
        </h2>
        <p>
          Changing an assumption would require re-running the V18 model, and AISP is
          not authorized to execute this bundle — its handoff declares
          <code> executionAllowed = false</code>. A what-if for V18 needs a new
          Model Studio forecast artifact, produced there and imported here. The
          Temperature and Park Factor controls in the Gen 1 track recompute Gen 1
          only, and never change this forecast or any official one.
        </p>
        <p className="muted">
          Game id {gameId}. Nothing on this page alters either forecast.
        </p>
      </section>
    </div>
  )
}
