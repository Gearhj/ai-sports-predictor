/**
 * Home · Today's games: one section, two alternate views.
 *
 *   - Timeline: the scrolling "one axis" chart (AgendaTimeline, unchanged).
 *   - List: today's games as chronological cards (TodaysGames).
 *
 * Only one view is shown at a time. Desktop and tablet open on Timeline and
 * remember the viewer's choice in this browser; phones use the List only, so
 * the toggle is hidden there. The header summary counts the same today-only
 * rows the List shows.
 */
import { useState, type ReactNode } from 'react'
import type { AgendaEvent } from '../../services/agenda'
import type { SportCode } from '../../services/commandCenter'
import { AgendaTimeline } from './AgendaTimeline'
import { ScheduleSummary, TodaysGames, type ScheduleRow } from './TodaysGames'
import { useWidth } from './chartKit'

export type ScheduleView = 'timeline' | 'list'
export const SCHEDULE_VIEW_KEY = 'aisp.home.schedule-view'
/** Below this section width the List is the only view. */
export const PHONE_WIDTH = 440

function storedView(): ScheduleView {
  try {
    return window.localStorage.getItem(SCHEDULE_VIEW_KEY) === 'list' ? 'list' : 'timeline'
  } catch {
    return 'timeline'
  }
}

export function HomeSchedule({ rows, events, now, idle, decisionsHref, withSport, asOf, loading }: {
  rows: ScheduleRow[]
  events: AgendaEvent[]
  now: number
  idle: Partial<Record<SportCode, string>>
  decisionsHref: string
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
  asOf: ReactNode
  /** Null while the slate is loading; otherwise the message to show instead of the views, if any. */
  loading: string | null
}) {
  const { ref, width } = useWidth<HTMLElement>()
  const [chosen, setChosen] = useState<ScheduleView>(storedView)
  const phone = width < PHONE_WIDTH
  const view: ScheduleView = phone ? 'list' : chosen
  const choose = (next: ScheduleView) => {
    setChosen(next)
    try { window.localStorage.setItem(SCHEDULE_VIEW_KEY, next) } catch { /* private mode: keep it for this visit */ }
  }

  return (
    <section className="cc-agenda-panel tg" aria-labelledby="tg-title" ref={ref}>
      <header>
        <div>
          <h2 id="tg-title">Today’s games</h2>
          <p className="tg-subtitle">Game times and AISP positions · Eastern Time</p>
        </div>
        <div className="tg-tools">
          {!phone && (
            <div className="tg-toggle" role="group" aria-label="Schedule view">
              {(['timeline', 'list'] as const).map((v) => (
                <button key={v} type="button" aria-pressed={view === v} className={view === v ? 'is-on' : ''} onClick={() => choose(v)}>
                  {v === 'timeline' ? 'Timeline' : 'List'}
                </button>
              ))}
            </div>
          )}
          {asOf}
        </div>
      </header>
      {loading != null
        ? <p className="cc-empty">{loading}</p>
        : (
          <>
            <ScheduleSummary rows={rows} />
            {view === 'timeline'
              ? <AgendaTimeline events={events} now={now} idle={idle} decisionsHref={decisionsHref} />
              : <TodaysGames rows={rows} withSport={withSport} summary={false} />}
          </>
        )}
    </section>
  )
}
