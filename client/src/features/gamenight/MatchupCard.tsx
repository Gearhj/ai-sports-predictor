/**
 * THE MATCHUP card beside the Monte Carlo grid (owner 10-27: "a lot of extra space that could be used to provide the
 * user with critical information or tools to build their own model"). For the game on the stage: when it starts,
 * AISP's chance and projected score for each side, the key players on AISP's every-sport availability board (QB1,
 * starting goalie, probable starters, stars) with their status, the best US-book price or when lines arrive, and
 * how far AISP sits from the market. A link drops the reader into Build Your Model on the same game.
 */
import { operationalToday } from '../../services/operationalDate'
import { american, etClock, pct, usePulse } from '../pulse/pulseData'
import { availabilityFor, useAvailability } from '../focus/FocusExtras'
import { projectedScore } from '../lab/lab'
import type { FocusTarget } from './gameNightData'

const STATUS_TONE: Record<string, string> = { OUT: 'out', DOUBTFUL: 'out', QUESTIONABLE: 'q', 'DAY-TO-DAY': 'q', AVAILABLE: 'ok' }

export function MatchupCard({ target }: { target: FocusTarget }) {
  const today = operationalToday()
  const pulse = usePulse(today)
  const avail = useAvailability(today)
  const g = pulse.games.find((x) => x.id === target.gameId) ?? null
  const a = g ? availabilityFor(g, avail.data?.games) : null
  const model = g?.modelHome ?? null
  const market = g?.marketFresh ? g.marketHome : null
  const proj = model != null ? projectedScore(target.sport, model) : null
  const starts = Date.parse(target.startsAt ?? g?.startsAt ?? '')
  const mins = Math.round((starts - Date.now()) / 60_000)
  const when = Number.isNaN(starts) ? '' : mins > 0 ? `${etClock(new Date(starts).toISOString())} ET · in ${mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`}` : `${etClock(new Date(starts).toISOString())} ET · started`
  const linesAt = Number.isNaN(starts) ? null : etClock(new Date(starts - 75 * 60_000).toISOString())
  const dp = target.sport === 'NHL' || target.sport === 'MLB' ? 1 : 0
  const side = (k: 'away' | 'home') => {
    const t = k === 'home' ? target.home : target.away
    const p = model == null ? null : k === 'home' ? model : 1 - model
    const best = k === 'home' ? g?.bestHome : g?.bestAway
    const players = a?.teams[k]?.key_players ?? []
    return (
      <div className={`mu__side mu__side--${k}`}>
        <span className="mu__team">{t.name ?? t.abbr}<small>{k === 'home' ? 'home' : 'away'}</small></span>
        <span className="mu__nums">
          <b>{p == null ? '–' : pct(p, 1)}</b>
          <em>{p == null ? 'no forecast' : 'AISP chance to win'}</em>
        </span>
        <span className="mu__price">{best?.american != null ? <>best US <b>{american(best.american)}</b>{best.book ? ` at ${best.book}` : ''}</> : linesAt ? `lines from about ${linesAt} ET` : 'no price yet'}</span>
        <ul className="mu__players">
          {target.sport === 'CFB' && !players.length ? <li className="mu__muted">ESPN publishes no college injury list</li>
            : !a ? <li className="mu__muted">injury report not in yet</li> : !players.length ? <li className="mu__muted">no key players listed</li>
            : players.slice(0, 4).map((pl) => (
              <li key={pl.name}><span>{pl.name}</span><small>{pl.role}</small><i className={`mu__st mu__st--${STATUS_TONE[pl.status] ?? 'q'}`}>{pl.status === 'AVAILABLE' ? 'IN' : pl.status}</i></li>
            ))}
        </ul>
      </div>
    )
  }
  const gap = model != null && market != null ? (model - market) * 100 : null
  return (
    <section className="mu" aria-label={`The matchup: ${target.away.abbr} at ${target.home.abbr}`}>
      <header className="mu__head"><b>THE MATCHUP <i>AISP's projection</i></b><small>{when}</small></header>
      <div className="mu__sides">
        {side('away')}
        <div className="mu__score" aria-label="AISP's projected final score">
          <small>PROJECTED FINAL</small>
          <b>{proj ? proj.away.toFixed(dp) : '–'}<em>–</em>{proj ? proj.home.toFixed(dp) : '–'}</b>
          <small>{target.away.abbr} @ {target.home.abbr}{proj ? ` · total ${(proj.home + proj.away).toFixed(1)}` : ''}</small>
        </div>
        {side('home')}
      </div>
      <p className="mu__foot">
        {gap != null ? <span>AISP vs the market: <b className={Math.abs(gap) >= 10 ? 'is-warn' : ''}>{gap >= 0 ? '+' : ''}{gap.toFixed(1)} pts</b> on {target.home.abbr}{Math.abs(gap) >= 10 ? ' · a gap this big makes AISP record-only' : ''}</span>
          : <span>Market price: {linesAt ? `AISP buys US-book lines from about ${linesAt} ET` : 'not collected'}</span>}
        <button type="button" className="mu__go" onClick={() => document.querySelector('.lb')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Build your model on this game ↓</button>
      </p>
    </section>
  )
}
