/**
 * The hero for every game page (NFL/MLB, NHL, NBA, College Football): the two teams in their colours with their
 * logos, the score or the start, the TUG OF WAR between AISP's model and the market (where each one puts the home
 * team, and who won the pull once the game is final), the paper bet ticket, and the seven gates from Mission Control.
 *
 * It reads Mission Control for the game's slate (today, then the two days either side) and matches the page's id
 * against the game id or the sport's own id. When the game is not found it renders nothing, so the page below is
 * never blocked. Read-only, paper experiment only.
 */
import { useMemo, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Crest, cfbColor } from './Crest'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { teamIdentity } from '../../services/teamIdentity'
import { american, etClock, usd } from '../pulse/pulseData'
import { STAGE_KEYS, STAGE_NAMES, chipLabel, stageOf, stageTone, useMissionControl, type MissionGame, type MissionSport } from '../mission/missionData'
import './gamehero.css'

const ICON: Record<string, string> = { forecast: '◔', approval: '✓', prices: '$', decision: '⚖', bet: '▤', live: '◉', result: '★' }
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
const colorOf = (sport: MissionSport, abbr: string) => {
  if (sport !== 'CFB') { const id = teamIdentity(sport, abbr); if (id.known) return id.primary }
  return cfbColor(abbr) ?? `hsl(${hueOf(abbr)} 60% 50%)`
}
const clean = (s: string | null | undefined) => (s ?? '').replace(/Â·/g, '·').trim()
const noVig = (a: number | null | undefined, b: number | null | undefined) => {
  const imp = (x: number) => (x > 0 ? 100 / (x + 100) : -x / (-x + 100))
  if (a == null || b == null) return null
  const pa = imp(a); const pb = imp(b)
  return pa / (pa + pb)
}

export function GameHero({ id }: { id: string }) {
  const today = operationalToday()
  const d0 = useMissionControl(today)
  const dm = useMissionControl(shiftDate(today, -1))
  const dp = useMissionControl(shiftDate(today, 1))
  const g = useMemo(() => {
    for (const r of [d0, dm, dp]) { const hit = r.data?.games.find((x) => x.game_id === id || x.source_game_id === id); if (hit) return hit }
    return null
  }, [d0.data, dm.data, dp.data, id]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!g) return null
  return <Hero g={g} />
}

function Hero({ g }: { g: MissionGame }) {
  const f = stageOf(g, 'forecast'); const p = stageOf(g, 'prices'); const d = stageOf(g, 'decision'); const b = stageOf(g, 'bet'); const r = stageOf(g, 'result'); const lv = stageOf(g, 'live')
  const awayC = colorOf(g.sport, g.away.abbr); const homeC = colorOf(g.sport, g.home.abbr)
  const model = f.home_win_prob ?? null
  const market = p.consensus_home ?? noVig(p.best?.home?.american, p.best?.away?.american)
  const final = g.phase === 'FINAL' || g.status === 'final'
  const live = !final && (g.status === 'live' || g.phase === 'STARTED' || Boolean(lv.is_live))
  const score = r.final ? { a: r.final.away, h: r.final.home } : lv.away_score != null && lv.home_score != null ? { a: lv.away_score, h: lv.home_score } : null
  const homeWon = final && score ? score.h > score.a : null
  // Who won the pull: whichever side put more probability on the team that actually won.
  const pull = homeWon == null || model == null || market == null ? null : (homeWon ? model > market : model < market) ? 'MODEL' : 'MARKET'
  const outcome = r.outcome ?? null
  return (
    <section className="gh" style={{ ['--ac' as string]: awayC, ['--hc' as string]: homeC } as CSSProperties} aria-label={`${g.matchup} game summary`}>
      <div className="gh__band">
        <div className="gh__team gh__team--away">
          <Crest sport={g.sport} abbr={g.away.abbr} size={84} />
          <span><small>AWAY</small><b>{g.away.name || g.away.abbr}</b>{model != null ? <em>Model {Math.round((1 - model) * 100)}%</em> : null}</span>
          {score ? <strong className={homeWon === false ? 'is-win' : ''}>{score.a}</strong> : null}
        </div>
        <div className="gh__mid">
          <span className="gh__sport">{g.sport}</span>
          {final ? <b className="gh__state">FINAL</b> : live ? <b className="gh__state is-live">● LIVE{lv.period_label ? ` · ${clean(lv.period_label)}` : ''}</b> : <b className="gh__state">{etClock(g.starts_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET</b>}
          {outcome ? <span className={`gh__stamp is-${outcome.toLowerCase()}`}>{outcome === 'WIN' ? 'CASHED' : outcome === 'LOSS' ? 'LOST' : outcome}</span> : null}
        </div>
        <div className="gh__team gh__team--home">
          {score ? <strong className={homeWon ? 'is-win' : ''}>{score.h}</strong> : null}
          <span><small>HOME</small><b>{g.home.name || g.home.abbr}</b>{model != null ? <em>Model {Math.round(model * 100)}%</em> : null}</span>
          <Crest sport={g.sport} abbr={g.home.abbr} size={84} />
        </div>
      </div>

      <div className="gh__body">
        <div className="gh__tug" aria-label="Model versus market">
          <header><h3>Tug of War</h3><small>where AISP's model and the betting market put {g.home.abbr}</small>{pull ? <span className={`gh__pull is-${pull.toLowerCase()}`}>{pull === 'MODEL' ? 'The model won the pull' : 'The market won the pull'}</span> : null}</header>
          <div className="gh__rope">
            <i className="gh__rope-away" /><i className="gh__rope-home" />
            <span className="gh__mid50" />
            {market != null ? <span className="gh__knot is-market" style={{ left: `${market * 100}%` }}><b>Market</b><em>{Math.round(market * 100)}%</em></span> : null}
            {model != null ? <span className="gh__knot is-model" style={{ left: `${model * 100}%` }}><b>AISP</b><em>{Math.round(model * 100)}%</em></span> : null}
          </div>
          <div className="gh__ends"><span>← {g.away.abbr} side</span><span>{g.home.abbr} side →</span></div>
          <p>{model == null ? 'No stored forecast for this game.' : market == null ? `AISP gives ${g.home.abbr} ${Math.round(model * 100)}%; no market price was recorded.` : `AISP gives ${g.home.abbr} ${Math.round(model * 100)}%, the market ${Math.round(market * 100)}%: a ${Math.abs(Math.round((model - market) * 100))}-point gap${Math.abs(model - market) > 0.1 ? ', over the 10-point warning line (more often model error than edge)' : ''}.`}</p>
        </div>

        <div className={`gh__ticket${b.state === 'DONE' ? '' : ' is-none'}`}>
          <h3>The paper bet</h3>
          {b.state === 'DONE' ? (
            <>
              <b className="gh__pick">{clean(b.label).split('·')[0].trim() || `${clean(b.side)} ${american(b.american ?? null)}`}</b>
              <span>{b.sportsbook ?? ''}{b.stake_usd != null ? ` · ${usd(b.stake_usd)} to win ${usd(b.to_win_usd)}` : ''}</span>
              <span>{outcome ? <em className={`is-${outcome.toLowerCase()}`}>{outcome} {r.profit_usd != null ? usd(r.profit_usd, true) : ''}</em> : <em className="is-open">{final ? 'Final · settling' : 'Riding'}</em>}</span>
            </>
          ) : <><b className="gh__pick">No bet</b><span>{clean(chipLabel('decision', d))}</span>{d.detail ? <small>{clean(d.detail)}</small> : null}</>}
          <Link to={`/mission-control`}>All gates in Mission Control ›</Link>
        </div>
      </div>

      <ol className="gh__gates">
        {STAGE_KEYS.map((k) => { const s = stageOf(g, k); return <li key={k} className={`t-${stageTone(s.state)}`} title={clean(s.detail) || undefined}><i>{ICON[k]}</i><span><small>{STAGE_NAMES[k]}</small><b>{clean(chipLabel(k, s))}</b></span></li> })}
      </ol>
    </section>
  )
}
