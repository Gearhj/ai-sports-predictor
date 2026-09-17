/**
 * Home · Today's games: the day's schedule as a chronological board.
 *
 * It answers two questions only: when does each game start, and does AISP hold
 * a position on it? There is no time axis. Cards follow start order (row-major
 * in two columns on wide screens, one column otherwise), so the schedule fills
 * the space naturally however the games are spread across the day.
 *
 * Only games whose Eastern date is today appear. A sport with no game today
 * contributes nothing -- no "next game" placeholder from another date. Started
 * and finished games stay, with LIVE / FINAL / DELAYED / POSTPONED beside the
 * scheduled time. Forecast, collection and decision timing live in Today's
 * Agenda, not here.
 */
import { memo } from 'react'
import { Link } from 'react-router-dom'
import { SPORT_COLORS, asSport, gameHref, money, type CoverageGame, type DecisionItem, type Ledger, type SportCode } from '../../services/commandCenter'

const ET_DAY = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' })
const ET_CLOCK = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })

/** The Eastern calendar date (YYYY-MM-DD) of an instant. */
export const etDate = (ms: number) => ET_DAY.format(ms)
export const etClock = (ms: number) => ET_CLOCK.format(ms)

/** More than this many games at exactly the same start share one time label. */
export const GROUP_OVER = 3

export type GameState = 'LIVE' | 'FINAL' | 'DELAYED' | 'POSTPONED' | 'CANCELED' | null

export interface SchedulePosition {
  key: string
  selection: string
  american: number | null
  sportsbook: string | null
  stake: number | null
  /** Only for an officially settled position. */
  result: 'Win' | 'Loss' | 'Push' | null
}

export interface ScheduleRow {
  gameId: string
  sport: SportCode
  away: string
  home: string
  startsAt: number
  state: GameState
  positions: SchedulePosition[]
}

export interface ScheduleSlot {
  at: number
  rows: ScheduleRow[]
}

export function gameState(status: string | null | undefined): GameState {
  const s = (status ?? '').toUpperCase()
  if (['LIVE', 'IN_PROGRESS', 'STARTED', 'IN PROGRESS'].includes(s)) return 'LIVE'
  if (['FINAL', 'COMPLETED', 'COMPLETE', 'CLOSED'].includes(s)) return 'FINAL'
  if (s === 'DELAYED' || s === 'SUSPENDED') return 'DELAYED'
  if (s === 'POSTPONED') return 'POSTPONED'
  if (s === 'CANCELED' || s === 'CANCELLED') return 'CANCELED'
  return null
}

const RESULT: Record<string, SchedulePosition['result']> = { WIN: 'Win', WON: 'Win', LOSS: 'Loss', LOST: 'Loss', PUSH: 'Push' }

function sideName(game: CoverageGame, side: 'away' | 'home'): string {
  const team = game[side]
  if (team?.abbreviation) return team.abbreviation
  const [away, home] = (game.matchup ?? '').split(/\s+@\s+/)
  return (side === 'away' ? away : home) || (side === 'away' ? 'Away' : 'Home')
}

/**
 * Today's rows, in start order. Positions come from the official ledger; a
 * decision's own position record fills in only when the ledger has not been
 * read yet. A result is attached only once the position has settled.
 */
export function buildSchedule({ games, ledger, items, day }: {
  games: CoverageGame[]; ledger: Ledger | null | undefined; items: DecisionItem[]; day: string
}): ScheduleRow[] {
  const byGame = new Map<string, SchedulePosition[]>()
  const add = (gameId: string, p: SchedulePosition) => byGame.set(gameId, [...(byGame.get(gameId) ?? []), p])
  if (ledger) {
    for (const sport of ledger.sports) {
      for (const p of sport.positions) {
        if (/VOID|CANCEL/i.test(p.status ?? '')) continue
        const settled = p.status === 'SETTLED'
        add(p.game_id, {
          key: p.position_id, selection: p.selection ?? 'Selection not recorded', american: p.entry?.american ?? null,
          sportsbook: p.entry?.sportsbook ?? null, stake: p.stake,
          result: settled ? RESULT[(p.result ?? '').toUpperCase()] ?? null : null,
        })
      }
    }
  } else {
    for (const item of items) {
      if (!item.position) continue
      const settled = item.position.status === 'SETTLED'
      add(item.game_id, {
        key: item.position.position_id, selection: item.selection ?? 'Selection not recorded', american: item.american,
        sportsbook: item.sportsbook, stake: item.position.stake,
        result: settled ? RESULT[(item.position.result ?? '').toUpperCase()] ?? null : null,
      })
    }
  }
  return games.flatMap((g) => {
    const sport = asSport(g.league)
    const startsAt = g.starts_at ? Date.parse(g.starts_at) : NaN
    if (!sport || !Number.isFinite(startsAt) || etDate(startsAt) !== day) return []
    return [{ gameId: g.game_id, sport, away: sideName(g, 'away'), home: sideName(g, 'home'), startsAt,
      state: gameState(g.status), positions: byGame.get(g.game_id) ?? [] }]
  }).sort((a, b) => a.startsAt - b.startsAt || a.sport.localeCompare(b.sport) || a.away.localeCompare(b.away))
}

/**
 * One slot per game, except that more than GROUP_OVER games starting at exactly
 * the same instant share a slot (one time label, every game still listed).
 */
export function scheduleSlots(rows: ScheduleRow[]): ScheduleSlot[] {
  const byStart = new Map<number, ScheduleRow[]>()
  for (const row of rows) byStart.set(row.startsAt, [...(byStart.get(row.startsAt) ?? []), row])
  const out: ScheduleSlot[] = []
  for (const [at, list] of byStart) {
    if (list.length > GROUP_OVER) out.push({ at, rows: list })
    else for (const row of list) out.push({ at, rows: [row] })
  }
  return out.sort((a, b) => a.at - b.at)
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const price = (american: number | null) => (american == null ? '' : american > 0 ? `+${american}` : `${american}`)

function PositionBadge({ position }: { position: SchedulePosition }) {
  const terms = [position.selection, price(position.american)].filter(Boolean).join(' ')
  return (
    <p className="tg-position">
      <b>AISP POSITION{position.result ? ` · ${position.result}` : ''}</b>
      <span>{terms}{position.sportsbook ? ` at ${position.sportsbook}` : ''}</span>
      <span>{money(position.stake)} virtual stake</span>
    </p>
  )
}

function GameLine({ row, withTime, withSport }: {
  row: ScheduleRow; withTime: boolean; withSport: (href: string, sport?: 'MLB' | 'NFL') => string
}) {
  const held = row.positions.length > 0
  return (
    <div className={`tg-game${held ? ' is-position' : ''}`} style={{ ['--sport' as string]: SPORT_COLORS[row.sport] }}>
      <Link className="tg-game__link" to={gameHref(row.sport, row.gameId)}>
        {withTime && <time className="tg-time" dateTime={new Date(row.startsAt).toISOString()}>{etClock(row.startsAt)} <small>ET</small></time>}
        <span className="tg-matchup"><span className="tg-sport">{row.sport}</span> · {row.away} at {row.home}</span>
        {row.state && <span className={`tg-state tg-state--${row.state.toLowerCase()}`}>{row.state}</span>}
        {held
          ? row.positions.map((p) => <PositionBadge key={p.key} position={p} />)
          : <span className="tg-none">No AISP position</span>}
      </Link>
      {held && <Link className="tg-decision" to={withSport('/decisions?view=decisions', row.sport === 'MLB' || row.sport === 'NFL' ? row.sport : undefined)}>View decision</Link>}
    </div>
  )
}

export const TodaysGames = memo(function TodaysGames({ rows, withSport = (h) => h }: {
  rows: ScheduleRow[]; withSport?: (href: string, sport?: 'MLB' | 'NFL') => string
}) {
  const positions = rows.reduce((n, r) => n + r.positions.length, 0)
  const slots = scheduleSlots(rows)
  return (
    <>
      <p className="tg-summary">{plural(rows.length, 'game')} · {plural(positions, 'AISP position')}</p>
      {rows.length === 0 && <p className="tg-empty">No games are scheduled for today.</p>}
      {rows.length > 0 && positions === 0 && <p className="tg-note">No AISP positions have been opened yet.</p>}
      {rows.length > 0 && (
        <ol className="tg-list">
          {slots.map((slot) => (
            <li key={`${slot.at}:${slot.rows[0].gameId}`} className={`tg-slot${slot.rows.length > 1 ? ' is-group' : ''}${slot.rows.some((r) => r.positions.length) ? ' is-position' : ''}`}>
              {slot.rows.length > 1
                ? (
                  <>
                    <h3 className="tg-time">{etClock(slot.at)} <small>ET · {slot.rows.length} games</small></h3>
                    <ul className="tg-group">
                      {slot.rows.map((row) => <li key={row.gameId}><GameLine row={row} withTime={false} withSport={withSport} /></li>)}
                    </ul>
                  </>
                )
                : <GameLine row={slot.rows[0]} withTime withSport={withSport} />}
            </li>
          ))}
        </ol>
      )}
    </>
  )
})
