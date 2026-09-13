import { useCallback } from 'react'
import { PageHeader } from '../components/ui'
import { ModelPerformance } from '../components/nfl/ModelPerformance'
import { NflProspectiveSection } from '../components/nfl/NflProspectiveResults'
import { useSportSelection } from '../hooks/useSportSelection'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { apiRequest, endpoints } from '../services/api'
import type { NflResearchResults } from '../types/nfl'
import type { NflProspectiveResults } from '../types/nflProspective'
import { PerformancePage } from './PerformancePage'
import { HistoricalResearchPage } from './HistoricalResearchPage'
import '../components/nfl/nflProspective.css'

/**
 * Prospective results sit ABOVE the development study, deliberately.
 *
 * A benchmark study says how a model scored on data it was built against. A
 * prospective result says how it scored on a game nobody had played yet. The
 * second is worth far more per observation and there is far less of it, so it
 * leads -- and its sample size leads with it, because one game read without
 * its n is how a single correct pick becomes "the model works".
 */
function NflProspective() {
  const resource = useApiResource<NflProspectiveResults>(
    useCallback((signal: AbortSignal) =>
      apiRequest<NflProspectiveResults>('/research/nfl-prospective', {}, signal), []),
    { key: 'research/nfl-prospective', staleTimeMs: STALE_TIME.history },
  )
  if (!resource.data) {
    return (
      <p role="status">
        {resource.state === 'loading'
          ? 'Loading scored NFL forecasts…'
          : resource.error ?? 'Scored NFL forecasts are unavailable.'}
      </p>
    )
  }
  return <NflProspectiveSection data={resource.data} />
}

function NflEvidence({ historical }: { historical: boolean }) {
  const resource = useApiResource<NflResearchResults & { data_mode?: 'live' }>(
    useCallback((signal: AbortSignal) => endpoints.nflResearch(signal), []),
    { key: 'research/nfl-evidence', staleTimeMs: STALE_TIME.history },
  )
  return (
    <section className="sport-research-section" aria-label="NFL research evidence">
      <h2>NFL {historical ? 'historical research' : 'model performance'}</h2>
      <p>
        Development evidence is separate from prospective performance. NFL remains research-only
        and cannot create a position.
      </p>
      {!historical && <NflProspective />}
      {resource.data
        ? <ModelPerformance results={resource.data} />
        : <p role="status">
          {resource.state === 'loading' ? 'Loading imported NFL studies…' : 'NFL study evidence is unavailable.'}
        </p>}
    </section>
  )
}

export function UnifiedResearchPage({ historical = false }: { historical?: boolean }) {
  const { sport } = useSportSelection()
  return (
    <div className="page unified-research">
      <PageHeader
        eyebrow={historical ? 'HISTORICAL RESEARCH' : 'MODEL PERFORMANCE'}
        title={historical ? 'The evidence behind the models' : 'Forecasts measured, not assumed'}
        description="Sport-specific evidence stays separate. Historical studies are not a live betting record."
      />
      {(sport === 'ALL' || sport === 'NFL') && <NflEvidence historical={historical} />}
      {(sport === 'ALL' || sport === 'MLB') && (
        <section aria-label="MLB research evidence">
          <h2>MLB</h2>
          {historical ? <HistoricalResearchPage /> : <PerformancePage />}
        </section>
      )}
      {!['ALL', 'MLB', 'NFL'].includes(sport) && (
        <p>{sport} research is planned; no results have been published.</p>
      )}
    </div>
  )
}
