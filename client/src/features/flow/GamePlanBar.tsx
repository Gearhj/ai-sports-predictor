/**
 * TONIGHT'S GAME PLAN: the six steps of a night on AISP, on every page, each with its live status, so the
 * pages read as one workflow: Scan (Home) -> Find edges (Games) -> Check readiness (Mission Control) ->
 * See outcomes (Game Night) -> Place picks (My Picks) -> Track (Live View / Record).
 */
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { operationalToday } from '../../services/operationalDate'
import { usePreviewPicks } from '../../services/previewPicks'
import { useMissionControl } from '../mission/missionData'
import { allPositions, resultOf, usd, usePulse } from '../pulse/pulseData'
import { pickOf } from './slip'
import './flow.css'

export type PlanStep = 'scan' | 'edges' | 'ready' | 'outcomes' | 'picks' | 'track'

export function GamePlanBar({ current }: { current: PlanStep }) {
  const today = operationalToday()
  const pulse = usePulse(today)
  const mission = useMissionControl(today)
  const picks = usePreviewPicks()
  const s = useMemo(() => {
    const games = pulse.games.filter((g) => g.day === today)
    const live = games.filter((g) => g.phase === 'LIVE' || g.phase === 'STARTED').length
    const edges = games.filter((g) => g.phase === 'UPCOMING' && (pickOf(g)?.edge ?? -1) >= 3).length
    const mg = mission.data?.games ?? []
    const blocked = mg.filter((g) => g.attention === 'BLOCKED' && g.phase !== 'FINAL').length
    const upcoming = mg.filter((g) => g.phase === 'PREGAME' || g.phase === 'DECISION_WINDOW')
    const ready = upcoming.filter((g) => g.stages.forecast?.state === 'DONE' && g.stages.prices?.state === 'DONE').length
    const open = allPositions(pulse.ledger).filter((p) => resultOf(p) === 'OPEN')
    const slip = picks.filter((p) => p.status !== 'locked' && !games.find((g) => g.id === p.game_id && g.phase === 'FINAL'))
    const locked = picks.filter((p) => p.status === 'locked')
    return { games: games.length, live, edges, blocked, ready, upcoming: upcoming.length, open, slip, locked, missionOk: Boolean(mission.data) }
  }, [pulse.games, pulse.ledger, mission.data, picks, today])
  const steps: Array<{ key: PlanStep; n: number; label: string; to: string; status: string; tone: 'ok' | 'warn' | 'info' | 'go' }> = [
    { key: 'scan', n: 1, label: 'Scan the night', to: '/', status: `${s.games} games · ${s.live} live`, tone: 'info' },
    { key: 'edges', n: 2, label: 'Find edges', to: '/games', status: s.edges ? `${s.edges} edge${s.edges > 1 ? 's' : ''} of 3+ pts` : 'no 3-pt edges yet', tone: s.edges ? 'go' : 'info' },
    { key: 'ready', n: 3, label: 'Check readiness', to: '/mission-control', status: !s.missionOk ? 'checking…' : s.blocked ? `${s.blocked} need attention` : `${s.ready}/${s.upcoming} priced & forecast`, tone: s.blocked ? 'warn' : 'ok' },
    { key: 'outcomes', n: 4, label: 'See outcomes', to: '/game-night', status: s.open.length ? `${s.open.length} bets in play` : 'every outcome, live', tone: 'info' },
    { key: 'picks', n: 5, label: 'Place picks', to: '/my-picks', status: s.slip.length ? `${s.slip.length} on your slip` : s.locked.length ? `${s.locked.length} locked` : 'slip is empty', tone: s.slip.length ? 'go' : 'info' },
    { key: 'track', n: 6, label: 'Track', to: '/live', status: s.open.length ? `${s.open.length} AISP bets · ${usd(s.open.reduce((t, p) => t + (p.stake ?? 0), 0))}` : 'live view & record', tone: 'info' },
  ]
  return (
    <nav className="gp" aria-label="Tonight's game plan">
      <span className="gp__title"><b>TONIGHT&apos;S GAME PLAN</b></span>
      <ol>
        {steps.map((st) => (
          <li key={st.key} className={`gp__step is-${st.tone}${st.key === current ? ' is-here' : ''}`}>
            <Link to={st.to} aria-current={st.key === current ? 'step' : undefined}>
              <i>{st.n}</i>
              <span><b>{st.label}</b><small>{st.status}</small></span>
            </Link>
          </li>
        ))}
      </ol>
    </nav>
  )
}
