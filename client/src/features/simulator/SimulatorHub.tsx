/**
 * Interactive Labs, rebuilt as the front door to the Game Simulator: pick a
 * game, see the model's call at a glance, open it. NFL comes from the current
 * product week; MLB from the labs index (games with a stored simulation).
 */
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Dices, Play } from 'lucide-react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { TeamLogo } from '../../components/TeamLogo'
import { teamIdentity } from '../../services/teamIdentity'
import { ScoreInline, useScores } from '../shared/ScoreBug'
import './simulator.css'

interface HubGame { id: string; sport: 'NFL' | 'MLB'; away: string; home: string; startsAt: string; final: boolean; homeWin: number | null; expAway: number | null; expHome: number | null; note: string }

interface NflWeekList { weeks: { season: number; week: number; first_kickoff_utc: string; last_kickoff_utc: string }[] }
interface NflWeek { events: { canonical_game_id: string | null; kickoff_utc: string; state: string; home: { abbreviation: string }; away: { abbreviation: string }; engines: { home_win_probability: number; expected_home_score: number; expected_away_score: number; snapshot_type: string }[] }[] }
interface LabsIndex { games: { game_id: string; home_team: string; away_team: string; starts_at: string; is_final: boolean; gen1_available: boolean; gen1_expected_home: number | null; gen1_expected_away: number | null; v18_available: boolean; v18_expected_home: number | null; v18_expected_away: number | null }[] }

function useHub() {
  const loader = useCallback(async (signal: AbortSignal) => {
    const out: HubGame[] = []
    const [weeks, labs] = await Promise.all([
      apiRequest<NflWeekList>('/nfl/weeks', {}, signal).catch(() => null),
      apiRequest<LabsIndex>('/labs/games', {}, signal).catch(() => null),
    ])
    if (weeks?.weeks?.length) {
      const now = Date.now()
      const wk = weeks.weeks.find((w) => Date.parse(w.last_kickoff_utc) + 12 * 3600_000 > now) ?? weeks.weeks[weeks.weeks.length - 1]
      const week = await apiRequest<NflWeek>(`/nfl/weeks/${wk.season}/${wk.week}`, {}, signal).catch(() => null)
      for (const e of week?.events ?? []) {
        if (!e.canonical_game_id) continue
        const eng = e.engines[0]
        out.push({
          id: e.canonical_game_id, sport: 'NFL', away: e.away.abbreviation, home: e.home.abbreviation, startsAt: e.kickoff_utc,
          final: e.state === 'FINAL', homeWin: eng ? eng.home_win_probability : null,
          expAway: eng?.expected_away_score ?? null, expHome: eng?.expected_home_score ?? null,
          note: `Week ${wk.week}${eng ? ` · ${eng.snapshot_type === 'FINAL_PREGAME' ? 'final forecast' : 'early forecast'}` : ''}`,
        })
      }
    }
    for (const g of (labs?.games ?? []).slice().sort((a, b) => Date.parse(b.starts_at) - Date.parse(a.starts_at)).slice(0, 18)) {
      const v18 = g.v18_available
      const eh = v18 ? g.v18_expected_home : g.gen1_expected_home
      const ea = v18 ? g.v18_expected_away : g.gen1_expected_away
      out.push({ id: g.game_id, sport: 'MLB', away: g.away_team, home: g.home_team, startsAt: g.starts_at, final: g.is_final, homeWin: null, expAway: ea, expHome: eh, note: v18 ? 'V18' : 'Gen 1 (development)' })
    }
    return out
  }, [])
  return useApiResource(loader, { key: cacheKey('sim-hub', {}), staleTimeMs: STALE_TIME.slate })
}

export function SimulatorHub() {
  const res = useHub()
  const [tab, setTab] = useState<'NFL' | 'MLB'>('NFL')
  const games = useMemo(() => (res.data ?? []).filter((g) => g.sport === tab), [res.data, tab])
  const scores = useScores(games.map((g) => g.id))
  const upcoming = games.filter((g) => !g.final).sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt))
  const done = games.filter((g) => g.final).sort((a, b) => Date.parse(b.startsAt) - Date.parse(a.startsAt))
  return (
    <div className="sim hub">
      <header className="hub__hero">
        <div>
          <p className="sim-eyebrow"><Dices size={13} /> Game Simulator</p>
          <h1>Play any game 10,000 times.</h1>
          <p>Pick a game. See who wins and how often, watch 100 simulated games land, and drag any spread or total to see whether it would cash. For finished games, see how surprising the real result was.</p>
        </div>
        <div className="seg" role="group" aria-label="Sport">
          <button aria-pressed={tab === 'NFL'} onClick={() => setTab('NFL')}>NFL</button>
          <button aria-pressed={tab === 'MLB'} onClick={() => setTab('MLB')}>MLB</button>
        </div>
      </header>
      {res.loading && <div className="sim-skel" style={{ height: 260 }} />}
      {!res.loading && games.length === 0 && <p className="sim-empty">No simulated {tab} games are stored right now.</p>}
      {upcoming.length > 0 && <HubSection title="Coming up" games={upcoming} scores={scores} />}
      {done.length > 0 && <HubSection title="Finished: how did the model do?" games={done} scores={scores} />}
    </div>
  )
}

function HubSection({ title, games, scores }: { title: string; games: HubGame[]; scores: ReturnType<typeof useScores> }) {
  return (
    <section>
      <h2 className="hub__title">{title}</h2>
      <div className="hub__grid">
        {games.map((g) => {
          const awayC = teamIdentity(g.sport, g.away).primary, homeC = teamIdentity(g.sport, g.home).primary
          const hw = g.homeWin
          return (
            <Link key={g.id} className="hub-card" to={`/games/${g.id}/simulation?sport=${g.sport}`}>
              <span className="hub-card__top"><ScoreInline state={scores[g.id]} away={g.away} home={g.home} startsAt={g.startsAt} /><small>{g.note}</small></span>
              <span className="hub-card__teams">
                <span><TeamLogo league={g.sport} abbreviation={g.away} size={30} /><b>{g.away}</b></span>
                <em>@</em>
                <span><b>{g.home}</b><TeamLogo league={g.sport} abbreviation={g.home} size={30} /></span>
              </span>
              {hw != null ? (
                <span className="hub-card__bar" aria-label={`Model: ${g.home} ${Math.round(hw * 100)}%`}>
                  <span style={{ width: `${(1 - hw) * 100}%`, background: awayC }}>{Math.round((1 - hw) * 100)}%</span>
                  <span style={{ width: `${hw * 100}%`, background: homeC }}>{Math.round(hw * 100)}%</span>
                </span>
              ) : (
                <span className="hub-card__exp">Projected {g.expAway?.toFixed(1) ?? '—'} – {g.expHome?.toFixed(1) ?? '—'}</span>
              )}
              <span className="hub-card__cta"><Play size={13} /> Simulate</span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
