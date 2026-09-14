/**
 * The scoreboard, rendered identically wherever a game appears.
 *
 * ONE COMPONENT FOR FIVE SURFACES
 * -------------------------------
 * Daily Decisions, the experiment's open and settled positions, a parlay
 * ticket's legs, game analysis and a historical wager all show the same thing.
 * Five implementations would drift, and drift here runs one way: toward a score
 * that looks more current and more certain than the evidence behind it.
 *
 * THE SCORE IS BIG; THE PROVENANCE IS ONE CLICK AWAY
 * --------------------------------------------------
 * A reader glancing at a phone wants "PIT 20 – ATL 13, FINAL" and nothing else.
 * The period breakdown, the source, the three separate timestamps and the
 * reconciliation status are all real and all belong somewhere other than the
 * first line, so they live in a disclosure.
 *
 * WHAT IT WILL NOT RENDER
 * -----------------------
 * *   **A clock nobody published.** `clock_available` false means the source has
 *     no clock field. The component shows the period alone rather than 0:00,
 *     which would read as the end of a quarter.
 * *   **A retrieval time dressed as a score time.** When the source supplies no
 *     timestamp the line says "Last checked", and the disclosure says source
 *     latency is unknown. "Score as of" appears only when a source actually
 *     said so.
 * *   **A fresh-looking stale score.** During an outage the last known score
 *     stays, with its real age and a DELAYED or UNAVAILABLE label. Reloading
 *     the page changes none of that.
 * *   **A settled wager.** FINAL is what the score feed says. Settlement is a
 *     separate record and the component says so rather than implying a payout.
 */
import { useState } from 'react'
import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react'
import type { GameScoreboard as Scoreboard } from '../../types/scoreboard'

function clockLabel(seconds: number | null | undefined): string | null {
  if (seconds == null) return null
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

/** "Q3, 6:42 remaining", "Top 9th", "Final" — only from what was supplied. */
export function progressLabel(game: Scoreboard): string | null {
  if (game.status === 'FINAL') return game.status_detail || 'Final'
  // Named rather than left blank. A row with two dashes and no word beside
  // them reads as a score that failed to load; "Scheduled" says the game has
  // not started, which is a different thing and the true one.
  if (game.status === 'SCHEDULED') return 'Scheduled'
  if (game.status && game.status !== 'IN_PROGRESS') {
    // Delayed, suspended, postponed, cancelled: the source's own word is the
    // most informative thing available and is never replaced by a guess.
    return game.status_detail || game.status.replace('_', ' ').toLowerCase()
  }
  const period = game.period
  if (!period?.label) return game.status_detail || null
  const state = period.state && period.state !== 'null' ? `${period.state} ` : ''
  const clock = game.clock_available ? clockLabel(game.clock_seconds_remaining) : null
  const base = `${state}${period.label}`.trim()
  return clock ? `${base}, ${clock} remaining` : base
}

function freshnessTone(state: string | undefined): string {
  if (state === 'DELAYED') return 'scorebd__fresh--warn'
  if (state === 'UNAVAILABLE') return 'scorebd__fresh--bad'
  return 'scorebd__fresh--ok'
}

function PeriodTable({ game }: { game: Scoreboard }) {
  const periods = game.periods ?? []
  if (periods.length === 0) return null
  const away = game.teams?.away?.abbreviation ?? 'Away'
  const home = game.teams?.home?.abbreviation ?? 'Home'
  return (
    <div className="bx-scroll">
      <table className="scorebd__periods" data-testid="scoreboard-periods">
        <caption className="sr-only">Score by period</caption>
        <thead>
          <tr>
            <th scope="col"></th>
            {periods.map((period) => (
              <th key={period.label ?? period.number} scope="col">{period.label}</th>
            ))}
            <th scope="col">T</th>
          </tr>
        </thead>
        <tbody>
          {([['away', away], ['home', home]] as const).map(([side, abbreviation]) => (
            <tr key={side}>
              <th scope="row">{abbreviation}</th>
              {periods.map((period) => {
                const played = side === 'away' ? period.away_played : period.home_played
                const value = side === 'away' ? period.away : period.home
                return (
                  <td
                    key={`${side}-${period.label ?? period.number}`}
                    className={played ? 'bx-num' : 'bx-num scorebd__unplayed'}
                    // A dash is not a zero. The title says which this is, so a
                    // reader never has to guess whether nobody batted or
                    // nobody scored.
                    title={played ? undefined : 'Not played'}
                  >
                    {played ? value : '–'}
                  </td>
                )
              })}
              <td className="bx-num scorebd__total">
                {side === 'away' ? game.away_score : game.home_score}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function GameScoreboard({
  game, compact = false, onRefresh, children,
}: {
  game: Scoreboard | null | undefined
  /** Inline in a dense list: score and status only, no disclosure. */
  compact?: boolean
  onRefresh?: () => void
  /** The recorded selection, rendered beside the score by the caller. */
  children?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)

  if (!game || !game.available) {
    return (
      <div className="scorebd scorebd--absent" data-testid="scoreboard-unavailable">
        <span className="bx-dim">
          {game?.unavailable_reason ?? 'No score has been collected for this game yet.'}
        </span>
        {children}
      </div>
    )
  }

  const away = game.teams?.away?.abbreviation ?? '—'
  const home = game.teams?.home?.abbreviation ?? '—'
  const progress = progressLabel(game)
  const freshness = game.freshness
  const started = game.status !== 'SCHEDULED'

  return (
    <div
      className={compact ? 'scorebd scorebd--compact' : 'scorebd'}
      data-testid={`scoreboard-${game.game_id}`}
    >
      <div className="scorebd__line">
        <span className="scorebd__score" data-testid="scoreboard-score">
          <b>{away}</b>{' '}
          <b className="bx-num">{started ? game.away_score ?? '–' : '–'}</b>
          <span className="scorebd__dash">–</span>
          <b className="bx-num">{started ? game.home_score ?? '–' : '–'}</b>{' '}
          <b>{home}</b>
        </span>
        {progress && (
          <span className="scorebd__progress" data-testid="scoreboard-progress">
            {progress}
          </span>
        )}
        {game.status === 'FINAL' && <span className="bx-chip">FINAL</span>}
        {game.status === 'IN_PROGRESS' && (
          <span className="bx-chip bx-chip--good" data-testid="scoreboard-live">LIVE</span>
        )}
      </div>

      {/* Baseball situation, only where the source actually supplied it. */}
      {game.status === 'IN_PROGRESS' && game.situation && (
        <p className="scorebd__situation" data-testid="scoreboard-situation">
          {game.situation.outs != null && `${game.situation.outs} out`}
          {game.situation.outs != null && game.situation.outs !== 1 && 's'}
          {game.situation.balls != null && game.situation.strikes != null
            && ` · ${game.situation.balls}-${game.situation.strikes}`}
          {game.situation.bases_occupied.length > 0
            ? ` · runners on ${game.situation.bases_occupied.join(', ')}`
            : ' · bases empty'}
        </p>
      )}

      {children}

      {freshness && (
        <p
          className={`scorebd__fresh ${freshnessTone(freshness.state)}`}
          data-testid="scoreboard-freshness"
          role={freshness.state === 'UNAVAILABLE' ? 'note' : undefined}
        >
          {freshness.state === 'UNAVAILABLE' && (
            <AlertTriangle size={12} aria-hidden="true" />
          )}
          {freshness.detail}
          {onRefresh && (
            <button
              type="button"
              className="scorebd__refresh"
              onClick={onRefresh}
              data-testid="scoreboard-refresh"
              // Reads AISP's own cache; it cannot reach a provider, so the
              // label promises a re-read rather than a re-collection.
              title="Re-read AISP's stored score. This makes no provider request."
            >
              <RefreshCw size={11} aria-hidden="true" /> Refresh
            </button>
          )}
        </p>
      )}

      {!compact && (
        <div className="scorebd__more">
          <button
            type="button"
            className="scorebd__toggle"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            data-testid="scoreboard-toggle"
          >
            <span>Period breakdown and source</span>
            <ChevronDown
              size={13} aria-hidden="true"
              className={open ? 'scorebd__chevron is-open' : 'scorebd__chevron'}
            />
          </button>
          {open && (
            <div className="scorebd__panel" data-testid="scoreboard-detail">
              <PeriodTable game={game} />
              {game.periods_note && (
                <p className="scorebd__note" data-testid="scoreboard-periods-note">
                  {game.periods_note}
                </p>
              )}
              {game.periods_reconcile === true && (
                <p className="scorebd__note">
                  The period scores add up to the final.
                </p>
              )}
              {game.clock_available === false && game.status === 'IN_PROGRESS' && (
                <p className="scorebd__note" data-testid="scoreboard-no-clock">
                  This source publishes no game clock, so no time remaining is shown.
                </p>
              )}
              <dl className="scorebd__facts">
                <div>
                  <dt>Source</dt>
                  <dd>{game.source?.name}</dd>
                </div>
                <div>
                  <dt>{game.freshness?.source_timestamp_available
                    ? 'Score as of' : 'Score timestamp'}</dt>
                  <dd>
                    {game.source?.source_observed_at
                      ?? <span className="bx-dim">not published by this source</span>}
                  </dd>
                </div>
                <div>
                  <dt>Received by AISP</dt>
                  <dd>{game.source?.retrieved_at ?? '—'}</dd>
                </div>
                <div>
                  <dt>Last successful check</dt>
                  <dd>{game.source?.last_checked_at ?? '—'}</dd>
                </div>
              </dl>
              <p className="scorebd__note">{game.source?.timestamp_note}</p>
              <p className="scorebd__note">{game.settlement_note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
