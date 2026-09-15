/**
 * /markets — the market command center renders at once; the detailed legacy
 * boards (every price row, replay, prediction markets) are not mounted, and
 * make no request, until they are opened.
 */
import { lazy, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CommandMarkets } from '../components/command/CommandMarkets'
import { LazyDetails } from '../components/command/LazyDetails'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { resolveOperationalDate } from '../services/operationalDate'
import '../broadcast.css'

const Boards = lazy(() => import('./UnifiedMarketsPage').then((m) => ({ default: () => <m.UnifiedMarketsPage auditOnly /> })))

export function MarketsRoute() {
  useBroadcastTheme()
  const [params, setParams] = useSearchParams()
  const day = resolveOperationalDate(params.get('date'))
  const onDay = useCallback((next: string) => setParams((old) => {
    const copy = new URLSearchParams(old)
    copy.set('date', next)
    return copy
  }, { replace: true }), [setParams])
  // A shared replay / RFF link opens the detailed boards directly.
  const deepLink = Boolean(params.get('view') || params.get('game') || params.get('rff'))
  return (
    <div className="bx bx-markets">
      <div className="bx__inner">
        <RegionBoundary label="market command center">
          <CommandMarkets day={day} onDay={onDay} />
        </RegionBoundary>
        <LazyDetails defaultOpen={deepLink} summary="Detailed boards — every price row, replay, prediction markets and the full RFF Lab">
          <div className="cc-nested"><Boards /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
