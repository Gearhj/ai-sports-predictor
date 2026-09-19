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
  // Every scored sport opens on its own tab: NBA's HISTORICAL channel scores and
  // College Football's prospective scores used to land on the MLB tab instead.
  const initial = sport === 'NFL' || sport === 'MLB' || sport === 'NBA' || sport === 'CFB' ? sport : undefined
  return (
    <div className="bx bx-performance">
      <div className="bx__inner">
        <RegionBoundary label="model performance">
          {/* Keyed so the global sport selector moves the page, not just the URL. */}
          <CommandPerformance key={initial ?? "default"} initialSport={initial} />
        </RegionBoundary>
        <LazyDetails summary="Technical diagnostics and methodology — offline evaluation, dataset readiness, per-game scorecards, model registry">
          <div className="cc-nested"><Diagnostics /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
