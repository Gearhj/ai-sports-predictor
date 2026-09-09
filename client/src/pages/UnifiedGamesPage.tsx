import { useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { PageHeader } from '../components/ui'
import { useSportSelection } from '../hooks/useSportSelection'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from '../services/api'
import { formatDate, formatTimestamp } from '../services/format'
import type { DashboardData, ForecastGame } from '../types/domain'
import { NflWeekPage } from './NflWeekPage'

export function UnifiedGamesPage() {
  const { sport } = useSportSelection()
  return <div className="page unified-games">
    <PageHeader eyebrow="GAMES & FORECASTS" title="Every game. A clearer picture." description="Explore the forecast, compare the available price, and open the simulation behind it." />
    {(sport === 'ALL' || sport === 'NFL') && <section className="unified-games-section" aria-label="NFL games"><NflWeekPage embedded /></section>}
    {(sport === 'ALL' || sport === 'MLB') && <section className="unified-games-section" aria-label="MLB games"><MlbSchedule /></section>}
    {!['ALL','MLB','NFL'].includes(sport) && <p className="labs-empty">{sport} is planned. No forecasts or markets are available yet.</p>}
  </div>
}

function MlbSchedule() {
  const [params,setParams] = useSearchParams()
  const date = params.get('date')
  const loader = useCallback((signal:AbortSignal) => apiRequest<DashboardData>(`/games/slate${date ? '?date='+encodeURIComponent(date) : ''}`,{},signal),[date])
  const { data,state,error } = useApiResource(loader)
  const displayed = date ?? data?.date ?? ''
  const changeDate = (next:string) => { const q = new URLSearchParams(params); q.set('date',next); setParams(q) }
  const step = (days:number) => { if (!displayed) return; const d = new Date(displayed+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+days); changeDate(d.toISOString().slice(0,10)) }
  const games = data?.games.filter(g => !g.league_code || g.league_code==='MLB') ?? []
  return <>
    <div className="section-heading"><div><span className="eyebrow">MLB · DAILY SLATE</span><h2>Baseball forecasts</h2></div>
      <div className="date-control" aria-label="MLB date navigation"><button type="button" aria-label="Previous MLB date" disabled={!displayed} onClick={()=>step(-1)}><ChevronLeft size={17}/></button><label><CalendarDays size={16}/><input aria-label="MLB date" type="date" value={displayed} onChange={e=>changeDate(e.target.value)}/></label><button type="button" aria-label="Next MLB date" disabled={!displayed} onClick={()=>step(1)}><ChevronRight size={17}/></button></div>
    </div>
    <p className="nfl-counts">{displayed && formatDate(displayed)} · {games.length} games · Gen 1 forecasts; V18 remains a separate research track.</p>
    {state==='loading' && <p role="status">Loading MLB games…</p>}
    {state==='unavailable' && <p role="status">{error ?? 'MLB forecasts unavailable.'}</p>}
    <div className="unified-game-grid">{games.map(game=><MlbForecastCard key={game.id} game={game}/>)}</div>
    {data && !games.length && <p className="labs-empty">No MLB games are recorded for this date.</p>}
  </>
}
const number = (value:number|null|undefined,digits=1) => value==null ? '—' : value.toFixed(digits)
function MlbForecastCard({game}:{game:ForecastGame}) {
  const {withSport}=useSportSelection()
  return <article className="nfl-card" aria-label={`${game.away_team.name} at ${game.home_team.name}`}>
    <header className="nfl-card__head"><h3>{game.away_team.abbreviation} @ {game.home_team.abbreviation}</h3><span className="nfl-card__state">{game.status}</span></header>
    <p className="nfl-card__kickoff">{formatTimestamp(game.start_time)}</p>
    <div className="nfl-card__scoreboard"><div><b>{game.away_team.abbreviation}</b><strong>{number(game.projected_away_runs)}</strong></div><small>EXPECTED<br/>RUNS</small><div><b>{game.home_team.abbreviation}</b><strong>{number(game.projected_home_runs)}</strong></div></div>
    <div className="nfl-card__probs"><span>{game.away_team.abbreviation} {number(game.away_win_probability)}%</span><span>{game.home_team.abbreviation} {number(game.home_win_probability)}%</span></div>
    <p className="nfl-card__market">Home margin {number(game.projected_run_difference)} · Total {number(game.projected_total)}</p>
    <p className="nfl-card__market">{game.market_available ? `Observed moneyline: ${game.away_team.abbreviation} ${game.best_away_moneyline ?? '—'} / ${game.home_team.abbreviation} ${game.best_home_moneyline ?? '—'} · See market timing in analysis` : 'Sportsbook price unavailable · no priced edge'}</p>
    <p className="nfl-card__market">Evidence {game.data_as_of ? formatTimestamp(game.data_as_of) : 'time not recorded'}</p>
    <details><summary>Forecast evidence</summary><p>{game.away_team.name} at {game.home_team.name} · {game.venue}</p><p>Forecast refreshed {game.last_refresh_at ? formatTimestamp(game.last_refresh_at) : 'not recorded'}. A newer publication does not imply newer source evidence.</p></details>
    <div className="nfl-card__actions"><Link className="lab-game__action lab-game__action--analysis" to={withSport(`/games/${game.id}`,'MLB')}>Game Analysis <ArrowRight size={13}/></Link><Link className="lab-game__action lab-game__action--v18" to={withSport(`/games/${game.id}/simulation`,'MLB')}>Diamond Simulator</Link></div>
  </article>
}
