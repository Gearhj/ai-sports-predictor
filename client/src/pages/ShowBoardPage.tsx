/**
 * /show-board — Show Prep. The day-before Show Board for the 5:00 PM ET
 * recording, separate from Daily Decisions (the governed decision center).
 * `?date=YYYY-MM-DD` selects a slate; without it the next slate with games.
 */
import { useSearchParams } from 'react-router-dom'
import { ShowBoard } from '../components/show/ShowBoard'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { useShowBoard } from '../services/showBoard'
import '../broadcast.css'

const VALID = /^\d{4}-\d{2}-\d{2}$/

export function ShowBoardPage() {
  useBroadcastTheme()
  const [params, setParams] = useSearchParams()
  const raw = params.get('date')
  const date = raw && VALID.test(raw) ? raw : null
  const resource = useShowBoard(date)
  const onDate = (next: string | null) => setParams((old) => {
    const copy = new URLSearchParams(old)
    if (next) copy.set('date', next)
    else copy.delete('date')
    return copy
  }, { replace: true })
  return (
    <div className="bx bx-show">
      <div className="bx__inner">
        <RegionBoundary label="show board">
          <ShowBoard resource={resource} date={date} onDate={onDate} />
        </RegionBoundary>
      </div>
    </div>
  )
}
