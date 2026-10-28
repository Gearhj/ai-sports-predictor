/**
 * Two big graphics outside the rink (owner 10-25 22:41: "no HOLY SHIT graphics outside of the rink").
 *
 * GamePulse: the game as a heartbeat. Every shot attempt is a spike (up = home, down = away; height = how dangerous),
 * hits are small blips, goals are towering spikes crowned with the scorer's face. It draws itself on load, a bright
 * dot beats at "now", and every new event thumps the strip.
 * HeatWall: every skater's face on one wall, sized and lit by tonight's impact (goals, assists, shots, hits, blocks),
 * reshuffling live as the game changes; hot players burn. Tap a face for the profile.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { elapsedAt, periodName, type ArenaFeed } from './arenaData'
import { isAttempt, type RinkEvent } from './nhlModel'

export function GamePulse({ feed, events, col }: { feed: ArenaFeed; events: RinkEvent[]; col: Record<string, string> }) {
  const { home, away } = feed.teams
  // A slim strip (owner 10-26: the rink is the focus; the pulse sat too big): wide viewBox, short height.
  const W = 2000; const H = 150; const mid = H / 2; const pad = 16
  const end = Math.max(3600, ...events.map((e) => e.t))
  const X = (t: number) => pad + (t / end) * (W - pad * 2)
  const beats = events.filter((e) => isAttempt(e.kind) || e.kind === 'hit')
  const amp = (e: RinkEvent) => (e.kind === 'goal' ? 46 : e.kind === 'hit' ? 6 : e.danger === 'high' ? 32 : e.danger === 'med' ? 20 : 11)
  // one continuous trace per team: flat, then a sharp spike at each beat
  const trace = (team: string, up: boolean) => {
    const pts: Array<[number, number]> = [[X(0), mid]]
    for (const e of beats.filter((b) => (b.kind === 'hit' ? b.team : b.shooter) === team)) {
      const x = X(e.t); const a = amp(e) * (up ? -1 : 1)
      pts.push([x - 4, mid], [x - 1.5, mid + a * 0.95], [x, mid + a], [x + 2, mid - a * 0.18], [x + 5, mid])
    }
    pts.push([X(feed.status.state === 'in' ? elapsedAt('nhl', feed.status.period, feed.status.clock) : Math.min(end, events.length ? events[events.length - 1].t : 0)), mid])
    return d3.line()(pts) ?? ''
  }
  const last = events[events.length - 1] ?? null
  const [thump, setThump] = useState(false)
  const seen = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (!last) return
    if (seen.current === undefined) { seen.current = last.id; return }
    if (last.id !== seen.current) { seen.current = last.id; setThump(true); const t = window.setTimeout(() => setThump(false), 700); return () => window.clearTimeout(t) }
    return undefined
  }, [last])
  const nowT = feed.status.state === 'in' ? elapsedAt('nhl', feed.status.period, feed.status.clock) : last?.t ?? 0
  const nowX = X(nowT)
  const goals = events.filter((e) => e.kind === 'goal')
  const rate = (id: string) => beats.filter((b) => b.shooter === id && isAttempt(b.kind) && last && b.t > last.t - 300).length
  return (
    <section className={`nh-card nh-pulse${thump ? ' is-thump' : ''}`} aria-label="Game pulse">
      <header>
        <h2>Game Pulse <small>every shot is a heartbeat · up = {home.abbr}, down = {away.abbr} · taller = more dangerous</small></h2>
        <span className="nh-pulse__bpm"><b style={{ color: col[home.id] }}>{rate(home.id)}</b><small>{home.abbr} shots / 5 min</small></span>
        <span className="nh-pulse__bpm"><b style={{ color: col[away.id] }}>{rate(away.id)}</b><small>{away.abbr} shots / 5 min</small></span>
      </header>
      <svg viewBox={`0 0 ${W} ${H}`} className="nh-pulse__svg" role="img" aria-label="Shot attempts over the game as a heartbeat">
        <defs>
          <filter id="nh-glow" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <pattern id="nh-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="rgba(94,234,212,.08)" /></pattern>
        </defs>
        <rect x="0" y="0" width={W} height={H} fill="url(#nh-grid)" />
        {[1, 2].map((k) => <line key={k} x1={X(k * 1200)} x2={X(k * 1200)} y1={6} y2={H - 6} className="nh-pulse__per" />)}
        {[0, 1, 2].map((k) => <text key={k} x={X(k * 1200 + 600)} y={H - 6} textAnchor="middle" className="nh-pulse__tick">{periodName('nhl', k + 1)}</text>)}
        <line x1={pad} x2={W - pad} y1={mid} y2={mid} className="nh-pulse__base" />
        <path d={trace(home.id, true)} className="nh-pulse__line" stroke={col[home.id]} filter="url(#nh-glow)" pathLength={1} />
        <path d={trace(away.id, false)} className="nh-pulse__line" stroke={col[away.id]} filter="url(#nh-glow)" pathLength={1} style={{ animationDelay: '.3s' }} />
        {goals.map((g, k) => {
          const up = g.shooter === home.id; const x = X(g.t); const y = mid + (up ? -52 : 52); const face = g.players[0]?.headshot; const id = `nh-pf-${g.id}`
          return (
            <g key={g.id} className="nh-pulse__goal" style={{ animationDelay: `${1.6 + k * 0.12}s` }}>
              <clipPath id={id}><circle cx={x} cy={y} r="12" /></clipPath>
              <circle cx={x} cy={y} r="14" fill="#0b1526" stroke={col[g.shooter ?? '']} strokeWidth="3" filter="url(#nh-glow)" />
              {face ? <image href={face} x={x - 12} y={y - 12} width="24" height="24" clipPath={`url(#${id})`} /> : null}
              <title>{g.text}</title>
            </g>
          )
        })}
        {feed.status.state === 'in' ? <g><circle cx={nowX} cy={mid} r="10" className="nh-pulse__now" /><circle cx={nowX} cy={mid} r="4.5" fill="#fff" /></g> : null}
      </svg>
    </section>
  )
}

export function HeatWall({ feed, col, hot, onOpen }: { feed: ArenaFeed; col: Record<string, string>; hot: Set<string>; onOpen: (id: string) => void }) {
  const n = (v: string | undefined) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }
  const rows = useMemo(() => feed.athletes.filter((a) => a.group !== 'goalies').map((a) => {
    const s = a.stats
    const score = n(s.goals) * 4 + n(s.assists) * 2.5 + n(s.shotsTotal) * 0.6 + n(s.hits) * 0.35 + n(s.blockedShots) * 0.4 + n(s.takeaways) * 0.3 + Math.max(0, n(s.plusMinus)) * 0.5
    return { a, score }
  }).sort((x, y) => y.score - x.score), [feed.athletes])
  const max = Math.max(1, rows[0]?.score ?? 1)
  const cols = 9; const cell = 92
  return (
    <section className="nh-card nh-wall" aria-label="Player heat wall">
      <header><h2>The Heat Wall <small>every skater, lit by tonight's impact · it reshuffles live · tap a face</small></h2></header>
      <div className="nh-wall__grid" style={{ height: Math.ceil(rows.length / cols) * cell }}>
        {rows.map(({ a, score }, k) => {
          const x = (k % cols) * (100 / cols); const y = Math.floor(k / cols) * cell
          const heat = score / max; const c = col[a.team ?? ''] ?? '#94a3b8'
          const pts = n(a.stats.goals) + n(a.stats.assists)
          return (
            <button key={a.id} type="button" className={`nh-wall__tile${hot.has(a.id) ? ' is-fire' : ''}`} onClick={() => onOpen(a.id)}
              style={{ left: `${x}%`, top: y, width: `${100 / cols}%`, ['--c' as string]: c, ['--h' as string]: heat.toFixed(2) } as CSSProperties}
              title={`${a.name}: ${a.stats.goals ?? 0} G, ${a.stats.assists ?? 0} A, ${a.stats.shotsTotal ?? 0} shots, ${a.stats.hits ?? 0} hits`}>
              <span className="nh-wall__face">{a.headshot ? <img src={a.headshot} alt="" loading="lazy" /> : <i>{a.jersey}</i>}{pts ? <em>{pts}</em> : null}</span>
              <small>{(a.short ?? a.name ?? '').replace(/^\w\. /, '')}</small>
            </button>
          )
        })}
      </div>
    </section>
  )
}
