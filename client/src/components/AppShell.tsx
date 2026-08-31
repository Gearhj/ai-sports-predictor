import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity,
  Archive,
  BarChart3,
  BookOpenText,
  ChevronDown,
  CircleDollarSign,
  DatabaseZap,
  FlaskConical,
  LayoutDashboard,
  LockKeyhole,
  Menu,
  Orbit,
  Radio,
  ShieldCheck,
  Trophy,
  Users,
  X,
} from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import {
  navigableRegistrySports,
  registryAvailabilityLabel,
  sportNavigationHref,
  useSportRegistry,
  type SportRegistryState,
} from '../hooks/useSportRegistry'
import { ILLUSTRATIVE_DEMO_LABEL } from '../config/publicModel'
import type { SportRegistryEntry } from '../types/multisport'

const primaryNav = [
  { to: '/', label: 'Daily command center', icon: LayoutDashboard, end: true, demoScoped: false },
  { to: '/research/history', label: 'Historical research', icon: Archive, demoScoped: false },
  { to: '/markets', label: 'Market board', icon: BarChart3 },
  { to: '/shadow-board', label: 'Shadow board', icon: FlaskConical },
  { to: '/market-pulse', label: 'Crowd & market pulse', icon: Radio },
  { to: '/performance', label: 'Model performance', icon: Activity },
  { to: '/experiment', label: '$1,000 experiment', icon: FlaskConical },
  { to: '/methodology', label: 'Methodology', icon: BookOpenText },
]

const futureNav = [
  { label: 'Community', icon: Users },
  { label: 'Leaderboards', icon: Trophy },
  { label: 'Fantasy', icon: Orbit },
]

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [sportOpen, setSportOpen] = useState(false)
  const location = useLocation()
  const { user } = useAuth()
  const { registry, state: registryState } = useSportRegistry()
  const demoMode = location.pathname === '/demo' || location.pathname.startsWith('/demo/')
  const scopedPath = (path: string) => demoMode ? path === '/' ? '/demo' : `/demo${path}` : path
  const navigableSports = navigableRegistrySports(registry)
  const activeSport = resolveActiveSport(location.pathname, location.search, navigableSports, registryState)

  useEffect(() => {
    setMenuOpen(false)
    setSportOpen(false)
  }, [location.pathname, location.search])

  return (
    <div className={`app-shell ${demoMode ? 'app-shell--demo' : ''}`}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="mobile-header">
        <Brand compact to={scopedPath('/')} />
        <button className="icon-button" type="button" onClick={() => setMenuOpen((open) => !open)} aria-label="Toggle navigation" aria-expanded={menuOpen}>
          {menuOpen ? <X /> : <Menu />}
        </button>
      </header>
      {menuOpen && <button className="nav-scrim" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
      <aside className={`sidebar ${menuOpen ? 'sidebar--open' : ''}`}>
        <Brand to={scopedPath('/')} />
        <div className="sport-switcher" aria-label="Sport selector">
          <button className="sport-switcher__active" type="button" onClick={() => setSportOpen((open) => !open)} aria-expanded={sportOpen} aria-controls="sport-options">
            <span className="sport-ball">{activeSport.shortLabel}</span>
            <span><strong>{activeSport.displayName}</strong><small><i /> {activeSport.status}</small></span>
            <ChevronDown size={15} />
          </button>
          <div className={`sport-future-list ${sportOpen ? 'sport-future-list--open' : ''}`} id="sport-options" aria-label="Sport options">
            <NavLink to="/">All sports <em>Daily hub</em></NavLink>
            {navigableSports.map((sport) => (
              <NavLink key={sport.league_code} to={sportNavigationHref(sport)!}>
                {sport.short_label} <em>{registryAvailabilityLabel(sport)}</em>
              </NavLink>
            ))}
            {registryState === 'loading' && <span aria-disabled="true">Sports <em>Registry loading</em></span>}
            {registryState === 'unavailable' && <span aria-disabled="true">Sports <em>Registry unavailable</em></span>}
          </div>
        </div>

        <nav className="side-nav" aria-label="Primary navigation">
          <span className="side-nav__label">Research desk</span>
          {primaryNav.map(({ to, label, icon: Icon, end, demoScoped = true }) => (
            <NavLink key={to} to={demoScoped ? scopedPath(to) : to} end={end} className={({ isActive }) => isActive ? 'side-nav__item side-nav__item--active' : 'side-nav__item'}>
              <Icon size={17} /><span>{label}</span>
            </NavLink>
          ))}
          <span className="side-nav__label side-nav__label--spaced">Platform</span>
          {(demoMode || user?.role === 'ADMIN') && (
            <NavLink to={scopedPath('/admin')} className={({ isActive }) => isActive ? 'side-nav__item side-nav__item--active' : 'side-nav__item'}>
              <DatabaseZap size={17} /><span>Data health</span>
            </NavLink>
          )}
          {futureNav.map(({ label, icon: Icon }) => (
            <span key={label} className="side-nav__item side-nav__item--disabled" aria-disabled="true">
              <Icon size={17} /><span>{label}</span><small>Future</small>
            </span>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="research-note"><ShieldCheck size={16} /><span>Research & simulation only<br /><small>No real-money wagering</small></span></div>
          <NavLink to="/account" className="account-link">
            <span className="account-avatar">{user ? user.display_name.slice(0, 2).toUpperCase() : <LockKeyhole size={15} />}</span>
            <span><strong>{user ? user.display_name : 'Sign in'}</strong><small>{user ? user.role : 'Account & access'}</small></span>
          </NavLink>
        </div>
      </aside>
      <main className="main-content" id="main-content">
        {demoMode && (
          <div className="demo-context-strip" role="status">
            <strong>{ILLUSTRATIVE_DEMO_LABEL}</strong>
            <span>Static examples only.</span>
            <NavLink to="/">View live application</NavLink>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  )
}

function resolveActiveSport(
  pathname: string,
  search: string,
  sports: SportRegistryEntry[],
  registryState: SportRegistryState,
): { shortLabel: string; displayName: string; status: string } {
  if (pathname === '/' || pathname === '') {
    return { shortLabel: 'ALL', displayName: 'All registered sports', status: 'Daily command center' }
  }
  const requestedSport = new URLSearchParams(search).get('sport')?.toUpperCase()
  const active = sports.find((sport) => {
    const href = sportNavigationHref(sport)
    if (!href) return false
    const [targetPath, targetSearch = ''] = href.split('?')
    if (pathname !== targetPath) return false
    if (!targetSearch) return true
    return requestedSport === new URLSearchParams(targetSearch).get('sport')?.toUpperCase()
  })
  if (active) {
    return {
      shortLabel: active.short_label,
      displayName: active.display_name,
      status: registryAvailabilityLabel(active),
    }
  }
  if (registryState === 'loading') {
    return { shortLabel: '…', displayName: 'Loading sport registry', status: 'Navigation pending' }
  }
  if (registryState === 'unavailable') {
    return { shortLabel: '—', displayName: 'Sport registry unavailable', status: 'Sport links hidden' }
  }
  return { shortLabel: 'ALL', displayName: 'All registered sports', status: 'Daily command center' }
}

function Brand({ compact = false, to = '/' }: { compact?: boolean; to?: string }) {
  return (
    <NavLink to={to} className={`brand ${compact ? 'brand--compact' : ''}`} aria-label="AI Sports Predictor home">
      <span className="brand-mark"><CircleDollarSign size={24} strokeWidth={1.7} /></span>
      <span><strong>AI SPORTS</strong><b>PREDICTOR</b>{!compact && <small>Forecasting laboratory</small>}</span>
    </NavLink>
  )
}
