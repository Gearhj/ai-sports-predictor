import { useCallback, useMemo, useState } from 'react'
import { ArrowRight, ChevronRight } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { useSportSelection } from '../hooks/useSportSelection'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp } from '../services/format'
import { labIsAvailable, resolveAllSports } from '../services/sportExperience'
import type { LabsGame, LabsGames } from '../types/labs'
import type { NflEvent, NflWeekOverview, NflWeeks } from '../types/nfl'
import './LabsGameHub.css'

type Data = { data_mode: 'live'; scope: string; mlb: LabsGames | null; weeks: NflWeeks | null; nfl: NflWeekOverview | null; warnings: string[] }
type MlbPreview = LabsGame & { gen1_expected_home?: number | null; gen1_expected_away?: number | null; gen1_simulation_count?: number | null; v18_expected_home?: number | null; v18_expected_away?: number | null; v18_simulation_count?: number | null }
type DrivePreview = { engine_id: string; simulation_count: number | null; scenario_id: string; expected_home_score: number | null; expected_away_score: number | null }

function MiniField({ nfl }: { nfl: boolean }) {
  return <div className={`lgh-preview ${nfl ? 'lgh-preview--football' : 'lgh-preview--diamond'}`} aria-hidden="true">
    {nfl ? <><span className="lgh-endzone">HOME</span><div className="lgh-yardlines">{[10, 20, 30, 40, 50, 40, 30, 20, 10].map((n, i) => <span key={i}>{n}</span>)}</div><span className="lgh-endzone">AWAY</span></> : <><div className="lgh-diamond"><i /><i /><i /><i /></div><span className="lgh-mound" /></>}
    <span className="lgh-preview-label">{nfl ? 'FOOTBALL SIMULATION LAB' : 'DIAMOND SIMULATOR'}</span>
  </div>
}

function ExpectedScore({ home, away, count, note }: { home: number | null | undefined; away: number | null | undefined; count: number | null | undefined; note: string }) {
  return <div className="lgh-expected"><span>Expected score · away / home</span><strong>{away != null && home != null ? `${away.toFixed(1)} — ${home.toFixed(1)}` : 'Open engine details'}</strong><small>{count != null ? `${count.toLocaleString()} simulations` : 'Simulation count not supplied here'} · {note}</small></div>
}

function MlbLabCard({ game }: { game: MlbPreview }) {
  const { withSport } = useSportSelection()
  const [choice, setChoice] = useState(game.v18_available ? 'v18' : 'gen1')
  const v18 = choice === 'v18' && game.v18_available
  const href = v18 ? game.v18_href : game.gen1_href
  const views = [game.v18_views.broadcast && 'Broadcast Replay', game.v18_views.geometry && 'Play Geometry', game.v18_views.population && 'Simulation Population'].filter(Boolean)
  const status = game.is_final ? `Final${game.away_score != null && game.home_score != null ? ` ${game.away_score}–${game.home_score}` : ''}` : game.is_pregame ? 'Pregame' : game.event_status.toLowerCase()
  return <article className="lgh-card" aria-label={`MLB ${game.label}`}><MiniField nfl={false} /><div className="lgh-card-content">
    <header><span className="lgh-league">MLB</span><span className="lgh-state">{status}</span></header><h3>{game.label}</h3><p className="lgh-when">{game.starts_at ? formatTimestamp(game.starts_at) : 'Time not recorded'} · {game.slate_date}</p>
    <ExpectedScore home={v18 ? game.v18_expected_home : game.gen1_expected_home} away={v18 ? game.v18_expected_away : game.gen1_expected_away} count={v18 ? game.v18_simulation_count : game.gen1_simulation_count} note={v18 ? 'V18 research shadow' : 'Gen 1'} />
    <label className="lgh-engine">Choose model<select aria-label={`Model for ${game.label}`} value={v18 ? 'v18' : 'gen1'} onChange={(e) => setChoice(e.target.value)}>{game.v18_available && <option value="v18">V18 · research shadow</option>}{game.gen1_available && <option value="gen1">Gen 1 · outcome distribution</option>}</select></label>
    <p className="lgh-modes">{v18 ? views.join(' · ') : 'Outcome distribution'}{v18 ? ` · ${game.v18_path_events} plate appearances` : ''}</p>
    {href && <Link className="lgh-enter" to={withSport(href, 'MLB')}>Enter {v18 ? 'V18 Diamond Simulator' : 'Gen 1 Outcome Distribution'} <ArrowRight size={16} /></Link>}
    <Link className="lgh-analysis" to={withSport(game.analysis_href, 'MLB')}>Game analysis</Link>
    <details className="lgh-evidence"><summary>Replay evidence</summary><p>{v18 ? `Forecast ${game.v18_forecast_id}; published ${game.v18_forecast_at ?? 'time unavailable'}.` : 'Gen 1 persisted outcomes; no new simulation is run.'}</p>{!game.v18_available && <p>{game.v18_skip_reason ? `V18 forecast skipped: ${game.v18_skip_reason}` : 'No V18 path is registered for this game.'}</p>}<p>Final/older games remain dated historical replays, not current forecasts.</p></details>
  </div></article>
}

function NflLabCard({ event }: { event: NflEvent & { drive_previews?: DrivePreview[] } }) {
  const { withSport } = useSportSelection()
  const engines = event.engines.filter((e) => e.simulation.joint_available || event.supplements.joint_engines.includes(e.engine_id))
  const [choice, setChoice] = useState(engines[0]?.engine_id ?? 'DRIVE')
  const isDrive = choice === 'DRIVE'
  const engine = engines.find((e) => e.engine_id === choice) ?? engines[0]
  const drive = event.drive_previews?.find((d) => d.scenario_id === 'BASE')
  const home = isDrive ? drive?.expected_home_score : engine?.expected_home_score
  const away = isDrive ? drive?.expected_away_score : engine?.expected_away_score
  const count = isDrive ? drive?.simulation_count : engine?.simulation.count
  const hasDrive = event.supplements.drive_engines.length > 0
  const label = `${event.away.abbreviation} @ ${event.home.abbreviation}`
  const href = `/games/${event.canonical_game_id}/simulation?view=${isDrive ? 'drives' : 'explorer'}&engine=${encodeURIComponent(isDrive ? event.supplements.drive_engines[0] : engine?.engine_id ?? '')}`
  return <article className="lgh-card lgh-card--nfl" aria-label={`NFL ${label}`}><MiniField nfl /><div className="lgh-card-content">
    <header><span className="lgh-league">NFL · WEEK {event.week}</span><span className="lgh-state">{event.state.toLowerCase()}</span></header><h3>{label}</h3><p className="lgh-when">{event.kickoff_utc ? formatTimestamp(event.kickoff_utc) : 'Time not recorded'}</p>
    <ExpectedScore home={home} away={away} count={count} note={isDrive ? 'Independent drive engine · BASE' : 'M1 research shadow'} />
    <label className="lgh-engine">Choose engine<select aria-label={`Engine for ${label}`} value={choice} onChange={(e) => setChoice(e.target.value)}>{engines.map((e) => <option key={e.engine_id} value={e.engine_id}>{e.engine_id === 'NFL_M1_XGBOOST' ? 'M1 · score distribution' : e.model_label}</option>)}{hasDrive && <option value="DRIVE">Drive engine V2 · BASE / pace sensitivity</option>}</select></label>
    <p className="lgh-modes">{isDrive ? 'Drive Theater · six representative games · BASE / pace scenarios' : 'Outcome Explorer · joint scores · margin & total'}{!isDrive && hasDrive ? ' · Drive Theater also available' : ''}</p>
    {event.canonical_game_id && <Link className="lgh-enter" to={withSport(href, 'NFL')}>Enter {isDrive ? 'Drive Theater' : 'NFL Outcome Explorer'} <ArrowRight size={16} /></Link>}
    {event.canonical_game_id && <Link className="lgh-analysis" to={withSport(`/games/${event.canonical_game_id}`, 'NFL')}>Game analysis</Link>}
    <details className="lgh-evidence"><summary>Engine & evidence</summary><p>{isDrive ? event.supplements.drive_engines.join(', ') : engine?.model_label}</p><p>{isDrive ? 'Drive-level simulations are independent of the M1 forecast. Pace is a changed-duration research sensitivity, not a known coaching decision.' : `Source evidence ${engine?.timestamps.source_evidence_utc ?? 'unavailable'}; publication ${engine?.timestamps.published_utc ?? 'unavailable'}.`}</p><p>Imported research only. No model executes here; no wagering authority.</p></details>
  </div></article>
}

export function LabsPage() {
  const { sport, withSport } = useSportSelection()
  const [params, setParams] = useSearchParams()
  const { registry } = useSportRegistry()
  const requestedDate = params.get('date')
  const requestedSeason = Number(params.get('season')) || null
  const requestedWeek = Number(params.get('week')) || null
  const [query, setQuery] = useState('')
  const loader = useCallback(async (signal: AbortSignal): Promise<Data> => {
    const warnings: string[] = []
    const safe = async <T,>(label: string, operation: () => Promise<T>): Promise<T | null> => { try { return await operation() } catch (error) { if (signal.aborted) throw error; warnings.push(`${label} unavailable. No illustrative game was substituted.`); return null } }
    const [mlb, weeks] = await Promise.all([
      ['ALL', 'MLB'].includes(sport) ? safe('MLB simulation list', () => endpoints.labsGames('MLB', signal)) : null,
      ['ALL', 'NFL'].includes(sport) ? safe('NFL schedule', () => endpoints.nflWeeks(signal)) : null,
    ])
    const selected = requestedSeason && requestedWeek ? { season: requestedSeason, week: requestedWeek } : weeks?.current
    const nfl = selected && weeks ? await safe('NFL simulations', () => endpoints.nflWeek(selected.season, selected.week, signal)) : null
    return { data_mode: 'live', scope: sport, mlb: mlb?.schema_version === 'AISP_INTERACTIVE_LABS_V1' && mlb.league === 'MLB' ? mlb : null, weeks, nfl, warnings }
  }, [sport, requestedSeason, requestedWeek])
  const resource = useApiResource(loader, {
    key: cacheKey('labs/games', { sport, season: requestedSeason, week: requestedWeek }),
    staleTimeMs: STALE_TIME.history,
  })
  const data = resource.data?.scope === sport ? resource.data : null
  const dates = [...new Set(data?.mlb?.games.map((g) => g.slate_date).filter((d): d is string => Boolean(d)) ?? [])].sort().reverse()
  const v18Dates = (data?.mlb?.games ?? []).filter((g) => g.v18_available && g.slate_date).map((g) => g.slate_date!).sort().reverse()
  const selectedDate = requestedDate ?? v18Dates[0] ?? data?.mlb?.featured_slate_date ?? dates[0] ?? ''
  const cards = useMemo(() => {
    const mlb = (data?.mlb?.games ?? []).filter((g) => g.slate_date === selectedDate && (g.gen1_available || g.v18_available) && g.label.toLowerCase().includes(query.toLowerCase())).map((g) => <MlbLabCard key={`MLB-${g.game_id}`} game={g} />)
    const nfl = (data?.nfl?.events ?? []).filter((g) => g.lab_available && g.canonical_game_id && `${g.away.name} ${g.home.name} ${g.away.abbreviation} ${g.home.abbreviation}`.toLowerCase().includes(query.toLowerCase())).map((g) => <NflLabCard key={`NFL-${g.event_id}`} event={g} />)
    // All sports intentionally surfaces both products immediately, not after a
    // full baseball slate. Every card retains its own sport and event date.
    return Array.from({ length: Math.max(mlb.length, nfl.length) }, (_, i) => [mlb[i], nfl[i]]).flat().filter(Boolean)
  }, [data, selectedDate, query])
  const planned = resolveAllSports(registry?.sports).filter((s) => !labIsAvailable(s) && s.lab)
  return <div className="page labs-page lgh-page">
    <nav className="breadcrumbs" aria-label="Breadcrumb"><Link to={withSport('/')}>Daily command center</Link><ChevronRight size={13} /><span aria-current="page">Interactive labs</span></nav>
    <PageHeader eyebrow="PICK A GAME. STEP INSIDE." title="Interactive Labs" description="Explore a real saved simulation. Pick your game and engine, then enter the field. No forecast changes. No wagers." />
    <div className="lgh-intro"><div><strong>From probability to the field.</strong><p>Baseball paths and football drives. Every replay belongs to its own model, game and published evidence.</p></div><span>RESEARCH · REPLAY · EXPLORE</span></div>
    <div className="lgh-toolbar" aria-label="Lab game filters">
      {sport !== 'NFL' && <label>MLB replay date<select aria-label="MLB replay date" value={selectedDate} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); next.set('date', e.target.value); return next }, { replace: true })}>{requestedDate && !dates.includes(requestedDate) && <option value={requestedDate}>{requestedDate} · no saved replay</option>}{dates.map((d) => <option key={d} value={d}>{formatDate(d)}{v18Dates.includes(d) ? ' · V18 available' : ''}</option>)}</select></label>}
      {sport !== 'MLB' && <label>NFL season / week<select aria-label="NFL lab week" value={data?.nfl ? `${data.nfl.season}-${data.nfl.week}` : ''} onChange={(e) => { const [s, w] = e.target.value.split('-'); setParams((old) => { const next = new URLSearchParams(old); next.set('season', s); next.set('week', w); return next }, { replace: true }) }}>{data?.weeks?.weeks.map((w) => <option key={`${w.season}-${w.week}`} value={`${w.season}-${w.week}`}>{w.season} · Week {w.week}</option>)}</select></label>}
      <label>Find your game<input aria-label="Find a lab game" placeholder="Team or abbreviation" value={query} onChange={(e) => setQuery(e.target.value)} /></label><span>{cards.length} playable games</span>
    </div>
    <p className="lgh-date-note">{sport !== 'NFL' && `MLB: ${selectedDate || 'no saved replay date'}${!requestedDate && v18Dates[0] ? ' · latest saved V18 slate' : ''}. `}{sport !== 'MLB' && `NFL: ${data?.nfl?.season ?? '—'} Week ${data?.nfl?.week ?? '—'}. `}These are separate windows; older replays are not labeled live.</p>
    {resource.loading && <p role="status">Loading playable simulations…</p>}{data?.warnings.map((w) => <p className="lgh-warning" role="status" key={w}>{w}</p>)}
    <section className="lgh-games" aria-label="Playable simulations">{cards}</section>
    {!resource.loading && cards.length === 0 && <p className="labs-empty">No {sport === 'ALL' ? '' : `${sport} `}game currently persists a simulation matching this date/week and team filter.</p>}
    {planned.length > 0 && <details className="lgh-planned"><summary>Planned labs · not yet playable</summary><p>{planned.map((s) => s.lab?.name).join(' · ')}</p><p>No model output or simulator destination is available for these planned labs.</p></details>}
  </div>
}
