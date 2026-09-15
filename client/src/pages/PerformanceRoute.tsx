/**
 * /performance — consolidated model-quality evidence renders at once; the
 * technical diagnostics (offline chronological evaluation, dataset readiness,
 * per-game NFL scorecards, component registry, methodology) are not mounted,
 * and make no request, until opened.
 */
import { lazy } from 'react'
import { CommandPerformance } from '../components/command/CommandPerformance'
import { LazyDetails } from '../components/command/LazyDetails'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { useSportSelection } from '../hooks/useSportSelection'
import '../broadcast.css'

const Diagnostics = lazy(() => import('./UnifiedResearchPage').then((m) => ({ default: m.UnifiedResearchPage })))

export function PerformanceRoute() {
  useBroadcastTheme()
  const { sport } = useSportSelection()
  const initial = sport === 'NFL' || sport === 'MLB' ? sport : undefined
  return (
    <div className="bx bx-performance">
      <div className="bx__inner">
        <RegionBoundary label="model performance">
          <CommandPerformance initialSport={initial} />
        </RegionBoundary>
        <LazyDetails summary="Technical diagnostics and methodology — offline evaluation, dataset readiness, per-game scorecards, model registry">
          <div className="cc-nested"><Diagnostics /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
