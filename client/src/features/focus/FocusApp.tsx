/**
 * FOCUS MODE (owner 10-27: "a stripped down page that is really just focused on the user's games and wagers …
 * this might be the view that a mobile version would have"). Four screens over the same data the full site reads:
 *
 *   Tonight   every game today as one card: time or live score, AISP's call (gold), the best US-book price, and
 *             your pick (emerald) or a one-tap "+ Pick".
 *   My Picks  your play-money slip: stake, to win, live / won / lost, and the night's total.
 *   Live      the games being played, biggest first, with what rides on each.
 *   Record    AISP's paper record and yours, side by side, never mixed.
 *
 * YOU and AISP stay apart everywhere (emerald vs gold). Nothing here places a bet; prices are what AISP observed.
 * "Full analysis" switches to the full site at that game's page.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Crest } from '../flow/Crest'
import { decimalOf, suggestStake } from '../flow/slip'
import { isUsBook } from '../edge/MarketEdge'
import { matches } from '../flow/Alerts'
import { useEspn } from '../gamenight/SweatMeter'
import { allPositions, american, etClock, pct, resultOf, usd, usePulse, type PulseGame } from '../pulse/pulseData'
import { gameHref, useMarketCoverage } from '../../services/commandCenter'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { addPick, playBankroll, removePick, updatePick, usePreviewPicks, type PreviewPick } from '../../services/previewPicks'
import { ModeToggle } from './ModeToggle'
import { BestCalls, Exposure, Pipeline, UpNext } from './FocusMore'
import { InjuryWatch } from '../lab/RailPanels'
import { ModelRecordCard, useModelTracker } from '../lab/ModelRecord'
import { useMissionControl, type MissionGame } from '../mission/missionData'
import { Countdown, EdgeBar, InjuryBadge, SportChips, TopPlays, TrustStrip, availabilityFor, topPlays, useAvailability, type AvailGame, type Play } from './FocusExtras'
import { setMode } from './mode'
import { InstallApp } from './InstallApp'
import './focus.css'

const SPORT: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
type CovQuote = { market?: string; sportsbook: string; fresh?: boolean; sides?: Record<string, { american?: number | null; decimal?: number | null }> }
type CovRow = { game_id: string; quotes?: CovQuote[] }
type Price = { american: number; book: string } | null
const TABS = [
  { to: '/', label: 'Tonight', icon: '◉' },
  { to: '/my-picks', label: 'My Picks', icon: '✓' },
  { to: '/live', label: 'Live', icon: '⚡' },
  { to: '/experiment', label: 'Record', icon: '▲' },
] as const

function bestUs(row: CovRow | undefined, side: 'HOME' | 'AWAY'): Price {
  let best: { american: number; book: string; dec: number } | null = null
  for (const q of row?.quotes ?? []) {
    const s = q.sides?.[side]
    if (q.market !== 'MONEYLINE' || q.fresh === false || !isUsBook(q.sportsbook) || s?.american == null || !s.decimal) continue
    if (!best || s.decimal > best.dec) best = { american: s.american, book: q.sportsbook, dec: s.decimal }
  }
  return best ? { american: best.american, book: best.book } : null
}
const isLive = (g: PulseGame) => g.phase === 'LIVE' || g.phase === 'STARTED'
const team = (g: PulseGame, side: 'HOME' | 'AWAY') => (side === 'HOME' ? g.home.abbr : g.away.abbr)

/** AISP's call on a game: its bet, its recorded no-bet, or the model's side. */
function aispCall(g: PulseGame): { kind: 'BET' | 'PASS' | 'LEAN' | 'NONE'; side: 'HOME' | 'AWAY' | null; text: string } {
  const sideOf = (sel: string | null | undefined) => { const s = (sel ?? '').toUpperCase(); return s.includes(g.home.abbr.toUpperCase()) || s.includes('HOME') ? 'HOME' : s.includes(g.away.abbr.toUpperCase()) || s.includes('AWAY') ? 'AWAY' : null }
  if (g.position) { const side = sideOf(g.position.selection); return { kind: 'BET', side, text: `AISP BET ${side ? team(g, side) : g.position.selection ?? ''} ${american(g.position.entry?.american ?? null)}`.trim() } }
  if (g.decision) return { kind: 'PASS', side: null, text: /RECORD/i.test(g.decision.recommendation) || /RECORD/i.test(String(g.decision.reason ?? '')) ? 'AISP: recorded, no stake' : 'AISP: no bet' }
  if (g.modelHome != null) { const side = g.modelHome >= 0.5 ? 'HOME' : 'AWAY'; const p = side === 'HOME' ? g.modelHome : 1 - g.modelHome; return { kind: 'LEAN', side, text: `AISP model: ${team(g, side)} ${pct(p)}` } }
  return { kind: 'NONE', side: null, text: 'No AISP forecast yet' }
}
function pickResult(pk: PreviewPick, g: PulseGame | undefined): 'WIN' | 'LOSS' | 'LIVE' | 'PREGAME' {
  if (g?.phase === 'FINAL' && g.score) { const homeWon = g.score.home > g.score.away; return (pk.side === 'HOME') === homeWon ? 'WIN' : 'LOSS' }
  return g && isLive(g) ? 'LIVE' : 'PREGAME'
}

export function FocusApp() {
  const { pathname } = useLocation()
  const today = operationalToday()
  const pulse = usePulse(today)
  const cov = useMarketCoverage(today)
  const picks = usePreviewPicks()
  // Live and final scores from ESPN's public scoreboard (AISP's own scoreboard does not carry every league live).
  // Yesterday's scoreboard too: after midnight ET last night's late games are still being played (or just ended).
  const LGS = ['nfl', 'cfb', 'nba', 'nhl', 'mlb'] as ('nfl' | 'cfb' | 'nba' | 'nhl' | 'mlb')[]
  const espnToday = useEspn(LGS, today)
  const espnPrev = useEspn(LGS, shiftDate(today, -1))
  const games = useMemo(() => pulse.games.filter((g) => g.day === today || isLive(g)).map((g) => {
    const lg = g.sport.toLowerCase() as 'nfl'
    const e = [...(espnToday[lg] ?? []), ...(espnPrev[lg] ?? [])].find((x) => matches(x.home, g.home.abbr, lg) && matches(x.away, g.away.abbr, lg) && x.state !== 'pre')
    if (!e) return g
    return { ...g, phase: e.state === 'post' ? 'FINAL' as const : 'LIVE' as const, score: { away: e.away.score, home: e.home.score, detail: e.state === 'post' ? 'Final' : e.detail } }
  })
    // a game from an earlier day that nobody confirms is still in play is over, not live
    .filter((g) => g.day === today || (g.phase === 'LIVE' && g.score != null) || Date.now() - Date.parse(g.startsAt) < 6 * 3_600_000)
    .sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt)), [pulse.games, today, espnToday, espnPrev])
  const covRows = (cov.data?.games ?? []) as unknown as CovRow[]
  // Today's market feed first; for other days the board's best price when it is a US-regulated book.
  const priceOf = (g: PulseGame, side: 'HOME' | 'AWAY'): Price => {
    const fromCov = bestUs(covRows.find((r) => r.game_id === g.id), side)
    if (fromCov) return fromCov
    const b = side === 'HOME' ? g.bestHome : g.bestAway
    return b?.american != null && b.book && isUsBook(b.book) ? { american: b.american, book: b.book } : null
  }
  const byId = useMemo(() => new Map([...pulse.games, ...games].map((g) => [g.id, g])), [pulse.games, games])
  const tab = TABS.find((t) => (t.to === '/' ? pathname === '/' : pathname.startsWith(t.to)))?.to ?? 'other'
  // Tomorrow's board, so there is always something to pick once tonight's games have all started.
  const tomorrow = useMemo(() => pulse.games.filter((g) => g.day === shiftDate(today, 1) && g.phase === 'UPCOMING').sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt)), [pulse.games, today])
  // Every sport's injury / key-player board (ESPN public feeds via AISP), today and tomorrow.
  const availToday = useAvailability(today)
  const availNext = useAvailability(shiftDate(today, 1))
  const board = useMemo(() => [...(availToday.data?.games ?? []), ...(availNext.data?.games ?? [])], [availToday.data, availNext.data])
  // AISP's pipeline per game (model / lines / decision), so every waiting state says what it waits for and when.
  useModelTracker()
  const mcToday = useMissionControl(today)
  const mcNext = useMissionControl(shiftDate(today, 1))
  const mcById = useMemo(() => new Map<string, MissionGame>([...(mcToday.data?.games ?? []), ...(mcNext.data?.games ?? [])].map((m) => [m.game_id, m])), [mcToday.data, mcNext.data])
  const ctx = { games, picks, byId, priceOf, loading: pulse.loading, tomorrow, board, ledger: pulse.ledger, mcById }
  return (
    <div className="fm">
      <header className="fm-top">
        <Link to="/" className="fm-brand"><b>✺ AISP</b><small>FOCUS</small></Link>
        <nav className="fm-tabs" aria-label="Focus">
          {TABS.map((t) => <Link key={t.to} to={t.to} className={tab === t.to ? 'is-on' : ''}><i>{t.icon}</i>{t.label}{t.to === '/my-picks' && picks.length ? <em>{picks.length}</em> : null}</Link>)}
        </nav>
        <InstallApp />
        <ModeToggle />
      </header>
      <main id="main-content" className="fm-main">
        {(tab === '/' || tab === 'other') && <Tonight {...ctx} />}
        {tab === '/my-picks' && <Picks {...ctx} />}
        {tab === '/live' && <Live {...ctx} />}
        {tab === '/experiment' && <><TrustStrip ledger={pulse.ledger} /><Record ledger={pulse.ledger} picks={picks} byId={byId} /><ModelRecordCard /><BestCalls ledger={pulse.ledger} /></>}
      </main>
      <nav className="fm-bottom" aria-label="Focus tabs">
        {TABS.map((t) => <Link key={t.to} to={t.to} className={tab === t.to ? 'is-on' : ''}><i>{t.icon}</i><span>{t.label}</span></Link>)}
      </nav>
    </div>
  )
}

interface Ctx { games: PulseGame[]; picks: PreviewPick[]; byId: Map<string, PulseGame>; priceOf: (g: PulseGame, side: 'HOME' | 'AWAY') => Price; loading: boolean; tomorrow: PulseGame[]; board: AvailGame[]; ledger: ReturnType<typeof usePulse>['ledger']; mcById: Map<string, MissionGame> }

/** Add a pick at the best US price with a quarter-Kelly play stake (shared by cards and top plays). */
function pickGame(g: PulseGame, side: 'HOME' | 'AWAY', price: Price, p: number | null) {
  if (!price || p == null) return
  addPick({ game_id: g.id, sport: g.sport, matchup: `${g.away.abbr} @ ${g.home.abbr}`, away: g.away.abbr, home: g.home.abbr, side, selection: team(g, side),
    model_home_two_way: g.modelHome, href: gameHref(g.sport, g.id), price_american: price.american, price_book: price.book, price_seen_at: new Date().toISOString(),
    market_prob: g.marketHome == null ? null : side === 'HOME' ? g.marketHome : 1 - g.marketHome, status: 'slip', starts_at: g.startsAt,
    stake: Math.max(1, suggestStake(p, price.american, playBankroll()) || 10) })
}

// ------------------------------------------------------------------ Tonight --
function Tonight({ games: all, picks, priceOf, loading, tomorrow: allTomorrow, board, ledger, mcById }: Ctx) {
  const [sport, setSport] = useState('ALL')
  const games = sport === 'ALL' ? all : all.filter((g) => g.sport === sport)
  const tomorrow = sport === 'ALL' ? allTomorrow : allTomorrow.filter((g) => g.sport === sport)
  const plays = useMemo(() => topPlays([...all, ...allTomorrow]), [all, allTomorrow])
  const onPlay = (p: Play) => pickGame(p.g, p.side, priceOf(p.g, p.side), p.model)
  const live = games.filter(isLive), up = games.filter((g) => g.phase === 'UPCOMING' || g.phase === 'STALE'), done = games.filter((g) => g.phase === 'FINAL')
  const aispBets = games.filter((g) => g.position).length
  const mine = games.filter((g) => picks.some((p) => p.game_id === g.id)).length
  const date = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'long', month: 'long', day: 'numeric' }).format(new Date())
  return (
    <>
      <section className="fm-hero">
        <small>{date}</small>
        <h1>Tonight</h1>
        <div className="fm-hero__stats">
          <span><b>{games.length}</b>games</span>
          <span className="is-live"><b>{live.length}</b>live</span>
          <span className="is-aisp"><b>{aispBets}</b>AISP bets</span>
          <span className="is-you"><b>{mine}</b>your picks</span>
        </div>
      </section>
      <TrustStrip ledger={ledger} />
      <TopPlays plays={plays} board={board} onPick={onPlay} picked={(id) => picks.some((p) => p.game_id === id)} price={(p) => priceOf(p.g, p.side)} />
      <SportChips games={[...all, ...allTomorrow]} value={sport} onChange={setSport} />
      {loading && !games.length ? <p className="fm-empty">Loading tonight’s games…</p> : null}
      {!loading && !games.length ? <p className="fm-empty">No games on today’s board.</p> : null}
      {live.length > 0 && <GameList title="Live now" games={live} picks={picks} priceOf={priceOf} board={board} mcById={mcById} />}
      {up.length > 0 && <GameList title="Coming up" games={up} picks={picks} priceOf={priceOf} board={board} mcById={mcById} />}
      {up.length === 0 && tomorrow.length > 0 && <GameList title="Tomorrow" games={tomorrow} picks={picks} priceOf={priceOf} board={board} mcById={mcById} />}
      {done.length > 0 && <GameList title="Final" games={done} picks={picks} priceOf={priceOf} board={board} mcById={mcById} />}
    </>
  )
}

function GameList({ title, games, picks, priceOf, board, mcById }: { title: string; games: PulseGame[]; picks: PreviewPick[]; priceOf: Ctx['priceOf']; board: AvailGame[]; mcById: Map<string, MissionGame> }) {
  return (
    <section className="fm-list">
      <h2>{title} <small>{games.length}</small></h2>
      <div className="fm-cards">{games.map((g) => <GameCard key={g.id} g={g} pick={picks.find((p) => p.game_id === g.id) ?? null} priceOf={priceOf} avail={availabilityFor(g, board)} mc={mcById.get(g.id)} />)}</div>
    </section>
  )
}

function GameCard({ g, pick, priceOf, avail, mc }: { g: PulseGame; pick: PreviewPick | null; priceOf: Ctx['priceOf']; avail: AvailGame | null; mc?: MissionGame }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const call = aispCall(g)
  const side: 'HOME' | 'AWAY' = pick?.side ?? call.side ?? (g.modelHome != null && g.modelHome < 0.5 ? 'AWAY' : 'HOME')
  const price = priceOf(g, side)
  const p = g.modelHome == null ? null : side === 'HOME' ? g.modelHome : 1 - g.modelHome
  const live = isLive(g), final = g.phase === 'FINAL'
  const result = pick ? pickResult(pick, g) : null
  const canPick = !pick && !live && !final && price != null && p != null
  const add = () => pickGame(g, side, price, p)
  const market = g.marketFresh && g.marketHome != null ? (side === 'HOME' ? g.marketHome : 1 - g.marketHome) : null
  const full = () => { setMode('full'); navigate(live && (g.sport === 'NFL' || g.sport === 'CFB' || g.sport === 'NBA' || g.sport === 'NHL') ? `/live?game=${g.id}` : gameHref(g.sport, g.id)) }
  return (
    <article className={`fm-card${live ? ' is-live' : ''}${pick ? ' has-pick' : ''}${g.position ? ' has-bet' : ''}`} style={{ '--sc': SPORT[g.sport] ?? '#94a3b8' } as CSSProperties}>
      <button type="button" className="fm-card__main" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="fm-card__sport">{g.sport}</span>
        <span className="fm-card__teams">
          <span><Crest sport={g.sport} abbr={g.away.abbr} size={30} /><b>{g.away.abbr}</b>{g.score ? <em>{g.score.away}</em> : null}</span>
          <span><Crest sport={g.sport} abbr={g.home.abbr} size={30} /><b>{g.home.abbr}</b>{g.score ? <em>{g.score.home}</em> : null}</span>
        </span>
        <span className="fm-card__when">{live ? <><i className="fm-dot" />{g.score?.detail || 'LIVE'}</> : final ? 'FINAL' : <>{etClock(g.startsAt)}<Countdown iso={g.startsAt} /></>}</span>
      </button>
      {p != null && !final && <EdgeBar model={p} market={market} team={team(g, side)} />}
      <InjuryBadge a={avail} />
      {!live && !final && <Pipeline mc={mc} />}
      <div className="fm-card__chips">
        {(call.kind !== 'LEAN' || p == null || final) && <span className={`fm-chip fm-chip--aisp is-${call.kind.toLowerCase()}`}>{call.text}</span>}
        {pick ? (
          <span className={`fm-chip fm-chip--you is-${(result ?? 'pregame').toLowerCase()}`}>
            {result === 'WIN' ? '✓ WON' : result === 'LOSS' ? '✕ LOST' : result === 'LIVE' ? '● SWEATING' : 'YOUR PICK'} · {pick.selection} {american(pick.price_american ?? null)}{pick.stake ? ` · ${usd(pick.stake)}` : ''}
          </span>
        ) : canPick ? (
          <button type="button" className="fm-pick" onClick={add}>+ Pick {team(g, side)} {american(price!.american)} <small>{price!.book}</small></button>
        ) : price ? <span className="fm-chip">Best US price {team(g, side)} {american(price.american)}</span>
          : null}
      </div>
      {open && (
        <div className="fm-card__more">
          {p != null && <span>AISP model <b>{team(g, side)} {pct(p)}</b></span>}
          {g.marketHome != null && <span>Market <b>{team(g, side)} {pct(side === 'HOME' ? g.marketHome : 1 - g.marketHome)}</b></span>}
          {price && <span>Best US book <b>{american(price.american)}</b> at {price.book}</span>}
          {avail && Object.values(avail.teams).map((t) => (
            <span key={t.abbreviation} className="fm-keys"><b>{t.abbreviation} key players</b> {t.key_players.length ? t.key_players.map((k) => `${k.name} (${k.role}) ${k.status === 'AVAILABLE' ? '✓' : k.status}`).join(' · ') : 'not known yet'}</span>
          ))}
          {pick && !final && <button type="button" className="fm-link" onClick={() => removePick(g.id)}>Remove my pick</button>}
          <button type="button" className="fm-link" onClick={full}>Full analysis →</button>
        </div>
      )}
    </article>
  )
}

// ----------------------------------------------------------------- My Picks --
function Picks({ picks, byId, games, tomorrow, board, priceOf }: Ctx) {
  const rows = picks.map((pk) => {
    const g = byId.get(pk.game_id)
    const res = pickResult(pk, g)
    const stake = pk.stake ?? 0
    const win = pk.price_american != null ? stake * (decimalOf(pk.price_american) - 1) : 0
    return { pk, g, res, stake, win, pl: res === 'WIN' ? win : res === 'LOSS' ? -stake : 0 }
  })
  const plays = topPlays([...games, ...tomorrow])
  const risk = rows.filter((r) => r.res === 'PREGAME' || r.res === 'LIVE').reduce((t, r) => t + r.stake, 0)
  const toWin = rows.filter((r) => r.res === 'PREGAME' || r.res === 'LIVE').reduce((t, r) => t + r.win, 0)
  const pl = rows.reduce((t, r) => t + r.pl, 0)
  return (
    <>
      <section className="fm-hero fm-hero--you">
        <small>Play money · separate from AISP’s record</small>
        <h1>My Picks</h1>
        <div className="fm-hero__stats">
          <span className="is-you"><b>{rows.length}</b>picks</span>
          <span><b>{usd(risk)}</b>at risk</span>
          <span className="is-you"><b>{usd(toWin)}</b>to win</span>
          <span className={pl >= 0 ? 'is-you' : 'is-loss'}><b>{usd(pl, true)}</b>settled</span>
        </div>
      </section>
      {!rows.length && <p className="fm-empty">No picks yet. Start with AISP’s strongest calls below, or tap <b>+ Pick</b> on any game in <Link to="/">Tonight</Link>. Your picks are play money and never mix with AISP’s record.</p>}
      {!rows.length && <TopPlays plays={plays} board={board} onPick={(p) => pickGame(p.g, p.side, priceOf(p.g, p.side), p.model)} picked={() => false} price={(p) => priceOf(p.g, p.side)} />}
      <Exposure rows={rows.map((r) => ({ sport: r.pk.sport, stake: r.stake, win: r.win, open: r.res === 'PREGAME' || r.res === 'LIVE' }))} />
      <div className="fm-cards">
        {rows.map(({ pk, g, res, stake, win }) => (
          <article key={pk.id} className={`fm-slip is-${res.toLowerCase()}`} style={{ '--sc': SPORT[pk.sport] ?? '#94a3b8' } as CSSProperties}>
            <span className="fm-slip__team"><Crest sport={pk.sport} abbr={pk.selection} size={38} /><span><b>{pk.selection} {american(pk.price_american ?? null)}</b><small>{pk.matchup} · {pk.price_book ?? 'book'}{g ? ` · ${g.phase === 'UPCOMING' ? etClock(g.startsAt) : g.score ? `${g.score.away}–${g.score.home} ${g.phase === 'FINAL' ? 'final' : g.score.detail}` : ''}` : ''}</small></span></span>
            <label className="fm-slip__stake">Stake $<input type="number" min={1} step={1} value={stake || ''} disabled={res === 'WIN' || res === 'LOSS'} onChange={(e) => updatePick(pk.game_id, { stake: Math.max(0, Number(e.target.value) || 0) })} /></label>
            {g && g.modelHome != null && res === 'PREGAME' && (() => {
              const mp = pk.side === 'HOME' ? g.modelHome! : 1 - g.modelHome!
              const mk = g.marketFresh && g.marketHome != null ? (pk.side === 'HOME' ? g.marketHome : 1 - g.marketHome) : null
              const now = priceOf(g, pk.side)
              return <div className="fm-slip__more">
                <EdgeBar model={mp} market={mk} team={pk.selection} />
                <InjuryBadge a={availabilityFor(g, board)} compact />
                {now && pk.price_american != null && now.american !== pk.price_american && <span className={`fm-move ${decimalOf(now.american) > decimalOf(pk.price_american) ? 'is-better' : 'is-worse'}`}>Line now {american(now.american)} at {now.book} (you took {american(pk.price_american)})</span>}
              </div>
            })()}
            <span className="fm-slip__state">{res === 'WIN' ? <b className="is-win">WON {usd(win, true)}</b> : res === 'LOSS' ? <b className="is-loss">LOST {usd(-stake, true)}</b> : res === 'LIVE' ? <b className="is-live">● LIVE · to win {usd(win)}</b> : <b>To win {usd(win)}</b>}</span>
            {res === 'PREGAME' && <button type="button" className="fm-x" aria-label={`Remove ${pk.selection}`} onClick={() => removePick(pk.game_id)}>✕</button>}
          </article>
        ))}
      </div>
      <p className="fm-fine">AISP never places a bet. Prices are the best US-book quotes AISP saw when you picked; place your own bet at your sportsbook.</p>
    </>
  )
}

// --------------------------------------------------------------------- Live --
function Live({ games, picks, mcById }: Ctx) {
  const navigate = useNavigate()
  const live = games.filter(isLive).sort((a, b) => Number(Boolean(b.position) || picks.some((p) => p.game_id === b.id)) - Number(Boolean(a.position) || picks.some((p) => p.game_id === a.id)))
  return (
    <>
      <section className="fm-hero"><small>Scores refresh on their own</small><h1>Live</h1><div className="fm-hero__stats"><span className="is-live"><b>{live.length}</b>in play</span></div></section>
      {!live.length && <p className="fm-empty">Nothing in play yet. The next games, what AISP is waiting on for each, and tonight's injury news are below.</p>}
      {!live.length && <UpNext games={games} mcById={mcById} />}
      {!live.length && <InjuryWatch />}
      <div className="fm-cards">
        {live.map((g) => {
          const pk = picks.find((p) => p.game_id === g.id)
          const call = aispCall(g)
          const lead = g.score ? g.score.home - g.score.away : 0
          return (
            <article key={g.id} className="fm-board" style={{ '--sc': SPORT[g.sport] ?? '#94a3b8' } as CSSProperties}>
              <span className="fm-board__sport">{g.sport} · <i className="fm-dot" />{g.score?.detail || 'in play'}</span>
              <div className="fm-board__score">
                <span className={lead < 0 ? 'is-ahead' : ''}><Crest sport={g.sport} abbr={g.away.abbr} size={44} /><b>{g.away.abbr}</b><em>{g.score?.away ?? '–'}</em></span>
                <span className={lead > 0 ? 'is-ahead' : ''}><em>{g.score?.home ?? '–'}</em><b>{g.home.abbr}</b><Crest sport={g.sport} abbr={g.home.abbr} size={44} /></span>
              </div>
              <div className="fm-card__chips">
                {g.position && <span className="fm-chip fm-chip--aisp is-bet">{call.text}</span>}
                {pk && <span className="fm-chip fm-chip--you is-live">YOU · {pk.selection} {american(pk.price_american ?? null)}</span>}
              </div>
              {(g.sport === 'NFL' || g.sport === 'CFB' || g.sport === 'NBA' || g.sport === 'NHL') && <button type="button" className="fm-link" onClick={() => { setMode('full'); navigate(`/live?game=${g.id}`) }}>Watch play by play →</button>}
            </article>
          )
        })}
      </div>
    </>
  )
}

// ------------------------------------------------------------------- Record --
function Record({ ledger, picks, byId }: { ledger: ReturnType<typeof usePulse>['ledger']; picks: PreviewPick[]; byId: Map<string, PulseGame> }) {
  const pos = allPositions(ledger ?? null)
  const settled = pos.filter((p) => ['WIN', 'LOSS', 'PUSH'].includes(resultOf(p)))
  const w = settled.filter((p) => resultOf(p) === 'WIN').length, l = settled.filter((p) => resultOf(p) === 'LOSS').length
  const pl = settled.reduce((t, p) => t + (p.profit_loss ?? 0), 0)
  const staked = settled.reduce((t, p) => t + (p.stake ?? 0), 0)
  const series = (ledger?.combined_series ?? []).map((p) => p.bankroll)
  const bank = ledger?.totals?.realized_bankroll ?? series[series.length - 1] ?? null
  const mine = picks.map((pk) => ({ pk, res: pickResult(pk, byId.get(pk.game_id)) })).filter((r) => r.res === 'WIN' || r.res === 'LOSS')
  const mw = mine.filter((r) => r.res === 'WIN').length
  const mpl = mine.reduce((t, r) => t + (r.res === 'WIN' ? (r.pk.stake ?? 0) * (decimalOf(r.pk.price_american ?? 100) - 1) : -(r.pk.stake ?? 0)), 0)
  const spark = useMemo(() => {
    if (series.length < 2) return ''
    const lo = Math.min(...series), hi = Math.max(...series), W = 600, H = 110
    return series.map((v, i) => `${i ? 'L' : 'M'}${(i / (series.length - 1)) * W},${H - 6 - ((v - lo) / (hi - lo || 1)) * (H - 12)}`).join('')
  }, [series])
  return (
    <>
      <section className="fm-hero"><small>Paper and play money · never mixed</small><h1>Record</h1></section>
      <div className="fm-record">
        <article className="fm-rec fm-rec--aisp">
          <small>AISP · paper bets</small>
          <b>{w}–{l}</b>
          <span className={pl >= 0 ? 'is-up' : 'is-down'}>{usd(pl, true)} on {usd(staked)}{staked ? ` · ${pct(pl / staked, 1)} ROI` : ''}</span>
          {spark && <svg viewBox="0 0 600 110" className="fm-spark" role="img" aria-label="AISP paper bankroll over the season"><defs><linearGradient id="fm-sp" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#fbbf24" stopOpacity=".45" /><stop offset="1" stopColor="#fbbf24" stopOpacity="0" /></linearGradient></defs><path d={`${spark}L600,110L0,110Z`} fill="url(#fm-sp)" /><path d={spark} fill="none" stroke="#fbbf24" strokeWidth="3" /></svg>}
          <em>Bankroll {bank == null ? '—' : usd(bank)}</em>
        </article>
        <article className="fm-rec fm-rec--you">
          <small>YOU · play money</small>
          <b>{mw}–{mine.length - mw}</b>
          <span className={mpl >= 0 ? 'is-up' : 'is-down'}>{usd(mpl, true)}</span>
          <em>{mine.length ? `${mine.length} settled pick${mine.length > 1 ? 's' : ''}` : 'Make picks on Tonight; they settle here.'}</em>
        </article>
      </div>
    </>
  )
}
