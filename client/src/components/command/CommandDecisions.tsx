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
 * Above the queue, the consumer assessment cards (qualified, closest,
 * passed, research) and the same-game Parlay Lab read the verified-population
 * assessments; the queue itself is the collapsible audit view below them.
 *
 * Only funded-era governed evaluations appear (the read model scopes them to
 * their own sport's games); legacy/research experiment rows stay in the audit
 * view below the page. Internal codes are translated into short language, with
 * the raw evaluation available on demand.
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
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
import { buildAgenda, nextAttention, KIND_LABEL, type AgendaEvent } from '../../services/agenda'
import { useAssessments } from '../../services/probability'
import { AssessmentBoard, classifyAssessments } from './AssessmentBoard'
import { ParlayLab } from './ParlayLab'
import { AllMarkets, MyCard } from './MarketsAndCard'
import { useMyCard } from '../../services/myCard'
import './command.css'

type Tab = 'ALL' | 'ACTIONABLE' | 'POSITIONS' | 'WATCHING' | 'PASSED' | 'COMPLETED'
type View = 'decisions' | 'markets' | 'card'
const VIEWS: { key: View; label: string }[] = [
  { key: 'decisions', label: 'AISP Decisions' },
  { key: 'markets', label: 'All Markets' },
  { key: 'card', label: 'My Card' },
]
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
  /** For a sport's next game off this slate: its readiness entry, which is the
   *  only record of whether its forecast and price exist. */
  readiness?: SportReadinessEntry | null
}

/** A readiness forecast state that means a forecast is actually held. */
const forecastHeld = (state: string | undefined) => Boolean(state && /AVAILABLE$/.test(state) && !/AWAITING|UNAVAILABLE/.test(state))

/** Plain label for a readiness price state; never implies a sportsbook did not post a line. */
function readinessPriceLabel(state: string | undefined): string {
  switch (state) {
    case 'FRESH_PRICE': return 'Market collected'
    case 'STORED_REFERENCE_ONLY': return 'Stale reference only'
    case 'COLLECTION_BLOCKED': return 'Price intake blocked'
    case 'COLLECTION_NOT_OPEN': return 'Collection not open'
    default: return 'Collection scheduled'
  }
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const am = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

/** Why a final evaluation passed, from its recorded reason; "edge below policy" only when that is the reason. */
export function passedLabel(reason: string | null): string {
  const text = reason ?? ''
  if (/stale|not current|freshness/i.test(text)) return 'Passed · price not current'
  if (/eligib|TIER_[A-Z]_INELIGIBLE/i.test(text)) return 'Passed · not eligible'
  if (/missing|unavailable|no (?:quote|price)/i.test(text)) return 'Passed · market incomplete'
  return 'Passed · edge below policy'
}

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
  if (rec === 'NO_BET' && item?.phase === 'FINAL_PREGAME') return { label: passedLabel(reason), tone: 'red', detail: reason }
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
    const priceBlocker = typeof entry.price?.blocker === 'string' ? entry.price.blocker : null
    cards.push({
      key: `next-${code}`, sport: code,
      href: code === 'NBA' ? '/nba/research' : code === 'CFB' ? '/college-football/research' : gameHref(code, entry.next_game.game_id),
      matchup: entry.next_game.matchup, startsAt: (entry.next_game as { starts_at?: string }).starts_at ?? null,
      windowOpens: window?.opens_at ?? null, windowCloses: window?.closes_at ?? null, tab: 'WATCHING',
      // Each label names what is actually true: a blocked comparison says it is
      // blocked and why; the NBA regular season has not started, so its game is
      // a research example, not a live slate.
      status: code === 'NBA'
        ? { label: 'Season not started · research example', tone: 'gray', detail: entry.price?.label ?? entry.pipeline.blocker }
        : blocked
          ? { label: 'Market comparison blocked', tone: 'gray', detail: entry.pipeline.blocker }
          : priceBlocker
            ? { label: 'Price intake blocked', tone: 'gray', detail: entry.price?.label ?? priceBlocker }
            : { label: 'Next scheduled decision', tone: 'gold', detail: entry.pipeline.blocker ?? entry.price?.label ?? null },
      item: null, game: null, nextLabel: entry.next_game.starts_label, readiness: entry,
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

const STAGES = ['Scheduled', 'Forecast', 'Market', 'Evaluation', 'Position', 'Settled'] as const

/** How far a game has progressed (0-based index into STAGES) and each stage's state. */
export function progress(card: FeedCard, now: number): { reached: number; states: ('done' | 'wait' | 'miss' | 'na')[]; labels: string[] } {
  const { item, game } = card
  const started = card.startsAt ? Date.parse(card.startsAt) <= now : false
  const held = card.readiness ? forecastHeld(card.readiness.forecast?.state) : null
  const forecast = held ?? (game ? Boolean(game.model) : Boolean(item?.model_probability != null))
  const priced = card.readiness
    ? card.readiness.price?.state === 'FRESH_PRICE'
    : game ? game.price_state === 'CURRENT_PRICE' || (game.quotes.length > 0 && started) : item?.american != null
  const evaluated = item?.phase === 'FINAL_PREGAME' && item.recommendation !== 'NOT_YET_EVALUATED'
  const positioned = Boolean(item?.position)
  const settled = item?.position?.status === 'SETTLED'
  const states: ('done' | 'wait' | 'miss' | 'na')[] = [
    'done',
    forecast ? 'done' : started ? 'miss' : 'wait',
    priced ? 'done' : started ? 'miss' : 'wait',
    evaluated ? 'done' : started ? 'miss' : 'wait',
    positioned ? 'done' : evaluated || started ? 'na' : 'wait',
    settled ? 'done' : positioned ? 'wait' : 'na',
  ]
  const labels = [
    'Scheduled',
    forecast ? 'Forecast available' : started ? 'No forecast' : 'Forecast preparing',
    card.readiness ? readinessPriceLabel(card.readiness.price?.state)
      : priced ? 'Market collected' : game?.quotes.length ? 'Stale reference only' : started ? 'No price collected' : 'Collection scheduled',
    evaluated ? (item?.recommendation === 'QUALIFIED_BET' ? 'Qualified' : item?.recommendation === 'NO_BET' ? 'Evaluated · no bet' : 'Evaluated') : started ? 'Not evaluated' : 'Evaluation pending',
    positioned ? 'Position recorded' : 'No position',
    settled ? `Settled ${item?.position?.result ?? ''}` : positioned ? 'Awaiting settlement' : '—',
  ]
  let reached = 0
  states.forEach((s, i) => { if (s === 'done') reached = i })
  return { reached, states, labels }
}

export function CommandDecisions({ day, initialSport }: { day: string; initialSport?: SportCode }) {
  const board = useDecisionBoard(day)
  const coverage = useMarketCoverage(day)
  const ledger = useLedger()
  const readiness = useSportReadiness()
  const now = useNow()
  const [sport, setSport] = useState<SportCode | 'ALL'>(initialSport ?? 'ALL')

  const items = useMemo(() => board.data?.items ?? [], [board.data])
  const games = useMemo(() => coverage.data?.games ?? [], [coverage.data])
  const readinessSports = useMemo(() => readiness.data?.sports ?? [], [readiness.data])
  const isToday = day === todayEt()
  const feed = useMemo(() => buildFeed(items, games, isToday ? readinessSports : [], now), [items, games, readinessSports, isToday, now])
  const agenda = useMemo(() => buildAgenda(games, items, ledger.data, now), [games, items, ledger.data, now])
  const visible = feed.filter((c) => sport === 'ALL' || c.sport === sport)
  const assessments = useAssessments(day)
  const groups = useMemo(() => classifyAssessments(visible, assessments.data, now), [visible, assessments.data, now])
  const research = (assessments.data?.research ?? []).filter((r) => sport === 'ALL' || r.sport === sport)
  const [params, setParams] = useSearchParams()
  const view: View = VIEWS.some((v) => v.key === params.get('view')) ? params.get('view') as View : 'decisions'
  const setView = (next: View) => setParams((old) => {
    const q = new URLSearchParams(old)
    if (next === 'decisions') q.delete('view')
    else q.set('view', next)
    q.delete('replay')  // leaving My Card always leaves the historical replay
    return q
  }, { replace: true })
  // Historical replay lives only inside My Card and is always visible in the URL.
  const replay = view === 'card' && params.get('replay') === '1'
  const setReplay = (on: boolean) => setParams((old) => {
    const q = new URLSearchParams(old)
    q.set('view', 'card')
    if (on) q.set('replay', '1')
    else q.delete('replay')
    return q
  })
  // Fixture-backed player-prop interface preview; never live, never selectable.
  const propsPreview = params.get('preview') === 'player-props'
  const [labGame, setLabGame] = useState<string | null>(null)
  const picks = useMyCard()
  const marketGames = useMemo(() => {
    const byId = new Map((assessments.data?.games ?? []).map((g) => [g.game_id, g]))
    return games.filter((g) => sport === 'ALL' || g.league === sport)
      .map((g) => ({ gameId: g.game_id, game: g, assessment: byId.get(g.game_id) ?? null }))
  }, [games, assessments.data, sport])
  const openLab = (gameId: string) => {
    setLabGame(gameId)
    window.setTimeout(() => document.getElementById('parlay-lab')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  // A game graduates from an agenda row to a decision card once there is a
  // decision to read: a final evaluation, a position, or a started game.
  const isCard = (c: FeedCard) => Boolean(c.item?.position) || c.item?.phase === 'FINAL_PREGAME'
    || (c.startsAt != null && Date.parse(c.startsAt) <= now && Boolean(c.game))
  const actionable = visible.filter((c) => c.tab === 'ACTIONABLE' || c.tab === 'POSITIONS')
  const completed = visible.filter((c) => isCard(c) && !actionable.includes(c))
  const waiting = visible.filter((c) => !isCard(c) && !actionable.includes(c))
  // Open windows are "requires attention now"; "what's next" is the next milestone that has not begun.
  const upcoming = nextAttention(agenda.filter((e) => (sport === 'ALL' || e.sport === sport) && e.status !== 'open'), now, 1)
  const settledToday = (ledger.data?.sports ?? []).flatMap((s) => s.positions)
    .filter((pos) => pos.status === 'SETTLED' && pos.settled_at
      && new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(pos.settled_at)) === day)
  const todayPnl = settledToday.reduce((s, pos) => s + (pos.profit_loss ?? 0), 0)
  const qualifiedPositions = visible.filter((c) => c.item?.position && c.item.position.status !== 'SETTLED').length
  const openWindows = agenda.filter((e) => e.status === 'open' && (e.kind === 'decision' || e.kind === 'collection'))
  const sportsInFeed = SPORT_CODES.filter((c) => feed.some((f) => f.sport === c))
  const nextByGame = useMemo(() => {
    const out = new Map<string, AgendaEvent>()
    for (const e of agenda) {
      if (e.status === 'done' || e.status === 'missed') continue
      if (e.status === 'open' || e.at >= now) { if (!out.has(e.gameId)) out.set(e.gameId, e) }
    }
    return out
  }, [agenda, now])

  return (
    <section className="cc-decisions" aria-label="Daily Decisions">
      <section className="cc-dark cc-dnow" aria-label="Decisions now">
        <div className="cc-dnow__head">
          <span className="cc-kicker">Daily Decisions · {new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })}</span>
          <AsOf resource={board} label="decisions" />
        </div>
        <div className="cc-dnow__grid">
          <div className="cc-dnow__block cc-dnow__block--wide">
            <small>Requires attention now</small>
            {actionable.length
              ? <b className="is-up">{actionable.length} {actionable.length === 1 ? 'decision' : 'decisions'} actionable</b>
              : openWindows.length
                ? <b className="is-gold">{openWindows.length} window{openWindows.length === 1 ? '' : 's'} open</b>
                : <b>Nothing right now</b>}
            <span>{actionable[0] ? `${actionable[0].matchup} · ${actionable[0].status.label}` : openWindows[0] ? `${openWindows[0].matchup} · ${openWindows[0].label}` : 'No decision or collection window is open'}</span>
          </div>
          <div className="cc-dnow__block cc-dnow__block--wide">
            <small>What’s next</small>
            {upcoming[0]
              ? <><b className="is-gold">{KIND_LABEL[upcoming[0].event.kind]} · {upcoming[0].event.at <= now ? 'due now' : `in ${countdown(new Date(upcoming[0].event.at).toISOString(), now)}`}</b>
                <span>{etTime(new Date(upcoming[0].event.at).toISOString())} · {upcoming[0].matchups.length > 1 ? `${upcoming[0].matchups.length} games` : upcoming[0].matchups[0]}</span></>
              : <><b>No milestone scheduled</b><span>Today’s slate is complete</span></>}
          </div>
          <div className="cc-dnow__block"><small>Qualified positions</small><b className={qualifiedPositions ? 'is-up' : ''}>{qualifiedPositions}</b><span>open</span></div>
          <div className="cc-dnow__block"><small>Virtual exposure</small><b>{money(ledger.data?.totals.open_exposure)}</b><span>{ledger.data?.totals.open_count ?? 0} open</span></div>
          <div className="cc-dnow__block"><small>{isToday ? 'Completed today' : 'Completed this slate day'}</small><b className={todayPnl > 0 ? 'is-up' : todayPnl < 0 ? 'is-down' : ''}>{signedMoney(todayPnl)}</b><span>{settledToday.length} settled · {completed.length} decided</span></div>
        </div>
      </section>

      {(sportsInFeed.length > 1 || sport !== 'ALL') && (
        <div className="cc-filters" role="group" aria-label="Sport">
          {(['ALL', ...new Set([...sportsInFeed, ...(sport === 'ALL' ? [] : [sport])])] as const).map((code) => (
            <button key={code} type="button" aria-pressed={sport === code} onClick={() => setSport(code)}>
              {code === 'ALL' ? 'All sports' : SPORT_NAMES[code]}
            </button>
          ))}
        </div>
      )}

      {!board.data && !coverage.data && (
        <p className="cc-empty" role="status">{board.error ? `Decisions are unavailable: ${board.error}` : 'Loading decisions…'}</p>
      )}

      <div className="dv-tabs" role="tablist" aria-label="Daily Decisions views">
        {VIEWS.map((v) => (
          <button key={v.key} type="button" role="tab" id={`dv-tab-${v.key}`} aria-controls={`dv-panel-${v.key}`}
            aria-selected={view === v.key} tabIndex={view === v.key ? 0 : -1} onClick={() => setView(v.key)}
            onKeyDown={(e) => {
              const i = VIEWS.findIndex((x) => x.key === view)
              if (e.key === 'ArrowRight') setView(VIEWS[(i + 1) % VIEWS.length].key)
              if (e.key === 'ArrowLeft') setView(VIEWS[(i + VIEWS.length - 1) % VIEWS.length].key)
            }}>
            {v.label}{v.key === 'card' && picks.length > 0 ? ` (${picks.length})` : ''}
          </button>
        ))}
      </div>
      {assessments.error && <p className="cc-empty" role="status">Model assessments are unavailable: {assessments.error}</p>}

      {view === 'markets' && (
        <div role="tabpanel" id="dv-panel-markets" aria-labelledby="dv-tab-markets">
          <AllMarkets games={marketGames} data={assessments.data} now={now} onOpenLab={openLab} preview={propsPreview} />
          <div id="parlay-lab"><ParlayLab data={assessments.data} coverage={games} focusGameId={labGame} /></div>
        </div>
      )}

      {view === 'card' && (
        <div role="tabpanel" id="dv-panel-card" aria-labelledby="dv-tab-card">
          <MyCard now={now} replay={replay} onReplay={setReplay} />
        </div>
      )}

      {view === 'decisions' && (
      <div role="tabpanel" id="dv-panel-decisions" aria-labelledby="dv-tab-decisions">
      <p className="dv-authority" role="note">
        <b>Official AISP decisions.</b> Produced automatically by the governed decision policy. They cannot be edited, overridden
        or deleted here, and your own picks (My Card) never change them or the $1,000 Experiment.
      </p>
      <AssessmentBoard groups={groups} research={research} day={day} loading={!assessments.data && !assessments.error} />

      <details className="as-audit" open>
        <summary>Audit view · full timing and readiness queue</summary>
      {actionable.length > 0 && (
        <section aria-label="Actionable and open positions">
          <h2 className="cc-section-title">Actionable · open positions <span>{actionable.length}</span></h2>
          <div className="cc-feed">{actionable.map((c) => <DecisionCard key={c.key} card={c} now={now} />)}</div>
        </section>
      )}

      {waiting.length > 0 && (
        <section className="cc-agenda-rows" aria-label="Upcoming decisions">
          <h2 className="cc-section-title">Upcoming · by next required event <span>{waiting.length}</span></h2>
          <div className="cc-rows" role="table" aria-label="Upcoming decisions">
            <div className="cc-rows__head" role="row">
              <span role="columnheader">Start</span><span role="columnheader">Game</span><span role="columnheader">Progress</span>
              <span role="columnheader">Forecast</span><span role="columnheader">Collection</span><span role="columnheader">Decision window</span>
              <span role="columnheader">Next required event</span>
            </div>
            {waiting.map((c) => <AgendaRow key={c.key} card={c} now={now} next={nextByGame.get(c.key) ?? null} />)}
          </div>
        </section>
      )}

      {completed.length > 0 && (
        <section aria-label="Completed decisions">
          <h2 className="cc-section-title">Completed today <span>{completed.length}</span></h2>
          <div className="cc-feed">{completed.map((c) => <DecisionCard key={c.key} card={c} now={now} />)}</div>
        </section>
      )}

      <SupportingVisuals items={items} ledger={ledger.data} />
      </details>
      </div>
      )}
    </section>
  )
}

const AgendaRow = memo(function AgendaRow({ card, now, next }: { card: FeedCard; now: number; next: AgendaEvent | null }) {
  const { game } = card
  const pr = progress(card, now)
  const model = game?.model
  const homeP = model?.home_two_way ?? null
  return (
    <Link role="row" to={card.href} className="cc-row2" style={{ ['--sport' as string]: SPORT_COLORS[card.sport] }}
      aria-label={`${card.matchup}, ${pr.labels[pr.reached]}`}>
      <span role="cell" className="cc-row2__time"><i className="cc-dot" />{card.startsAt ? etTime(card.startsAt).replace(' ET', '') : card.nextLabel?.split(', ').slice(-1)[0]}</span>
      <span role="cell" className="cc-row2__game"><b>{card.matchup}</b><small>{SPORT_NAMES[card.sport]}{card.game ? '' : ` · ${card.nextLabel ?? ''}`}</small></span>
      <span role="cell" className="cc-row2__progress" title={pr.labels.join(' → ')}>
        {pr.states.map((s, i) => <i key={STAGES[i]} className={`cc-pip cc-pip--${s}`} aria-hidden="true" />)}
        <small>{pr.labels[Math.min(pr.reached + 1, 5)]}</small>
      </span>
      <span role="cell">{model && homeP != null
        ? <><b>{homeP >= 0.5 ? game?.home?.abbreviation : game?.away?.abbreviation} {pct(homeP >= 0.5 ? homeP : 1 - homeP, 0)}</b><small>{model.expected_away != null && model.expected_home != null ? `${model.expected_away.toFixed(1)}–${model.expected_home.toFixed(1)}` : 'forecast'}</small></>
        : card.readiness
          ? <span title={card.readiness.forecast?.label}>{forecastHeld(card.readiness.forecast?.state) ? <b>Forecast held</b> : <span className="is-gold">Awaiting forecast</span>}</span>
          : <span className="is-gold">{card.game ? 'Preparing' : card.status.label}</span>}</span>
      <span role="cell">{game
        ? game.price_state === 'CURRENT_PRICE' ? <b className="is-up">Collected</b>
          : <><b>{etTime(game.checkpoints.collection_opens_at).replace(' ET', '')}</b><small>{Date.parse(game.checkpoints.collection_opens_at) > now ? `in ${countdown(game.checkpoints.collection_opens_at, now)}` : 'open'}</small></>
        : card.readiness
          ? <span title={card.readiness.price?.label}>{readinessPriceLabel(card.readiness.price?.state)}</span>
          : '—'}</span>
      <span role="cell">{card.windowOpens ? <><b>{etTime(card.windowOpens).replace(' ET', '')}–{etTime(card.windowCloses).replace(' ET', '')}</b><small>{Date.parse(card.windowOpens) > now ? `in ${countdown(card.windowOpens, now)}` : 'open'}</small></> : '—'}</span>
      <span role="cell" className="cc-row2__next">{next
        ? <><b>{next.label}</b><small>{next.status === 'open' ? `closes in ${countdown(new Date(next.until ?? next.at).toISOString(), now)}` : `${etTime(new Date(next.at).toISOString()).replace(' ET', '')} · in ${countdown(new Date(next.at).toISOString(), now)}`}</small></>
        : <b title={card.status.detail ?? undefined}>{card.status.label}</b>}</span>
    </Link>
  )
})

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
  // Both sides of the market the decision was priced from, same book only.
  const bookKey = item?.sportsbook ?? null
  const twoSided = game?.quotes.filter((q) => q.market === 'MONEYLINE' && (!bookKey || shortBook(q.sportsbook).toLowerCase() === shortBook(bookKey).toLowerCase()))
    .sort((a, b) => b.observed_at.localeCompare(a.observed_at))[0] ?? null
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
      {twoSided && (
        <p className="cc-dcard__market">
          Market · {game?.away?.abbreviation} {am(twoSided.sides.AWAY?.american)} / {game?.home?.abbreviation} {am(twoSided.sides.HOME?.american)} at {shortBook(twoSided.sportsbook)} ({twoSided.provider}, {twoSided.classification.toLowerCase()}) · observed {relative(twoSided.observed_at)}{twoSided.fresh ? '' : ' · not current'}
        </p>
      )}
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
