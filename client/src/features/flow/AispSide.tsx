/**
 * The one left menu for the whole platform: every page, grouped by what a visitor wants to do. The page you
 * are on opens into a YOU ARE HERE card (the question it answers and the next stop), so nobody is lost.
 * `children` slots page-specific filters (the sports list on Home and Games) under the menu.
 * `compact` renders icons only (the Live View's narrow rail), with the label on hover.
 */
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useOptionalUser } from '../../hooks/useAuth'
import { AlertsBell } from './Alerts'
import './two-tone.css'

export type NavItem = { to: string; label: string; icon: string; match?: RegExp; ask: string; next?: string }

/** Grouped by what a visitor wants to do, not by how the pages were built. `ask` is the one question the page answers. */
export const AISP_NAV: Array<{ title: string; items: NavItem[] }> = [
  { title: 'TONIGHT', items: [
    { to: '/', label: 'Home', icon: '⌂', match: /^\/$/, ask: "What's on tonight, and what does AISP like?", next: '/games' },
    { to: '/games', label: 'Games', icon: '▦', match: /^\/games(\/|$)|^\/(nfl|mlb|nba|nhl|college-football)\/games/, ask: "Every game: the model's price against the market, and the edge.", next: '/my-picks' },
    { to: '/mission-control', label: 'Mission Control', icon: '◈', ask: 'Is every game ready, what did AISP decide, and how did it end?', next: '/game-night' },
    { to: '/game-night', label: 'Game Night', icon: '▣', ask: "Every game at once, TV-style, with AISP's bets riding.", next: '/live' },
    { to: '/live', label: 'Live View', icon: '◉', ask: 'One game, play by play, with the win-probability river.', next: '/experiment' },
    { to: '/mma', label: 'UFC Fight Night', icon: '🥊', ask: "Fight cards with the research model's odds (research only).", next: '/markets' },
  ] },
  { title: 'YOUR PLAY', items: [
    { to: '/my-picks', label: 'My Picks', icon: '✓', ask: 'Your slip: pick from any game and lock it before the start.', next: '/a-house-divided' },
    { to: '/a-house-divided', label: 'Compete', icon: '♛', ask: 'Race the AISP model and your friends on the same games.', next: '/experiment' },
  ] },
  { title: 'CAN I TRUST IT?', items: [
    { to: '/experiment', label: 'Record', icon: '◎', ask: "AISP's paper bankroll: every bet, won or lost, and did it beat the close?", next: '/performance' },
    { to: '/performance', label: 'Model Performance', icon: '▥', ask: 'Is the model better than the market? Accuracy and calibration.', next: '/decisions' },
    { to: '/decisions', label: 'Daily Decisions', icon: '☑', ask: 'Why AISP bet, or passed, on each game today.', next: '/shadow-board' },
    { to: '/shadow-board', label: 'Shadow Board', icon: '◐', ask: 'Other strategies tracked side by side, never bet.', next: '/methodology' },
  ] },
  { title: 'DIG DEEPER', items: [
    { to: '/markets', label: 'Markets & Crowd', icon: '◇', ask: 'Best price, value plays, line moves and the prediction markets.', next: '/labs' },
    { to: '/labs', label: 'Game Simulator', icon: '⚗', ask: 'Play any game thousands of times and see every outcome.', next: '/research/history' },
    { to: '/research/history', label: 'Historical Research', icon: '⌛', ask: 'Past seasons and past games, for your own digging.', next: '/methodology' },
    { to: '/methodology', label: 'Methodology', icon: '?', ask: 'How AISP builds its numbers, in plain English.', next: '/start' },
  ] },
]

export const NAV_ITEMS: NavItem[] = AISP_NAV.flatMap((g) => g.items)
export const navItemAt = (pathname: string): NavItem | undefined => NAV_ITEMS.find((it) => (it.match ? it.match.test(pathname) : pathname === it.to || pathname.startsWith(`${it.to}/`)))

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
  const here = navItemAt(pathname)
  const next = here?.next ? NAV_ITEMS.find((it) => it.to === here.next) ?? (here.next === '/start' ? { to: '/start', label: 'Start here' } : undefined) : undefined
  return (
    <div className="hq-side">
      <Link to="/" className="hq-logo"><b>✺ AISP</b><small>AI SPORTS PREDICTOR</small></Link>
      <Link to="/start" className={`as-start${pathname === '/start' ? ' is-on' : ''}`}><i aria-hidden="true">➜</i><span><b>Start here</b><small>New? The map of AISP</small></span></Link>
      {AISP_NAV.map((g) => (
        <div key={g.title} className="as-group">
          <small className="hq-side__label">{g.title}</small>
          <nav className="hq-nav" aria-label={g.title}>
            {g.items.map((it) => (
              <div key={it.to} className="as-item">
                <Link to={it.to} className={on(it) ? 'is-on' : ''} title={it.ask} aria-current={on(it) ? 'page' : undefined}><i aria-hidden="true">{it.icon}</i>{it.label}</Link>
                {here === it ? (
                  <div className="as-here">
                    <small>YOU ARE HERE</small>
                    <p>{it.ask}</p>
                    {next ? <Link to={next.to} className="as-here__next">Next: {next.label} →</Link> : null}
                  </div>
                ) : null}
              </div>
            ))}
          </nav>
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
