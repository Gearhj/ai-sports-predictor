/**
 * The home page: YOUR SPORTS COMMAND CENTER (owner's design, sent several times; 2026-10-24 final pass).
 *   hero (welcome, tonight's stars from the live football games, five doors) · Your Performance
 *   Today's Games (featured live card + game cards) · My Upcoming Bets
 *   AISP Top Model Picks Today · Betting Opportunities · Live Games + Recent Key Insights
 * The attack map and the model edge explorer live on the Live View, not here (owner).
 * Theme: navy + brushed steel. Every number comes from AISP's read models; the live win-probability lines
 * and player cutouts come from ESPN through the test-mode preview feed. Paper experiment only.
 */
import { AispSide } from '../flow/AispSide'
import { StartNudge } from '../start/StartNudge'
import { Crest, cfbColor } from '../flow/Crest'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { TeamLogo } from '../../components/TeamLogo'
import { gameHref, type LedgerPosition, type SportCode } from '../../services/commandCenter'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { teamIdentity } from '../../services/teamIdentity'
import { allPositions, american, etClock, pct, resultOf, usd, usePulse, type PulseGame } from '../pulse/pulseData'
import { GamePlanBar } from '../flow/GamePlanBar'
import { addToSlip } from '../flow/slip'
import { removePick, usePreviewPicks } from '../../services/previewPicks'
import '../slate/slate.css'
import './home.css'

type Sport = 'ALL' | SportCode
const SPORTS: Array<{ key: SportCode; label: string; color: string; icon: string }> = [
  { key: 'NFL', label: 'NFL', color: '#6ea4ff', icon: '🏈' }, { key: 'CFB', label: 'CFB', color: '#b88cff', icon: '🏈' }, { key: 'NBA', label: 'NBA', color: '#ffb14a', icon: '🏀' },
  { key: 'NHL', label: 'NHL', color: '#7cc4e4', icon: '🏒' }, { key: 'MLB', label: 'MLB', color: '#ff6b57', icon: '⚾' },
]
const NAV = [{ to: '/', label: 'Home', icon: '⌂', on: true }, { to: '/live', label: 'Live View', icon: '◉' }, { to: '/experiment', label: 'My Bets', icon: '▤' }, { to: '/games', label: 'Games', icon: '▦' }, { to: '/performance', label: 'Analytics', icon: '▥' }, { to: '/research/history', label: 'Research', icon: '◎' }, { to: '/a-house-divided', label: 'Leaderboard', icon: '♛' }, { to: '/markets', label: 'Community', icon: '☺' }]
const isLive = (g: PulseGame) => g.phase === 'LIVE' || g.phase === 'STARTED'
const isFootball = (g: PulseGame) => g.sport === 'NFL' || g.sport === 'CFB'
const sideOf = (g: PulseGame, side: 'HOME' | 'AWAY') => (side === 'HOME' ? g.home : g.away)
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
const teamColor = (sport: SportCode, abbr: string) => { if (sport !== 'CFB') { const id = teamIdentity(sport, abbr); if (id.known) return id.primary } return cfbColor(abbr) ?? `hsl(${hueOf(abbr)} 60% 55%)` }
const short = (s: string, n = 10) => (s.length <= n ? s : s.split(/\s+/).map((w, i, a) => (i === a.length - 1 ? w : `${w[0]}.`)).join(' '))
const sgn = (v: number | null, d = 1, suf = '') => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}${suf}`)

type Pick = { side: 'HOME' | 'AWAY'; model: number; market: number | null; edge: number | null; price: number | null; source: 'position' | 'decision' | 'lean' | 'model' }
function pickOf(g: PulseGame): Pick | null {
  const fromSel = (sel: string | null | undefined): 'HOME' | 'AWAY' | null => { const s = (sel ?? '').toUpperCase(); if (!s) return null; if (s.includes(g.home.abbr.toUpperCase()) || s.includes('HOME')) return 'HOME'; if (s.includes(g.away.abbr.toUpperCase()) || s.includes('AWAY')) return 'AWAY'; return null }
  const side = (g.position && fromSel(g.position.selection)) || (g.decision && fromSel(g.decision.selection)) || g.lean?.side || g.favorite?.side || null
  if (!side || g.modelHome == null) return null
  const model = side === 'HOME' ? g.modelHome : 1 - g.modelHome
  const market = g.marketHome == null ? null : side === 'HOME' ? g.marketHome : 1 - g.marketHome
  const best = side === 'HOME' ? g.bestHome : g.bestAway
  return { side, model, market: g.marketFresh ? market : null, edge: g.marketFresh && market != null ? (model - market) * 100 : null, price: g.position?.entry.american ?? g.decision?.american ?? best?.american ?? null, source: g.position ? 'position' : g.decision && /QUALIFIED|BET/i.test(g.decision.recommendation) ? 'decision' : g.lean ? 'lean' : 'model' }
}
const tierOf = (e: number | null) => (e == null ? null : e >= 6 ? { label: 'Strong', cls: 'strong' } : e >= 3 ? { label: 'Moderate', cls: 'moderate' } : e > 0 ? { label: 'Lean', cls: 'lean' } : null)

/** Live win probability (thinned) + the situation line for a live football game, from the preview feed. */
function useLiveWp(g: PulseGame | null, enabled: boolean): { home: number[]; situation: string | null } | null {
  const [data, setData] = useState<{ home: number[]; situation: string | null } | null>(null)
  useEffect(() => {
    if (!enabled || !g) return undefined
    let alive = true
    const q = new URLSearchParams({ league: g.sport === 'CFB' ? 'college-football' : 'nfl', date: operationalToday(new Date(g.startsAt)).replace(/-/g, ''), home: g.home.abbr, away: g.away.abbr, home_name: g.home.name, away_name: g.away.name, lite: '1' })
    const load = () => fetch(`/preview-api/football/live?${q.toString()}`).then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : null)).then((b) => { if (alive && b?.found) setData({ home: (b.win_probability ?? []).map((w: { home: number }) => w.home).filter((v: unknown) => typeof v === 'number'), situation: b.situation?.text ? `${b.situation.text}${b.situation.possessionText ? ` · ${b.situation.possessionText}` : ''}` : null }) }).catch(() => undefined)
    void load(); const t = window.setInterval(load, 30_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [enabled, g?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  return data
}

/** Tonight's stars: the stat leaders of up to two live football games, as ESPN's transparent headshot cutouts. */
type Star = { id: string; name: string; line: string; img: string; team: string }
function useStars(games: PulseGame[]): Star[] {
  const [stars, setStars] = useState<Star[]>([])
  const key = games.map((g) => g.id).join(',')
  useEffect(() => {
    if (!games.length) { setStars([]); return undefined }
    let alive = true
    Promise.all(games.slice(0, 2).map((g) => {
      const league = g.sport === 'CFB' ? 'college-football' : 'nfl'
      const q = new URLSearchParams({ league, date: operationalToday(new Date(g.startsAt)).replace(/-/g, ''), home: g.home.abbr, away: g.away.abbr, home_name: g.home.name, away_name: g.away.name })
      return fetch(`/preview-api/football/live?${q.toString()}`).then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : null)).then((b) => {
        if (!b?.found) return [] as Star[]
        const abbr = (tid: string) => (b.teams?.home?.id === tid ? b.teams.home.abbr : b.teams?.away?.abbr) ?? ''
        return (b.leaders ?? []).filter((l: { id?: string }) => l.id).map((l: { id: string; name: string; line: string; team: string }) => ({ id: l.id, name: l.name, line: l.line, team: abbr(l.team), img: `https://a.espncdn.com/i/headshots/${league}/players/full/${l.id}.png` }))
      }).catch(() => [] as Star[])
    })).then((lists) => { if (!alive) return; const seen = new Set<string>(); const out: Star[] = []; const longest = Math.max(0, ...lists.map((l) => l.length)); for (let i = 0; i < longest; i += 1) for (const l of lists) { const s = l[i]; if (s && !seen.has(s.id)) { seen.add(s.id); out.push(s) } } setStars(out.slice(0, 5)) })
    return () => { alive = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return stars
}

export function HqHome() {
  const [params, setParams] = useSearchParams()
  const today = operationalToday()
  const day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('day') ?? '') ? (params.get('day') as string) : today
  const pulse = usePulse(day)
  const [sport, setSport] = useState<Sport>('ALL')
  const [pickSport, setPickSport] = useState<Sport>('ALL')
  const [order, setOrder] = useState<'bets' | 'time' | 'edge'>('bets')
  const [q, setQ] = useState('')
  const navigate = useNavigate()
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(t) }, [])
  const dayGames = useMemo(() => pulse.games.filter((g) => g.day === day && (!q.trim() || `${g.away.abbr} ${g.away.name} ${g.home.abbr} ${g.home.name}`.toLowerCase().includes(q.trim().toLowerCase()))), [pulse.games, day, q])
  const live = useMemo(() => dayGames.filter(isLive), [dayGames])
  const counts = useMemo(() => { const m = new Map<string, number>(); for (const g of dayGames) m.set(g.sport, (m.get(g.sport) ?? 0) + 1); return m }, [dayGames])
  const picks = useMemo(() => new Map(dayGames.map((g) => [g.id, pickOf(g)])), [dayGames])
  const positions = useMemo(() => allPositions(pulse.ledger), [pulse.ledger])
  const open = useMemo(() => positions.filter((p) => resultOf(p) === 'OPEN').sort((a, b) => Date.parse(a.starts_at ?? '') - Date.parse(b.starts_at ?? '')), [positions])
  const settled = useMemo(() => positions.filter((p) => resultOf(p) !== 'OPEN' && p.settled_at).sort((a, b) => Date.parse(a.settled_at!) - Date.parse(b.settled_at!)), [positions])
  const gameById = useMemo(() => new Map(pulse.games.map((g) => [g.id, g])), [pulse.games])
  const cards = useMemo(() => dayGames.filter((g) => sport === 'ALL' || g.sport === sport).sort((a, b) => Number(isLive(b)) - Number(isLive(a)) || (order === 'bets' ? Number(Boolean(b.position)) - Number(Boolean(a.position)) : 0) || (order === 'edge' ? (picks.get(b.id)?.edge ?? -99) - (picks.get(a.id)?.edge ?? -99) : 0) || Date.parse(a.startsAt) - Date.parse(b.startsAt)), [dayGames, sport, order, picks])
  const featured = cards.find((g) => isLive(g) && isFootball(g)) ?? cards.find(isLive) ?? null
  const ranked = useMemo(() => dayGames.filter((g) => g.phase !== 'FINAL' && (pickSport === 'ALL' || g.sport === pickSport)).map((g) => ({ g, p: picks.get(g.id) })).filter((x): x is { g: PulseGame; p: Pick } => Boolean(x.p && x.p.edge != null && x.p.edge > 0)).sort((a, b) => (b.p.edge ?? 0) - (a.p.edge ?? 0)), [dayGames, picks, pickSport])
  const stars = useStars(live.filter(isFootball))
  const railRef = useRef<HTMLDivElement | null>(null)
  const slip = usePreviewPicks()
  const onSlip = new Set(slip.map((p) => p.game_id))
  const openGame = (g: PulseGame) => navigate(isLive(g) && isFootball(g) ? `/live?game=${g.id}` : gameHref(g.sport, g.id))
  const stakes = settled.map((p) => p.stake ?? 0).filter((v) => v > 0).sort((a, b) => a - b)
  const unit = stakes.length ? stakes[Math.floor(stakes.length / 2)] : 5

  return (
    <div className="hq hm">
      <AispSide>
        <small className="hq-side__label">SPORTS</small>
        <nav className="hq-nav hq-nav--sports">
          {SPORTS.map((s) => <button key={s.key} type="button" className={sport === s.key ? 'is-on' : ''} onClick={() => setSport(sport === s.key ? 'ALL' : s.key)}><i aria-hidden="true">{s.icon}</i>{s.label}<em>{counts.get(s.key) ?? 0}</em></button>)}
          <Link to="/mma" className="hq-nav__mma" title="UFC Fight Night: historical archive, test mode"><i aria-hidden="true">🥊</i>MMA · UFC</Link>
          <button type="button" disabled title="Not covered by AISP yet"><i aria-hidden="true">🥊</i>Boxing</button>
        </nav>
      </AispSide>
      <div className="hq-main">
        <header className="hm-top">
          <label className="hq-search"><span className="lf-sr">Search</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams, players, games, or ask a question…" /></label>
          <span className="hq-date"><button type="button" aria-label="Previous day" onClick={() => setParams({ day: shiftDate(day, -1) })}>‹</button><b>📅 {day === today ? 'Today, ' : ''}{new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(`${day}T12:00:00Z`))}</b><button type="button" aria-label="Next day" onClick={() => setParams({ day: shiftDate(day, 1) })}>›</button></span>
          <select className="hq-select" value={sport} onChange={(e) => setSport(e.target.value as Sport)} aria-label="Sport"><option value="ALL">All Sports</option>{SPORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          <Link to="/mission-control" className="hq-bell" title="Alerts">🔔{live.length ? <i>{live.length}</i> : null}</Link>
          <Link to="/account" className="hq-avatar">JG</Link>
        </header>
        <GamePlanBar current="scan" />

        <div className="hm-grid">
          <div className="hm-main">
            <StartNudge />
            <section className="hm-hero" aria-label="Welcome">
              <div className="hm-hero__stars" aria-hidden={!stars.length}>
                {stars.length ? stars.map((s, i) => <figure key={s.id} className="hm-star" style={{ ['--i' as string]: i } as CSSProperties} title={`${s.name} (${s.team}) · ${s.line}`}><img src={s.img} alt={s.name} onError={(e) => { (e.currentTarget.parentElement as HTMLElement).style.display = 'none' }} /><figcaption><b>{s.name}</b><small>{s.team} · {s.line}</small></figcaption></figure>)
                  : dayGames.slice(0, 5).map((g, i) => <span key={g.id} className="hm-star hm-star--logo" style={{ ['--i' as string]: i } as CSSProperties}><Crest sport={g.sport} abbr={g.home.abbr} size={110} /></span>)}
              </div>
              <div className="hm-hero__text">
                <small>WELCOME TO AISP</small>
                <h1>YOUR SPORTS<br />COMMAND CENTER</h1>
                <p>AI-powered predictions. Real-time data. Your bets. All in one place.</p>
              </div>
              <nav className="hm-doors" aria-label="Shortcuts">
                <Link to="/live"><i>◉</i><span><b>Live Games</b><small>Watch, track, analyze</small></span></Link>
                <Link to="/decisions"><i>✦</i><span><b>AI Model Picks</b><small>Our highest-confidence plays</small></span></Link>
                <Link to="/experiment"><i>▤</i><span><b>Your Bets</b><small>Track your action in real time</small></span></Link>
                <Link to="/performance"><i>▥</i><span><b>Advanced Analytics</b><small>Find edges, explore trends</small></span></Link>
                <Link to="/live"><i>💬</i><span><b>Ask the Booth</b><small>Get instant answers</small></span></Link>
              </nav>
            </section>

            <section className="sl-card hm-games" aria-label="Today's games">
              <header className="sl-card__head">
                <h2>Today&apos;s Games</h2>
                <span className="hm-tabs">
                  <button type="button" className={sport === 'ALL' ? 'is-on' : ''} onClick={() => setSport('ALL')}>All Sports ({dayGames.length})</button>
                  {SPORTS.map((s) => <button key={s.key} type="button" className={sport === s.key ? 'is-on' : ''} onClick={() => setSport(s.key)} disabled={!counts.get(s.key)}>{s.label} ({counts.get(s.key) ?? 0})</button>)}
                  <button type="button" disabled>MMA (0)</button><button type="button" disabled>Boxing (0)</button>
                </span>
                <label className="hm-show">Show <select value={order} onChange={(e) => setOrder(e.target.value as typeof order)}><option value="bets">My Bets First</option><option value="edge">Biggest Edge</option><option value="time">Start Time</option></select></label>
              </header>
              <div className="hm-railwrap">
                <div className="hm-rail" ref={railRef}>
                  {featured ? <FeaturedCard g={featured} pick={picks.get(featured.id) ?? null} onOpen={() => openGame(featured)} /> : null}
                  {cards.filter((g) => g !== featured).slice(0, 30).map((g) => <GameCard key={g.id} g={g} pick={picks.get(g.id) ?? null} onOpen={() => openGame(g)} />)}
                  {!cards.length ? <p className="sl-empty">{pulse.loading ? 'Loading the slate…' : 'No games.'}</p> : null}
                </div>
                <button type="button" className="hm-rail__arrow" aria-label="More games" onClick={() => railRef.current?.scrollBy({ left: 600, behavior: 'smooth' })}>›</button>
              </div>
            </section>

            <div className="hm-pair">
              <section className="sl-card hm-picks" aria-label="AISP top model picks today">
                <header className="sl-card__head"><h2>AISP Top Model Picks Today</h2>
                  <span className="hm-tabs hm-tabs--sm"><button type="button" className={pickSport === 'ALL' ? 'is-on' : ''} onClick={() => setPickSport('ALL')}>All Sports</button>{SPORTS.map((s) => <button key={s.key} type="button" className={pickSport === s.key ? 'is-on' : ''} onClick={() => setPickSport(s.key)}>{s.label}</button>)}</span>
                  <Link to="/decisions" className="hm-all">View All ›</Link>
                </header>
                <table className="hm-table">
                  <thead><tr><th>#</th><th>Game</th><th>Pick</th><th>AISP Win%</th><th>Market%</th><th>Edge</th><th>Confidence</th><th /><th>Slip</th></tr></thead>
                  <tbody>
                    {ranked.slice(0, 6).map((x, i) => { const t = tierOf(x.p.edge); const team = sideOf(x.g, x.p.side); return (
                      <tr key={x.g.id} onClick={() => openGame(x.g)}>
                        <td><span className={`hm-rank hm-rank--${Math.min(i, 4)}`}>{i + 1}</span></td>
                        <td><span className="hm-game"><Crest sport={x.g.sport} abbr={x.g.away.abbr} size={20} /><span>{short(x.g.away.abbr)} @ {short(x.g.home.abbr)}</span></span></td>
                        <td><b>{short(team.abbr)} {x.p.price != null ? american(x.p.price) : ''}</b></td>
                        <td>{pct(x.p.model)}</td><td>{pct(x.p.market)}</td>
                        <td className="is-up"><b>{sgn(x.p.edge, 1, '%')}</b></td>
                        <td>{t ? <span className={`hm-conf hm-conf--${t.cls}`}>{t.label}</span> : null}</td>
                        <td><span className="hm-bars" aria-hidden="true">{[0.33, 0.66, 1].map((h, k) => <i key={k} style={{ height: `${h * 14}px`, opacity: (x.p.edge ?? 0) >= [0, 3, 6][k] ? 1 : 0.25 }} />)}</span></td>
                        <td>{x.g.phase === 'UPCOMING' ? <button type="button" className={`hm-slip${onSlip.has(x.g.id) ? ' is-on' : ''}`} onClick={(e) => { e.stopPropagation(); if (onSlip.has(x.g.id)) removePick(x.g.id); else addToSlip(x.g) }} title={onSlip.has(x.g.id) ? 'On your My Picks slip' : 'Add to your play-money slip'}>{onSlip.has(x.g.id) ? '✓' : '+'}</button> : <small className="hm-dim">live</small>}</td>
                      </tr>) })}
                    {!ranked.length ? <tr><td colSpan={9} className="sl-empty">No game with a fresh price and a positive edge yet.</td></tr> : null}
                  </tbody>
                </table>
              </section>
              <section className="sl-card hm-opps" aria-label="Betting opportunities">
                <header className="sl-card__head"><h2>Betting Opportunities</h2><Link to="/markets" className="hm-all">View All ›</Link></header>
                <span className="hm-tabs hm-tabs--sm"><button type="button" disabled title="Spreads are not collected by AISP">Spreads</button><button type="button" disabled title="Totals are not collected by AISP">Totals</button><button type="button" className="is-on">Moneyline</button><button type="button" disabled title="Prop prices are not collected">Player Props</button></span>
                <table className="hm-table">
                  <thead><tr><th>Game</th><th>Market</th><th>AISP</th><th>Edge</th><th>Chart</th></tr></thead>
                  <tbody>
                    {ranked.slice(0, 6).map((x) => { const team = sideOf(x.g, x.p.side); return (
                      <tr key={x.g.id} onClick={() => openGame(x.g)}>
                        <td><span className="hm-game"><Crest sport={x.g.sport} abbr={team.abbr} size={20} /><span>{short(x.g.away.abbr)} @ {short(x.g.home.abbr)}</span></span></td>
                        <td>{short(team.abbr)} {x.p.price != null ? american(x.p.price) : ''}</td>
                        <td>{pct(x.p.model)}</td>
                        <td className="is-up">{sgn(x.p.edge, 1, '%')}</td>
                        <td><span className="hm-bar"><u style={{ width: `${Math.min(100, (x.p.edge ?? 0) * 4)}%` }} /></span></td>
                      </tr>) })}
                    {!ranked.length ? <tr><td colSpan={5} className="sl-empty">Prices are still coming in.</td></tr> : null}
                  </tbody>
                </table>
              </section>
            </div>
          </div>

          <aside className="hm-side">
            <Performance settled={settled} unit={unit} now={now} />
            <section className="sl-card hm-upcoming" aria-label="My upcoming bets">
              <header className="sl-card__head"><h2>My Upcoming Bets</h2><Link to="/experiment" className="hm-all">View All ›</Link></header>
              <ul className="hm-list">
                {open.slice(0, 4).map((p) => { const g = gameById.get(p.game_id); return (
                  <li key={p.position_id}><button type="button" onClick={() => g && openGame(g)}>
                    <span className="hm-list__logos">{g ? <><Crest sport={p.sport} abbr={g.away.abbr} size={24} /><Crest sport={p.sport} abbr={g.home.abbr} size={24} /></> : null}</span>
                    <span className="hm-list__main"><b>{short(p.selection ?? '', 18)} {p.entry.american != null ? american(p.entry.american) : ''}</b><small className="is-up">{((p.stake ?? 0) / unit).toFixed(1)}u · {usd(p.stake)}</small></span>
                    <span className="hm-list__meta"><b>{g && isLive(g) ? 'LIVE' : etClock(p.starts_at, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</b><small>{p.sport}</small></span><em>›</em>
                  </button></li>) })}
                {!open.length ? <li className="sl-empty">No open paper bets.</li> : null}
              </ul>
            </section>
            <section className="sl-card hm-live" aria-label="Live games">
              <header className="sl-card__head"><h2>Live Games</h2><Link to="/live" className="hm-all">View All ›</Link></header>
              <ul className="hm-list">{live.slice(0, 5).map((g) => <LiveItem key={g.id} g={g} onOpen={() => openGame(g)} />)}{!live.length ? <li className="sl-empty">Nothing live right now.</li> : null}</ul>
            </section>
            <section className="sl-card hm-insights" aria-label="Recent key insights">
              <header className="sl-card__head"><h2>Recent Key Insights</h2><Link to="/mission-control" className="hm-all">View All ›</Link></header>
              <Insights games={dayGames} picks={picks} settled={settled} now={now} onOpen={openGame} />
            </section>
          </aside>
        </div>
        <footer className="hq-foot hm-foot">Research and paper only. Picks, bets and edges are the $5,000 paper experiment&apos;s, never advice or a wager signal. Spreads, totals and props are not collected.</footer>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- pieces --

function Spark({ series, ca, ch, h = 34 }: { series: number[]; ca: string; ch: string; h?: number }) {
  const W = 120; const n = series.length
  if (n < 2) return null
  const X = (i: number) => (i / (n - 1)) * W
  const Y = (v: number) => 2 + (1 - v) * (h - 4)
  const d = series.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  const last = series[n - 1]
  return <svg className="hm-spark" viewBox={`0 0 ${W} ${h}`} preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2={W} y1={Y(0.5)} y2={Y(0.5)} stroke="rgba(255,255,255,.14)" strokeDasharray="2 3" /><path d={d} fill="none" stroke={last >= 0.5 ? ch : ca} strokeWidth="2" strokeLinejoin="round" /><circle cx={W} cy={Y(last)} r="2.6" fill={last >= 0.5 ? ch : ca} /></svg>
}

function FeaturedCard({ g, pick, onOpen }: { g: PulseGame; pick: Pick | null; onOpen: () => void }) {
  const wp = useLiveWp(g, isFootball(g))
  const ca = teamColor(g.sport, g.away.abbr); const ch = teamColor(g.sport, g.home.abbr)
  return (
    <article className="hm-feat" style={{ ['--ca' as string]: ca, ['--ch' as string]: ch } as CSSProperties}>
      <header><span className="sl-live">LIVE</span><b>{g.sport}</b><small>{g.score?.detail ?? ''}</small></header>
      <div className="hm-feat__teams">
        {(['AWAY', 'HOME'] as const).map((s) => { const t = sideOf(g, s); return <span key={s} className={pick?.side === s ? 'is-pick' : ''}><Crest sport={g.sport} abbr={t.abbr} size={30} /><b>{short(t.abbr, 14)}</b><strong>{g.score ? (s === 'AWAY' ? g.score.away : g.score.home) : ''}</strong></span> })}
      </div>
      {wp?.situation ? <small className="hm-feat__sit">{wp.situation}</small> : null}
      <div className="hm-feat__model">
        <span><small>Model</small><b>{pick ? `${short(sideOf(g, pick.side).abbr, 8)} ${pct(pick.model)}` : '—'}</b><small>Edge</small><b className={(pick?.edge ?? 0) >= 0 ? 'is-up' : 'is-down'}>{sgn(pick?.edge ?? null, 1, '%')}</b></span>
        {wp?.home.length ? <Spark series={wp.home} ca={ca} ch={ch} h={40} /> : null}
      </div>
      <button type="button" className="hm-feat__cta" onClick={onOpen}>Open Live View →</button>
    </article>
  )
}

function GameCard({ g, pick, onOpen }: { g: PulseGame; pick: Pick | null; onOpen: () => void }) {
  const t = g.position ? { label: `Our bet · ${usd(g.position.stake)}`, cls: 'bet' } : tierOf(pick?.edge ?? null)
  const live = isLive(g)
  const sportC = SPORTS.find((s) => s.key === g.sport)
  const price = (s: 'AWAY' | 'HOME') => { const b = s === 'AWAY' ? g.bestAway : g.bestHome; return b?.american != null ? american(b.american) : g.modelHome != null ? pct(s === 'HOME' ? g.modelHome : 1 - g.modelHome) : '' }
  return (
    <article className={`hm-gc${live ? ' is-live' : ''}`} onClick={onOpen} style={{ ['--ca' as string]: teamColor(g.sport, g.away.abbr), ['--ch' as string]: teamColor(g.sport, g.home.abbr), ['--sc' as string]: sportC?.color } as CSSProperties}>
      <header><span className="hm-gc__lg"><i aria-hidden="true">{sportC?.icon}</i>{g.sport}</span><small>{live ? <><span className="sl-live">LIVE</span> {g.score ? `${g.score.away}-${g.score.home}` : ''}</> : g.phase === 'FINAL' ? 'Final' : etClock(g.startsAt, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</small></header>
      <div className="hm-gc__vs">
        {(['AWAY', 'HOME'] as const).map((s) => { const team = sideOf(g, s); return <span key={s} className={pick?.side === s ? 'is-pick' : ''}><Crest sport={g.sport} abbr={team.abbr} size={40} /><b>{short(team.abbr, 9)}</b><small>{price(s)}</small></span> })}
      </div>
      <p className="hm-gc__nums"><span>AISP <b className="is-up">{pick ? pct(pick.model) : '—'}</b></span><span>Market <b>{pick?.market != null ? pct(pick.market) : '—'}</b></span></p>
      {t ? <span className={`hm-conf hm-conf--${t.cls}`}>{t.label.toUpperCase()}{t.cls !== 'bet' ? ' EDGE' : ''}</span> : <span className="hm-conf">MODEL ONLY</span>}
    </article>
  )
}

function LiveItem({ g, onOpen }: { g: PulseGame; onOpen: () => void }) {
  const wp = useLiveWp(g, isFootball(g))
  const ca = teamColor(g.sport, g.away.abbr); const ch = teamColor(g.sport, g.home.abbr)
  return (
    <li><button type="button" onClick={onOpen}>
      <span className="hm-live__st"><span className="sl-live">LIVE</span><small>{g.score?.detail ?? ''}</small></span>
      <span className="hm-live__teams"><span><Crest sport={g.sport} abbr={g.away.abbr} size={18} />{short(g.away.abbr, 11)}<b>{g.score?.away ?? ''}</b></span><span><Crest sport={g.sport} abbr={g.home.abbr} size={18} />{short(g.home.abbr, 11)}<b>{g.score?.home ?? ''}</b></span></span>
      {wp?.home.length ? <Spark series={wp.home} ca={ca} ch={ch} /> : <span className="hm-live__nowp">{g.modelHome != null ? `model ${pct(Math.max(g.modelHome, 1 - g.modelHome))} ${short(g.modelHome >= 0.5 ? g.home.abbr : g.away.abbr, 6)}` : ''}</span>}
      <em>›</em>
    </button></li>
  )
}

function Performance({ settled, unit, now }: { settled: LedgerPosition[]; unit: number; now: number }) {
  const [range, setRange] = useState<'month' | 'season' | 'all'>('month')
  const today = operationalToday(new Date(now))
  const since = range === 'month' ? `${today.slice(0, 7)}-01` : range === 'season' ? `${Number(today.slice(5, 7)) >= 8 ? today.slice(0, 4) : String(Number(today.slice(0, 4)) - 1)}-08-01` : ''
  const rows = settled.filter((p) => !since || (p.settled_at ?? '') >= since)
  const w = rows.filter((p) => resultOf(p) === 'WIN').length; const l = rows.filter((p) => resultOf(p) === 'LOSS').length
  const pl = rows.reduce((t, p) => t + (p.profit_loss ?? 0), 0); const staked = rows.reduce((t, p) => t + (p.stake ?? 0), 0)
  let streak = 0
  for (let i = rows.length - 1; i >= 0; i -= 1) { const r = resultOf(rows[i]); if (r === 'PUSH') continue; const win = r === 'WIN'; if (!streak) streak = win ? 1 : -1; else if (win === streak > 0) streak += Math.sign(streak); else break }
  const series: number[] = []; let acc = 0
  for (const p of rows) { acc += (p.profit_loss ?? 0) / unit; series.push(acc) }
  const W = 300; const H = 96; const mn = Math.min(0, ...series); const mx = Math.max(0, ...series)
  const X = (i: number) => (series.length > 1 ? (i / (series.length - 1)) * W : 0)
  const Y = (v: number) => 8 + (1 - (v - mn) / (mx - mn || 1)) * (H - 18)
  const first = rows[0]?.settled_at; const lastAt = rows[rows.length - 1]?.settled_at
  return (
    <section className="sl-card hm-perf" aria-label="Your performance">
      <header className="sl-card__head"><h2>Your Performance</h2><Link to="/experiment" className="hm-all">View Details ›</Link></header>
      <span className="hm-tabs hm-tabs--fill">{(['month', 'season', 'all'] as const).map((r) => <button key={r} type="button" className={range === r ? 'is-on' : ''} onClick={() => setRange(r)}>{r === 'month' ? 'This Month' : r === 'season' ? 'This Season' : 'All Time'}</button>)}</span>
      <div className="hm-kpis">
        <span><small>Record</small><b>{w} - {l}</b><em>({w + l ? ((w / (w + l)) * 100).toFixed(1) : '0.0'}%)</em></span>
        <span><small>ROI</small><b className={pl >= 0 ? 'is-up' : 'is-down'}>{staked ? sgn((pl / staked) * 100, 1, '%') : '—'}</b></span>
        <span><small>Units</small><b className={pl >= 0 ? 'is-up' : 'is-down'}>{sgn(pl / unit, 1)}</b></span>
        <span><small>Streak</small><b className={streak >= 0 ? 'is-up' : 'is-down'}>{streak ? `${streak > 0 ? 'W' : 'L'} ${Math.abs(streak)}` : '—'}</b></span>
      </div>
      {series.length > 1 ? (
        <svg className="hm-perf__chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Units over the period">
          <defs><linearGradient id="hm-pg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#34d399" stopOpacity=".35" /><stop offset="1" stopColor="#34d399" stopOpacity="0" /></linearGradient></defs>
          <line x1="0" x2={W} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.18)" strokeDasharray="3 4" />
          <polygon points={`0,${Y(0)} ${series.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')} ${W},${Y(0)}`} fill="url(#hm-pg)" />
          <polyline points={series.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')} fill="none" stroke={pl >= 0 ? '#34d399' : '#f87171'} strokeWidth="2" />
          <circle cx={W} cy={Y(series[series.length - 1])} r="3" fill="#34d399" />
        </svg>
      ) : <p className="sl-empty">No settled bets in this period.</p>}
      {series.length > 1 ? <div className="hm-perf__axis"><small>{etClock(first, { month: 'short', day: 'numeric' })}</small><b className={pl >= 0 ? 'is-up' : 'is-down'}>{sgn(pl / unit, 1)}u</b><small>{etClock(lastAt, { month: 'short', day: 'numeric' })}</small></div> : null}
    </section>
  )
}

function Insights({ games, picks, settled, now, onOpen }: { games: PulseGame[]; picks: Map<string, Pick | null>; settled: LedgerPosition[]; now: number; onOpen: (g: PulseGame) => void }) {
  const items: Array<{ tone: string; icon: string; text: ReactNode; sub: string; g?: PulseGame }> = []
  const best = games.filter((g) => g.phase !== 'FINAL').map((g) => ({ g, p: picks.get(g.id) })).filter((x) => x.p?.edge != null).sort((a, b) => (b.p!.edge ?? 0) - (a.p!.edge ?? 0))[0]
  if (best) items.push({ tone: 'green', icon: '↗', text: <><b>{short(sideOf(best.g, best.p!.side).abbr, 14)}</b> is today&apos;s biggest edge: model {pct(best.p!.model)} vs market {pct(best.p!.market)}</>, sub: `${best.g.away.abbr} @ ${best.g.home.abbr} · ${etClock(best.g.startsAt)} ET`, g: best.g })
  const upset = games.filter((g) => isLive(g) && g.score && g.modelHome != null).map((g) => { const favHome = g.modelHome! >= 0.5; return { g, favHome, lead: (g.score!.home - g.score!.away) * (favHome ? 1 : -1) } }).sort((a, b) => a.lead - b.lead)[0]
  if (upset && upset.lead < 0) items.push({ tone: 'orange', icon: '▶', text: <>Upset watch: <b>{short(upset.favHome ? upset.g.home.abbr : upset.g.away.abbr, 14)}</b> ({pct(Math.max(upset.g.modelHome!, 1 - upset.g.modelHome!))} pregame) trails by {Math.abs(upset.lead)}</>, sub: upset.g.score!.detail || 'live', g: upset.g })
  const soon = games.filter((g) => g.phase === 'UPCOMING' && Date.parse(g.startsAt) > now && Date.parse(g.startsAt) - now < 3600_000)
  if (soon.length) items.push({ tone: 'violet', icon: '◷', text: <><b>{soon.length}</b> game{soon.length > 1 ? 's' : ''} start in the next hour</>, sub: [...new Set(soon.map((g) => g.sport))].join(', ') })
  const week = settled.filter((p) => (p.settled_at ?? '') >= new Date(now - 7 * 86_400_000).toISOString())
  if (week.length) { const w = week.filter((p) => resultOf(p) === 'WIN').length; const l = week.filter((p) => resultOf(p) === 'LOSS').length; const pl = week.reduce((t, p) => t + (p.profit_loss ?? 0), 0); items.push({ tone: pl >= 0 ? 'teal' : 'red', icon: pl >= 0 ? '▲' : '!', text: <>Last 7 days: <b>{w}-{l}</b>, <span className={pl >= 0 ? 'is-up' : 'is-down'}>{pl >= 0 ? '+' : '-'}{usd(Math.abs(pl))}</span> on the paper book</>, sub: `${week.length} settled bets` }) }
  if (!items.length) items.push({ tone: 'blue', icon: '✦', text: 'Insights appear as forecasts, prices and scores arrive.', sub: '' })
  return <ul className="hm-ins">{items.slice(0, 4).map((it, i) => <li key={i}><button type="button" onClick={() => it.g && onOpen(it.g)} disabled={!it.g}><i className={`tone-${it.tone}`}>{it.icon}</i><span><p>{it.text}</p><small>{it.sub}</small></span></button></li>)}</ul>
}
