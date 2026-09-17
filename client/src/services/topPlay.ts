/**
 * Daily Decisions · the one best action per game.
 *
 * Three $1 figures, never confused:
 *   - win payout:       total returned on a win = decimal odds d (profit d − 1);
 *   - expected return:  average total returned = pWin × d + pPush (+ pVoid);
 *   - expected profit:  pWin × (d − 1) − pLoss  (= expected return − 1).
 * Probabilities are the server's verified-population masses for the exact
 * quote line (push-aware). The server publishes the expected profit itself
 * (`expected_return` on a side, per $1 staked) only for a CURRENT, permitted,
 * lined quote; an outcome is ranked only when that value exists. Stale quotes,
 * player props, same-game combinations without a sportsbook parlay price and
 * cross-game products are never ranked.
 *
 * The official decision is kept apart from this research ranking: the
 * governed policy's selected or qualified outcome is the "#1 AISP PICK"; the
 * strongest cross-market value is shown beside it only when it differs.
 */
import type { CoverageGame, DecisionItem, SportCode } from './commandCenter'
import type { AssessedQuote, AssessedSide, GameAssessment, LegMasses } from './probability'
import { teamIdentity } from './teamIdentity'

export type Market = 'MONEYLINE' | 'SPREAD' | 'TOTAL'
export type Side = 'HOME' | 'AWAY' | 'OVER' | 'UNDER'

export const MARKET_ORDER: Market[] = ['MONEYLINE', 'SPREAD', 'TOTAL']
export const marketOf = (q: Pick<AssessedQuote, 'market'>): string => (q.market === 'RUN_LINE' ? 'SPREAD' : q.market)
export const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

export const decimalFromAmerican = (a: number) => (a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a))

/** $1 on a win: total back (stake included) and profit. */
export function winPayout(decimal: number) {
  return { total: decimal, profit: decimal - 1 }
}

/** Average dollars back and average profit per $1, from push-aware masses. */
export function expectation(m: Pick<LegMasses, 'win' | 'loss' | 'push' | 'void'>, decimal: number) {
  const profit = m.win * (decimal - 1) - m.loss
  return { profit, total: m.win * decimal + m.push + (m.void ?? 0) }
}

export const money2 = (v: number) => `$${v.toFixed(2)}`
export const payoutText = (decimal: number) => `Bet $1 → ${money2(decimal)} total if it wins`
export const profitIfWinText = (decimal: number) => `Profit if it wins: ${money2(decimal - 1)}`
export const expectedReturnText = (total: number) => `AISP expected return: ${money2(total)} per $1`
export function expectedProfitText(profit: number) {
  const cents = Math.round(profit * 100) / 100
  return cents >= 0 ? `Expected profit: +${money2(cents)} per $1` : `Expected loss: −${money2(Math.abs(cents))} per $1`
}
export const signedCents = (profit: number) => {
  const cents = Math.round(profit * 100) / 100
  return `${cents >= 0 ? '+' : '−'}${money2(Math.abs(cents))}`
}

export const PRICE_BLOCKED_TEXT: Record<string, string> = {
  PENDING: 'AISP expected return pending — forecast not available',
  STALE: 'Stored reference price — current expected return unavailable',
  NOT_PERMITTED: 'Provider not permitted by the decision policy — expected return not assessed',
  LINE_MISSING: 'Line not recorded — expected return not assessed',
  ONE_SIDED: 'Only one side quoted — expected return not assessed',
}

export function teamShort(game: Pick<CoverageGame, 'league' | 'home' | 'away'> | null, side: 'HOME' | 'AWAY'): string {
  const team = side === 'HOME' ? game?.home : game?.away
  const identity = teamIdentity(game?.league ?? '', team?.abbreviation)
  if (identity.known) return identity.nickname
  return team?.name ?? team?.abbreviation ?? (side === 'HOME' ? 'Home' : 'Away')
}

/** The outcome's own line: a spread quote carries the home line. */
export function sideLine(market: string, side: string, line: number | null): number | null {
  if (market === 'MONEYLINE' || line == null) return null
  if (market === 'SPREAD') return side === 'HOME' ? line : -line
  return line
}

const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v)}`

export function selectionText(game: Pick<CoverageGame, 'league' | 'home' | 'away'> | null, market: string, side: string, line: number | null): string {
  if (market === 'TOTAL') return `${side === 'OVER' ? 'Over' : 'Under'} ${line ?? ''}`.trim()
  const team = teamShort(game, side as 'HOME' | 'AWAY')
  if (market === 'MONEYLINE') return `${team} to win`
  const own = sideLine(market, side, line)
  return own == null ? team : `${team} ${signed(own)}`
}

export const marketLabel = (market: string, sport: SportCode | string) =>
  market === 'MONEYLINE' ? 'Moneyline' : market === 'TOTAL' ? 'Game total' : sport === 'MLB' ? 'Run line' : 'Spread'

export interface BookPrice {
  quote: AssessedQuote
  side: AssessedSide
  sportsbook: string
  american: number
  decimal: number
  current: boolean
}

export interface Outcome {
  key: string
  market: Market
  side: Side
  /** The quote line (a spread quote's home line); `ownLine` is this side's line. */
  line: number | null
  ownLine: number | null
  label: string
  /** Best current price (highest decimal), or the newest stored one when none is current. */
  best: BookPrice | null
  books: BookPrice[]
  masses: LegMasses | null
  /** Server expected profit per $1 at the best current price; null when not assessable. */
  expectedProfit: number | null
  expectedReturn: number | null
  /** Why no expectation is shown (a PRICE_BLOCKED_TEXT key). */
  blocked: keyof typeof PRICE_BLOCKED_TEXT | null
  rank: number | null
}

const outcomeKey = (market: string, side: string, line: number | null) => `${market}|${side}|${line ?? ''}`

function blockedFor(side: AssessedSide, current: boolean, supported: boolean): Outcome['blocked'] {
  if (!current || side.edge_reason === 'QUOTE_NOT_CURRENT') return 'STALE'
  if (!supported || !side.model || side.edge_reason === 'NO_VERIFIED_POPULATION') return 'PENDING'
  if (side.edge_reason === 'PROVIDER_NOT_PERMITTED') return 'NOT_PERMITTED'
  if (side.edge_reason === 'LINE_MISSING') return 'LINE_MISSING'
  if (side.edge_reason === 'NO_VIG_UNAVAILABLE') return 'ONE_SIDED'
  return side.expected_return == null ? 'PENDING' : null
}

/**
 * Every straight outcome of a game, each at its best current book, ranked by
 * expected profit per $1 with the deterministic tie-breaks:
 * expected profit, policy-adjusted edge, fresher quote, lower Monte Carlo
 * error, then sportsbook and selection name.
 */
export function rankOutcomes(assessment: GameAssessment | null, game: Pick<CoverageGame, 'league' | 'home' | 'away'> | null,
  official?: { key: string; adjustedEdge: number | null } | null): Outcome[] {
  if (!assessment) return []
  const supported = assessment.binding?.status === 'SUPPORTED'
  const groups = new Map<string, Outcome>()
  for (const quote of assessment.quotes) {
    const market = marketOf(quote)
    if (!MARKET_ORDER.includes(market as Market)) continue
    for (const [side, price] of Object.entries(quote.sides)) {
      if (price.american == null || price.decimal == null || !(price.decimal > 1)) continue
      if (market !== 'MONEYLINE' && quote.line == null) continue
      const key = outcomeKey(market, side, quote.line)
      const current = quote.state === 'CURRENT'
      const book: BookPrice = { quote, side: price, sportsbook: shortBook(quote.sportsbook) || 'Sportsbook', american: price.american, decimal: price.decimal, current }
      const entry = groups.get(key) ?? {
        key, market: market as Market, side: side as Side, line: quote.line, ownLine: sideLine(market, side, quote.line),
        label: selectionText(game, market, side, quote.line), best: null, books: [], masses: null,
        expectedProfit: null, expectedReturn: null, blocked: null, rank: null,
      }
      entry.books.push(book)
      groups.set(key, entry)
    }
  }
  const order = (a: BookPrice, b: BookPrice) =>
    Number(b.current) - Number(a.current)
    || (b.current ? b.decimal - a.decimal : Date.parse(b.quote.observed_at) - Date.parse(a.quote.observed_at))
    || a.quote.age_minutes - b.quote.age_minutes
    || a.sportsbook.localeCompare(b.sportsbook)
  for (const o of groups.values()) {
    o.books.sort(order)
    // The best price among books whose expected profit the server assessed; otherwise the best book shown.
    const assessed = o.books.filter((b) => b.current && b.side.expected_return != null && b.side.model)
    const best = assessed[0] ?? o.books[0] ?? null
    o.best = best
    o.masses = best?.side.model ?? o.books.find((b) => b.side.model)?.side.model ?? null
    if (best && best.current && best.side.expected_return != null && best.side.model && supported) {
      o.expectedProfit = best.side.expected_return
      o.expectedReturn = 1 + best.side.expected_return
    } else if (best) {
      o.blocked = blockedFor(best.side, best.current, supported)
    }
  }
  const ranked = [...groups.values()].filter((o) => o.expectedProfit != null)
  const adjusted = (o: Outcome) => (official && official.key === o.key && official.adjustedEdge != null ? official.adjustedEdge : o.best?.side.edge ?? -Infinity)
  ranked.sort((a, b) =>
    b.expectedProfit! - a.expectedProfit!
    || adjusted(b) - adjusted(a)
    || a.best!.quote.age_minutes - b.best!.quote.age_minutes
    || (a.masses?.mcse ?? Infinity) - (b.masses?.mcse ?? Infinity)
    || a.best!.sportsbook.localeCompare(b.best!.sportsbook)
    || a.label.localeCompare(b.label))
  ranked.forEach((o, i) => { o.rank = i + 1 })
  const marketIndex = (o: Outcome) => MARKET_ORDER.indexOf(o.market)
  return [...groups.values()].sort((a, b) => marketIndex(a) - marketIndex(b)
    || (a.line ?? 0) - (b.line ?? 0)
    || ['AWAY', 'HOME', 'OVER', 'UNDER'].indexOf(a.side) - ['AWAY', 'HOME', 'OVER', 'UNDER'].indexOf(b.side))
}

/** A same-game combination may compete only with a real sportsbook parlay price and an assessed expectation. */
export function rankableParlays(assessment: GameAssessment | null) {
  return (assessment?.parlay_lab ?? []).filter((p) => p.parlay.quote_status === 'RECORDED_SPORTSBOOK_PARLAY_PRICE'
    && p.parlay.expected_return != null && typeof (p.parlay.quote as { decimal?: unknown } | null)?.decimal === 'number')
}

/** The official decision's outcome, when the governed policy selected or qualified one. */
export function officialOutcome(item: DecisionItem | null, game: Pick<CoverageGame, 'home' | 'away'> | null): { market: Market; side: Side; key: string } | null {
  if (!item || !item.selection || item.selection === '—') return null
  const official = item.recommendation === 'QUALIFIED_BET' || item.position != null
  if (!official) return null
  const market = (item.market === 'RUN_LINE' ? 'SPREAD' : item.market) as Market
  if (market !== 'MONEYLINE') return null // the experiment governs moneyline decisions only
  const code = item.selection.split(/\s+/)[0]?.toUpperCase()
  const side: Side | null = code === game?.away?.abbreviation?.toUpperCase() ? 'AWAY'
    : code === game?.home?.abbreviation?.toUpperCase() ? 'HOME' : null
  if (!side) return null
  return { market, side, key: outcomeKey(market, side, null) }
}

export type TopState = 'OFFICIAL' | 'MODEL_VALUE' | 'NO_POSITIVE' | 'PENDING_FORECAST' | 'PRICE_NEEDED'

export const TOP_LABEL: Record<TopState, string> = {
  OFFICIAL: '#1 AISP PICK',
  MODEL_VALUE: 'BEST CURRENT MODEL VALUE',
  NO_POSITIVE: 'NO POSITIVE MODEL VALUE',
  PENDING_FORECAST: 'MODEL GUIDANCE PENDING',
  PRICE_NEEDED: 'CURRENT PRICE NEEDED',
}

export interface TopPlay {
  state: TopState
  outcomes: Outcome[]
  /** The official pick's outcome (OFFICIAL only). */
  official: Outcome | null
  /** The strongest positive cross-market value (any state), or null. */
  bestValue: Outcome | null
  /** True when the official pick and the best cross-market value are different outcomes. */
  differs: boolean
}

export function topPlay(assessment: GameAssessment | null, game: CoverageGame | null, item: DecisionItem | null): TopPlay {
  const officialRef = officialOutcome(item, game)
  const outcomes = rankOutcomes(assessment, game, officialRef ? { key: officialRef.key, adjustedEdge: item?.adjusted?.edge ?? null } : null)
  const ranked = outcomes.filter((o) => o.rank != null).sort((a, b) => a.rank! - b.rank!)
  const bestValue = ranked[0] && ranked[0].expectedProfit! > 0 ? ranked[0] : null
  const official = officialRef ? outcomes.find((o) => o.key === officialRef.key) ?? null : null
  const supported = assessment?.binding?.status === 'SUPPORTED'
  const anyCurrent = outcomes.some((o) => o.books.some((b) => b.current))
  let state: TopState
  if (officialRef) state = 'OFFICIAL'
  else if (bestValue) state = 'MODEL_VALUE'
  else if (!supported) state = 'PENDING_FORECAST'
  else if (!anyCurrent) state = 'PRICE_NEEDED'
  // A forecast and current prices, but no outcome the server could assess (e.g. provider not permitted).
  else if (ranked.length === 0) state = 'PENDING_FORECAST'
  else state = 'NO_POSITIVE'
  return { state, outcomes, official, bestValue, differs: Boolean(officialRef && bestValue && bestValue.key !== officialRef.key) }
}
