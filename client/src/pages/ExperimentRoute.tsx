/**
 * /experiment — the summary renders as soon as its small chunk and the cached
 * ledger are available; the per-era detail page (explanations, scoreboards,
 * era charts: about thirty chunks) loads only as it nears the viewport.
 */
import { Suspense, lazy } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CommandExperiment } from '../components/command/CommandExperiment'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { DeferUntilVisible } from '../components/command/chartKit'

const BankrollPage = lazy(() => import('./BankrollPage').then((module) => ({ default: module.BankrollPage })))

export function ExperimentRoute() {
  const [searchParams] = useSearchParams()
  return (
    <>
      <div className="page bankroll-page" style={{ paddingBottom: 0 }}>
        <RegionBoundary label="experiment summary">
          <CommandExperiment highlight={searchParams.get('position')} />
        </RegionBoundary>
        {/* Record hosts the model-performance reports: how the forecasts scored,
            the official funded positions, and the weekly Monday Morning QB — the
            analysis behind the paper record above. */}
        <nav className="exp-reports" aria-label="Model performance and weekly reports">
          <span className="exp-reports__eyebrow">Model performance &amp; reports</span>
          <ul>
            <li><Link to="/performance"><b>Overview</b><small>One primary evaluation per game, with three findings</small></Link></li>
            <li><Link to="/performance?panel=wagers&sport=NFL"><b>Wager Results</b><small>Official funded positions vs recorded entry estimates</small></Link></li>
            <li><Link to="/performance?panel=mmqb&league=NFL"><b>Monday Morning QB</b><small>The weekly NFL report — every official position checked</small></Link></li>
            <li><Link to="/performance?panel=detailed"><b>Detailed model analysis</b><small>Calibration, over time, model vs market, score error</small></Link></li>
          </ul>
        </nav>
      </div>
      {/* Model-era detail, policy and methodology sit below the ledger and load as they near the viewport. */}
      <DeferUntilVisible minHeight={400}>
        <Suspense fallback={<p className="cc-empty" role="status" style={{ padding: '0 24px' }}>Loading model-era detail…</p>}>
          <BankrollPage withoutSummary />
        </Suspense>
      </DeferUntilVisible>
    </>
  )
}
