/**
 * NBA Live View additions (owner 10-25, same ask as the NHL page): THE STORY SO FAR in plain English with a
 * Basketball 101 glossary, the STARTING FIVE OF THE NIGHT cards with big headshots, a TUG OF WAR of team stats, and a
 * PROFILE DRAWER with that player's line in plain words, their shots on a mini court and their plays.
 * Read-only: ESPN's public feed via /preview-api.
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { periodName, teamPair, type ArenaAthlete, type ArenaFeed, type ArenaTeam } from './arenaData'
import './nhl.css'

const num = (v: string | undefined) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
const split = (v: string | undefined) => { const [m, a] = (v ?? '0-0').split('-').map(Number); return { m: m || 0, a: a || 0 } }
const fg = (a: ArenaAthlete) => split(a.stats['fieldGoalsMade-fieldGoalsAttempted'])
const tp = (a: ArenaAthlete) => split(a.stats['threePointFieldGoalsMade-threePointFieldGoalsAttempted'])
const ft = (a: ArenaAthlete) => split(a.stats['freeThrowsMade-freeThrowsAttempted'])
const cx = (x: number) => x * 10
const cy = (y: number) => Math.min(46.5, y + 5.25) * 10
const ORD = ['1st', '2nd', '3rd', '4th']

/** A Hollinger-style game score: the one number that ranks a night. */
function gameScore(a: ArenaAthlete): number {
  const f = fg(a); const t = ft(a); const s = a.stats
  return num(s.points) + 0.4 * f.m - 0.7 * f.a - 0.4 * (t.a - t.m) + 0.7 * num(s.offensiveRebounds) + 0.3 * num(s.defensiveRebounds) + num(s.steals) + 0.7 * num(s.assists) + 0.7 * num(s.blocks) - 0.4 * num(s.fouls) - num(s.turnovers)
}

export function NbaStory({ feed, bet }: { feed: ArenaFeed; bet: string | null }) {
  const { home, away } = feed.teams
  const [learn, setLearn] = useState(false)
  const st = feed.status
  const team = (t: ArenaTeam) => { const ps = feed.athletes.filter((a) => a.team === t.id); const f = ps.reduce((s, a) => ({ m: s.m + fg(a).m, a: s.a + fg(a).a }), { m: 0, a: 0 }); const th = ps.reduce((s, a) => s + tp(a).m, 0); return { f, th, reb: ps.reduce((s, a) => s + num(a.stats.rebounds), 0), to: ps.reduce((s, a) => s + num(a.stats.turnovers), 0) } }
  const h = team(home); const a = team(away)
  const lead = home.score - away.score
  const leader = lead > 0 ? home : away; const trailer = lead > 0 ? away : home
  let maxLead = 0; let maxTeam: ArenaTeam | null = null
  for (const p of feed.plays) { if (p.home == null || p.away == null) continue; const d = p.home - p.away; if (Math.abs(d) > maxLead) { maxLead = Math.abs(d); maxTeam = d > 0 ? home : away } }
  const lines: string[] = []
  if (st.state === 'pre') lines.push(`${away.short} at ${home.short} hasn't tipped off yet.`)
  else {
    const when = st.state === 'post' ? '' : (st.period ?? 1) <= 4 ? `${st.clock} left in the ${ORD[(st.period ?? 1) - 1]} quarter` : `${st.clock} left in overtime`
    if (lead === 0) lines.push(`It's tied at ${home.score}${when ? ` with ${when}` : ''}.`)
    else if (st.state === 'post') lines.push(`${leader.short} beat ${trailer.short} ${Math.max(home.score, away.score)}–${Math.min(home.score, away.score)}.`)
    else lines.push(`${leader.short} lead ${trailer.short} ${Math.max(home.score, away.score)}–${Math.min(home.score, away.score)}${when ? ` with ${when}` : ''}.`)
    const pc = (x: { m: number; a: number }) => (x.a ? Math.round((x.m / x.a) * 100) : 0)
    if (h.f.a + a.f.a > 0) lines.push(`${home.short} are hitting ${pc(h.f)}% of their shots and ${away.short} ${pc(a.f)}%; a typical NBA team makes about 47%.`)
    if (h.th + a.th > 0) lines.push(`Threes: ${home.short} ${h.th}, ${away.short} ${a.th}. Each one is worth 3 points, so a few extra makes swing games.`)
    if (h.reb + a.reb > 0) lines.push(`${h.reb >= a.reb ? home.short : away.short} are winning the boards ${Math.max(h.reb, a.reb)}–${Math.min(h.reb, a.reb)} (rebounds = extra chances to score).`)
    if (maxTeam && maxLead >= 8) lines.push(`The biggest lead so far: ${maxTeam.short} by ${maxLead}.`)
    const wp = feed.win_probability.length ? feed.win_probability[feed.win_probability.length - 1].home : null
    if (wp != null && st.state !== 'post') lines.push(`ESPN's live win probability: ${home.short} ${Math.round(wp * 100)}%.`)
  }
  return (
    <section className="nh-card nh-story" aria-label="The story so far">
      <header><h2>The Story So Far <small>in plain English</small></h2><button type="button" className={`ar-chip${learn ? ' is-on' : ''}`} onClick={() => setLearn((v) => !v)} aria-expanded={learn}>🏀 Basketball 101</button></header>
      <p>{lines.join(' ')}</p>
      {bet ? <p className="nh-story__bet">AISP's paper bet on this game: <b>{bet}</b></p> : null}
      {learn ? (
        <dl className="nh-101">
          <div><dt>FG</dt><dd>Field goals: made shots out of attempts (free throws not included). 7-15 means 7 makes on 15 tries.</dd></div>
          <div><dt>3PT</dt><dd>Shots from behind the arc, worth 3 points. Teams take about 35 a game and make about 36%.</dd></div>
          <div><dt>Rebound</dt><dd>Grabbing a missed shot. An offensive rebound gives your team a second chance.</dd></div>
          <div><dt>Assist</dt><dd>The pass right before a made basket.</dd></div>
          <div><dt>Turnover</dt><dd>Losing the ball without a shot: a bad pass, stepping out of bounds, a steal.</dd></div>
          <div><dt>+/−</dt><dd>How much the team outscored the other side while that player was on the floor.</dd></div>
          <div><dt>Run</dt><dd>One team scoring several times in a row, like a 10–0 run. Coaches call timeouts to stop them.</dd></div>
          <div><dt>Quarters</dt><dd>Four 12-minute quarters. Tied after 48 minutes? 5-minute overtimes until someone wins.</dd></div>
        </dl>
      ) : null}
    </section>
  )
}

export function NbaPlayers({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col: Record<string, string> = { [home.id]: pair.home, [away.id]: pair.away }
  const [open, setOpen] = useState<string | null>(null)
  const [on, setOn] = useState(false)
  useEffect(() => { const t = window.setTimeout(() => setOn(true), 60); return () => window.clearTimeout(t) }, [])
  const played = feed.athletes.filter((a) => num(a.stats.minutes) > 0)
  const stars = [...played].sort((x, y) => gameScore(y) - gameScore(x)).slice(0, 5)
  const teamOf = (id: string | null | undefined) => (id === home.id ? home : away)
  const sum = (id: string, f: (a: ArenaAthlete) => number) => played.filter((a) => a.team === id).reduce((s, a) => s + f(a), 0)
  const rows: Array<[string, (id: string) => number, boolean]> = [
    ['Field goals made', (id) => sum(id, (a) => fg(a).m), true], ['Threes made', (id) => sum(id, (a) => tp(a).m), true], ['Free throws made', (id) => sum(id, (a) => ft(a).m), true],
    ['Rebounds', (id) => sum(id, (a) => num(a.stats.rebounds)), true], ['Offensive rebounds', (id) => sum(id, (a) => num(a.stats.offensiveRebounds)), true], ['Assists', (id) => sum(id, (a) => num(a.stats.assists)), true],
    ['Steals', (id) => sum(id, (a) => num(a.stats.steals)), true], ['Blocks', (id) => sum(id, (a) => num(a.stats.blocks)), true], ['Turnovers', (id) => sum(id, (a) => num(a.stats.turnovers)), false], ['Fouls', (id) => sum(id, (a) => num(a.stats.fouls)), false],
  ]
  if (!played.length) return null
  return (
    <div className="nh-row">
      <section className="nh-card nh-tug" aria-label="Tug of war">
        <header><h2>Tug of War <small>who is winning each battle</small></h2></header>
        <div className="nh-tug__teams"><b style={{ color: col[away.id] }}>{away.logo ? <img src={away.logo} alt="" /> : null}{away.abbr}</b><b style={{ color: col[home.id] }}>{home.abbr}{home.logo ? <img src={home.logo} alt="" /> : null}</b></div>
        {rows.map(([label, f, more]) => {
          const a = f(away.id); const h = f(home.id); const share = a + h ? a / (a + h) : 0.5
          return (
            <div key={label} className="nh-tug__row">
              <span className={(more ? a > h : a < h) ? 'is-win' : ''}>{a}</span>
              <div className="nh-tug__bar"><i style={{ width: `${(on ? share : 0.5) * 100}%`, background: col[away.id] }} /><i style={{ width: `${(on ? 1 - share : 0.5) * 100}%`, background: col[home.id] }} /><em>{label}</em></div>
              <span className={(more ? h > a : h < a) ? 'is-win' : ''}>{h}</span>
            </div>
          )
        })}
      </section>
      <section className="nh-card nh-stars" aria-label="Players of the night">
        <header><h2>Players of the Night <small>tap for the profile</small></h2></header>
        <div className="nh-stars__grid nh-stars__grid--five">
          {stars.map((a, k) => {
            const t = teamOf(a.team)
            return (
              <button key={a.id} type="button" className="nh-star" onClick={() => setOpen(a.id)} style={{ ['--c' as string]: col[t.id], animationDelay: `${k * 120}ms` } as CSSProperties}>
                <span className="nh-star__n">#{k + 1}</span>
                {t.logo ? <img src={t.logo} alt="" className="nh-star__logo" /> : null}
                {a.headshot ? <img src={a.headshot} alt="" className="nh-star__face" /> : <i className="nh-star__face">{a.jersey}</i>}
                <b>{a.name}</b>
                <small>{a.pos}{a.jersey ? ` #${a.jersey}` : ''} · {t.abbr}</small>
                <em>{a.stats.points} PTS · {a.stats.rebounds} REB · {a.stats.assists} AST</em>
              </button>
            )
          })}
        </div>
      </section>
      {open ? <NbaDrawer feed={feed} id={open} color={col[teamOf(feed.athletes.find((x) => x.id === open)?.team).id]} team={teamOf(feed.athletes.find((x) => x.id === open)?.team)} onClose={() => setOpen(null)} /> : null}
    </div>
  )
}

function NbaDrawer({ feed, id, color, team, onClose }: { feed: ArenaFeed; id: string; color: string; team: ArenaTeam; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    closeRef.current?.focus()
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const a = feed.athletes.find((x) => x.id === id)
  if (!a) return null
  const s = a.stats
  const mine = feed.plays.filter((p) => p.players.some((q) => q.id === id))
  const shots = mine.filter((p) => p.shooting && p.coord && p.players[0]?.id === id && p.attempted !== 1 && !/free throw/i.test(p.type ?? ''))
  const cells: Array<[string, string | undefined, string]> = [
    ['Points', s.points, ''], ['Rebounds', s.rebounds, `${s.offensiveRebounds ?? 0} offensive`], ['Assists', s.assists, 'passes before a basket'], ['Field goals', s['fieldGoalsMade-fieldGoalsAttempted'], 'made-attempted'],
    ['Threes', s['threePointFieldGoalsMade-threePointFieldGoalsAttempted'], 'made-attempted'], ['Free throws', s['freeThrowsMade-freeThrowsAttempted'], 'made-attempted'], ['Steals', s.steals, ''], ['Blocks', s.blocks, ''],
    ['Turnovers', s.turnovers, 'fewer is better'], ['+/−', s.plusMinus, 'team margin while on the floor'], ['Minutes', s.minutes, ''], ['Fouls', s.fouls, '6 and you are out'],
  ]
  return (
    <div className="nh-drawer" role="dialog" aria-modal="true" aria-label={`${a.name} profile`} onClick={onClose}>
      <aside style={{ ['--c' as string]: color } as CSSProperties} onClick={(e) => e.stopPropagation()}>
        <button type="button" ref={closeRef} className="nh-drawer__x" onClick={onClose} aria-label="Close profile">✕</button>
        <div className="nh-drawer__hero">
          {team.logo ? <img src={team.logo} alt="" className="nh-drawer__logo" /> : null}
          {a.headshot ? <img src={a.headshot} alt="" className="nh-drawer__face" /> : <i className="nh-drawer__face" />}
          <span><b>{a.name}</b><small>{a.pos ?? ''}{a.jersey ? ` · #${a.jersey}` : ''} · {team.name}</small></span>
        </div>
        <div className="nh-drawer__stats">{cells.map(([l, v, h]) => <span key={l}><small>{l}</small><b>{v ?? '—'}</b>{h ? <em>{h}</em> : null}</span>)}</div>
        <h3>Their shots tonight</h3>
        <svg viewBox="0 0 500 470" className="nh-drawer__rink" role="img" aria-label={`${a.name}'s shots`}>
          <rect x="0" y="0" width="500" height="470" fill="#1a140e" rx="10" />
          <g className="ar-lines">
            <rect x="170" y="0" width="160" height="190" /><circle cx="250" cy="190" r="60" />
            <line x1="30" y1="0" x2="30" y2="140" /><line x1="470" y1="0" x2="470" y2="140" /><path d="M30 140 A237.5 237.5 0 0 0 470 140" />
            <line x1="220" y1="40" x2="280" y2="40" className="ar-board" /><circle cx="250" cy="52.5" r="7.5" className="ar-rim" />
          </g>
          {shots.map((p, k) => <circle key={p.id} className="ar-shot" style={{ animationDelay: `${k * 80}ms` }} cx={cx(p.coord!.x)} cy={cy(p.coord!.y)} r={p.scoring ? 10 : 8} fill={p.scoring ? color : 'none'} stroke={p.scoring ? '#fff' : color} strokeWidth={p.scoring ? 2 : 3}><title>{p.text}</title></circle>)}
        </svg>
        {!shots.length ? <p className="ar-small">No shots from the floor yet.</p> : null}
        <h3>Their plays</h3>
        <ol className="nh-drawer__plays">
          {mine.slice(-12).reverse().map((p) => <li key={p.id} className={p.scoring ? 'is-goal' : ''}><span>{periodName('nba', p.period)} {p.clock}</span><b>{p.scoring ? `+${p.value ?? ''}` : (p.type ?? '').split(' ')[0].toUpperCase()}</b><em>{p.text}</em></li>)}
          {!mine.length ? <li><em>Nothing on the play-by-play yet.</em></li> : null}
        </ol>
      </aside>
    </div>
  )
}
