/**
 * Home: a concise four-sport command center, about one to two viewports.
 *
 *   1. A dark financial hero: combined virtual bankroll, change from starting
 *      capital, today's P&L, open exposure, record, and the bankroll chart
 *      (combined or by sport). This is the only bankroll visual on Home.
 *   2. Beside it, "Today": games, open positions, qualified decisions, the next
 *      decision deadline, and the next games that need attention.
 *   3. Four small sport bankroll cards: bankroll, P&L, record, open positions,
 *      sparkline. No pipeline or model prose -- that lives on Decisions.
 *   4. One compact row: next games, the strongest current model-vs-market
 *      disagreement (only when a current comparable price exists), and the
 *      most important decision or position.
 *
 * Everything is persisted AISP state read through the shared cache; nothing on
 * this page reaches a provider.
 */
import { ArrowRight } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useSportReadiness, type SportReadinessEntry } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, etTime, gameHref, money, relative, signedMoney,
  useDecisionBoard, useLedger, useMarketCoverage, type CoverageGame, type DecisionItem, type Ledger, type SportCode,
} from '../../services/commandCenter'
import { BankrollHistory } from './BankrollHistory'
import { AsOf } from './RegionState'
import { Sparkline } from './Sparkline'
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

function todaysSettlements(ledger: Ledger | null, day: string, sport?: SportCode) {
  const settled = (ledger?.sports ?? [])
    .filter((s) => !sport || s.sport === sport)
    .flatMap((s) => s.positions.filter((p) => p.status === 'SETTLED' && sameEtDay(p.settled_at, day)))
  return { count: settled.length, pnl: settled.reduce((sum, p) => sum + (p.profit_loss ?? 0), 0) }
}

const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'is-up' : 'is-down')
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`)
const researchHref = (sport: SportCode, gameId?: string) =>
  sport === 'NBA' ? '/nba/research' : sport === 'CFB' ? '/college-football/research' : gameHref(sport, gameId)

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
  const now = Date.now()

  // Next games needing attention: soonest decision window still ahead.
  const attention = games
    .filter((g) => Date.parse(g.checkpoints.decision_window_closes_at) > now)
    .sort((a, b) => a.checkpoints.decision_window_opens_at.localeCompare(b.checkpoints.decision_window_opens_at))
    .slice(0, 4)
  const otherSportsNext = (readiness.data?.sports ?? [])
    .filter((s) => s.next_game && !games.some((g) => asSport(g.league) === s.sport))
    .map((s) => ({ sport: s.sport as SportCode, next: s.next_game! }))
  const nextDeadline = attention[0]?.checkpoints.decision_window_opens_at
    ?? (readiness.data?.sports ?? []).map((s) => (s.decision?.window as { opens_at?: string } | undefined)?.opens_at).filter(Boolean).sort()[0]
  const qualified = items.filter((i) => i.recommendation === 'QUALIFIED_BET' && i.phase === 'FINAL_PREGAME')
  const openPositions = (ledger.data?.sports ?? []).flatMap((s) => s.positions.filter((p) => p.status !== 'SETTLED'))

  const disagreement = useMemo(() => games
    .filter((g) => g.consensus_is_current && g.model?.home_two_way != null && g.consensus_no_vig_home != null)
    .map((g) => ({ g, gap: (g.model!.home_two_way as number) - (g.consensus_no_vig_home as number) }))
    .sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap))[0] ?? null, [games])

  const keyDecision: DecisionItem | null = items.find((i) => i.position && i.position.status !== 'SETTLED')
    ?? items.find((i) => i.group === 'READY')
    ?? null

  return (
    <section className="cc-home" aria-label="Command center">
      <div className="cc-home__top">
        <section className="cc-dark cc-hero-bank" aria-label="Combined virtual bankroll">
          <header className="cc-hero-bank__head">
            <div>
              <span className="cc-kicker">Virtual bankroll · MLB · NFL · NBA · CFB</span>
              <div className="cc-hero-bank__value">{money(totals?.realized_bankroll)}</div>
              <div className={`cc-hero-bank__change ${tone(totals?.profit_loss)}`}>
                {totals ? `${signedMoney(totals.profit_loss)} (${((totals.profit_loss / totals.starting_capital) * 100).toFixed(2)}%) from ${money(totals.starting_capital, 0)} starting capital` : 'Reading the ledger…'}
              </div>
            </div>
            <AsOf resource={ledger} />
          </header>
          <dl className="cc-hero-bank__stats">
            <div><dt>Today's P&amp;L</dt><dd className={tone(today.pnl)}>{signedMoney(today.pnl)}</dd><small>{today.count} settled today</small></div>
            <div><dt>Open exposure</dt><dd>{money(totals?.open_exposure)}</dd><small>{totals?.open_count ?? 0} open</small></div>
            <div><dt>Record</dt><dd>{totals ? `${totals.record.WIN}–${totals.record.LOSS}${totals.record.PUSH ? `–${totals.record.PUSH}` : ''}` : '—'}</dd><small>{totals?.settled_count ?? 0} settled</small></div>
            <div><dt>Available</dt><dd>{money(totals?.available_capital)}</dd><small>virtual capital</small></div>
          </dl>
          {ledger.data
            ? <BankrollHistory ledger={ledger.data} compactHeight={190} hideTable />
            : <div className="cc-hero-bank__placeholder" />}
          <p className="cc-hero-bank__foot">Virtual research only · no real-money wagering · <Link to="/experiment">$1,000 Experiment <ArrowRight size={12} /></Link></p>
        </section>

        <section className="cc-today" aria-label="Today">
          <header>
            <h2>Today</h2>
            <span>{new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</span>
          </header>
          <dl className="cc-today__stats">
            <div><dt>Games</dt><dd>{coverage.data ? games.length : '—'}</dd></div>
            <div><dt>Open positions</dt><dd>{ledger.data ? openPositions.length : '—'}</dd></div>
            <div><dt>Qualified</dt><dd className={qualified.length ? 'is-up' : ''}>{decisions.data ? qualified.length : '—'}</dd></div>
            <div className="cc-today__deadline"><dt>Next decision</dt><dd className="is-gold">{nextDeadline ? etTime(nextDeadline).replace(' ET', '') : '—'}</dd><small>{nextDeadline ? relative(nextDeadline) : 'none scheduled'}</small></div>
          </dl>
          <h3>Needs attention next</h3>
          <ul className="cc-attn">
            {attention.map((g) => <AttentionRow key={g.game_id} game={g} />)}
            {attention.length === 0 && otherSportsNext.slice(0, 3).map(({ sport, next }) => (
              <li key={sport}>
                <Link to={researchHref(sport, next.game_id)} style={{ ['--sport' as string]: SPORT_COLORS[sport] }}>
                  <i className="cc-dot" /><b>{next.matchup}</b><span>{next.starts_label}</span>
                </Link>
              </li>
            ))}
            {attention.length === 0 && otherSportsNext.length === 0 && <li className="cc-muted">No decision window scheduled.</li>}
          </ul>
          <Link className="cc-today__link" to={withSport('/decisions')}>Open Daily Decisions <ArrowRight size={13} /></Link>
        </section>
      </div>

      <div className="cc-sportcards" role="list" aria-label="Sport bankrolls">
        {SPORT_CODES.map((code) => {
          const s = ledger.data?.sports.find((x) => x.sport === code)
          const todaySport = todaysSettlements(ledger.data, day, code)
          return (
            <Link role="listitem" key={code} className="cc-sportcard" style={{ ['--sport' as string]: SPORT_COLORS[code] }}
              to="/experiment" aria-label={`${SPORT_NAMES[code]} bankroll ${money(s?.realized_bankroll)}`}>
              <span className="cc-sportcard__name">{SPORT_NAMES[code]}</span>
              <span className="cc-sportcard__value">{money(s?.realized_bankroll)}</span>
              <span className={`cc-sportcard__pnl ${tone(todaySport.count ? todaySport.pnl : s?.profit_loss)}`}>
                {todaySport.count ? `${signedMoney(todaySport.pnl)} today` : `${signedMoney(s?.profit_loss)} total`}
              </span>
              <span className="cc-sportcard__meta">
                {s ? `${s.record.WIN}–${s.record.LOSS}${s.record.PUSH ? `–${s.record.PUSH}` : ''}` : '—'} · {s?.open_count ?? 0} open
              </span>
              <span className="cc-sportcard__spark">{s && <Sparkline series={s.series} color={SPORT_COLORS[code]} width={96} height={30} />}</span>
            </Link>
          )
        })}
      </div>

      <div className="cc-strip">
        <section aria-label="Next games">
          <h3>Next games</h3>
          <ul className="cc-attn cc-attn--plain">
            {games.filter((g) => g.starts_at && Date.parse(g.starts_at) > now).slice(0, 3).map((g) => {
              const code = asSport(g.league) ?? 'MLB'
              const p = g.model?.home_two_way
              return (
                <li key={g.game_id}>
                  <Link to={gameHref(code, g.game_id)} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
                    <i className="cc-dot" /><b>{g.matchup}</b>
                    <span>{etTime(g.starts_at).replace(' ET', '')}{p != null ? ` · ${p >= 0.5 ? g.home?.abbreviation : g.away?.abbreviation} ${pct(p >= 0.5 ? p : 1 - p)}` : ''}</span>
                  </Link>
                </li>
              )
            })}
            {otherSportsNext.slice(0, Math.max(0, 3 - games.length)).map(({ sport, next }) => (
              <li key={sport}>
                <Link to={researchHref(sport, next.game_id)} style={{ ['--sport' as string]: SPORT_COLORS[sport] }}>
                  <i className="cc-dot" /><b>{next.matchup}</b><span>{next.starts_label}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link className="cc-strip__link" to={withSport('/games')}>Games &amp; Forecasts <ArrowRight size={12} /></Link>
        </section>

        <section aria-label="Strongest disagreement">
          <h3>Model vs market</h3>
          {disagreement ? (
            <Link className="cc-insight" to={gameHref(asSport(disagreement.g.league) ?? 'MLB', disagreement.g.game_id)}>
              <b>{disagreement.g.matchup}</b>
              <span className={disagreement.gap >= 0 ? 'is-up' : 'is-down'}>
                {disagreement.gap >= 0 ? '+' : '−'}{Math.abs(disagreement.gap * 100).toFixed(1)} pts
              </span>
              <small>Model {pct(disagreement.g.model?.home_two_way)} vs consensus {pct(disagreement.g.consensus_no_vig_home)} ({disagreement.g.home?.abbreviation} win)</small>
            </Link>
          ) : (
            <p className="cc-insight cc-insight--empty">
              <b>No current comparable price</b>
              <small>{coverage.data?.summary.next_collection_at ? `Collection opens ${etTime(coverage.data.summary.next_collection_at)}` : 'No collection scheduled today'}</small>
            </p>
          )}
          <Link className="cc-strip__link" to={withSport('/markets')}>Markets &amp; Crowd <ArrowRight size={12} /></Link>
        </section>

        <section aria-label="Key decision">
          <h3>{keyDecision?.position ? 'Open position' : keyDecision ? 'Qualified now' : 'Latest position'}</h3>
          {keyDecision ? (
            <Link className="cc-insight" to={gameHref(keyDecision.sport, keyDecision.game_id)}>
              <b>{keyDecision.selection} {keyDecision.american != null ? (keyDecision.american > 0 ? `+${keyDecision.american}` : keyDecision.american) : ''}</b>
              <span>{keyDecision.position ? `${money(keyDecision.position.stake)} at risk` : `Edge ${((keyDecision.edge ?? 0) * 100).toFixed(1)} pts`}</span>
              <small>{keyDecision.matchup} · {SPORT_NAMES[keyDecision.sport]}</small>
            </Link>
          ) : (
            <LatestSettlement ledger={ledger.data} />
          )}
          <Link className="cc-strip__link" to={withSport('/decisions')}>Daily Decisions <ArrowRight size={12} /></Link>
        </section>
      </div>
    </section>
  )
}

function AttentionRow({ game }: { game: CoverageGame }) {
  const code = asSport(game.league) ?? 'MLB'
  const opens = game.checkpoints.decision_window_opens_at
  const open = Date.parse(opens) <= Date.now()
  const status = game.price_state === 'CURRENT_PRICE' ? 'Priced' : !game.model ? 'Awaiting forecast' : 'Awaiting price'
  return (
    <li>
      <Link to={gameHref(code, game.game_id)} style={{ ['--sport' as string]: SPORT_COLORS[code] }}>
        <i className="cc-dot" />
        <b>{game.matchup}</b>
        <span className={open ? 'is-up' : 'is-gold'}>{open ? 'Window open' : `Window ${relative(opens)}`}</span>
        <small>{status}</small>
      </Link>
    </li>
  )
}

function LatestSettlement({ ledger }: { ledger: Ledger | null }) {
  const latest = (ledger?.sports ?? []).flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport })))
    .filter((p) => p.status === 'SETTLED').sort((a, b) => (b.settled_at ?? '').localeCompare(a.settled_at ?? ''))[0]
  if (!latest) return <p className="cc-insight cc-insight--empty"><b>No position yet</b></p>
  return (
    <Link className="cc-insight" to={`/experiment?position=${latest.position_id}`}>
      <b>{latest.selection} {latest.entry.american != null ? (latest.entry.american > 0 ? `+${latest.entry.american}` : latest.entry.american) : ''}</b>
      <span className={tone(latest.profit_loss)}>{latest.result} {signedMoney(latest.profit_loss)}</span>
      <small>{latest.matchup} · {SPORT_NAMES[latest.sport]} · settled {relative(latest.settled_at)}</small>
    </Link>
  )
}
