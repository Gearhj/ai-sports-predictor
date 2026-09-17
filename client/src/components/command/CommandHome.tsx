/**
 * Home: today's command center, about one desktop viewport.
 *
 *   1. Today's Edge Board: up to four game cards -- the best evaluated edges,
 *      the closest plays, the model's leans before prices, or the games on
 *      deck -- chosen by `services/edgeBoard`.
 *   2. The condensed virtual bankroll (with its D3 curve and the four sport
 *      accounts as chips) beside Today's Agenda: operational state, counts and
 *      the next three items needing attention.
 *   3. The day on one D3 axis, clustered and packed so a full MLB slate stays
 *      legible.
 *   4. AISP Today, last: the day in one sentence, seven counts and a
 *      four-sport readiness strip, counted from the same read models as above.
 *
 * Every region reads the shared cache first and refreshes in the background;
 * nothing on this page reaches a provider.
 */
import { ArrowRight } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSportReadiness, type SportReadinessEntry } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, gameHref, money, signedMoney,
  useDecisionBoard, useLedger, useMarketCoverage, type Ledger, type SportCode,
} from '../../services/commandCenter'
import { BankrollHistory } from './BankrollHistory'
import { AsOf } from './RegionState'
import { RegionBoundary } from './RegionBoundary'
import { EdgeBoard } from './EdgeBoard'
import { GameScheduleTimeline, type ScheduleGame } from './GameScheduleTimeline'
import { buildAgenda, completedCount, nextAttention, KIND_LABEL, type AgendaEvent } from '../../services/agenda'
import { buildEdgeBoard } from '../../services/edgeBoard'
import { AispToday, buildTodaySummary } from './AispToday'
import './command.css'

export const todayEt = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())

function sameEtDay(iso: string | null | undefined, day: string): boolean {
  return Boolean(iso) && new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(iso as string)) === day
}

/** Short status for a sport, for Decisions and the Experiment -- not for Home cards. */
export function sportStatus(entry: SportReadinessEntry | undefined): { text: string; tone: '' | 'good' | 'warn' | 'bad' } {
  if (!entry) return { text: 'Reading…', tone: '' }
  if (entry.read_state === 'READ_FAILED') return { text: 'Read failed', tone: 'bad' }
  const price = entry.price?.state ?? ''
  if ((entry.position?.open ?? 0) > 0) return { text: 'Position open', tone: 'good' }
  if (entry.decision?.state === 'WINDOW_OPEN') return { text: 'Decision window open', tone: 'good' }
  if (entry.eligibility && !entry.eligibility.eligible) return { text: 'Awaiting eligibility', tone: 'warn' }
  if (price === 'COLLECTION_BLOCKED' || !entry.pipeline.ready) return { text: 'Price intake blocked', tone: 'bad' }
  if (entry.price?.blocker) return { text: 'Price source unverified', tone: 'warn' }
  if (entry.forecast?.state === 'FORECAST_AWAITING_PRODUCER') return { text: 'Awaiting forecast', tone: 'warn' }
  if (price === 'FRESH_PRICE') return { text: 'Priced · window not open', tone: 'good' }
  return { text: 'Awaiting price window', tone: '' }
}

export function todaysSettlements(ledger: Ledger | null, day: string, sport?: SportCode) {
  const settled = (ledger?.sports ?? [])
    .filter((s) => !sport || s.sport === sport)
    .flatMap((s) => s.positions.filter((p) => p.status === 'SETTLED' && sameEtDay(p.settled_at, day)))
  return { count: settled.length, pnl: settled.reduce((sum, p) => sum + (p.profit_loss ?? 0), 0) }
}

const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'is-up' : 'is-down')
export function CommandHome({ withSport }: { withSport: (href: string, sport?: 'MLB' | 'NFL') => string }) {
  const day = todayEt()
  const ledger = useLedger()
  const decisions = useDecisionBoard(day)
  const coverage = useMarketCoverage(day)
  const readiness = useSportReadiness()

  const totals = ledger.data?.totals
  const today = todaysSettlements(ledger.data, day)
  const games = useMemo(() => coverage.data?.games ?? [], [coverage.data])
  const items = useMemo(() => decisions.data?.items ?? [], [decisions.data])
  const now = useMinute()

  const qualified = items.filter((i) => i.recommendation === 'QUALIFIED_BET' && i.phase === 'FINAL_PREGAME')
  const openPositions = (ledger.data?.sports ?? []).flatMap((s) => s.positions.filter((p) => p.status !== 'SETTLED'))

  const agenda = useMemo(() => buildAgenda(games, items, ledger.data, now), [games, items, ledger.data, now])
  const idle = useMemo(() => {
    const out: Partial<Record<SportCode, string>> = {}
    for (const s of readiness.data?.sports ?? []) {
      const code = asSport(s.sport)
      if (code && s.next_game && !games.some((g) => asSport(g.league) === code)) out[code] = `Next: ${s.next_game.matchup} · ${s.next_game.starts_label}`
    }
    return out
  }, [readiness.data, games])
  // Home talks about official decisions, so its links open AISP Picks (Bet Board is the page default).
  const decisionsHref = withSport('/decisions?view=decisions')
  const board = useMemo(() => buildEdgeBoard({
    decisions: items, games, ledger: ledger.data, now,
    decisionsHref: (sport) => withSport('/decisions?view=decisions', sport === 'MLB' || sport === 'NFL' ? sport : undefined),
    analyzeHref: (sport, gameId) => gameHref(sport, gameId),
  }), [items, games, ledger.data, now, withSport])
  // The Home schedule shows only game starts and positions; operational events stay in Today's Agenda.
  const schedule = useMemo<ScheduleGame[]>(() => {
    const positions = new Map(items.filter((i) => i.position).map((i) => [i.game_id, i.position]))
    return games.flatMap((g) => {
      const sport = asSport(g.league)
      const start = g.starts_at ? Date.parse(g.starts_at) : NaN
      if (!sport || !Number.isFinite(start)) return []
      const position = positions.get(g.game_id)
      return [{ gameId: g.game_id, sport, matchup: g.matchup ?? `${g.away?.abbreviation ?? 'Away'} @ ${g.home?.abbreviation ?? 'Home'}`,
        startsAt: start, position: position ? { stake: position.stake } : null }]
    })
  }, [games, items])
  const summary = useMemo(() => buildTodaySummary({ games, items, ledger: ledger.data, now }), [games, items, ledger.data, now])
  const dateLabel = new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })
  const ret = totals && totals.starting_capital ? (totals.profit_loss / totals.starting_capital) * 100 : null

  return (
    <section className="cc-home" aria-label="Command center">
      <p className="cc-home__intro">
        AI Sports Predictor forecasts MLB and NFL games, compares each forecast with sportsbook prices, and tracks
        a virtual $1,000-per-sport experiment. Research only — no real money.
      </p>
      <RegionBoundary label="today's edge board">
        <EdgeBoard board={board} now={now} loading={!decisions.data || !coverage.data} dateLabel={dateLabel}
          allHref={decisionsHref} asOf={<AsOf resource={decisions.data ? decisions : coverage} label="decisions" />} />
      </RegionBoundary>

      <div className="cc-home__mid">
        <section className="cc-dark cc-bank" aria-label="Combined virtual bankroll">
          <header className="cc-bank__head">
            <div>
              <span className="cc-kicker">Virtual bankroll · four sport accounts</span>
              <div className="cc-bank__value">{money(totals?.realized_bankroll)}</div>
              <div className={`cc-bank__change ${tone(totals?.profit_loss)}`}>
                {totals && ret != null ? `${signedMoney(totals.profit_loss)} (${ret >= 0 ? '+' : '−'}${Math.abs(ret).toFixed(2)}%) net` : 'Reading the ledger…'}
              </div>
            </div>
            <AsOf resource={ledger} />
          </header>
          <dl className="cc-bank__stats">
            <div><dt>Starting</dt><dd>{money(totals?.starting_capital, 0)}</dd></div>
            <div><dt title={`${today.count} settled today`}>Today</dt><dd className={tone(today.pnl)}>{signedMoney(today.pnl)}</dd></div>
            <div><dt title={`${totals?.open_count ?? 0} open positions`}>Exposure</dt><dd>{money(totals?.open_exposure)}</dd></div>
            <div><dt>Record</dt><dd>{totals ? `${totals.record.WIN}–${totals.record.LOSS}${totals.record.PUSH ? `–${totals.record.PUSH}` : ''}` : '—'}</dd></div>
            <div><dt>Available</dt><dd>{money(totals?.available_capital, 0)}</dd></div>
          </dl>
          <ul className="cc-bank__sports" aria-label="Sport accounts">
            {SPORT_CODES.map((code) => {
              const s = ledger.data?.sports.find((x) => x.sport === code)
              return (
                <li key={code} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
                  <i className="cc-dot" /><span>{code}</span>
                  <b>{money(s?.realized_bankroll, 0)}</b>
                  <small className={tone(s?.profit_loss)}>{s ? signedMoney(s.profit_loss) : '—'}</small>
                </li>
              )
            })}
          </ul>
          {ledger.data
            ? <BankrollHistory ledger={ledger.data} compactHeight={112} hideTable />
            : <div className="cc-hero-bank__placeholder cc-bank__placeholder" />}
          <p className="cc-hero-bank__foot">Virtual research only · no real-money wagering · <Link to="/experiment">$1,000 Experiment <ArrowRight size={12} /></Link></p>
        </section>

        <TodayAgenda events={agenda} now={now} positionsOpen={openPositions.length}
          qualified={qualified.length} gamesToday={coverage.data ? games.length : null} withSport={withSport} />
      </div>

      <section className="cc-agenda-panel" aria-label="Today's timeline">
        <header>
          <h2>Today’s games · start times and AISP positions</h2>
          <AsOf resource={coverage} label="slate" />
        </header>
        {coverage.data
          ? <GameScheduleTimeline games={schedule} now={now} idle={idle} withSport={withSport} />
          : <p className="cc-empty">{coverage.error ? 'The slate read is unavailable.' : 'Loading today’s slate…'}</p>}
      </section>

      <RegionBoundary label="AISP today">
        <AispToday summary={summary} readiness={readiness.data?.sports ?? []} decisionsHref={decisionsHref}
          loading={!coverage.data || !decisions.data} />
      </RegionBoundary>
    </section>
  )
}

/** The clock, ticking once a minute (and aligned to the minute). */
function useMinute(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    let timer: number
    const tick = () => { setNow(Date.now()); timer = window.setTimeout(tick, 60_000 - (Date.now() % 60_000) + 50) }
    timer = window.setTimeout(tick, 60_000 - (Date.now() % 60_000) + 50)
    return () => window.clearTimeout(timer)
  }, [])
  return now
}

function operationalState(events: AgendaEvent[], now: number): string {
  const open = events.filter((e) => e.status === 'open')
  const decision = open.find((e) => e.kind === 'decision')
  if (decision) return 'Decision window open'
  if (open.some((e) => e.kind === 'game')) return `${open.filter((e) => e.kind === 'game').length} game${open.filter((e) => e.kind === 'game').length === 1 ? '' : 's'} in progress`
  if (open.some((e) => e.kind === 'collection')) return 'Market collection open'
  const upcoming = events.filter((e) => e.at > now && e.status !== 'done')
  if (upcoming.length === 0) return events.length ? 'Slate complete · awaiting settlement' : 'No game on today’s slate'
  const hasForecast = events.some((e) => e.kind === 'forecast' && e.status === 'done')
  return hasForecast ? 'Forecasts in · awaiting market collection' : 'Awaiting forecasts and market collection'
}

const ET_CLOCK = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(ms)

function TodayAgenda({ events, now, positionsOpen, qualified, gamesToday, withSport }: {
  events: AgendaEvent[]; now: number; positionsOpen: number; qualified: number; gamesToday: number | null
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
}) {
  const next = nextAttention(events, now, 3)
  const done = completedCount(events)
  return (
    <section className="cc-today" aria-label="Today's agenda">
      <header>
        <h2>Today’s Agenda</h2>
        <span className="cc-today__clock">{ET_CLOCK(now)} ET</span>
      </header>
      <p className="cc-today__state">{operationalState(events, now)}</p>
      <dl className="cc-today__stats">
        <div><dt>Games</dt><dd>{gamesToday ?? '—'}</dd></div>
        <div><dt>Qualified</dt><dd className={qualified ? 'is-up' : ''}>{qualified}</dd></div>
        <div><dt>Open positions</dt><dd>{positionsOpen}</dd></div>
        <div><dt>Milestones done</dt><dd>{done}<small> / {events.length}</small></dd></div>
      </dl>
      <h3>Next up</h3>
      <ol className="cc-next">
        {next.length === 0 && <li className="cc-muted">Nothing else scheduled today.</li>}
        {next.map((item) => {
          const e = item.event
          const minutes = Math.round(((e.status === 'open' ? (e.until ?? e.at) : e.at) - now) / 60_000)
          const when = e.status === 'open' ? `open · closes in ${minutes}m` : minutes >= 60 ? `in ${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m` : `in ${Math.max(0, minutes)}m`
          return (
            <li key={e.key} style={{ ['--sport' as string]: SPORT_COLORS[item.sports[0]] }}>
              <span className="cc-next__time">{ET_CLOCK(e.at)}</span>
              <span className="cc-next__body">
                <b>{KIND_LABEL[e.kind]}{e.status === 'expected' ? ' (expected)' : ''}</b>
                <small>{item.matchups.length > 1 ? `${item.matchups.length} games · ${item.matchups.slice(0, 3).join(', ')}${item.matchups.length > 3 ? '…' : ''}` : item.matchups[0]}</small>
              </span>
              <span className={`cc-next__when ${e.status === 'open' ? 'is-up' : 'is-gold'}`}>{when}</span>
            </li>
          )
        })}
      </ol>
      <Link className="cc-today__link" to={withSport('/decisions')}>Open Daily Decisions <ArrowRight size={13} /></Link>
    </section>
  )
}
