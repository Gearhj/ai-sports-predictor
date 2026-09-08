/**
 * The scorebug across the top of the theater.
 *
 * Reads as one composition with the field beneath it: same dark ground, same
 * accent colours, no card border between them. Every value is the persisted
 * state after the plate appearance now being shown.
 */
import { basesFromMask } from '../../services/replayModel'
import type { ShadowPathEvent } from '../../types/shadow'

const STATUS_LABEL: Record<string, string> = {
  PREGAME: 'Pregame',
  IN_PROGRESS: 'In progress',
  EXTRA_INNINGS: 'Extra innings',
  FINAL: 'Final',
}

export function BroadcastScorebug({
  event, awayTeam, homeTeam, accentAway, accentHome, status, index, total, pitcher,
}: {
  event: ShadowPathEvent
  awayTeam: string
  homeTeam: string
  accentAway: string
  accentHome: string
  status: 'PREGAME' | 'IN_PROGRESS' | 'EXTRA_INNINGS' | 'FINAL'
  index: number
  total: number
  pitcher: { label: string; sub: string }
}) {
  const bases = basesFromMask(event.baseMaskAfter)
  const batting = event.half === 'TOP' ? 'away' : 'home'

  return (
    <div className="scorebug" role="group" aria-label="Simulated game scoreboard">
      <div className={`scorebug__team${batting === 'away' ? ' is-batting' : ''}`}>
        <span className="scorebug__chip" style={{ background: accentAway }} aria-hidden="true" />
        <span className="scorebug__abbr">{awayTeam}</span>
        <span className="scorebug__score">{event.awayScoreAfter}</span>
      </div>

      <div className="scorebug__center">
        <div className="scorebug__inning">
          <span className="scorebug__arrow" aria-hidden="true">
            {event.half === 'TOP' ? '▲' : '▼'}
          </span>
          <strong>{event.inning}</strong>
          <span className="sr-only">
            {event.half === 'TOP' ? 'Top' : 'Bottom'} of inning {event.inning}
          </span>
        </div>

        <div className="scorebug__diamond" role="img" aria-label={
          [
            bases.first && 'runner on first',
            bases.second && 'runner on second',
            bases.third && 'runner on third',
          ].filter(Boolean).join(', ') || 'bases empty'
        }>
          <i className={bases.second ? 'is-on' : ''} data-base="second" />
          <i className={bases.third ? 'is-on' : ''} data-base="third" />
          <i className={bases.first ? 'is-on' : ''} data-base="first" />
        </div>

        <div className="scorebug__outs">
          <span className="scorebug__outs-dots" aria-hidden="true">
            {[0, 1, 2].map((slot) => (
              <i key={slot} className={slot < event.outsAfter ? 'is-out' : ''} />
            ))}
          </span>
          <span>{event.outsAfter} {event.outsAfter === 1 ? 'out' : 'outs'}</span>
        </div>
      </div>

      <div className={`scorebug__team scorebug__team--home${batting === 'home' ? ' is-batting' : ''}`}>
        <span className="scorebug__score">{event.homeScoreAfter}</span>
        <span className="scorebug__abbr">{homeTeam}</span>
        <span className="scorebug__chip" style={{ background: accentHome }} aria-hidden="true" />
      </div>

      <div className="scorebug__meta">
        <span className={`scorebug__status scorebug__status--${status.toLowerCase()}`}>
          {STATUS_LABEL[status]}
        </span>
        <span className="scorebug__pa">PA {index + 1} <small>of {total}</small></span>
        <span className="scorebug__batter">
          Batting <strong>#{event.battingOrderPosition}</strong>
        </span>
        <span className="scorebug__pitcher">
          {pitcher.label} <small>{pitcher.sub}</small>
        </span>
      </div>
    </div>
  )
}
