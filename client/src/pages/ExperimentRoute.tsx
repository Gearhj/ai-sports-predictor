/**
 * /experiment — the summary renders as soon as its small chunk and the cached
 * ledger are available; the per-era detail page (explanations, scoreboards,
 * era charts: about thirty chunks) loads underneath without holding it back.
 */
import { Suspense, lazy } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CommandExperiment } from '../components/command/CommandExperiment'
import { RegionBoundary } from '../components/command/RegionBoundary'

const BankrollPage = lazy(() => import('./BankrollPage').then((module) => ({ default: module.BankrollPage })))

export function ExperimentRoute() {
  const [searchParams] = useSearchParams()
  return (
    <>
      <div className="page bankroll-page" style={{ paddingBottom: 0 }}>
        <RegionBoundary label="experiment summary">
          <CommandExperiment highlight={searchParams.get('position')} />
        </RegionBoundary>
      </div>
      <Suspense fallback={<p className="cc-empty" role="status" style={{ padding: '0 24px' }}>Loading model-era detail…</p>}>
        <BankrollPage withoutSummary />
      </Suspense>
    </>
  )
}
