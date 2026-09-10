import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Activity, Archive, Beaker, BookOpenText, CircleDollarSign, DatabaseZap, FlaskConical, LayoutDashboard, LockKeyhole, Menu, Radio, ShieldCheck, Trophy, Users, X, ClipboardCheck } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { useSportSelection, type SelectedSport } from '../hooks/useSportSelection'
import { resolveAllSports } from '../services/sportExperience'
import { ILLUSTRATIVE_DEMO_LABEL } from '../config/publicModel'
import '../unified-sports.css'

const primaryNav = [
  { to: '/', label: 'Home', icon: LayoutDashboard, end: true },
  { to: '/games', label: 'Games & Forecasts', icon: Trophy },
  { to: '/decisions', label: 'Daily Decisions', icon: ClipboardCheck },
  { to: '/markets', label: 'Markets & Crowd', icon: Radio },
  { to: '/labs', label: 'Interactive Labs', icon: Beaker },
  { to: '/performance', label: 'Model Performance', icon: Activity },
  { to: '/research/history', label: 'Historical Research', icon: Archive },
  { to: '/shadow-board', label: 'Shadow Board', icon: FlaskConical },
  { to: '/experiment', label: '$1,000 Experiment', icon: CircleDollarSign },
  { to: '/methodology', label: 'Methodology', icon: BookOpenText },
]

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const { user } = useAuth()
  const { sport, setSport, withSport } = useSportSelection()
  const { registry, state } = useSportRegistry()
  const sports = resolveAllSports(registry?.sports)
  const demoMode = location.pathname === '/demo' || location.pathname.startsWith('/demo/')
  useEffect(() => { setMenuOpen(false) }, [location.pathname, location.search])
  return (
    <div className={`app-shell ${demoMode ? 'app-shell--demo' : ''}`}>
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
            <NavLink key={to} to={withSport(to)} end={end} className={({ isActive }) => `side-nav__item ${isActive ? 'side-nav__item--active' : ''}`}><Icon size={17} /><span>{label}</span></NavLink>
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
        <div className="workspace-toolbar">
          <label className="global-sport-selector" htmlFor="workspace-sport"><span>SPORT</span>
            <select id="workspace-sport" aria-label="Sport" value={sport} onChange={(e) => setSport(e.target.value as SelectedSport)}>
              <option value="ALL">All sports</option>
              {sports.map((item) => <option key={item.leagueCode} value={item.leagueCode} disabled={!item.navigable || !['LIVE', 'RESEARCH_SHADOW'].includes(item.availability)}>{item.shortName}{item.navigable ? '' : ' · Planned'}</option>)}
            </select>
          </label>
          <p className="workspace-context">{sport === 'ALL' ? 'One workspace. Every game.' : sport === 'NFL' ? 'NFL · Research forecasts and simulations' : sport === 'MLB' ? 'MLB · Forecasts, markets and simulations' : `${sport} · Planned`}</p>
          {state === 'unavailable' && <span role="status">Sport availability could not be loaded</span>}
        </div>
        {demoMode && <div className="demo-context-strip" role="status"><strong>{ILLUSTRATIVE_DEMO_LABEL}</strong><span>Static examples only.</span><NavLink to="/">View live application</NavLink></div>}
        <Outlet />
      </main>
    </div>
  )
}

function Brand({ compact = false, to = '/' }: { compact?: boolean; to?: string }) {
  return <NavLink to={to} className={`brand ${compact ? 'brand--compact' : ''}`} aria-label="AI Sports Predictor home"><span className="brand-mark"><CircleDollarSign size={24} strokeWidth={1.7} /></span><span><strong>AI SPORTS</strong><b>PREDICTOR</b>{!compact && <small>The game, understood.</small>}</span></NavLink>
}
