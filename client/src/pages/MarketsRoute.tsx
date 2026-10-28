/**
 * /markets — the market command center renders at once; the detailed legacy
 * boards (every price row, replay, prediction markets) are not mounted, and
 * make no request, until they are opened.
 */
import { lazy, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CommandMarkets } from '../components/command/CommandMarkets'
import { ShopTheLine } from '../components/command/ShopTheLine'
import { MarketMovers, ValueFinder } from '../features/edge/MarketEdge'
import { useSportSelection } from '../hooks/useSportSelection'
import { LazyDetails } from '../components/command/LazyDetails'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { resolveOperationalDate } from '../services/operationalDate'
import '../broadcast.css'
import { VibeBox } from '../features/crowd/VibeBox'

const Boards = lazy(() => import('./UnifiedMarketsPage').then((m) => ({ default: () => <m.UnifiedMarketsPage auditOnly /> })))

export function MarketsRoute() {
  useBroadcastTheme()
  const [params, setParams] = useSearchParams()
  const { sport: selected } = useSportSelection()
  const initialSport = selected === 'MLB' || selected === 'NFL' ? selected : undefined
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
        <p className="mk-howto"><b>Step 5 · shop the line.</b> This is where you find the best price for a side you like. <b>Value Finder</b> lists US-book prices that pay more than the market's own fair price; <b>+ My Picks</b> puts that exact price and book on your slip (step 7). <b>Shop the Line</b> (first) compares every book on the same side, and <b>Market Movers</b> shows how prices moved today. AISP never places the bet; you place it at that sportsbook.</p>
        {/* Owner 10-26: Shop the Line is the critical read, so it leads, above the scroll line. */}
        {/* Owner 10-27: THE CROWD sits beside Shop the Line, above Best price; Market Movers left, Value Finder right. */}
        <RegionBoundary label="shop the line"><ShopTheLine day={day} side={<VibeBox inline league="nfl" />} /></RegionBoundary>
        <div className="mk-pair">
          <RegionBoundary label="market movers"><MarketMovers day={day} /></RegionBoundary>
          <RegionBoundary label="value finder"><ValueFinder day={day} /></RegionBoundary>
        </div>
        <RegionBoundary label="market command center">
          {/* Keyed so the global sport selector filters the board, not just the URL. */}
          <CommandMarkets key={initialSport ?? 'ALL'} day={day} onDay={onDay} initialSport={initialSport} />
        </RegionBoundary>
        <p className="bx-markets__alt"><Link to={`/markets/board?date=${day}`}>Every game side by side in one table →</Link></p>
        <LazyDetails defaultOpen={deepLink} summary="Detailed boards — every price row, replay, prediction markets and the full RFF Lens">
          <div className="cc-nested"><Boards /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
