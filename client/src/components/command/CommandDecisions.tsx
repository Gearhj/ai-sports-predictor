/**
 * Daily Decisions: the decision command center.
 *
 *   * A dark Decision Pulse: the next game that needs attention with a live
 *     countdown to its window, actionable decisions, open positions, capital at
 *     risk, today's P&L, and a compact interactive funnel.
 *   * A status-filtered feed (All, Actionable, Positions, Watching, Passed,
 *     Completed) in one or two columns, ordered by the next required action and
 *     its deadline. No fixed workflow columns: an uneven day -- every game
 *     waiting -- still reads as an intentional, ordered list.
 *   * Supporting visuals only when they have something to show: the
 *     model-vs-market edge plot when comparable prices exist, and a results
 *     tape of settled positions.
 *
 * Only funded-era governed evaluations appear (the read model scopes them to
 * their own sport's games); legacy/research experiment rows stay in the audit
 * view below the page. Internal codes are translated into short language, with
 * the raw evaluation available on demand.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSportReadiness, type SportReadinessEntry } from '../readiness/SportReadiness'
import {
  SPORT_CODES, SPORT_COLORS, SPORT_NAMES, asSport, etTime, gameHref, money, relative, signedMoney,
  useDecisionBoard, useLedger, useMarketCoverage,
  type CoverageGame, type DecisionItem, type Ledger, type SportCode,
} from '../../services/commandCenter'
import { ChartTable, DeferUntilVisible, Tooltip, useReducedMotion, useTooltip, useWidth } from './chartKit'
import { EdgeMap } from './EdgeMap'
import { AsOf } from './RegionState'
import { todayEt } from './CommandHome'
import './command.css'

type Tab = 'ALL' | 'ACTIONABLE' | 'POSITIONS' | 'WATCHING' | 'PASSED' | 'COMPLETED'
type Tone = 'green' | 'gold' | 'red' | 'gray'

interface FeedCard {
  key: string
  sport: SportCode
  href: string
  matchup: string
  startsAt: string | null
  windowOpens: string | null
  windowCloses: string | null
  tab: Exclude<Tab, 'ALL'>
  status: { label: string; tone: Tone; detail: string | null }
  item: DecisionItem | null
  game: CoverageGame | null
  nextLabel: string | null
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

/** Internal decision codes, in plain language. The raw row stays available. */
export function describeDecision(item: DecisionItem | null, game: CoverageGame | null, started: boolean): FeedCard['status'] {
  const reason = item?.reason ?? null
  if (item?.position) {
    if (item.position.status === 'SETTLED') {
      const pnl = item.position.profit_loss ?? 0
      const verb = item.position.result === 'WIN' ? 'Won' : item.position.result === 'LOSS' ? 'Lost' : 'Settled'
      return { label: `${verb} ${signedMoney(pnl)}`, tone: pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'gray', detail: reason }
    }
    return { label: 'Position open', tone: 'green', detail: reason }
  }
  const rec = item?.recommendation ?? 'NOT_YET_EVALUATED'
  if (rec === 'QUALIFIED_BET' && item?.phase === 'FINAL_PREGAME') {
    return started ? { label: 'Qualified · not positioned', tone: 'red', detail: reason } : { label: 'Qualifies now', tone: 'green', detail: reason }
  }
  if (rec === 'NO_BET' && item?.phase === 'FINAL_PREGAME') return { label: 'Passed · edge below policy', tone: 'red', detail: reason }
  if (rec === 'BLOCKED') return { label: /eligib/i.test(reason ?? '') ? 'Awaiting producer eligibility' : 'Blocked by policy', tone: 'red', detail: reason }
  if (started) return { label: 'Window closed · no final evaluation', tone: 'red', detail: reason }
  if (rec === 'PROVISIONAL_EDGE') return { label: 'Early edge · awaiting final check', tone: 'gold', detail: reason }
  if (rec === 'WATCH') return { label: 'Near the threshold', tone: 'gold', detail: reason }
  if (rec === 'QUALIFIED_BET') return { label: 'Morning read qualifies · awaiting final check', tone: 'gold', detail: reason }
  if (game && !game.model) return { label: 'Waiting for the forecast', tone: 'gold', detail: reason }
  if (game && game.price_state !== 'CURRENT_PRICE') return { label: 'Waiting for a sportsbook price', tone: 'gold', detail: reason }
  return { label: 'Waiting for the decision window', tone: 'gold', detail: reason }
}

function pickItem(items: DecisionItem[]): DecisionItem | null {
  const rank = (i: DecisionItem) => (i.position ? 100 : 0) + (i.recommendation === 'QUALIFIED_BET' ? 20 : 0)
    + (i.phase === 'FINAL_PREGAME' ? 10 : i.phase === 'LINEUP_CONFIRMED' ? 5 : 0) + (i.edge ?? -1)
  return [...items].sort((a, b) => rank(b) - rank(a))[0] ?? null
}

export function buildFeed(items: DecisionItem[], games: CoverageGame[], readiness: SportReadinessEntry[], now: number): FeedCard[] {
  const byGame = new Map<string, DecisionItem[]>()
  for (const i of items) byGame.set(i.game_id, [...(byGame.get(i.game_id) ?? []), i])
  const gameById = new Map(games.map((g) => [g.game_id, g]))
  const ids = new Set([...byGame.keys(), ...games.map((g) => g.game_id)])
  const cards: FeedCard[] = []
  for (const id of ids) {
    const game = gameById.get(id) ?? null
    const item = pickItem(byGame.get(id) ?? [])
    const sport = (item?.sport ?? asSport(game?.league)) as SportCode | null
    if (!sport) continue
    const startsAt = game?.starts_at ?? item?.starts_at ?? null
    const started = startsAt ? Date.parse(startsAt) <= now : false
    const status = describeDecision(item, game, started)
    const tab: FeedCard['tab'] = item?.position ? (item.position.status === 'SETTLED' ? 'COMPLETED' : 'POSITIONS')
      : status.tone === 'green' ? 'ACTIONABLE'
        : started ? 'COMPLETED'
          : status.tone === 'red' ? 'PASSED' : 'WATCHING'
    cards.push({
      key: id, sport, href: gameHref(sport, id), matchup: game?.matchup ?? item?.matchup ?? 'Game',
      startsAt, windowOpens: game?.checkpoints.decision_window_opens_at ?? item?.decision_window?.opens_at ?? null,
      windowCloses: game?.checkpoints.decision_window_closes_at ?? item?.decision_window?.closes_at ?? null,
      tab, status, item, game, nextLabel: null,
    })
  }
  // Sports with nothing on this slate still show their next opportunity.
  for (const entry of readiness) {
    const code = asSport(entry.sport)
    if (!code || !entry.next_game || cards.some((c) => c.sport === code)) continue
    const window = entry.decision?.window as { opens_at?: string; closes_at?: string } | undefined
    const blocked = entry.pipeline && !entry.pipeline.ready
    cards.push({
      key: `next-${code}`, sport: code,
      href: code === 'NBA' ? '/nba/research' : code === 'CFB' ? '/college-football/research' : gameHref(code, entry.next_game.game_id),
      matchup: entry.next_game.matchup, startsAt: (entry.next_game as { starts_at?: string }).starts_at ?? null,
      windowOpens: window?.opens_at ?? null, windowCloses: window?.closes_at ?? null, tab: 'WATCHING',
      status: blocked
        ? { label: 'Price intake not available yet', tone: 'gray', detail: entry.pipeline.blocker }
        : { label: 'Next scheduled decision', tone: 'gold', detail: entry.pipeline.blocker },
      item: null, game: null, nextLabel: entry.next_game.starts_label,
    })
  }
  const order: Record<FeedCard['tab'], number> = { ACTIONABLE: 0, POSITIONS: 1, WATCHING: 2, PASSED: 3, COMPLETED: 4 }
  return cards.sort((a, b) => order[a.tab] - order[b.tab]
    || (a.tab === 'COMPLETED' ? (b.startsAt ?? '').localeCompare(a.startsAt ?? '') : (a.windowOpens ?? '9').localeCompare(b.windowOpens ?? '9'))
    || a.matchup.localeCompare(b.matchup))
}

function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t) }, [ms])
  return now
}

export function countdown(iso: string | null, now: number): string {
  if (!iso) return '—'
  const minutes = Math.round((Date.parse(iso) - now) / 60_000)
  if (minutes <= 0) return 'now'
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h >= 48) return `${Math.round(h / 24)} days`
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

const TABS: { key: Tab; label: string }[] = [
  { key: 'ALL', label: 'All' }, { key: 'ACTIONABLE', label: 'Actionable' }, { key: 'POSITIONS', label: 'Positions' },
  { key: 'WATCHING', label: 'Watching' }, { key: 'PASSED', label: 'Passed / declined' }, { key: 'COMPLETED', label: 'Completed' },
]

export function CommandDecisions({ day }: { day: string }) {
  const board = useDecisionBoard(day)
  const coverage = useMarketCoverage(day)
  const ledger = useLedger()
  const readiness = useSportReadiness()
  const now = useNow()
  const [tab, setTab] = useState<Tab>('ALL')
  const [sport, setSport] = useState<SportCode | 'ALL'>('ALL')

  const items = useMemo(() => board.data?.items ?? [], [board.data])
  const games = useMemo(() => coverage.data?.games ?? [], [coverage.data])
  const readinessSports = useMemo(() => readiness.data?.sports ?? [], [readiness.data])
  const isToday = day === todayEt()
  const feed = useMemo(() => buildFeed(items, games, isToday ? readinessSports : [], now), [items, games, readinessSports, isToday, now])
  const inSport = (c: FeedCard) => sport === 'ALL' || c.sport === sport
  const visible = feed.filter((c) => inSport(c) && (tab === 'ALL' || c.tab === tab))
  const count = (t: Tab) => feed.filter((c) => inSport(c) && (t === 'ALL' || c.tab === t)).length

  const next = feed.find((c) => c.tab === 'ACTIONABLE') ?? feed.find((c) => c.tab === 'POSITIONS')
    ?? feed.filter((c) => c.tab === 'WATCHING' && c.windowOpens).sort((a, b) => (a.windowOpens ?? '').localeCompare(b.windowOpens ?? ''))[0] ?? null
  const nextOpen = next?.windowOpens ? Date.parse(next.windowOpens) <= now : false
  const todayPnl = (ledger.data?.sports ?? []).flatMap((s) => s.positions)
    .filter((p) => p.status === 'SETTLED' && p.settled_at
      && new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(p.settled_at)) === todayEt())
    .reduce((s, p) => s + (p.profit_loss ?? 0), 0)
  const sportsInFeed = SPORT_CODES.filter((c) => feed.some((f) => f.sport === c))

  return (
    <section className="cc-decisions" aria-label="Daily Decisions">
      <section className="cc-dark cc-pulse" aria-label="Decision pulse">
        <div className="cc-pulse__next">
          <span className="cc-kicker">Decision pulse · {new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })}</span>
          {next ? (
            <Link to={next.href} className="cc-pulse__game" style={{ ['--sport' as string]: SPORT_COLORS[next.sport] }}>
              <span className="cc-pulse__sport"><i className="cc-dot" />{SPORT_NAMES[next.sport]} · {next.startsAt ? etTime(next.startsAt, true) : next.nextLabel}</span>
              <b>{next.matchup}</b>
              <span className={`cc-pulse__status cc-tone--${next.status.tone}`}>{next.status.label}</span>
            </Link>
          ) : <b className="cc-pulse__game">No decision is scheduled on this slate</b>}
          <div className="cc-pulse__countdown">
            <small>{nextOpen ? 'Window closes in' : 'Decision window in'}</small>
            <strong>{next ? countdown(nextOpen ? next.windowCloses : next.windowOpens, now) : '—'}</strong>
            <small>{next?.windowOpens ? `${etTime(next.windowOpens).replace(' ET', '')}–${etTime(next.windowCloses)}` : ''}</small>
          </div>
        </div>
        <dl className="cc-pulse__stats">
          <div><dt>Actionable</dt><dd className={count('ACTIONABLE') ? 'is-up' : ''}>{count('ACTIONABLE')}</dd></div>
          <div><dt>Open positions</dt><dd>{ledger.data ? ledger.data.totals.open_count : '—'}</dd></div>
          <div><dt>Capital at risk</dt><dd>{money(ledger.data?.totals.open_exposure)}</dd></div>
          <div><dt>Today's P&amp;L</dt><dd className={todayPnl > 0 ? 'is-up' : todayPnl < 0 ? 'is-down' : ''}>{signedMoney(todayPnl)}</dd></div>
        </dl>
        <div className="cc-pulse__funnel"><DecisionFunnel items={items} games={games} /></div>
        <div className="cc-pulse__asof"><AsOf resource={board} label="decisions" /></div>
      </section>

      <div className="cc-feedbar">
        <div className="cc-tabs" role="tablist" aria-label="Decision status">
          {TABS.map((t) => {
            const n = count(t.key)
            return (
              <button key={t.key} role="tab" type="button" aria-selected={tab === t.key}
                disabled={t.key !== 'ALL' && n === 0} onClick={() => setTab(t.key)}>
                {t.label} <span>{n}</span>
              </button>
            )
          })}
        </div>
        {sportsInFeed.length > 1 && (
          <div className="cc-filters" role="group" aria-label="Sport" style={{ margin: 0 }}>
            {(['ALL', ...sportsInFeed] as const).map((code) => (
              <button key={code} type="button" aria-pressed={sport === code} onClick={() => setSport(code)}>
                {code === 'ALL' ? 'All sports' : SPORT_NAMES[code]}
              </button>
            ))}
          </div>
        )}
      </div>

      {!board.data && !coverage.data
        ? <p className="cc-empty" role="status">{board.error ? `Decisions are unavailable: ${board.error}` : 'Loading decisions…'}</p>
        : visible.length === 0
          ? <p className="cc-empty">Nothing in this view.</p>
          : <div className="cc-feed" role="tabpanel">{visible.map((c) => <DecisionCard key={c.key} card={c} now={now} />)}</div>}

      <SupportingVisuals items={items} ledger={ledger.data} />
    </section>
  )
}

const DecisionCard = memo(function DecisionCard({ card, now }: { card: FeedCard; now: number }) {
  const { item, game } = card
  const model = game?.model ?? null
  const homeP = model?.home_two_way ?? null
  const pickHome = homeP != null ? homeP >= 0.5 : null
  const price = item?.american != null
    ? { text: `${item.selection ?? ''} ${am(item.american)}`, book: shortBook(item.sportsbook), when: item.quote_observed_at }
    : game?.best.AWAY && game.best.HOME
      ? { text: `${game.away?.abbreviation} ${am(game.best.AWAY.american)} · ${game.home?.abbreviation} ${am(game.best.HOME.american)}`, book: 'best current', when: game.best.HOME.observed_at }
      : null
  const marketP = item?.market_probability ?? (game?.consensus_is_current ? game.consensus_no_vig_home : null)
  const windowOpen = card.windowOpens ? Date.parse(card.windowOpens) <= now : false
  const started = card.startsAt ? Date.parse(card.startsAt) <= now : false
  const final = item?.phase === 'FINAL_PREGAME'
  const steps = [
    { label: 'Forecast', state: game ? (model ? 'done' : 'wait') : item ? 'done' : 'na' },
    { label: 'Price', state: game ? (game.price_state === 'CURRENT_PRICE' ? 'done' : game.price_state === 'STORED_REFERENCE_ONLY' ? 'stale' : 'wait') : item?.american != null ? 'done' : 'na' },
    { label: 'Evaluated', state: final && item?.recommendation !== 'NOT_YET_EVALUATED' ? 'done' : started ? 'miss' : 'wait' },
    { label: 'Qualified', state: final && item?.recommendation === 'QUALIFIED_BET' ? 'done' : final || started ? 'miss' : 'wait' },
    { label: 'Positioned', state: item?.position ? 'done' : started || (final && item?.recommendation !== 'QUALIFIED_BET') ? 'miss' : 'wait' },
  ]
  return (
    <article className={`cc-dcard cc-dcard--${card.status.tone}`} style={{ ['--sport' as string]: SPORT_COLORS[card.sport] }} aria-label={`${card.matchup}: ${card.status.label}`}>
      <header className="cc-dcard__head">
        <span className="cc-dcard__sport"><i className="cc-dot" />{SPORT_NAMES[card.sport]}</span>
        <span className="cc-dcard__time">{card.startsAt ? etTime(card.startsAt, !card.game) : card.nextLabel}</span>
      </header>
      <div className="cc-dcard__main">
        <div style={{ minWidth: 0 }}>
          <h3>{card.matchup}</h3>
          <span className={`cc-dcard__status cc-tone--${card.status.tone}`}>{card.status.label}</span>
        </div>
        <div className="cc-dcard__clock">
          {started ? <small>Started</small> : windowOpen
            ? <><small>Window closes</small><b>{countdown(card.windowCloses, now)}</b></>
            : <><small>Window opens</small><b>{countdown(card.windowOpens, now)}</b></>}
        </div>
      </div>
      <dl className="cc-dcard__facts">
        <div><dt>Model</dt>
          <dd>{homeP != null && game ? `${pickHome ? game.home?.abbreviation : game.away?.abbreviation} ${pct(pickHome ? homeP : 1 - homeP, 0)}` : item?.model_probability != null ? pct(item.model_probability, 0) : '—'}</dd>
          <small>{model?.expected_home != null && model.expected_away != null ? `${model.expected_away.toFixed(1)}–${model.expected_home.toFixed(1)} proj.` : model ? 'forecast' : 'no forecast yet'}</small></div>
        <div><dt>Price</dt><dd>{price ? price.text : '—'}</dd>
          <small>{price ? `${price.book}${price.when ? ` · ${relative(price.when)}` : ''}` : game?.price_state === 'STORED_REFERENCE_ONLY' ? 'stale reference only' : 'not collected'}</small></div>
        <div><dt>Market</dt><dd>{pct(marketP, 1)}</dd><small>no-vig</small></div>
        <div><dt>Edge · EV</dt><dd className={item?.edge != null ? (item.edge >= 0 ? 'is-up' : 'is-down') : ''}>{item?.edge != null ? `${(item.edge * 100).toFixed(1)} pts` : '—'}</dd>
          <small>{item?.expected_value != null ? `EV ${(item.expected_value * 100).toFixed(1)}%` : '—'}</small></div>
      </dl>
      {item?.position && (
        <p className="cc-dcard__entry">
          <b>Entry {item.selection} {am(item.american)}</b> · {shortBook(item.sportsbook)} · stake {money(item.position.stake)}
          {item.position.status === 'SETTLED'
            ? <> · <span className={(item.position.profit_loss ?? 0) >= 0 ? 'is-up' : 'is-down'}>{item.position.result} {signedMoney(item.position.profit_loss)}</span></>
            : ' · open'}
        </p>
      )}
      <ol className="cc-steps" aria-label="Readiness">
        {steps.map((s) => <li key={s.label} className={`cc-step cc-step--${s.state}`}>{s.label}</li>)}
      </ol>
      <footer className="cc-dcard__foot">
        {(card.status.detail || item) ? (
          <details>
            <summary>Details</summary>
            <p>{card.status.detail ?? 'No reason recorded.'}</p>
            {item && <p className="cc-dim">{item.experiment} · {item.phase.replace(/_/g, ' ').toLowerCase()} · stored {item.stored_recommendation}{item.recommended_at ? ` · ${etTime(item.recommended_at, true)}` : ''}</p>}
          </details>
        ) : <span />}
        <Link to={item?.position ? `/experiment?position=${item.position.position_id}` : card.href} className="cc-dcard__action">
          {item?.position ? 'View position' : card.sport === 'NBA' || card.sport === 'CFB' ? 'Research' : 'Analysis'} →
        </Link>
      </footer>
    </article>
  )
})

/** scheduled → forecast → priced → evaluated → qualified → positioned, by game. Compact. */
const DecisionFunnel = memo(function DecisionFunnel({ items, games }: { items: DecisionItem[]; games: CoverageGame[] }) {
  const reduced = useReducedMotion()
  const { ref, width } = useWidth<HTMLDivElement>(320)
  const { tip, show, hide, frame } = useTooltip()
  const bars = useRef<SVGGElement | null>(null)
  const stages = useMemo(() => {
    const ids = (pred: (i: DecisionItem) => boolean) => new Set(items.filter(pred).map((i) => i.game_id)).size
    return [
      { key: 'scheduled', label: 'Scheduled', value: games.length, note: 'Games on this slate.' },
      { key: 'forecast', label: 'Forecast', value: games.filter((g) => g.model).length, note: 'Controlling forecast published.' },
      { key: 'priced', label: 'Priced', value: games.filter((g) => g.price_state === 'CURRENT_PRICE').length, note: 'A current two-sided sportsbook price is stored.' },
      { key: 'evaluated', label: 'Evaluated', value: ids((i) => i.phase === 'FINAL_PREGAME' && i.recommendation !== 'NOT_YET_EVALUATED'), note: 'Final-pregame evaluation recorded.' },
      { key: 'qualified', label: 'Qualified', value: ids((i) => i.phase === 'FINAL_PREGAME' && i.recommendation === 'QUALIFIED_BET'), note: 'Cleared every policy gate.' },
      { key: 'positioned', label: 'Positioned', value: ids((i) => Boolean(i.position)), note: 'Virtual position recorded.' },
    ]
  }, [items, games])
  const max = Math.max(1, ...stages.map((s) => s.value))
  const rowH = 20
  const labelW = 78
  const height = stages.length * rowH
  const x = d3.scaleLinear().domain([0, max]).range([0, Math.max(40, width - labelW - 30)])

  useEffect(() => {
    if (!bars.current || reduced) return
    d3.select(bars.current).selectAll<SVGRectElement, unknown>('rect.cc-fbar')
      .attr('width', 0).transition().duration(500).ease(d3.easeCubicOut)
      .attr('width', function () { return Number((this as SVGRectElement).dataset.w) })
  }, [stages, width, reduced])

  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={height} role="img" aria-label={stages.map((s) => `${s.label} ${s.value}`).join(', ')}>
        <g ref={bars}>
          {stages.map((s, i) => {
            const w = s.value ? Math.max(3, x(s.value)) : 0
            const body = <><b>{s.label}: {s.value}</b><p style={{ margin: '3px 0 0' }}>{s.note}</p></>
            return (
              <g key={s.key} transform={`translate(0, ${i * rowH})`} tabIndex={0} className="cc-lane cc-lane--static"
                aria-label={`${s.label}: ${s.value}`} onMouseMove={(e) => show(e, body)} onMouseLeave={hide} onFocus={(e) => show(e, body)} onBlur={hide}>
                <rect x={0} y={0} width={width} height={rowH} className="cc-lane__hit" />
                <text x={labelW - 6} y={rowH / 2 + 4} textAnchor="end" className="cc-lane__label">{s.label}</text>
                <rect x={labelW} y={4} width={w} data-w={w} height={rowH - 8} rx={3} className={`cc-fbar cc-fbar--${i}`} />
                <text x={labelW + w + 6} y={rowH / 2 + 4} className="cc-funnel__value">{s.value}</text>
              </g>
            )
          })}
        </g>
      </svg>
      <Tooltip tip={tip} width={width} />
    </div>
  )
})

function SupportingVisuals({ items, ledger }: { items: DecisionItem[]; ledger: Ledger | null }) {
  const comparable = items.filter((i) => i.model_probability != null && i.market_probability != null).length
  const settled = (ledger?.sports ?? []).flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport })))
    .filter((p) => p.status === 'SETTLED').sort((a, b) => (b.settled_at ?? '').localeCompare(a.settled_at ?? '')).slice(0, 16)
  return (
    <div className="cc-support">
      <section className="cc-support__panel" aria-label="Results tape">
        <h3>Results tape · latest settlements</h3>
        {settled.length === 0 ? <p className="cc-muted">No settled position yet.</p> : (
          <ol className="cc-tape">
            {settled.map((p) => (
              <li key={p.position_id} style={{ ['--sport' as string]: SPORT_COLORS[p.sport] }}>
                <Link to={`/experiment?position=${p.position_id}`}
                  className={(p.profit_loss ?? 0) > 0 ? 'is-win' : (p.profit_loss ?? 0) < 0 ? 'is-loss' : 'is-push'}
                  title={`${p.selection} ${am(p.entry.american)} · ${p.matchup} · ${p.result} ${signedMoney(p.profit_loss)}`}>
                  <i className="cc-dot" /><b>{(p.selection ?? '').replace(/ ML$/, '')}</b><span>{signedMoney(p.profit_loss)}</span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </section>
      {comparable > 0 ? (
        <section className="cc-support__panel" aria-label="Model versus market">
          <h3>Model vs market · {comparable} comparable evaluations</h3>
          <DeferUntilVisible minHeight={300}><EdgeMap items={items} /></DeferUntilVisible>
        </section>
      ) : (
        <section className="cc-support__panel cc-support__panel--compact" aria-label="Model versus market">
          <h3>Model vs market</h3>
          <p className="cc-muted">Appears when a forecast and a stored sportsbook price meet in an evaluation.</p>
        </section>
      )}
      <ChartTable caption="Settled positions">
        <thead><tr><th>Sport</th><th>Selection</th><th>Game</th><th>Result</th><th>P&amp;L</th></tr></thead>
        <tbody>{settled.map((p) => <tr key={p.position_id}><td>{p.sport}</td><td>{p.selection}</td><td>{p.matchup}</td><td>{p.result}</td><td>{signedMoney(p.profit_loss)}</td></tr>)}</tbody>
      </ChartTable>
    </div>
  )
}
