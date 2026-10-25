import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Activity, Archive, Beaker, BookOpenText, CircleDollarSign, DatabaseZap, FlaskConical, LayoutDashboard, ListChecks, LockKeyhole, Menu, MonitorPlay, Radar, Radio, ShieldCheck, Trophy, Users, X, ClipboardCheck, Swords } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { loadNflProductionStatus } from '../services/nflGovernance'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { PrefetchNavLink } from './PrefetchLink'
import { warmPrimaryRoutesWhenIdle } from '../services/routePrefetch'
import { useSportSelection, type SelectedSport } from '../hooks/useSportSelection'
import { resolveAllSports } from '../services/sportExperience'
import { ILLUSTRATIVE_DEMO_LABEL } from '../config/publicModel'
import { ActiveWagersDock } from './activeWagers/ActiveWagersPanel'
import { readCollapsed } from '../services/activeWagers'
import '../unified-sports.css'
import './tabbar.css'

// Five primary destinations, per the accepted consumer direction.
//   Today → the command-center home · Games → the slate · My Picks → the
//   personal play-money slip (separate from the paper experiment) · Record →
//   the AISP $1,000 paper experiment · Compete → A House Divided.
const primaryNav = [
  { to: '/', label: 'Today', icon: LayoutDashboard, end: true },
  { to: '/games', label: 'Games', icon: Trophy },
  { to: '/my-picks', label: 'My Picks', icon: ListChecks },
  { to: '/experiment', label: 'Record', icon: CircleDollarSign },
  { to: '/a-house-divided', label: 'Compete', icon: Swords },
]

// Secondary research + tools: reachable, but out of the five primary
// destinations. Show Prep is intentionally NOT listed (here or above): this is
// a navigation change only — its route, collector jobs and templates stay
// intact in App.tsx and on the server.
const secondaryNav = [
  // Every game of the day as a live pipeline, forecast through result.
  { to: '/mission-control', label: 'Mission Control', icon: Radar },
  // The broadcast wall for game nights: a full-screen page outside this shell.
  { to: '/game-night', label: 'Game Night', icon: MonitorPlay },
  { to: '/markets', label: 'Markets & Crowd', icon: Radio },
  { to: '/performance', label: 'Model Performance', icon: Activity },
  { to: '/decisions', label: 'Daily Decisions', icon: ClipboardCheck },
  { to: '/labs', label: 'Game Simulator', icon: Beaker },
  { to: '/research/history', label: 'Historical Research', icon: Archive },
  { to: '/shadow-board', label: 'Shadow Board', icon: FlaskConical },
  { to: '/methodology', label: 'Methodology', icon: BookOpenText },
]

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [wagersLayout, setWagersLayout] = useState<'expanded' | 'collapsed' | 'mobile'>(() => (readCollapsed() ? 'collapsed' : 'expanded'))
  const location = useLocation()
  const { user } = useAuth()
  const { sport, setSport, withSport } = useSportSelection()
  const { registry, state } = useSportRegistry()
  const navigate = useNavigate()
  const sports = resolveAllSports(registry?.sports)
  /**
   * A league with a research surface but no day surface is selectable, and
   * choosing it goes to that surface.
   *
   * Without this the NBA option is disabled and its imported forecasts are
   * unreachable from the selector — a reader would conclude AISP holds no NBA
   * evidence at all. It does not make NBA navigable: the backend still reports
   * navigable=false, NBA stays out of the supported filters, and no governed
   * section is created for it.
   */
  // The NFL line depends on whether a verified production handoff is pinned.
  // Fail-closed: any problem leaves the research wording in place.
  const [nflProduction, setNflProduction] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    loadNflProductionStatus(controller.signal)
      .then((status) => setNflProduction(Boolean(status?.production_label_allowed)))
      .catch(() => setNflProduction(false))
    return () => controller.abort()
  }, [])
  const nflContext = nflProduction
    ? 'NFL · M1 production baseline · prospective monitoring'
    : 'NFL · Research forecasts and simulations'

  const researchOnly = new Map(
    sports.filter((item) => !item.navigable && item.researchHref)
      .map((item) => [item.leagueCode as string, item.researchHref as string]),
  )
  const chooseSport = (next: SelectedSport) => {
    const research = researchOnly.get(next)
    setSport(next)
    if (research) navigate(research)
  }
  const demoMode = location.pathname === '/demo' || location.pathname.startsWith('/demo/')
  // The Pulse home carries its own ticker, bankroll and open bets, so the
  // global toolbar and the wagers dock would only repeat it.
  const pulseHome = location.pathname === '/'
  // Wide tool pages use the whole width; the wagers dock would squeeze them.
  // Mission Control carries its own "Sweating now" rail of open bets, so the
  // wagers dock would duplicate it; it also has its own sport filter and
  // header, so the workspace toolbar is hidden there too.
  const missionControl = location.pathname === '/mission-control'
  const wideTool = missionControl || location.pathname === '/markets/board' || location.pathname === '/labs' ||/^\/games\/[^/]+\/simulation$/.test(location.pathname)
  useEffect(() => { setMenuOpen(false) }, [location.pathname, location.search])
  // Once, after first paint: warm the primary routes' code and first reads while idle.
  useEffect(() => { warmPrimaryRoutesWhenIdle() }, [])
  return (
    <div className={`app-shell ${demoMode || pulseHome || wideTool ? 'app-shell--demo' : `app-shell--wagers-${wagersLayout}`} ${pulseHome ? 'app-shell--pulse' : ''}`}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="mobile-header">
        <Brand compact to={withSport('/')} />
        <button className="icon-button" type="button" onClick={() => setMenuOpen(!menuOpen)} aria-label="Toggle navigation" aria-expanded={menuOpen}>{menuOpen ? <X /> : <Menu />}</button>
      </header>
      {menuOpen && <button className="nav-scrim" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`}>
        <Brand to={withSport('/')} />
        <nav className="side-nav" aria-label="Primary navigation">
          <span className="side-nav__label">Your sports workspace</span>
          {primaryNav.map(({ to, label, icon: Icon, end }) => (
            /* Hovering or tabbing to a nav item starts its route chunk, and its
               first read where one is worth warming. Following the link then
               lands on content instead of on a spinner. */
            <PrefetchNavLink key={to} to={withSport(to)} end={end} className={({ isActive }) => `side-nav__item ${isActive ? 'side-nav__item--active' : ''}`}><Icon size={17} /><span>{label}</span></PrefetchNavLink>
          ))}
          <span className="side-nav__label side-nav__label--spaced">Research &amp; tools</span>
          {secondaryNav.map(({ to, label, icon: Icon }) => (
            <PrefetchNavLink key={to} to={withSport(to)} className={({ isActive }) => `side-nav__item ${isActive ? 'side-nav__item--active' : ''}`}><Icon size={17} /><span>{label}</span></PrefetchNavLink>
          ))}
          <span className="side-nav__label side-nav__label--spaced">Platform</span>
          {(demoMode || user?.role === 'ADMIN') && <NavLink to="/admin" className="side-nav__item"><DatabaseZap size={17} />Data health</NavLink>}
          <span className="side-nav__item side-nav__item--disabled" aria-disabled="true"><Users size={17} /><span>Community</span><small>Future</small></span>
        </nav>
        <div className="sidebar-footer">
          <div className="research-note"><ShieldCheck size={16} /><span>Research & simulation only<br /><small>No real-money wagering</small></span></div>
          <NavLink to="/account" className="account-link"><span className="account-avatar">{user ? user.display_name.slice(0, 2).toUpperCase() : <LockKeyhole size={15} />}</span><span><strong>{user ? user.display_name : 'Sign in'}</strong><small>{user ? user.role : 'Account & access'}</small></span></NavLink>
        </div>
      </aside>
      <main className="main-content" id="main-content">
        {!pulseHome && !missionControl && <div className="workspace-toolbar">
          <label className="global-sport-selector" htmlFor="workspace-sport"><span>SPORT</span>
            <select id="workspace-sport" aria-label="Sport" value={sport} onChange={(e) => chooseSport(e.target.value as SelectedSport)}>
              <option value="ALL">All sports</option>
              {sports.map((item) => {
                const research = researchOnly.has(item.leagueCode)
                const active = item.navigable && ['LIVE', 'RESEARCH_SHADOW'].includes(item.availability)
                return (
                  <option key={item.leagueCode} value={item.leagueCode} disabled={!active && !research}>
                    {item.shortName}{active ? '' : research ? ' · Research & paper' : ' · Planned'}
                  </option>
                )
              })}
            </select>
          </label>
          <p className="workspace-context">{sport === 'ALL' ? 'One workspace. Every game.' : sport === 'NFL' ? nflContext : sport === 'MLB' ? 'MLB · Forecasts, markets and simulations' : researchOnly.has(sport) ? `${sport} · Forecasts and its virtual paper account (prices and decisions on its page)` : `${sport} · Planned`}</p>
          {state === 'unavailable' && <span role="status">Sport availability could not be loaded</span>}
        </div>}
        {sport !== 'ALL' && !['MLB', 'NFL'].includes(sport) && !researchOnly.has(sport) && location.pathname !== '/games' && !missionControl && (
          <p className="planned-sport-note" role="status">
            {sport} is planned. AISP has no {sport} forecasts, prices or decisions yet; the pages below show the sports it covers today.
          </p>
        )}
        {demoMode && <div className="demo-context-strip" role="status"><strong>{ILLUSTRATIVE_DEMO_LABEL}</strong><span>Static examples only.</span><NavLink to="/">View live application</NavLink></div>}
        <Outlet />
      </main>
      {/* Funded paper positions, on every page. Illustrative demo routes show static examples only, so the live ledger stays out of them. */}
      {!demoMode && !pulseHome && !wideTool && <ActiveWagersDock onLayout={setWagersLayout} />}
      {/* Phones: the five destinations sit under the thumb, not behind a menu. */}
      <nav className="tabbar" aria-label="Primary">
        {primaryNav.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={withSport(to)} end={end} className={({ isActive }) => `tabbar__item ${isActive ? 'is-on' : ''}`}><Icon size={20} /><span>{label}</span></NavLink>
        ))}
      </nav>
    </div>
  )
}

function Brand({ compact = false, to = '/' }: { compact?: boolean; to?: string }) {
  return <NavLink to={to} className={`brand ${compact ? 'brand--compact' : ''}`} aria-label="AI Sports Predictor home"><span className="brand-mark"><CircleDollarSign size={24} strokeWidth={1.7} /></span><span><strong>AI SPORTS</strong><b>PREDICTOR</b>{!compact && <small>The game, understood.</small>}</span></NavLink>
}
