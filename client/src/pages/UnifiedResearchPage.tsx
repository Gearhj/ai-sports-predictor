import { useCallback } from 'react'
import { PageHeader } from '../components/ui'
import { ModelPerformance } from '../components/nfl/ModelPerformance'
import { useSportSelection } from '../hooks/useSportSelection'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import type { NflResearchResults } from '../types/nfl'
import { PerformancePage } from './PerformancePage'
import { HistoricalResearchPage } from './HistoricalResearchPage'

function NflEvidence({historical}:{historical:boolean}) {
  const resource=useApiResource<NflResearchResults & { data_mode?: 'live' }>(useCallback((signal:AbortSignal)=>endpoints.nflResearch(signal),[]))
  return <section className="sport-research-section" aria-label="NFL research evidence">
    <h2>NFL {historical ? 'historical research' : 'model performance'}</h2>
    <p>Development evidence is separate from prospective performance. NFL remains research-only and cannot create a position.</p>
    {resource.data ? <ModelPerformance results={resource.data}/> : <p role="status">{resource.state==='loading' ? 'Loading imported NFL studies…' : 'NFL study evidence is unavailable.'}</p>}
  </section>
}
export function UnifiedResearchPage({historical=false}:{historical?:boolean}) {
  const {sport}=useSportSelection()
  return <div className="page unified-research">
    <PageHeader eyebrow={historical ? 'HISTORICAL RESEARCH' : 'MODEL PERFORMANCE'} title={historical ? 'The evidence behind the models' : 'Forecasts measured, not assumed'} description="Sport-specific evidence stays separate. Historical studies are not a live betting record."/>
    {(sport==='ALL'||sport==='NFL') && <NflEvidence historical={historical}/>}
    {(sport==='ALL'||sport==='MLB') && <section aria-label="MLB research evidence"><h2>MLB</h2>{historical ? <HistoricalResearchPage/> : <PerformancePage/>}</section>}
    {!['ALL','MLB','NFL'].includes(sport) && <p>{sport} research is planned; no results have been published.</p>}
  </div>
}
