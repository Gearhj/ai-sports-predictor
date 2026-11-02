/**
 * The one left menu for the whole platform: every page, grouped by what a visitor wants to do. The page you
 * are on opens into a YOU ARE HERE card (the question it answers and the next stop), so nobody is lost.
 * `children` slots page-specific filters (the sports list on Home and Games) under the menu.
 * `compact` renders icons only (the Live View's narrow rail), with the label on hover.
 */
import './aisp-side-beta.css'
import { setMode } from '../focus/mode'
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useOptionalUser } from '../../hooks/useAuth'
import { AlertsBell } from './Alerts'
import { PlayerHud } from '../progress/PlayerHud'
import { GAMES_ON, GAMES_PATHS, WELCOME_ON } from '../release/flags'
import './two-tone.css'

export type NavItem = { to: string; label: string; icon: string; match?: RegExp; ask: string; next?: string; step?: string }

/**
 * The recommended path, numbered (owner 10-26: "put the pages in order of our recommended use and number them").
 * Steps 1-10 are the nightly routine; research (4-6) is grouped; everything else sits below, unnumbered, by purpose.
 * `ask` is the one question the page answers; `next` is the next stop.
 */
export const AISP_NAV: Array<{ title: string; items: NavItem[] }> = [
  { title: 'TONIGHT, STEP BY STEP', items: [
    { step: '1', to: '/', label: 'Home', icon: '⌂', match: /^\/$/, ask: "Step 1 · the overview: what's on tonight and what AISP likes.", next: '/games' },
    { step: '2', to: '/games', label: 'Games', icon: '▦', match: /^\/games(\/|$)|^\/(nfl|mlb|nba|nhl|college-football)\/games/, ask: "Step 2 · today's games: the model's price against the market, and the edge.", next: '/mission-control' },
    { step: '3', to: '/mission-control', label: 'Mission Control', icon: '◈', ask: 'Step 3 · readiness: is every game ready, and what did AISP decide?', next: '/game-night' },
  ] },
  { title: 'RESEARCH (STEPS 4–6)', items: [
    { step: '4', to: '/game-night', label: 'Game Night', icon: '▣', ask: "Step 4 · research: every game at once, the 10,000 simulated finals, AISP's bets riding.", next: '/markets' },
    { step: '5', to: '/markets', label: 'Markets & Crowd', icon: '◇', ask: 'Step 5 · research: the best price at each book, value plays and line moves.', next: '/labs' },
    { step: '6', to: '/labs', label: 'Game Simulator', icon: '⚗', ask: 'Step 6 · research: play any game thousands of times and see every outcome.', next: '/my-picks' },
  ] },
  { title: 'MAKE YOUR PICKS', items: [
    { step: '7', to: '/my-picks', label: 'My Picks', icon: '✓', ask: 'Step 7 · build your slip: each pick shows the best price and which sportsbook has it. AISP never places a bet; you place it at your sportsbook.', next: '/live' },
  ] },
  { title: 'WATCH AND REVIEW', items: [
    { step: '8', to: '/live', label: 'Live View', icon: '◉', ask: 'Step 8 · watch: one game, play by play, with the win-probability river.', next: '/experiment' },
    { step: '8', to: '/mma', label: 'UFC Fight Night', icon: '🥊', ask: "Step 8 · watch: fight cards with the research model's odds (research only).", next: '/experiment' },
    { step: '9', to: '/experiment', label: 'Record', icon: '◎', ask: "Step 9 · results: AISP's paper bankroll, every bet won or lost, and did it beat the close?", next: '/mmqb' },
    { step: '10', to: '/mmqb', label: 'Monday Morning QB', icon: '🏈', ask: "Step 10 · the weekly review: last week's NFL bets, what went right and wrong.", next: '/' },
  ] },
  { title: 'PLAY AND COMPETE', items: [
    { to: '/arena', label: 'The Arena', icon: '⚔', ask: 'You vs the machine: call tonight before AISP shows its hand. XP, levels, combos, trophies.', next: '/player' },
    { to: '/player', label: 'Player Card', icon: '★', ask: 'You as a trading card: your level, XP, streak, record against AISP and every trophy.', next: '/a-house-divided' },
    { to: '/a-house-divided', label: 'Compete', icon: '♛', ask: 'Race the AISP model and your friends on the same games.', next: '/arena' },
  ] },
  { title: 'CAN I TRUST IT?', items: [
    { to: '/decisions', label: 'Daily Decisions', icon: '☑', ask: 'Why AISP bet, or passed, on each game today: the Decision Machine.', next: '/performance' },
    { to: '/performance', label: 'Model Performance', icon: '▥', ask: 'Is the model better than the market? Accuracy and calibration.', next: '/shadow-board' },
    { to: '/shadow-board', label: 'Shadow Board', icon: '◐', ask: 'The proving ground: challenger models make would-be bets with no money, to earn promotion.', next: '/methodology' },
  ] },
  { title: 'DIG DEEPER', items: [
    { to: '/underdogs', label: 'Underdog Report', icon: '🐶', ask: 'How often the underdog really won, by sport, against what the market expected.', next: '/research/history' },
    { to: '/research/history', label: 'Historical Research', icon: '⌛', ask: 'Past seasons and past games, for your own digging.', next: '/methodology' },
    { to: '/methodology', label: 'Methodology', icon: '?', ask: 'How AISP builds its numbers, in plain English.', next: '/start' },
  ] },
]

// Release switches (owner 10-28): games-layer pages stay out of the menu until GAMES_ON.
if (!GAMES_ON) for (const g of AISP_NAV) g.items = g.items.filter((it) => !GAMES_PATHS.includes(it.to))
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
        {GAMES_ON && <PlayerHud compact />}
      </nav>
    )
  }
  const here = navItemAt(pathname)
  const next = here?.next ? NAV_ITEMS.find((it) => it.to === here.next) ?? (here.next === '/start' ? { to: '/start', label: 'Start here' } : undefined) : undefined
  return (
    <nav className="hq-side" aria-label="Primary navigation">
      <a href="#main-content" className="as-skip">Skip to content</a>
      <Link to="/" className="hq-logo"><b>✺ AISP</b><small>AI SPORTS PREDICTOR</small></Link>
      {GAMES_ON && <PlayerHud />}
      {/* Owner 10-27: the Focus switch must be impossible to miss, so it also lives here, above Start here. */}
      <button type="button" className="as-focus" onClick={() => setMode('focus')}><i aria-hidden="true">✦</i><span><b>Focus Mode</b><small>Just your games and picks</small></span></button>
      <Link to="/start" className={`as-start${pathname === '/start' ? ' is-on' : ''}`}><i aria-hidden="true">➜</i><span><b>Start here</b><small>New? The map of AISP</small></span></Link>
      {WELCOME_ON && <Link to="/welcome#feedback" className={`as-beta${pathname === '/welcome' ? ' is-on' : ''}`}><b>BETA</b><span>Welcome · send feedback</span></Link>}
      {AISP_NAV.map((g) => (
        <div key={g.title} className="as-group">
          <small className="hq-side__label">{g.title}</small>
          <nav className="hq-nav" aria-label={g.title}>
            {g.items.map((it) => (
              <div key={it.to} className="as-item">
                <Link to={it.to} className={`${on(it) ? 'is-on' : ''}${it.step ? ' as-step' : ''}`} title={it.ask} aria-current={on(it) ? 'page' : undefined}>{it.step ? <b className="as-num" aria-label={`Step ${it.step}`}>{it.step}</b> : <i aria-hidden="true">{it.icon}</i>}{it.label}</Link>
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
        <span className="as-note">Information &amp; entertainment · no wagering, no prizes · <Link to="/terms">Terms</Link> · <Link to="/privacy">Privacy</Link></span>
      </nav>
    </nav>
  )
}
