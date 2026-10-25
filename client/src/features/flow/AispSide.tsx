/**
 * The one left menu for the whole platform: every page, grouped the way a night on AISP flows.
 * `children` slots page-specific filters (the sports list on Home and Games) under the menu.
 * `compact` renders icons only (the Live View's narrow rail), with the label on hover.
 */
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useOptionalUser } from '../../hooks/useAuth'
import { AlertsBell } from './Alerts'

export const AISP_NAV: Array<{ title: string; items: Array<{ to: string; label: string; icon: string; match?: RegExp }> }> = [
  { title: 'YOUR NIGHT', items: [
    { to: '/', label: 'Home', icon: '⌂', match: /^\/$/ },
    { to: '/games', label: 'Games', icon: '▦', match: /^\/games(\/|$)|^\/(nfl|mlb|nba|nhl|college-football)\/games/ },
    { to: '/mission-control', label: 'Mission Control', icon: '◈' },
    { to: '/live', label: 'Live View', icon: '◉' },
    { to: '/game-night', label: 'Game Night', icon: '▣' },
    { to: '/my-picks', label: 'My Picks', icon: '✓' },
    { to: '/experiment', label: 'Record', icon: '◎' },
    { to: '/a-house-divided', label: 'Compete', icon: '♛' },
    { to: '/mma', label: 'UFC Fight Night', icon: '🥊' },
  ] },
  { title: 'RESEARCH & TOOLS', items: [
    { to: '/markets', label: 'Markets & Crowd', icon: '◇' },
    { to: '/model-lab', label: 'Model Lab', icon: '⚖' },
    { to: '/performance', label: 'Model Performance', icon: '▥' },
    { to: '/decisions', label: 'Daily Decisions', icon: '☑' },
    { to: '/labs', label: 'Game Simulator', icon: '⚗' },
    { to: '/research/history', label: 'Historical Research', icon: '⌛' },
    { to: '/shadow-board', label: 'Shadow Board', icon: '◐' },
    { to: '/methodology', label: 'Methodology', icon: '?' },
  ] },
]

export function AispSide({ children, compact = false }: { children?: ReactNode; compact?: boolean }) {
  const { pathname } = useLocation()
  const user = useOptionalUser()
  const on = (it: { to: string; match?: RegExp }) => (it.match ? it.match.test(pathname) : pathname === it.to || pathname.startsWith(`${it.to}/`))
  if (compact) {
    return (
      <nav className="as-compact" aria-label="All pages">
        {AISP_NAV.flatMap((g) => g.items).map((it) => <Link key={it.to} to={it.to} className={on(it) ? 'is-on' : ''} title={it.label} aria-label={it.label}><i aria-hidden="true">{it.icon}</i><span>{it.label}</span></Link>)}
        <AlertsBell compact />
      </nav>
    )
  }
  return (
    <div className="hq-side">
      <Link to="/" className="hq-logo"><b>✺ AISP</b><small>AI SPORTS PREDICTOR</small></Link>
      {AISP_NAV.map((g) => (
        <div key={g.title} className="as-group">
          <small className="hq-side__label">{g.title}</small>
          <nav className="hq-nav" aria-label={g.title}>{g.items.map((it) => <Link key={it.to} to={it.to} className={on(it) ? 'is-on' : ''}><i aria-hidden="true">{it.icon}</i>{it.label}</Link>)}</nav>
        </div>
      ))}
      {children}
      <nav className="hq-nav hq-nav--foot">
        <AlertsBell />
        {user?.role === 'ADMIN' ? <Link to="/admin" className={pathname === '/admin' ? 'is-on' : ''}><i aria-hidden="true">⛁</i>Data health</Link> : null}
        <Link to="/account" className={pathname === '/account' ? 'is-on' : ''}><i aria-hidden="true">⚙</i>{user ? user.display_name : 'Account & sign in'}</Link>
        <span className="as-note">Research &amp; paper only · no real-money wagering</span>
      </nav>
    </div>
  )
}
