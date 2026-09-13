/**
 * Official Gen 1 forecast beside any registered research-shadow forecast.
 *
 * Display rules, all enforced here rather than left to the reader:
 *
 *  - The official forecast is always labelled as the official one; the shadow
 *    is always labelled research shadow with no wagering authority.
 *  - Each carries its own timestamp and model identity, so neither can be
 *    mistaken for the other's evidence.
 *  - A difference is shown only when the backend paired the two under the
 *    acceptance contract's pregame timing rule. When it did not, the two are
 *    shown separately with the mismatch stated.
 *  - No superiority language, no recommendation, and no implied edge: a
 *    difference inside the shadow's own Monte Carlo interval is called
 *    simulation-indistinguishable.
 */
import { useCallback } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { endpoints } from '../services/api'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { formatTimestamp } from '../services/format'
import { EnsembleCandidatePanel } from './EnsembleCandidatePanel'
import type { ShadowComparison, ShadowForecastView } from '../types/shadow'

function pct(value: number | null | undefined): string {
  return value == null ? 'Not available' : `${value.toFixed(1)}%`
}

export function ShadowForecastPanel({ gameId }: { gameId: string }) {
  // Through the shared cache, so this panel is fetched once per game however
  // many places ask for it, and a return to the same game renders it from
  // what was already read. A game with no shadow track is the normal case, and
  // an unreachable endpoint must not break the official analysis around it —
  // both still resolve to "no shadow track" rather than an error.
  // The selected candidate arm rides in the URL, so a link to one configuration
  // is shareable and the cache keeps the two arms apart rather than serving one
  // under the other's heading.
  const [searchParams] = useSearchParams()
  const configuration = searchParams.get('configuration')
  const resource = useApiResource<ShadowComparison>(
    useCallback(
      (signal: AbortSignal) => endpoints.gameShadowForecasts(gameId, configuration, signal),
      [gameId, configuration],
    ),
    {
      key: cacheKey('game/shadow-forecasts', { gameId, configuration }),
      staleTimeMs: STALE_TIME.game,
    },
  )
  const payload = resource.data
  const data = payload?.schema_version === 'AISP_MLB_V18_SHADOW_COMPARISON_V1' ? payload : null

  // A payload without a recognisable shadow list is treated as "no shadow
  // track", never as a rendering error: this panel must never be able to take
  // down the official analysis around it.
  const candidate = data?.candidate_comparison ?? null
  if (!data || !Array.isArray(data.shadows) || data.shadows.length === 0) {
    // A candidate can exist for a game that has no registered V18 shadow row.
    // It is still worth showing, with its baseline side reported as absent.
    return candidate
      ? <EnsembleCandidatePanel comparison={candidate} homeTeam={data?.home_team ?? null}
                                cohort={payload?.candidate_cohort ?? null} />
      : null
  }

  return (
    <section className="shadow-compare" id="research-shadow" aria-labelledby="shadow-compare-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">RESEARCH SHADOW</span>
          <h2 id="shadow-compare-heading">Official forecast vs. research challenger</h2>
        </div>
        <p className="shadow-compare__note">{data.notes}</p>
        {data.authority_note && (
          <p className="shadow-compare__note shadow-compare__note--authority" role="note">
            {data.authority_note}
          </p>
        )}
      </div>

      <div className="shadow-compare__grid">
        {data.official && (
          <article className="shadow-card shadow-card--official">
            <header>
              <span className="shadow-card__track">Official · Gen 1</span>
              {/* This badge used to read "Wagering authority", asserted as a
                  constant. Which engine a funded experiment uses is recorded in
                  the champion authority, not decided by this panel, and the
                  simulator beside it reports V18 controlling funded decisions.
                  The badge now names the track; the authority note above says
                  what does and does not grant authority. */}
              <span className="shadow-card__authority">Official forecast track</span>
            </header>
            <p className="shadow-card__value">{pct(data.official.home_win_probability)}</p>
            <p className="shadow-card__caption">
              {data.home_team ?? 'Home'} win probability
            </p>
            <dl className="shadow-card__facts">
              <div><dt>Model</dt><dd>{data.official.model ?? 'Not recorded'}</dd></div>
              <div>
                <dt>Forecast time</dt>
                <dd>
                  {data.official.generated_at
                    ? formatTimestamp(data.official.generated_at)
                    : 'Not recorded'}
                </dd>
              </div>
              <div>
                <dt>Produced before first pitch</dt>
                <dd>{data.official.pregame ? 'Yes' : 'No'}</dd>
              </div>
            </dl>
            <Link className="shadow-card__action" to={`/games/${data.game_id}/simulation?track=gen1`}>
              Explore Gen 1 outcome distribution <ArrowRight size={13} aria-hidden="true" />
            </Link>
          </article>
        )}

        {data.shadows.map((shadow) => (
          <ShadowCard
            key={shadow.forecast_id}
            shadow={shadow}
            homeTeam={data.home_team}
            gameId={data.game_id}
          />
        ))}
      </div>

      {candidate && (
        <EnsembleCandidatePanel
          comparison={candidate}
          homeTeam={data.home_team}
          cohort={payload?.candidate_cohort ?? null}
        />
      )}
    </section>
  )
}

function ShadowCard({ shadow, homeTeam, gameId }: {
  shadow: ShadowForecastView
  homeTeam: string | null
  gameId: string
}) {
  const difference = shadow.difference_vs_official
  return (
    <article className="shadow-card shadow-card--shadow">
      <header>
        <span className="shadow-card__track">Research shadow · {shadow.engine_id}</span>
        <span className="shadow-card__authority shadow-card__authority--none">
          Research package · grants no authority
        </span>
      </header>
      <p className="shadow-card__value">{pct(shadow.home_win_probability)}</p>
      <p className="shadow-card__caption">{homeTeam ?? 'Home'} win probability</p>

      {shadow.paired_with_official && difference != null ? (
        <p className="shadow-card__delta">
          <strong>
            {difference > 0 ? '+' : ''}{difference.toFixed(1)} pts
          </strong>{' '}
          vs. the official forecast
          {shadow.simulation_indistinguishable && (
            <em> — inside this run’s own Monte Carlo interval, so the two are
              simulation-indistinguishable</em>
          )}
        </p>
      ) : (
        <p className="shadow-card__delta shadow-card__delta--unpaired">
          {shadow.pairing_note ?? 'Not paired with the official forecast.'}
        </p>
      )}

      <dl className="shadow-card__facts">
        <div><dt>Bundle</dt><dd>{shadow.bundle_id ?? 'Not recorded'}</dd></div>
        <div>
          <dt>Forecast time</dt>
          <dd>{shadow.generated_at ? formatTimestamp(shadow.generated_at) : 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Produced before first pitch</dt>
          <dd>{shadow.pregame ? 'Yes' : 'No'}</dd>
        </div>
        <div>
          <dt>Simulated paths</dt>
          <dd>
            {shadow.simulation_count ?? 'Not recorded'}
            {shadow.monte_carlo_95_half_width != null && (
              <small> · ±{(shadow.monte_carlo_95_half_width * 100).toFixed(2)} pts (95% sampling)</small>
            )}
          </dd>
        </div>
        <div><dt>Seed</dt><dd>{shadow.master_seed}</dd></div>
        <div>
          <dt>Expected runs</dt>
          <dd>
            {shadow.expected_away_runs?.toFixed(2) ?? '—'} away ·{' '}
            {shadow.expected_home_runs?.toFixed(2) ?? '—'} home
          </dd>
        </div>
      </dl>

      {/* Projected-participant and stale-feature caveats, stated where the
          number is, not buried in a methodology page. */}
      {shadow.lineup_modes?.some((mode) => mode.startsWith('PROJECTED')) && (
        <p className="shadow-card__caveat">
          Lineups are projected from the most recent prior complete lineup, not confirmed.
        </p>
      )}
      {shadow.limitations && shadow.limitations.length > 0 && (
        <details className="shadow-card__limitations">
          <summary>Known limitations ({shadow.limitations.length})</summary>
          <ul>
            {shadow.limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </details>
      )}

      {/* Settlement is stated on the card so a reader never has to infer
          whether the result is in yet. */}
      <p className={`shadow-card__settlement shadow-card__settlement--${shadow.settlement_state.toLowerCase()}`}>
        {shadow.settlement_state === 'SETTLED' && shadow.settlement
          ? `Settled: ${shadow.settlement.awayRuns ?? '—'}–${shadow.settlement.homeRuns ?? '—'}${shadow.settlement.extraInnings ? ' (extra innings)' : ''}`
          : 'Pending — no final outcome recorded yet'}
        <em>{shadow.settlement_note}</em>
      </p>

      {shadow.sequential_path_events > 0 && (
        <Link className="shadow-card__action" to={`/games/${gameId}/simulation?track=v18`}>
          Replay V18 simulated game <ArrowRight size={13} aria-hidden="true" />
        </Link>
      )}

      <p className="shadow-card__lineage">
        {shadow.sequential_path_events > 0
          ? `Sequential path persisted: ${shadow.sequential_path_events} plate appearances with bases, outs, innings, score and pitching changes.`
          : 'No sequential path is persisted for this forecast.'}
        {shadow.path_digest && <> Path digest {shadow.path_digest.slice(0, 12)}…</>}
      </p>
    </article>
  )
}
