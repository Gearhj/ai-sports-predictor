/**
 * The shell for every page that has not been rebuilt yet: the one left menu, Tonight's Game Plan, and the
 * page itself inside `.aisp-legacy__page`, where the generated navy + steel overrides (legacy-dark.css) restyle
 * the old light stylesheets until each page gets its own redesign.
 */
import { Link, Outlet, useLocation } from 'react-router-dom'
import { ILLUSTRATIVE_DEMO_LABEL } from '../../config/publicModel'
import { DegradedBar } from '../../components/DegradedBar'
import { AispSide } from './AispSide'
import { GamePlanBar, type PlanStep } from './GamePlanBar'
import { GameHero } from './GameHero'
import '../slate/slate.css'
import './flow.css'
import './legacy-dark.css'
import './legacy-fixups.css'

/** The id on a game page: /games/:id, /nhl/games/:id, /nba/games/:id, /college-football/games/:id (not the simulation sub-page). */
const GAME_PAGE = /^\/(?:(?:nhl|nba|college-football)\/)?games\/([^/]+)\/?$/

const STEP: Array<[RegExp, PlanStep]> = [[/^\/(games|nfl|mlb|nba|nhl|college-football)/, 'edges'], [/^\/(mission-control|decisions)/, 'ready'], [/^\/(labs|game-night)/, 'outcomes'], [/^\/my-picks/, 'picks'], [/^\/(experiment|performance|a-house-divided)/, 'track']]

export function AispLayout() {
  const { pathname } = useLocation()
  const step = STEP.find(([re]) => re.test(pathname))?.[1] ?? 'scan'
  const gameId = GAME_PAGE.exec(pathname)?.[1]
  const gid = gameId && gameId !== 'classic' ? decodeURIComponent(gameId) : null
  const demo = pathname === '/demo' || pathname.startsWith('/demo/')
  return (
    <div className="hq aisp-legacy">
      <AispSide />
      <div className="hq-main">
        <DegradedBar />
        <GamePlanBar current={step} />
        {demo ? <div className="demo-context-strip" role="status"><strong>{ILLUSTRATIVE_DEMO_LABEL}</strong><span>Static examples only.</span><Link to="/">View live application</Link></div> : null}
        <main id="main-content" className="aisp-legacy__page" tabIndex={-1}>{gid ? <GameHero key={gid} id={gid} /> : null}<Outlet /></main>
      </div>
    </div>
  )
}
