/**
 * NHL LIVE VIEW (owner 10-25: "no player profiles with pictures, no movement on the rink, no HOLY SHIT moments").
 *
 * THE LIVING RINK: the puck travels the last run of play (faceoff, hit, shot, save…) with a comet trail and a call at
 * every stop; a goal sets off the red light and an explosion with the scorer's face, and every goal stays on the ice
 * as the scorer's headshot. GOAL MOMENTS replays any goal from its faceoff. Big PLAYER CARDS open a profile drawer
 * with that player's night and their own shots on a mini rink. AISP's LIVE WIN ESTIMATE (score + clock + pregame
 * price, a simple Poisson model, labelled as such) swings a gauge and draws a river with the scorers on it. GOALIE
 * DUEL save rings, TUG-OF-WAR stat bars, and THE STORY SO FAR in plain English with a Hockey 101 glossary.
 *
 * Read-only: ESPN's public play-by-play via /preview-api. Nothing here is a forecast AISP bets on.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { periodName, teamPair, type ArenaAthlete, type ArenaFeed, type ArenaTeam } from './arenaData'
import { goalSequence, isAttempt, rinkEvents, winSeries, type RinkEvent, type WinPoint } from './nhlModel'
import './nhl.css'

const RX = (x: number) => (x + 100) * 4
const RY = (y: number) => (42.5 - y) * 4
const pct = (v: number) => `${Math.round(v * 100)}%`
const num = (v: string | undefined) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const CALL: Record<RinkEvent['kind'], string> = { goal: 'GOAL!', shot: 'SAVE', miss: 'WIDE', block: 'BLOCKED', hit: 'HIT', faceoff: 'FACEOFF', give: 'GIVEAWAY', take: 'TAKEAWAY', penalty: 'PENALTY' }
const ORD = ['1st', '2nd', '3rd']

interface Ctx { feed: ArenaFeed; events: RinkEvent[]; col: Record<string, string>; teamOf: (id: string | null | undefined) => ArenaTeam; openPlayer: (id: string) => void }

export function NhlArena({ feed, prior, priorSource, bet, children }: { feed: ArenaFeed; prior: number | null; priorSource: string; bet: string | null; children?: ReactNode }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col: Record<string, string> = { [home.id]: pair.home, [away.id]: pair.away }
  const events = useMemo(() => rinkEvents(feed), [feed])
  const [player, setPlayer] = useState<string | null>(null)
  const [replay, setReplay] = useState<{ goal: RinkEvent; n: number } | null>(null)
  const ctx: Ctx = { feed, events, col, teamOf: (id) => (id === home.id ? home : away), openPlayer: setPlayer }
  const p0 = prior ?? 0.5
  const series = useMemo(() => winSeries(feed, events, p0), [feed, events, p0])
  return (
    <div className="nh">
      <Story ctx={ctx} wp={series[series.length - 1]?.p ?? p0} bet={bet} />
      <div className="nh-top">
        <LivingRink ctx={ctx} replay={replay} />
        <div className="nh-side">
          <WinGauge ctx={ctx} series={series} prior={p0} priorSource={prior == null ? 'no pregame price, so a 50/50 start' : priorSource} />
          <GoalieDuel ctx={ctx} />
        </div>
      </div>
      <GoalMoments ctx={ctx} onReplay={(goal) => { setReplay((r) => ({ goal, n: (r?.n ?? 0) + 1 })); document.querySelector('.nh-rink')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }} />
      <div className="nh-row">
        <TugOfWar ctx={ctx} />
        <Stars ctx={ctx} />
      </div>
      <div className="nh-row">
        <OnIceNow ctx={ctx} />
        <Momentum ctx={ctx} />
      </div>
      {children}
      {player ? <PlayerDrawer ctx={ctx} id={player} onClose={() => setPlayer(null)} /> : null}
    </div>
  )
}

// ------------------------------------------------------------ the story --

function Story({ ctx, wp, bet }: { ctx: Ctx; wp: number; bet: string | null }) {
  const { feed, events } = ctx
  const { home, away } = feed.teams
  const [learn, setLearn] = useState(false)
  const sog = (id: string) => events.filter((e) => e.shooter === id && (e.kind === 'shot' || e.kind === 'goal')).length
  const hd = (id: string) => events.filter((e) => e.shooter === id && e.danger === 'high' && e.kind !== 'block').length
  const pens = events.filter((e) => e.kind === 'penalty').length
  const st = feed.status
  const lead = home.score - away.score
  const leader = lead > 0 ? home : away
  const trailer = lead > 0 ? away : home
  const lines: string[] = []
  if (st.state === 'pre') lines.push(`${away.short} at ${home.short} hasn't started yet. When it does, this page fills in play by play.`)
  else {
    const between = st.clock === '0:00' || /end|intermission/i.test(st.detail ?? '')
    const when = st.state === 'post' ? '' : between ? (st.detail ?? '') : (st.period ?? 1) <= 3 ? `${st.clock} left in the ${ORD[(st.period ?? 1) - 1]} period` : `${st.clock} left in overtime`
    if (lead === 0) lines.push(`It's tied ${home.score}–${away.score}${when ? ` (${when})` : st.state === 'post' ? '' : ''}.`)
    else if (st.state === 'post') lines.push(`${leader.short} beat ${trailer.short} ${Math.max(home.score, away.score)}–${Math.min(home.score, away.score)}.`)
    else lines.push(`${leader.short} lead ${trailer.short} ${Math.max(home.score, away.score)}–${Math.min(home.score, away.score)}${when ? (between ? ` (${when})` : ` with ${when}`) : ''}.`)
    const sh = sog(home.id); const sa = sog(away.id)
    if (sh + sa > 0) {
      const more = sh >= sa ? home : away
      lines.push(sh === sa ? `Both teams have ${sh} shots on goal: an even fight for the puck.` : `${more.short} have outshot ${(more === home ? away : home).short} ${Math.max(sh, sa)}–${Math.min(sh, sa)}. More shots usually means more time in the attacking end, but goalies stop about 9 of every 10.`)
    }
    const hh = hd(home.id); const ha = hd(away.id)
    if (hh + ha > 0) lines.push(hh === ha ? `Dangerous chances, from right in front of the net, are even at ${hh} each.` : `${(hh > ha ? home : away).short} have had the better looks: ${Math.max(hh, ha)} dangerous chances from right in front of the net to ${Math.min(hh, ha)}. Those go in far more often than shots from the blue line.`)
    const goalies = feed.athletes.filter((a) => a.group === 'goalies' && a.stats.saves != null && num(a.stats.shotsAgainst) >= 8)
    const hot = [...goalies].sort((a, b) => num(b.stats.savePct) - num(a.stats.savePct))[0]
    if (hot) lines.push(`${hot.name} has stopped ${hot.stats.saves} of ${hot.stats.shotsAgainst} shots (${hot.stats.savePct}); the league norm is about .900.`)
    if (pens) lines.push(`${pens} penalt${pens === 1 ? 'y has' : 'ies have'} been called. Each one sends a player to the box for 2 minutes and gives the other team a power play: 5 skaters against 4.`)
    if (st.state !== 'post') lines.push(`AISP's live estimate: ${home.short} ${pct(wp)} to win, ${away.short} ${pct(1 - wp)}.`)
  }
  return (
    <section className="nh-card nh-story" aria-label="The story so far">
      <header><h2>The Story So Far <small>in plain English</small></h2><button type="button" className={`ar-chip${learn ? ' is-on' : ''}`} onClick={() => setLearn((v) => !v)} aria-expanded={learn}>🏒 Hockey 101</button></header>
      <p>{lines.join(' ')}</p>
      {bet ? <p className="nh-story__bet">AISP's paper bet on this game: <b>{bet}</b></p> : null}
      {learn ? (
        <dl className="nh-101">
          <div><dt>Shot on goal</dt><dd>A shot the goalie had to stop, or that went in. Shots that miss the net or hit a defender don't count.</dd></div>
          <div><dt>Dangerous chance</dt><dd>A shot from the "slot", the area right in front of the net. Most goals come from there.</dd></div>
          <div><dt>Save %</dt><dd>Saves divided by shots on goal. .900 is normal, .930 is excellent, under .880 is a rough night.</dd></div>
          <div><dt>Power play</dt><dd>After a penalty, one team plays a man up for 2 minutes. About 1 in 5 power plays ends in a goal.</dd></div>
          <div><dt>+/−</dt><dd>Goals for minus goals against while that player was on the ice at even strength.</dd></div>
          <div><dt>TOI</dt><dd>Time on ice. Players rotate in shifts of about 45 seconds; top defenders play 22+ minutes.</dd></div>
          <div><dt>Periods</dt><dd>Three 20-minute periods. Tied after 60? Five minutes of 3-on-3 overtime, next goal wins, then a shootout.</dd></div>
          <div><dt>Hit, giveaway, takeaway</dt><dd>A legal body check; losing the puck to the other team; stealing it from them.</dd></div>
        </dl>
      ) : null}
    </section>
  )
}

// ------------------------------------------------------- the living rink --

function RinkLines({ ctx }: { ctx: Ctx }) {
  const { home, away } = ctx.feed.teams
  return (
    <>
      <rect x="2" y="2" width="796" height="336" rx="112" className="ar-ice" />
      {home.logo ? <image href={home.logo} x={RX(0) - 36} y={RY(0) - 36} width="72" height="72" opacity="0.14" /> : null}
      <line x1={RX(0)} x2={RX(0)} y1="2" y2="338" className="ar-red" />
      <line x1={RX(-25)} x2={RX(-25)} y1="2" y2="338" className="ar-blue" /><line x1={RX(25)} x2={RX(25)} y1="2" y2="338" className="ar-blue" />
      <line x1={RX(-89)} x2={RX(-89)} y1="18" y2="322" className="ar-goalline" /><line x1={RX(89)} x2={RX(89)} y1="18" y2="322" className="ar-goalline" />
      {[[-69, 22], [-69, -22], [69, 22], [69, -22], [0, 0]].map(([x, y]) => <circle key={`${x},${y}`} cx={RX(x)} cy={RY(y)} r="60" className="ar-circle" />)}
      {[[-20, 22], [-20, -22], [20, 22], [20, -22]].map(([x, y]) => <circle key={`d${x},${y}`} cx={RX(x)} cy={RY(y)} r="5" className="ar-dot" />)}
      <path d={`M${RX(89)} ${RY(-4)} A24 24 0 0 0 ${RX(89)} ${RY(4)}`} className="ar-crease" /><path d={`M${RX(-89)} ${RY(-4)} A24 24 0 0 1 ${RX(-89)} ${RY(4)}`} className="ar-crease" />
      <rect x={RX(89)} y={RY(3)} width="14" height="24" className="ar-net" /><rect x={RX(-89) - 14} y={RY(3)} width="14" height="24" className="ar-net" />
      <text x={RX(-62)} y="330" className="ar-rink__lab" textAnchor="middle">← {away.abbr} ATTACK</text><text x={RX(62)} y="330" className="ar-rink__lab" textAnchor="middle">{home.abbr} ATTACK →</text>
    </>
  )
}

type Mode = 'recent' | 'replay' | 'new'

function LivingRink({ ctx, replay }: { ctx: Ctx; replay: { goal: RinkEvent; n: number } | null }) {
  const { feed, events, col, teamOf } = ctx
  const { home, away } = feed.teams
  const [lens, setLens] = useState<'flow' | 'heat' | 'shots'>('flow')
  const [team, setTeam] = useState<'all' | string>('all')
  const [caption, setCaption] = useState<RinkEvent | null>(null)
  const [call, setCall] = useState<RinkEvent | null>(null)
  const [run, setRun] = useState<{ mode: Mode; n: number }>({ mode: 'recent', n: 0 })
  const puckRef = useRef<SVGGElement | null>(null)
  const lastSeen = useRef<string | null | undefined>(undefined)
  const latest = events[events.length - 1] ?? null
  useEffect(() => { if (replay) setRun((r) => ({ mode: 'replay', n: r.n + 1 })) }, [replay])
  useEffect(() => {
    if (!latest) return
    if (lastSeen.current === undefined) { lastSeen.current = latest.id; return }
    if (latest.id !== lastSeen.current) { lastSeen.current = latest.id; setRun((r) => (r.mode === 'replay' ? r : { mode: 'new', n: r.n + 1 })) }
  }, [latest])
  useEffect(() => {
    // the recent run of play replays on its own, so the rink is never still
    const t = window.setInterval(() => setRun((r) => (r.mode === 'recent' ? { mode: 'recent', n: r.n + 1 } : r)), 18_000)
    return () => window.clearInterval(t)
  }, [])
  const eventsRef = useRef(events); eventsRef.current = events
  const colRef = useRef(col); colRef.current = col
  const replayRef = useRef(replay); replayRef.current = replay
  useEffect(() => {
    const el = puckRef.current
    const evs = eventsRef.current
    const seq = run.mode === 'replay' && replayRef.current ? goalSequence(evs, replayRef.current.goal) : evs.slice(-8)
    if (!el || !seq.length) return undefined
    const g = d3.select(el)
    g.selectAll('*').interrupt(); g.selectAll('*').remove()
    if (reduced()) { setCaption(seq[seq.length - 1]); return undefined }
    const pts = seq.map((e) => ({ e, x: RX(e.nx), y: RY(e.ny) }))
    const timers: number[] = []
    const trail = g.append('g')
    const puck = g.append('g').attr('class', 'nh-puck').attr('transform', `translate(${pts[0].x},${pts[0].y})`)
    puck.append('circle').attr('r', 14).attr('class', 'nh-puck__glow')
    puck.append('circle').attr('r', 6.5).attr('class', 'nh-puck__disc')
    const big = run.mode !== 'recent'
    const ping = (p: (typeof pts)[number]) => {
      const c = colRef.current[p.e.shooter ?? p.e.team ?? ''] ?? '#e2e8f0'
      setCaption(p.e)
      g.append('circle').attr('cx', p.x).attr('cy', p.y).attr('r', 4).attr('fill', 'none').attr('stroke', c).attr('stroke-width', 3)
        .transition().duration(900).ease(d3.easeCubicOut).attr('r', 26).style('opacity', 0).remove()
      g.append('text').attr('x', p.x).attr('y', p.y - 14).attr('text-anchor', 'middle').attr('class', `nh-callout nh-callout--${p.e.kind}`).style('fill', c).text(CALL[p.e.kind])
        .transition().duration(1400).ease(d3.easeCubicOut).attr('y', p.y - 36).style('opacity', 0).remove()
      if (p.e.kind === 'goal') explode(g, p.x, p.y, c, p.e.players[0]?.headshot ?? null, big)
      if (p.e.kind === 'goal' && big) { setCall(p.e); timers.push(window.setTimeout(() => setCall(null), 5200)) }
    }
    let i = 0
    const step = () => {
      i++
      if (i >= pts.length) { if (run.mode !== 'recent') timers.push(window.setTimeout(() => setRun((r) => ({ mode: 'recent', n: r.n })), 1500)); return }
      const a = pts[i - 1]; const b = pts[i]
      const dur = Math.min(1100, 280 + Math.hypot(b.x - a.x, b.y - a.y) * 2.1)
      trail.append('line').attr('x1', a.x).attr('y1', a.y).attr('x2', a.x).attr('y2', a.y).attr('class', 'nh-trail')
        .transition().duration(dur).ease(d3.easeQuadInOut).attr('x2', b.x).attr('y2', b.y)
        .transition().duration(3200).style('opacity', 0).remove()
      puck.transition().duration(dur).ease(d3.easeQuadInOut).attr('transform', `translate(${b.x},${b.y})`)
        .on('end', () => { ping(b); timers.push(window.setTimeout(step, b.e.kind === 'goal' ? 2400 : 420)) })
    }
    timers.push(window.setTimeout(() => { ping(pts[0]); timers.push(window.setTimeout(step, 420)) }, 300))
    return () => { timers.forEach((t) => window.clearTimeout(t)); g.selectAll('*').interrupt(); g.selectAll('*').remove() }
  }, [run])
  const attempts = events.filter((e) => isAttempt(e.kind) && (team === 'all' || e.shooter === team))
  const goals = events.filter((e) => e.kind === 'goal')
  const heat = useMemo(() => {
    if (lens !== 'heat') return []
    return [home, away].filter((t) => team === 'all' || t.id === team).map((t) => {
      const pts = events.filter((e) => e.shooter === t.id && (e.kind === 'shot' || e.kind === 'goal' || e.kind === 'miss')).map((e) => [RX(e.nx), RY(e.ny)] as [number, number])
      const dens = d3.contourDensity<[number, number]>().x((d) => d[0]).y((d) => d[1]).size([800, 340]).bandwidth(26).thresholds(7)(pts)
      return { team: t, dens, max: d3.max(dens, (d) => d.value) ?? 1 }
    })
  }, [lens, team, events, home, away])
  const geo = d3.geoPath()
  const mode = run.mode
  return (
    <section className="nh-card nh-rink" aria-label="The living rink">
      <header>
        <h2>The Living Rink <small>{mode === 'replay' ? 'replaying the goal from its faceoff' : mode === 'new' ? 'the newest play' : 'the last run of play, on a loop'}</small></h2>
        <span className="ar-seg">{([['flow', 'Puck + shots'], ['heat', '🔥 Heat map'], ['shots', 'Every attempt']] as const).map(([k, l]) => <button key={k} type="button" className={lens === k ? 'is-on' : ''} onClick={() => setLens(k)}>{l}</button>)}</span>
        <span className="ar-seg">{[['all', 'Both'], [away.id, away.abbr], [home.id, home.abbr]].map(([k, l]) => <button key={k} type="button" className={team === k ? 'is-on' : ''} onClick={() => setTeam(k)}>{l}</button>)}</span>
      </header>
      <div className="ar-rink__wrap">
        <svg viewBox="0 0 800 340" role="img" aria-label="Rink with the moving puck, every attempt and every goal">
          <defs>{goals.map((e) => <clipPath key={e.id} id={`nh-face-${e.id}`}><circle cx={RX(e.nx)} cy={RY(e.ny)} r="15" /></clipPath>)}</defs>
          <RinkLines ctx={ctx} />
          {lens === 'heat' ? heat.map((h) => <g key={h.team.id} className="nh-heat">{h.dens.map((d, k) => <path key={k} d={geo(d) ?? ''} fill={col[h.team.id]} fillOpacity={0.06 + 0.5 * (d.value / h.max)} stroke={col[h.team.id]} strokeOpacity={0.3} style={{ animationDelay: `${k * 90}ms` }} />)}</g>) : null}
          {attempts.filter((e) => e.kind !== 'goal').map((e) => {
            const c = col[e.shooter ?? ''] ?? '#94a3b8'
            const x = RX(e.nx); const y = RY(e.ny)
            const op = lens === 'heat' ? 0.25 : lens === 'flow' ? (e.danger === 'high' ? 0.9 : 0.5) : 0.9
            const r = e.danger === 'high' ? 6.5 : e.danger === 'med' ? 5 : 4
            if (e.kind === 'block') return <rect key={e.id} x={x - 3.5} y={y - 3.5} width="7" height="7" fill="none" stroke={c} strokeWidth="1.6" opacity={op * 0.8}><title>{e.text}</title></rect>
            return <circle key={e.id} className="ar-shot" cx={x} cy={y} r={r} fill={e.kind === 'shot' ? c : 'none'} stroke={e.kind === 'shot' ? '#0b1526' : c} strokeWidth={e.kind === 'shot' ? 1 : 1.8} opacity={op}><title>{e.text}</title></circle>
          })}
          {goals.filter((e) => team === 'all' || e.shooter === team).map((e) => {
            const c = col[e.shooter ?? ''] ?? '#ef4444'; const x = RX(e.nx); const y = RY(e.ny); const face = e.players[0]?.headshot
            return (
              <g key={e.id} className="nh-goalface" onClick={() => { if (e.players[0]) ctx.openPlayer(e.players[0].id) }}>
                <circle cx={x} cy={y} r="19" className="ar-goal__ring" style={{ stroke: c }} />
                <circle cx={x} cy={y} r="16.5" fill="#0b1526" stroke={c} strokeWidth="3" />
                {face ? <image href={face} x={x - 15} y={y - 15} width="30" height="30" clipPath={`url(#nh-face-${e.id})`} preserveAspectRatio="xMidYMin slice" /> : <text x={x} y={y + 5} textAnchor="middle" fill="#fff" fontWeight="900">★</text>}
                <title>{e.text}</title>
              </g>
            )
          })}
          <g ref={puckRef} />
        </svg>
        {call ? <GoalBurst e={call} color={col[call.shooter ?? ''] ?? '#ef4444'} team={teamOf(call.shooter)} /> : null}
      </div>
      <div className="nh-caption" style={{ ['--c' as string]: caption ? col[caption.shooter ?? caption.team ?? ''] ?? '#94a3b8' : '#94a3b8' } as CSSProperties} aria-live="polite">
        {caption ? (<>
          {caption.players[0]?.headshot ? <img src={caption.players[0].headshot} alt="" /> : <i />}
          <span><b>{CALL[caption.kind]}</b> <small>{periodName('nhl', caption.period)} {caption.clock}{caption.strength && caption.strength !== 'Even Strength' ? ` · ${caption.strength}` : ''}</small><br />{caption.text}</span>
        </>) : <span>Waiting for the first play…</span>}
      </div>
      <div className="ar-rink__key"><span>● shot on goal</span><span>○ missed the net</span><span>□ blocked</span><span>bigger dot = closer to the net</span><span>faces = goal scorers (tap one)</span>{lens === 'heat' ? <span className="is-slot">heat = where each team shoots from</span> : null}</div>
    </section>
  )
}

/** The red light: the rink flashes, rings and sparks burst from the spot and the scorer's face blows up there. */
function explode(g: d3.Selection<SVGGElement, unknown, null, undefined>, x: number, y: number, c: string, face: string | null, big: boolean) {
  const flash = g.append('rect').attr('x', 2).attr('y', 2).attr('width', 796).attr('height', 336).attr('rx', 112).attr('class', 'nh-redlight').style('opacity', 0)
  flash.transition().duration(200).style('opacity', 0.55).transition().duration(200).style('opacity', 0.1).transition().duration(200).style('opacity', 0.5).transition().duration(800).style('opacity', 0).remove()
  for (let k = 0; k < 3; k++) {
    g.append('circle').attr('cx', x).attr('cy', y).attr('r', 6).attr('fill', 'none').attr('stroke', k === 1 ? '#fff' : c).attr('stroke-width', 5 - k)
      .transition().delay(k * 160).duration(1300).ease(d3.easeCubicOut).attr('r', 90 + k * 40).style('opacity', 0).remove()
  }
  for (let k = 0; k < 18; k++) {
    const a = (k / 18) * Math.PI * 2; const r = 40 + (k % 3) * 22
    g.append('line').attr('x1', x).attr('y1', y).attr('x2', x).attr('y2', y).attr('stroke', k % 2 ? '#fde68a' : c).attr('stroke-width', 3).attr('stroke-linecap', 'round')
      .transition().duration(700).ease(d3.easeExpOut).attr('x1', x + Math.cos(a) * r * 0.5).attr('y1', y + Math.sin(a) * r * 0.5).attr('x2', x + Math.cos(a) * r).attr('y2', y + Math.sin(a) * r)
      .transition().duration(500).style('opacity', 0).remove()
  }
  if (face && big) {
    const id = `nh-boom-${Math.round(x)}-${Math.round(y)}`
    const clip = g.append('clipPath').attr('id', id)
    clip.append('circle').attr('cx', x).attr('cy', y).attr('r', 34)
    g.append('circle').attr('cx', x).attr('cy', y).attr('r', 36).attr('fill', '#0b1526').attr('stroke', c).attr('stroke-width', 4).style('opacity', 0)
      .transition().duration(300).style('opacity', 1).transition().delay(1800).duration(600).style('opacity', 0).remove()
    g.append('image').attr('href', face).attr('x', x - 34).attr('y', y - 34).attr('width', 68).attr('height', 68).attr('clip-path', `url(#${id})`).style('opacity', 0)
      .transition().duration(300).style('opacity', 1).transition().delay(1800).duration(600).style('opacity', 0).remove()
    clip.transition().delay(3000).remove()
  }
}

function GoalBurst({ e, color, team }: { e: RinkEvent; color: string; team: ArenaTeam }) {
  const who = e.players.find((p) => p.role === 'scorer') ?? e.players[0]
  const helpers = e.players.filter((p) => p.role === 'assister')
  return (
    <div className="nh-burst" style={{ ['--c' as string]: color } as CSSProperties}>
      <i className="ar-goalcall__light" />
      <b>GOAL!</b>
      <span className="nh-burst__who">
        {who?.headshot ? <img src={who.headshot} alt="" className="nh-burst__face" /> : null}
        <span><em>{who?.name ?? team.short}</em><small>{team.logo ? <img src={team.logo} alt="" /> : null}{team.short} · {periodName('nhl', e.period)} {e.clock}{e.strength && e.strength !== 'Even Strength' ? ` · ${e.strength}` : ''}</small>
          <small>{helpers.length ? `Assists: ${helpers.map((h) => h.short ?? h.name).join(', ')}` : 'Unassisted'}</small></span>
      </span>
    </div>
  )
}

// --------------------------------------------------------- goal moments --

function GoalMoments({ ctx, onReplay }: { ctx: Ctx; onReplay: (g: RinkEvent) => void }) {
  const goals = ctx.events.filter((e) => e.kind === 'goal')
  if (!goals.length) return null
  return (
    <section className="nh-card nh-moments" aria-label="Goal moments">
      <header><h2>Goal Moments <small>tap ▶ to watch the puck go from the faceoff to the net</small></h2></header>
      <ol>
        {goals.map((g, k) => {
          const who = g.players.find((p) => p.role === 'scorer') ?? g.players[0]
          const t = ctx.teamOf(g.shooter)
          return (
            <li key={g.id} style={{ ['--c' as string]: ctx.col[g.shooter ?? ''] ?? '#94a3b8', animationDelay: `${k * 120}ms` } as CSSProperties}>
              <button type="button" className="nh-moments__face" onClick={() => { if (who) ctx.openPlayer(who.id) }} aria-label={`${who?.name ?? 'Scorer'} profile`}>{who?.headshot ? <img src={who.headshot} alt="" /> : <i>★</i>}</button>
              <span className="nh-moments__txt"><b>{who?.name ?? t.short}</b><small>{t.abbr} · {periodName('nhl', g.period)} {g.clock}{g.strength && g.strength !== 'Even Strength' ? ` · ${g.strength}` : ''}</small><small>{ctx.feed.teams.away.abbr} {g.away ?? '–'} – {g.home ?? '–'} {ctx.feed.teams.home.abbr}</small></span>
              <button type="button" className="nh-moments__play" onClick={() => onReplay(g)} aria-label={`Replay the goal by ${who?.name ?? t.short}`}>▶</button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

// ------------------------------------------------------- the win gauge --

function WinGauge({ ctx, series, prior, priorSource }: { ctx: Ctx; series: WinPoint[]; prior: number; priorSource: string }) {
  const { home, away } = ctx.feed.teams
  const now = series[series.length - 1]?.p ?? prior
  const needle = useRef<SVGGElement | null>(null)
  const label = useRef<SVGTextElement | null>(null)
  const prev = useRef(0.5)
  useEffect(() => {
    const from = prev.current; prev.current = now
    const ang = (p: number) => -90 + p * 180
    const dur = reduced() ? 0 : 1800
    if (needle.current) d3.select(needle.current).transition().duration(dur).ease(d3.easeElasticOut.amplitude(1).period(0.5)).attrTween('transform', () => (t: number) => `rotate(${ang(from + (now - from) * t)} 150 132)`)
    const el = label.current
    if (el) d3.select(el).transition().duration(dur).ease(d3.easeCubicOut).tween('text', () => { const i = d3.interpolate(from, now); return (t: number) => { const v = i(t); el.textContent = pct(Math.max(v, 1 - v)) } })
  }, [now])
  const W = 520; const H = 150; const pad = 18
  const end = Math.max(3600, series[series.length - 1]?.t ?? 0)
  const X = (t: number) => pad + (t / end) * (W - pad * 2)
  const Y = (p: number) => 10 + (1 - p) * (H - 30)
  const line = d3.line<WinPoint>().x((d) => X(d.t)).y((d) => Y(d.p))(series) ?? ''
  const area = d3.area<WinPoint>().x((d) => X(d.t)).y0(Y(0.5)).y1((d) => Y(d.p))(series) ?? ''
  const swings = series.filter((s) => s.goal).map((s) => ({ ...s, before: series.find((x) => x.t === s.t - 1)?.p ?? prior }))
  const biggest = swings.reduce<(typeof swings)[number] | null>((b, s) => (!b || Math.abs(s.p - s.before) > Math.abs(b.p - b.before) ? s : b), null)
  const fav = now >= 0.5 ? home : away
  const arc = (a0: number, a1: number) => d3.arc()({ innerRadius: 92, outerRadius: 118, startAngle: a0, endAngle: a1 }) ?? ''
  return (
    <section className="nh-card nh-wp" aria-label="AISP live win estimate">
      <header><h2>Who Wins? <small>AISP live estimate</small></h2></header>
      <svg viewBox="0 0 300 196" className="nh-wp__gauge" role="img" aria-label={`${fav.short} ${pct(Math.max(now, 1 - now))} to win`}>
        <g transform="translate(150 132)">
          <path d={arc(-Math.PI / 2, -0.012)} fill={ctx.col[away.id]} opacity={now < 0.5 ? 0.95 : 0.4} className="nh-wp__arc" />
          <path d={arc(0.012, Math.PI / 2)} fill={ctx.col[home.id]} opacity={now >= 0.5 ? 0.95 : 0.4} className="nh-wp__arc" />
          {[0.1, 0.25, 0.75, 0.9].map((v) => { const a = -Math.PI / 2 + v * Math.PI; return <line key={v} x1={Math.sin(a) * 86} y1={-Math.cos(a) * 86} x2={Math.sin(a) * 80} y2={-Math.cos(a) * 80} stroke="#7f8b9d" strokeWidth="2" /> })}
        </g>
        <g ref={needle} transform="rotate(0 150 132)"><path d="M146 132 L150 30 L154 132 Z" fill="#fff" /><circle cx="150" cy="132" r="10" fill="#fff" /><circle cx="150" cy="132" r="4" fill="#0b1526" /></g>
        {away.logo ? <image href={away.logo} x="6" y="136" width="36" height="36" /> : null}
        {home.logo ? <image href={home.logo} x="258" y="136" width="36" height="36" /> : null}
        <text x="150" y="170" textAnchor="middle" className="nh-wp__big" ref={label}>{pct(Math.max(now, 1 - now))}</text>
        <text x="150" y="190" textAnchor="middle" className="nh-wp__who" fill={ctx.col[fav.id]}>{Math.abs(now - 0.5) < 0.005 ? 'Coin flip' : `${fav.short} to win`}</text>
      </svg>
      <svg viewBox={`0 0 ${W} ${H}`} className="nh-wp__river" role="img" aria-label="Win estimate over the game">
        <defs><clipPath id="nh-up"><rect x="0" y="0" width={W} height={Y(0.5)} /></clipPath><clipPath id="nh-dn"><rect x="0" y={Y(0.5)} width={W} height={H} /></clipPath></defs>
        {[1, 2].map((k) => <line key={k} x1={X(k * 1200)} x2={X(k * 1200)} y1={6} y2={H - 18} className="ar-flow__grid" />)}
        {[0, 1, 2].map((k) => <text key={k} x={X(k * 1200 + 600)} y={H - 4} textAnchor="middle" className="ar-flow__tick">{periodName('nhl', k + 1)}</text>)}
        <line x1={pad} x2={W - pad} y1={Y(0.5)} y2={Y(0.5)} className="ar-flow__zero" />
        <path d={area} fill={ctx.col[home.id]} opacity="0.35" clipPath="url(#nh-up)" className="nh-fadein" />
        <path d={area} fill={ctx.col[away.id]} opacity="0.35" clipPath="url(#nh-dn)" className="nh-fadein" />
        <path d={line} className="nh-wp__line" pathLength={1} />
        <text x={pad + 2} y={16} className="ar-flow__lab" fill={ctx.col[home.id]}>{home.abbr} ↑</text><text x={pad + 2} y={H - 22} className="ar-flow__lab" fill={ctx.col[away.id]}>{away.abbr} ↓</text>
        {swings.map((s) => {
          const g = s.goal!; const face = g.players[0]?.headshot; const x = X(s.t); const y = Y(s.p); const id = `nh-wpf-${g.id}`
          return (
            <g key={g.id} className="nh-wp__goal" onClick={() => { if (g.players[0]) ctx.openPlayer(g.players[0].id) }}>
              <clipPath id={id}><circle cx={x} cy={y} r="11" /></clipPath>
              <circle cx={x} cy={y} r="13" fill="#0b1526" stroke={ctx.col[g.shooter ?? '']} strokeWidth="2.5" />
              {face ? <image href={face} x={x - 11} y={y - 11} width="22" height="22" clipPath={`url(#${id})`} /> : null}
              <title>{`${g.text} (${home.abbr} ${s.p >= s.before ? '+' : '−'}${Math.abs(Math.round((s.p - s.before) * 100))} points)`}</title>
            </g>
          )
        })}
      </svg>
      {biggest ? <p className="nh-wp__swing">Biggest swing: <b>{biggest.goal!.players[0]?.name ?? 'a goal'}</b> moved it {Math.abs(Math.round((biggest.p - biggest.before) * 100))} points.</p> : null}
      <p className="ar-small">A simple model: the score, the clock and the pregame price ({priorSource}: {home.abbr} {pct(prior)}), with goals arriving at the league's usual rate. Not AISP's betting model and never used for bets.</p>
    </section>
  )
}

// ------------------------------------------------------- goalie duel --

function useMounted() {
  const [on, setOn] = useState(false)
  useEffect(() => { const r = window.setTimeout(() => setOn(true), 60); return () => window.clearTimeout(r) }, [])
  return on
}

function GoalieDuel({ ctx }: { ctx: Ctx }) {
  const { feed } = ctx
  const on = useMounted()
  const goalie = (t: ArenaTeam) => feed.athletes.filter((a) => a.team === t.id && a.group === 'goalies' && a.stats.saves != null).sort((a, b) => num(b.stats.shotsAgainst) - num(a.stats.shotsAgainst))[0]
  const pair = [feed.teams.away, feed.teams.home].map((t) => ({ t, g: goalie(t) })).filter((x): x is { t: ArenaTeam; g: ArenaAthlete } => !!x.g)
  if (!pair.length) return null
  const C = 2 * Math.PI * 46
  return (
    <section className="nh-card nh-duel" aria-label="Goalie duel">
      <header><h2>Goalie Duel <small>the ring fills with save %</small></h2></header>
      <div className="nh-duel__grid">
        {pair.map(({ t, g }) => {
          const sv = num(g.stats.shotsAgainst) ? num(g.stats.saves) / num(g.stats.shotsAgainst) : 0
          const c = ctx.col[t.id]
          return (
            <button key={t.id} type="button" className="nh-duel__g" onClick={() => ctx.openPlayer(g.id)} style={{ ['--c' as string]: c } as CSSProperties}>
              <svg viewBox="0 0 110 110" aria-hidden="true">
                <circle cx="55" cy="55" r="46" className="nh-duel__track" />
                <circle cx="55" cy="55" r="46" className="nh-duel__ring" stroke={c} strokeDasharray={`${C}`} strokeDashoffset={on ? C * (1 - sv) : C} transform="rotate(-90 55 55)" />
                <clipPath id={`nh-gk-${g.id}`}><circle cx="55" cy="55" r="38" /></clipPath>
                {g.headshot ? <image href={g.headshot} x="17" y="17" width="76" height="76" clipPath={`url(#nh-gk-${g.id})`} /> : null}
              </svg>
              <b>{g.name}</b>
              <strong>{g.stats.savePct ?? '—'}</strong>
              <small>{g.stats.saves} saves on {g.stats.shotsAgainst} shots · {t.abbr}</small>
            </button>
          )
        })}
      </div>
    </section>
  )
}

// ------------------------------------------------------- tug of war --

function TugOfWar({ ctx }: { ctx: Ctx }) {
  const { feed, events } = ctx
  const { home, away } = feed.teams
  const on = useMounted()
  const ev = (id: string, f: (e: RinkEvent) => boolean) => events.filter((e) => e.shooter === id && f(e)).length
  const sum = (id: string, k: string) => feed.athletes.filter((a) => a.team === id).reduce((s, a) => s + num(a.stats[k]), 0)
  const rows: Array<[string, (id: string) => number, string, boolean]> = [
    ['Shots on goal', (id) => ev(id, (e) => e.kind === 'shot' || e.kind === 'goal'), 'Shots the goalie had to face', true],
    ['All shot attempts', (id) => ev(id, (e) => isAttempt(e.kind)), 'Includes misses and blocked shots: who has the puck more', true],
    ['Dangerous chances', (id) => ev(id, (e) => e.danger === 'high' && e.kind !== 'block'), 'Shots from right in front of the net', true],
    ['Faceoffs won', (id) => sum(id, 'faceoffsWon'), 'Winning the drop gives your team the puck', true],
    ['Hits', (id) => sum(id, 'hits'), 'Body checks thrown', true],
    ['Blocked shots', (id) => sum(id, 'blockedShots'), 'Players throwing themselves in front of shots', true],
    ['Takeaways', (id) => sum(id, 'takeaways'), 'Pucks stolen from the other team', true],
    ['Giveaways', (id) => sum(id, 'giveaways'), 'Pucks lost to the other team: fewer is better', false],
    ['Penalty minutes', (id) => sum(id, 'penaltyMinutes'), 'Time spent in the penalty box: fewer is better', false],
  ]
  return (
    <section className="nh-card nh-tug" aria-label="Tug of war">
      <header><h2>Tug of War <small>who is winning each battle</small></h2></header>
      <div className="nh-tug__teams"><b style={{ color: ctx.col[away.id] }}>{away.logo ? <img src={away.logo} alt="" /> : null}{away.abbr}</b><b style={{ color: ctx.col[home.id] }}>{home.abbr}{home.logo ? <img src={home.logo} alt="" /> : null}</b></div>
      {rows.map(([label, f, hint, more]) => {
        const a = f(away.id); const h = f(home.id); const share = a + h ? a / (a + h) : 0.5
        const aw = more ? a > h : a < h; const hw = more ? h > a : h < a
        return (
          <div key={label} className="nh-tug__row" title={hint}>
            <span className={aw ? 'is-win' : ''}>{a}</span>
            <div className="nh-tug__bar"><i style={{ width: `${(on ? share : 0.5) * 100}%`, background: ctx.col[away.id] }} /><i style={{ width: `${(on ? 1 - share : 0.5) * 100}%`, background: ctx.col[home.id] }} /><em>{label}</em></div>
            <span className={hw ? 'is-win' : ''}>{h}</span>
          </div>
        )
      })}
    </section>
  )
}

// ------------------------------------------------------- stars + on ice --

function starScore(a: ArenaAthlete): number {
  if (a.group === 'goalies') return num(a.stats.shotsAgainst) >= 15 ? (num(a.stats.savePct) - 0.88) * 120 + num(a.stats.saves) * 0.08 : -1
  return num(a.stats.goals) * 3 + num(a.stats.assists) * 2 + num(a.stats.shotsTotal) * 0.3 + num(a.stats.plusMinus) * 0.4 + num(a.stats.hits) * 0.1
}
function statLine(a: ArenaAthlete): string {
  if (a.group === 'goalies') return `${a.stats.saves} saves · ${a.stats.savePct} · ${a.stats.timeOnIce} on ice`
  return [num(a.stats.goals) ? `${a.stats.goals} G` : '', num(a.stats.assists) ? `${a.stats.assists} A` : '', `${a.stats.shotsTotal ?? 0} shots`, `${a.stats.timeOnIce ?? '—'} on ice`].filter(Boolean).join(' · ')
}

function Stars({ ctx }: { ctx: Ctx }) {
  const stars = ctx.feed.athletes.map((a) => ({ a, s: starScore(a) })).filter((x) => x.s > 0).sort((x, y) => y.s - x.s).slice(0, 3)
  if (!stars.length) return null
  return (
    <section className="nh-card nh-stars" aria-label="Three stars">
      <header><h2>Three Stars <small>so far · tap for the profile</small></h2></header>
      <div className="nh-stars__grid">
        {stars.map(({ a }, k) => {
          const t = ctx.teamOf(a.team)
          return (
            <button key={a.id} type="button" className="nh-star" onClick={() => ctx.openPlayer(a.id)} style={{ ['--c' as string]: ctx.col[t.id], animationDelay: `${k * 140}ms` } as CSSProperties}>
              <span className="nh-star__n">{'★'.repeat(3 - k)} {['1st', '2nd', '3rd'][k]} star</span>
              {t.logo ? <img src={t.logo} alt="" className="nh-star__logo" /> : null}
              {a.headshot ? <img src={a.headshot} alt="" className="nh-star__face" /> : <i className="nh-star__face">{a.jersey}</i>}
              <b>{a.name}</b>
              <small>{a.pos}{a.jersey ? ` #${a.jersey}` : ''} · {t.abbr}</small>
              <em>{statLine(a)}</em>
            </button>
          )
        })}
      </div>
    </section>
  )
}

function OnIceNow({ ctx }: { ctx: Ctx }) {
  const { feed } = ctx
  if (!feed.on_ice.length) return null
  return (
    <section className="nh-card nh-onice" aria-label="On the ice now">
      <header><h2>On the Ice Now <small>tap a player</small></h2></header>
      {[feed.teams.away, feed.teams.home].map((t) => (
        <div key={t.id} className="nh-onice__row" style={{ ['--c' as string]: ctx.col[t.id] } as CSSProperties}>
          {t.logo ? <img src={t.logo} alt={t.abbr} className="nh-onice__logo" /> : <b>{t.abbr}</b>}
          <span>{feed.on_ice.filter((p) => p.team === t.id).sort((a, b) => (a.pos === 'G' ? 1 : 0) - (b.pos === 'G' ? 1 : 0)).map((p) => (
            <button key={p.id} type="button" onClick={() => ctx.openPlayer(p.id)}>{p.headshot ? <img src={p.headshot} alt="" /> : <i>{p.jersey ?? '?'}</i>}<small>{p.short ?? p.name}</small><em>{p.pos}{p.jersey ? ` #${p.jersey}` : ''}</em></button>
          ))}</span>
        </div>
      ))}
    </section>
  )
}

// ------------------------------------------------------- momentum --

function Momentum({ ctx }: { ctx: Ctx }) {
  const { feed, events, col } = ctx
  const { home, away } = feed.teams
  const W = 640; const H = 170; const pad = 24
  const att = events.filter((e) => isAttempt(e.kind))
  const total = Math.max(3600, ...att.map((e) => e.t))
  let d = 0
  const pts = [{ t: 0, d: 0 }, ...att.map((e) => { d += e.shooter === home.id ? 1 : -1; return { t: e.t, d } })]
  const maxD = Math.max(6, ...pts.map((p) => Math.abs(p.d)))
  const X = (t: number) => pad + (t / total) * (W - pad * 2)
  const Y = (v: number) => H / 2 - (v / maxD) * (H / 2 - 14)
  const line = d3.line<{ t: number; d: number }>().x((p) => X(p.t)).y((p) => Y(p.d)).curve(d3.curveMonotoneX)(pts) ?? ''
  const area = d3.area<{ t: number; d: number }>().x((p) => X(p.t)).y0(H / 2).y1((p) => Y(p.d)).curve(d3.curveMonotoneX)(pts) ?? ''
  const last = pts[pts.length - 1]
  return (
    <section className="nh-card ar-flow" aria-label="Momentum">
      <header><h2>Momentum <small>shot attempts: {home.abbr} above, {away.abbr} below</small></h2><b className="ar-flow__now" style={{ color: last.d >= 0 ? col[home.id] : col[away.id] }}>{last.d === 0 ? 'Even' : `${last.d > 0 ? home.abbr : away.abbr} +${Math.abs(last.d)}`}</b></header>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Shot attempt differential over time">
        <defs><clipPath id="nh-mabove"><rect x="0" y="0" width={W} height={H / 2} /></clipPath><clipPath id="nh-mbelow"><rect x="0" y={H / 2} width={W} height={H / 2} /></clipPath></defs>
        {[0, 1, 2].map((i) => <g key={i}><line x1={X(i * 1200)} x2={X(i * 1200)} y1={8} y2={H - 8} className="ar-flow__grid" /><text x={X(i * 1200) + 4} y={H - 4} className="ar-flow__tick">{periodName('nhl', i + 1)}</text></g>)}
        <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="ar-flow__zero" />
        <path d={area} fill={col[home.id]} opacity="0.35" clipPath="url(#nh-mabove)" className="nh-fadein" />
        <path d={area} fill={col[away.id]} opacity="0.35" clipPath="url(#nh-mbelow)" className="nh-fadein" />
        <path d={line} className="nh-wp__line" pathLength={1} />
        {events.filter((e) => e.kind === 'goal').map((e) => { const x = X(e.t); const up = e.shooter === home.id; return <g key={e.id}><line x1={x} x2={x} y1={14} y2={H - 14} stroke={up ? col[home.id] : col[away.id]} strokeDasharray="3 3" /><text x={x} y={up ? 12 : H - 16} textAnchor="middle" className="ar-flow__goal">🚨</text></g> })}
      </svg>
    </section>
  )
}

// ------------------------------------------------------- the profile drawer --

function PlayerDrawer({ ctx, id, onClose }: { ctx: Ctx; id: string; onClose: () => void }) {
  const { feed, events, col } = ctx
  const closeRef = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    closeRef.current?.focus()
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const a = feed.athletes.find((x) => x.id === id)
  const fromPlay = feed.plays.flatMap((p) => p.players).find((p) => p.id === id)
  const iceMate = feed.on_ice.find((p) => p.id === id)
  const name = a?.name ?? fromPlay?.name ?? iceMate?.name ?? 'Player'
  const face = a?.headshot ?? fromPlay?.headshot ?? iceMate?.headshot ?? null
  const teamId = a?.team ?? iceMate?.team ?? feed.plays.find((p) => p.players[0]?.id === id)?.team ?? null
  const t = ctx.teamOf(teamId)
  const c = col[t.id] ?? '#94a3b8'
  const mine = events.filter((e) => e.players.some((p) => p.id === id))
  const shots = mine.filter((e) => e.players[0]?.id === id && (e.kind === 'shot' || e.kind === 'goal' || e.kind === 'miss'))
  const s = a?.stats ?? {}
  const goalie = a?.group === 'goalies'
  const cells: Array<[string, string | undefined, string]> = goalie
    ? [['Saves', s.saves, 'shots stopped'], ['Shots faced', s.shotsAgainst, 'on goal'], ['Save %', s.savePct, '.900 is normal'], ['Goals allowed', s.goalsAgainst, ''], ['PP saves', s.powerPlaySaves, 'while a man down'], ['Time on ice', s.timeOnIce, '']]
    : [['Goals', s.goals, ''], ['Assists', s.assists, 'passes before a goal'], ['Shots', s.shotsTotal, 'on goal'], ['+/−', s.plusMinus, 'even-strength goal swing'], ['Time on ice', s.timeOnIce, `${s.shifts ?? '—'} shifts`], ['Hits', s.hits, 'body checks'], ['Blocked', s.blockedShots, 'shots blocked'], ['Faceoffs', num(s.faceoffsWon) + num(s.faceoffsLost) ? `${s.faceoffsWon}–${s.faceoffsLost}` : '—', s.faceoffPercent ? `${s.faceoffPercent}% won` : ''], ['Season goals', s.ytdGoals, 'this season'], ['Penalty min', s.penaltyMinutes, '']]
  return (
    <div className="nh-drawer" role="dialog" aria-modal="true" aria-label={`${name} profile`} onClick={onClose}>
      <aside style={{ ['--c' as string]: c } as CSSProperties} onClick={(e) => e.stopPropagation()}>
        <button type="button" ref={closeRef} className="nh-drawer__x" onClick={onClose} aria-label="Close profile">✕</button>
        <div className="nh-drawer__hero">
          {t.logo ? <img src={t.logo} alt="" className="nh-drawer__logo" /> : null}
          {face ? <img src={face} alt="" className="nh-drawer__face" /> : <i className="nh-drawer__face" />}
          <span><b>{name}</b><small>{a?.pos ?? iceMate?.pos ?? ''}{a?.jersey ? ` · #${a.jersey}` : ''} · {t.name}</small></span>
        </div>
        {a ? <div className="nh-drawer__stats">{cells.map(([l, v, h]) => <span key={l}><small>{l}</small><b>{v ?? '—'}</b>{h ? <em>{h}</em> : null}</span>)}</div> : <p className="ar-small">No box-score line for this player yet.</p>}
        {!goalie ? (
          <>
            <h3>Their shots tonight</h3>
            <svg viewBox="0 0 800 340" className="nh-drawer__rink" role="img" aria-label={`${name}'s shots`}>
              <RinkLines ctx={ctx} />
              {shots.map((e, k) => <circle key={e.id} className="ar-shot" style={{ animationDelay: `${k * 90}ms` }} cx={RX(e.nx)} cy={RY(e.ny)} r={e.kind === 'goal' ? 12 : 8} fill={e.kind === 'miss' ? 'none' : c} stroke={e.kind === 'goal' ? '#fde68a' : c} strokeWidth={e.kind === 'goal' ? 4 : 2.5}><title>{e.text}</title></circle>)}
            </svg>
            {!shots.length ? <p className="ar-small">No shot attempts yet.</p> : null}
          </>
        ) : null}
        <h3>Their plays</h3>
        <ol className="nh-drawer__plays">
          {mine.slice(-12).reverse().map((e) => <li key={e.id} className={e.kind === 'goal' ? 'is-goal' : ''}><span>{periodName('nhl', e.period)} {e.clock}</span><b>{CALL[e.kind]}</b><em>{e.text}</em></li>)}
          {!mine.length ? <li><em>Nothing on the play-by-play yet.</em></li> : null}
        </ol>
      </aside>
    </div>
  )
}
