import { useCallback } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME } from '../services/queryCache'
import { endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import type { ModelEraData } from './ModelEraBadge'
import type { ShadowForecastView } from '../types/shadow'
import type { GameSimulationData } from '../types/simulation'

/**
 * Which artifact is on screen, and which era actually controls decisions.
 *
 * These are two different facts and the page used to state neither. It carried
 * a "LIVE DATA" banner that meant only "the API answered", beside a saved
 * simulation — and a tab labelled "Official Gen 1" that had been fixed wording
 * since before the funded era moved to V18.
 *
 * So: the selected artifact is described from the artifact itself, the
 * controlling era is read live from the model-era endpoint, and the fact that
 * a replay is not live game coverage is said once, plainly.
 */
export function SimulationProvenance({
  track, shadow, simulation, refreshing, error, onRetry,
}: {
  track: 'gen1' | 'v18'
  shadow: ShadowForecastView | null | undefined
  simulation: GameSimulationData | null | undefined
  refreshing?: boolean
  error?: string | null
  onRetry?: () => void
}) {
  const era = useApiResource<ModelEraData>(
    useCallback((signal: AbortSignal) => endpoints.modelEra(signal), []),
    { key: 'research/model-era', staleTimeMs: STALE_TIME.reference },
  )

  const selected = track === 'v18'
    ? {
      label: shadow?.label ?? 'V18 research shadow',
      engine: shadow?.engine_id ?? 'MLB_V18_WHOLE_GAME',
      identity: shadow?.forecast_id ?? null,
      cutoff: shadow?.evidence_cutoff ?? null,
      basis: 'Research shadow — no wagering authority.',
    }
    : {
      label: 'Gen 1 baseline',
      engine: simulation?.model_label ?? 'AISP_BASELINE',
      identity: simulation?.forecast_id ?? null,
      cutoff: simulation?.generated_at ?? null,
      basis: 'Baseline final-score distribution.',
    }

  const controlling = era.data?.present ? era.data : null
  // The controlling era is a live fact, so a mismatch with the artifact on
  // screen is normal and worth saying rather than hiding: a reader may well be
  // looking at a superseded engine's saved work on purpose.
  const controllingEngine = controlling?.engine_id ?? null
  const differs = Boolean(controllingEngine && controllingEngine !== selected.engine)

  return (
    <section className="sim-provenance" aria-label="Simulation provenance">
      <div className="sim-provenance__row">
        <div>
          <span className="sim-provenance__label">On screen</span>
          <strong>{selected.label}</strong>
          <small>
            {selected.engine}
            {selected.identity && <> · <code>{shortId(selected.identity)}</code></>}
          </small>
          <small>
            {selected.cutoff
              ? `Evidence cutoff ${formatTimestamp(selected.cutoff)}`
              : 'Evidence cutoff not recorded'}
            {' · '}{selected.basis}
          </small>
        </div>

        <div>
          <span className="sim-provenance__label">Controls funded decisions now</span>
          {era.loading && !controlling
            ? <strong className="sim-provenance__muted">reading…</strong>
            : controlling
              ? (
                <>
                  <strong>{controlling.model?.name ?? controlling.engine_id}</strong>
                  <small>
                    {controlling.model_era_id}
                    {controlling.effective_at_utc
                      && <> · effective {formatTimestamp(controlling.effective_at_utc)}</>}
                  </small>
                </>
              )
              : <strong className="sim-provenance__muted">not published</strong>}
          {differs && (
            <small className="sim-provenance__differs">
              This is not the engine on screen. You are viewing {selected.engine}'s saved work.
            </small>
          )}
        </div>
      </div>

      <p className="sim-provenance__note">
        <AlertTriangle size={13} aria-hidden="true" />
        A saved simulation is not live game coverage. Nothing here re-runs a model, and the
        page's own refresh reads stored evidence only.
        {refreshing && ' Re-reading stored evidence…'}
      </p>

      {error && (
        <p className="sim-provenance__error" role="alert">
          {error}. The last stored read remains on screen.
          {onRetry && (
            <button type="button" className="text-button" onClick={onRetry}>
              <RefreshCw size={13} aria-hidden="true" /> Retry
            </button>
          )}
        </p>
      )}
    </section>
  )
}

function shortId(value: string): string {
  return value.length > 30 ? `${value.slice(0, 18)}…${value.slice(-6)}` : value
}
