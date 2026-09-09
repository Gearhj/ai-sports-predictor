/**
 * Game analysis for a non-baseball research-shadow event.
 *
 * The MLB analysis page is built around pitchers, bullpens and Statcast. An
 * NFL event has none of those, and rendering that scaffolding produced a page
 * that said "forecast unavailable" while a verified forecast existed. This
 * shows the forecast AISP actually holds, and nothing it does not:
 *
 *  - home, away and tie are separate outcomes; the tie is never redistributed
 *  - market comparison stays unavailable while no governed quote exists
 *  - source-evidence, publication and AISP-import times are distinct facts
 *  - no quarterback, injury, weather or market evidence is claimed
 */
import { useCallback } from 'react'
import { ArrowRight, FlaskConical } from 'lucide-react'
import { Link } from 'react-router-dom'
import { MetricCard, Panel } from './ui'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { DataProvenance, ForecastGame, ShadowBoardData, ShadowEngineView } from '../types/domain'

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function score(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

export function NflResearchAnalysis({ game, slateDate }: {
  game: ForecastGame
  slateDate: string
}) {
  const loader = useCallback(
    (signal: AbortSignal) => endpoints.shadowBoard(slateDate, signal, 'NFL'),
    [slateDate],
  )
  const { data, loading } = useApiResource(loader as (signal: AbortSignal) => Promise<ShadowBoardData & DataProvenance>, {})
  const event = data?.games?.find((item) => item.canonical_game_id === game.id)
  const engine: ShadowEngineView | undefined = event?.engines?.[0]

  return (
    <>
      <Panel
        title={`${game.away_team.name} at ${game.home_team.name}`}
        eyebrow={`NFL · ${formatTimestamp(game.start_time)}`}
        className="section-panel"
        action={<span className="admin-chip"><FlaskConical size={12} /> RESEARCH SHADOW</span>}
      >
        <p className="muted">
          <strong>Research shadow — not executable.</strong> This forecast informs research
          only. It funds no position, carries no wagering authority, and is never an
          official AISP decision.
        </p>

        {!engine && (
          <p className="muted">
            {loading
              ? 'Loading the registered research forecast…'
              : 'No research-shadow forecast is registered for this event.'}
          </p>
        )}

        {engine && (
          <>
            <section className="metric-grid metric-grid--three">
              <MetricCard
                label={`${game.home_team.abbreviation} win`}
                value={pct(engine.home_win_probability)}
                detail={`Expected ${score(engine.expected_home_score)}`}
              />
              <MetricCard
                label={`${game.away_team.abbreviation} win`}
                value={pct(engine.away_win_probability)}
                detail={`Expected ${score(engine.expected_away_score)}`}
              />
              {/* Its own outcome. Never folded into either team. */}
              <MetricCard
                label="Tie"
                value={pct(engine.tie_probability)}
                detail="Own outcome — not redistributed"
              />
            </section>

            <section className="metric-grid metric-grid--three">
              <MetricCard
                label="Expected margin"
                value={engine.expected_margin_home == null ? '—'
                  : `${engine.expected_margin_home >= 0 ? '+' : ''}${engine.expected_margin_home.toFixed(4)}`}
                detail={`${game.home_team.abbreviation} minus ${game.away_team.abbreviation}`}
              />
              <MetricCard
                label="Expected total"
                value={score(engine.expected_total_score)}
                detail="Combined points"
              />
              <MetricCard
                label="Model"
                value={engine.engine_id}
                detail={`${(engine.snapshot_type ?? 'CURRENT').replaceAll('_', ' ')} v${engine.snapshot_version ?? 1} · readiness ${engine.readiness_score}/${engine.readiness_threshold}`}
              />
            </section>

            <dl className="today-answer__meta">
              <div>
                <dt>Source evidence</dt>
                <dd>
                  {engine.source_evidence_utc
                    ? formatTimestamp(engine.source_evidence_utc)
                    : 'Not recorded'}
                </dd>
              </div>
              <div>
                <dt>Forecast published</dt>
                <dd>
                  {engine.forecast_published_utc
                    ? formatTimestamp(engine.forecast_published_utc)
                    : 'Not recorded'}
                </dd>
              </div>
              <div>
                <dt>Imported by AISP</dt>
                <dd>
                  {engine.imported_at_utc
                    ? formatTimestamp(engine.imported_at_utc)
                    : 'Not recorded'}
                </dd>
              </div>
              <div>
                <dt>Market comparison</dt>
                <dd>
                  {engine.decisions?.length
                    ? `${engine.decisions.length} governed quote(s)`
                    : <>Unavailable <small>· no governed sportsbook quote exists</small></>}
                </dd>
              </div>
            </dl>

            {engine.evidence_basis?.note && (
              <p className="muted">
                <strong>Early-season baseline.</strong> {engine.evidence_basis.note}
                {' '}No quarterback, injury, weather or market evidence entered this model.
              </p>
            )}

            <Link className="lab-game__action" to={`/shadow-board?date=${slateDate}&sport=NFL`}>
              Open the NFL research slate <ArrowRight size={13} aria-hidden="true" />
            </Link>
          </>
        )}
      </Panel>
    </>
  )
}
