/**
 * NHL page-level show (owner 10-25 22:2x: "a cool animation when two players fight", "what a user needs right next
 * to the rink", "a HOLY SHIT moment beyond the rink").
 *
 * RinkSide: RIGHT NOW in plain words — the strength (5 on 5 / power play and its clock), the penalty box with faces and
 * countdowns, a pressure meter (shot attempts over the last 3 minutes), the last goal and one "what to watch" tip.
 * GoalHorn: the full-screen goal celebration (team sweep, siren lights, giant logo, scorer, the score flipping).
 * FightCard: the full-screen "DROP THE GLOVES!" card, the two fighters face to face.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Confetti } from '../heat/Heat'
import { elapsedAt, periodName, type ArenaFeed, type ArenaPlay, type ArenaTeam } from './arenaData'
import { isAttempt, type RinkEvent } from './nhlModel'

const NOT_PENALTY = /^(goal|shot|missed|blocked|hit|face ?off|giveaway|takeaway|stoppage|period (start|end)|game end|end of|shootout|penalty shot|delayed penalty|official challenge|challenge|timeout|early intermission)/i
export const fmt = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.floor(Math.max(0, s) % 60)).padStart(2, '0')}`
export const isFight = (p: { type: string | null }) => /fight/i.test(p.type ?? '')

export interface Pen { p: ArenaPlay; team: string | null; start: number; dur: number; left: number; major: boolean }

/** Penalties still being served at `now` (minors end early on a power-play goal). */
export function activePenalties(feed: ArenaFeed, now: number): Pen[] {
  const goals = feed.plays.filter((p) => p.type === 'Goal').map((g) => ({ team: g.team, t: elapsedAt('nhl', g.period, g.clock) }))
  const out: Pen[] = []
  for (const p of feed.plays) {
    if (!p.type || NOT_PENALTY.test(p.type) || !p.team) continue
    const start = elapsedAt('nhl', p.period, p.clock)
    const major = isFight(p) || /major|misconduct|match/i.test(`${p.type} ${p.text ?? ''}`)
    const dur = /misconduct/i.test(p.type) ? 600 : major ? 300 : /double/i.test(p.text ?? '') ? 240 : 120
    let end = start + dur
    if (!major) { const ppg = goals.find((g) => g.team !== p.team && g.t > start && g.t < end); if (ppg) end = ppg.t }
    if (now >= start && now < end) out.push({ p, team: p.team, start, dur, left: end - now, major })
  }
  return out
}

export function RinkSide({ feed, events, col, teamOf }: { feed: ArenaFeed; events: RinkEvent[]; col: Record<string, string>; teamOf: (id: string | null | undefined) => ArenaTeam }) {
  const { home, away } = feed.teams
  const live = feed.status.state === 'in'
  const now = live ? elapsedAt('nhl', feed.status.period, feed.status.clock) : feed.status.state === 'post' ? 99_999 : 0
  const pens = useMemo(() => (live ? activePenalties(feed, now) : []), [feed, now, live])
  const minors = (id: string) => pens.filter((x) => x.team === id && !x.major).length
  const skaters = (id: string) => Math.max(3, 5 - minors(id))
  const hs = skaters(home.id); const as = skaters(away.id)
  const pp = hs !== as ? (hs > as ? home : away) : null
  const ppLeft = pp ? Math.min(...pens.filter((x) => x.team !== pp.id && !x.major).map((x) => x.left)) : 0
  const recent = events.filter((e) => isAttempt(e.kind) && e.t > now - 180 && e.t <= now)
  const ra = recent.filter((e) => e.shooter === away.id).length; const rh = recent.filter((e) => e.shooter === home.id).length
  const lastGoal = [...events].reverse().find((e) => e.kind === 'goal') ?? null
  const lead = home.score - away.score
  const leader = lead > 0 ? home : lead < 0 ? away : null; const trailer = leader ? (leader === home ? away : home) : null
  const p3late = (feed.status.period ?? 0) === 3 && now >= 3300
  const hd = (id: string) => events.filter((e) => e.shooter === id && e.danger === 'high' && e.kind !== 'block').length
  let tip: string
  if (!live) tip = feed.status.state === 'post' ? 'Final. Scroll down for the goals, the three stars and the play-by-play.' : 'When the puck drops, this panel tells you what to watch.'
  else if (pp) tip = `${pp.short} have an extra skater for ${fmt(ppLeft)}. About 1 in 5 power plays ends in a goal, so this is ${pp.short}' best chance to score.`
  else if (p3late && Math.abs(lead) === 1 && trailer) tip = `${trailer.short} are down one late. With about 2 minutes left they'll pull their goalie for an extra attacker. Watch for an empty-net goal either way.`
  else if (Math.abs(lead) >= 3 && leader) tip = `${leader.short} lead by ${Math.abs(lead)}. Teams come back from ${Math.abs(lead)} goals down this late less than 5% of the time.`
  else if (ra + rh >= 4 && Math.abs(ra - rh) >= 3) tip = `${(ra > rh ? away : home).short} are buzzing: ${Math.max(ra, rh)} shot attempts in the last 3 minutes to ${Math.min(ra, rh)}. A goal often follows a spell like this.`
  else tip = `Chances from right in front of the net: ${home.abbr} ${hd(home.id)}, ${away.abbr} ${hd(away.id)}. The team that gets more of those usually wins.`
  const share = ra + rh ? ra / (ra + rh) : 0.5
  const person = (id: string | undefined) => feed.athletes.find((a) => a.id === id)
  return (
    <section className="nh-card nh-now" aria-label="Right now">
      <header><h2>Right Now <small>what matters this second</small></h2></header>
      <div className={`nh-now__str${pp ? ' is-pp' : ''}`} style={{ ['--c' as string]: pp ? col[pp.id] : '#5eead4' } as CSSProperties}>
        <b>{live ? `${as} on ${hs}` : feed.status.short}</b>
        <span>{pp ? <>⚡ <em>{pp.short} POWER PLAY</em> · {fmt(ppLeft)} left</> : live ? 'Even strength: full sides' : ''}</span>
      </div>
      <PenaltyPanel pens={pens} feed={feed} col={col} teamOf={teamOf} />
      <div className="nh-now__press">
        <small>PRESSURE · shot attempts, last 3 minutes</small>
        <div className="nh-tug__row"><span className={ra > rh ? 'is-win' : ''}>{ra}</span><div className="nh-tug__bar"><i style={{ width: `${share * 100}%`, background: col[away.id] }} /><i style={{ width: `${(1 - share) * 100}%`, background: col[home.id] }} /><em>{ra + rh >= 4 && Math.abs(ra - rh) >= 3 ? `🔥 ${(ra > rh ? away : home).abbr} PRESSURE` : `${away.abbr} · ${home.abbr}`}</em></div><span className={rh > ra ? 'is-win' : ''}>{rh}</span></div>
      </div>
      {lastGoal ? <div className="nh-now__goal" style={{ ['--c' as string]: col[lastGoal.shooter ?? ''] ?? '#94a3b8' } as CSSProperties}>{lastGoal.players[0]?.headshot ? <img src={lastGoal.players[0].headshot} alt="" /> : null}<span><small>LAST GOAL · {live ? `${fmt(now - lastGoal.t)} of game time ago` : `${periodName('nhl', lastGoal.period)} ${lastGoal.clock}`}</small><b>{lastGoal.players[0]?.name}</b></span></div> : null}
      <p className="nh-now__tip">💡 {tip}</p>
    </section>
  )
}

export function GoalHorn({ e, feed, col, teamOf, onDone }: { e: RinkEvent; feed: ArenaFeed; col: Record<string, string>; teamOf: (id: string | null | undefined) => ArenaTeam; onDone: () => void }) {
  useEffect(() => { const t = window.setTimeout(onDone, 4800); const k = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onDone() }; window.addEventListener('keydown', k); return () => { window.clearTimeout(t); window.removeEventListener('keydown', k) } }, [onDone])
  const t = teamOf(e.shooter); const c = col[e.shooter ?? ''] ?? '#ef4444'
  const who = e.players.find((p) => p.role === 'scorer') ?? e.players[0]
  const a = feed.athletes.find((x) => x.id === who?.id)
  const helpers = e.players.filter((p) => p.role === 'assister').map((h) => h.name).join(' and ')
  const home = e.shooter === feed.teams.home.id
  const [flip, setFlip] = useState(false)
  useEffect(() => { const k = window.setTimeout(() => setFlip(true), 900); return () => window.clearTimeout(k) }, [])
  const after = { away: e.away ?? 0, home: e.home ?? 0 }
  const before = home ? { ...after, home: Math.max(0, after.home - 1) } : { ...after, away: Math.max(0, after.away - 1) }
  const show = flip ? after : before
  return (
    <div className="nh-horn" style={{ ['--c' as string]: c } as CSSProperties} role="dialog" aria-label="Goal" onClick={onDone}>
      <i className="nh-horn__sweep" /><i className="nh-horn__siren is-l" /><i className="nh-horn__siren is-r" />
      {t.logo ? <img src={t.logo} alt="" className="nh-horn__logo" /> : null}
      <div className="nh-horn__body">
        <b className="nh-horn__word">GOAL!</b>
        <div className="nh-horn__who">
          {who?.headshot ? <img src={who.headshot} alt="" /> : null}
          <span><em>{who?.name ?? t.short}</em><small>{Number(a?.stats.goals ?? 0) >= 3 ? 'HAT TRICK! ' : ''}{a?.stats.goals ?? '—'} G · {a?.stats.assists ?? '—'} A tonight{helpers ? ` · from ${helpers}` : ''}</small></span>
        </div>
        <div className="nh-horn__score">
          <span>{feed.teams.away.logo ? <img src={feed.teams.away.logo} alt="" /> : null}<b className={!home && flip ? 'is-flip' : ''}>{show.away}</b></span>
          <i>–</i>
          <span><b className={home && flip ? 'is-flip' : ''}>{show.home}</b>{feed.teams.home.logo ? <img src={feed.teams.home.logo} alt="" /> : null}</span>
        </div>
        <small className="nh-horn__when">{periodName('nhl', e.period)} {e.clock}{e.strength && e.strength !== 'Even Strength' ? ` · ${e.strength}` : ''} · tap to close</small>
      </div>
      <Confetti color={c} />
    </div>
  )
}

export function FightCard({ e, feed, col, onDone }: { e: RinkEvent; feed: ArenaFeed; col: Record<string, string>; onDone: () => void }) {
  useEffect(() => { const t = window.setTimeout(onDone, 4600); const k = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onDone() }; window.addEventListener('keydown', k); return () => { window.clearTimeout(t); window.removeEventListener('keydown', k) } }, [onDone])
  const [p1, p2] = e.players
  const team = (id: string | undefined) => feed.athletes.find((a) => a.id === id)?.team ?? null
  const tm = (id: string | null) => (id === feed.teams.home.id ? feed.teams.home : feed.teams.away)
  const side = (pl: typeof p1 | undefined, k: 'l' | 'r') => { const t = tm(team(pl?.id) ?? (k === 'l' ? e.team : null)); return (
    <div className={`nh-fight__side is-${k}`} style={{ ['--c' as string]: col[t.id] ?? '#94a3b8' } as CSSProperties}>
      {pl?.headshot ? <img src={pl.headshot} alt="" /> : null}
      <b>{pl?.name ?? t.short}</b><small>{t.logo ? <img src={t.logo} alt="" /> : null}{t.short}</small>
    </div>) }
  return (
    <div className="nh-fight" role="dialog" aria-label="Fight" onClick={onDone}>
      <i className="nh-fight__flash" />
      <b className="nh-fight__word">DROP THE GLOVES!</b>
      <div className="nh-fight__ring">{side(p1, 'l')}<span className="nh-fight__vs">VS</span>{side(p2, 'r')}</div>
      <small className="nh-fight__note">{periodName('nhl', e.period)} {e.clock} · Fighting is a 5-minute major for both players. They sit in the penalty box; their teams play on at full strength. · tap to close</small>
    </div>
  )
}

/** Where each team's booth sits on the rink (center ice, against the bottom boards). */
export const BOOTH = (homeTeam: boolean) => ({ x: homeTeam ? 449 : 351, y: 316 })

/** The two penalty-box booths on the rink: glass, a door, a red light while occupied, faces with a burning-down ring. */
export function PenaltyBooths({ feed, col }: { feed: ArenaFeed; col: Record<string, string> }) {
  const live = feed.status.state === 'in'
  const now = live ? elapsedAt('nhl', feed.status.period, feed.status.clock) : 0
  const pens = live ? activePenalties(feed, now) : []
  return (
    <g className="nh-booths" aria-hidden="true">
      {[feed.teams.away, feed.teams.home].map((t) => {
        const b = BOOTH(t.id === feed.teams.home.id); const mine = pens.filter((x) => x.team === t.id); const busy = mine.length > 0
        return (
          <g key={t.id} className={`nh-booth${busy ? ' is-busy' : ''}`} style={{ ['--c' as string]: col[t.id] } as CSSProperties}>
            <rect x={b.x - 34} y={b.y - 12} width="68" height="24" rx="5" className="nh-booth__glass" />
            <line x1={b.x - 34} x2={b.x - 34} y1={b.y - 12} y2={b.y + 12} className="nh-booth__door" />
            <circle cx={b.x + 30} cy={b.y - 16} r="4" className="nh-booth__light" />
            <text x={b.x} y={b.y + 22} textAnchor="middle" className="nh-booth__lab">{t.abbr} BOX</text>
            {mine.slice(0, 2).map((x, k) => {
              const cx = b.x + (mine.length > 1 ? (k ? 15 : -15) : 0); const C = 2 * Math.PI * 12; const frac = Math.max(0, Math.min(1, x.left / x.dur))
              const id = `nh-bf-${x.p.id}`; const face = x.p.players[0]?.headshot
              return (
                <g key={x.p.id} className={x.left <= 20 ? 'nh-booth__who is-soon' : 'nh-booth__who'}>
                  <clipPath id={id}><circle cx={cx} cy={b.y} r="9" /></clipPath>
                  <circle cx={cx} cy={b.y} r="12" className="nh-booth__track" />
                  <circle cx={cx} cy={b.y} r="12" className="nh-booth__ring" strokeDasharray={C} strokeDashoffset={C * (1 - frac)} transform={`rotate(-90 ${cx} ${b.y})`} />
                  {face ? <image href={face} x={cx - 9} y={b.y - 9} width="18" height="18" clipPath={`url(#${id})`} /> : null}
                  <text x={cx} y={b.y - 15} textAnchor="middle" className="nh-booth__time">{fmt(x.left)}</text>
                </g>
              )
            })}
          </g>
        )
      })}
    </g>
  )
}

/** The Right Now penalty box: bars slam down on a new arrival, the clock pulses red near the end, BACK ON THE ICE! on release. */
function PenaltyPanel({ pens, feed, col, teamOf }: { pens: Pen[]; feed: ArenaFeed; col: Record<string, string>; teamOf: (id: string | null | undefined) => ArenaTeam }) {
  const [freed, setFreed] = useState<Pen[]>([])
  const [prev, setPrev] = useState<Pen[]>(pens)
  const ids = pens.map((x) => x.p.id).join('|')
  useEffect(() => {
    const gone = prev.filter((x) => !pens.some((y) => y.p.id === x.p.id))
    if (gone.length) { setFreed(gone); const t = window.setTimeout(() => setFreed([]), 3500); setPrev(pens); return () => window.clearTimeout(t) }
    setPrev(pens)
    return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids])
  const person = (id: string | undefined) => feed.athletes.find((a) => a.id === id)
  const card = (x: Pen, out: boolean) => {
    const a = person(x.p.players[0]?.id); const c = col[x.team ?? ''] ?? '#94a3b8'; const C = 2 * Math.PI * 20; const frac = out ? 0 : Math.max(0, Math.min(1, x.left / x.dur))
    return (
      <div key={`${x.p.id}${out ? '-out' : ''}`} className={`nh-now__pen${out ? ' is-out' : x.left <= 20 ? ' is-soon' : ''}`} style={{ ['--c' as string]: c } as CSSProperties}>
        <span className="nh-now__penface">
          <svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="20" className="nh-now__pentrack" /><circle cx="24" cy="24" r="20" className="nh-now__penring" strokeDasharray={C} strokeDashoffset={C * (1 - frac)} transform="rotate(-90 24 24)" /></svg>
          {x.p.players[0]?.headshot ? <img src={x.p.players[0].headshot} alt="" /> : <i>{teamOf(x.team).abbr}</i>}
        </span>
        <span><b>{a?.name ?? x.p.players[0]?.name ?? teamOf(x.team).short}</b><small>{out ? 'BACK ON THE ICE!' : `${x.p.type}${x.major ? ' · 5 min major' : ' · 2 min'}${x.left <= 20 ? ' · ALMOST FREE' : ''}`}</small></span>
        <em>{out ? '✓' : fmt(x.left)}</em>
        {out ? null : <i className="nh-now__bars" aria-hidden="true" />}
      </div>
    )
  }
  return (
    <div className="nh-now__box">
      <small>PENALTY BOX</small>
      {pens.map((x) => card(x, false))}
      {freed.map((x) => card(x, true))}
      {!pens.length && !freed.length ? <p className="nh-now__empty">Empty. Nobody is serving a penalty.</p> : null}
    </div>
  )
}
