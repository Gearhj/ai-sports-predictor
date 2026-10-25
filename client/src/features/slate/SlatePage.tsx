/**
 * The Games page: THE SLATE (owner's design, 2026-10-24, third pass). Every game of the day in one place:
 * six headline tiles (live games, next model run, open wagers, stake at risk, bankroll, strongest edge),
 * filters, a NEXT UP strip, then each sport's games as broadcast rows (live win-probability line for live
 * football, moneyline prices, market vs model, model edge, actions), with My Active Wagers and Slate
 * Insights alongside.
 *
 * Every number comes from AISP's read models (coverage, decisions, ledger, scoreboard); the live win
 * probability line comes from ESPN through the test-mode preview feed. Spreads and totals are not collected,
 * so they are not shown. Picks, bets and edges are the paper experiment's, never advice or a wager signal.
 */
import { AispSide } from '../flow/AispSide'
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
import { useMissionControl, type MissionGame } from '../mission/missionData'
import { usePreviewPicks, removePick } from '../../services/previewPicks'
import './slate.css'

type Sport = 'ALL' | SportCode
type Tab = 'all' | 'live' | 'soon' | 'bets' | 'edges' | 'done'
const SPORTS: Array<{ key: SportCode; label: string; long: string; color: string; icon: string }> = [
  { key: 'NFL', label: 'NFL', long: 'NFL', color: '#6ea4ff', icon: '🏈' }, { key: 'CFB', label: 'CFB', long: 'College Football', color: '#b88cff', icon: '🏈' },
  { key: 'NBA', label: 'NBA', long: 'NBA', color: '#ffb14a', icon: '🏀' }, { key: 'NHL', label: 'NHL', long: 'NHL', color: '#7cc4e4', icon: '🏒' },
  { key: 'MLB', label: 'MLB', long: 'MLB', color: '#ff6b57', icon: '⚾' },
]
const NAV = [
  { to: '/', label: 'Home', icon: '⌂' }, { to: '/live', label: 'Live View', icon: '◉' }, { to: '/games', label: 'Games', icon: '▦', on: true }, { to: '/my-picks', label: 'My Picks', icon: '✓' },
  { to: '/experiment', label: 'Bets', icon: '▤' }, { to: '/performance', label: 'Analytics', icon: '▥' }, { to: '/research/history', label: 'Research', icon: '◎' }, { to: '/a-house-divided', label: 'Leaderboard', icon: '♛' }, { to: '/markets', label: 'Community', icon: '☺' },
]
const isLive = (g: PulseGame) => g.phase === 'LIVE' || g.phase === 'STARTED'
const isFootball = (g: PulseGame) => g.sport === 'NFL' || g.sport === 'CFB'
const sideOf = (g: PulseGame, side: 'HOME' | 'AWAY') => (side === 'HOME' ? g.home : g.away)
const hueOf = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7)
function teamColor(sport: SportCode, abbr: string): string {
  if (sport !== 'CFB') { const id = teamIdentity(sport, abbr); if (id.known) return id.primary }
  return cfbColor(abbr) ?? `hsl(${hueOf(abbr)} 60% 55%)`
}
const shortName = (abbr: string, n = 14) => (abbr.length <= n ? abbr : abbr.split(/\s+/).map((w, i, a) => (i === a.length - 1 ? w : `${w[0]}.`)).join(' '))
const fair = (p: number | null) => (p == null || p <= 0 || p >= 1 ? null : p >= 0.5 ? -Math.round((100 * p) / (1 - p)) : Math.round((100 * (1 - p)) / p))
const signed = (v: number | null, d = 1, suffix = '') => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}${suffix}`)

type Pick = { side: 'HOME' | 'AWAY'; model: number; market: number | null; edge: number | null; price: number | null; source: 'position' | 'decision' | 'lean' | 'model' }
function pickOf(g: PulseGame): Pick | null {
  const fromSel = (sel: string | null | undefined): 'HOME' | 'AWAY' | null => {
    const s = (sel ?? '').toUpperCase()
    if (!s) return null
    if (s.includes(g.home.abbr.toUpperCase()) || s.includes('HOME')) return 'HOME'
    if (s.includes(g.away.abbr.toUpperCase()) || s.includes('AWAY')) return 'AWAY'
    return null
  }
  const side = (g.position && fromSel(g.position.selection)) || (g.decision && fromSel(g.decision.selection)) || g.lean?.side || g.favorite?.side || null
  if (!side || g.modelHome == null) return null
  const model = side === 'HOME' ? g.modelHome : 1 - g.modelHome
  const market = g.marketHome == null ? null : side === 'HOME' ? g.marketHome : 1 - g.marketHome
  const best = side === 'HOME' ? g.bestHome : g.bestAway
  return { side, model, market: g.marketFresh ? market : null, edge: g.marketFresh && market != null ? (model - market) * 100 : null, price: g.position?.entry.american ?? g.decision?.american ?? best?.american ?? null, source: g.position ? 'position' : g.decision && /QUALIFIED|BET/i.test(g.decision.recommendation) ? 'decision' : g.lean ? 'lean' : 'model' }
}
const tierOf = (e: number | null) => (e == null ? null : e >= 10 ? { label: 'VERY STRONG', cls: 'vstrong' } : e >= 6 ? { label: 'STRONG', cls: 'strong' } : e >= 3 ? { label: 'MODERATE', cls: 'moderate' } : e > 0 ? { label: 'LEAN', cls: 'lean' } : { label: 'MARKET HIGHER', cls: 'none' })

/** Live win probability for a live football game, thinned, from the test-mode preview feed. */
type LiveWp = { home: number[]; status: string | null; situation: string | null }
function useLiveWp(g: PulseGame, enabled: boolean): LiveWp | null {
  const [data, setData] = useState<LiveWp | null>(null)
  useEffect(() => {
    if (!enabled) return undefined
    let alive = true
    const league = g.sport === 'CFB' ? 'college-football' : 'nfl'
    const q = new URLSearchParams({ league, date: operationalToday(new Date(g.startsAt)).replace(/-/g, ''), home: g.home.abbr, away: g.away.abbr, home_name: g.home.name, away_name: g.away.name, lite: '1' })
    const load = () => fetch(`/preview-api/football/live?${q.toString()}`, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : null))
      .then((b) => { if (alive && b?.found) setData({ home: (b.win_probability ?? []).map((w: { home: number }) => w.home).filter((v: unknown) => typeof v === 'number'), status: b.status?.detail ?? null, situation: b.situation?.text ? `${b.situation.text}${b.situation.possessionText ? ` · ${b.situation.possessionText}` : ''}` : null }) })
      .catch(() => undefined)
    void load()
    const t = window.setInterval(load, 30_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [enabled, g.id]) // eslint-disable-line react-hooks/exhaustive-deps
  return data
}

export function SlatePage() {
  const [params, setParams] = useSearchParams()
  const today = operationalToday()
  const day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('day') ?? '') ? (params.get('day') as string) : today
  const pulse = usePulse(day)
  const [sport, setSport] = useState<Sport>('ALL')
  const [tab, setTab] = useState<Tab>('all')
  const [sort, setSort] = useState<'time' | 'edge'>('time')
  const [view, setView] = useState<'cards' | 'table'>('cards')
  const [q, setQ] = useState('')
  const [wtab, setWtab] = useState<'active' | 'live' | 'upcoming'>('active')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const navigate = useNavigate()
  const mission = useMissionControl(day)
  const missionById = useMemo(() => new Map((mission.data?.games ?? []).map((m) => [m.game_id, m])), [mission.data])
  const slipPicks = usePreviewPicks()
  const onSlip = useMemo(() => new Set(slipPicks.map((p) => p.game_id)), [slipPicks])
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(t) }, [])

  const dayGames = useMemo(() => pulse.games.filter((g) => g.day === day), [pulse.games, day])
  const live = useMemo(() => dayGames.filter(isLive), [dayGames])
  const positions = useMemo(() => allPositions(pulse.ledger), [pulse.ledger])
  const gameById = useMemo(() => new Map(pulse.games.map((g) => [g.id, g])), [pulse.games])
  const open = useMemo(() => positions.filter((p) => resultOf(p) === 'OPEN').sort((a, b) => Date.parse(a.starts_at ?? '') - Date.parse(b.starts_at ?? '')), [positions])
  const settled = useMemo(() => positions.filter((p) => resultOf(p) !== 'OPEN' && p.settled_at).sort((a, b) => Date.parse(a.settled_at!) - Date.parse(b.settled_at!)), [positions])
  const picks = useMemo(() => new Map(dayGames.map((g) => [g.id, pickOf(g)])), [dayGames])
  const matches = (g: PulseGame) => (sport === 'ALL' || g.sport === sport) && (!q.trim() || `${g.away.abbr} ${g.away.name} ${g.home.abbr} ${g.home.name}`.toLowerCase().includes(q.trim().toLowerCase()))
  const inTab = (g: PulseGame) => {
    const p = picks.get(g.id)
    switch (tab) {
      case 'live': return isLive(g)
      case 'soon': return g.phase === 'UPCOMING' && Date.parse(g.startsAt) - now < 3 * 3600_000
      case 'bets': return Boolean(g.position)
      case 'edges': return (p?.edge ?? -1) >= 3 && g.phase !== 'FINAL'
      case 'done': return g.phase === 'FINAL'
      default: return true
    }
  }
  const tabCount = (t: Tab) => dayGames.filter((g) => (sport === 'ALL' || g.sport === sport) && (() => { const prev = tab; void prev; return true })() && (t === 'all' ? true : t === 'live' ? isLive(g) : t === 'soon' ? g.phase === 'UPCOMING' && Date.parse(g.startsAt) - now < 3 * 3600_000 : t === 'bets' ? Boolean(g.position) : t === 'edges' ? (picks.get(g.id)?.edge ?? -1) >= 3 && g.phase !== 'FINAL' : g.phase === 'FINAL')).length
  const shown = useMemo(() => dayGames.filter((g) => matches(g) && inTab(g)).sort((a, b) => (sort === 'edge' || tab === 'edges' ? (picks.get(b.id)?.edge ?? -99) - (picks.get(a.id)?.edge ?? -99) : 0) || (Number(isLive(b)) - Number(isLive(a))) || Date.parse(a.startsAt) - Date.parse(b.startsAt)), [dayGames, sport, q, tab, sort, picks, now]) // eslint-disable-line react-hooks/exhaustive-deps
  const counts = useMemo(() => { const m = new Map<string, number>(); for (const g of dayGames) m.set(g.sport, (m.get(g.sport) ?? 0) + 1); return m }, [dayGames])

  // headline tiles
  const nextRun = dayGames.concat(pulse.games.filter((g) => g.day !== day)).filter((g) => g.phase === 'UPCOMING' && g.windowOpensAt && Date.parse(g.windowOpensAt) > now).sort((a, b) => Date.parse(a.windowOpensAt!) - Date.parse(b.windowOpensAt!))[0] ?? null
  const mins = nextRun ? Math.round((Date.parse(nextRun.windowOpensAt!) - now) / 60_000) : null
  const stake = open.reduce((t, p) => t + (p.stake ?? 0), 0)
  const payout = open.reduce((t, p) => t + (p.stake ?? 0) * (p.entry.decimal ?? 1), 0)
  const bankroll = pulse.ledger?.totals.realized_bankroll ?? null
  const monthStart = `${day.slice(0, 7)}-01`
  const monthPl = settled.filter((p) => (p.settled_at ?? '') >= monthStart).reduce((t, p) => t + (p.profit_loss ?? 0), 0)
  const strongest = [...dayGames].filter((g) => g.phase !== 'FINAL').map((g) => ({ g, p: picks.get(g.id) })).filter((x) => x.p?.edge != null).sort((a, b) => (b.p!.edge ?? 0) - (a.p!.edge ?? 0))[0] ?? null
  const openLive = open.filter((p) => { const g = gameById.get(p.game_id); return g ? isLive(g) : false })
  const nextUp = pulse.games.filter((g) => g.phase === 'UPCOMING' && Date.parse(g.startsAt) > now - 60_000 && Date.parse(g.startsAt) - now < 6 * 3600_000).sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt))
  const nextRef = useRef<HTMLDivElement | null>(null)
  const openGame = (g: PulseGame) => navigate(gameHref(g.sport, g.id))
  const wagers = (wtab === 'live' ? openLive : wtab === 'upcoming' ? open.filter((p) => !openLive.includes(p)) : open)

  return (
    <div className="hq">
      <AispSide>
        <small className="hq-side__label">SPORTS</small>
        <nav className="hq-nav hq-nav--sports">
          {SPORTS.map((s) => <button key={s.key} type="button" className={sport === s.key ? 'is-on' : ''} onClick={() => setSport(sport === s.key ? 'ALL' : s.key)}><i aria-hidden="true">{s.icon}</i>{s.label}<em>{counts.get(s.key) ?? 0}</em></button>)}
          <Link to="/mma" className="hq-nav__mma" title="UFC Fight Night: historical archive, test mode"><i aria-hidden="true">🥊</i>MMA · UFC</Link>
          <button type="button" disabled title="Not covered by AISP yet"><i aria-hidden="true">🥊</i>Boxing</button>
        </nav>
      </AispSide>
      <div className="hq-main">
        <header className="sl-top">
          <div className="sl-title"><small>Games</small><h1>The Slate</h1><p>All games. Model forecasts. Real-time lines. Your edge.</p></div>
          <label className="hq-search"><span className="lf-sr">Search</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams, players, games, or ask a question…" /></label>
          <span className="hq-date">
            <button type="button" aria-label="Previous day" onClick={() => setParams({ day: shiftDate(day, -1) })}>‹</button>
            <b>📅 {new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(`${day}T12:00:00Z`))}</b>
            <button type="button" aria-label="Next day" onClick={() => setParams({ day: shiftDate(day, 1) })}>›</button>
          </span>
          <select className="hq-select" value={sport} onChange={(e) => setSport(e.target.value as Sport)} aria-label="Sport"><option value="ALL">All Sports</option>{SPORTS.map((s) => <option key={s.key} value={s.key}>{s.long}</option>)}</select>
          <Link to="/mission-control" className="hq-bell" title="Alerts and pipeline health">🔔{live.length ? <i>{live.length}</i> : null}</Link>
          <Link to="/account" className="hq-avatar">JG</Link>
        </header>

        <GamePlanBar current="edges" />
        <div className="sl-layout">
          <main className="sl-content">
            <section className="sl-kpis" aria-label="Today at a glance">
              <Kpi tone="green" icon="((•))" big={String(live.length)} label="Live Games" sub="Across all sports" onClick={() => setTab('live')} />
              <Kpi tone="blue" icon="◷" big={mins == null ? '—' : mins >= 120 ? `${Math.round(mins / 60)} hr` : `${mins} min`} label="Next Model Run" sub={nextRun ? `${new Date(nextRun.windowOpensAt!).toDateString() === new Date(now).toDateString() ? 'Today' : etClock(nextRun.windowOpensAt, { weekday: 'short' })} ${etClock(nextRun.windowOpensAt)} ET` : 'No window scheduled'} />
              <Kpi tone="violet" icon="▤" big={String(open.length)} label="Open Wagers" sub={`${openLive.length} live · ${open.length - openLive.length} upcoming`} onClick={() => setTab('bets')} />
              <Kpi tone="gold" icon="$" big={usd(stake)} label="Stake at Risk" sub={`Potential payout ${usd(payout)}`} />
              <Kpi tone="teal" icon="▥" big={bankroll == null ? '—' : usd(bankroll)} label="Bankroll" sub={bankroll ? <span className={monthPl >= 0 ? 'is-up' : 'is-down'}>{signed((monthPl / Math.max(1, bankroll - monthPl)) * 100, 1, '%')} this month</span> : 'paper experiment'} />
              <Kpi tone="orange" icon="⚡" big={strongest ? `${shortName(sideOf(strongest.g, strongest.p!.side).abbr, 10)} ${strongest.p!.price != null ? american(strongest.p!.price) : ''}` : '—'} label="Strongest Edge Today" sub={strongest ? <span className="is-up">{signed(strongest.p!.edge, 1, '%')}</span> : 'needs a fresh price'} onClick={strongest ? () => openGame(strongest.g) : undefined} />
            </section>

            <section className="sl-bar" aria-label="Filters">
              <span className="sl-tabs" role="tablist">
                {([['all', 'All Games'], ['live', 'Live'], ['soon', 'Starting Soon'], ['bets', 'My Bets'], ['edges', 'Biggest Edges'], ['done', 'Completed']] as Array<[Tab, string]>).map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l} <em>({tabCount(k)})</em></button>)}
              </span>
              <label className="sl-sort">Sort by <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}><option value="time">Start Time</option><option value="edge">Biggest Edge</option></select></label>
              <span className="sl-view"><button type="button" className={view === 'cards' ? 'is-on' : ''} onClick={() => setView('cards')}>▦ Cards</button><button type="button" className={view === 'table' ? 'is-on' : ''} onClick={() => setView('table')}>☰ Table</button></span>
            </section>

            {nextUp.length ? (
              <section className="sl-next" aria-label="Next up">
                <header><h2>Next Up</h2><small>{nextUp.length} games in the next 6 hours</small></header>
                <div className="sl-next__rail" ref={nextRef}>
                  {nextUp.slice(0, 14).map((g) => (
                    <button key={g.id} type="button" className="sl-nx" onClick={() => openGame(g)} style={{ ['--sc' as string]: SPORTS.find((s) => s.key === g.sport)?.color } as CSSProperties}>
                      <span className="sl-nx__top"><b>{etClock(g.startsAt)}</b><em>{g.sport}</em></span>
                      <span className="sl-nx__teams"><Crest sport={g.sport} abbr={g.away.abbr} size={26} /><small>vs</small><Crest sport={g.sport} abbr={g.home.abbr} size={26} /></span>
                      <span className="sl-nx__abbr">{shortName(g.away.abbr, 8)} · {shortName(g.home.abbr, 8)}</span>
                    </button>
                  ))}
                </div>
                <button type="button" className="sl-next__arrow" aria-label="More" onClick={() => nextRef.current?.scrollBy({ left: 500, behavior: 'smooth' })}>›</button>
              </section>
            ) : null}

            {SPORTS.filter((s) => sport === 'ALL' || s.key === sport).map((s) => {
              const list = shown.filter((g) => g.sport === s.key)
              if (!list.length) return null
              const all = Boolean(expanded[s.key]) || sport !== 'ALL'
              const nLive = list.filter(isLive).length
              return (
                <section key={s.key} className={`sl-sport${view === 'table' ? ' is-table' : ''}`} style={{ ['--sc' as string]: s.color } as CSSProperties} aria-label={s.long}>
                  <header className="sl-sport__head"><i aria-hidden="true">{s.icon}</i><h2>{s.long}</h2><small>{list.length} games{nLive ? ` · ${nLive} live` : ''}</small><button type="button" onClick={() => (sport === 'ALL' ? setSport(s.key) : setSport('ALL'))}>{sport === 'ALL' ? `View all ${s.label} games ›` : '‹ All sports'}</button></header>
                  {view === 'table' ? <div className="sl-thead"><span>Time</span><span>Matchup</span><span>Win probability</span><span>Moneyline</span><span>Market · Model</span><span>Model edge</span><span /></div> : null}
                  {(all ? list : list.slice(0, 5)).map((g) => <SlateRow key={g.id} g={g} pick={picks.get(g.id) ?? null} onOpen={() => openGame(g)} onLive={() => navigate(`/live?game=${g.id}`)} dense={view === 'table'} mission={missionById.get(g.id) ?? null} slipped={onSlip.has(g.id)} />)}
                  {!all && list.length > 5 ? <button type="button" className="sl-more" onClick={() => setExpanded((e) => ({ ...e, [s.key]: true }))}>Show all {list.length} {s.label} games</button> : null}
                </section>
              )
            })}
            {!pulse.loading && !shown.length ? <p className="sl-empty">No games match these filters.</p> : null}
            {pulse.loading && !dayGames.length ? <p className="sl-empty">Loading the slate…</p> : null}
            <footer className="hq-foot">Research and paper only. Forecasts, prices, positions and scores come from AISP's read models; live win probability is ESPN's (test-mode feed). Spreads and totals are not collected. Picks, bets and edges are the paper experiment's, never advice or a wager signal.</footer>
          </main>

          <aside className="sl-rail" aria-label="Your wagers and insights">
            <section className="sl-card">
              <header className="sl-card__head"><h2>My Active Wagers</h2><Link to="/experiment">View All ›</Link></header>
              <div className="sl-mini">
                <span className="tone-violet"><i>▤</i><b>{open.length}</b><small>Open Wagers</small></span>
                <span className="tone-gold"><i>$</i><b>{usd(stake)}</b><small>Stake at Risk</small></span>
                <span className="tone-green"><i>↗</i><b>{usd(payout)}</b><small>Potential Payout</small></span>
                <span className="tone-teal"><i>▥</i><b>{bankroll == null ? '—' : usd(bankroll)}</b><small>Bankroll</small></span>
              </div>
              <span className="sl-wtabs">{([['active', `Active (${open.length})`], ['live', `Live (${openLive.length})`], ['upcoming', `Upcoming (${open.length - openLive.length})`]] as const).map(([k, l]) => <button key={k} type="button" className={wtab === k ? 'is-on' : ''} onClick={() => setWtab(k)}>{l}</button>)}</span>
              <ul className="sl-wagers">
                {wagers.slice(0, 7).map((p) => <Wager key={p.position_id} p={p} g={gameById.get(p.game_id) ?? null} onOpen={(g) => openGame(g)} />)}
                {!wagers.length ? <li className="sl-empty">No {wtab === 'active' ? 'open' : wtab} paper wagers.</li> : null}
              </ul>
            </section>
            <section className="sl-card">
              <header className="sl-card__head"><h2>💡 Slate Insights</h2><Link to="/mission-control">View All ›</Link></header>
              <Insights games={dayGames} picks={picks} settled={settled} open={open} now={now} onOpen={openGame} />
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- pieces --

function Kpi({ tone, icon, big, label, sub, onClick }: { tone: string; icon: string; big: string; label: string; sub: ReactNode; onClick?: () => void }) {
  const body = <><i className="sl-kpi__icon" aria-hidden="true">{icon}</i><span className="sl-kpi__text"><small>{label}</small><b>{big}</b><em>{sub}</em></span></>
  return onClick ? <button type="button" className={`sl-kpi tone-${tone}`} onClick={onClick}>{body}</button> : <div className={`sl-kpi tone-${tone}`}>{body}</div>
}

function Spark({ series, ca, ch }: { series: number[]; ca: string; ch: string }) {
  const W = 220; const H = 54
  const n = series.length
  const X = (i: number) => (n > 1 ? (i / (n - 1)) * W : W)
  const Y = (v: number) => 4 + (1 - v) * (H - 8)
  const d = series.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')
  const last = series[n - 1]
  const id = `sp${Math.abs(series.reduce((h, v) => h * 31 + Math.round(v * 1000), 7)) % 100000}`
  return (
    <svg className="sl-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={ch} /><stop offset=".5" stopColor="rgba(255,255,255,.35)" /><stop offset="1" stopColor={ca} /></linearGradient></defs>
      <line x1="0" x2={W} y1={Y(0.5)} y2={Y(0.5)} stroke="rgba(255,255,255,.15)" strokeDasharray="3 4" />
      <path d={d} fill="none" stroke={`url(#${id})`} strokeWidth="2.2" strokeLinejoin="round" />
      {n ? <circle cx={X(n - 1)} cy={Y(last)} r="3.5" fill={last >= 0.5 ? ch : ca} stroke="#fff" strokeWidth="1.2" /> : null}
    </svg>
  )
}

function SlateRow({ g, pick, onOpen, onLive, dense, mission, slipped }: { g: PulseGame; pick: Pick | null; onOpen: () => void; onLive: () => void; dense: boolean; mission: MissionGame | null; slipped: boolean }) {
  const live = isLive(g)
  const wp = useLiveWp(g, live && isFootball(g))
  const ca = teamColor(g.sport, g.away.abbr); const ch = teamColor(g.sport, g.home.abbr)
  const homeNow = wp?.home.length ? wp.home[wp.home.length - 1] : g.modelHome
  const leadHome = (homeNow ?? 0.5) >= 0.5
  const t = tierOf(pick?.edge ?? null)
  const mkHome = g.marketFresh ? g.marketHome : null
  return (
    <article className={`sl-row${live ? ' is-live' : ''}${g.position ? ' has-bet' : ''}${dense ? ' is-dense' : ''}`} style={{ ['--ca' as string]: ca, ['--ch' as string]: ch } as CSSProperties}>
      <div className="sl-row__when">
        {live ? <><span className="sl-live">LIVE</span><b>{wp?.status ?? g.score?.detail ?? 'In progress'}</b></> : g.phase === 'FINAL' ? <><span className="sl-final">FINAL</span></> : <><b>{etClock(g.startsAt)}</b><small>{etClock(g.startsAt, { weekday: 'short' }).toUpperCase()}</small></>}
      </div>
      <div className="sl-row__teams">
        {(['AWAY', 'HOME'] as const).map((s) => { const team = sideOf(g, s); return (
          <span key={s} className={pick?.side === s ? 'is-pick' : ''}><Crest sport={g.sport} abbr={team.abbr} size={dense ? 20 : 26} /><b title={team.name}>{shortName(team.name || team.abbr, 22)}</b>{g.score ? <strong>{s === 'AWAY' ? g.score.away : g.score.home}</strong> : null}</span>) })}
        {live && wp?.situation && !dense ? <small className="sl-row__sit">{wp.situation}</small> : null}
      </div>
      <div className="sl-row__wp">
        <small>{wp?.home.length ? 'Live win probability' : 'Model win probability'}</small>
        <div className="sl-row__wpline">
          <b style={{ color: leadHome ? ch : ca }}>{shortName(leadHome ? g.home.abbr : g.away.abbr, 8)} {homeNow == null ? '—' : pct(leadHome ? homeNow : 1 - homeNow)}</b>
          {wp?.home.length && !dense ? <Spark series={wp.home} ca={ca} ch={ch} /> : <span className="sl-split"><u style={{ width: `${(1 - (homeNow ?? 0.5)) * 100}%`, background: ca }} /><u style={{ width: `${(homeNow ?? 0.5) * 100}%`, background: ch }} /></span>}
        </div>
        {!dense && wp?.home.length && g.modelHome != null ? <small className="sl-row__pre">pregame model: {g.away.abbr.length > 10 ? shortName(g.away.abbr, 8) : g.away.abbr} {pct(1 - g.modelHome)} · {shortName(g.home.abbr, 8)} {pct(g.modelHome)}</small> : null}
      </div>
      <div className="sl-row__ml">
        <small>Moneyline</small>
        <span><em>{shortName(g.away.abbr, 6)}</em><b>{g.bestAway?.american != null ? american(g.bestAway.american) : '—'}</b><i>{american(fair(g.modelHome == null ? null : 1 - g.modelHome))}</i></span>
        <span><em>{shortName(g.home.abbr, 6)}</em><b>{g.bestHome?.american != null ? american(g.bestHome.american) : '—'}</b><i>{american(fair(g.modelHome))}</i></span>
        <small className="sl-row__mlkey">Market · Model</small>
      </div>
      <div className="sl-row__mm">
        <small>Market · Model</small>
        <span><em>{shortName(g.away.abbr, 6)}</em><b>{mkHome == null ? '—' : pct(1 - mkHome)}</b><i>{g.modelHome == null ? '—' : pct(1 - g.modelHome)}</i></span>
        <span><em>{shortName(g.home.abbr, 6)}</em><b>{mkHome == null ? '—' : pct(mkHome)}</b><i>{g.modelHome == null ? '—' : pct(g.modelHome)}</i></span>
      </div>
      <div className="sl-row__edge">
        <small>Model edge</small>
        <b className={pick?.edge == null ? '' : pick.edge >= 0 ? 'is-up' : 'is-down'}>{pick?.edge == null ? '—' : signed(pick.edge, 1, '%')}</b>
        {t ? <span className={`sl-chip sl-chip--${t.cls}`}>{t.label}</span> : <span className="sl-chip">MODEL ONLY</span>}
        {pick ? <small className="sl-row__pick">on {shortName(sideOf(g, pick.side).abbr, 10)}</small> : null}
      </div>
      <div className="sl-row__act">
        <button type="button" className="sl-btn sl-btn--main" onClick={onOpen}>Open Game Center →</button>
        {live && isFootball(g) ? <button type="button" className="sl-btn sl-btn--live" onClick={onLive}>▶ Live View</button> : null}
        {g.phase === 'UPCOMING' && pick ? (slipped ? <button type="button" className="sl-btn sl-btn--slipped" onClick={() => removePick(g.id)} title="On your My Picks slip; click to remove">✓ On your slip</button> : <button type="button" className="sl-btn sl-btn--slip" onClick={() => addToSlip(g)} title="Add the model's side to your play-money slip on My Picks">+ Add to slip</button>) : null}
        {g.position ? <span className="sl-bet">OUR BET · {usd(g.position.stake)} {g.position.selection?.split(' ')[0]}</span> : null}
      </div>
      {mission && !dense && g.phase !== 'FINAL' ? <Ready m={mission} /> : null}
    </article>
  )
}

/** Readiness from Mission Control, on the row: forecast, prices, approval and the decision clock. */
function Ready({ m }: { m: MissionGame }) {
  const st = m.stages
  const item = (key: 'forecast' | 'approval' | 'prices' | 'decision', name: string) => {
    const s = st[key]
    const state = s?.state ?? 'UNKNOWN'
    return <span key={key} className={`sl-ready__i is-${state.toLowerCase()}`} title={s?.detail ?? ''}>{state === 'DONE' ? '✓' : state === 'BLOCKED' ? '!' : state === 'WAITING' ? '◷' : state === 'NOT_APPLICABLE' ? '–' : '?'} {name}{s?.label ? <em>{s.label}</em> : null}</span>
  }
  return <div className="sl-ready" aria-label="Readiness">{item('forecast', 'Forecast')}{item('approval', 'Approval')}{item('prices', 'Prices')}{item('decision', 'Decision')}<Link to="/mission-control" className="sl-ready__mc">Mission Control ›</Link></div>
}

function Wager({ p, g, onOpen }: { p: LedgerPosition & { sport: SportCode }; g: PulseGame | null; onOpen: (g: PulseGame) => void }) {
  const live = g ? isLive(g) : false
  const side = g ? (p.selection ?? '').toUpperCase().includes(g.home.abbr.toUpperCase()) ? g.home : g.away : null
  const toWin = (p.stake ?? 0) * ((p.entry.decimal ?? 1) - 1)
  return (
    <li>
      <button type="button" onClick={() => g && onOpen(g)} disabled={!g}>
        <span className="sl-w__logo">{side ? <Crest sport={p.sport} abbr={side.abbr} size={30} /> : null}</span>
        <span className="sl-w__main"><small>{p.sport} · {g ? `${shortName(g.away.abbr, 12)} @ ${shortName(g.home.abbr, 12)}` : p.matchup}</small><b>{p.selection} <span className="is-up">{p.entry.american != null ? american(p.entry.american) : ''}</span></b><em>{usd(p.stake)} stake · <span className="is-up">{usd(toWin)} to win</span></em><small>{etClock(p.starts_at, { weekday: 'short', month: 'short', day: 'numeric' })} · {etClock(p.starts_at)} ET{p.entry.sportsbook ? ` · ${p.entry.sportsbook}` : ''}</small></span>
        <span className={`sl-w__st${live ? ' is-live' : ''}`}>{live ? (g?.score ? `LIVE ${g.score.away}-${g.score.home}` : 'LIVE') : 'Upcoming'}</span>
      </button>
    </li>
  )
}

function Insights({ games, picks, settled, open, now, onOpen }: { games: PulseGame[]; picks: Map<string, Pick | null>; settled: LedgerPosition[]; open: LedgerPosition[]; now: number; onOpen: (g: PulseGame) => void }) {
  const items: Array<{ tone: string; icon: string; text: ReactNode; sub: string; g?: PulseGame }> = []
  const best = games.filter((g) => g.phase !== 'FINAL').map((g) => ({ g, p: picks.get(g.id) })).filter((x) => x.p?.edge != null).sort((a, b) => (b.p!.edge ?? 0) - (a.p!.edge ?? 0))[0]
  if (best) items.push({ tone: 'green', icon: '↗', text: <><b>{shortName(sideOf(best.g, best.p!.side).abbr, 14)} {best.p!.price != null ? american(best.p!.price) : ''}</b> is today&apos;s biggest edge</>, sub: `Model ${pct(best.p!.model)} vs market ${pct(best.p!.market)} (${signed(best.p!.edge, 1, '%')})`, g: best.g })
  const soon = games.filter((g) => g.phase === 'UPCOMING' && Date.parse(g.startsAt) - now < 3600_000 && Date.parse(g.startsAt) > now)
  if (soon.length) items.push({ tone: 'blue', icon: '◷', text: <><b>{soon.length} game{soon.length > 1 ? 's' : ''}</b> starting in the next hour</>, sub: [...new Set(soon.map((g) => g.sport))].join(', ') + ' action coming up' })
  const upset = games.filter((g) => isLive(g) && g.score && g.modelHome != null).map((g) => { const favHome = g.modelHome! >= 0.5; return { g, favHome, lead: (g.score!.home - g.score!.away) * (favHome ? 1 : -1) } }).sort((a, b) => a.lead - b.lead)[0]
  if (upset && upset.lead < 0) items.push({ tone: 'red', icon: '!', text: <>Upset watch: <b>{shortName(upset.favHome ? upset.g.home.abbr : upset.g.away.abbr, 14)}</b> trails by {Math.abs(upset.lead)}</>, sub: `Model favourite at ${pct(Math.max(upset.g.modelHome!, 1 - upset.g.modelHome!))} · ${upset.g.score!.detail || 'live'}`, g: upset.g })
  const week = settled.filter((p) => (p.settled_at ?? '') >= new Date(now - 7 * 86_400_000).toISOString())
  if (week.length) { const w = week.filter((p) => resultOf(p) === 'WIN').length; const l = week.filter((p) => resultOf(p) === 'LOSS').length; const pl = week.reduce((t, p) => t + (p.profit_loss ?? 0), 0); items.push({ tone: pl >= 0 ? 'teal' : 'red', icon: '▥', text: <>Last 7 days: <b>{w}-{l}</b>, <span className={pl >= 0 ? 'is-up' : 'is-down'}>{usd(pl, true)}</span></>, sub: `${week.length} settled paper positions` }) }
  if (open.length) items.push({ tone: 'violet', icon: '▤', text: <><b>{open.length}</b> open paper wagers, {usd(open.reduce((t, p) => t + (p.stake ?? 0), 0))} at risk</>, sub: 'Within the $100 daily allowance' })
  if (!items.length) items.push({ tone: 'blue', icon: '✦', text: 'Insights appear as forecasts, prices and scores arrive.', sub: '' })
  return <ul className="sl-ins">{items.slice(0, 5).map((it, i) => <li key={i}><button type="button" onClick={() => it.g && onOpen(it.g)} disabled={!it.g}><i className={`tone-${it.tone}`}>{it.icon}</i><span><p>{it.text}</p><small>{it.sub}</small></span>{it.g ? <em>›</em> : null}</button></li>)}</ul>
}
