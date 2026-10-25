import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, ChevronRight, Clock3, Flame, Radio, Swords, Trophy, X, Zap } from 'lucide-react'
import { TeamLogo } from '../../components/TeamLogo'
import { useScores } from '../shared/ScoreBug'
import { gameHref, type LedgerPosition, type SportCode } from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { FORECAST_TIMING_SUMMARY, forecastRefreshText } from '../../services/forecastCadence'
import type { Standing } from '../../services/houseDivided'
import {
  ageText, allPositions, american, countdown, dayLabel, etClock, pct, resultOf, usd, usePulse, type PulseGame,
} from './pulseData'
import './pulse.css'

type SportFilter = 'ALL' | SportCode

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t) }, [ms])
  return now
}

const sideAbbr = (g: PulseGame, side: 'HOME' | 'AWAY') => (side === 'HOME' ? g.home.abbr : g.away.abbr)

export function PulseHome() {
  const today = operationalToday()
  const pulse = usePulse(today)
  const now = useNow()
  const [filter, setFilter] = useState<SportFilter>('ALL')
  const [sortChoice, setSort] = useState<'gap' | 'time' | null>(null)
  // The open game lives in the URL (?g=), so a drawer can be shared or reloaded.
  const [params, setParams] = useSearchParams()
  const selected = params.get('g')
  const setSelected = useCallback((id: string | null) => {
    setParams((prev) => { const next = new URLSearchParams(prev); if (id) next.set('g', id); else next.delete('g'); return next }, { replace: true })
  }, [setParams])
  const navigate = useNavigate()

  const positions = useMemo(() => allPositions(pulse.ledger), [pulse.ledger])
  const settled = useMemo(() => positions.filter((p) => resultOf(p) !== 'OPEN' && p.settled_at)
    .sort((a, b) => Date.parse(b.settled_at!) - Date.parse(a.settled_at!)), [positions])
  const open = useMemo(() => positions.filter((p) => resultOf(p) === 'OPEN'), [positions])

  const upcoming = pulse.games.filter((g) => g.phase === 'UPCOMING')
  const live = pulse.games.filter((g) => g.phase === 'LIVE')
  // Compared games (fresh price) lead, by size of disagreement; model-only
  // games follow, by how sure the model is. "Kickoff" orders by start time,
  // and is the default while nothing can be compared yet.
  const anyCompared = upcoming.some((g) => g.lean)
  const sort = sortChoice ?? (anyCompared ? 'gap' : 'time')
  const board = useMemo(() => upcoming
    .filter((g) => g.favorite && (filter === 'ALL' || g.sport === filter))
    .sort((a, b) => sort === 'time'
      ? Date.parse(a.startsAt) - Date.parse(b.startsAt)
      : (Number(Boolean(b.lean)) - Number(Boolean(a.lean)))
        || ((b.lean?.gap ?? 0) - (a.lean?.gap ?? 0))
        || (b.favorite!.prob - a.favorite!.prob)), [upcoming, filter, sort])
  const compared = board.filter((g) => g.lean).length
  const sports = [...new Set(upcoming.filter((g) => g.favorite).map((g) => g.sport))]

  // Keyboard: j/k move through the board, Enter opens the brief, Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input, select, textarea')) return
      const idx = board.findIndex((g) => g.id === selected)
      if (e.key === 'j' || e.key === 'ArrowDown' && selected) { e.preventDefault(); setSelected(board[Math.min(board.length - 1, idx + 1)]?.id ?? null) }
      else if (e.key === 'k' || e.key === 'ArrowUp' && selected) { e.preventDefault(); setSelected(board[Math.max(0, idx - 1)]?.id ?? null) }
      else if (e.key === 'Escape') setSelected(null)
      else if (e.key === 'Enter' && selected) {
        const g = pulse.games.find((x) => x.id === selected); if (g) navigate(gameHref(g.sport, g.id))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [board, selected, pulse.games, navigate])

  const selectedGame = pulse.games.find((g) => g.id === selected) ?? null

  return (
    <div className={`pulse ${selectedGame ? 'pulse--drawer' : ''}`}>
      <Ticker games={pulse.games} today={today} onPick={setSelected} selected={selected} />

      <div className="pulse__grid">
        <section className="pulse__hero" aria-label="Right now">
          <HeroMoment games={pulse.games} live={live} open={open} lastSettled={settled[0] ?? null} now={now} onPick={setSelected} loading={pulse.loading} />
        </section>

        <section className="pulse__bank" aria-label="Bankroll">
          <BankrollPulse ledger={pulse.ledger} settled={settled} today={today} />
        </section>

        <section className="pulse__board" aria-labelledby="mvv-title">
          <header className="pulse__section-head">
            <div>
              <p className="pulse__eyebrow"><Zap size={13} /> Where the model disagrees</p>
              <h2 id="mvv-title">Model vs. Vegas</h2>
            </div>
            <div className="pulse__controls">
              <div className="pulse__chips" role="group" aria-label="Sport">
                {(['ALL', ...sports] as SportFilter[]).map((s) => (
                  <button key={s} aria-pressed={filter === s} className={`pulse-chip ${filter === s ? 'is-on' : ''}`} onClick={() => setFilter(s)}>{s === 'ALL' ? 'All' : s}</button>
                ))}
              </div>
              <div className="pulse__chips" role="group" aria-label="Sort">
                <button aria-pressed={sort === 'gap'} className={`pulse-chip pulse-chip--quiet ${sort === 'gap' ? 'is-on' : ''}`} onClick={() => setSort('gap')}>{anyCompared ? 'Biggest edge' : 'Most confident'}</button>
                <button aria-pressed={sort === 'time'} className={`pulse-chip pulse-chip--quiet ${sort === 'time' ? 'is-on' : ''}`} onClick={() => setSort('time')}>Kickoff</button>
              </div>
            </div>
          </header>
          <p className="pulse__hint">
            {compared
              ? <>Games with a fresh sportsbook price show how far our model is from Vegas. </>
              : <>No game has a fresh sportsbook price yet. Until it does, each card shows the model's own read; the Vegas comparison appears once a current line is collected. </>}
            A lean is not a bet: official picks are made in each game's decision window.
            <span className="kbd-help"> <kbd>j</kbd>/<kbd>k</kbd> to browse, <kbd>Enter</kbd> to open.</span>
          </p>
          {pulse.loading && <div className="pulse-skeleton" style={{ height: 240 }} />}
          {!pulse.loading && board.length === 0 && <p className="pulse__empty">No upcoming game has a forecast yet. Forecast timing: {FORECAST_TIMING_SUMMARY}.</p>}
          <div className="mvv-grid">
            {board.map((g, i) => {
              const slot = sort === 'time' && (i === 0 || board[i - 1].startsAt !== g.startsAt)
              return [
                slot ? <h3 key={`s-${g.startsAt}`} className="mvv-slot">{dayLabel(g.day, today)} · {etClock(g.startsAt)} ET</h3> : null,
                <LeanCard key={g.id} game={g} today={today} active={g.id === selected} onPick={() => setSelected(g.id === selected ? null : g.id)} />,
              ]
            })}
          </div>
          <PendingStrip games={upcoming.filter((g) => !g.favorite && (filter === 'ALL' || g.sport === filter))} today={today} onPick={setSelected} />
        </section>

        <aside className="pulse__side">
          <StandingsCard standings={pulse.standings} />
          <Receipts settled={settled} />
        </aside>
      </div>

      {selectedGame && <GameDrawer game={selectedGame} today={today} onClose={() => setSelected(null)} />}
    </div>
  )
}

// ---------------------------------------------------------------- ticker --

function Ticker({ games, today, onPick, selected }: { games: PulseGame[]; today: string; onPick: (id: string) => void; selected: string | null }) {
  if (!games.length) return <div className="ticker ticker--empty" />
  let lastDay = ''
  return (
    <nav className="ticker" aria-label="Scores and schedule">
      <div className="ticker__track">
        {games.map((g) => {
          const dayBreak = g.day !== lastDay ? (lastDay = g.day, <span key={`d-${g.day}`} className="ticker__day">{dayLabel(g.day, today)}</span>) : null
          const res = g.position ? resultOf(g.position) : null
          return [dayBreak,
            <button key={g.id} className={`tick tick--${g.phase.toLowerCase()} ${selected === g.id ? 'is-on' : ''}`} onClick={() => onPick(g.id)}>
              <span className="tick__meta">{g.phase === 'LIVE' ? <><i className="live-dot" />{g.score?.detail || 'Live'}</> : g.phase === 'FINAL' ? 'Final' : g.phase === 'STALE' ? 'No result' : g.phase === 'STARTED' ? 'In progress' : etClock(g.startsAt)}
                {g.position
                  ? <span className={`tick__bet tick__bet--${(res ?? 'OPEN').toLowerCase()}`}>{res === 'OPEN' ? 'OUR BET' : res === 'WIN' ? usd(g.position.profit_loss, true) : res === 'LOSS' ? usd(g.position.profit_loss, true) : 'PUSH'}</span>
                  : <b>{g.sport}</b>}</span>
              <span className="tick__row"><TeamLogo league={g.sport} abbreviation={g.away.abbr} size={16} /><span>{g.away.abbr}</span><strong>{g.score?.away ?? ''}</strong></span>
              <span className="tick__row"><TeamLogo league={g.sport} abbreviation={g.home.abbr} size={16} /><span>{g.home.abbr}</span><strong>{g.score?.home ?? ''}</strong></span>
            </button>]
        })}
      </div>
    </nav>
  )
}

// ------------------------------------------------------------------ hero --

function HeroMoment({ games, live, open, lastSettled, now, onPick, loading }: {
  games: PulseGame[]; live: PulseGame[]; open: LedgerPosition[]; lastSettled: LedgerPosition | null; now: number; onPick: (id: string) => void; loading: boolean
}) {
  if (loading) return <div className="hero hero--loading"><div className="pulse-skeleton" style={{ height: 180 }} /></div>
  const liveBet = live.find((g) => g.position)
  const nextWindow = games.filter((g) => g.phase === 'UPCOMING' && g.windowOpensAt && Date.parse(g.windowOpensAt) > now)
    .sort((a, b) => Date.parse(a.windowOpensAt!) - Date.parse(b.windowOpensAt!))
  const next = nextWindow[0]
  const cluster = next ? nextWindow.filter((g) => g.windowOpensAt === next.windowOpensAt) : []

  return (
    <div className="hero">
      {liveBet ? (
        <div className="hero__main">
          <p className="hero__kicker"><i className="live-dot" /> Our bet is live</p>
          <LiveScore game={liveBet} />
          <p className="hero__sub">{liveBet.position!.selection} at {american(liveBet.position!.entry.american)} · {usd(liveBet.position!.stake)} to win {usd((liveBet.position!.stake ?? 0) * ((liveBet.position!.entry.decimal ?? 1) - 1))}</p>
        </div>
      ) : live.length ? (
        <div className="hero__main">
          <p className="hero__kicker"><i className="live-dot" /> {live.length} game{live.length > 1 ? 's' : ''} live now</p>
          <LiveScore game={live[0]} />
          <button className="hero__link" onClick={() => onPick(live[0].id)}>Model vs. market for this game <ArrowRight size={14} /></button>
        </div>
      ) : next ? (
        <div className="hero__main">
          <p className="hero__kicker"><Clock3 size={14} /> Next: the model makes its picks</p>
          <div className="hero__count" aria-live="off">{countdown(next.windowOpensAt!, now)}</div>
          <p className="hero__sub">{etClock(next.windowOpensAt, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET · {cluster.length} game{cluster.length > 1 ? 's' : ''} {cluster.length > 1 ? 'are' : 'is'} evaluated against the best available price</p>
          <div className="hero__matchups">
            {cluster.slice(0, 6).map((g) => (
              <button key={g.id} className="hero__matchup" onClick={() => onPick(g.id)}>
                <TeamLogo league={g.sport} abbreviation={g.away.abbr} size={26} />
                <span>{g.away.abbr} <em>@</em> {g.home.abbr}</span>
                <TeamLogo league={g.sport} abbreviation={g.home.abbr} size={26} />
              </button>
            ))}
            {cluster.length > 6 && <span className="hero__more">+{cluster.length - 6} more</span>}
          </div>
        </div>
      ) : (
        <div className="hero__main">
          <p className="hero__kicker"><Clock3 size={14} /> Quiet right now</p>
          <p className="hero__title">No decision window is scheduled in the next few days.</p>
        </div>
      )}

      {open.length > 0 && <RidingNow open={open} games={games} onPick={onPick} />}
      {lastSettled && <LastCall p={lastSettled} />}
    </div>
  )
}

/** NFL and CFB record the side ("HOME"/"AWAY"); show the club instead. */
function pickLabel(p: LedgerPosition, g: PulseGame | undefined): string {
  const side = (p.selection ?? '').toUpperCase()
  if (g && side === 'HOME') return `${g.home.abbr} ML`
  if (g && side === 'AWAY') return `${g.away.abbr} ML`
  return p.selection ?? p.matchup ?? 'Open bet'
}

/** Every open bet at a glance: the pick, the stake, and the game as it stands. */
function RidingNow({ open, games, onPick }: { open: LedgerPosition[]; games: PulseGame[]; onPick: (id: string) => void }) {
  const byId = new Map(games.map((g) => [g.id, g]))
  const rows = open
    .map((p) => ({ p, g: byId.get(p.game_id) }))
    .sort((a, b) => Date.parse(a.g?.startsAt ?? a.p.starts_at ?? '') - Date.parse(b.g?.startsAt ?? b.p.starts_at ?? ''))
  return (
    <div className="riding">
      <p className="riding__head">{open.length} open bet{open.length > 1 ? 's' : ''} riding · {usd(open.reduce((s, p) => s + (p.stake ?? 0), 0))} at risk</p>
      <div className="riding__list">
        {rows.map(({ p, g }) => (
          <button key={p.position_id ?? p.game_id} className={`riding__bet ${g?.phase === 'LIVE' ? 'is-live' : ''}`} onClick={() => onPick(p.game_id)}>
            <span className="riding__pick">{pickLabel(p, g)} <em>{american(p.entry.american)}</em></span>
            {g ? (
              <span className="riding__game">
                <span>{g.away.abbr} <strong>{g.score?.away ?? ''}</strong></span>
                <span>{g.home.abbr} <strong>{g.score?.home ?? ''}</strong></span>
                <small>{g.phase === 'LIVE' ? <><i className="live-dot" />{g.score?.detail || 'Live'}</> : g.phase === 'FINAL' ? 'Final' : g.phase === 'STARTED' ? 'In progress · no live feed' : etClock(g.startsAt)}</small>
              </span>
            ) : <span className="riding__game"><small>{p.matchup}</small></span>}
            <span className="riding__stake">{usd(p.stake)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function LiveScore({ game }: { game: PulseGame }) {
  return (
    <div className="livescore">
      <div className="livescore__team"><TeamLogo league={game.sport} abbreviation={game.away.abbr} size={44} /><span>{game.away.abbr}</span><strong>{game.score?.away ?? 0}</strong></div>
      <span className="livescore__detail">{game.score?.detail}</span>
      <div className="livescore__team"><strong>{game.score?.home ?? 0}</strong><span>{game.home.abbr}</span><TeamLogo league={game.sport} abbreviation={game.home.abbr} size={44} /></div>
    </div>
  )
}

function LastCall({ p }: { p: LedgerPosition & { sport?: SportCode } }) {
  const r = resultOf(p)
  return (
    <Link to={gameHref(p.sport ?? 'MLB', p.game_id)} className={`lastcall lastcall--${r.toLowerCase()}`}>
      <span className="lastcall__stamp">{r === 'WIN' ? 'Cashed' : r === 'LOSS' ? 'Lost' : 'Push'}</span>
      <span className="lastcall__body">
        <small>Last call · {p.matchup} · {etClock(p.starts_at ?? p.settled_at, { weekday: 'short', month: 'short', day: 'numeric' })}</small>
        <strong>{p.selection} {american(p.entry.american)}</strong>
      </span>
      <span className="lastcall__pl">{usd(p.profit_loss, true)}</span>
      <ChevronRight size={16} />
    </Link>
  )
}

// -------------------------------------------------------------- bankroll --

function BankrollPulse({ ledger, settled, today }: { ledger: ReturnType<typeof usePulse>['ledger']; settled: LedgerPosition[]; today: string }) {
  if (!ledger) return <div className="bank"><div className="pulse-skeleton" style={{ height: 200 }} /></div>
  const t = ledger.totals
  const series = ledger.combined_series.filter((p) => p.at)
  const todayPL = settled.filter((p) => p.settled_at && new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(p.settled_at)) === today)
    .reduce((s, p) => s + (p.profit_loss ?? 0), 0)
  const streak = (() => {
    const first = settled[0] ? resultOf(settled[0]) : null
    if (!first || first === 'PUSH') return null
    let n = 0; for (const p of settled) { if (resultOf(p) === first) n++; else break }
    return { kind: first, n }
  })()
  const winPct = t.record.WIN + t.record.LOSS ? t.record.WIN / (t.record.WIN + t.record.LOSS) : null
  return (
    <div className="bank">
      <p className="pulse__eyebrow">Paper bankroll · all sports</p>
      <div className="bank__big">{usd(t.realized_bankroll)}</div>
      <p className={`bank__delta ${t.profit_loss >= 0 ? 'is-up' : 'is-down'}`}>{usd(t.profit_loss, true)} all-time · <span>{usd(todayPL, true)} today</span></p>
      <Sparkline points={series.map((p) => p.bankroll)} base={t.starting_capital} />
      <dl className="bank__stats">
        <div><dt>Record</dt><dd>{t.record.WIN}–{t.record.LOSS}{t.record.PUSH ? `–${t.record.PUSH}` : ''}</dd></div>
        <div><dt>Hit rate</dt><dd>{pct(winPct)}</dd></div>
        <div><dt>Streak</dt><dd>{streak ? <span className={streak.kind === 'WIN' ? 'is-up' : 'is-down'}>{streak.kind === 'WIN' ? <Flame size={13} /> : null}{streak.kind[0]}{streak.n}</span> : '—'}</dd></div>
      </dl>
      <div className="bank__sports">
        {ledger.sports.filter((s) => s.settled_count > 0 || s.open_count > 0).map((s) => (
          <span key={s.sport} className={`bank__sport ${s.profit_loss >= 0 ? 'is-up' : 'is-down'}`}><b>{s.sport}</b>{usd(s.profit_loss, true)}</span>
        ))}
      </div>
      <Link className="pulse__more" to="/experiment">Full record <ArrowRight size={13} /></Link>
    </div>
  )
}

function Sparkline({ points, base }: { points: number[]; base: number }) {
  if (points.length < 2) return null
  const w = 300, h = 64
  const min = Math.min(base, ...points), max = Math.max(base, ...points)
  const y = (v: number) => h - 4 - ((v - min) / (max - min || 1)) * (h - 8)
  const x = (i: number) => (i / (points.length - 1)) * w
  const d = points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const up = points[points.length - 1] >= base
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label="Bankroll over time">
      <line x1="0" x2={w} y1={y(base)} y2={y(base)} className="spark__base" />
      <path d={`${d} L${w},${h} L0,${h} Z`} className={`spark__fill ${up ? 'is-up' : 'is-down'}`} />
      <path d={d} className={`spark__line ${up ? 'is-up' : 'is-down'}`} />
    </svg>
  )
}

// ------------------------------------------------------------ lean cards --

function LeanCard({ game, today, active, onPick }: { game: PulseGame; today: string; active: boolean; onPick: () => void }) {
  const lean = game.lean
  const fav = game.favorite!
  const side = lean?.side ?? fav.side
  const gapPts = lean ? Math.round(lean.gap * 100) : 0
  const tier = !lean ? 'model' : gapPts >= 15 ? 'hot' : gapPts >= 6 ? 'warm' : 'cool'
  const pickAbbr = sideAbbr(game, side)
  // A forecast made days before the game is a different, weaker thing than the final one.
  const forecastOld = game.modelCreatedAt != null && Date.now() - Date.parse(game.modelCreatedAt) > 48 * 3600_000
  return (
    <button className={`mvv ${active ? 'is-on' : ''} mvv--${tier}`} onClick={onPick} aria-pressed={active}>
      <span className="mvv__top">
        <span className="mvv__when">{dayLabel(game.day, today)} · {etClock(game.startsAt)} <b>{game.sport}</b></span>
        {lean
          ? <span className={`mvv__gap mvv__gap--${tier}`} title={game.bettable.ok ? undefined : game.bettable.label}>{gapPts > 0 ? `+${gapPts}` : gapPts} pts vs Vegas{game.bettable.ok ? '' : ' · not an open bet'}</span>
          : <span className="mvv__gap mvv__gap--model">Model read</span>}
      </span>
      <span className="mvv__teams">
        <span className={side === 'AWAY' ? 'is-lean' : ''}><TeamLogo league={game.sport} abbreviation={game.away.abbr} size={30} />{game.away.abbr}</span>
        <em>@</em>
        <span className={side === 'HOME' ? 'is-lean' : ''}>{game.home.abbr}<TeamLogo league={game.sport} abbreviation={game.home.abbr} size={30} /></span>
      </span>
      {lean ? (
        <span className="mvv__bars">
          <span className="mvv__bar"><span className="mvv__label">Model</span><span className="mvv__track"><span className="mvv__fill mvv__fill--model" style={{ width: pct(lean.model, 1) }} /></span><b>{pct(lean.model)}</b></span>
          <span className="mvv__bar"><span className="mvv__label">Vegas</span><span className="mvv__track"><span className="mvv__fill mvv__fill--market" style={{ width: pct(lean.market, 1) }} /></span><b>{pct(lean.market)}</b></span>
        </span>
      ) : (
        // Head-to-head: one bar, both teams' model win chance.
        <span className="h2h" aria-label={`Model: ${game.away.abbr} ${pct(1 - game.modelHome!)}, ${game.home.abbr} ${pct(game.modelHome)}`}>
          <b className={side === 'AWAY' ? 'is-lean' : ''}>{pct(1 - game.modelHome!)}</b>
          <span className="h2h__bar">
            <span className={`h2h__away ${side === 'AWAY' ? 'is-lean' : ''}`} style={{ width: pct(1 - game.modelHome!, 1) }} />
            <span className={`h2h__home ${side === 'HOME' ? 'is-lean' : ''}`} style={{ width: pct(game.modelHome, 1) }} />
          </span>
          <b className={side === 'HOME' ? 'is-lean' : ''}>{pct(game.modelHome)}</b>
        </span>
      )}
      <span className="mvv__foot">
        <span>{lean ? <>Model likes <strong>{pickAbbr}</strong> more than Vegas</> : <>Model favors <strong>{pickAbbr}</strong></>}
          {/* A gap is not an edge the policy can take: say when the game is not bettable now, and why. */}
          {lean && !game.bettable.ok && <> · <em className="mvv__notbet" data-testid="mvv-not-bettable">{game.bettable.label}</em></>}</span>
        <span className="mvv__fresh">{game.decision?.position ? 'Bet placed' : lean ? (game.marketCurrent ? 'Live price' : `Price ${ageText(game.marketObservedAt)}`) : 'Line pending'}</span>
      </span>
      {forecastOld && <span className="mvv__warn">Early forecast · made {ageText(game.modelCreatedAt).replace(' old', '')} ago · {forecastRefreshText(game.sport)}</span>}
    </button>
  )
}

function PendingStrip({ games, today, onPick }: { games: PulseGame[]; today: string; onPick: (id: string) => void }) {
  if (!games.length) return null
  return (
    <div className="pending">
      <p className="pending__title">Waiting on a forecast or a price</p>
      <div className="pending__row">
        {games.map((g) => (
          <button key={g.id} className="pending__item" onClick={() => onPick(g.id)}>
            <b>{g.sport}</b> {g.away.abbr} @ {g.home.abbr} <span>{dayLabel(g.day, today)} {etClock(g.startsAt)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// -------------------------------------------------------------- side rail --

function StandingsCard({ standings }: { standings: Standing[] }) {
  if (!standings.length) return null
  const sorted = [...standings].sort((a, b) => b.bankroll - a.bankroll)
  const top = sorted[0].bankroll, low = Math.min(...sorted.map((s) => s.bankroll), sorted[0].starting_balance)
  return (
    <section className="side-card">
      <header className="side-card__head"><span><Swords size={15} /> A House Divided</span><Link to="/a-house-divided">Standings <ArrowRight size={13} /></Link></header>
      <ol className="race">
        {sorted.map((s, i) => (
          <li key={s.participant} className="race__row">
            <span className="race__rank">{i === 0 ? <Trophy size={14} /> : i + 1}</span>
            <span className="race__name">{s.display_name.replace(' — The Models', ' (Models)')}</span>
            <span className="race__track"><span style={{ width: `${Math.max(4, ((s.bankroll - low) / (top - low || 1)) * 100)}%`, background: s.color }} /></span>
            <span className={`race__pl ${s.settled_profit >= 0 ? 'is-up' : 'is-down'}`}>{usd(s.settled_profit, true)}</span>
          </li>
        ))}
      </ol>
      <Link className="side-card__cta" to="/a-house-divided">Make your picks</Link>
    </section>
  )
}

function Receipts({ settled }: { settled: (LedgerPosition & { sport?: SportCode })[] }) {
  const recent = settled.slice(0, 6)
  const scores = useScores(recent.map((p) => p.game_id))
  if (!settled.length) return null
  const last20 = settled.slice(0, 20).reverse()
  return (
    <section className="side-card">
      <header className="side-card__head"><span><Radio size={15} /> Recent results</span><Link to="/performance">Wager results <ArrowRight size={13} /></Link></header>
      <div className="dots" aria-label="Last 20 results, oldest first">
        {last20.map((p) => <i key={p.position_id} className={`dot dot--${resultOf(p).toLowerCase()}`} title={`${p.matchup}: ${p.selection} ${resultOf(p)}`} />)}
      </div>
      <ul className="receipts">
        {recent.map((p) => {
          const r = resultOf(p)
          const s = scores[p.game_id]
          const [aw, hm] = (p.matchup ?? '@').split('@').map((x) => x.trim())
          const final = s && s.phase === 'FINAL' && s.away != null ? `${aw} ${s.away}–${s.home} ${hm}` : p.matchup
          return (
            <li key={p.position_id}>
              <Link to={gameHref(p.sport ?? 'MLB', p.game_id)} className="receipt">
                <span className={`receipt__mark receipt__mark--${r.toLowerCase()}`}>{r[0]}</span>
                <span className="receipt__what"><strong>{p.selection} {american(p.entry.american)}</strong><small>{p.sport} · {final} · {etClock(p.starts_at ?? p.settled_at, { month: 'short', day: 'numeric' })}</small></span>
                <span className={`receipt__pl ${(p.profit_loss ?? 0) >= 0 ? 'is-up' : 'is-down'}`}>{usd(p.profit_loss, true)}</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// ---------------------------------------------------------------- drawer --

function GameDrawer({ game, today, onClose }: { game: PulseGame; today: string; onClose: () => void }) {
  const d = game.decision
  const p = game.position
  const homeModel = game.modelHome, homeMkt = game.marketHome
  return (
    <aside className="drawer" aria-label={`${game.away.name} at ${game.home.name}`}>
      <button className="drawer__close" onClick={onClose} aria-label="Close"><X size={18} /></button>
      <p className="pulse__eyebrow">{game.sport} · {dayLabel(game.day, today)} {etClock(game.startsAt)} ET</p>
      <div className="drawer__teams">
        <div><TeamLogo league={game.sport} abbreviation={game.away.abbr} size={52} /><strong>{game.away.name}</strong>{game.score && <b>{game.score.away}</b>}</div>
        <span>{game.phase === 'FINAL' ? 'Final' : game.phase === 'LIVE' ? game.score?.detail : '@'}</span>
        <div><TeamLogo league={game.sport} abbreviation={game.home.abbr} size={52} /><strong>{game.home.name}</strong>{game.score && <b>{game.score.home}</b>}</div>
      </div>

      {p && (
        <div className={`drawer__bet drawer__bet--${resultOf(p).toLowerCase()}`}>
          <small>Our paper bet</small>
          <strong>{p.selection} {american(p.entry.american)} · {usd(p.stake)}</strong>
          <span>{resultOf(p) === 'OPEN' ? `Pending · placed at ${p.entry.sportsbook ?? 'book'}` : `${resultOf(p) === 'WIN' ? 'Won' : resultOf(p) === 'LOSS' ? 'Lost' : 'Push'} ${usd(p.profit_loss, true)}`}</span>
        </div>
      )}

      <h3>Win probability</h3>
      {homeModel == null && homeMkt == null && <p className="drawer__muted">No forecast or market price is stored for this game yet.</p>}
      {(homeModel != null || homeMkt != null) && (
        <div className="split">
          <SplitRow label="Model" away={homeModel == null ? null : 1 - homeModel} home={homeModel} game={game} note={game.modelCreatedAt ? `forecast ${ageText(game.modelCreatedAt)}` : 'no forecast yet'} />
          <SplitRow label="Vegas" away={homeMkt == null ? null : 1 - homeMkt} home={homeMkt} game={game} stale={!game.marketFresh}
            note={homeMkt == null ? 'no price yet' : `${game.books} book${game.books === 1 ? '' : 's'} · ${game.marketCurrent ? 'current' : ageText(game.marketObservedAt)}${game.marketFresh ? '' : ' · too old to compare'}`} />
        </div>
      )}
      {game.lean && <p className="drawer__lean">The model rates <strong>{sideAbbr(game, game.lean.side)}</strong> <b>{Math.round(game.lean.gap * 100)} points</b> higher than the market does.</p>}

      {(game.bestAway || game.bestHome) && (
        <>
          <h3>Best moneyline</h3>
          <div className="drawer__prices">
            <div><span>{game.away.abbr}</span><strong>{american(game.bestAway?.american)}</strong><small>{game.bestAway?.book ?? '—'}</small></div>
            <div><span>{game.home.abbr}</span><strong>{american(game.bestHome?.american)}</strong><small>{game.bestHome?.book ?? '—'}</small></div>
          </div>
        </>
      )}

      <h3>Official decision</h3>
      <p className="drawer__decision">
        {d ? (d.position ? `Qualified: ${d.selection} at ${american(d.american)}${d.sportsbook ? ` (${d.sportsbook})` : ''}.`
          : d.plain_reasons?.length ? `No bet: ${d.plain_reasons.join('; ')}.`
          : d.recommendation === 'NOT_YET_EVALUATED' ? 'Not evaluated yet.' : `${d.recommendation.replace(/_/g, ' ').toLowerCase()}.`)
          : game.windowOpensAt ? `The model decides ${etClock(game.windowOpensAt, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ET.` : 'No decision scheduled.'}
      </p>

      <Link className="drawer__cta" to={gameHref(game.sport, game.id)}>Open the full game brief <ArrowRight size={15} /></Link>
    </aside>
  )
}

function SplitRow({ label, away, home, game, note, stale = false }: { label: string; away: number | null; home: number | null; game: PulseGame; note: string; stale?: boolean }) {
  return (
    <div className={`split__row ${stale ? 'split__row--stale' : ''}`}>
      <span className="split__label">{label}<small>{note}</small></span>
      {away == null || home == null ? <span className="split__none">—</span> : (
        <span className="split__bar">
          <span className="split__away" style={{ width: pct(away, 1) }}>{game.away.abbr} {pct(away)}</span>
          <span className="split__home" style={{ width: pct(home, 1) }}>{pct(home)} {game.home.abbr}</span>
        </span>
      )}
    </div>
  )
}
