/**
 * Daily Decisions · Bet Board.
 *
 * One expandable board per game. Every real market outcome is its own action
 * row: the selection in plain words, the best CURRENT sportsbook price for it,
 * when that price was observed, what a winning $1 returns, AISP's expected
 * profit per $1 when the verified population supports that exact quote, and
 * Add to My Card.
 *
 * The rules this board keeps:
 *   - Payout comes from the price alone, so it shows even with no forecast.
 *   - AISP expectation comes only from the server's verified-population
 *     assessment of that quote; otherwise the row says why it is pending.
 *   - Only a current quote can be added; a stale best price is shown as a
 *     stored reference and cannot be picked.
 *   - A side that no book quoted is shown as not quoted, never invented.
 *   - Player props show their real state; fixtures never appear here.
 */
import { memo, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { SPORT_COLORS, SPORT_NAMES, etTime, gameHref, relative, type CoverageGame } from '../../services/commandCenter'
import { isCurrentQuote, myCard, pickKey, useMyCard } from '../../services/myCard'
import { american, legLabel, pct, type AssessedQuote, type AssessedSide, type GameAssessment, type PlayerPropQuote } from '../../services/probability'
import { expectationLine, expectationUnavailable, payoutLine, profitLine } from '../../services/payout'
import { teamIdentity } from '../../services/teamIdentity'
import { PlayerPropsSection, pickFrom } from './MarketsAndCard'
import './assessments.css'

export const SAME_GAME_UNPRICED = 'Model probability available · sportsbook parlay price unavailable'
export const PROPS_UNAVAILABLE = 'Player-prop prices are not available yet. Live collection remains off while the provider balance is below its reserve floor.'
export const PROPS_POLICY = 'When available, sportsbook player props can be added to My Card. AISP assessment remains unavailable until prospective calibration is complete.'

type MarketKey = 'MONEYLINE' | 'SPREAD' | 'TOTAL'
const SIDES: Record<MarketKey, [string, string]> = { MONEYLINE: ['AWAY', 'HOME'], SPREAD: ['AWAY', 'HOME'], TOTAL: ['OVER', 'UNDER'] }
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')
const marketOf = (q: AssessedQuote): string => (q.market === 'RUN_LINE' ? 'SPREAD' : q.market)
const signed = (line: number) => (line > 0 ? `+${line}` : line < 0 ? `−${Math.abs(line)}` : 'PK')

export interface Candidate { quote: AssessedQuote; side: AssessedSide; current: boolean }
export interface OutcomeRow {
  key: string
  market: MarketKey
  side: string
  /** The line from this side's point of view (spread already oriented). */
  line: number | null
  best: Candidate | null
  current: boolean
  candidates: Candidate[]
}

export function teamWord(game: Pick<CoverageGame, 'league' | 'home' | 'away'>, side: string): string {
  const team = side === 'HOME' ? game.home : game.away
  const identity = team?.abbreviation ? teamIdentity(game.league, team.abbreviation) : null
  return identity?.known ? identity.nickname : team?.name ?? team?.abbreviation ?? (side === 'HOME' ? 'Home' : 'Away')
}

export function selectionText(game: Pick<CoverageGame, 'league' | 'home' | 'away'>, market: MarketKey, side: string, line: number | null): string {
  if (market === 'MONEYLINE') return `${teamWord(game, side)} to win`
  if (market === 'TOTAL') return `${side === 'OVER' ? 'Over' : 'Under'} ${line ?? ''}`.trim()
  return `${teamWord(game, side)} ${line == null ? '' : signed(line)}`.trim()
}

/**
 * Every outcome row for one market, best current price first. The most-quoted
 * line comes first; other lines follow. A side nobody quoted at a line that the
 * other side has is kept as an empty row, so it is visibly "not quoted".
 */
export function outcomeRows(quotes: AssessedQuote[], market: MarketKey, now: number): OutcomeRow[] {
  const groups = new Map<string, OutcomeRow>()
  const lineCount = new Map<string, number>()
  for (const quote of quotes.filter((q) => marketOf(q) === market)) {
    const lineKey = market === 'MONEYLINE' ? '' : String(quote.line)
    lineCount.set(lineKey, (lineCount.get(lineKey) ?? 0) + 1)
    for (const side of SIDES[market]) {
      const line = market === 'MONEYLINE' || quote.line == null ? null
        : market === 'SPREAD' && side === 'AWAY' ? -quote.line : quote.line
      const key = `${market}|${lineKey}|${side}`
      const row = groups.get(key) ?? { key, market, side, line, best: null, current: false, candidates: [] }
      groups.set(key, row)
      const price = quote.sides[side]
      if (!price || price.american == null || price.decimal == null) continue
      const current = quote.state === 'CURRENT' && isCurrentQuote(quote.observed_at, now)
      row.candidates.push({ quote, side: price, current })
    }
  }
  for (const row of groups.values()) {
    const pool = row.candidates.some((c) => c.current) ? row.candidates.filter((c) => c.current) : row.candidates
    row.best = pool.reduce<Candidate | null>((top, c) => (!top || (c.side.decimal ?? 0) > (top.side.decimal ?? 0) ? c : top), null)
    row.current = Boolean(row.best?.current)
    row.candidates.sort((a, b) => Number(b.current) - Number(a.current) || (b.side.decimal ?? 0) - (a.side.decimal ?? 0))
  }
  const lineOf = (row: OutcomeRow) => row.key.split('|')[1]
  return [...groups.values()].sort((a, b) =>
    (lineCount.get(lineOf(b)) ?? 0) - (lineCount.get(lineOf(a)) ?? 0)
    || Number(lineOf(a)) - Number(lineOf(b))
    || SIDES[market].indexOf(a.side) - SIDES[market].indexOf(b.side))
}

function OutcomeActionRow({ game, row, now, chosen, forecastId, modelId }: {
  game: CoverageGame; row: OutcomeRow; now: number; chosen: Set<string>; forecastId?: string | null; modelId?: string | null
}) {
  const label = selectionText(game, row.market, row.side, row.line)
  const best = row.best
  if (!best) {
    return (
      <li className="bb-row is-empty" aria-label={`${label}: not quoted`}>
        <span className="bb-row__sel"><b>{label}</b><small>Not quoted — nothing to select</small></span>
      </li>
    )
  }
  const pick = pickFrom(game, best.quote, row.side)
  const already = pick != null && chosen.has(pickKey(pick))
  const selectable = pick != null && row.current
  const model = best.side.model
  const expectation = expectationLine(best.side.expected_return)
  const book = shortBook(best.quote.sportsbook)
  return (
    <li className={`bb-row${row.current ? '' : ' is-stale'}`} aria-label={`${label} ${american(best.side.american)}`}>
      <span className="bb-row__sel">
        <b>{label}</b>
        <small>{row.line != null ? `Line ${row.market === 'TOTAL' ? row.line : signed(row.line)} · ` : ''}{row.current ? 'Best current price' : 'Stored price — not current'} · {book}</small>
        <small className="bb-row__time">Observed {etTime(best.quote.observed_at, true)} ({relative(best.quote.observed_at, now)}){best.quote.classification === 'RESEARCH' ? ` · ${best.quote.provider}, research-classified` : best.quote.provider ? ` · ${best.quote.provider}` : ''}</small>
      </span>
      <span className="bb-row__price">
        <b>{american(best.side.american)}</b>
        <em className={`as-state ${row.current ? 'is-current' : 'is-stale'}`}>{row.current ? 'Current' : 'Not current'}</em>
      </span>
      <span className="bb-row__pay">
        <b>{payoutLine(best.side.decimal)}</b>
        <small>{profitLine(best.side.decimal)}</small>
      </span>
      <span className="bb-row__aisp">
        {model && <small>AISP {row.market === 'TOTAL' ? (row.side === 'OVER' ? 'Over' : 'Under') : 'win'} probability {pct(model.win)}{model.push > 0 ? ` · push ${pct(model.push)}` : ''}</small>}
        {expectation
          ? <b className={(best.side.expected_return ?? 0) >= 0 ? 'is-up' : 'is-down'}>{expectation}</b>
          : <small className="bb-row__pending">{expectationUnavailable(best.side)}</small>}
      </span>
      <button type="button" className="bb-row__add" disabled={!selectable || already}
        aria-label={selectable ? `Add ${label} ${american(best.side.american)} at ${book} to My Card` : `${label}: not selectable (${pick ? 'price not current' : 'not quoted'})`}
        onClick={() => { if (pick && selectable) myCard.add(pick, { now, current: true }) }}>
        {already ? 'On your card' : selectable ? 'Add to My Card' : 'Not current'}
      </button>
      <details className="bb-row__why">
        <summary>Why?</summary>
        <dl>
          {model ? <>
            <div><dt>AISP probability used</dt><dd>win {pct(model.win)} · loss {pct(model.loss)} · push {pct(model.push)}</dd></div>
            <div><dt>AISP fair price</dt><dd>{american(model.fair_american)}</dd></div>
            <div><dt>Forecast</dt><dd className="bb-ident">{modelId ?? 'verified population'}{forecastId ? ` · ${forecastId}` : ''}</dd></div>
          </> : <div><dt>AISP probability</dt><dd>No verified population for this game yet.</dd></div>}
          <div><dt>No-vig market</dt><dd>{pct(best.side.no_vig)} (the books' own view, not AISP's)</dd></div>
          <div><dt>Every book for this outcome</dt><dd>
            <ul className="bb-books">
              {row.candidates.map((c, i) => (
                <li key={`${c.quote.sportsbook_key}-${i}`} className={c.current ? '' : 'is-stale'}>
                  {shortBook(c.quote.sportsbook)} {american(c.side.american)} · {etTime(c.quote.observed_at, true)}{c.current ? '' : ' · not current'}
                </li>
              ))}
            </ul>
          </dd></div>
        </dl>
      </details>
    </li>
  )
}

const SECTION_LABEL = (league: string): Record<MarketKey, string> => ({
  MONEYLINE: 'Moneyline',
  SPREAD: league === 'MLB' ? 'Run line' : 'Spread',
  TOTAL: 'Game total',
})

export const BetBoardGame = memo(function BetBoardGame({ game, assessment, props, propsStatus, now, open, onOpenLab }: {
  game: CoverageGame; assessment: GameAssessment | null; props: PlayerPropQuote[]; propsStatus: 'NOT_STORED' | 'AVAILABLE' | null
  now: number; open?: boolean; onOpenLab: (gameId: string) => void
}) {
  const card = useMyCard()
  const chosen = useMemo(() => new Set(card.map(pickKey)), [card])
  const quotes = assessment?.quotes ?? []
  const rows = useMemo(() => (['MONEYLINE', 'SPREAD', 'TOTAL'] as MarketKey[]).map((m) => [m, outcomeRows(quotes, m, now)] as const), [quotes, now])
  const currentCount = quotes.filter((q) => q.state === 'CURRENT' && isCurrentQuote(q.observed_at, now)).length
  const supported = assessment?.binding.status === 'SUPPORTED'
  const binding = assessment?.binding.binding
  const labels = SECTION_LABEL(game.league)
  const away = game.away?.name ?? game.away?.abbreviation ?? 'Away'
  const home = game.home?.name ?? game.home?.abbreviation ?? 'Home'
  return (
    <details className="bb-game" open={open} style={{ ['--sport' as string]: SPORT_COLORS[game.league as 'MLB'] }}>
      <summary>
        <span className="am-game__sport"><i className="cc-dot" />{SPORT_NAMES[game.league as 'MLB'] ?? game.league}</span>
        <b>{away} at {home}</b>
        <span className="am-game__when">{game.starts_at ? etTime(game.starts_at, true) : 'time TBD'}</span>
        <span className={`am-state ${currentCount ? 'is-current' : 'is-stale'}`}>{currentCount ? `${currentCount} current price${currentCount === 1 ? '' : 's'}` : 'No current price'}</span>
        <span className={`am-state ${supported ? 'is-current' : 'is-stale'}`}>{supported ? 'AISP forecast ready' : 'AISP forecast pending'}</span>
      </summary>
      <div className="bb-game__body">
        {rows.map(([market, list]) => (
          <section key={market} className="bb-market" aria-label={`${labels[market]} markets`}>
            <h4>{labels[market]}</h4>
            {list.length === 0
              ? <p className="am-none">Not quoted — nothing to select.</p>
              : <ul className="bb-rows">{list.map((row) => (
                <OutcomeActionRow key={row.key} game={game} row={row} now={now} chosen={chosen}
                  forecastId={binding?.forecastId} modelId={binding?.modelId} />
              ))}</ul>}
          </section>
        ))}
        {props.length > 0
          ? <PlayerPropsSection game={game} quotes={props} now={now} />
          : (
            <section className="bb-market" aria-label="Player props">
              <h4>Player props</h4>
              <p className="am-none">{propsStatus === 'AVAILABLE' ? 'No player-prop prices are stored for this game.' : PROPS_UNAVAILABLE}</p>
              <p className="am-none">{PROPS_POLICY}</p>
            </section>
          )}
        {supported && (
          <section className="bb-market" aria-label="Same-game combinations">
            <h4>Same-game combinations</h4>
            {(assessment?.parlay_lab ?? []).map((ex, i) => (
              <p key={i} className="am-sgp">
                {ex.legs.map((leg) => legLabel(leg, game.home?.abbreviation ?? 'Home', game.away?.abbreviation ?? 'Away')).join(' + ')}:
                {' '}AISP joint win {pct(ex.probabilities.win)} ({ex.effective_draw_count.toLocaleString()} paired draws) · {SAME_GAME_UNPRICED}
              </p>
            ))}
            <button type="button" className="am-link" onClick={() => onOpenLab(game.game_id)}>Build a same-game combination in the Parlay Lab</button>
          </section>
        )}
        <p className="am-foot"><Link to={gameHref(game.league, game.game_id)}>Open Game Center →</Link></p>
      </div>
    </details>
  )
})
