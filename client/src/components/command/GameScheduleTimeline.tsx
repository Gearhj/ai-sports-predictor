/**
 * Home · today's games on one axis — a viewer's schedule, not an operations monitor.
 *
 * It answers two questions only: when does each game begin, and has AISP taken
 * a position? One lane per sport, one marker per game (or per group of games
 * starting within ten minutes). Two treatments only:
 *   - scheduled game: quiet outline;
 *   - AISP position: filled accent, a plain "POSITION" label and the stake.
 * Collection, forecast, decision-window and settlement events are deliberately
 * absent; they live in Today's Agenda.
 *
 * The axis covers the game window, not the whole day, with at most ~six hour
 * labels. Labels are packed into rows so they never overprint. A grouped marker
 * reads "3 games · 7:10 PM" and lists its games when opened. Below 600 px the
 * chart becomes a chronological list.
 */
import * as d3 from 'd3'
import { memo, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { SPORT_CODES, SPORT_COLORS, SPORT_NAMES, gameHref, type SportCode } from '../../services/commandCenter'
import { ChartTable, useWidth } from './chartKit'

const MIN = 60_000
const GROUP_WINDOW = 10 * MIN
const et = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(ms)
const etHour = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric' }).format(ms)

export interface ScheduleGame {
  gameId: string
  sport: SportCode
  matchup: string
  startsAt: number
  /** Virtual stake when AISP holds (or held) a position on this game. */
  position: { stake: number | null } | null
}

export interface ScheduleGroup {
  key: string
  sport: SportCode
  at: number
  games: ScheduleGame[]
  positions: number
}

/** Games in one sport that start within ten minutes of the group's first game. */
export function groupGames(games: ScheduleGame[]): ScheduleGroup[] {
  const out: ScheduleGroup[] = []
  for (const sport of SPORT_CODES) {
    let current: ScheduleGroup | null = null
    for (const game of games.filter((g) => g.sport === sport).sort((a, b) => a.startsAt - b.startsAt || a.matchup.localeCompare(b.matchup))) {
      if (current && game.startsAt - current.at <= GROUP_WINDOW) {
        current.games.push(game)
      } else {
        current = { key: `${sport}:${game.startsAt}:${game.gameId}`, sport, at: game.startsAt, games: [game], positions: 0 }
        out.push(current)
      }
    }
  }
  for (const group of out) group.positions = group.games.filter((g) => g.position).length
  return out
}

/** Whole-hour ticks at a step that keeps the count at or below `maxTicks`. */
export function hourTicks(lo: number, hi: number, maxTicks = 6): number[] {
  const hours = Math.max(1, (hi - lo) / 3_600_000)
  const step = [1, 2, 3, 4, 6, 12].find((s) => hours / s <= maxTicks) ?? 12
  const start = new Date(lo)
  start.setMinutes(0, 0, 0)
  const ticks: number[] = []
  for (let t = start.getTime(); t <= hi; t += 3_600_000) {
    const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(t))
    if (t >= lo && hour % step === 0) ticks.push(t)
  }
  return ticks.slice(0, maxTicks)
}

const stakeText = (game: ScheduleGame) => (game.position?.stake != null ? `POSITION · $${game.position.stake.toFixed(2)}` : 'POSITION')

function groupLines(group: ScheduleGroup): string[] {
  if (group.games.length === 1) {
    const game = group.games[0]
    return [game.matchup, et(game.startsAt), game.position ? stakeText(game) : 'No position']
  }
  return [`${group.games.length} games · ${et(group.at)}`,
    group.positions ? `${group.positions} POSITION${group.positions === 1 ? '' : 'S'}` : 'No position']
}

interface Placed extends ScheduleGroup { x: number; lx: number; row: number }

function packRows(groups: ScheduleGroup[], x: (ms: number) => number, bounds: [number, number]): { placed: Placed[]; rows: number } {
  const rowEnds: number[] = []
  const placed = groups.map((group) => {
    const width = Math.max(...groupLines(group).map((l) => l.length)) * 6.4 + 12
    const cx = x(group.at)
    // The label stays inside the lane; the dot stays at the true start time.
    const lx = Math.min(Math.max(cx, bounds[0] + width / 2), bounds[1] - width / 2)
    const left = lx - width / 2
    let row = rowEnds.findIndex((end) => left > end + 8)
    if (row === -1) { row = rowEnds.length; rowEnds.push(0) }
    rowEnds[row] = lx + width / 2
    return { ...group, x: cx, lx, row }
  })
  return { placed, rows: Math.max(1, rowEnds.length) }
}

export const GameScheduleTimeline = memo(function GameScheduleTimeline({ games, now, idle = {}, withSport = (h) => h }: {
  games: ScheduleGame[]
  now: number
  /** "Next: …" for a sport with no game today. */
  idle?: Partial<Record<SportCode, string>>
  withSport?: (href: string) => string
}) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const navigate = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  const groups = useMemo(() => groupGames(games), [games])
  const sports = SPORT_CODES.filter((s) => groups.some((g) => g.sport === s) || idle[s])
  const narrow = width < 600

  if (games.length === 0 && sports.length === 0) {
    return <div ref={ref}><p className="cc-empty">No game is scheduled today.</p></div>
  }

  if (narrow) {
    const ordered = [...games].sort((a, b) => a.startsAt - b.startsAt)
    return (
      <div ref={ref} className="hs-list">
        <ol aria-label="Today's games in start order">
          {ordered.map((game) => (
            <li key={game.gameId} className={game.position ? 'has-position' : ''} style={{ ['--sport' as string]: SPORT_COLORS[game.sport] }}>
              <time>{et(game.startsAt)}</time>
              <a href={withSport(gameHref(game.sport, game.gameId))} onClick={(e) => { e.preventDefault(); navigate(withSport(gameHref(game.sport, game.gameId))) }}>{game.matchup}</a>
              <small>{SPORT_NAMES[game.sport]}{game.startsAt <= now ? ' · started' : ''}</small>
              {game.position && <b className="hs-badge">{stakeText(game)}</b>}
            </li>
          ))}
        </ol>
        {SPORT_CODES.filter((s) => idle[s] && !games.some((g) => g.sport === s)).map((s) => (
          <p key={s} className="cc-muted-text">{SPORT_NAMES[s]} · {idle[s]}</p>
        ))}
      </div>
    )
  }

  const labelW = 112
  const right = 16
  const starts = games.map((g) => g.startsAt)
  const first = d3.min(starts) ?? now
  const last = d3.max(starts) ?? now
  const lo = first - 45 * MIN
  const hi = Math.max(last + 75 * MIN, lo + 3 * 3_600_000)
  const x = d3.scaleTime().domain([lo, hi]).range([labelW, Math.max(labelW + 120, width - right)])
  const ticks = hourTicks(lo, hi)
  const axisH = 22
  const rowH = 44
  const markerY = 16
  const lanes = sports.map((sport) => {
    const { placed, rows } = packRows(groups.filter((g) => g.sport === sport), (ms) => x(ms), [labelW - 4, width - 4])
    return { sport, placed, height: markerY + 14 + rows * rowH }
  })
  const tops = lanes.reduce<number[]>((acc, lane, i) => [...acc, i === 0 ? axisH : acc[i - 1] + lanes[i - 1].height + 6], [])
  const height = (lanes.length ? tops[tops.length - 1] + lanes[lanes.length - 1].height : axisH) + 24
  const openGroup = groups.find((g) => g.key === open) ?? null
  const nowInside = now >= lo && now <= hi

  return (
    <div ref={ref} className="cc-chart hs-timeline">
      <svg width={width} height={height} role="group" aria-label={`Today's games: ${games.length} across ${sports.length} sports; ${games.filter((g) => g.position).length} with an AISP position`}>
        {ticks.map((t) => (
          <g key={t} transform={`translate(${x(t)},0)`}>
            <line y1={axisH - 4} y2={height - 20} className="hs-timeline__grid" />
            <text y={12} textAnchor="middle" className="hs-timeline__tick">{etHour(t)}</text>
          </g>
        ))}
        {lanes.map((lane, li) => {
          const top = tops[li]
          const color = SPORT_COLORS[lane.sport]
          return (
            <g key={lane.sport} transform={`translate(0,${top})`}>
              <rect x={0} y={0} width={width} height={lane.height} rx={8} className="cc-agenda__lane" />
              <rect x={0} y={0} width={4} height={lane.height} rx={2} fill={color} />
              <text x={10} y={18} className="cc-agenda__sport">{SPORT_NAMES[lane.sport]}</text>
              <text x={10} y={32} className="cc-muted-text">
                {lane.placed.length ? `${lane.placed.reduce((n, g) => n + g.games.length, 0)} games` : idle[lane.sport] ?? ''}
              </text>
              {lane.placed.map((g) => {
                const lines = groupLines(g)
                const labelTop = markerY + 18 + g.row * rowH
                const positioned = g.positions > 0
                const single = g.games.length === 1
                const label = single
                  ? `${lines[0]}, ${lines[1]}, ${lines[2]}`
                  : `${lines[0]}${g.positions ? `, ${lines[1]}` : ''}. Press Enter to list the games.`
                const activate = () => {
                  if (single) navigate(withSport(gameHref(g.sport, g.games[0].gameId)))
                  else setOpen((o) => (o === g.key ? null : g.key))
                }
                return (
                  <g key={g.key} className={`hs-marker${positioned ? ' is-position' : ''}${open === g.key ? ' is-open' : ''}`}
                    role="button" tabIndex={0} aria-label={label} aria-expanded={single ? undefined : open === g.key}
                    onClick={activate}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate() } }}
                    style={{ ['--sport' as string]: color }}>
                    {(g.row > 0 || Math.abs(g.lx - g.x) > 2) && <line x1={g.x} x2={g.lx} y1={markerY + 7} y2={labelTop - 10} className="hs-marker__stem" />}
                    <circle cx={g.x} cy={markerY} r={single ? 6 : 8} className="hs-marker__dot" />
                    {!single && <text x={g.x} y={markerY + 3.5} textAnchor="middle" className="hs-marker__count">{g.games.length}</text>}
                    {lines.map((line, i) => (
                      <text key={i} x={g.lx} y={labelTop + i * 12} textAnchor="middle"
                        className={i === 0 ? 'hs-marker__title' : line.startsWith('POSITION') || line.includes('POSITION') ? 'hs-marker__badge' : 'hs-marker__sub'}>{line}</text>
                    ))}
                  </g>
                )
              })}
            </g>
          )
        })}
        {nowInside && (
          <g className="cc-now" transform={`translate(${x(now)},0)`}>
            <line y1={axisH - 4} y2={height - 20} />
            <rect x={-34} y={height - 19} width={68} height={15} rx={7} className="cc-now__pill" />
            <text y={height - 8} textAnchor="middle" className="cc-now__text">now {et(now)}</text>
          </g>
        )}
      </svg>
      {!nowInside && games.length > 0 && (
        <p className="cc-muted-text hs-timeline__note">
          {now < lo ? `Now ${et(now)} · first game at ${et(first)}` : `Now ${et(now)} · every game today has started`}
        </p>
      )}
      {openGroup && (
        <div className="hs-timeline__open" role="region" aria-label={`${openGroup.games.length} games at ${et(openGroup.at)}`}>
          <b>{SPORT_NAMES[openGroup.sport]} · {openGroup.games.length} games · {et(openGroup.at)}</b>
          <ul>
            {openGroup.games.map((game) => (
              <li key={game.gameId}>
                <a href={withSport(gameHref(game.sport, game.gameId))} onClick={(e) => { e.preventDefault(); navigate(withSport(gameHref(game.sport, game.gameId))) }}>{game.matchup}</a>
                {' '}· {et(game.startsAt)}{game.position ? <b className="hs-badge"> {stakeText(game)}</b> : ' · no position'}
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => setOpen(null)}>Close</button>
        </div>
      )}
      <ul className="cc-legend" aria-label="Legend">
        <li><i className="hs-key hs-key--game" />Scheduled game</li>
        <li><i className="hs-key hs-key--position" />AISP position</li>
      </ul>
      <ChartTable caption="Today's games">
        <thead><tr><th>Start (ET)</th><th>Sport</th><th>Game</th><th>AISP position</th></tr></thead>
        <tbody>{[...games].sort((a, b) => a.startsAt - b.startsAt).map((game) => (
          <tr key={game.gameId}><td>{et(game.startsAt)}</td><td>{SPORT_NAMES[game.sport]}</td><td>{game.matchup}</td>
            <td>{game.position ? stakeText(game) : 'No position'}</td></tr>
        ))}</tbody>
      </ChartTable>
    </div>
  )
})
