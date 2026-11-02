/**
 * THE ARENA: YOU vs THE MACHINE (2026-10-26, owner: "more like a video game ... HOLY SHIT moments").
 *
 * Tonight's slate is a fight card. You call each game before it starts; AISP's call sits face-down until you
 * turn it over (lock BEFORE you look = a BLIND call, ×1.5 XP). Power 1-3 says how sure you are. When a game
 * goes final the round resolves on screen: a hit lands on whoever was wrong, health drains, a combo grows, XP
 * pours into your level bar and badges unlock. The Prophet Duel (D3) races your running record against
 * AISP's on every game you both called. REWIND lets you play last night's finished games blind, right now.
 *
 * Honesty rules: play only, no money, stored in this browser. AISP's card is the paper policy's side when it
 * bet or decided, otherwise the model's favourite; it is labelled which. Scores come from AISP's scoreboard.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { AispSide } from '../flow/AispSide'
import { Crest } from '../flow/Crest'
import { pickOf } from '../flow/slip'
import { useOptionalUser } from '../../hooks/useAuth'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { teamIdentity } from '../../services/teamIdentity'
import { etClock, pct, usePulse, type PulseGame } from '../pulse/pulseData'
import type { SportCode } from '../../services/commandCenter'
import {
  badgesOf, levelOf, currentProfile, dropCall, lockCall, markSeen, resetArena, reveal, settle, titleOf, useArena, useLeague, xpOf,
  type ArenaCall, type LeagueCall, type Side,
} from './arenaStore'
import { BOSS_HIT, bossOf, rivalryOf, weekStart } from './league'
import { useProgress } from '../progress/progress'
import { clearRehearsalArena } from './arenaStore'
import { simulateNight, startRehearsal, useRehearsal } from '../rehearsal/rehearsal'
import { hit as sfxHit, levelUp as sfxLevel, miss as sfxMiss } from '../progress/sound'
import '../slate/slate.css'
import './arena.css'

const SPORT_COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }

/**
 * AISP's card for a game: the side the paper policy actually BET (it can be a value underdog, labelled so),
 * otherwise the model's favourite. A mere value lean is not a "who wins" call, so it never sets the card.
 */
function aispCall(g: PulseGame): { side: Side; prob: number | null; basis: string } | null {
  const p = pickOf(g)
  if (p && (p.source === 'position' || p.source === 'decision')) return { side: p.side, prob: p.model, basis: p.model < 0.5 ? 'AISP BET · VALUE DOG' : 'AISP BET' }
  if (g.modelHome == null) return null
  const side: Side = g.modelHome >= 0.5 ? 'HOME' : 'AWAY'
  return { side, prob: side === 'HOME' ? g.modelHome : 1 - g.modelHome, basis: 'MODEL PICK' }
}
const winnerOf = (g: PulseGame): Side | 'TIE' | null =>
  g.phase !== 'FINAL' || !g.score ? null : g.score.home > g.score.away ? 'HOME' : g.score.away > g.score.home ? 'AWAY' : 'TIE'
const abbrOf = (g: { home: string; away: string } | PulseGame, s: Side) => {
  const v = s === 'HOME' ? g.home : g.away
  return typeof v === 'string' ? v : v.abbr
}
function teamColor(sport: string, abbr: string): string {
  if (sport !== 'CFB') { const id = teamIdentity(sport as SportCode, abbr); if (id.known) return id.primary }
  return `hsl(${[...abbr].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)} 60% 55%)`
}

// ------------------------------------------------------------------ effects --

/** One-shot particle burst on a full-screen canvas (level up, KO). Pure canvas, no library. */
function burst(colors: string[], count = 140): void {
  if (typeof document === 'undefined' || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  const cv = document.createElement('canvas')
  cv.className = 'ar-burst'
  cv.width = window.innerWidth; cv.height = window.innerHeight
  document.body.appendChild(cv)
  const ctx = cv.getContext('2d')
  if (!ctx) { cv.remove(); return }
  const cx = cv.width / 2, cy = cv.height * 0.38
  const parts = Array.from({ length: count }, () => {
    const a = Math.random() * Math.PI * 2, v = 4 + Math.random() * 11
    return { x: cx, y: cy, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 4, r: 2 + Math.random() * 4, c: colors[Math.floor(Math.random() * colors.length)], life: 1 }
  })
  let frame = 0
  const tick = () => {
    ctx.clearRect(0, 0, cv.width, cv.height)
    parts.forEach((p) => { p.x += p.vx; p.y += p.vy; p.vy += 0.32; p.vx *= 0.985; p.life -= 0.011; ctx.globalAlpha = Math.max(0, p.life); ctx.fillStyle = p.c; ctx.fillRect(p.x, p.y, p.r, p.r * 1.6) })
    if (++frame < 110) requestAnimationFrame(tick); else cv.remove()
  }
  requestAnimationFrame(tick)
}

// ------------------------------------------------------------------ page --

interface Moment { id: string; kind: 'KO' | 'HIT' | 'BLOCK' | 'PUSH'; title: string; sub: string; xp: number; tags: string[] }

export function ArenaPage() {
  const realToday = operationalToday()
  const pulse = usePulse(realToday)
  // REHEARSAL: last night replayed on a simulated clock (features/rehearsal); real games otherwise.
  const reh = useRehearsal()
  const today = reh.active ? reh.day : realToday
  const games = useMemo(() => (reh.active ? simulateNight(pulse.games, reh.day, reh.now).map((x) => x.game) : pulse.games), [pulse.games, reh.active, reh.day, Math.floor(reh.now / 15_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  const rehearse = () => {
    const night = pulse.games.filter((g) => g.day === shiftDate(realToday, -1) && g.score)
    if (!night.length) return
    clearRehearsalArena()
    startRehearsal(shiftDate(realToday, -1), Math.min(...night.map((g) => Date.parse(g.startsAt))))
  }
  const arena = useArena()
  const user = useOptionalUser()
  const you = currentProfile()
  const league = useLeague()
  // In a rehearsal the rival is a labelled bot (60% the model's favourite, otherwise the other side), seeded per game.
  const bot = useMemo(() => (!reh.active ? null : { profile: 'ELVIS_BOT', calls: games.filter((g) => g.day === today).map((g) => {
    const fav: Side = (g.modelHome ?? 0.5) >= 0.5 ? 'HOME' : 'AWAY'
    const h = [...g.id].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) % 1000, 7) / 1000
    const side: Side = h < 0.6 ? fav : fav === 'HOME' ? 'AWAY' : 'HOME'
    const started = Date.parse(g.startsAt) <= reh.now
    const mineHere = arena.calls.some((c) => c.game_id === g.id)
    const w = winnerOf(g)
    return started || mineHere ? { game_id: g.id, side, starts_at: g.startsAt, away: g.away.abbr, home: g.home.abbr, result: w ? { winner: w, aisp: null, at: '' } : undefined } as LeagueCall : { game_id: g.id, starts_at: g.startsAt, hidden: true }
  }) }), [reh.active, games, today, arena.calls, Math.floor(reh.now / 15_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  const rivals = bot ? [bot] : (league?.players ?? []).filter((x) => x.profile !== you)
  const [rivalName, setRivalName] = useState<string>('ELVIS')
  const rival = rivals.find((x) => x.profile === rivalName) ?? rivals[0] ?? null
  const ghostsFor = (gameId: string) => rivals.map((r) => ({ who: r.profile, c: r.calls.find((c) => c.game_id === gameId) })).filter((x): x is { who: string; c: LeagueCall } => Boolean(x.c))
  const wk = weekStart(reh.active ? reh.now : Date.now()) // a rehearsal is scored in the week it replays
  const boss = bossOf(arena.calls, wk)
  const rivalry = rival ? rivalryOf(arena.calls, rival.calls, wk) : null
  const [mode, setMode] = useState<'TONIGHT' | 'REWIND'>('TONIGHT')
  const [power, setPower] = useState<Record<string, 1 | 2 | 3>>({})
  const [moment, setMoment] = useState<Moment | null>(null)
  const [shake, setShake] = useState(false)
  const [levelUp, setLevelUp] = useState<number | null>(null)
  const [realNow, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t) }, [])
  const now = reh.active ? reh.now : realNow

  const byId = useMemo(() => new Map(games.map((g) => [g.id, g])), [games])
  const callOf = (id: string) => arena.calls.find((c) => c.game_id === id)

  // Settle any call whose game is now final (AISP's card is frozen at that moment).
  useEffect(() => {
    for (const c of arena.calls) {
      if (c.result) continue
      const g = byId.get(c.game_id)
      const w = g ? winnerOf(g) : null
      if (g && w) settle(c.game_id, { winner: w, aisp: aispCall(g)?.side ?? null, at: new Date().toISOString() })
    }
  }, [arena.calls, byId])

  // One level for the whole site: Arena XP plus exploring, daily visits and locked picks (features/progress).
  const progress = useProgress()
  const rehXp = arena.calls.reduce((n, c) => n + xpOf(c).xp, 0)
  const xpTotal = reh.active ? rehXp : progress.xp
  const lvl = reh.active ? { ...levelOf(rehXp) } : { level: progress.level, into: progress.into, need: progress.need }

  // Fresh results play their moment once: hit, KO, level up.
  useEffect(() => {
    const fresh = arena.calls.filter((c) => c.result && !arena.seenResults.includes(c.game_id))
    if (!fresh.length) return
    const c = fresh[fresh.length - 1]
    const r = c.result!
    const { xp, tags } = xpOf(c)
    const me = r.winner === c.side, bot = r.aisp != null && r.winner === r.aisp
    const kind: Moment['kind'] = r.winner === 'TIE' ? 'PUSH' : me && !bot ? 'KO' : me ? 'HIT' : 'BLOCK'
    const title = kind === 'KO' ? 'CRITICAL HIT!' : kind === 'HIT' ? 'DIRECT HIT' : kind === 'PUSH' ? 'DRAW' : bot ? 'AISP STRIKES' : 'BOTH MISSED'
    const team = abbrOf(c, r.winner === 'TIE' ? c.side : r.winner)
    setMoment({ id: c.game_id, kind, title, sub: r.winner === 'TIE' ? `${c.away} @ ${c.home} ended level` : `${team} won · ${c.away} @ ${c.home}`, xp, tags })
    if (kind === 'KO' || kind === 'HIT') burst(kind === 'KO' ? ['#34d399', '#a7f3d0', '#fde68a', '#ffffff'] : ['#34d399', '#d1fae5'], kind === 'KO' ? 200 : 90)
    if (kind === 'KO' || kind === 'BLOCK') { setShake(true); window.setTimeout(() => setShake(false), 520) }
    if (kind === 'KO' || kind === 'HIT') sfxHit(); else if (kind === 'BLOCK') sfxMiss()
    if (lvl.level > arena.level) { window.setTimeout(() => { setLevelUp(lvl.level); sfxLevel(); burst(['#fbbf24', '#fde68a', '#f59e0b', '#ffffff'], 260) }, 1500) }
    markSeen(fresh.map((f) => f.game_id), Math.max(arena.level, lvl.level))
  }, [arena.calls, arena.seenResults, arena.level, lvl.level])
  useEffect(() => { if (!moment) return; const t = window.setTimeout(() => setMoment(null), 3600); return () => window.clearTimeout(t) }, [moment])
  useEffect(() => { if (levelUp == null) return; const t = window.setTimeout(() => setLevelUp(null), 4200); return () => window.clearTimeout(t) }, [levelUp])

  // The card: tonight's games (or last night's finals in REWIND).
  const card = useMemo(() => {
    if (mode === 'REWIND') return pulse.games.filter((g) => g.day === shiftDate(realToday, -1) && g.phase === 'FINAL' && aispCall(g))
    return games.filter((g) => g.day === today && aispCall(g))
  }, [pulse.games, games, mode, today, realToday])

  // Tonight's fight: health drains on disagreements only (agreeing is a tag-team move, no damage).
  const tonight = arena.calls.filter((c) => c.mode === 'LIVE' && c.starts_at && byId.get(c.game_id)?.day === today)
  const clashes = tonight.filter((c) => { const g = byId.get(c.game_id); const a = g ? aispCall(g) : null; return a && a.side !== c.side })
  const dmg = 100 / Math.max(3, clashes.length)
  const hp = { you: 100, aisp: 100 }
  clashes.forEach((c) => { const r = c.result; if (!r || r.winner === 'TIE') return; if (r.winner === c.side) hp.aisp -= dmg; else hp.you -= dmg })
  hp.you = Math.max(0, hp.you); hp.aisp = Math.max(0, hp.aisp)

  // Season head to head on every settled LIVE call.
  const settled = arena.calls.filter((c) => c.result && c.result.winner !== 'TIE' && c.mode === 'LIVE')
  const season = { you: settled.filter((c) => c.result!.winner === c.side).length, aisp: settled.filter((c) => c.result!.aisp === c.result!.winner).length, n: settled.length }
  let combo = 0
  for (const c of [...arena.calls].filter((c) => c.result && c.result.winner !== 'TIE').sort((a, b) => Date.parse(b.starts_at) - Date.parse(a.starts_at))) { if (c.result!.winner === c.side) combo++; else break }
  const badges = badgesOf(arena.calls)

  const lock = (g: PulseGame, side: Side) => {
    // The House = the market favourite at lock. A rehearsal replays history, so the stored consensus counts there.
    let house: Side | null = (g.marketFresh || reh.active) && g.marketHome != null ? (g.marketHome >= 0.5 ? 'HOME' : 'AWAY') : null
    if (!house && reh.active && g.decision?.market_probability != null && g.decision.selection) {
      // A replayed night: the market probability AISP recorded on its decision for the side it looked at.
      const sel = g.decision.selection.toUpperCase()
      const selSide: Side | null = sel.includes(g.home.abbr.toUpperCase()) ? 'HOME' : sel.includes(g.away.abbr.toUpperCase()) ? 'AWAY' : null
      if (selSide) house = g.decision.market_probability >= 0.5 ? selSide : selSide === 'HOME' ? 'AWAY' : 'HOME'
    }
    lockCall({ game_id: g.id, sport: g.sport, away: g.away.abbr, home: g.home.abbr, starts_at: g.startsAt, side, power: power[g.id] ?? 1, mode: mode === 'REWIND' ? 'REWIND' : 'LIVE', house })
  }

  return (
    <div className={`hq ar${shake ? ' is-shake' : ''}`}>
      <AispSide />
      <div className="hq-main">
        <main id="main-content" className="ar-main">
          <section className="ar-vs" aria-label="You versus AISP">
            <div className="ar-fighter ar-fighter--you">
              <div className="ar-fighter__tag">P1 · {titleOf(lvl.level)} · LV {lvl.level}</div>
              <div className="ar-fighter__name">{you}</div>
              <Hp value={hp.you} side="you" />
              <div className="ar-fighter__meta">Season {season.you}–{season.n - season.you}{combo >= 2 ? <b className="ar-combo"> 🔥 {combo} COMBO</b> : null}</div>
            </div>
            <div className="ar-vs__mid">
              <div className="ar-vs__badge">VS</div>
              <small>{mode === 'REWIND' ? 'REWIND · last night' : `ROUND ${tonight.length} · ${clashes.length} clash${clashes.length === 1 ? '' : 'es'}`}</small>
            </div>
            <div className="ar-fighter ar-fighter--aisp">
              <div className="ar-fighter__tag">CPU · THE MACHINE</div>
              <div className="ar-fighter__name">AISP</div>
              <Hp value={hp.aisp} side="aisp" />
              <div className="ar-fighter__meta">Season {season.aisp}–{season.n - season.aisp} on your games</div>
            </div>
            {hp.aisp === 0 && clashes.length ? <div className="ar-ko" aria-live="polite">K.O.<small>You beat the machine tonight</small></div> : null}
            {hp.you === 0 && clashes.length ? <div className="ar-ko ar-ko--lost" aria-live="polite">K.O.<small>The machine wins tonight</small></div> : null}
          </section>

          <section className="ar-xp" aria-label="Your level">
            <div className="ar-xp__lv"><b>{lvl.level}</b><small>{titleOf(lvl.level)}</small></div>
            <div className="ar-xp__bar"><i style={{ width: `${(100 * lvl.into) / lvl.need}%` }} /><span>{lvl.into.toLocaleString()} / {lvl.need.toLocaleString()} XP to level {lvl.level + 1}</span></div>
            <div className="ar-xp__total">{xpTotal.toLocaleString()} XP{reh.active ? <small className="ar-xp__reh">REHEARSAL · not counted</small> : null}</div>
          </section>

          <div className="ar-cols">
            <section className="ar-card" aria-label="Fight card">
              <header className="ar-card__head">
                <h1>{mode === 'REWIND' ? 'Rewind: call last night blind' : reh.active ? 'Rehearsal fight card' : "Tonight's fight card"}</h1>
                {/* Rehearse last night: retired 10-27 */}
                {user ? <span className="ar-who">Playing as <b>{user.display_name}</b></span> : (
                  <span className="ar-who">Playing as <b>Guest</b> · calls stay on this device · <Link to="/login">Sign in to join the league</Link></span>
                )}
                <div className="ar-tabs" role="tablist">
                  <button role="tab" aria-selected={mode === 'TONIGHT'} className={mode === 'TONIGHT' ? 'is-on' : ''} onClick={() => setMode('TONIGHT')}>Tonight</button>
                  <button role="tab" aria-selected={mode === 'REWIND'} className={mode === 'REWIND' ? 'is-on' : ''} onClick={() => setMode('REWIND')}>Rewind ⟲</button>
                </div>
              </header>
              <p className="ar-card__how">Call the winner. Lock it <b>before</b> you flip AISP&apos;s card for a <b>BLIND ×1.5</b> bonus. Beat AISP when you disagree: <b>+150 MACHINE BREAKER</b>. {mode === 'REWIND' ? 'Rewind rounds count half XP and never touch the season fight.' : 'Calls close at the start.'}</p>
              {pulse.loading && !card.length ? <p className="ar-empty">Loading the card…</p> : null}
              {!pulse.loading && !card.length ? <p className="ar-empty">{mode === 'REWIND' ? 'No finished games from last night with a model call.' : 'No games with a model call tonight.'}</p> : null}
              <ol className="ar-rounds">
                {card.map((g, i) => {
                  const c = callOf(g.id)
                  const a = aispCall(g)!
                  const started = mode === 'TONIGHT' && Date.parse(g.startsAt) <= now
                  const shown = arena.revealed.includes(g.id) || (c != null && (started || mode === 'REWIND')) || g.phase === 'FINAL' && mode === 'TONIGHT'
                  const w = winnerOf(g)
                  const pw = power[g.id] ?? c?.power ?? 1
                  const open = !c && !started
                  const res = c?.result
                  const tone = !res ? '' : res.winner === 'TIE' ? 'is-push' : res.winner === c!.side ? 'is-win' : 'is-loss'
                  return (
                    <li key={g.id} className={`ar-round ${tone}${c ? ' is-locked' : ''}`} style={{ '--sp': SPORT_COLOR[g.sport] ?? '#94a3b8' } as CSSProperties}>
                      <div className="ar-round__no">R{i + 1}<small>{g.sport}</small></div>
                      <div className="ar-round__teams">
                        {(['AWAY', 'HOME'] as Side[]).map((s) => {
                          const t = s === 'HOME' ? g.home : g.away
                          const mine = c?.side === s
                          return (
                            <button key={s} type="button" className={`ar-team${mine ? ' is-mine' : ''}${w === s && (mode === 'TONIGHT' || c) ? ' is-won' : ''}`}
                              disabled={!open} onClick={() => lock(g, s)} style={{ '--tc': teamColor(g.sport, t.abbr) } as CSSProperties}
                              aria-label={`Call ${t.name}`}>
                              <Crest sport={g.sport} abbr={t.abbr} size={34} />
                              <span><b>{t.abbr}</b><small>{s === 'HOME' ? 'home' : 'away'}</small></span>
                              {mine ? <em>YOUR CALL{c?.blind ? ' · BLIND' : ''}</em> : null}
                              {mode === 'TONIGHT' ? <span className="ar-ghosts">{ghostsFor(g.id).filter((x) => !x.c.hidden && x.c.side === s).map((x) => <b key={x.who} title={`${x.who} called ${t.abbr}`}>👻 {x.who}</b>)}</span> : null}
                            </button>
                          )
                        })}
                      </div>
                      <div className="ar-round__power" aria-label="Power">
                        {[1, 2, 3].map((n) => (
                          <button key={n} type="button" disabled={!open} className={pw >= n ? 'is-on' : ''} onClick={() => setPower((p) => ({ ...p, [g.id]: n as 1 | 2 | 3 }))} title={`Power ${n}: ${100 * n} XP if right`}>⚡</button>
                        ))}
                      </div>
                      <button type="button" className={`ar-flip${shown ? ' is-shown' : ''}`} onClick={() => reveal(g.id)} disabled={shown} aria-label="Turn over AISP's card">
                        <span className="ar-flip__in">
                          <span className="ar-flip__back">AISP<small>{c ? 'tap to reveal' : 'face down · tap to peek'}</small></span>
                          <span className="ar-flip__front"><small>{a.basis}</small><b>{abbrOf(g, a.side)}</b><small>{a.prob != null ? pct(a.prob) : ''}</small></span>
                        </span>
                      </button>
                      <div className="ar-round__state">
                        {g.score && (g.phase === 'FINAL' || g.phase === 'LIVE') && (mode === 'TONIGHT' || c) ? <b>{g.away.abbr} {g.score.away} – {g.score.home} {g.home.abbr}<small>{g.phase === 'FINAL' ? 'FINAL' : g.score.detail}</small></b>
                          : mode === 'REWIND' ? <small>{c ? 'resolving…' : 'final hidden'}</small>
                          : started ? <small>{c ? 'LIVE · locked' : 'started · closed'}</small> : <small>{etClock(g.startsAt)} ET</small>}
                        {mode === 'TONIGHT' && ghostsFor(g.id).some((x) => x.c.hidden) ? <small className="ar-sealed">🔒 {ghostsFor(g.id).filter((x) => x.c.hidden).map((x) => x.who).join(', ')} locked · lock yours to see</small> : null}
                        {res ? <em className="ar-round__xp">{xpOf(c!).xp ? `+${xpOf(c!).xp} XP` : res.winner === 'TIE' ? 'draw' : 'miss'}</em> : c && open === false && !started && mode === 'TONIGHT' ? <button type="button" className="ar-undo" onClick={() => dropCall(g.id)}>change</button> : null}
                      </div>
                    </li>
                  )
                })}
              </ol>
            </section>

            <aside className="ar-side">
              <section className={`ar-panel ar-boss${boss.broken ? ' is-broken' : ''}`} aria-label="Weekly boss: The House">
                <div className="ar-boss__art" aria-hidden="true"><span>♠</span><b>HOUSE</b></div>
                <div className="ar-boss__body">
                  <h2>Weekly Boss <small>resets Monday</small></h2>
                  <p className="ar-panel__sub">The House is the market&apos;s favourite when you lock. Go against it and win: −{BOSS_HIT} HP to the boss. Lose: −{BOSS_HIT} to you. Siding with the market is safe.</p>
                  <div className="ar-boss__bar"><span>THE HOUSE</span><Hp value={boss.bossHp} side="aisp" /></div>
                  <div className="ar-boss__bar"><span>{you}</span><Hp value={boss.youHp} side="you" /></div>
                  <small className="ar-boss__tally">{boss.broken ? '💥 THE HOUSE IS BROKEN this week' : `${boss.wins}–${boss.losses} against the market this week${boss.pending ? ` · ${boss.pending} fight${boss.pending === 1 ? '' : 's'} pending` : ''}`}</small>
                </div>
              </section>
              <section className="ar-panel ar-rival" aria-label="Rival mode">
                <h2>Rival Mode {rivals.length ? <select value={rival?.profile ?? ''} onChange={(e) => setRivalName(e.target.value)}>{rivals.map((r) => <option key={r.profile} value={r.profile}>{r.profile}</option>)}</select> : null}</h2>
                {!rival || !rivalry ? (
                  <p className="ar-panel__sub">No rival has played yet. Switch &ldquo;Playing as&rdquo; to ELVIS (or MOM) and make their calls, and the head-to-head starts here.</p>
                ) : (
                  <>
                    <div className="ar-rival__split">
                      <div className="is-you"><small>{you}</small><b>{rivalry.me}</b></div>
                      <div className="ar-rival__belt" title="This week's belt">{rivalry.belt ? <>🏆<small>{rivalry.belt === 'ME' ? you : rival.profile} holds the belt</small></> : <>🏆<small>belt up for grabs</small></>}</div>
                      <div className="is-them"><small>{rival.profile}</small><b>{rivalry.rival}</b></div>
                    </div>
                    <p className="ar-panel__sub">{rivalry.settled} shared games settled · this week {rivalry.weekMe}–{rivalry.weekRival} · {rivalry.clashes.length} clash{rivalry.clashes.length === 1 ? '' : 'es'} (you picked different sides)</p>
                    <ul className="ar-rival__list">
                      {rivalry.clashes.slice(0, 6).map((x) => (
                        <li key={x.game_id} className={!x.winner ? '' : x.winner === x.mine ? 'is-win' : x.winner === x.theirs ? 'is-loss' : ''}>
                          <span>{x.label}</span>
                          <span>{you} {x.mine === 'HOME' ? x.label.split(' @ ')[1] : x.label.split(' @ ')[0]} · {rival.profile} {x.theirs === 'HOME' ? x.label.split(' @ ')[1] : x.label.split(' @ ')[0]}</span>
                          <b>{!x.winner ? 'pending' : x.winner === x.mine ? `you took ${rival.profile} down` : `${rival.profile} got you`}</b>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
              <section className="ar-panel">
                <h2>The Prophet Duel</h2>
                <p className="ar-panel__sub">Running record (right minus wrong) on every game you both called.</p>
                <Duel calls={arena.calls} />
              </section>
              <section className="ar-panel">
                <h2>Trophy case <small>{badges.filter((b) => b.got).length}/{badges.length}</small></h2>
                <ul className="ar-badges">
                  {badges.map((b) => <li key={b.id} className={b.got ? 'is-got' : ''} title={b.how}><i>{b.icon}</i><b>{b.name}</b><small>{b.how}</small></li>)}
                </ul>
              </section>
              <p className="ar-fine">Play only: no stake, no money, saved in this browser. AISP&apos;s card is its paper bet or decision when one exists, otherwise its model&apos;s favourite. <Link to="/my-picks">Your real slip lives in My Picks →</Link> <button type="button" className="ar-reset" onClick={() => { if (window.confirm('Reset your Arena record?')) resetArena() }}>reset</button></p>
            </aside>
          </div>
        </main>
      </div>

      {moment ? (
        <div className={`ar-moment ar-moment--${moment.kind.toLowerCase()}`} role="status" aria-live="assertive" key={moment.id}>
          <b>{moment.title}</b>
          <span>{moment.sub}</span>
          {moment.xp ? <em>+{moment.xp} XP</em> : null}
          {moment.tags.length ? <small>{moment.tags.join(' · ')}</small> : null}
        </div>
      ) : null}
      {levelUp != null ? (
        <div className="ar-levelup" role="status" aria-live="assertive"><small>LEVEL UP</small><b>{levelUp}</b><span>{titleOf(levelUp)}</span></div>
      ) : null}
    </div>
  )
}

function Hp({ value, side }: { value: number; side: 'you' | 'aisp' }) {
  // The red "recent damage" ghost lags behind the real bar, fighting-game style.
  const [ghost, setGhost] = useState(value)
  useEffect(() => { const t = window.setTimeout(() => setGhost(value), 700); return () => window.clearTimeout(t) }, [value])
  return (
    <div className={`ar-hp ar-hp--${side}${value <= 30 ? ' is-low' : ''}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)} aria-label={`${side === 'you' ? 'Your' : "AISP's"} health`}>
      <i className="ar-hp__ghost" style={{ width: `${ghost}%` }} />
      <i className="ar-hp__fill" style={{ width: `${value}%` }} />
      <span>{Math.round(value)}</span>
    </div>
  )
}

/** D3: two racing lines, YOU (emerald) vs AISP (gold), drawn in on mount and whenever a round settles. */
function Duel({ calls }: { calls: ArenaCall[] }) {
  const ref = useRef<SVGSVGElement | null>(null)
  const rows = useMemo(() => calls.filter((c) => c.result && c.result.winner !== 'TIE' && c.result.aisp)
    .sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at)), [calls])
  useEffect(() => {
    const svg = d3.select(ref.current)
    if (!ref.current) return
    const W = 420, H = 210, m = { t: 14, r: 46, b: 22, l: 30 }
    svg.selectAll('*').remove()
    svg.attr('viewBox', `0 0 ${W} ${H}`)
    if (!rows.length) {
      svg.append('text').attr('x', W / 2).attr('y', H / 2).attr('text-anchor', 'middle').attr('class', 'ar-duel__empty').text('Settle a round to start the race')
      return
    }
    let y = 0, z = 0
    const pts = [{ i: 0, you: 0, aisp: 0 }, ...rows.map((c, i) => {
      y += c.result!.winner === c.side ? 1 : -1
      z += c.result!.winner === c.result!.aisp ? 1 : -1
      return { i: i + 1, you: y, aisp: z }
    })]
    const ext = d3.extent(pts.flatMap((p) => [p.you, p.aisp])) as [number, number]
    const x = d3.scaleLinear().domain([0, pts.length - 1]).range([m.l, W - m.r])
    const yS = d3.scaleLinear().domain([Math.min(-2, ext[0]), Math.max(2, ext[1])]).nice().range([H - m.b, m.t])
    const g = svg.append('g')
    g.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', yS(0)).attr('y2', yS(0)).attr('class', 'ar-duel__zero')
    g.append('g').attr('transform', `translate(${m.l},0)`).attr('class', 'ar-duel__axis').call(d3.axisLeft(yS).ticks(4).tickSize(-(W - m.l - m.r)))
    const series: Array<['you' | 'aisp', string]> = [['aisp', '#fbbf24'], ['you', '#34d399']]
    for (const [k, color] of series) {
      const line = d3.line<typeof pts[number]>().x((p) => x(p.i)).y((p) => yS(p[k])).curve(d3.curveMonotoneX)
      const area = d3.area<typeof pts[number]>().x((p) => x(p.i)).y0(yS(0)).y1((p) => yS(p[k])).curve(d3.curveMonotoneX)
      g.append('path').datum(pts).attr('d', area).attr('fill', color).attr('opacity', 0).transition().delay(900).duration(600).attr('opacity', 0.08)
      const path = g.append('path').datum(pts).attr('d', line).attr('fill', 'none').attr('stroke', color).attr('stroke-width', 3).attr('stroke-linecap', 'round')
      const len = (path.node() as SVGPathElement).getTotalLength()
      path.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len).transition().duration(1400).ease(d3.easeCubicOut).attr('stroke-dashoffset', 0)
      g.selectAll(`circle.${k}`).data(pts.slice(1)).join('circle').attr('class', k).attr('cx', (p) => x(p.i)).attr('cy', (p) => yS(p[k])).attr('r', 0).attr('fill', color)
        .transition().delay((_, i) => 1400 * ((i + 1) / pts.length)).duration(200).attr('r', 3.2)
      const last = pts[pts.length - 1]
      g.append('text').attr('x', W - m.r + 6).attr('y', yS(last[k]) + 4).attr('fill', color).attr('class', 'ar-duel__label').attr('opacity', 0)
        .text(`${k === 'you' ? 'YOU' : 'AISP'} ${last[k] >= 0 ? '+' : ''}${last[k]}`).transition().delay(1400).duration(300).attr('opacity', 1)
    }
  }, [rows])
  return <svg ref={ref} className="ar-duel" role="img" aria-label={`Running record: ${rows.length} shared rounds`} />
}
