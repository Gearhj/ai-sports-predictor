/**
 * /decisions — the decision command center renders at once; the governance
 * audit (funnel counts, every selection, blockers, explanations, totals,
 * scoreboard) is not mounted, and makes no request, until it is opened.
 */
import { lazy } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CommandDecisions } from '../components/command/CommandDecisions'
import { LazyDetails } from '../components/command/LazyDetails'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { resolveOperationalDate } from '../services/operationalDate'
import '../broadcast.css'

const Audit = lazy(() => import('./MultiSportTodayPage').then((m) => ({ default: () => <m.MultiSportTodayPage auditOnly /> })))

export function DecisionsRoute() {
  useBroadcastTheme()
  const [params] = useSearchParams()
  return (
    <div className="bx bx-decisions">
      <div className="bx__inner">
        <RegionBoundary label="decision command center">
          <CommandDecisions day={resolveOperationalDate(params.get('date'))} />
        </RegionBoundary>
        <LazyDetails summary="Governance evidence and full audit — funnel counts, every selection, blockers and explanations">
          <div className="cc-nested"><Audit /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
