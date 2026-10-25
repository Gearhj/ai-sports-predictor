/**
 * LIVE VIEW for the NBA and the NHL (owner 10-24: "the Live Views for all of the sports"). Test mode only.
 *
 * NBA: the half court with every shot both teams took (made filled, missed hollow), the newest shot flying to the rim
 * once with a SPLASH / SLAM call, HOT ZONES (each zone's field-goal rate against a typical NBA rate), GAME FLOW (the
 * score margin over the game with ESPN's live win probability), and the RUN DETECTOR that calls a scoring run while
 * it is happening.
 * NHL: the full rink with every shot, miss, block and goal (each team shown attacking its own end), the DANGER MAP
 * (how close and central each chance was), the SHOT-ATTEMPT MOMENTUM line, who is ON THE ICE right now, and a GOAL
 * callout with the goal light the moment a new goal lands.
 * Both: score bug with logos, AISP's pregame model, market and paper bet for this game, leaders, box score, play feed.
 *
 * Feed: ESPN public play-by-play via the preview server, never stored, never used for settlement. Animations run once
 * per new play and then hold still.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { MissionGame } from '../../mission/missionData'
import { stageOf } from '../../mission/missionData'
import { LiveShell, useFavorites, type StripInfo } from '../football/LiveShell'
import { elapsedAt, onNavy, periodName, teamPair, useArenaLive, type ArenaFeed, type ArenaPlay, type ArenaTeam } from './arenaData'
import './arena.css'

const etDate = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso)).replace(/-/g, '')
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)

export function ArenaLive({ game, games, onPickGame, sportFilter, onSport, booth }: { game: MissionGame; games: MissionGame[]; onPickGame: (id: string) => void; sportFilter: string | null; onSport: (s: string | null) => void; booth: React.ReactNode }) {
  const league = game.sport === 'NBA' ? 'nba' : 'nhl'
  const live = game.status === 'live' || game.phase === 'STARTED'
  const { data, error } = useArenaLive(league, etDate(game.starts_at), game.home.abbr, game.away.abbr, live)
  const [favs, toggleFav] = useFavorites()
  const [boothOpen, setBoothOpen] = useState(false)
  const feed = data?.found ? data : null
  const strip: StripInfo = { league: league.toUpperCase(), week: null, season: null, venue: feed?.venue ?? null, city: null, weather: null, model: league === 'nba' ? 'NBA Gen1' : 'NHL Gen1', broadcast: null,
    live: feed?.status.state === 'in', clock: feed ? (feed.status.state === 'in' ? `${periodName(league, feed.status.period)} ${feed.status.clock ?? ''}` : feed.status.short ?? '') : '', status: feed?.status.state === 'post' ? 'FINAL' : 'PREGAME', date: game.starts_at }
  return (
    <LiveShell games={games} gameId={game.game_id} onPick={onPickGame} sportFilter={sportFilter} onSport={onSport} favs={favs} onFav={toggleFav} strip={strip} onBooth={() => setBoothOpen((v) => !v)}>
      <div className="ar" data-league={league}>
        {!feed ? <p className="ar-note">{error ? (error.includes('preview') ? 'The live NBA/NHL feed is only wired on the test-mode preview server (127.0.0.1:5199).' : `Live feed error: ${error}`) : data && !data.found ? data.reason : 'Loading the play-by-play…'}</p> : (
          <>
            <ScoreBug feed={feed} league={league} />
            <Pregame game={game} feed={feed} />
            {league === 'nba' ? <NbaView feed={feed} /> : <NhlView feed={feed} />}
          </>
        )}
        {boothOpen ? <div className="ar-booth">{booth}</div> : null}
        <footer className="ar-foot">Research and paper only. Play-by-play, coordinates, players and {league === 'nba' ? 'win probability' : 'on-ice lineups'}: ESPN's public feed via the test-mode preview server, never stored and never used for settlement. AISP's pregame numbers come from Mission Control.</footer>
      </div>
    </LiveShell>
  )
}

function ScoreBug({ feed, league }: { feed: ArenaFeed; league: 'nba' | 'nhl' }) {
  const { home, away } = feed.teams
  const side = (t: ArenaTeam, right = false) => (
    <div className={`ar-sb__team${right ? ' is-home' : ''}`} style={{ ['--tc' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
      {t.logo ? <img src={t.logo} alt="" /> : null}
      <span><b>{t.short || t.abbr}</b><small>{t.record ?? ''}</small></span>
      <strong className={t.score > (t === home ? away.score : home.score) ? 'is-lead' : ''}>{t.score}</strong>
    </div>
  )
  const periods = Math.max(home.linescores.length, away.linescores.length, league === 'nba' ? 4 : 3)
  return (
    <section className="ar-sb" aria-label="Score">
      {side(away)}
      <div className="ar-sb__mid">
        <b className={feed.status.state === 'in' ? 'is-live' : ''}>{feed.status.state === 'in' ? `● ${periodName(league, feed.status.period)} ${feed.status.clock ?? ''}` : feed.status.short}</b>
        <table><thead><tr><th />{Array.from({ length: periods }, (_, i) => <th key={i}>{periodName(league, i + 1)}</th>)}</tr></thead>
          <tbody>{[away, home].map((t) => <tr key={t.id}><th>{t.abbr}</th>{Array.from({ length: periods }, (_, i) => <td key={i}>{t.linescores[i] ?? ''}</td>)}</tr>)}</tbody></table>
      </div>
      {side(home, true)}
    </section>
  )
}

function Pregame({ game, feed }: { game: MissionGame; feed: ArenaFeed }) {
  const f = stageOf(game, 'forecast'); const p = stageOf(game, 'prices'); const b = stageOf(game, 'bet'); const d = stageOf(game, 'decision')
  const wp = feed.win_probability.length ? feed.win_probability[feed.win_probability.length - 1].home : null
  return (
    <section className="ar-pre" aria-label="AISP before the game">
      <span><small>AISP model, pregame</small><b>{feed.teams.home.abbr} {pct(f.home_win_prob ?? null)}</b></span>
      <span><small>Market, pregame</small><b>{feed.teams.home.abbr} {pct(p.consensus_home ?? null)}</b></span>
      {wp != null ? <span className="is-live"><small>Live win probability (ESPN)</small><b>{feed.teams.home.abbr} {pct(wp)}</b></span> : null}
      <span className={b.state === 'DONE' ? 'is-bet' : ''}><small>AISP paper bet</small><b>{b.state === 'DONE' ? (b.label ?? '').replace(/Â·/g, '·') : d.label ?? 'No bet'}</b></span>
    </section>
  )
}

// ---------------------------------------------------------------- NBA --

type Zone = 'ra' | 'paint' | 'mid' | 'c3' | 'ab3'
const ZONE_NAME: Record<Zone, string> = { ra: 'At the rim', paint: 'Paint', mid: 'Mid-range', c3: 'Corner 3', ab3: 'Above-break 3' }
const LEAGUE_FG: Record<Zone, number> = { ra: 0.64, paint: 0.43, mid: 0.41, c3: 0.385, ab3: 0.355 }
const isFT = (p: ArenaPlay) => /free throw/i.test(p.type ?? '') || p.attempted === 1
function zoneOf(p: ArenaPlay): Zone {
  const { x, y } = p.coord!
  const d = Math.hypot(x - 25, y)
  if (p.attempted === 3) return y + 5.25 < 14 ? 'c3' : 'ab3'
  if (d < 4) return 'ra'
  if (Math.abs(x - 25) < 8 && y + 5.25 < 19) return 'paint'
  return 'mid'
}
const cx = (x: number) => x * 10
const cy = (y: number) => Math.min(46.5, y + 5.25) * 10

function NbaView({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = { [home.id]: pair.home, [away.id]: pair.away }
  const [team, setTeam] = useState<'all' | string>('all')
  const [period, setPeriod] = useState<number | 0>(0)
  const [lens, setLens] = useState<'shots' | 'zones'>('shots')
  const shots = useMemo(() => feed.plays.filter((p) => p.shooting && p.coord && !isFT(p)), [feed.plays])
  const shown = shots.filter((p) => (team === 'all' || p.team === team) && (!period || p.period === period))
  const latest = shots[shots.length - 1] ?? null
  const periods = Array.from(new Set(shots.map((p) => p.period ?? 0))).filter(Boolean).sort()
  const zones = useMemo(() => {
    const out: Record<string, Record<Zone, { m: number; a: number }>> = {}
    for (const t of [home.id, away.id]) out[t] = { ra: { m: 0, a: 0 }, paint: { m: 0, a: 0 }, mid: { m: 0, a: 0 }, c3: { m: 0, a: 0 }, ab3: { m: 0, a: 0 } }
    shown.forEach((p) => { if (!p.team || !out[p.team]) return; const z = zoneOf(p); out[p.team][z].a += 1; if (p.scoring) out[p.team][z].m += 1 })
    return out
  }, [shown, home.id, away.id])
  const zoneTeam = team === 'all' ? null : team
  const zoneAgg = (z: Zone) => { const ids = zoneTeam ? [zoneTeam] : [home.id, away.id]; return ids.reduce((s, id) => ({ m: s.m + zones[id][z].m, a: s.a + zones[id][z].a }), { m: 0, a: 0 }) }
  const zoneFill = (z: Zone) => { const { m, a } = zoneAgg(z); if (!a) return 'rgba(148,163,184,.08)'; const d = m / a - LEAGUE_FG[z]; const k = Math.min(1, Math.abs(d) / 0.2); return d >= 0 ? `rgba(248,113,113,${0.18 + 0.55 * k})` : `rgba(96,165,250,${0.18 + 0.55 * k})` }
  return (
    <div className="ar-grid">
      <section className="ar-card ar-court" aria-label="Shot chart">
        <header>
          <h2>The Court <small>{shown.length} shots · made ● missed ○</small></h2>
          <span className="ar-seg">{(['shots', 'zones'] as const).map((k) => <button key={k} type="button" className={lens === k ? 'is-on' : ''} onClick={() => setLens(k)}>{k === 'shots' ? 'Every shot' : '🔥 Hot zones'}</button>)}</span>
          <span className="ar-seg">{[['all', 'Both'], [away.id, away.abbr], [home.id, home.abbr]].map(([k, l]) => <button key={k} type="button" className={team === k ? 'is-on' : ''} onClick={() => setTeam(k)}>{l}</button>)}</span>
          <span className="ar-seg">{[0, ...periods].map((k) => <button key={k} type="button" className={period === k ? 'is-on' : ''} onClick={() => setPeriod(k)}>{k ? periodName('nba', k) : 'All'}</button>)}</span>
        </header>
        <div className="ar-court__wrap">
          <svg viewBox="0 0 500 470" role="img" aria-label="Half court shot chart">
            <defs><radialGradient id="ar-floor" cx="50%" cy="10%" r="90%"><stop offset="0" stopColor="#2a2016" /><stop offset="1" stopColor="#140f0a" /></radialGradient></defs>
            <rect x="0" y="0" width="500" height="470" fill="url(#ar-floor)" />
            {lens === 'zones' ? <ZoneShapes fill={zoneFill} /> : null}
            <g className="ar-lines">
              <rect x="170" y="0" width="160" height="190" /><circle cx="250" cy="190" r="60" /><path d="M210 52.5 A40 40 0 0 0 290 52.5" />
              <line x1="30" y1="0" x2="30" y2="140" /><line x1="470" y1="0" x2="470" y2="140" /><path d="M30 140 A237.5 237.5 0 0 0 470 140" />
              <line x1="220" y1="40" x2="280" y2="40" className="ar-board" /><circle cx="250" cy="52.5" r="7.5" className="ar-rim" />
              <path d="M190 470 A60 60 0 0 1 310 470" /><line x1="0" y1="469" x2="500" y2="469" />
            </g>
            {lens === 'shots' ? shown.map((p) => {
              const c = col[p.team ?? ''] ?? '#cbd5e1'; const three = p.attempted === 3
              return p.scoring
                ? <circle key={p.id} cx={cx(p.coord!.x)} cy={cy(p.coord!.y)} r={three ? 7.5 : 6} fill={c} stroke="#fff" strokeWidth={three ? 1.6 : 0.8} className="ar-shot"><title>{p.text}</title></circle>
                : <circle key={p.id} cx={cx(p.coord!.x)} cy={cy(p.coord!.y)} r={5.5} fill="none" stroke={c} strokeWidth={2} opacity={0.75} className="ar-shot"><title>{p.text}</title></circle>
            }) : <ZoneLabels agg={zoneAgg} />}
            {latest && lens === 'shots' ? <ShotFlight key={latest.id} p={latest} color={col[latest.team ?? ''] ?? '#fff'} /> : null}
          </svg>
          {latest ? <ShotCall key={latest.id} p={latest} color={col[latest.team ?? ''] ?? '#fff'} /> : null}
        </div>
        {lens === 'zones' ? <p className="ar-small">Red: shooting better than a typical NBA team from that zone; blue: worse. {zoneTeam ? '' : 'Both teams combined; pick a team to split.'}</p> : null}
        <ZoneTable zones={zones} home={home} away={away} />
      </section>
      <div className="ar-col">
        <GameFlow feed={feed} />
        <Runs feed={feed} />
        <Leaders feed={feed} />
      </div>
      <BoxScore feed={feed} />
      <PlayFeed feed={feed} league="nba" />
    </div>
  )
}

function ZoneShapes({ fill }: { fill: (z: Zone) => string }) {
  return (
    <g className="ar-zones">
      <path d="M0 0 H30 V140 A237.5 237.5 0 0 0 470 140 V0 H500 V470 H0 Z" fill={fill('ab3')} />
      <rect x="0" y="0" width="30" height="140" fill={fill('c3')} /><rect x="470" y="0" width="30" height="140" fill={fill('c3')} />
      <path d="M30 0 H470 V140 A237.5 237.5 0 0 1 30 140 Z" fill={fill('mid')} />
      <rect x="170" y="0" width="160" height="190" fill={fill('paint')} />
      <circle cx="250" cy="52.5" r="40" fill={fill('ra')} />
    </g>
  )
}
function ZoneLabels({ agg }: { agg: (z: Zone) => { m: number; a: number } }) {
  const at: Record<Zone, [number, number]> = { ra: [250, 62], paint: [250, 150], mid: [250, 235], c3: [28, 96], ab3: [250, 360] }
  return <g className="ar-zlabels">{(Object.keys(at) as Zone[]).map((z) => { const { m, a } = agg(z); return <g key={z} transform={`translate(${at[z][0]} ${at[z][1]})`}><text textAnchor="middle" className="ar-zl__pct">{a ? `${Math.round((m / a) * 100)}%` : '—'}</text><text textAnchor="middle" y="16" className="ar-zl__n">{m}/{a} · NBA {Math.round(LEAGUE_FG[z] * 100)}%</text></g> })}</g>
}

function ShotFlight({ p, color }: { p: ArenaPlay; color: string }) {
  const ref = useRef<SVGAnimateMotionElement | null>(null)
  useEffect(() => { try { ref.current?.beginElement() } catch { /* SMIL unsupported */ } }, [])
  const x0 = cx(p.coord!.x); const y0 = cy(p.coord!.y); const x1 = 250; const y1 = 52.5
  const peak = Math.min(y0, y1) - 60 - Math.hypot(x1 - x0, y1 - y0) * 0.25
  const path = `M${x0},${y0} Q${(x0 + x1) / 2},${peak} ${x1},${y1}`
  return (
    <g className="ar-flight" pointerEvents="none">
      <path d={path} className="ar-flight__trail" style={{ stroke: color }} />
      <circle r="7" fill="#f97316" stroke="#7c2d12" strokeWidth="1.5"><animateMotion ref={ref} dur="1.1s" begin="indefinite" fill="freeze" path={path} /></circle>
      <circle cx={x0} cy={y0} r="12" className="ar-flight__spot" style={{ stroke: color }} />
    </g>
  )
}
function ShotCall({ p, color }: { p: ArenaPlay; color: string }) {
  if (!p.scoring) return null
  const dunk = /dunk|alley/i.test(p.type ?? '')
  const word = dunk ? 'SLAM!' : p.attempted === 3 ? 'SPLASH!' : null
  if (!word) return null
  const who = p.players[0]
  return <div className="ar-call" style={{ ['--c' as string]: color } as CSSProperties}><b>{word}</b>{who?.name ? <span>{who.headshot ? <img src={who.headshot} alt="" /> : null}{who.name}</span> : null}</div>
}

function ZoneTable({ zones, home, away }: { zones: Record<string, Record<Zone, { m: number; a: number }>>; home: ArenaTeam; away: ArenaTeam }) {
  return (
    <table className="ar-ztable"><thead><tr><th>Zone</th><th>{away.abbr}</th><th>{home.abbr}</th><th>NBA</th></tr></thead>
      <tbody>{(Object.keys(ZONE_NAME) as Zone[]).map((z) => <tr key={z}><th>{ZONE_NAME[z]}</th>{[away.id, home.id].map((id) => { const { m, a } = zones[id][z]; return <td key={id} className={a && m / a > LEAGUE_FG[z] ? 'is-hot' : a ? 'is-cold' : ''}>{a ? `${m}/${a} · ${Math.round((m / a) * 100)}%` : '—'}</td> })}<td>{Math.round(LEAGUE_FG[z] * 100)}%</td></tr>)}</tbody></table>
  )
}

/** Score margin (home minus away) across the game, with ESPN's live home win probability. */
function GameFlow({ feed }: { feed: ArenaFeed }) {
  const league = feed.league
  const { home, away } = feed.teams
  const W = 640; const H = 190; const pad = 26
  const total = league === 'nba' ? Math.max(2880, elapsedAt('nba', feed.status.period, '0:00')) : Math.max(3600, elapsedAt('nhl', feed.status.period, '0:00'))
  const pts = useMemo(() => {
    const out: Array<{ t: number; m: number; id: string }> = [{ t: 0, m: 0, id: 'start' }]
    feed.plays.forEach((p) => { if (p.home != null && p.away != null) out.push({ t: elapsedAt(league, p.period, p.clock), m: p.home - p.away, id: p.id }) })
    return out
  }, [feed.plays, league])
  const maxM = Math.max(8, ...pts.map((p) => Math.abs(p.m)))
  const X = (t: number) => pad + (t / total) * (W - pad * 2)
  const Y = (m: number) => H / 2 - (m / maxM) * (H / 2 - 16)
  const line = pts.map((p) => `${X(p.t).toFixed(1)},${Y(p.m).toFixed(1)}`).join(' ')
  const wpIdx = new Map(feed.win_probability.map((w) => [w.play, w.home]))
  const wpLine = feed.plays.filter((p) => wpIdx.has(p.id)).map((p) => `${X(elapsedAt(league, p.period, p.clock)).toFixed(1)},${(16 + (1 - wpIdx.get(p.id)!) * (H - 32)).toFixed(1)}`).join(' ')
  const { home: hc, away: ac } = teamPair(home, away)
  const per = league === 'nba' ? 720 : 1200
  const last = pts[pts.length - 1]
  return (
    <section className="ar-card ar-flow" aria-label="Game flow">
      <header><h2>Game Flow <small>score margin{wpLine ? ' · dashed = live win probability' : ''}</small></h2><b className="ar-flow__now" style={{ color: last.m > 0 ? hc : last.m < 0 ? ac : '#e2e8f0' }}>{last.m === 0 ? 'Tied' : `${last.m > 0 ? home.abbr : away.abbr} +${Math.abs(last.m)}`}</b></header>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Score margin over time">
        <defs>
          <clipPath id="ar-above"><rect x="0" y="0" width={W} height={H / 2} /></clipPath>
          <clipPath id="ar-below"><rect x="0" y={H / 2} width={W} height={H / 2} /></clipPath>
        </defs>
        {Array.from({ length: Math.ceil(total / per) + 1 }, (_, i) => <g key={i}><line x1={X(i * per)} x2={X(i * per)} y1={8} y2={H - 8} className="ar-flow__grid" /><text x={X(i * per) + 4} y={H - 4} className="ar-flow__tick">{i < Math.ceil(total / per) ? periodName(league, i + 1) : ''}</text></g>)}
        <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="ar-flow__zero" />
        <polygon points={`${X(0)},${H / 2} ${line} ${X(last.t)},${H / 2}`} fill={hc} opacity="0.35" clipPath="url(#ar-above)" />
        <polygon points={`${X(0)},${H / 2} ${line} ${X(last.t)},${H / 2}`} fill={ac} opacity="0.35" clipPath="url(#ar-below)" />
        <polyline points={line} className="ar-flow__line" />
        {wpLine ? <polyline points={wpLine} className="ar-flow__wp" /> : null}
        <text x={W - pad} y={16} textAnchor="end" className="ar-flow__lab" fill={hc}>{home.abbr} ahead ↑</text>
        <text x={W - pad} y={H - 18} textAnchor="end" className="ar-flow__lab" fill={ac}>{away.abbr} ahead ↓</text>
      </svg>
    </section>
  )
}

/** Unanswered scoring runs: the biggest for each team, and the one happening right now. */
function Runs({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const runs = useMemo(() => {
    let cur: { team: string; pts: number; from: ArenaPlay; to: ArenaPlay } | null = null
    const all: Array<{ team: string; pts: number; from: ArenaPlay; to: ArenaPlay }> = []
    let ph = 0; let pa = 0
    feed.plays.forEach((p) => {
      if (p.home == null || p.away == null) return
      const dh = p.home - ph; const da = p.away - pa
      ph = p.home; pa = p.away
      if (!dh && !da) return
      const t = dh > 0 ? home.id : away.id; const v = dh > 0 ? dh : da
      if (cur && cur.team === t) { cur.pts += v; cur.to = p } else { if (cur) all.push(cur); cur = { team: t, pts: v, from: p, to: p } }
    })
    if (cur) all.push(cur)
    return { all, current: cur as { team: string; pts: number; from: ArenaPlay; to: ArenaPlay } | null }
  }, [feed.plays, home.id, away.id])
  const best = (id: string) => runs.all.filter((r) => r.team === id).sort((a, b) => b.pts - a.pts)[0]
  const name = (id: string) => (id === home.id ? home : away)
  const big = runs.current && runs.current.pts >= 7 && feed.status.state === 'in' ? runs.current : null
  return (
    <section className="ar-card ar-runs" aria-label="Scoring runs">
      <h2>Run Detector</h2>
      {big ? <div className="ar-run-now" style={{ ['--c' as string]: onNavy(name(big.team).color, name(big.team).alt) } as CSSProperties}><b>{name(big.team).abbr} ON A {big.pts}-0 RUN</b><small>since {periodName('nba', big.from.period)} {big.from.clock}</small></div> : null}
      <ul>{[away, home].map((t) => { const r = best(t.id); return <li key={t.id} style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}><b>{t.abbr}</b>{r ? <span>biggest run <strong>{r.pts}-0</strong> · {periodName('nba', r.from.period)} {r.from.clock} to {periodName('nba', r.to.period)} {r.to.clock}</span> : <span>no run yet</span>}</li> })}</ul>
    </section>
  )
}

// ---------------------------------------------------------------- NHL --

const RX = (x: number) => (x + 100) * 4
const RY = (y: number) => (42.5 - y) * 4
type RinkEvent = ArenaPlay & { nx: number; ny: number; shooter: string | null; danger: 'high' | 'med' | 'low'; kind: 'goal' | 'shot' | 'miss' | 'block' }

function NhlView({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = { [home.id]: pair.home, [away.id]: pair.away }
  const [team, setTeam] = useState<'all' | string>('all')
  const [lens, setLens] = useState<'all' | 'danger'>('all')
  const events = useMemo<RinkEvent[]>(() => {
    // which way the home team attacks in each period (teams switch ends): from its own shots and goals
    const dir = new Map<number, number>()
    for (const per of Array.from(new Set(feed.plays.map((p) => p.period ?? 0)))) {
      const xs = feed.plays.filter((p) => p.period === per && p.team === home.id && p.coord && /^(Shot|Goal|Missed)$/.test(p.type ?? '')).map((p) => p.coord!.x)
      const ax = feed.plays.filter((p) => p.period === per && p.team === away.id && p.coord && /^(Shot|Goal|Missed)$/.test(p.type ?? '')).map((p) => -p.coord!.x)
      const s = [...xs, ...ax].reduce((a, b) => a + b, 0)
      dir.set(per, s >= 0 ? 1 : -1)
    }
    return feed.plays.filter((p) => p.coord && /^(Shot|Goal|Missed|Blocked)$/.test(p.type ?? '')).map((p) => {
      const f = dir.get(p.period ?? 0) ?? 1
      const nx = p.coord!.x * f; const ny = p.coord!.y * f
      const kind = p.type === 'Goal' ? 'goal' : p.type === 'Shot' ? 'shot' : p.type === 'Missed' ? 'miss' : 'block'
      const shooter = kind === 'block' ? (p.team === home.id ? away.id : home.id) : p.team
      const goalX = shooter === home.id ? 89 : -89
      const dist = Math.hypot(goalX - nx, ny)
      const danger = dist < 22 && Math.abs(ny) < 14 ? 'high' : dist < 40 ? 'med' : 'low'
      return { ...p, nx, ny, shooter, danger, kind } as RinkEvent
    })
  }, [feed.plays, home.id, away.id])
  const shown = events.filter((e) => team === 'all' || e.shooter === team)
  const goals = events.filter((e) => e.kind === 'goal')
  const latestGoal = goals[goals.length - 1] ?? null
  // a GOAL callout fires only for a goal that arrives while the page is open
  const seen = useRef<string | null | undefined>(undefined)
  const [flash, setFlash] = useState<RinkEvent | null>(null)
  useEffect(() => {
    if (seen.current === undefined) { seen.current = latestGoal?.id ?? null; return undefined }
    if (latestGoal && latestGoal.id !== seen.current) {
      seen.current = latestGoal.id; setFlash(latestGoal)
      const t = window.setTimeout(() => setFlash(null), 6500)
      return () => window.clearTimeout(t)
    }
    return undefined
  }, [latestGoal])
  const count = (id: string, k?: RinkEvent['kind'] | 'danger') => events.filter((e) => e.shooter === id && (k === 'danger' ? e.danger === 'high' && e.kind !== 'block' : !k || e.kind === k)).length
  const onGoal = (id: string) => count(id, 'shot') + count(id, 'goal')
  return (
    <div className="ar-grid">
      <section className="ar-card ar-rink" aria-label="Rink">
        <header>
          <h2>The Rink <small>{away.abbr} attacks ← · → {home.abbr} attacks</small></h2>
          <span className="ar-seg">{(['all', 'danger'] as const).map((k) => <button key={k} type="button" className={lens === k ? 'is-on' : ''} onClick={() => setLens(k)}>{k === 'all' ? 'Every attempt' : '⚠ Danger map'}</button>)}</span>
          <span className="ar-seg">{[['all', 'Both'], [away.id, away.abbr], [home.id, home.abbr]].map(([k, l]) => <button key={k} type="button" className={team === k ? 'is-on' : ''} onClick={() => setTeam(k)}>{l}</button>)}</span>
          {latestGoal ? <button type="button" className="ar-chip" onClick={() => { setFlash(null); window.setTimeout(() => setFlash(latestGoal), 30); window.setTimeout(() => setFlash(null), 6500) }}>🚨 Replay last goal</button> : null}
        </header>
        <div className="ar-rink__wrap">
          <svg viewBox="0 0 800 340" role="img" aria-label="Rink shot map">
            <defs><linearGradient id="ar-ice" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#e8f1f8" /><stop offset="1" stopColor="#cfdde9" /></linearGradient></defs>
            <rect x="2" y="2" width="796" height="336" rx="112" className="ar-ice" />
            {lens === 'danger' ? <g className="ar-slot"><path d={`M${RX(89)} ${RY(-4)} L${RX(69)} ${RY(-22)} L${RX(55)} ${RY(-14)} L${RX(55)} ${RY(14)} L${RX(69)} ${RY(22)} L${RX(89)} ${RY(4)} Z`} /><path d={`M${RX(-89)} ${RY(-4)} L${RX(-69)} ${RY(-22)} L${RX(-55)} ${RY(-14)} L${RX(-55)} ${RY(14)} L${RX(-69)} ${RY(22)} L${RX(-89)} ${RY(4)} Z`} /></g> : null}
            <line x1={RX(0)} x2={RX(0)} y1="2" y2="338" className="ar-red" />
            <line x1={RX(-25)} x2={RX(-25)} y1="2" y2="338" className="ar-blue" /><line x1={RX(25)} x2={RX(25)} y1="2" y2="338" className="ar-blue" />
            <line x1={RX(-89)} x2={RX(-89)} y1="18" y2="322" className="ar-goalline" /><line x1={RX(89)} x2={RX(89)} y1="18" y2="322" className="ar-goalline" />
            {[[-69, 22], [-69, -22], [69, 22], [69, -22], [0, 0]].map(([x, y]) => <circle key={`${x},${y}`} cx={RX(x)} cy={RY(y)} r="60" className="ar-circle" />)}
            {[[-20, 22], [-20, -22], [20, 22], [20, -22]].map(([x, y]) => <circle key={`d${x},${y}`} cx={RX(x)} cy={RY(y)} r="5" className="ar-dot" />)}
            <path d={`M${RX(89)} ${RY(-4)} A24 24 0 0 0 ${RX(89)} ${RY(4)}`} className="ar-crease" /><path d={`M${RX(-89)} ${RY(-4)} A24 24 0 0 1 ${RX(-89)} ${RY(4)}`} className="ar-crease" />
            <rect x={RX(89)} y={RY(3)} width="14" height="24" className="ar-net" /><rect x={RX(-89) - 14} y={RY(3)} width="14" height="24" className="ar-net" />
            <text x={RX(-62)} y="330" className="ar-rink__lab" textAnchor="middle">{away.abbr} ATTACK</text><text x={RX(62)} y="330" className="ar-rink__lab" textAnchor="middle">{home.abbr} ATTACK</text>
            {shown.map((e) => {
              const c = col[e.shooter ?? ''] ?? '#334155'
              const op = lens === 'danger' ? (e.danger === 'high' ? 1 : e.danger === 'med' ? 0.55 : 0.2) : 1
              const x = RX(e.nx); const y = RY(e.ny)
              if (e.kind === 'goal') return <g key={e.id} className="ar-goal" transform={`translate(${x} ${y})`}><circle r="15" className="ar-goal__ring" style={{ stroke: c }} /><path d="M0 -11 L3 -3 L11 -3 L5 2 L7 10 L0 5 L-7 10 L-5 2 L-11 -3 L-3 -3 Z" fill={c} stroke="#0b1526" strokeWidth="1.2" /><title>{e.text}</title></g>
              if (e.kind === 'block') return <rect key={e.id} x={x - 4} y={y - 4} width="8" height="8" fill="none" stroke={c} strokeWidth="1.8" opacity={op * 0.8}><title>{e.text}</title></rect>
              return <circle key={e.id} cx={x} cy={y} r={e.kind === 'shot' ? 6 : 5} fill={e.kind === 'shot' ? c : 'none'} stroke={e.kind === 'shot' ? '#0b1526' : c} strokeWidth={e.kind === 'shot' ? 1 : 2} opacity={op}><title>{e.text}</title></circle>
            })}
          </svg>
          {flash ? <GoalCall key={flash.id} e={flash} color={col[flash.shooter ?? ''] ?? '#ef4444'} team={flash.shooter === home.id ? home : away} /> : null}
        </div>
        <div className="ar-rink__key"><span>★ goal</span><span>● shot on goal</span><span>○ missed</span><span>□ blocked</span>{lens === 'danger' ? <span className="is-slot">shaded slot = high-danger area</span> : null}</div>
        <table className="ar-ztable"><thead><tr><th /><th>{away.abbr}</th><th>{home.abbr}</th></tr></thead>
          <tbody>
            <tr><th>Shots on goal</th><td>{onGoal(away.id)}</td><td>{onGoal(home.id)}</td></tr>
            <tr><th>All attempts</th><td>{count(away.id)}</td><td>{count(home.id)}</td></tr>
            <tr><th>High-danger chances</th><td className={count(away.id, 'danger') > count(home.id, 'danger') ? 'is-hot' : ''}>{count(away.id, 'danger')}</td><td className={count(home.id, 'danger') > count(away.id, 'danger') ? 'is-hot' : ''}>{count(home.id, 'danger')}</td></tr>
            <tr><th>Goals</th><td>{count(away.id, 'goal')}</td><td>{count(home.id, 'goal')}</td></tr>
          </tbody></table>
      </section>
      <div className="ar-col">
        <Momentum feed={feed} events={events} />
        <OnIce feed={feed} />
        <Leaders feed={feed} />
      </div>
      <BoxScore feed={feed} />
      <PlayFeed feed={feed} league="nhl" />
    </div>
  )
}

function GoalCall({ e, color, team }: { e: RinkEvent; color: string; team: ArenaTeam }) {
  const who = e.players[0]
  return (
    <div className="ar-goalcall" style={{ ['--c' as string]: color } as CSSProperties}>
      <i className="ar-goalcall__light" />
      <b>GOAL!</b>
      <span>{team.logo ? <img src={team.logo} alt="" className="ar-goalcall__logo" /> : null}{who?.headshot ? <img src={who.headshot} alt="" /> : null}<em>{who?.name ?? team.short}</em><small>{e.strength && e.strength !== 'Even Strength' ? e.strength : ''} · {periodName('nhl', e.period)} {e.clock}</small></span>
    </div>
  )
}

/** Cumulative shot attempts, home minus away, with every goal marked. */
function Momentum({ feed, events }: { feed: ArenaFeed; events: RinkEvent[] }) {
  const { home, away } = feed.teams
  const W = 640; const H = 170; const pad = 24
  const total = Math.max(3600, elapsedAt('nhl', feed.status.period, '0:00'))
  let d = 0
  const pts = [{ t: 0, d: 0 }, ...events.map((e) => { d += e.shooter === home.id ? 1 : -1; return { t: elapsedAt('nhl', e.period, e.clock), d } })]
  const maxD = Math.max(6, ...pts.map((p) => Math.abs(p.d)))
  const X = (t: number) => pad + (t / total) * (W - pad * 2)
  const Y = (v: number) => H / 2 - (v / maxD) * (H / 2 - 14)
  const line = pts.map((p) => `${X(p.t).toFixed(1)},${Y(p.d).toFixed(1)}`).join(' ')
  const { home: hc, away: ac } = teamPair(home, away)
  const last = pts[pts.length - 1]
  return (
    <section className="ar-card ar-flow" aria-label="Shot attempt momentum">
      <header><h2>Momentum <small>shot attempts, {home.abbr} minus {away.abbr}</small></h2><b className="ar-flow__now" style={{ color: last.d >= 0 ? hc : ac }}>{last.d === 0 ? 'Even' : `${last.d > 0 ? home.abbr : away.abbr} +${Math.abs(last.d)}`}</b></header>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Shot attempt differential over time">
        <defs><clipPath id="ar-mabove"><rect x="0" y="0" width={W} height={H / 2} /></clipPath><clipPath id="ar-mbelow"><rect x="0" y={H / 2} width={W} height={H / 2} /></clipPath></defs>
        {[0, 1, 2, 3].map((i) => <g key={i}><line x1={X(i * 1200)} x2={X(i * 1200)} y1={8} y2={H - 8} className="ar-flow__grid" /><text x={X(i * 1200) + 4} y={H - 4} className="ar-flow__tick">{i < 3 ? periodName('nhl', i + 1) : ''}</text></g>)}
        <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="ar-flow__zero" />
        <polygon points={`${X(0)},${H / 2} ${line} ${X(last.t)},${H / 2}`} fill={hc} opacity="0.35" clipPath="url(#ar-mabove)" />
        <polygon points={`${X(0)},${H / 2} ${line} ${X(last.t)},${H / 2}`} fill={ac} opacity="0.35" clipPath="url(#ar-mbelow)" />
        <polyline points={line} className="ar-flow__line" />
        {events.filter((e) => e.kind === 'goal').map((e) => { const x = X(elapsedAt('nhl', e.period, e.clock)); const up = e.shooter === home.id; return <g key={e.id}><line x1={x} x2={x} y1={14} y2={H - 14} stroke={up ? hc : ac} strokeDasharray="3 3" /><text x={x} y={up ? 12 : H - 16} textAnchor="middle" className="ar-flow__goal" fill={up ? hc : ac}>🚨</text></g> })}
      </svg>
    </section>
  )
}

function OnIce({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  if (!feed.on_ice.length) return null
  return (
    <section className="ar-card ar-onice" aria-label="On the ice">
      <h2>On the Ice Now</h2>
      {[away, home].map((t) => (
        <div key={t.id} className="ar-onice__row" style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
          <b>{t.abbr}</b>
          <span>{feed.on_ice.filter((p) => p.team === t.id).sort((a, b) => (a.pos === 'G' ? 1 : 0) - (b.pos === 'G' ? 1 : 0)).map((p) => <figure key={p.id}>{p.headshot ? <img src={p.headshot} alt="" /> : <i>{p.jersey ?? '?'}</i>}<figcaption>{p.short ?? p.name}<small>{p.pos}{p.jersey ? ` #${p.jersey}` : ''}</small></figcaption></figure>)}</span>
        </div>
      ))}
    </section>
  )
}

// ------------------------------------------------------------- shared --

function Leaders({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  if (!feed.leaders.length) return null
  return (
    <section className="ar-card ar-leaders" aria-label="Leaders">
      <h2>Leaders</h2>
      <div className="ar-leaders__grid">
        {[away, home].map((t) => (
          <div key={t.id} style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
            <b className="ar-leaders__team">{t.abbr}</b>
            {feed.leaders.filter((l) => l.team === t.id).slice(0, 3).map((l) => <span key={`${l.category}-${l.id}`} className="ar-leader">{l.headshot ? <img src={l.headshot} alt="" /> : <i />}<span><small>{l.category}</small><b>{l.name}</b><em>{l.value}</em></span></span>)}
          </div>
        ))}
      </div>
    </section>
  )
}

function BoxScore({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const nba = feed.league === 'nba'
  const num = (v: string | undefined) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
  const rows = (id: string) => feed.athletes.filter((a) => a.team === id && (nba ? a.stats.minutes && a.stats.minutes !== '0' : a.group !== 'goalies'))
    .sort((a, b) => (nba ? num(b.stats.points) - num(a.stats.points) : num(b.stats.goals) * 2 + num(b.stats.assists) - (num(a.stats.goals) * 2 + num(a.stats.assists)) || num(b.stats.shotsTotal) - num(a.stats.shotsTotal))).slice(0, 6)
  const goalie = (id: string) => feed.athletes.find((a) => a.team === id && a.group === 'goalies' && a.stats.saves != null)
  return (
    <section className="ar-card ar-box" aria-label="Box score">
      <h2>Box Score <small>top players</small></h2>
      <div className="ar-box__grid">
        {[away, home].map((t) => (
          <table key={t.id} style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
            <thead><tr><th>{t.abbr}</th>{nba ? <><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>FG</th><th>3PT</th></> : <><th>G</th><th>A</th><th>S</th><th>+/-</th><th>TOI</th></>}</tr></thead>
            <tbody>
              {rows(t.id).map((a) => <tr key={a.id}><th>{a.headshot ? <img src={a.headshot} alt="" /> : null}{a.short ?? a.name}</th>{nba ? <><td>{a.stats.minutes}</td><td className="is-big">{a.stats.points}</td><td>{a.stats.rebounds}</td><td>{a.stats.assists}</td><td>{a.stats['fieldGoalsMade-fieldGoalsAttempted']}</td><td>{a.stats['threePointFieldGoalsMade-threePointFieldGoalsAttempted']}</td></> : <><td className="is-big">{a.stats.goals}</td><td>{a.stats.assists}</td><td>{a.stats.shotsTotal}</td><td>{a.stats.plusMinus}</td><td>{a.stats.timeOnIce}</td></>}</tr>)}
              {!nba && goalie(t.id) ? (() => { const g = goalie(t.id)!; return <tr className="ar-box__g"><th>{g.headshot ? <img src={g.headshot} alt="" /> : null}{g.short} (G)</th><td colSpan={5}>{g.stats.saves} saves{g.stats.shotsAgainst ? ` of ${g.stats.shotsAgainst}` : ''}{g.stats.savePct ? ` · ${g.stats.savePct}` : ''}</td></tr> })() : null}
            </tbody>
          </table>
        ))}
      </div>
    </section>
  )
}

function PlayFeed({ feed, league }: { feed: ArenaFeed; league: 'nba' | 'nhl' }) {
  const [scoringOnly, setScoringOnly] = useState(false)
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = { [home.id]: pair.home, [away.id]: pair.away }
  const skip = /substitution|stoppage|period start|period end|timeout|face off/i
  const list = feed.plays.filter((p) => !skip.test(p.type ?? '') && (!scoringOnly || p.scoring)).slice(-40).reverse()
  return (
    <section className="ar-card ar-feed" aria-label="Play by play">
      <header><h2>Play by Play</h2><button type="button" className={`ar-chip${scoringOnly ? ' is-on' : ''}`} onClick={() => setScoringOnly((v) => !v)}>{scoringOnly ? 'Scoring plays only' : 'All plays'}</button></header>
      <ol>
        {list.map((p) => { const who = p.players[0]; return (
          <li key={p.id} className={p.scoring ? 'is-score' : ''} style={{ ['--c' as string]: col[p.team ?? ''] ?? '#475569' } as CSSProperties}>
            <span className="ar-feed__when">{periodName(league, p.period)} {p.clock}</span>
            {who?.headshot ? <img src={who.headshot} alt="" /> : <i />}
            <span className="ar-feed__text">{p.scoring ? <b>{league === 'nhl' ? '🚨 ' : ''}{p.text}</b> : p.text}</span>
            <span className="ar-feed__score">{p.away ?? ''}–{p.home ?? ''}</span>
          </li>) })}
      </ol>
    </section>
  )
}
