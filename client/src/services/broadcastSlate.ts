/**
 * One shape for a game, whatever sport produced it.
 *
 * MLB and NFL arrive from different services on different scales: MLB
 * probabilities are percentages, NFL probabilities are fractions; MLB has no
 * tie, NFL publishes one and it is not an overtime probability; MLB scores are
 * runs, NFL scores are points. Normalising that here -- once, with the
 * conversion visible -- is the only way the slate can put them on one page
 * without the page quietly comparing a percentage against a fraction.
 *
 * What is NOT normalised, deliberately: prices. A moneyline, a run line and a
 * total are different questions, and the row keeps them in separate fields so
 * nothing downstream can average them.
 */
import { isGoverned, type NflProductionStatus } from './nflGovernance'
import type { DashboardData, ForecastGame } from '../types/domain'
import type { NflEngine, NflEvent, NflMarketBlock } from '../types/nfl'
import { teamIdentity } from './teamIdentity'

/**
 * Resolve a side through the canonical identity table.
 *
 * Done HERE rather than in each component so one fix reaches every surface at
 * once. The API's `name` is kept only when we have no canonical record for the
 * club, which is the case that should look obviously unfinished rather than
 * silently plausible.
 *
 * This is why the slate says "Los Angeles Rams" while `teams.name` in the
 * database still says "St. Louis Rams": that row is the franchise's founding
 * identity, and rendering it against a 2026 fixture is simply wrong.
 */
function side(
  league: 'MLB' | 'NFL', abbreviation: string, apiName: string, apiColor?: string | null,
): SlateTeam {
  const identity = teamIdentity(league, abbreviation)
  return {
    abbreviation: identity.abbreviation,
    name: identity.known ? identity.name : apiName,
    color: identity.known ? identity.primary : (apiColor ?? null),
  }
}

export interface SlateTeam {
  abbreviation: string
  name: string
  color: string | null
}

export interface SlateGame {
  key: string
  league: 'MLB' | 'NFL'
  /** The canonical game id used by every in-app link. Null means no detail page. */
  gameId: string | null
  startsAt: string | null
  /** SCHEDULED | STARTED | IN_PROGRESS | FINAL, as the source reported it. */
  status: string
  home: SlateTeam
  away: SlateTeam
  /** Fractions, 0..1. Null when no forecast is published. */
  homeWin: number | null
  awayWin: number | null
  /** NFL only. The model's own final-score tie, not an overtime probability. */
  tie: number | null
  expHome: number | null
  expAway: number | null
  expTotal: number | null
  /** Home minus away. */
  expMargin: number | null
  scoreUnit: 'runs' | 'points'
  /** Market home-win probability as a fraction, when a comparable one exists. */
  marketHome: number | null
  bestHomePrice: number | null
  bestAwayPrice: number | null
  priceBook: string | null
  priceObservedAt: string | null
  priceFresh: boolean | null
  handicap: number | null
  totalLine: number | null
  /**
   * What the price boxes are allowed to say. CURRENT: a persisted quote inside
   * the freshness window. STORED_REFERENCE: a persisted quote that is too old
   * to be a current price. NOT_COLLECTED: no persisted sportsbook quote. Model
   * output never fills a price box, whatever this is.
   */
  priceStatus: PriceStatus
  priceProvider: string | null
  priceAgeMinutes: number | null
  /** Model minus market, in percentage points of home-win probability. */
  edgePoints: number | null
  /** Which side the published band describes. The band and `edgePoints`
   *  disagree in sign whenever this is 'away', so the row must name it. */
  edgeSide: 'home' | 'away' | null
  /** The backend's own edge, in points, signed against `edgeSide`. */
  modelEdgePoints: number | null
  modelLabel: string | null
  evidenceAt: string | null
  analysisHref: string
  simulationHref: string | null
  /** Whatever the source called the decision, or null when it governs none. */
  decisionState: string | null
  /** Which forecast the probabilities and the band above describe. */
  selection: import('./forecastSelection').ForecastSelection | null
  researchOnly: boolean
}

export type PriceStatus = 'CURRENT' | 'STORED_REFERENCE' | 'NOT_COLLECTED'

/** The governed quote freshness window, in minutes (FINAL_PREGAME reads 240). */
export const QUOTE_FRESHNESS_MINUTES = 240

const PROVIDER_LABELS: Record<string, string> = {
  THE_ODDS_API: 'The Odds API',
  SPORTSGAMEODDS: 'SportsGameOdds',
}
export const providerLabel = (code: string | null | undefined): string | null =>
  code ? PROVIDER_LABELS[code] ?? code.replace(/_/g, ' ') : null

export function priceStatusOf(observedAt: string | null | undefined, hasQuote: boolean,
  now: number = Date.now()): { status: PriceStatus; ageMinutes: number | null } {
  if (!hasQuote) return { status: 'NOT_COLLECTED', ageMinutes: null }
  const at = observedAt ? Date.parse(observedAt) : Number.NaN
  // A quote without a usable price time is a reference, never a current price.
  if (!Number.isFinite(at) || at > now + 60_000) return { status: 'STORED_REFERENCE', ageMinutes: null }
  const ageMinutes = (now - at) / 60_000
  return { status: ageMinutes <= QUOTE_FRESHNESS_MINUTES ? 'CURRENT' : 'STORED_REFERENCE', ageMinutes }
}

const fraction = (percent: number | null | undefined): number | null =>
  percent == null || !Number.isFinite(percent) ? null : percent / 100

export function mlbSlate(data: DashboardData | null): SlateGame[] {
  const games = (data?.games ?? []).filter((g) => !g.league_code || g.league_code === 'MLB')
  return games.map((game: ForecastGame) => {
    const homeWin = fraction(game.home_win_probability)
    const marketHome = fraction(game.market_home_probability)
    const price = priceStatusOf(game.market_quote_observed_at, Boolean(game.market_available))
    return {
      key: `MLB:${game.id}`,
      league: 'MLB' as const,
      gameId: game.id,
      startsAt: game.start_time ?? null,
      status: game.status ?? 'SCHEDULED',
      home: side('MLB', game.home_team.abbreviation, game.home_team.name, game.home_team.color),
      away: side('MLB', game.away_team.abbreviation, game.away_team.name, game.away_team.color),
      homeWin,
      awayWin: fraction(game.away_win_probability),
      tie: null,
      expHome: game.projected_home_runs ?? null,
      expAway: game.projected_away_runs ?? null,
      expTotal: game.projected_total ?? null,
      expMargin: game.projected_run_difference ?? null,
      scoreUnit: 'runs' as const,
      marketHome,
      bestHomePrice: game.market_available ? game.best_home_moneyline ?? null : null,
      bestAwayPrice: game.market_available ? game.best_away_moneyline ?? null : null,
      priceBook: game.market_books?.length
        ? (game.market_books.length === 1 ? game.market_books[0] : `${game.market_books.length} books`) : null,
      // The price time, never the forecast's data time.
      priceObservedAt: game.market_quote_observed_at ?? null,
      priceFresh: price.status === 'CURRENT',
      handicap: null,
      totalLine: game.market_total ?? null,
      priceStatus: price.status,
      priceProvider: game.market_providers?.length
        ? game.market_providers.map((code) => providerLabel(code)).join(' + ') : null,
      priceAgeMinutes: price.ageMinutes,
      // Home-side model minus market, in points. `model_edge` is published
      // against `edge_side`, so it is not reused here: a signed home-side
      // number is what the row renders and it must mean exactly that.
      edgePoints: homeWin != null && marketHome != null ? (homeWin - marketHome) * 100 : null,
      edgeSide: (game.edge_side === 'home' || game.edge_side === 'away') ? game.edge_side : null,
      modelEdgePoints: typeof game.model_edge === 'number' ? game.model_edge : null,
      modelLabel: (game as { forecast_model_label?: string }).forecast_model_label ?? null,
      evidenceAt: game.data_as_of ?? null,
      analysisHref: `/games/${game.id}`,
      simulationHref: `/games/${game.id}/simulation`,
      decisionState: game.opportunity ?? null,
      selection: (game as { forecast_selection?: import('./forecastSelection').ForecastSelection })
        .forecast_selection ?? null,
      researchOnly: false,
    }
  })
}

const block = (engine: NflEngine | undefined, type: string): NflMarketBlock | undefined =>
  engine?.market_comparison?.markets.find((entry) => entry.market_type === type)

/** The two-way home probability, conditional on no tie, or null. */
export function conditionalHomeWin(engine: NflEngine | undefined): number | null {
  if (!engine) return null
  const twoWay = engine.home_win_probability + engine.away_win_probability
  return twoWay > 0 ? engine.home_win_probability / twoWay : null
}

export function nflSlate(events: NflEvent[], preferredEngine = 'NFL_M1_XGBOOST',
                         governance?: NflProductionStatus | null): SlateGame[] {
  return events.map((event) => {
    const engine = event.engines.find((e) => e.engine_id === preferredEngine) ?? event.engines[0]
    const moneyline = block(engine, 'MONEYLINE')
    const spread = block(engine, 'SPREAD')
    const total = block(engine, 'TOTAL')
    // Only a same-book two-sided quote yields a no-vig market probability; an
    // incomplete one yields no comparison at all, never a half-derived number.
    const marketHome = moneyline?.comparison_available ? moneyline.market_no_vig?.a ?? null : null
    const modelHome = conditionalHomeWin(engine)
    return {
      key: `NFL:${event.event_id}`,
      league: 'NFL' as const,
      gameId: event.canonical_game_id ?? null,
      startsAt: event.kickoff_utc ?? null,
      status: event.state ?? 'UPCOMING',
      home: side('NFL', event.home.abbreviation, event.home.name),
      away: side('NFL', event.away.abbreviation, event.away.name),
      homeWin: engine?.home_win_probability ?? null,
      awayWin: engine?.away_win_probability ?? null,
      tie: engine?.tie_probability ?? null,
      expHome: engine?.expected_home_score ?? null,
      expAway: engine?.expected_away_score ?? null,
      expTotal: engine?.expected_total_score ?? null,
      expMargin: engine?.expected_margin_home ?? null,
      scoreUnit: 'points' as const,
      marketHome,
      bestHomePrice: moneyline?.quote?.a?.american ?? null,
      bestAwayPrice: moneyline?.quote?.b?.american ?? null,
      priceBook: moneyline?.book ?? null,
      priceObservedAt: moneyline?.observed_at ?? null,
      priceFresh: moneyline ? Boolean(moneyline.fresh) : null,
      handicap: spread?.line ?? null,
      totalLine: total?.line ?? null,
      ...(() => {
        const hasQuote = Boolean(moneyline?.quote || spread?.quote || total?.quote)
        const price = priceStatusOf(moneyline?.observed_at ?? spread?.observed_at ?? total?.observed_at, hasQuote)
        // The server's role is authoritative: a stored reference is never current.
        const status: PriceStatus = price.status === 'CURRENT'
          && [moneyline, spread, total].some((b) => b?.role === 'STORED_REFERENCE')
          ? 'STORED_REFERENCE' : price.status
        return {
          priceStatus: status,
          priceProvider: moneyline?.provider_label ?? providerLabel(moneyline?.provider) ?? null,
          priceAgeMinutes: price.ageMinutes,
        }
      })(),
      edgePoints: modelHome != null && marketHome != null ? (modelHome - marketHome) * 100 : null,
      // NFL publishes no decision band, so it publishes no edge side either.
      edgeSide: null,
      modelEdgePoints: null,
      modelLabel: engine?.model_label ?? null,
      evidenceAt: engine?.timestamps?.source_evidence_utc ?? null,
      analysisHref: event.canonical_game_id ? `/games/${event.canonical_game_id}` : '/games',
      simulationHref: event.lab_available && event.canonical_game_id
        ? `/games/${event.canonical_game_id}/simulation` : null,
      // NFL is a research track. It governs no decision, so it reports none
      // rather than reporting "NO BET", which would imply it could have bet.
      decisionState: null,
      // NFL keeps its retained M1 selection; this selector governs MLB only.
      selection: null,
      // Governed by the pinned handoff and the kickoff clock, not by the
      // bundle's own lineage: a pre-activation kickoff stays research.
      researchOnly: !isGoverned(governance ?? null, event.kickoff_utc ?? null),
    }
  })
}

export const byStartTime = (a: SlateGame, b: SlateGame): number =>
  (a.startsAt ?? '').localeCompare(b.startsAt ?? '')

/** Largest absolute model-versus-market gap first; games without one last. */
export const byDisagreement = (a: SlateGame, b: SlateGame): number =>
  Math.abs(b.edgePoints ?? -1) - Math.abs(a.edgePoints ?? -1)

export const isUpcoming = (game: SlateGame): boolean =>
  !['FINAL', 'COMPLETED', 'CLOSED'].includes(game.status.toUpperCase())
