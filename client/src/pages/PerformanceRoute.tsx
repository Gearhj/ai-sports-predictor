/**
 * /performance — the Model Performance page, four views in one page shell:
 *
 *   * Overview (default) — one primary evaluation per game, the assessment and
 *     three findings computed from the same snapshot as the table.
 *   * Wager Results (`?panel=wagers&sport=NFL`) — every official funded position
 *     and the return/probability breakdowns, with the existing sport, engine and
 *     era filters (the same WagerResults component, now a top-level view).
 *   * Monday Morning QB (`?panel=mmqb&league=NFL&season=2026&week=3`) — the weekly
 *     scorecard, game deconstructions and show report, inside the page.
 *   * Detailed model analysis (`?panel=detailed`) — the existing per-sport cards.
 *
 * Views are URL state: each tab pushes a history entry, so refresh, direct links
 * and back/forward all land on the same view. The legacy
 * `?panel=detailed&view=wagers&sport=NFL` is redirected (replace) to the Wager
 * Results view. Views that are not shown are not mounted, so make no request.
 * The technical diagnostics stay lazy below.
 */
import { TruthMeter } from '../components/command/TruthMeter'
import { lazy } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { CommandPerformance, type View } from '../components/command/CommandPerformance'
import { LazyDetails } from '../components/command/LazyDetails'
import { ModelPerformanceOverview } from '../components/command/ModelPerformanceOverview'
import { MmqbReport } from '../components/command/MmqbReport'
import { RegionBoundary } from '../components/command/RegionBoundary'
import { WagerResults } from '../components/command/WagerResults'
import { useBroadcastTheme } from '../components/broadcast/primitives'
import { useSportSelection } from '../hooks/useSportSelection'
import { PERFORMANCE_SPORTS, PERFORMANCE_SPORT_NAMES, type PerformanceSport } from '../services/commandCenter'
import '../broadcast.css'

const Diagnostics = lazy(() => import('./UnifiedResearchPage').then((m) => ({ default: m.UnifiedResearchPage })))
const VIEWS: Record<string, View> = { wagers: 'WAGERS', calibration: 'CALIBRATION', time: 'TIME', market: 'MARKET', errors: 'ERRORS' }

export type Panel = 'overview' | 'wagers' | 'mmqb' | 'detailed'
export const PANELS: readonly { key: Panel; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'wagers', label: 'Wager Results' },
  { key: 'mmqb', label: 'Monday Morning QB' },
  { key: 'detailed', label: 'Detailed model analysis' },
]

/** The Wager Results view's own URL (used by every "see official wagers" link). */
export function wagersHref(sport: string = 'NFL'): string {
  return `/performance?panel=wagers&sport=${sport}`
}

/** The legacy nested Wager Results URL, rewritten to the top-level view; null when the URL is not legacy. */
export function legacyRedirect(search: string): string | null {
  const params = new URLSearchParams(search)
  if (params.get('panel') !== 'detailed' || (params.get('view') ?? '').toLowerCase() !== 'wagers') return null
  const next = new URLSearchParams(params)
  next.set('panel', 'wagers')
  next.delete('view')
  return `/performance?${next.toString()}`
}

export function panelOf(value: string | null): Panel {
  return value === 'wagers' || value === 'mmqb' || value === 'detailed' ? value : 'overview'
}

/** Scored sports include research-only NHL: the detailed view and Wager Results both
 *  open on it rather than falling back to MLB (Wager Results only to say that no
 *  official NHL position exists). */
const pick = (code: string) =>
  (PERFORMANCE_SPORTS as string[]).includes(code) ? code as PerformanceSport : undefined

export function PerformanceRoute() {
  useBroadcastTheme()
  const { sport } = useSportSelection()
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const legacy = legacyRedirect(location.search)
  const panel = panelOf(params.get('panel'))
  const number = (key: string) => { const v = Number(params.get(key)); return Number.isFinite(v) && params.get(key) ? v : null }
  const linked = (params.get('sport') ?? '').toUpperCase()
  // Every scored sport opens on its own tab: NBA's HISTORICAL channel scores and
  // College Football's prospective scores used to land on the MLB tab instead.
  const initial = pick(linked) ?? pick(sport)
  const initialView = VIEWS[(params.get('view') ?? '').toLowerCase()]
  const wagerSport = pick(linked) ?? 'NFL'
  const go = (patch: Record<string, string | null>) => {
    const copy = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) { if (v == null) copy.delete(k); else copy.set(k, v) }
    const query = copy.toString()
    navigate(`/performance${query ? `?${query}` : ''}`)   // push: back/forward walk the views
  }
  const choose = (next: Panel) => {
    if (next === panel) return
    const keep: Record<Panel, string[]> = { overview: [], wagers: ['sport'], mmqb: ['league', 'season', 'week'], detailed: ['sport'] }
    const copy = new URLSearchParams()
    if (next !== 'overview') copy.set('panel', next)
    for (const key of keep[next]) { const v = params.get(key); if (v) copy.set(key, v) }
    if (next === 'wagers' && !copy.get('sport')) copy.set('sport', wagerSport)
    const query = copy.toString()
    navigate(`/performance${query ? `?${query}` : ''}`)
  }
  if (legacy) return <Navigate to={legacy} replace />
  return (
    <div className="bx bx-performance">
      <div className="bx__inner">
        <div className="mp-view cc-tabs" role="tablist" aria-label="Model performance view">
          {PANELS.map((p) => (
            <button key={p.key} type="button" role="tab" id={`mp-tab-${p.key}`} aria-selected={panel === p.key}
              aria-controls={`mp-panel-${p.key}`} onClick={() => choose(p.key)}>{p.label}</button>
          ))}
        </div>
        <div role="tabpanel" id={`mp-panel-${panel}`} aria-labelledby={`mp-tab-${panel}`} data-testid={`mp-panel-${panel}`}>
          {panel === 'overview' && (
            <RegionBoundary label="model performance overview">
              <TruthMeter />
              <ModelPerformanceOverview wagersHref={wagersHref('NFL')} />
            </RegionBoundary>
          )}
          {panel === 'wagers' && (
            <RegionBoundary label="wager results">
              <section className="cc-dark mp-wagers" aria-label="Wager Results">
                <div className="mp-view cc-tabs" role="group" aria-label="Wager Results sport">
                  {PERFORMANCE_SPORTS.map((code) => (
                    <button key={code} type="button" aria-pressed={wagerSport === code}
                      onClick={() => go({ sport: code })}>{PERFORMANCE_SPORT_NAMES[code]}</button>
                  ))}
                </div>
                {/* Every sport, NHL included since its paper era opened (2026-10-16), reads its funded
                    positions here. Keyed so a sport change starts the component's own filters afresh. */}
                <WagerResults key={wagerSport} pageSport={wagerSport} />
              </section>
            </RegionBoundary>
          )}
          {panel === 'mmqb' && (
            <RegionBoundary label="monday morning qb">
              <MmqbReport league={(params.get('league') ?? 'NFL').toUpperCase()} season={number('season')} week={number('week')} />
            </RegionBoundary>
          )}
          {panel === 'detailed' && (
            <RegionBoundary label="model performance">
              {/* Keyed so the global sport selector (or a linked sport) moves the page, not just the URL. */}
              <CommandPerformance key={`${initial ?? 'default'}|${initialView ?? ''}`} initialSport={initial} initialView={initialView} />
            </RegionBoundary>
          )}
        </div>
        <LazyDetails summary="Technical diagnostics and methodology — offline evaluation, dataset readiness, per-game scorecards, model registry">
          <div className="cc-nested"><Diagnostics /></div>
        </LazyDetails>
      </div>
    </div>
  )
}
