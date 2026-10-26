/**
 * NBA LIVE VIEW (owner 10-25: "the court view is cartoonish ... no player profiles, hot zones, fire plays, 3-point
 * animation, free throw graphics, changing stats ... where is the HOLY SHIT moment?").
 *
 * THE ARENA: a full hardwood court, each team attacking its own basket, every shot at its real spot (made = filled,
 * missed = x). The ball flies to the rim in a D3 arc for every new shot and replays the last run of shots on a loop:
 * SPLASH on a three, SLAM on a dunk, the net ripples on a make, CLANK on a miss, and free throws leave the line.
 * HE'S ON FIRE: three straight makes and the player's face goes up in flames (NBA Jam), their shots burn on the court.
 * HEX HEAT: hexagons sized by attempts and coloured by how far the shooting runs above or below the league norm.
 * BIG MOMENTS: dunks, threes, and-ones, lead changes and clutch buckets, each replayable on the court.
 * WHO WINS?: ESPN's live win probability as a gauge and a river with the faces of the plays that swung it most.
 * FREE THROW LINE: every attempt as a ball, made or missed. Read-only: ESPN's public feed via /preview-api.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { elapsedAt, hex, periodName, teamPair, type ArenaFeed, type ArenaPlay, type ArenaTeam } from './arenaData'
import { NbaPlayers, NbaStory } from './NbaExtras'
import './nba.css'

const W = 940; const H = 500
const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const isFT = (p: ArenaPlay) => /free throw/i.test(p.type ?? '') || p.attempted === 1
const isDunk = (p: ArenaPlay) => /dunk|alley oop/i.test(p.type ?? '')
const pct = (v: number) => `${Math.round(v * 100)}%`

/** The basket a team attacks (home right, away left), in court pixels (10 px per foot). */
const basket = (home: boolean) => ({ x: home ? W - 52.5 : 52.5, y: H / 2 })
/** ESPN half-court feet (x 0-50 across, y = feet out from the rim) to full-court pixels. */
function spot(p: ArenaPlay, home: boolean): { x: number; y: number } {
  const b = basket(home)
  if (!p.coord) { const ft = 13.75 * 10; return { x: home ? b.x - ft : b.x + ft, y: b.y } }      // free throw line
  const along = (p.coord.y) * 10; const across = (p.coord.x - 25) * 10
  return home ? { x: b.x - along, y: b.y + across } : { x: b.x + along, y: b.y - across }
}
const expected = (p: ArenaPlay) => { if (p.attempted === 3) return 0.36; const d = p.coord ? Math.hypot(p.coord.x - 25, p.coord.y) : 0; return d < 4 ? 0.64 : d < 14 ? 0.43 : 0.41 }

interface Shot { p: ArenaPlay; home: boolean; x: number; y: number; ft: boolean; three: boolean; made: boolean; shooter: string | null }
interface Ctx { feed: ArenaFeed; col: Record<string, string>; shots: Shot[]; fire: Map<string, number>; teamOf: (id: string | null | undefined) => ArenaTeam }

export function NbaArena({ feed, bet, side, children }: { feed: ArenaFeed; bet: string | null; side?: ReactNode; children?: ReactNode }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = useMemo<Record<string, string>>(() => ({ [home.id]: pair.home, [away.id]: pair.away }), [home.id, away.id, pair.home, pair.away])
  const shots = useMemo<Shot[]>(() => feed.plays.filter((p) => p.shooting).map((p) => {
    const h = p.team === home.id
    const s = spot(p, h)
    return { p, home: h, x: s.x, y: s.y, ft: isFT(p), three: p.attempted === 3, made: !!p.scoring, shooter: p.players[0]?.id ?? null }
  }), [feed.plays, home.id])
  // current made-in-a-row streak per player (field goals only; a miss resets it)
  const fire = useMemo(() => {
    const m = new Map<string, number>()
    for (const s of shots) { if (s.ft || !s.shooter) continue; m.set(s.shooter, s.made ? (m.get(s.shooter) ?? 0) + 1 : 0) }
    return m
  }, [shots])
  const [replay, setReplay] = useState<{ shot: Shot; n: number } | null>(null)
  const ctx: Ctx = { feed, col, shots, fire, teamOf: (id) => (id === home.id ? home : away) }
  return (
    <div className="nb">
      <NbaStory feed={feed} bet={bet} />
      <OnFire ctx={ctx} />
      <div className="nb-top">
        <TheArena ctx={ctx} replay={replay} />
        <div className="nb-side">
          <WhoWins ctx={ctx} />
          <FreeThrowLine ctx={ctx} />
          {side}
        </div>
      </div>
      <BigMoments ctx={ctx} onReplay={(shot) => { setReplay((r) => ({ shot, n: (r?.n ?? 0) + 1 })); document.querySelector('.nb-arena')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }} />
      <NbaPlayers feed={feed} />
      {children}
    </div>
  )
}

// ------------------------------------------------------------ the court --

function CourtEnd() {
  // the right-hand end, drawn in court pixels; the left end is its mirror
  return (
    <g className="nb-lines">
      <rect x={W - 190} y={170} width={190} height={160} className="nb-key" />
      <circle cx={W - 190} cy={250} r={60} />
      <path d={`M${W - 190} 190 A60 60 0 0 0 ${W - 190} 310`} strokeDasharray="10 8" transform={`translate(0 0)`} />
      <path d={`M${W} 30 L${W - 140} 30 A237.5 237.5 0 0 0 ${W - 140} 470 L${W} 470`} />
      <path d={`M${W - 52.5} 210 A40 40 0 0 0 ${W - 52.5} 290`} />
      <line x1={W - 40} x2={W - 40} y1={220} y2={280} className="nb-board" />
      <circle cx={W - 52.5} cy={250} r={7.5} className="nb-rim" />
    </g>
  )
}

function Court({ home, away }: { home: ArenaTeam; away: ArenaTeam }) {
  return (
    <>
      <defs>
        <pattern id="nb-planks" width="940" height="28" patternUnits="userSpaceOnUse">
          {Array.from({ length: 4 }, (_, k) => <rect key={k} x={0} y={k * 7} width={940} height={7} fill={['#c98d4e', '#c2864a', '#cd9455', '#bf8346'][k]} />)}
          {Array.from({ length: 12 }, (_, k) => <line key={`j${k}`} x1={(k * 173) % 940} x2={(k * 173) % 940} y1={(k % 4) * 7} y2={(k % 4) * 7 + 7} stroke="#a86d36" strokeWidth="0.8" />)}
        </pattern>
        <radialGradient id="nb-spot" cx="50%" cy="45%" r="70%"><stop offset="0" stopColor="#fff" stopOpacity="0.16" /><stop offset="0.7" stopColor="#000" stopOpacity="0.08" /><stop offset="1" stopColor="#000" stopOpacity="0.45" /></radialGradient>
        <filter id="nb-fire" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect x="0" y="0" width={W} height={H} rx="10" fill="url(#nb-planks)" />
      <rect x="0" y="0" width={W} height={H} rx="10" fill="url(#nb-spot)" />
      <g className="nb-lines">
        <rect x="2" y="2" width={W - 4} height={H - 4} />
        <line x1={W / 2} x2={W / 2} y1="2" y2={H - 2} />
        <circle cx={W / 2} cy={H / 2} r="60" /><circle cx={W / 2} cy={H / 2} r="20" />
      </g>
      {home.logo ? <image href={home.logo} x={W / 2 - 52} y={H / 2 - 52} width="104" height="104" opacity="0.55" /> : null}
      <CourtEnd />
      <g transform={`translate(${W} 0) scale(-1 1)`}><CourtEnd /></g>
      <text x={W - 16} y={H / 2} className="nb-baseline" transform={`rotate(90 ${W - 16} ${H / 2})`} textAnchor="middle">{home.short.toUpperCase()}</text>
      <text x={16} y={H / 2} className="nb-baseline" transform={`rotate(-90 16 ${H / 2})`} textAnchor="middle">{away.short.toUpperCase()}</text>
    </>
  )
}

type Mode = 'recent' | 'new' | 'replay'

function TheArena({ ctx, replay }: { ctx: Ctx; replay: { shot: Shot; n: number } | null }) {
  const { feed, col, shots, fire, teamOf } = ctx
  const { home, away } = feed.teams
  const [lens, setLens] = useState<'shots' | 'heat'>('shots')
  const [team, setTeam] = useState<'all' | string>('all')
  const [period, setPeriod] = useState(0)
  const [caption, setCaption] = useState<Shot | null>(null)
  const [call, setCall] = useState<{ s: Shot; word: string } | null>(null)
  const [shake, setShake] = useState(false)
  const [run, setRun] = useState<{ mode: Mode; n: number }>({ mode: 'recent', n: 0 })
  const ballRef = useRef<SVGGElement | null>(null)
  const seen = useRef<string | null | undefined>(undefined)
  const shotsRef = useRef(shots); shotsRef.current = shots
  const colRef = useRef(col); colRef.current = col
  const replayRef = useRef(replay); replayRef.current = replay
  const latest = shots[shots.length - 1] ?? null
  useEffect(() => { if (replay) setRun((r) => ({ mode: 'replay', n: r.n + 1 })) }, [replay])
  useEffect(() => {
    if (!latest) return
    if (seen.current === undefined) { seen.current = latest.p.id; return }
    if (latest.p.id !== seen.current) { seen.current = latest.p.id; setRun((r) => (r.mode === 'replay' ? r : { mode: 'new', n: r.n + 1 })) }
  }, [latest])
  useEffect(() => { const t = window.setInterval(() => setRun((r) => (r.mode === 'recent' ? { mode: 'recent', n: r.n + 1 } : r)), 16_000); return () => window.clearInterval(t) }, [])
  useEffect(() => {
    const el = ballRef.current; const all = shotsRef.current
    const seq = run.mode === 'replay' && replayRef.current ? [replayRef.current.shot] : run.mode === 'new' ? all.slice(-1) : all.slice(-6)
    if (!el || !seq.length) return undefined
    const g = d3.select(el); g.selectAll('*').interrupt(); g.selectAll('*').remove()
    if (reduced()) { setCaption(seq[seq.length - 1]); return undefined }
    const timers: number[] = []
    const big = run.mode !== 'recent'
    const fly = (k: number) => {
      if (k >= seq.length) { if (run.mode !== 'recent') timers.push(window.setTimeout(() => setRun((r) => ({ mode: 'recent', n: r.n })), 1800)); return }
      const s = seq[k]; const b = basket(s.home); const c = colRef.current[s.p.team ?? ''] ?? '#f97316'
      setCaption(s)
      const dist = Math.hypot(b.x - s.x, b.y - s.y)
      const apex = Math.min(140, 40 + dist * 0.35)
      const shadow = g.append('ellipse').attr('rx', 7).attr('ry', 4).attr('class', 'nb-shadow').attr('cx', s.x).attr('cy', s.y)
      const ball = g.append('g').attr('class', 'nb-ball')
      ball.append('circle').attr('r', 8).attr('class', 'nb-ball__body')
      ball.append('path').attr('d', 'M-8 0 H8 M0 -8 V8').attr('class', 'nb-ball__seam')
      const trail = g.append('path').attr('class', 'nb-arc').attr('stroke', c)
      const pts: Array<[number, number]> = []
      g.append('circle').attr('cx', s.x).attr('cy', s.y).attr('r', 4).attr('fill', 'none').attr('stroke', c).attr('stroke-width', 3)
        .transition().duration(700).attr('r', 22).style('opacity', 0).remove()
      const dur = s.ft ? 900 : 1000 + dist * 0.8
      ball.transition().duration(dur).ease(d3.easeLinear).tween('fly', () => (t: number) => {
        const x = s.x + (b.x - s.x) * t; const y = s.y + (b.y - s.y) * t; const h = 4 * apex * t * (1 - t)
        ball.attr('transform', `translate(${x},${y - h * 0.55}) scale(${1 + h / 160}) rotate(${t * 540})`)
        shadow.attr('cx', x).attr('cy', y).attr('rx', 7 - h / 40).style('opacity', 0.35 - h / 600)
        pts.push([x, y - h * 0.55]); trail.attr('d', d3.line()(pts) ?? '')
      }).on('end', () => {
        shadow.remove(); trail.transition().duration(1400).style('opacity', 0).remove()
        if (s.made) {
          ball.transition().duration(260).attr('transform', `translate(${b.x},${b.y + 10}) scale(0.7)`).style('opacity', 0).remove()
          for (let r = 0; r < 3; r++) g.append('circle').attr('cx', b.x).attr('cy', b.y).attr('r', 6).attr('fill', 'none').attr('stroke', r === 1 ? '#fff' : c).attr('stroke-width', 3 - r * 0.6)
            .transition().delay(r * 120).duration(900).ease(d3.easeCubicOut).attr('r', 46 + r * 18).style('opacity', 0).remove()
          const word = s.ft ? '+1' : s.three ? '+3' : '+2'
          g.append('text').attr('x', b.x + (s.home ? -40 : 40)).attr('y', b.y - 20).attr('text-anchor', 'middle').attr('class', 'nb-pts').style('fill', c).text(word)
            .transition().duration(1300).attr('y', b.y - 70).style('opacity', 0).remove()
          const streak = s.shooter ? fire.get(s.shooter) ?? 0 : 0
          if (big && !s.ft && (s.three || isDunk(s.p) || streak >= 3)) {
            setCall({ s, word: streak >= 3 && s.p.id === latest?.p.id ? "HE'S ON FIRE!" : isDunk(s.p) ? 'SLAM!' : 'SPLASH!' })
            if (isDunk(s.p)) { setShake(true); timers.push(window.setTimeout(() => setShake(false), 500)) }
            timers.push(window.setTimeout(() => setCall(null), 3600))
          }
        } else {
          const off = (s.home ? -1 : 1) * (18 + Math.random() * 20)
          ball.transition().duration(500).ease(d3.easeQuadOut).attr('transform', `translate(${b.x + off},${b.y + (Math.random() - 0.5) * 70}) scale(0.9)`).transition().duration(400).style('opacity', 0).remove()
          g.append('text').attr('x', b.x + (s.home ? -36 : 36)).attr('y', b.y - 16).attr('text-anchor', 'middle').attr('class', 'nb-clank').text(s.ft ? 'MISS' : 'CLANK')
            .transition().duration(1100).attr('y', b.y - 50).style('opacity', 0).remove()
        }
        timers.push(window.setTimeout(() => fly(k + 1), s.made && big ? 1400 : 650))
      })
    }
    timers.push(window.setTimeout(() => fly(0), 250))
    return () => { timers.forEach((t) => window.clearTimeout(t)); g.selectAll('*').interrupt(); g.selectAll('*').remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])
  const periods = Array.from(new Set(shots.map((s) => s.p.period ?? 0))).filter(Boolean).sort((a, b) => a - b)
  const shown = shots.filter((s) => !s.ft && (team === 'all' || s.p.team === team) && (!period || s.p.period === period))
  const hexes = useMemo(() => {
    if (lens !== 'heat') return []
    const R = 22; const dx = R * Math.sqrt(3); const dy = R * 1.5
    const bins = new Map<string, { x: number; y: number; n: number; m: number; e: number; home: boolean }>()
    for (const s of shown) {
      const row = Math.round(s.y / dy); const colx = Math.round((s.x - (row % 2 ? dx / 2 : 0)) / dx)
      const k = `${row}:${colx}`; const cx = colx * dx + (row % 2 ? dx / 2 : 0); const cy = row * dy
      const b = bins.get(k) ?? { x: cx, y: cy, n: 0, m: 0, e: 0, home: s.home }
      b.n += 1; b.m += s.made ? 1 : 0; b.e += expected(s.p); bins.set(k, b)
    }
    const max = Math.max(1, ...Array.from(bins.values()).map((b) => b.n))
    return Array.from(bins.values()).map((b) => ({ ...b, r: R * (0.35 + 0.65 * Math.sqrt(b.n / max)), d: (b.m - b.e) / b.n }))
  }, [lens, shown])
  const hexPath = (r: number) => d3.range(6).map((i) => { const a = (Math.PI / 3) * i + Math.PI / 6; return `${i ? 'L' : 'M'}${(r * Math.cos(a)).toFixed(1)} ${(r * Math.sin(a)).toFixed(1)}` }).join('') + 'Z'
  const heatColor = d3.scaleDiverging<string>().domain([-0.3, 0, 0.3]).interpolator((t) => d3.interpolateRdYlBu(1 - t)).clamp(true)
  return (
    <section className="nb-card nb-arena" aria-label="The arena">
      <header>
        <h2>The Arena <small>{run.mode === 'replay' ? 'replaying the moment' : run.mode === 'new' ? 'the newest shot' : 'the last six shots, on a loop'}</small></h2>
        <span className="ar-seg">{([['shots', 'Every shot'], ['heat', '🔥 Hex heat']] as const).map(([k, l]) => <button key={k} type="button" className={lens === k ? 'is-on' : ''} onClick={() => setLens(k)}>{l}</button>)}</span>
        <span className="ar-seg">{[['all', 'Both'], [away.id, away.abbr], [home.id, home.abbr]].map(([k, l]) => <button key={k} type="button" className={team === k ? 'is-on' : ''} onClick={() => setTeam(k)}>{l}</button>)}</span>
        <span className="ar-seg">{[0, ...periods].map((k) => <button key={k} type="button" className={period === k ? 'is-on' : ''} onClick={() => setPeriod(k)}>{k ? periodName('nba', k) : 'All'}</button>)}</span>
      </header>
      <div className={`nb-arena__wrap${shake ? ' is-shake' : ''}`} style={{ ['--nb-paint' as string]: hex(home.color, '#1d428a') } as CSSProperties}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Full court with every shot">
          <Court home={home} away={away} />
          <text x={W * 0.25} y={22} className="nb-dir" textAnchor="middle">← {away.abbr} SHOOTS THIS WAY</text>
          <text x={W * 0.75} y={22} className="nb-dir" textAnchor="middle">{home.abbr} SHOOTS THIS WAY →</text>
          {lens === 'heat' ? hexes.map((h, k) => <path key={k} d={hexPath(h.r)} transform={`translate(${h.x} ${h.y})`} fill={heatColor(h.d)} stroke="rgba(0,0,0,.35)" className="nb-hex" style={{ animationDelay: `${k * 8}ms` }}><title>{`${h.m}/${h.n} made (${pct(h.m / h.n)}), league norm ${pct(h.e / h.n)}`}</title></path>) : null}
          {lens === 'shots' ? shown.map((s) => {
            const c = col[s.p.team ?? ''] ?? '#e2e8f0'
            const hot = s.made && s.shooter && (fire.get(s.shooter) ?? 0) >= 3
            return s.made
              ? <circle key={s.p.id} cx={s.x} cy={s.y} r={s.three ? 7 : 5.5} fill={hot ? '#f97316' : c} stroke={hot ? '#fde047' : '#0b1526'} strokeWidth={s.three ? 2.2 : 1.4} className={`ar-shot${hot ? ' nb-hot' : ''}`} filter={hot ? 'url(#nb-fire)' : undefined}><title>{s.p.text}</title></circle>
              : <g key={s.p.id} transform={`translate(${s.x} ${s.y})`} className="ar-shot" opacity="0.8"><path d="M-4 -4 L4 4 M4 -4 L-4 4" stroke={c} strokeWidth="2.4" strokeLinecap="round" /><title>{s.p.text}</title></g>
          }) : null}
          <g ref={ballRef} />
        </svg>
        {call ? <ShotCall s={call.s} word={call.word} color={col[call.s.p.team ?? ''] ?? '#f97316'} team={teamOf(call.s.p.team)} /> : null}
      </div>
      <div className="nb-caption" style={{ ['--c' as string]: caption ? col[caption.p.team ?? ''] ?? '#94a3b8' : '#94a3b8' } as CSSProperties} aria-live="polite">
        {caption ? (<>
          {caption.p.players[0]?.headshot ? <img src={caption.p.players[0].headshot} alt="" /> : <i />}
          <span><b>{caption.made ? (caption.ft ? 'FREE THROW' : caption.three ? 'THREE' : isDunk(caption.p) ? 'DUNK' : 'BUCKET') : 'MISS'}</b> <small>{periodName('nba', caption.p.period)} {caption.p.clock} · {away.abbr} {caption.p.away ?? ''}–{caption.p.home ?? ''} {home.abbr}</small><br />{caption.p.text}</span>
        </>) : <span>Waiting for the first shot…</span>}
      </div>
      <div className="ar-rink__key"><span>● made</span><span>× missed</span><span>bigger dot = three</span><span className="is-hot">🔥 orange = a player on a 3+ make streak</span>{lens === 'heat' ? <span>hex size = attempts · red = shooting above the league norm, blue = below</span> : null}</div>
    </section>
  )
}

function ShotCall({ s, word, color, team }: { s: Shot; word: string; color: string; team: ArenaTeam }) {
  const who = s.p.players[0]
  const fire = word.includes('FIRE')
  return (
    <div className={`nb-call${fire ? ' is-fire' : ''}`} style={{ ['--c' as string]: color } as CSSProperties}>
      <b>{word}</b>
      <span>{who?.headshot ? <img src={who.headshot} alt="" /> : null}<em>{who?.name ?? team.short}</em><small>{team.logo ? <img src={team.logo} alt="" className="nb-call__logo" /> : null}{periodName('nba', s.p.period)} {s.p.clock}</small></span>
    </div>
  )
}

// ------------------------------------------------------------ on fire --

function OnFire({ ctx }: { ctx: Ctx }) {
  const { feed, fire, col } = ctx
  const hot = Array.from(fire.entries()).filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1])
  const seen = useRef<Set<string> | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  useEffect(() => {
    const now = new Set(hot.filter(([, n]) => n >= 3).map(([id]) => id))
    if (seen.current) for (const id of now) if (!seen.current.has(id)) { setFlash(id); window.setTimeout(() => setFlash(null), 5000) }
    seen.current = now
  }, [hot])
  if (!hot.length) return null
  const person = (id: string) => feed.athletes.find((a) => a.id === id) ?? null
  return (
    <section className="nb-fire" aria-label="Who is hot">
      {hot.slice(0, 3).map(([id, n]) => {
        const a = person(id); const c = col[a?.team ?? ''] ?? '#f97316'
        return (
          <div key={id} className={`nb-fire__card${n >= 3 ? ' is-fire' : ''}${flash === id ? ' is-flash' : ''}`} style={{ ['--c' as string]: c } as CSSProperties}>
            <span className="nb-fire__face">{a?.headshot ? <img src={a.headshot} alt="" /> : null}{n >= 3 ? <i className="nb-flames" aria-hidden="true"><i /><i /><i /></i> : null}</span>
            <span><b>{n >= 3 ? "HE'S ON FIRE!" : 'HEATING UP'}</b><em>{a?.name ?? 'Player'}</em><small>{n} straight makes · {a?.stats.points ?? '—'} pts ({a?.stats['fieldGoalsMade-fieldGoalsAttempted'] ?? '—'} FG)</small></span>
          </div>
        )
      })}
    </section>
  )
}

// ------------------------------------------------------------ big moments --

function BigMoments({ ctx, onReplay }: { ctx: Ctx; onReplay: (s: Shot) => void }) {
  const { feed, shots, col } = ctx
  const list = useMemo(() => {
    const out: Array<{ s: Shot; tag: string; score: number }> = []
    let lastSign = 0
    shots.forEach((s, i) => {
      if (!s.made) return
      const margin = (s.p.home ?? 0) - (s.p.away ?? 0); const sign = Math.sign(margin)
      const late = (s.p.period ?? 1) >= 4 && elapsedAt('nba', s.p.period, s.p.clock) >= 2580 && Math.abs(margin) <= 6
      const andOne = !s.ft && shots[i + 1]?.ft && shots[i + 1].shooter === s.shooter && shots[i + 1].p.clock === s.p.clock && /1 of 1/i.test(shots[i + 1].p.type ?? '')
      const change = sign !== 0 && lastSign !== 0 && sign !== lastSign
      if (sign !== 0) lastSign = sign
      if (s.ft) return
      const tag = andOne ? 'AND-ONE' : isDunk(s.p) ? 'SLAM' : change ? 'LEAD CHANGE' : late ? 'CLUTCH' : s.three ? 'SPLASH' : ''
      if (tag) out.push({ s, tag, score: (late ? 3 : 0) + (change ? 2 : 0) + (andOne ? 2 : 0) + (isDunk(s.p) ? 1.5 : 0) + (s.three ? 1 : 0) })
    })
    return out.slice(-14).reverse()
  }, [shots])
  if (!list.length) return null
  return (
    <section className="nb-card nb-moments" aria-label="Big moments">
      <header><h2>Big Moments <small>tap ▶ to watch it on the court</small></h2></header>
      <ol>
        {list.map(({ s, tag }, k) => {
          const who = s.p.players[0]
          return (
            <li key={s.p.id} style={{ ['--c' as string]: col[s.p.team ?? ''] ?? '#94a3b8', animationDelay: `${k * 70}ms` } as CSSProperties}>
              <span className="nb-moments__face">{who?.headshot ? <img src={who.headshot} alt="" /> : <i />}</span>
              <span className="nb-moments__txt"><b className={`t-${tag.replace(/\W/g, '').toLowerCase()}`}>{tag}</b><em>{who?.name ?? ''}</em><small>{periodName('nba', s.p.period)} {s.p.clock} · {feed.teams.away.abbr} {s.p.away}–{s.p.home} {feed.teams.home.abbr}</small></span>
              <button type="button" className="nb-moments__play" onClick={() => onReplay(s)} aria-label={`Replay ${tag} by ${who?.name ?? 'player'}`}>▶</button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

// ------------------------------------------------------------ who wins --

function WhoWins({ ctx }: { ctx: Ctx }) {
  const { feed, col } = ctx
  const { home, away } = feed.teams
  const byId = useMemo(() => new Map(feed.plays.map((p) => [p.id, p])), [feed.plays])
  const pts = useMemo(() => {
    const raw = feed.win_probability.map((w, i) => { const p = byId.get(w.play); return p ? { t: elapsedAt('nba', p.period, p.clock), p: w.home, play: p, i } : null })
      .filter((x): x is { t: number; p: number; play: ArenaPlay; i: number } => !!x).sort((x, y) => x.t - y.t || x.i - y.i)
    if (feed.status.state !== 'post' || !raw.length) return raw
    // ESPN appends readings after the buzzer; a finished game ends at its result
    const end = Math.max(...raw.map((r) => r.t))
    const last = raw[raw.length - 1].play
    return [...raw.filter((r) => r.t < end), { t: end, p: home.score > away.score ? 1 : 0, play: last, i: raw.length }]
  }, [feed.win_probability, feed.status.state, byId, home.score, away.score])
  const now = pts.length ? pts[pts.length - 1].p : null
  const needle = useRef<SVGGElement | null>(null); const label = useRef<SVGTextElement | null>(null); const prev = useRef(0.5)
  useEffect(() => {
    if (now == null) return
    const from = prev.current; prev.current = now
    const ang = (p: number) => -90 + p * 180; const dur = reduced() ? 0 : 1600
    if (needle.current) d3.select(needle.current).transition().duration(dur).ease(d3.easeElasticOut.amplitude(1).period(0.5)).attrTween('transform', () => (t: number) => `rotate(${ang(from + (now - from) * t)} 150 132)`)
    const el = label.current
    if (el) d3.select(el).transition().duration(dur).tween('text', () => { const i = d3.interpolate(from, now); return (t: number) => { const v = i(t); el.textContent = pct(Math.max(v, 1 - v)) } })
  }, [now])
  if (now == null) return null
  const RW = 520; const RH = 150; const pad = 16
  const end = Math.max(2880, pts[pts.length - 1].t)
  const X = (t: number) => pad + (t / end) * (RW - pad * 2); const Y = (p: number) => 8 + (1 - p) * (RH - 28)
  const smooth = pts.filter((_, i) => i % Math.max(1, Math.floor(pts.length / 160)) === 0 || i === pts.length - 1)
  const line = d3.line<{ t: number; p: number }>().x((d) => X(d.t)).y((d) => Y(d.p)).curve(d3.curveMonotoneX)(smooth) ?? ''
  const area = d3.area<{ t: number; p: number }>().x((d) => X(d.t)).y0(Y(0.5)).y1((d) => Y(d.p)).curve(d3.curveMonotoneX)(smooth) ?? ''
  const swings = pts.map((d, i) => ({ ...d, dp: i ? d.p - pts[i - 1].p : 0 })).filter((d) => d.play.players[0]?.headshot && Math.abs(d.dp) <= 0.3 && d.t > 60 && d.i < pts.length - 1).sort((a, b) => Math.abs(b.dp) - Math.abs(a.dp)).slice(0, 5)
  const fav = now >= 0.5 ? home : away
  const arc = (a0: number, a1: number) => d3.arc()({ innerRadius: 92, outerRadius: 118, startAngle: a0, endAngle: a1 }) ?? ''
  return (
    <section className="nb-card nb-wp" aria-label="Win probability">
      <header><h2>Who Wins? <small>ESPN live win probability</small></h2></header>
      <svg viewBox="0 0 300 196" className="nh-wp__gauge" role="img" aria-label={`${fav.short} ${pct(Math.max(now, 1 - now))}`}>
        <g transform="translate(150 132)">
          <path d={arc(-Math.PI / 2, -0.012)} fill={col[away.id]} opacity={now < 0.5 ? 0.95 : 0.4} />
          <path d={arc(0.012, Math.PI / 2)} fill={col[home.id]} opacity={now >= 0.5 ? 0.95 : 0.4} />
        </g>
        <g ref={needle} transform="rotate(0 150 132)"><path d="M146 132 L150 30 L154 132 Z" fill="#fff" /><circle cx="150" cy="132" r="10" fill="#fff" /><circle cx="150" cy="132" r="4" fill="#0b1526" /></g>
        {away.logo ? <image href={away.logo} x="6" y="136" width="36" height="36" /> : null}
        {home.logo ? <image href={home.logo} x="258" y="136" width="36" height="36" /> : null}
        <text x="150" y="170" textAnchor="middle" className="nh-wp__big" ref={label}>{pct(Math.max(now, 1 - now))}</text>
        <text x="150" y="190" textAnchor="middle" className="nh-wp__who" fill={col[fav.id]}>{feed.status.state === 'post' ? `${fav.short} won` : `${fav.short} to win`}</text>
      </svg>
      <svg viewBox={`0 0 ${RW} ${RH}`} className="nh-wp__river" role="img" aria-label="Win probability over the game">
        <defs><clipPath id="nb-up"><rect x="0" y="0" width={RW} height={Y(0.5)} /></clipPath><clipPath id="nb-dn"><rect x="0" y={Y(0.5)} width={RW} height={RH} /></clipPath></defs>
        {[1, 2, 3].map((k) => <line key={k} x1={X(k * 720)} x2={X(k * 720)} y1={6} y2={RH - 18} className="ar-flow__grid" />)}
        {[0, 1, 2, 3].map((k) => <text key={k} x={X(k * 720 + 360)} y={RH - 4} textAnchor="middle" className="ar-flow__tick">{periodName('nba', k + 1)}</text>)}
        <line x1={pad} x2={RW - pad} y1={Y(0.5)} y2={Y(0.5)} className="ar-flow__zero" />
        <path d={area} fill={col[home.id]} opacity="0.35" clipPath="url(#nb-up)" className="nh-fadein" />
        <path d={area} fill={col[away.id]} opacity="0.35" clipPath="url(#nb-dn)" className="nh-fadein" />
        <path d={line} className="nh-wp__line" pathLength={1} />
        <text x={pad + 2} y={16} className="ar-flow__lab" fill={col[home.id]}>{home.abbr} ↑</text><text x={pad + 2} y={RH - 22} className="ar-flow__lab" fill={col[away.id]}>{away.abbr} ↓</text>
        {swings.map((s) => { const x = X(s.t); const y = Y(s.p); const id = `nb-wf-${s.play.id}`; return (
          <g key={s.play.id} className="nh-wp__goal"><clipPath id={id}><circle cx={x} cy={y} r="11" /></clipPath>
            <circle cx={x} cy={y} r="13" fill="#0b1526" stroke={col[s.play.team ?? ''] ?? '#fff'} strokeWidth="2.5" />
            <image href={s.play.players[0]!.headshot!} x={x - 11} y={y - 11} width="22" height="22" clipPath={`url(#${id})`} />
            <title>{`${s.play.text} (${home.abbr} ${s.dp >= 0 ? '+' : '−'}${Math.abs(Math.round(s.dp * 100))} pts)`}</title></g>) })}
      </svg>
      {swings[0] ? <p className="nh-wp__swing">Biggest swing: <b>{swings[0].play.players[0]?.name}</b>, {Math.abs(Math.round(swings[0].dp * 100))} points ({periodName('nba', swings[0].play.period)} {swings[0].play.clock}).</p> : null}
    </section>
  )
}

// ------------------------------------------------------------ free throws --

function FreeThrowLine({ ctx }: { ctx: Ctx }) {
  const { feed, shots, col } = ctx
  const fts = shots.filter((s) => s.ft)
  if (!fts.length) return null
  return (
    <section className="nb-card nb-ft" aria-label="Free throw line">
      <header><h2>The Free Throw Line <small>every attempt, in order</small></h2></header>
      {[feed.teams.away, feed.teams.home].map((t) => {
        const mine = fts.filter((s) => s.p.team === t.id); const made = mine.filter((s) => s.made).length
        return (
          <div key={t.id} className="nb-ft__row" style={{ ['--c' as string]: col[t.id] } as CSSProperties}>
            <b>{t.logo ? <img src={t.logo} alt="" /> : null}{t.abbr}</b>
            <span className="nb-ft__balls">{mine.map((s, k) => <i key={s.p.id} className={s.made ? 'is-made' : 'is-miss'} style={{ animationDelay: `${k * 35}ms` }} title={s.p.text ?? ''} />)}</span>
            <strong>{made}/{mine.length}<small>{mine.length ? pct(made / mine.length) : '—'}</small></strong>
          </div>
        )
      })}
      <p className="ar-small">A typical NBA team makes about 78% of its free throws.</p>
    </section>
  )
}
