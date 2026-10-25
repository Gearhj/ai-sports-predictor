/**
 * Home — the AISP command center.
 *
 * A concise first stop, about one to two viewports: the combined virtual
 * bankroll with its chart, today's decision picture, the four sport bankrolls,
 * and one row of pointers into Games, Markets, Decisions and the Experiment.
 *
 * WHAT MOVED OFF THIS PAGE, AND WHY
 * --------------------------------
 * The featured-matchup card, the full-day timeline, the second slate rail, the
 * decisions summary, a second $1,000 Experiment presentation, the RFF card, the
 * labs list and the freshness panel each reproduced a page that already exists.
 * Stacked together they made Home several pages long with nothing establishing
 * importance. Each still lives on its own page; Home links to them.
 */
import { CommandHome } from '../components/command/CommandHome'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { useSportSelection } from '../hooks/useSportSelection'
import '../broadcast.css'

export function HomePage() {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()
  return (
    <div className="bx bx-home">
      <div className="bx__inner">
        <RegionBoundary label="command center"><CommandHome withSport={withSport} sport={sport} /></RegionBoundary>
      </div>
    </div>
  )
}
