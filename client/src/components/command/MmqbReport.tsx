/**
 * Monday Morning QB — the weekly NFL report, a view of the Model Performance page.
 *
 * The page renders the server's own report (`/research/mmqb?format=html`), the
 * SAME generator output as the offline report.html, so the two cannot drift.
 * The report is read-only and states its data cutoff and snapshot at the top.
 *
 *   * `MmqbLink` — one line on the Overview: "Monday Morning QB: NFL Week 3",
 *     labelled from `/research/mmqb/index` (the latest week with a position).
 *   * `MmqbReport` — labelled Season and Week controls (native selects, keyboard
 *     accessible) and the report in a same-origin frame that follows the page
 *     theme (`theme=` query) and is sized to its content (no inner scrollbar);
 *     its game links open the site's own game page in the top window.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useApiResource } from '../../hooks/useApiResource'
import { API_BASE_URL, apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'

export interface MmqbWeek { season: number; week: number; label: string; positions: number; first_kickoff_utc: string | null }
interface MmqbIndex { league: string; weeks: MmqbWeek[]; latest: MmqbWeek | null }

export function mmqbHref(week: Pick<MmqbWeek, 'season' | 'week'>, league = 'NFL'): string {
  return `/performance?panel=mmqb&league=${league}&season=${week.season}&week=${week.week}`
}

export function pageTheme(): 'dark' | 'light' {
  if (typeof document === 'undefined') return 'light'
  return document.body.classList.contains('bx-dark') || document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

export function mmqbReportUrl(params: { league: string; season?: number | null; week?: number | null; theme?: string }): string {
  const query = new URLSearchParams({ league: params.league, format: 'html' })
  if (params.season) query.set('season', String(params.season))
  if (params.week != null) query.set('week', String(params.week))
  if (params.theme) query.set('theme', params.theme)
  return `${API_BASE_URL}/research/mmqb?${query.toString()}`
}

function useMmqbIndex(league: string) {
  const loader = useCallback(async (signal: AbortSignal) =>
    apiRequest<MmqbIndex>(`/research/mmqb/index?league=${league}`, {}, signal), [league])
  return useApiResource<MmqbIndex>(loader, { key: cacheKey('research-mmqb-index', { league }), staleTimeMs: STALE_TIME.history })
}

export function MmqbLink({ league = 'NFL' }: { league?: string }) {
  const index = useMmqbIndex(league)
  const latest = index.data?.latest
  if (!latest) return null
  return (
    <p className="mpo__mmqb" data-testid="mmqb-link">
      <Link to={mmqbHref(latest, league)}>Monday Morning QB: {league} Week {latest.week}</Link>
      <span className="cc-muted"> · the weekly report: every official position, the entry expectation, model vs market and a game-by-game check</span>
    </p>
  )
}

/** Season and Week selects over the weeks that hold official positions. */
export function MmqbWeekPicker({ weeks, season, week, onChange }: {
  weeks: MmqbWeek[]; season: number | null; week: number | null; onChange: (season: number, week: number) => void
}) {
  const seasons = useMemo(() => [...new Set(weeks.map((w) => w.season))].sort((a, b) => b - a), [weeks])
  const inSeason = weeks.filter((w) => w.season === season)
  return (
    <div className="mmqb__picker" role="group" aria-label="Report week">
      <label>Season{' '}
        <select value={season ?? ''} aria-label="Season" onChange={(e) => {
          const s = Number(e.target.value)
          const last = weeks.filter((w) => w.season === s).pop()
          if (last) onChange(s, last.week)
        }}>
          {season == null && <option value="">—</option>}
          {seasons.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label>Week{' '}
        <select value={week ?? ''} aria-label="Week" onChange={(e) => season != null && onChange(season, Number(e.target.value))}>
          {week == null && <option value="">—</option>}
          {inSeason.map((w) => <option key={w.week} value={w.week}>Week {w.week} · {w.positions} position{w.positions === 1 ? '' : 's'}</option>)}
        </select>
      </label>
    </div>
  )
}

export function MmqbReport({ league = 'NFL', season, week }: { league?: string; season?: number | null; week?: number | null }) {
  const index = useMmqbIndex(league)
  const navigate = useNavigate()
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(1600)
  const [loaded, setLoaded] = useState(false)
  const weeks = index.data?.weeks ?? []
  const latest = index.data?.latest ?? null
  // A bare ?panel=mmqb opens the latest week; the URL is then made explicit (replace, no extra history entry).
  useEffect(() => {
    if ((season == null || week == null) && latest) navigate(mmqbHref(latest, league), { replace: true })
  }, [season, week, latest, league, navigate])
  const theme = pageTheme()
  const src = season != null && week != null ? mmqbReportUrl({ league, season, week, theme }) : null
  useEffect(() => { setLoaded(false) }, [src])
  const fit = () => {
    setLoaded(true)
    try {
      const doc = frame.current?.contentDocument
      if (!doc) return
      const measure = () => setHeight(Math.max(600, Math.ceil(doc.body.scrollHeight)))
      measure()
      // Cards open and close, and the width can change: follow the document's height.
      if (typeof ResizeObserver !== 'undefined') new ResizeObserver(measure).observe(doc.body)
      doc.querySelectorAll('details').forEach((d) => d.addEventListener('toggle', measure))
    } catch { /* cross-origin API base: keep the default height */ }
  }
  return (
    <section className="mmqb" aria-label="Monday Morning QB" data-testid="mmqb-report">
      <nav className="mmqb__weeks" aria-label="Report controls">
        <MmqbWeekPicker weeks={weeks} season={season ?? null} week={week ?? null}
          onChange={(s, w) => navigate(mmqbHref({ season: s, week: w }, league))} />
        <Link to={`/performance?panel=wagers&sport=${league}`}>Official wagers: Wager Results</Link>
        {src && <a href={src.replace(`&theme=${theme}`, '')} target="_blank" rel="noreferrer">Open the report on its own</a>}
      </nav>
      {!loaded && <p className="cc-muted" role="status">{src ? 'Building the report from one read-only snapshot…' : 'Loading the report weeks…'}</p>}
      {src && (
        <iframe ref={frame} title="Monday Morning QB report" src={src} onLoad={fit} scrolling="no"
          style={{ width: '100%', height, border: 0, display: 'block', overflow: 'hidden' }} />
      )}
    </section>
  )
}
