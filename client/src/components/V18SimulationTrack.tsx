/**
 * The V18 research-shadow track: the Monte Carlo Diamond Theater.
 *
 * Order is deliberate. The theater comes first and carries the population
 * summary as overlays around the park, so the field is the page rather than a
 * widget beneath a wall of metrics. Analysis follows below it, and everything
 * technical — engine identity, checksums, lineup caveats, limitations — sits
 * in collapsed disclosures.
 *
 * Nothing on this page runs a simulation, mutates evidence or calls a
 * provider, and no Gen 1 value ever appears here.
 */
import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { DiamondTheaterShell } from './theater/DiamondTheaterShell'
import { InningLineScore, PopulationPanel } from './theater/InningLineScore'
import { PlayGeometryView } from './theater/PlayGeometryView'
import { PopulationView } from './theater/PopulationView'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { PlayGeometryPayload, ShadowForecastView, ShadowPath } from '../types/shadow'

/** Which visualisation of the same V18 evidence the reader asked for. */
export type V18View = 'broadcast' | 'geometry' | 'population'

export function V18SimulationTrack({ shadow, awayTeam, homeTeam, gameId, view = 'broadcast' }: {
  shadow: ShadowForecastView
  awayTeam: string
  homeTeam: string
  gameId: string
  view?: V18View
}) {
  const [path, setPath] = useState<ShadowPath | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading')
  const [geometry, setGeometry] = useState<PlayGeometryPayload | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let live = true
    endpoints
      .shadowForecastGeometry(shadow.forecast_id, controller.signal)
      .then((payload) => {
        if (!live) return
        // Fail closed: only a payload that declares the research-shadow display
        // authority and no execution/wagering authority may ever be drawn.
        const usable = payload?.schema_version === 'AISP_MLB_V18_PLAY_GEOMETRY_VIEW_V1'
          && payload.display_authority === 'RESEARCH_SHADOW_PLAY_GEOMETRY'
          && payload.wagering_authority === false
          && payload.execution_allowed_in_aisp === false
          && Array.isArray(payload.events) && payload.events.length > 0
        setGeometry(usable ? payload : null)
      })
      .catch(() => { if (live) setGeometry(null) })
    return () => { live = false; controller.abort() }
  }, [shadow.forecast_id])

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
        setState(usable ? 'ready' : 'unavailable')
      })
      .catch(() => { if (live) { setPath(null); setState('unavailable') } })
    return () => { live = false; controller.abort() }
  }, [shadow.forecast_id])

  const events = path?.paths[0] ?? []

  return (
    <div className="v18-track">
      {view !== 'population' && state === 'loading' && (
        <p className="theater-empty" role="status">Loading the persisted simulated path…</p>
      )}
      {view !== 'population' && state === 'unavailable' && (
        <p className="theater-empty" role="status">
          No sequential path is persisted for this research-shadow forecast, so there
          is nothing to replay. Nothing has been reconstructed in its place.
        </p>
      )}

      {view === 'population' && (
        <PopulationView
          forecastId={shadow.forecast_id}
          shadow={shadow}
          awayTeam={awayTeam}
          homeTeam={homeTeam}
          gameId={gameId}
        />
      )}

      {view === 'geometry' && (
        geometry
          ? (
            <PlayGeometryView
              geometry={geometry}
              shadow={shadow}
              awayTeam={awayTeam}
              homeTeam={homeTeam}
            />
          )
          : (
            <p className="theater-empty" role="status">
              No checksum-verified play-geometry supplement is registered for this
              forecast, so there is no modelled geometry to draw. The Broadcast
              Replay remains available and nothing has been synthesised here.
            </p>
          )
      )}

      {view === 'broadcast' && state === 'ready' && path && (
        <DiamondTheaterShell
          path={path}
          shadow={shadow}
          awayTeam={awayTeam}
          homeTeam={homeTeam}
        />
      )}

      {view !== 'population' && state === 'ready' && path && (
        <>
          <PopulationPanel population={shadow.population} />
          <InningLineScore events={events} awayTeam={awayTeam} homeTeam={homeTeam} />
        </>
      )}

      <section className="analysis-section v18-footnotes" aria-labelledby="v18-footnotes-heading">
        <h2 id="v18-footnotes-heading" className="sr-only">Provenance and limitations</h2>

        <div className="v18-footnotes__row">
          <Link className="v18-footnotes__compare" to={`/games/${gameId}`}>
            Compare with the official Gen 1 forecast <ArrowRight size={13} aria-hidden="true" />
          </Link>
          <span className="v18-footnotes__scenario" aria-disabled="true">
            V18 what-if scenarios require a new Model Studio simulation artifact.
          </span>
        </div>

        <details className="v18-disclosure">
          <summary>How this simulation was produced</summary>
          <dl>
            <div><dt>Engine</dt><dd>{shadow.engine_id} · research shadow</dd></div>
            <div><dt>Bundle</dt><dd>{shadow.bundle_id ?? 'Not recorded'}</dd></div>
            <div>
              <dt>Bundle checksum</dt>
              <dd className="mono">{shadow.bundle_sha256 ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Evaluation contract</dt>
              <dd>{shadow.evaluation_contract ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Forecast produced</dt>
              <dd>{shadow.generated_at ? formatTimestamp(shadow.generated_at) : 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Evidence cutoff</dt>
              <dd>{shadow.evidence_cutoff ? formatTimestamp(shadow.evidence_cutoff) : 'Not recorded'}</dd>
            </div>
            <div><dt>Master seed</dt><dd>{shadow.master_seed}</dd></div>
            <div>
              <dt>Deterministic replay</dt>
              <dd>{shadow.deterministic_replay_verified ? 'Verified' : 'Not verified'}</dd>
            </div>
            <div>
              <dt>Illegal states / nonterminations</dt>
              <dd>{shadow.illegal_state_count ?? '—'} / {shadow.nontermination_count ?? '—'}</dd>
            </div>
            <div>
              <dt>Path digest</dt>
              <dd className="mono">{shadow.path_digest ?? 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Lineup state</dt>
              <dd>
                {shadow.lineup_modes?.join(' · ').replaceAll('_', ' ').toLowerCase()
                  ?? 'Not recorded'}
              </dd>
            </div>
            <div>
              <dt>Settlement</dt>
              <dd>
                {shadow.settlement_state === 'SETTLED' && shadow.settlement
                  ? `Settled ${shadow.settlement.awayRuns ?? '—'}–${shadow.settlement.homeRuns ?? '—'}`
                  : 'Pending — no final outcome recorded yet'}
              </dd>
            </div>
            <div><dt>Wagering authority</dt><dd>None — research shadow only</dd></div>
          </dl>
          <p className="v18-disclosure__note">{shadow.settlement_note}</p>
        </details>

        {shadow.limitations && shadow.limitations.length > 0 && (
          <details className="v18-disclosure">
            <summary>Known limitations ({shadow.limitations.length})</summary>
            <ul>{shadow.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            <p className="v18-disclosure__note">
              Runner identity, pitch sequences, batted-ball locations and defensive
              plays are not persisted and are never drawn. Runner movement between
              bases is inferred from the recorded before/after base state.
            </p>
          </details>
        )}
      </section>
    </div>
  )
}
