/**
 * Daily Decisions, consumer view: the day's games as assessment cards.
 *
 *   Qualified decisions      -- qualifying now, or a position was recorded (open or settled)
 *   Closest to qualifying    -- not decided yet, ranked by the best governed edge
 *   Useful passed decisions  -- final evaluations that did not qualify
 *   Research comparisons     -- NBA / College Football, research authority only
 *
 * Each game card leads with its top opportunity (the official AISP pick, or the
 * best current model value, or a plain statement of why there is none), then
 * the forecast, then every market as ranked outcome rows behind a disclosure.
 *
 * Every number is a stored or server-computed value. Prices are the stored
 * quote pair for that book; a side or a line that was not quoted is shown as
 * missing, never inferred. Model probabilities come from the verified paired
 * population (win / loss / push mass). An edge or expected return appears only
 * where the server assessed one: a current, governed, two-sided quote.
 */
import { memo, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { TeamLogo } from '../TeamLogo'
import {
  SPORT_COLORS, SPORT_NAMES, etTime, gameHref, relative,
  type CoverageGame, type DecisionItem, type SportCode,
} from '../../services/commandCenter'
import {
  AUTHORITY_TEXT, EDGE_REASON_TEXT, american, pct,
  type AssessedQuote, type AssessedSide, type Channel, type DailyAssessments, type GameAssessment,
  type ResearchComparison,
} from '../../services/probability'
import { topPlay } from '../../services/topPlay'
import { marketLine, modelLine, type StatusLine } from '../../services/pregameStatus'
import { inputsLine, otherFigures, testLine, type EdgeMetrics } from '../../services/decisionEdges'
import { MarketSections, TopOpportunity } from './TopOpportunity'
import './assessments.css'

type Tone = 'green' | 'gold' | 'red' | 'gray'

/** The minimum a card needs from the operational feed (see CommandDecisions.buildFeed). */
export interface AssessmentSource {
  key: string
  sport: SportCode
  matchup: string
  startsAt: string | null
  tab: 'ACTIONABLE' | 'POSITIONS' | 'WATCHING' | 'PASSED' | 'COMPLETED'
  status: { label: string; tone: Tone; detail: string | null }
  item: DecisionItem | null
  game: CoverageGame | null
}

export type AssessmentGroup = 'QUALIFIED' | 'CLOSEST' | 'PASSED'

export interface AssessmentCardModel extends AssessmentSource {
  group: AssessmentGroup
  assessment: GameAssessment | null
  bestEdge: number | null
}

export const GROUP_TITLE: Record<AssessmentGroup, string> = {
  QUALIFIED: 'Qualified decisions',
  CLOSEST: 'Closest to qualifying',
  PASSED: 'Useful passed decisions',
}

const MARKETS: { key: string; label: string; sides: [string, string] }[] = [
  { key: 'MONEYLINE', label: 'Moneyline', sides: ['AWAY', 'HOME'] },
  { key: 'SPREAD', label: 'Spread', sides: ['AWAY', 'HOME'] },
  { key: 'TOTAL', label: 'Total', sides: ['OVER', 'UNDER'] },
]

const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const marketOf = (q: AssessedQuote) => (q.market === 'RUN_LINE' ? 'SPREAD' : q.market)

/** The one quote a card shows per market: current and governed first, then the newest. */
export function primaryQuote(quotes: AssessedQuote[], market: string, preferBook?: string | null): AssessedQuote | null {
  const pool = quotes.filter((q) => marketOf(q) === market)
  if (!pool.length) return null
  const book = (preferBook ?? '').toLowerCase()
  const rank = (q: AssessedQuote) => (q.state === 'CURRENT' ? 4 : 0) + (q.classification === 'GOVERNED' ? 2 : 0)
    + (book && (q.sportsbook ?? '').toLowerCase().startsWith(book) ? 1 : 0)
  return [...pool].sort((a, b) => rank(b) - rank(a) || b.observed_at.localeCompare(a.observed_at))[0]
}

/** Best research edge the server assessed for a game (current governed quotes only). */
export function bestAssessedEdge(assessment: GameAssessment | null): number | null {
  let best: number | null = null
  for (const q of assessment?.quotes ?? []) {
    for (const side of Object.values(q.sides)) {
      if (side.edge != null && (best == null || side.edge > best)) best = side.edge
    }
  }
  return best
}

export function classifyAssessments(feed: AssessmentSource[], data: DailyAssessments | null, now: number): Record<AssessmentGroup, AssessmentCardModel[]> {
  const byGame = new Map((data?.games ?? []).map((g) => [g.game_id, g]))
  const out: Record<AssessmentGroup, AssessmentCardModel[]> = { QUALIFIED: [], CLOSEST: [], PASSED: [] }
  for (const card of feed) {
    if (!card.game) continue // off-slate "next game" rows stay in the audit queue
    const assessment = byGame.get(card.key) ?? null
    const started = card.startsAt ? Date.parse(card.startsAt) <= now : false
    const item = card.item
    const bestEdge = item?.edge ?? bestAssessedEdge(assessment)
    const final = item?.phase === 'FINAL_PREGAME'
    let group: AssessmentGroup | null = null
    // A qualified decision stays in the story after its position settles.
    if (card.tab === 'ACTIONABLE' || card.tab === 'POSITIONS' || item?.position) group = 'QUALIFIED'
    else if (final && item && ['NO_BET', 'BLOCKED'].includes(item.recommendation)) group = 'PASSED'
    else if (!started) group = 'CLOSEST'
    if (group) out[group].push({ ...card, group, assessment, bestEdge })
  }
  const byEdge = (a: AssessmentCardModel, b: AssessmentCardModel) =>
    (b.bestEdge ?? -Infinity) - (a.bestEdge ?? -Infinity) || (a.startsAt ?? '').localeCompare(b.startsAt ?? '')
  out.CLOSEST.sort(byEdge)
  out.PASSED.sort(byEdge)
  out.CLOSEST = out.CLOSEST.slice(0, 6)
  out.PASSED = out.PASSED.slice(0, 4)
  return out
}

const edgeText = (side: AssessedSide | undefined) => {
  if (!side) return '—'
  if (side.edge == null) return '—'
  return `${side.edge >= 0 ? '+' : '−'}${Math.abs(side.edge * 100).toFixed(1)} pts`
}

function sideName(sideKey: string, game: CoverageGame | null): string {
  if (sideKey === 'HOME') return game?.home?.abbreviation ?? 'Home'
  if (sideKey === 'AWAY') return game?.away?.abbreviation ?? 'Away'
  return sideKey === 'OVER' ? 'Over' : 'Under'
}

function lineFor(market: string, sideKey: string, line: number | null): string {
  if (line == null || market === 'MONEYLINE') return ''
  if (market === 'TOTAL') return ` ${line}`
  const signed = sideKey === 'HOME' ? line : -line
  return ` ${signed > 0 ? '+' : ''}${signed}`
}

export function MarketBlock({ market, quote, game }: { market: typeof MARKETS[number]; quote: AssessedQuote | null; game: CoverageGame | null }) {
  if (!quote) {
    return (
      <div className="as-market as-market--empty" aria-label={`${market.label}: not quoted`}>
        <h5>{market.label}</h5>
        <p>No {market.label.toLowerCase()} quote stored</p>
      </div>
    )
  }
  const state = quote.state === 'CURRENT' ? 'Current' : 'Stored reference only'
  const reasons = new Set(Object.values(quote.sides).map((s) => s.edge_reason).filter(Boolean) as string[])
  // Without a verified population only the market's own numbers exist.
  const modeled = Object.values(quote.sides).some((s) => s.model)
  const rows = modeled ? ['Price', 'Model', 'No-vig', 'Fair', 'Push', 'Model − no-vig · return'] : ['Price', 'No-vig']
  return (
    <div className={`as-market ${quote.state === 'CURRENT' ? '' : 'is-stale'}`} aria-label={`${market.label} at ${quote.sportsbook ?? 'book'}`}>
      <h5>{market.label}<span className={`as-state ${quote.state === 'CURRENT' ? 'is-current' : 'is-stale'}`}>{state}</span></h5>
      <p className="as-market__src">{shortBook(quote.sportsbook) || 'Sportsbook'} · {quote.provider ?? 'provider'}{quote.classification === 'RESEARCH' ? ' (research-classified)' : ''} · {etTime(quote.observed_at, true)}</p>
      <table>
        <thead><tr><th scope="col"><span className="sr-only">Measure</span></th>
          {market.sides.map((s) => <th key={s} scope="col">{sideName(s, game)}{lineFor(market.key, s, quote.line)}</th>)}</tr></thead>
        <tbody>
          <tr><th scope="row">Price</th>{market.sides.map((s) => <td key={s} className="as-price">{quote.sides[s] ? american(quote.sides[s].american) : <span className="as-missing">not quoted</span>}</td>)}</tr>
          {rows.includes('Model') && <tr><th scope="row">Model</th>{market.sides.map((s) => <td key={s}>{pct(quote.sides[s]?.model?.conditional)}</td>)}</tr>}
          <tr><th scope="row">No-vig</th>{market.sides.map((s) => <td key={s}>{pct(quote.sides[s]?.no_vig)}</td>)}</tr>
          {modeled && <tr><th scope="row">Fair</th>{market.sides.map((s) => <td key={s}>{american(quote.sides[s]?.model?.fair_american)}</td>)}</tr>}
          {modeled && <tr><th scope="row">Push</th>{market.sides.map((s) => <td key={s}>{quote.sides[s]?.model ? pct(quote.sides[s].model?.push) : '—'}</td>)}</tr>}
          {modeled && <tr><th scope="row" title="Model minus the no-vig market, then the expected return per $1 at the offered price">Model − no-vig · return</th>{market.sides.map((s) => {
            const side = quote.sides[s]
            return <td key={s} className={side?.edge != null ? (side.edge >= 0 ? 'is-up' : 'is-down') : ''}>
              {edgeText(side)}{side?.expected_return != null ? <small> · return {(side.expected_return * 100).toFixed(1)}%</small> : null}
            </td>
          })}</tr>}
        </tbody>
      </table>
      {[...reasons].filter((r) => modeled || r !== 'NO_VERIFIED_POPULATION').map((r) => <p key={r} className="as-market__note">{EDGE_REASON_TEXT[r] ?? r}</p>)}
    </div>
  )
}

function ForecastStrip({ channel, game, sport, labels }: {
  channel: Channel | null; game: CoverageGame | null; sport: SportCode; labels?: { away: string; home: string }
}) {
  const model = game?.model ?? null
  const winner = channel?.winner ?? (model ? { home: model.home, away: model.away, tie: null } : null)
  const expected = channel?.expected_score ?? (model ? { home: model.expected_home ?? null, away: model.expected_away ?? null } : null)
  const away = labels?.away ?? game?.away?.abbreviation ?? 'Away'
  const home = labels?.home ?? game?.home?.abbreviation ?? 'Home'
  const authority = channel?.authority ? AUTHORITY_TEXT[channel.authority] ?? channel.authority : null
  const created = channel?.forecast_created_at ?? model?.created_at ?? null
  return (
    <div className="as-forecast">
      <div><small>Forecast</small>
        <b>{authority ?? (model ? 'Stored forecast' : 'No forecast yet')}</b>
        <span title="AISP's row timestamp: the start of the import transaction, not the producer's creation time and not an exact insert or commit time">{created ? `import began ${relative(created)}` : '—'}{channel?.horizon ? ` · ${channel.horizon.replace(/_/g, ' ').toLowerCase()}` : ''}</span></div>
      <div><small>Expected score</small>
        <b>{expected?.away != null && expected.home != null ? `${away} ${expected.away.toFixed(1)} – ${home} ${expected.home.toFixed(1)}` : '—'}</b></div>
      <div><small>Win probability</small>
        <b>{winner ? `${away} ${pct(winner.away)} · ${home} ${pct(winner.home)}` : '—'}</b>
        <span>{winner?.tie ? `Tie ${pct(winner.tie)}` : sport === 'NFL' ? 'Tie mass shown when modeled' : 'No tie outcome'}</span></div>
      {channel?.status === 'UNSUPPORTED' && channel.error && (
        <p className="as-forecast__warn">Line probabilities unavailable: {channel.error.message}</p>
      )}
    </div>
  )
}

const LINE_TONE: Record<StatusLine['tone'], Tone> = { good: 'green', watch: 'gold', bad: 'red', neutral: 'gray' }

/** Market and model, side by side and independent: one never stands in for the other. */
export function PregameStatus({ game }: { game: CoverageGame | null }) {
  const market = marketLine(game?.market_status)
  const model = modelLine(game?.model_status, game?.league === 'NFL' ? 'M1' : 'V18')
  if (!market && !model) return null
  const rows: [string, StatusLine | null][] = [['Market', market], ['Model', model]]
  return (
    <div className="as-pregame" aria-label="Market and model status">
      {rows.map(([title, line]) => line && (
        <div key={title}>
          <small>{title}</small>
          <b className={`cc-tone--${LINE_TONE[line.tone]}`}>{line.label}</b>
          <span>{line.detail}</span>
        </div>
      ))}
    </div>
  )
}

/** The policy's own tests, then the inputs, then any other recorded figure -- each named. */
function EdgeTests({ metrics, book }: { metrics: EdgeMetrics; book: string }) {
  const others = otherFigures(metrics)
  return (
    <div className="as-edges" aria-label="Policy tests">
      {metrics.tests.map((t) => <p key={t.metric} className={t.passed ? 'is-pass' : 'is-fail'}>{testLine(t)}</p>)}
      <p className="as-dim">{inputsLine(metrics)} · {book}</p>
      {others && <p className="as-dim">Also recorded: {others}</p>}
    </div>
  )
}

export const AssessmentCard = memo(function AssessmentCard({ card, day }: { card: AssessmentCardModel; day: string }) {
  const { game, item, assessment, sport } = card
  const channel = assessment?.binding ?? null
  const play = useMemo(() => topPlay(assessment, game, item), [assessment, game, item])
  const simulate = `/games/${card.key}/simulation?sport=${sport}`
  // A not-yet-evaluated decision carries the morning's placeholder reason
  // ("Awaiting first forecast and quote"), which is wrong once quotes exist.
  // The market and model lines above say what is actually pending.
  const notYet = item?.recommendation === 'NOT_YET_EVALUATED'
  const windowText = game ? `Evaluated in the ${etTime(game.checkpoints.decision_window_opens_at)}–${etTime(game.checkpoints.decision_window_closes_at)} decision window` : null
  const reasons = notYet && game?.model_status ? (windowText ? [windowText] : [])
    : item?.plain_reasons?.length ? item.plain_reasons : card.status.detail ? [card.status.detail] : []
  return (
    <article className={`as-card as-card--${card.status.tone}`} style={{ ['--sport' as string]: SPORT_COLORS[sport] }}
      aria-label={`${card.matchup}: ${card.status.label}`}>
      <header className="as-card__head">
        <div className="as-teams">
          <TeamLogo league={sport} abbreviation={game?.away?.abbreviation} size={36} />
          <div>
            <span className="as-sport"><i className="cc-dot" />{SPORT_NAMES[sport]} · {card.startsAt ? etTime(card.startsAt, true) : 'time TBD'}</span>
            <h3>{game?.away?.name ?? game?.away?.abbreviation ?? ''} <em>at</em> {game?.home?.name ?? game?.home?.abbreviation ?? card.matchup}</h3>
          </div>
          <TeamLogo league={sport} abbreviation={game?.home?.abbreviation} size={36} />
        </div>
        {/* An official pick carries its status inside the top-opportunity panel. */}
        {play.state !== 'OFFICIAL' && <span className={`as-status cc-tone--${card.status.tone}`}>{card.status.label}</span>}
      </header>
      <PregameStatus game={game} />
      <TopOpportunity play={play} game={game} item={item} sport={sport} status={card.status.label} channel={channel} />
      <ForecastStrip channel={channel} game={game} sport={sport} />
      <MarketSections play={play} assessment={assessment} game={game} sport={sport} />
      <div className="as-decision">
        <small>Decision</small>
        <b>{card.status.label}{item?.selection && item.selection !== '—' ? ` · ${item.selection}` : ''}</b>
        {reasons.slice(0, 3).map((r) => <p key={r}>{r}</p>)}
        {item?.edge_metrics
          ? <EdgeTests metrics={item.edge_metrics} book={shortBook(item.sportsbook) || 'the recorded book'} />
          : item?.edge != null && <p className="as-dim">Model minus the offered price's break-even: {(item.edge * 100).toFixed(1)} pts{item.expected_value != null ? ` · expected return at the offered price ${(item.expected_value * 100).toFixed(1)}%` : ''} at {shortBook(item.sportsbook) || 'the recorded book'}</p>}
      </div>
      <footer className="as-actions">
        <Link to={gameHref(sport, card.key)}>Analyze</Link>
        <Link to={simulate}>Simulate</Link>
        <Link to={`/markets?date=${day}&sport=${sport}`}>Shop Books</Link>
      </footer>
    </article>
  )
})

export const ResearchCard = memo(function ResearchCard({ row }: { row: ResearchComparison }) {
  const paired = row.channels.find((c) => c.channel === 'PAIRED_POPULATION') ?? null
  const point = row.channels.find((c) => c.channel === 'POINT_ELO') ?? null
  const away = row.away?.abbreviation ?? 'Away'
  const home = row.home?.abbreviation ?? 'Home'
  const awayName = row.away?.name ?? away
  const homeName = row.home?.name ?? home
  return (
    <article className="as-card as-card--gray as-card--research" style={{ ['--sport' as string]: SPORT_COLORS[row.sport] }}
      aria-label={`${row.matchup}: research only`}>
      <header className="as-card__head">
        <div className="as-teams">
          <div>
            <span className="as-sport"><i className="cc-dot" />{SPORT_NAMES[row.sport]} · {row.starts_at ? etTime(row.starts_at, true) : 'time TBD'}</span>
            <h3>{awayName} <em>at</em> {homeName}</h3>
          </div>
        </div>
        <span className="as-status cc-tone--gray">Research only</span>
      </header>
      {paired?.status === 'SUPPORTED' ? <ForecastStrip channel={paired} game={null} sport={row.sport} labels={{ away: awayName, home: homeName }} />
        : <p className="as-forecast__warn">Joint simulation unavailable: {paired?.error?.message ?? 'no verified population'}</p>}
      {paired?.status === 'SUPPORTED' && paired.winner && (
        <p className="as-dim">{paired.authority_label} · {paired.binding?.drawCount.toLocaleString()} paired final scores</p>
      )}
      {point && (
        <p className="as-dim">Separate Elo winner channel (not a simulation): {awayName} {pct(point.winner?.away)} · {homeName} {pct(point.winner?.home)} — no spread, total or parlay support.</p>
      )}
      <p className="as-market__note">{row.price_note}</p>
      <footer className="as-actions">
        <Link to={gameHref(row.sport, row.game_id)}>Research page</Link>
      </footer>
    </article>
  )
})

export function AssessmentBoard({ groups, research, day, loading }: {
  groups: Record<AssessmentGroup, AssessmentCardModel[]>
  research: ResearchComparison[]
  day: string
  loading: boolean
}) {
  const order: AssessmentGroup[] = ['QUALIFIED', 'CLOSEST', 'PASSED']
  const empty = order.every((g) => groups[g].length === 0)
  return (
    <section className="as-board" aria-label="Game assessments">
      {loading && empty && <p className="cc-empty" role="status">Loading game assessments…</p>}
      {!loading && empty && <p className="cc-empty">No MLB or NFL game on this slate needs an assessment.</p>}
      {order.filter((g) => groups[g].length).map((g) => (
        <section key={g} aria-label={GROUP_TITLE[g]}>
          <h2 className="cc-section-title">{GROUP_TITLE[g]} <span>{groups[g].length}</span></h2>
          <div className="as-grid">{groups[g].map((c) => <AssessmentCard key={c.key} card={c} day={day} />)}</div>
        </section>
      ))}
      {research.length > 0 && (
        <section aria-label="Research-only comparisons">
          <h2 className="cc-section-title">Research-only comparisons <span>{research.length}</span></h2>
          <div className="as-grid">{research.map((r) => <ResearchCard key={r.game_id} row={r} />)}</div>
        </section>
      )}
    </section>
  )
}
