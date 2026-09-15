/**
 * Today's Edge Board: which games Home headlines, in what order, and under what
 * title. Pure functions over the three cached read models (decision board,
 * market coverage, ledger); no request of its own.
 *
 * WHEN A GAME MAY BE CALLED AN EDGE
 * ---------------------------------
 * Only a decision that has actually been evaluated, and whose evidence checks
 * out independently of its disposition:
 *   - the forecast it used is the controlling engine's, and was the latest one
 *     at the decision (and, for a not-yet-final read, has not been superseded);
 *   - its quote is a complete same-book, same-provider two-sided moneyline pair
 *     for the same market, from a permitted provider;
 *   - that pair's OLDER leg was within the freshness window at the decision;
 *   - the evaluation carries a model probability, a market probability and an
 *     edge.
 * Anything short of that is not ranked as an edge. There is no fallback that
 * derives an edge from coverage quotes.
 *
 * ORDER
 * -----
 * Status first (position recorded, qualified, watching, declined), then the
 * policy's adjusted edge, then adjusted expected return, then urgency (the
 * sooner decision window first).
 *
 * TITLES
 * ------
 *   Today's Best Edges   evaluated games exist and one is qualified or positioned
 *   Closest to a Play    evaluated games exist, none qualifies
 *   Today's Model Leans  controlling forecasts exist for upcoming games, no evaluation yet
 *   Today's Games on Deck neither forecasts nor evaluations yet
 */
import type { CoverageGame, DecisionItem, Ledger, LedgerPosition, SportCode } from './commandCenter'
import { asSport } from './commandCenter'
import { bookRange, forSide, rangeForSide, type Side } from './marketMath'

export const MAX_CARDS = 4

export type HeroState = 'BEST_EDGES' | 'CLOSEST' | 'MODEL_LEANS' | 'ON_DECK'
export type CardStatus = 'POSITION' | 'QUALIFIED' | 'WATCHING' | 'DECLINED' | 'MARKET_PENDING' | 'EVALUATION_PENDING' | 'ON_DECK'

export const STATUS_LABEL: Record<CardStatus, string> = {
  POSITION: 'Position recorded',
  QUALIFIED: 'Qualified',
  WATCHING: 'Watching',
  DECLINED: 'Declined',
  MARKET_PENDING: 'Market pending',
  EVALUATION_PENDING: 'Priced · pending',
  ON_DECK: 'On deck',
}

export const HERO_TITLE: Record<HeroState, string> = {
  BEST_EDGES: 'Today’s Best Edges',
  CLOSEST: 'Closest to a Play',
  MODEL_LEANS: 'Today’s Model Leans',
  ON_DECK: 'Today’s Games on Deck',
}

const STATUS_TIER: Record<CardStatus, number> = {
  POSITION: 0, QUALIFIED: 1, WATCHING: 2, DECLINED: 3, EVALUATION_PENDING: 4, MARKET_PENDING: 5, ON_DECK: 6,
}

export interface PriceTerms {
  american: number | null
  sportsbook: string | null
  provider: string | null
  classification: string | null
  observedAt: string | null
  /** BEST_CURRENT: the best current coverage price for the side. AT_EVALUATION: the price the policy judged. */
  basis: 'BEST_CURRENT' | 'AT_EVALUATION' | 'REFERENCE'
}

export interface EdgeCard {
  key: string
  kind: 'evaluated' | 'lean' | 'deck'
  status: CardStatus
  sport: SportCode
  gameId: string
  game: CoverageGame | null
  matchup: string
  startsAt: string | null
  window: { opensAt: string; closesAt: string } | null
  collectionOpensAt: string | null
  side: Side | null
  selectionLabel: string | null
  modelProbability: number | null
  marketProbability: number | null
  consensus: number | null
  consensusCurrent: boolean
  range: [number, number] | null
  edge: number | null
  edgeBasis: 'ADJUSTED' | 'RECORDED' | null
  expectedValue: number | null
  price: PriceTerms | null
  position: { american: number | null; sportsbook: string | null; stake: number | null; status: string; result: string | null; profitLoss: number | null } | null
  reason: string | null
  projected: { home: number; away: number } | null
  evaluatedAt: string | null
  preFinal: boolean
  action: { label: 'View decision' | 'Analyze game'; href: string }
}

export interface EdgeBoard {
  state: HeroState
  title: string
  subtitle: string
  cards: EdgeCard[]
  /** How many games qualified for the state before the four-card cap. */
  available: number
}

/** Why an evaluation cannot be headlined as an edge; null when it can. */
export function edgeIneligibility(item: DecisionItem): string | null {
  if (item.recommendation === 'NOT_YET_EVALUATED') return 'not evaluated'
  if (item.market !== 'MONEYLINE') return 'not a moneyline evaluation'
  const fc = item.forecast_check
  if (!fc || !fc.controlling || !fc.latest_at_decision) return 'no controlling forecast'
  const mi = item.market_integrity
  if (!mi || !mi.two_sided_same_book || !mi.market_matches) return 'incomplete market'
  if (!mi.provider_permitted) return 'provider not permitted'
  if (!mi.fresh_at_decision) return 'stale quote'
  if (item.model_probability == null || item.market_probability == null) return 'missing probabilities'
  if ((item.adjusted?.edge ?? item.edge) == null) return 'missing edge'
  if (item.phase !== 'FINAL_PREGAME' && !item.position && fc.superseded_since_decision) return 'forecast superseded'
  return null
}

export function evaluatedStatus(item: DecisionItem): CardStatus {
  if (item.position) return 'POSITION'
  if (item.phase === 'FINAL_PREGAME' && item.recommendation === 'QUALIFIED_BET') return 'QUALIFIED'
  if (item.phase === 'FINAL_PREGAME' && item.recommendation === 'NO_BET') return 'DECLINED'
  return 'WATCHING'
}

export function sideOf(selection: string | null, game: CoverageGame | null): Side | null {
  const s = (selection ?? '').toUpperCase().replace(/\s+ML$/, '').trim()
  if (s === 'HOME' || s === 'AWAY') return s
  if (game?.home?.abbreviation && s === game.home.abbreviation.toUpperCase()) return 'HOME'
  if (game?.away?.abbreviation && s === game.away.abbreviation.toUpperCase()) return 'AWAY'
  return null
}

const DISPOSITION_REASON: Record<string, string> = {
  WATCH: 'Edge is below the qualifying threshold',
  PROVISIONAL_EDGE: 'Edge present, not confirmed by the final evaluation',
  EXTREME_DISAGREEMENT: 'Model and market disagree too sharply to act',
  NO_BET: 'Did not clear the qualifying thresholds',
  QUALIFIED_BET: 'Cleared every qualifying threshold',
}

/** One plain sentence: the first human reason, else a sentence for the disposition. */
export function plainReason(item: DecisionItem): string | null {
  const first = item.plain_reasons?.find((r) => !/^V\d|^Exact |under the existing policy/i.test(r))
  if (first) return first.replace(/\s*\(penalties:[^)]*\)/i, '').replace(/\.$/, '')
  return DISPOSITION_REASON[item.recommendation] ?? null
}

const startMs = (iso: string | null | undefined) => (iso ? Date.parse(iso) : Number.POSITIVE_INFINITY)

export function compareEvaluated(a: EdgeCard, b: EdgeCard): number {
  return STATUS_TIER[a.status] - STATUS_TIER[b.status]
    || (b.edge ?? Number.NEGATIVE_INFINITY) - (a.edge ?? Number.NEGATIVE_INFINITY)
    || (b.expectedValue ?? Number.NEGATIVE_INFINITY) - (a.expectedValue ?? Number.NEGATIVE_INFINITY)
    || startMs(a.window?.closesAt) - startMs(b.window?.closesAt)
    || a.matchup.localeCompare(b.matchup)
}

function positionFor(item: DecisionItem, ledger: Ledger | null): EdgeCard['position'] {
  if (!item.position) return null
  const recorded: LedgerPosition | undefined = ledger?.sports.flatMap((s) => s.positions).find((p) => p.position_id === item.position?.position_id)
  return {
    american: recorded?.entry.american ?? item.american,
    sportsbook: (recorded?.entry.sportsbook ?? item.sportsbook ?? null),
    stake: item.position.stake,
    status: item.position.status,
    result: item.position.result,
    profitLoss: item.position.profit_loss,
  }
}

function baseCard(game: CoverageGame | null, fallback: { gameId: string; sport: SportCode; matchup: string | null; startsAt: string | null }) {
  const start = game?.starts_at ?? fallback.startsAt
  return {
    sport: fallback.sport,
    gameId: fallback.gameId,
    game,
    matchup: game?.matchup ?? fallback.matchup ?? 'Game',
    startsAt: start,
    window: game ? { opensAt: game.checkpoints.decision_window_opens_at, closesAt: game.checkpoints.decision_window_closes_at } : null,
    collectionOpensAt: game?.checkpoints.collection_opens_at ?? null,
    projected: game?.model?.expected_home != null && game.model.expected_away != null
      ? { home: game.model.expected_home, away: game.model.expected_away } : null,
  }
}

export function evaluatedCard(item: DecisionItem, game: CoverageGame | null, ledger: Ledger | null, decisionsHref: string): EdgeCard {
  const side = sideOf(item.selection, game)
  const mi = item.market_integrity!
  const best = side && game?.best?.[side]
  const price: PriceTerms = best && best.american != null
    ? { american: best.american, sportsbook: best.sportsbook, provider: best.provider, classification: game?.quotes.find((q) => q.sportsbook === best.sportsbook && q.market === 'MONEYLINE')?.classification ?? null, observedAt: best.observed_at, basis: 'BEST_CURRENT' }
    : { american: mi.selected_american ?? item.american, sportsbook: mi.sportsbook, provider: mi.provider, classification: mi.classification, observedAt: mi.older_leg_observed_at, basis: 'AT_EVALUATION' }
  const adjusted = item.adjusted?.edge != null
  return {
    key: `decision:${item.decision_id}`,
    kind: 'evaluated',
    status: evaluatedStatus(item),
    ...baseCard(game, { gameId: item.game_id, sport: item.sport, matchup: item.matchup, startsAt: item.starts_at }),
    side,
    selectionLabel: item.selection,
    modelProbability: item.model_probability,
    marketProbability: item.market_probability,
    consensus: side && game ? forSide(game.consensus_no_vig_home, side) : null,
    consensusCurrent: Boolean(game?.consensus_is_current),
    range: side && game ? rangeForSide(bookRange(game), side) : null,
    edge: adjusted ? item.adjusted!.edge : item.edge,
    edgeBasis: adjusted ? 'ADJUSTED' : 'RECORDED',
    expectedValue: item.adjusted?.expected_value ?? item.expected_value,
    price,
    position: positionFor(item, ledger),
    reason: plainReason(item),
    evaluatedAt: item.recommended_at,
    preFinal: item.phase !== 'FINAL_PREGAME',
    action: { label: 'View decision', href: decisionsHref },
  }
}

export function leanCard(game: CoverageGame, sport: SportCode, analyzeHref: string): EdgeCard {
  const home = game.model?.home_two_way ?? null
  const side: Side | null = home == null ? null : home >= 0.5 ? 'HOME' : 'AWAY'
  const current = game.price_state === 'CURRENT_PRICE'
  const best = current && side ? game.best?.[side] : null
  const reference = !current && game.price_state === 'STORED_REFERENCE_ONLY'
  return {
    key: `lean:${game.game_id}`,
    kind: 'lean',
    status: current ? 'EVALUATION_PENDING' : 'MARKET_PENDING',
    ...baseCard(game, { gameId: game.game_id, sport, matchup: game.matchup, startsAt: game.starts_at }),
    side,
    selectionLabel: side ? `${(side === 'HOME' ? game.home : game.away)?.abbreviation ?? side} ML` : null,
    modelProbability: side ? forSide(home, side) : null,
    marketProbability: null,
    // Market evidence only when it is current; a stale range is never drawn.
    consensus: current && side ? forSide(game.consensus_no_vig_home, side) : null,
    consensusCurrent: current && game.consensus_is_current,
    range: current && side ? rangeForSide(bookRange(game), side) : null,
    edge: null,
    edgeBasis: null,
    expectedValue: null,
    price: best && best.american != null
      ? { american: best.american, sportsbook: best.sportsbook, provider: best.provider, classification: null, observedAt: best.observed_at, basis: 'BEST_CURRENT' }
      : reference ? { american: null, sportsbook: null, provider: null, classification: null, observedAt: game.freshest_observed_at, basis: 'REFERENCE' } : null,
    position: null,
    reason: null,
    evaluatedAt: null,
    preFinal: true,
    action: { label: 'Analyze game', href: analyzeHref },
  }
}

export function deckCard(game: CoverageGame, sport: SportCode, analyzeHref: string): EdgeCard {
  return {
    ...leanCard(game, sport, analyzeHref),
    key: `deck:${game.game_id}`,
    kind: 'deck',
    status: 'ON_DECK',
    side: null,
    selectionLabel: null,
    modelProbability: null,
    consensus: null,
    range: null,
    price: null,
  }
}

export interface BoardInput {
  decisions: DecisionItem[]
  games: CoverageGame[]
  ledger: Ledger | null
  now: number
  decisionsHref: (sport: SportCode) => string
  analyzeHref: (sport: SportCode, gameId: string) => string
}

export function buildEdgeBoard({ decisions, games, ledger, now, decisionsHref, analyzeHref }: BoardInput): EdgeBoard {
  const gameById = new Map(games.map((g) => [g.game_id, g]))
  const started = (iso: string | null | undefined) => iso != null && Date.parse(iso) <= now

  const evaluated = decisions
    .filter((item) => edgeIneligibility(item) === null)
    .map((item) => evaluatedCard(item, gameById.get(item.game_id) ?? null, ledger, decisionsHref(item.sport)))
  // One card per game: the best-ranked evaluation of it.
  const bestPerGame = (cards: EdgeCard[]) => {
    const seen = new Set<string>()
    return [...cards].sort(compareEvaluated).filter((c) => (seen.has(c.gameId) ? false : (seen.add(c.gameId), true)))
  }
  const live = bestPerGame(evaluated.filter((c) => c.status === 'POSITION' || !started(c.startsAt)))
  const upcoming = games.filter((g) => asSport(g.league) && g.starts_at && !started(g.starts_at))

  const evaluatedGames = new Set(live.map((c) => c.gameId))
  const recap = upcoming.length === 0 ? bestPerGame(evaluated) : []
  const pool = live.length ? live : recap
  if (pool.length) {
    const strong = pool.some((c) => c.status === 'POSITION' || c.status === 'QUALIFIED')
    return {
      state: strong ? 'BEST_EDGES' : 'CLOSEST',
      title: HERO_TITLE[strong ? 'BEST_EDGES' : 'CLOSEST'],
      subtitle: strong
        ? 'Evaluated with a current controlling forecast and a complete same-book market'
        : 'Evaluated today · nothing has cleared the qualifying thresholds',
      cards: pool.slice(0, MAX_CARDS),
      available: pool.length,
    }
  }

  const forecasted = upcoming.filter((g) => g.model?.home_two_way != null && !evaluatedGames.has(g.game_id))
  if (forecasted.length) {
    const conviction = (g: CoverageGame) => Math.abs((g.model?.home_two_way ?? 0.5) - 0.5)
    const cards = [...forecasted]
      .sort((a, b) => conviction(b) - conviction(a)
        || startMs(a.checkpoints.decision_window_opens_at) - startMs(b.checkpoints.decision_window_opens_at)
        || (a.matchup ?? '').localeCompare(b.matchup ?? ''))
      .map((g) => leanCard(g, asSport(g.league)!, analyzeHref(asSport(g.league)!, g.game_id)))
    const anyCurrent = forecasted.some((g) => g.price_state === 'CURRENT_PRICE')
    return {
      state: 'MODEL_LEANS',
      title: HERO_TITLE.MODEL_LEANS,
      subtitle: anyCurrent ? 'Markets arriving · final evaluations pending' : 'Sportsbook markets pending',
      cards: cards.slice(0, MAX_CARDS),
      available: cards.length,
    }
  }

  const cards = [...upcoming]
    .sort((a, b) => startMs(a.checkpoints.decision_window_opens_at) - startMs(b.checkpoints.decision_window_opens_at)
      || (a.matchup ?? '').localeCompare(b.matchup ?? ''))
    .map((g) => deckCard(g, asSport(g.league)!, analyzeHref(asSport(g.league)!, g.game_id)))
  return {
    state: 'ON_DECK',
    title: HERO_TITLE.ON_DECK,
    subtitle: cards.length ? 'Forecasts and sportsbook markets still to arrive' : 'No game remains on today’s slate',
    cards: cards.slice(0, MAX_CARDS),
    available: cards.length,
  }
}
