/**
 * One score display for every surface: live score with period and a pulsing
 * dot while a game is on, the official final once it is over, and the start
 * time before it begins. Reads AISP's scoreboard cache (30 s while live);
 * never a provider.
 */
import { useMemo } from 'react'
import { useScoreboard } from '../../hooks/useScoreboard'
import type { GameScoreboard } from '../../types/scoreboard'
import { TeamLogo } from '../../components/TeamLogo'
import './shared.css'

export interface ScoreState {
  phase: 'LIVE' | 'FINAL' | 'PRE' | 'UNKNOWN'
  away: number | null
  home: number | null
  /** "Top 7th", "Q3 4:12", "Final", "Final/10" */
  detail: string
  situation?: GameScoreboard['situation']
}

export function scoreStateOf(entry: GameScoreboard | undefined | null): ScoreState {
  if (!entry) return { phase: 'UNKNOWN', away: null, home: null, detail: '' }
  if (entry.available && entry.away_score != null && entry.home_score != null) {
    if (entry.is_final) {
      const extra = entry.period?.number && entry.league_code === 'MLB' && entry.period.number > 9 ? `/${entry.period.number}` : ''
      return { phase: 'FINAL', away: entry.away_score, home: entry.home_score, detail: `Final${extra}` }
    }
    if (entry.is_live) {
      const clock = entry.clock_available && entry.clock_seconds_remaining != null
        ? ` ${Math.floor(entry.clock_seconds_remaining / 60)}:${String(entry.clock_seconds_remaining % 60).padStart(2, '0')}` : ''
      const per = entry.period ? `${entry.period.state && entry.league_code === 'MLB' ? `${entry.period.state} ` : ''}${entry.period.label ?? ''}` : ''
      return { phase: 'LIVE', away: entry.away_score, home: entry.home_score, detail: (entry.period?.in_break ? `${per} · break` : `${per}${clock}`).trim() || 'Live', situation: entry.situation }
    }
  }
  if (entry.governed_final) return { phase: 'FINAL', away: entry.governed_final.away_score, home: entry.governed_final.home_score, detail: 'Final' }
  return { phase: entry.status === 'SCHEDULED' || !entry.status ? 'PRE' : 'UNKNOWN', away: null, home: null, detail: entry.status_detail ?? '' }
}

/** Scores for a set of games, one request, shared through the query cache. */
export function useScores(gameIds: string[]) {
  const ids = useMemo(() => [...new Set(gameIds.filter(Boolean))].sort(), [gameIds])
  const { data } = useScoreboard({ gameIds: ids, enabled: ids.length > 0 })
  return useMemo(() => {
    const out: Record<string, ScoreState> = {}
    const games = (data as { games?: Record<string, GameScoreboard> } | null)?.games ?? {}
    for (const id of ids) out[id] = scoreStateOf(games[id])
    return out
  }, [data, ids])
}

const startLabel = (iso: string | null | undefined) => iso
  ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)) + ' ET'
  : '—'

/** Compact inline score: "TB 24 · DAL 16 Final" or "● Top 7th". */
export function ScoreInline({ state, away, home, startsAt }: { state: ScoreState | undefined; away: string; home: string; startsAt?: string | null }) {
  if (!state || state.phase === 'PRE' || state.phase === 'UNKNOWN' || state.away == null) {
    return <span className="sb-inline sb-inline--pre">{startLabel(startsAt)}</span>
  }
  const awayWin = state.phase === 'FINAL' && state.away > state.home!
  const homeWin = state.phase === 'FINAL' && state.home! > state.away
  return (
    <span className={`sb-inline sb-inline--${state.phase.toLowerCase()}`}>
      <span className={awayWin ? 'is-win' : ''}>{away} {state.away}</span>
      <span className="sb-inline__sep">·</span>
      <span className={homeWin ? 'is-win' : ''}>{home} {state.home}</span>
      <em>{state.phase === 'LIVE' ? <><i className="sb-dot" />{state.detail}</> : state.detail}</em>
    </span>
  )
}

/** Big broadcast score bar for page headers. */
export function ScoreBar({ gameId, sport, away, home, startsAt }: {
  gameId: string; sport: string; away: { abbr: string; name: string }; home: { abbr: string; name: string }; startsAt?: string | null
}) {
  const state = useScores([gameId])[gameId]
  const has = state && (state.phase === 'LIVE' || state.phase === 'FINAL') && state.away != null
  const awayWin = has && state.phase === 'FINAL' && state.away! > state.home!
  const homeWin = has && state.phase === 'FINAL' && state.home! > state.away!
  return (
    <div className={`sb-bar sb-bar--${has ? state.phase.toLowerCase() : 'pre'}`} role="group" aria-label="Score">
      <div className={`sb-bar__team ${awayWin ? 'is-win' : ''} ${homeWin ? 'is-lose' : ''}`}>
        <TeamLogo league={sport} abbreviation={away.abbr} size={48} />
        <span><strong>{away.abbr}</strong><small>{away.name}</small></span>
        {has && <b>{state.away}</b>}
      </div>
      <div className="sb-bar__mid">
        {state?.phase === 'LIVE' ? <span className="sb-bar__live"><i className="sb-dot" />LIVE</span> : null}
        <span>{has ? state.detail : startLabel(startsAt)}</span>
        {state?.phase === 'LIVE' && state.situation && sport === 'MLB' && (
          <span className="sb-bar__sit">
            <Diamond bases={state.situation.bases_occupied ?? []} />
            {state.situation.outs != null && <small>{state.situation.outs} out</small>}
          </span>
        )}
      </div>
      <div className={`sb-bar__team sb-bar__team--home ${homeWin ? 'is-win' : ''} ${awayWin ? 'is-lose' : ''}`}>
        {has && <b>{state.home}</b>}
        <span><strong>{home.abbr}</strong><small>{home.name}</small></span>
        <TeamLogo league={sport} abbreviation={home.abbr} size={48} />
      </div>
    </div>
  )
}

function Diamond({ bases }: { bases: string[] }) {
  const on = (b: string) => bases.some((x) => x.toLowerCase().startsWith(b))
  return (
    <svg viewBox="0 0 40 28" width="34" height="24" aria-label={`Runners: ${bases.join(', ') || 'none'}`}>
      {[['second', 20, 6], ['third', 9, 16], ['first', 31, 16]].map(([b, x, y]) => (
        <rect key={b as string} x={(x as number) - 5} y={(y as number) - 5} width="10" height="10" transform={`rotate(45 ${x} ${y})`}
          fill={on(b as string) ? '#ffc94d' : 'transparent'} stroke="#9fb3c8" strokeWidth="1.5" />
      ))}
    </svg>
  )
}
