/**
 * The live simulated-game feed.
 *
 * Shows only plays the viewer has already reached: `buildFeed` never returns
 * an event after the current index, so the feed cannot spoil the path. Every
 * line is derived from persisted fields by `describeEvent`; where the record
 * cannot support natural language, the state transition is stated directly.
 */
import { buildFeed, ordinal } from '../../services/replayModel'
import type { ShadowPathEvent } from '../../types/shadow'

export function SimulationEventFeed({ events, index, awayTeam, homeTeam }: {
  events: ShadowPathEvent[]
  index: number
  awayTeam: string
  homeTeam: string
}) {
  const entries = buildFeed(events, index, 8)
  const current = events[Math.min(index, events.length - 1)]

  return (
    <aside className="feed" aria-label="Simulated game feed">
      <header className="feed__head">
        <span className="feed__title">Game feed</span>
        <span className="feed__now">
          {current.half === 'TOP' ? 'Top' : 'Bottom'} {ordinal(current.inning)} ·{' '}
          {current.half === 'TOP' ? awayTeam : homeTeam} batting
        </span>
      </header>

      <ol className="feed__list">
        {entries.map((entry, position) => (
          <li
            key={entry.key}
            className={`feed__item feed__item--${entry.tone}${position === 0 ? ' is-current' : ''}`}
          >
            <span className="feed__pa">{entry.tone === 'inning' || entry.tone === 'change' ? '·' : entry.pa}</span>
            <span className="feed__body">
              <span className="feed__headline">{entry.headline}</span>
              {entry.detail && <span className="feed__detail">{entry.detail}</span>}
            </span>
          </li>
        ))}
      </ol>

      <p className="feed__note">
        Runner movement between bases is inferred from the persisted before/after
        base state. No runner identity, pitch or batted-ball detail is recorded.
      </p>
    </aside>
  )
}
