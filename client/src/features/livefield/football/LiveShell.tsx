/**
 * The Live View shell: top navigation (logo, sections, search, controls), the sports rail with favourites,
 * and the game strip (league, game picker, date and venue, weather, model, test mode, broadcaster, clock).
 * Everything in it is wired: nav items go to real pages, the rail filters tonight's board, the star keeps
 * favourites in this browser, and the search finds a game by team.
 */
import { AispSide } from '../../flow/AispSide'
import '../../flow/legacy-fixups.css'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { MissionGame } from '../../mission/missionData'
import { etClock } from '../liveFieldData'

export const RAIL: Array<{ key: string; label: string; sport: string | null }> = [
  { key: 'NFL', label: 'NFL', sport: 'NFL' }, { key: 'NCAAF', label: 'NCAAF', sport: 'CFB' }, { key: 'NBA', label: 'NBA', sport: 'NBA' }, { key: 'MLB', label: 'MLB', sport: 'MLB' }, { key: 'NHL', label: 'NHL', sport: 'NHL' },
  { key: 'SOC', label: 'Soccer', sport: null }, { key: 'NCAAB', label: 'NCAAB', sport: null }, { key: 'UFC', label: 'UFC', sport: null },
]
const FAV_KEY = 'aisp.live.favorites'
export function useFavorites(): [string[], (abbr: string) => void] {
  const [favs, setFavs] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem(FAV_KEY) ?? '[]') as string[] } catch { return [] } })
  const toggle = (abbr: string) => setFavs((f) => { const n = f.includes(abbr) ? f.filter((x) => x !== abbr) : [...f, abbr].slice(-12); try { localStorage.setItem(FAV_KEY, JSON.stringify(n)) } catch { /* private window */ } return n })
  return [favs, toggle]
}

export interface StripInfo { league: string; week: number | null; season: number | null; venue: string | null; city: string | null; weather: { text: string | null; temp: number | null } | null; model: string; broadcast: string | null; live: boolean; clock: string; status: string; title?: string | null; date?: string | null }

interface Props {
  games: MissionGame[]
  gameId: string | null
  onPick: (id: string) => void
  sportFilter: string | null
  onSport: (sport: string | null) => void
  favs: string[]
  onFav: (abbr: string) => void
  strip: StripInfo | null
  onBooth: () => void
  children: ReactNode
}

export function LiveShell({ games, gameId, onPick, sportFilter, onSport, favs, onFav, strip, onBooth, children }: Props) {
  const [q, setQ] = useState('')
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(id) }, [])
  const list = useMemo(() => {
    const t = q.trim().toLowerCase()
    return games.filter((g) => (!sportFilter || g.sport === sportFilter) && (!t || `${g.away.abbr} ${g.away.name ?? ''} ${g.home.abbr} ${g.home.name ?? ''}`.toLowerCase().includes(t)))
  }, [games, sportFilter, q])
  const game = games.find((g) => g.game_id === gameId) ?? null
  const favGames = favs.map((abbr) => games.find((g) => g.away.abbr === abbr || g.home.abbr === abbr)).filter(Boolean) as MissionGame[]
  const when = game ? new Date(game.starts_at) : strip?.date ? new Date(strip.date) : new Date(now)
  const dateStr = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }).format(Number.isNaN(when.getTime()) ? now : when)
  return (
    <div className="ls">
      <header className="ls-top">
        <Link to="/" className="ls-logo"><b>AISP</b><small>AI SPORTS PREDICTOR</small></Link>
        <nav className="ls-nav" aria-label="Sections">
          <Link to="/live" className="is-on">Live View</Link>
          <Link to="/games">Games</Link>
          <Link to="/decisions">Models</Link>
          <Link to="/mission-control">Insights</Link>
          <button type="button" onClick={onBooth}>Ask the Booth</button>
          <details className="ls-more"><summary>More ▾</summary><div><Link to="/game-night">Game Night</Link><Link to="/markets">Markets</Link><Link to="/performance">Performance</Link><Link to="/methodology">Methodology</Link></div></details>
        </nav>
        <label className="ls-search"><span className="lf-sr">Search games</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search games, teams, or players…" list="ls-games" /><datalist id="ls-games">{list.map((g) => <option key={g.game_id} value={`${g.away.abbr} @ ${g.home.abbr}`} />)}</datalist></label>
        <div className="ls-icons">
          <Link to="/mission-control" title="Alerts and health" aria-label="Alerts">🔔</Link>
          <Link to="/account" title="Account" aria-label="Account">⚙</Link>
          <Link to="/account" className="ls-avatar" aria-label="Account">J</Link>
        </div>
      </header>
      <div className="ls-body">
        <aside className="ls-rail" aria-label="Sports">
          <small>ALL PAGES</small>
          <AispSide compact />
          <small>LIVE</small>
          {RAIL.map((r) => {
            const n = games.filter((g) => g.sport === r.sport).length
            return <button key={r.key} type="button" disabled={!r.sport} className={sportFilter === r.sport && r.sport ? 'is-on' : ''} title={r.sport ? `${n} games tonight` : 'Not covered by AISP yet'} onClick={() => onSport(sportFilter === r.sport ? null : r.sport)}><i className={`ls-dot${n ? ' is-live' : ''}`} />{r.label}</button>
          })}
          <button type="button" className="ls-rail__more" onClick={() => onSport(null)}>More</button>
          <small>FAVORITES</small>
          {favs.length ? favs.map((abbr) => { const g = favGames.find((x) => x.away.abbr === abbr || x.home.abbr === abbr); return <button key={abbr} type="button" className={g ? 'has-game' : ''} disabled={!g} title={g ? `${g.away.abbr} @ ${g.home.abbr} ${etClock(g.starts_at)}` : 'No game tonight'} onClick={() => g && onPick(g.game_id)}>★ {abbr}</button> }) : <span className="ls-rail__hint">Star a team in the game picker</span>}
        </aside>
        <div className="ls-main">
          <div className="ls-strip">
            <span className="ls-pill ls-pill--brand">◈ AISP LIVE VIEW</span>
            {strip ? <span className="ls-pill"><i className="ls-dot is-live" /> {strip.league}</span> : null}
            <span className="ls-pick">
              <select value={gameId ?? ''} onChange={(e) => onPick(e.target.value)} aria-label="Game">
                {!game && strip?.title ? <option value="">{strip.title} · replay</option> : null}
                {!list.length && !(!game && strip?.title) ? <option value="">No games match</option> : null}
                {list.map((g) => <option key={g.game_id} value={g.game_id}>{g.away.abbr} @ {g.home.abbr} · {etClock(g.starts_at)}{g.status === 'live' || g.phase === 'STARTED' ? ' · LIVE' : g.status === 'final' || g.phase === 'FINAL' ? ' · FINAL' : ''}</option>)}
              </select>
              {game ? <><button type="button" className={`ls-star${favs.includes(game.away.abbr) ? ' is-on' : ''}`} onClick={() => onFav(game.away.abbr)} title={`Favourite ${game.away.abbr}`}>★ {game.away.abbr}</button><button type="button" className={`ls-star${favs.includes(game.home.abbr) ? ' is-on' : ''}`} onClick={() => onFav(game.home.abbr)} title={`Favourite ${game.home.abbr}`}>★ {game.home.abbr}</button></> : null}
            </span>
            {strip ? (
              <>
                <span className="ls-meta"><b>{strip.week ? `Week ${strip.week} · ` : ''}{dateStr}</b><small>{[strip.venue, strip.city].filter(Boolean).join(' · ') || 'Venue not published'}</small></span>
                {strip.weather && (strip.weather.temp != null || (strip.weather.text && Number.isNaN(Number(strip.weather.text)))) ? <span className="ls-meta ls-meta--wx"><b>{strip.weather.temp != null ? `${strip.weather.temp}°` : ''}</b><small>{strip.weather.text && Number.isNaN(Number(strip.weather.text)) ? strip.weather.text : 'at kickoff (ESPN)'}</small></span> : null}
                <span className="ls-pill">Model: {strip.model} ▾</span>
                <span className="ls-pill ls-pill--test">Test Mode</span>
                <span className="ls-strip__right">
                  {strip.broadcast ? <span className="ls-tv">{strip.broadcast}</span> : null}
                  <span className={`ls-live ls-live--${strip.live ? 'on' : 'off'}`}>{strip.live ? 'LIVE' : strip.status}</span>
                  <b>{strip.clock}</b>
                </span>
              </>
            ) : null}
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}
