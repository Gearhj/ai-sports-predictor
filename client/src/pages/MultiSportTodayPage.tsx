/**
 * Daily Decisions — the decision desk.
 *
 * WHAT THIS PAGE IS FOR
 * ---------------------
 * Five questions, in this order, and nothing else:
 *   What qualifies now?  What is close?  What is blocked, and why?
 *   What has already resolved?  When does the next decision lock?
 *
 * THE FAILURE IT IS BUILT TO AVOID
 * --------------------------------
 * The page this replaces rendered every state as the same beige card. A game
 * that cleared the 2% threshold, a game 0.1 points short of it, and a game
 * with no sportsbook price at all were three identical rectangles that
 * differed only in a sentence of small type. That is not a decision product;
 * it is a list that happens to contain a decision. Here a qualified selection
 * is the loudest thing on the screen, a near miss is amber with its exact
 * distance from the threshold drawn against it, and a game with no price is
 * grey and in the blockers list -- because "no edge" and "no price" are
 * different facts with different fixes.
 *
 * WHAT IT MAY NOT DO
 * ------------------
 * It reads persisted decisions and counts them. It does not re-run the policy,
 * re-price anything, lower a threshold or call a provider. The 2.0-point
 * minimum shown here is read from the funnel service so this page cannot drift
 * from the code that enforced it. NFL cannot fund an official position in this
 * phase, so an NFL row reports research status and is never counted as a
 * decision the policy declined to make.
 */
import { SportReadiness } from '../components/readiness/SportReadiness'
import { useCallback, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PrefetchLink } from '../components/PrefetchLink'
import {
  AlertTriangle, ArrowRight, CheckCircle2, ClipboardCheck, Clock, Timer,
} from 'lucide-react'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { useSportRegistry } from '../hooks/useSportRegistry'
import { useSportSelection } from '../hooks/useSportSelection'
import { TeamLogo } from '../components/TeamLogo'
import { Freshness, SkeletonCards, SkeletonTable } from '../components/ui'
import { apiRequest, endpoints } from '../services/api'
import { resolveOperationalDate } from '../services/operationalDate'
import { CommandDecisions } from '../components/command/CommandDecisions'
import { RegionBoundary } from '../components/command/RegionBoundary'
import {
  LeagueTag, Panel, agoOf, american, clockOf, dayOf, pctPoints, signedPoints, useBroadcastTheme,
} from '../components/broadcast/primitives'
import type {
  NormalizedSelection, SportSection, TodayAllSportsReadModel,
} from '../types/multisport'
import type { ExperimentFunnelData } from '../types/domain'
import '../broadcast.css'
import { CoverageFunnel, type CoverageFunnelData } from '../components/CoverageFunnel'
import { ModelEraBadge, type ModelEraData } from '../components/ModelEraBadge'
import { WagerExplanation } from '../components/explain/WagerExplanation'
import { useSlateExplanations } from '../hooks/useExplanation'
import type { DecisionExplanation } from '../types/explain'
import type { TotalsBoard } from '../types/totals'
import { useMlbTotals } from '../hooks/useMlbTotals'
import { useScoreboard } from '../hooks/useScoreboard'
import { useSlateStatus } from '../hooks/useSlateStatus'
import { SlateBoard, type SlateFilter } from '../components/decisions/SlateBoard'
import { GameScoreboard } from '../components/scoreboard/GameScoreboard'
import type { GameScoreboard as Scoreboard } from '../types/scoreboard'
import {
  TotalsOpportunities, collectOpportunities, type TotalsSort,
} from '../components/markets/TotalsOpportunities'

/** The page never ranks across sports; the read model says so and this asserts it. */
export function hasNoCrossSportRanking(model: TodayAllSportsReadModel | null): boolean {
  return model?.cross_sport_ranking === 'NOT_APPLICABLE_PHASE_1'
}

interface DecisionsBoard {
  data_mode: 'live'
  today: TodayAllSportsReadModel | null
  funnel: ExperimentFunnelData | null
  warnings: string[]
}

type BlockerKey =
  | 'MISSING_FORECAST' | 'MISSING_QUOTE' | 'STALE_QUOTE'
  | 'INCOMPLETE_EVIDENCE' | 'INSUFFICIENT_EDGE' | 'RESEARCH_ONLY' | 'AWAITING_WINDOW'

/**
 * First blocking reason, in the order the policy evaluates them. A game that
 * has neither a price nor an edge is a pricing problem, not an edge problem,
 * and counting it in both would make the blockers add to more than the games.
 */

/** Stable identity: a fresh object literal would rebuild the hook's key. */
const EMPTY_SCENARIOS: Record<string, string> = {}

type MarketFilterValue = 'ALL' | 'MONEYLINE' | 'TOTALS'

export function readSlateFilter(value: string | null): SlateFilter {
  const upper = (value ?? '').toUpperCase()
  return upper === 'OPPORTUNITIES' || upper === 'POSITIONS' ? upper : 'ALL'
}

export function readMarket(value: string | null): MarketFilterValue {
  const upper = (value ?? '').toUpperCase()
  return upper === 'MONEYLINE' || upper === 'TOTALS' ? upper : 'ALL'
}

export function readTotalsSort(value: string | null, oversOnly: boolean): TotalsSort {
  const upper = (value ?? '').toUpperCase()
  if (upper === 'EXPECTED_RETURN') return 'EXPECTED_RETURN'
  // Ranking by Over probability only means something while the listing is
  // Overs. Applied to a mixed listing it would sort two opposite sides of the
  // same game by one side's number, which reads as a ranking and is not one.
  if (upper === 'WIN_PROBABILITY' && oversOnly) return 'WIN_PROBABILITY'
  return 'GAME_TIME'
}

/**
 * Why the totals listing is empty, from the board's own coverage.
 *
 * An empty listing has several quite different causes -- no game, no forecast,
 * no recorded line, no usable price -- and they are not interchangeable. A
 * generic blank would let "the books have not posted a total" read as "the
 * model has nothing to say", so each one says which it is.
 */
export function totalsUnavailableReason(board: TotalsBoard | null): string | null {
  if (!board?.coverage) return null
  const { coverage, market_availability: availability } = board
  if (coverage.games === 0) return 'No MLB game is scheduled for this slate.'
  if (coverage.games_with_v18_distribution === 0) {
    return 'No MLB game on this slate has a current V18 forecast, so no total can be priced.'
  }
  if (coverage.games_with_recorded_line === 0) {
    return `No sportsbook total is recorded for any game on this slate. ${availability?.detail ?? ''}`.trim()
  }
  if (coverage.selections_with_usable_price === 0) {
    return 'A total is recorded for this slate, but no side carries a usable offered price.'
  }
  return null
}

/**
 * Moneyline / Totals, and "Overs only" inside Totals.
 *
 * The counts are on the buttons because the honest answer to "are there any
 * totals today?" is usually a small number, and a reader should be able to see
 * that without switching markets and finding an empty panel.
 */
function MarketFilter({
  market, oversOnly, moneylineCount, totalsCount, onMarket, onOversOnly,
}: {
  market: MarketFilterValue
  oversOnly: boolean
  moneylineCount: number
  totalsCount: number
  onMarket: (next: MarketFilterValue) => void
  onOversOnly: (next: boolean) => void
}) {
  return (
    <div className="totals-op__markets" data-testid="market-filter">
      <div className="bx-segment" role="group" aria-label="Market">
        <button type="button" aria-pressed={market === 'ALL'}
          onClick={() => onMarket('ALL')} data-testid="market-ALL">All markets</button>
        <button type="button" aria-pressed={market === 'MONEYLINE'}
          onClick={() => onMarket('MONEYLINE')} data-testid="market-MONEYLINE">
          Moneyline <span className="bx-dim">{moneylineCount}</span>
        </button>
        <button type="button" aria-pressed={market === 'TOTALS'}
          onClick={() => onMarket('TOTALS')} data-testid="market-TOTALS">
          Totals <span className="bx-dim">{totalsCount}</span>
        </button>
      </div>
      {market !== 'MONEYLINE' && (
        <label className="totals-op__overs">
          <input type="checkbox" checked={oversOnly} data-testid="overs-only"
            onChange={(event) => onOversOnly(event.target.checked)} />
          <span>Overs only</span>
        </label>
      )}
    </div>
  )
}

export function firstBlocker(selection: NormalizedSelection): BlockerKey | null {
  const gates = (selection.outstanding_gates ?? []).join(' | ').toLowerCase()
  if (!selection.can_ever_fund_official_position) return 'RESEARCH_ONLY'
  if (selection.model_probability == null || gates.includes('no valid current forecast')
    || gates.includes('awaiting first forecast')) return 'MISSING_FORECAST'
  if (selection.market_evidence_state && selection.market_evidence_state !== 'QUOTE_PRESENT') {
    return selection.market_evidence_state.toUpperCase().includes('STALE') ? 'STALE_QUOTE' : 'MISSING_QUOTE'
  }
  if (selection.price_american == null) return 'MISSING_QUOTE'
  if (gates.includes('stale')) return 'STALE_QUOTE'
  if (gates.includes('below the') && gates.includes('minimum')) return 'INSUFFICIENT_EDGE'
  if (gates.length > 0) return 'INCOMPLETE_EVIDENCE'
  return null
}

const BLOCKER_LABEL: Record<BlockerKey, string> = {
  MISSING_FORECAST: 'No current forecast',
  MISSING_QUOTE: 'No usable price',
  STALE_QUOTE: 'Price too old',
  INCOMPLETE_EVIDENCE: 'Required evidence incomplete',
  INSUFFICIENT_EDGE: 'Edge below the threshold',
  RESEARCH_ONLY: 'Research only — cannot fund',
  AWAITING_WINDOW: 'Decision window still open',
}
const BLOCKER_COLOR: Record<BlockerKey, string> = {
  MISSING_FORECAST: '#8b7bff',
  MISSING_QUOTE: '#ff6f6f',
  STALE_QUOTE: '#f5b944',
  INCOMPLETE_EVIDENCE: '#4a90ff',
  INSUFFICIENT_EDGE: '#35d6c2',
  RESEARCH_ONLY: '#63788f',
  AWAITING_WINDOW: '#63788f',
}

/** One row per event, so a game evaluated at three phases is counted once. */
function distinctSelections(sections: SportSection[]): NormalizedSelection[] {
  const byEvent = new Map<string, NormalizedSelection>()
  for (const section of sections) {
    for (const selection of section.selections ?? []) {
      const key = `${selection.league_code}:${selection.event_id}`
      const existing = byEvent.get(key)
      // Keep the most advanced decision recorded for the event.
      const rank = (item: NormalizedSelection) =>
        item.decision_state === 'FINAL_QUALIFIED' ? 3
          : item.decision_state === 'PROVISIONAL_EDGE' ? 2
            : item.decision_state === 'AWAITING_FINAL_EVIDENCE' ? 1 : 0
      if (!existing || rank(selection) > rank(existing)) byEvent.set(key, selection)
    }
  }
  return [...byEvent.values()]
}

export function MultiSportTodayPage({ auditOnly = false }: { auditOnly?: boolean } = {}) {
  useBroadcastTheme()
  const { sport, withSport } = useSportSelection()
  const { registry, state: registryState } = useSportRegistry()

  const [params, setParams] = useSearchParams()
  const setParam = useCallback((key: string, value: string | null) => {
    setParams((old) => {
      const next = new URLSearchParams(old)
      if (value === null) next.delete(key)
      else next.set(key, value)
      return next
    }, { replace: true })
  }, [setParams])

  const loader = useCallback(async (signal: AbortSignal): Promise<DecisionsBoard> => {
    const warnings: string[] = []
    const [today, funnel] = await Promise.all([
      endpoints.today(signal),
      apiRequest<ExperimentFunnelData>('/experiment/funnel', {}, signal)
        .catch((error: unknown) => {
          if (signal.aborted) throw error
          warnings.push('Experiment funnel unavailable')
          return null
        }),
    ])
    return { data_mode: 'live', today, funnel, warnings }
  }, [])

  const resource = useApiResource(loader, {
    key: 'today/all-sports',
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
  const model = resource.data?.today ?? null
  const funnel = resource.data?.funnel ?? null

  const sections = useMemo(
    () => (model?.sport_sections ?? []).filter((section) =>
      sport === 'ALL' || section.league_code === sport),
    [model, sport],
  )
  const selections = useMemo(() => distinctSelections(sections), [sections])

  const fundable = useMemo(
    () => selections.filter((item) => item.can_ever_fund_official_position),
    [selections],
  )
  const minimumEdge = (funnel?.min_adjusted_edge ?? 0.02) * 100

  const stages = useMemo(() => {
    const scheduled = sections.reduce((sum, s) => sum + (s.scheduled_event_count ?? 0), 0)
    const forecast = sections.reduce((sum, s) => sum + (s.valid_forecast_count ?? 0), 0)
    const priced = selections.filter((item) => item.market_evidence_state === 'QUOTE_PRESENT').length
    const qualified = sections.reduce((sum, s) => sum + (s.final_qualified_count ?? 0), 0)
    const positioned = sections.reduce((sum, s) => sum + (s.official_position_count ?? 0), 0)
    return [
      { key: 'scheduled', label: 'Scheduled', count: scheduled, note: 'games on today\'s slate' },
      { key: 'forecast', label: 'Forecasted', count: forecast, note: 'a current model forecast exists' },
      { key: 'priced', label: 'Priced', count: priced, note: 'a usable two-sided quote is persisted' },
      { key: 'qualified', label: 'Qualified', count: qualified, note: `cleared the ${minimumEdge.toFixed(1)}% minimum` },
      { key: 'positioned', label: 'Positioned', count: positioned, note: 'recorded in the virtual ledger' },
    ]
  }, [sections, selections, minimumEdge])

  const ranked = useMemo(() => {
    const scored = fundable.filter((item) => item.adjusted_edge != null)
    return [...scored].sort((a, b) => (b.adjusted_edge ?? 0) - (a.adjusted_edge ?? 0))
  }, [fundable])
  const qualifiedNow = ranked.filter((item) => item.decision_state === 'FINAL_QUALIFIED')
  const nearThreshold = ranked.filter((item) =>
    item.decision_state !== 'FINAL_QUALIFIED'
    && (item.adjusted_edge ?? -99) >= minimumEdge - 2 && (item.adjusted_edge ?? -99) < minimumEdge)
  const clearingButOpen = ranked.filter((item) =>
    item.decision_state !== 'FINAL_QUALIFIED' && (item.adjusted_edge ?? -99) >= minimumEdge)
  // Everything fundable stays on the page, in edge order. Dropping the games
  // that are well short would leave the reader unable to tell "we looked and
  // it was not close" from "we never looked".
  // One request for the whole slate rather than one per card. The server side
  // spends no quota either way, but a decision desk should not issue fifteen
  // requests before anyone has asked a question.
  // The slate the board is showing. `today` carries the date the server used,
  // so the explanations are fetched for the same day the cards describe rather
  // than for the browser's idea of today.
  // The user's chosen day wins over the operational date, so completed games
  // are reachable at all. Everything below reads this one value, so the
  // scoreboard, the slate and the explanations can never disagree about which
  // day is on screen.
  const slateDate = params.get('date')
    ?? model?.operational_date ?? new Date().toISOString().slice(0, 10)
  // Which market the desk is showing. It lives in the URL for the same reason
  // the markets page keeps its view there: a board someone is looking at should
  // be a link they can send rather than a set of clicks they have to describe.
  const market = readMarket(params.get('market'))
  const oversOnly = params.get('overs') === '1'
  const totalsSort = readTotalsSort(params.get('sort'), oversOnly)
  const showTotals = (market === 'ALL' || market === 'TOTALS')
    && (sport === 'ALL' || sport === 'MLB')
  const showMoneyline = market === 'ALL' || market === 'MONEYLINE'
  // The filter narrows the listing in the browser, so the board is fetched
  // whole and "Overs only" costs no second request. Either way this is a read
  // of stored evidence: no provider is called when the filter changes.
  const totals = useMlbTotals({
    day: slateDate, enabled: showTotals, oversOnly: false, scenarios: EMPTY_SCENARIOS,
  })
  const totalsCount = useMemo(
    () => collectOpportunities(totals.data, oversOnly).length,
    [totals.data, oversOnly],
  )

  // One read for the whole page. Every card below shares it, so eight
  // opportunities are one request and no card polls anything by itself.
  const scoreboard = useScoreboard({ day: slateDate })

  // Every scheduled game, which is what the default view shows. A board that
  // listed only opportunities answered "what should I look at" and refused to
  // answer "what is happening today" -- so a quiet slate rendered as an empty
  // page, which reads as a broken product rather than as a quiet day.
  const slate = useSlateStatus(slateDate)
  const slateFilter = readSlateFilter(params.get('games'))

  const explanations = useSlateExplanations(slateDate, true)
  const explanationByGame = useMemo(() => {
    const map = new Map<string, DecisionExplanation>()
    for (const row of explanations.data?.explanations ?? []) {
      // A game can carry several phases; the recorded wager wins, otherwise the
      // most advanced decision already sorted last by the service.
      const existing = map.get(row.game_id)
      if (!existing || (row.recorded_wager && !existing.recorded_wager)) {
        map.set(row.game_id, row)
      }
    }
    return map
  }, [explanations.data])

  const ordered = useMemo(() => {
    const head = [...qualifiedNow, ...clearingButOpen, ...nearThreshold]
    const seen = new Set(head.map((item) => `${item.league_code}:${item.event_id}`))
    return [...head, ...ranked.filter((item) => !seen.has(`${item.league_code}:${item.event_id}`))]
  }, [qualifiedNow, clearingButOpen, nearThreshold, ranked])

  const blockers = useMemo(() => {
    const counts = new Map<BlockerKey, Set<string>>()
    for (const selection of selections) {
      const key = firstBlocker(selection)
      if (!key) continue
      const set = counts.get(key) ?? new Set<string>()
      set.add(`${selection.league_code}:${selection.event_id}`)
      counts.set(key, set)
    }
    return [...counts.entries()]
      .map(([key, games]) => ({ key, games: games.size }))
      .sort((a, b) => b.games - a.games)
  }, [selections])

  const timeline = useMemo(() => {
    const rows = selections
      .filter((item) => item.final_decision_cutoff)
      .map((item) => ({ selection: item, cutoff: item.final_decision_cutoff as string }))
    return rows.sort((a, b) => a.cutoff.localeCompare(b.cutoff))
  }, [selections])

  const resolved = useMemo(
    () => selections.filter((item) => item.position_status || item.event_status === 'FINAL'),
    [selections],
  )

  const zone = model?.display_timezone
  const now = Date.now()

  if (registryState === 'unavailable') {
    return (
      <div className="bx bx-decisions"><div className="bx__inner">
        <p className="bx-empty" role="alert">
          Sport registry authority is unavailable. The decision desk fails closed rather than
          guessing which leagues may create a position.
        </p>
      </div></div>
    )
  }

  return (
    <div className="bx bx-decisions">
      <div className="bx__inner">
        {!auditOnly && <RegionBoundary label="decision command center"><CommandDecisions day={resolveOperationalDate(slateDate)} /></RegionBoundary>}

        <AuditWrap auditOnly={auditOnly} summary="Governance evidence and full audit — funnel counts, every selection, blockers and explanations">
        <header className="bx-head">
          <div>
            <span className="bx-eyebrow"><ClipboardCheck size={13} aria-hidden="true" /> Daily Decisions audit</span>
            <h2>What <em>qualifies</em> today</h2>
            <p className="bx-muted" style={{ marginTop: 8, maxWidth: '58ch' }}>
              Persisted decisions, counted. Nothing here re-runs the policy, re-prices a game or
              calls a provider.
            </p>
          </div>
          <div className="bx-head__meta">
            <span className="bx-chip">
              <Clock size={12} aria-hidden="true" />
              {model?.operational_date ? dayOf(`${model.operational_date}T12:00:00Z`) : 'Loading'}
              {zone ? ` · ${zone}` : ''}
            </span>
            <span className="bx-chip">
              Forecasts {agoOf(model?.data_freshness?.latest_forecast_at ?? null, now) ?? 'not recorded'}
            </span>
            <span className="bx-chip bx-chip--warn">Research &amp; simulation only</span>
            {/* When the browser last re-read stored decisions. The forecast and
                quote clocks above are evidence times and are unaffected by it. */}
            <Freshness
              label="Decisions"
              updatedAt={resource.updatedAt}
              refreshing={resource.refreshing}
              stale={resource.stale}
              error={model ? resource.error : null}
              onRetry={resource.refresh}
            />
          </div>
        </header>
        <SportReadiness sport={sport} title="Decision readiness by sport" />

        {resource.loading && !model && (
          <>
            <SkeletonCards count={4} />
            <SkeletonTable rows={7} columns={6} ariaLabel="Loading today’s decisions" />
          </>
        )}
        {resource.error && !model && (
          <p className="bx-empty" role="alert">
            {resource.error}. No decision, count or price is substituted when the read fails.
          </p>
        )}

        {model && (
          <Panel eyebrow="Decision funnel" title="From today's slate to a recorded position"
            action={<span className="bx-dim">Counts are games, not decisions. A game evaluated at several
              phases is counted once.</span>}>
            <div className="bx-funnel">
              {stages.map((stage, index) => {
                const previous = index === 0 ? stage.count : stages[index - 1].count
                const dropped = Math.max(0, previous - stage.count)
                return (
                  <div key={stage.key}
                    className={`bx-funnel__stage${stage.key === 'positioned' && stage.count > 0 ? ' bx-funnel__stage--final' : ''}`}>
                    <small>{stage.label}</small>
                    <strong className="bx-num">{stage.count}</strong>
                    <span>{stage.note}</span>
                    {dropped > 0 && <span className="bx-funnel__drop">−{dropped}</span>}
                  </div>
                )
              })}
            </div>
            {sections.some((section) => !section.can_create_official_positions) && (
              <p className="bx-dim" style={{ marginTop: 12 }}>
                {sections.filter((s) => !s.can_create_official_positions).map((s) => s.league_code).join(', ')}{' '}
                {sections.filter((s) => !s.can_create_official_positions).length === 1 ? 'is' : 'are'} research
                shadow in this phase and cannot create an official position. Those games are not counted
                as decisions the policy declined.
              </p>
            )}
          </Panel>
        )}

        <Panel
          eyebrow="Today's slate"
          title="Every scheduled game"
          className="bx-panel--slate"
        >
          {slate.loading && !slate.data
            ? <p className="bx-dim" role="status">Loading the slate…</p>
            : <SlateBoard
                slate={slate.data}
                sport={sport}
                filter={slateFilter}
                onFilter={(next) => setParam('games', next === 'ALL' ? null : next)}
                // The page's single scoreboard read, shared by every row. No
                // row fetches anything, so opening this page costs one request
                // and buys no provider objects: MLB scores come from the free
                // MLB Stats API and NFL from the free nflverse release.
                scores={scoreboard.games}
                onRefreshScore={scoreboard.refresh}
                day={slateDate}
                onDay={(next) => setParam('date', next)}
              />}
        </Panel>

        <div style={{ display: 'grid', gap: 18, gridTemplateColumns: 'minmax(0, 1.55fr) minmax(0, 1fr)', alignItems: 'start' }}
          className="bx-decisions__split">
          <Panel
            eyebrow="Best opportunities now"
            title={market === 'TOTALS' ? 'MLB Over / Under' : 'Ranked by adjusted edge'}
            action={
              <MarketFilter
                market={market} oversOnly={oversOnly}
                moneylineCount={Math.min(ordered.length, 8)} totalsCount={totalsCount}
                onMarket={(next) => setParam('market', next === 'ALL' ? null : next)}
                onOversOnly={(next) => setParam('overs', next ? '1' : null)}
              />
            }
          >
            {!showMoneyline ? null : ranked.length === 0
              ? <p className="bx-empty">
                No fundable selection has a recorded adjusted edge today. A zero-selection day is a
                valid outcome, not a missing result.
              </p>
              : <div style={{ display: 'grid', gap: 9 }}>
                {ordered.slice(0, 8).map((selection, index) => (
                  <OpportunityRow key={`${selection.league_code}:${selection.event_id}`}
                    rank={index + 1} selection={selection} minimumEdge={minimumEdge}
                    zone={zone} withSport={withSport}
                    explanation={explanationByGame.get(selection.event_id) ?? null}
                    scoreboard={scoreboard.games[selection.event_id] ?? null}
                    onRefreshScore={scoreboard.refresh} />
                ))}
                {qualifiedNow.length === 0 && (
                  <p className="bx-dim">
                    No selection has reached a final qualified decision yet. The rows above are ranked
                    by recorded adjusted edge and remain subject to the final pregame evaluation.
                  </p>
                )}
              </div>}

            {showTotals && (
              <div className={showMoneyline ? 'totals-op__section' : undefined}>
                {showMoneyline && <h3 className="totals-op__subhead">MLB Over / Under</h3>}
                {totals.loading && !totals.data
                  ? <p className="bx-dim" role="status">Loading totals opportunities…</p>
                  : <TotalsOpportunities
                      board={totals.data} oversOnly={oversOnly} sort={totalsSort}
                      onSort={(next) => setParam('sort', next === 'GAME_TIME' ? null : next)}
                      unavailableReason={totalsUnavailableReason(totals.data)}
                    />}
              </div>
            )}
          </Panel>

          <div style={{ display: 'grid', gap: 18 }}>
            <Panel eyebrow="Forecasting engine" title="Which model decides">
              <ModelEra />
            </Panel>

            <Panel eyebrow="Price coverage" title="Where the slate stopped"
              action={<span className="bx-chip">MLB · distinct games</span>}>
              <PriceCoverage />
            </Panel>

            <Panel eyebrow="Why games did not qualify" title="Blockers"
              action={<span className="bx-dim">distinct games</span>}>
              {blockers.length === 0
                ? <p className="bx-empty">Nothing is blocked on today's slate.</p>
                : <div className="bx-blockers">
                  {blockers.map((row) => {
                    const widest = blockers[0].games || 1
                    return (
                      <div className="bx-blocker" key={row.key}>
                        <span>{BLOCKER_LABEL[row.key]}</span>
                        <b className="bx-num">{row.games}</b>
                        <span className="bx-blocker__bar">
                          <span style={{ width: `${(row.games / widest) * 100}%`, background: BLOCKER_COLOR[row.key] }} />
                        </span>
                      </div>
                    )
                  })}
                  <p className="bx-dim">
                    Each game is charged to the first gate it failed, in the order the policy
                    evaluates them, so these counts never exceed the games on the slate.
                  </p>
                </div>}
            </Panel>

            <Panel eyebrow="Today's cutoffs" title="When decisions lock"
              action={<Timer size={15} aria-hidden="true" />}>
              {timeline.length === 0
                ? <p className="bx-empty">No decision cutoff is recorded for today.</p>
                : <div className="bx-timeline">
                  {timeline.map(({ selection, cutoff }) => {
                    const passed = new Date(cutoff).getTime() < now
                    return (
                      <div key={`${selection.league_code}:${selection.event_id}`}
                        className={`bx-timeline__row${passed ? ' bx-timeline__row--passed' : ''}`}>
                        <span className="bx-timeline__time">{clockOf(cutoff, zone)}</span>
                        <span className="bx-timeline__dot" aria-hidden="true">
                          <i style={{ background: passed ? 'var(--bx-dim)' : 'var(--bx-accent)' }} />
                        </span>
                        <span style={{ minWidth: 0 }}>
                          <LeagueTag league={selection.league_code} /> {selection.event_label}
                        </span>
                        <span className="bx-dim">{passed ? 'locked' : 'upcoming'}</span>
                      </div>
                    )
                  })}
                </div>}
            </Panel>
          </div>
        </div>

        <Panel eyebrow="Resolved" title="Closed for decisions">
          {resolved.length === 0
            ? <p className="bx-dim">No game has closed for decisions yet today.</p>
            : <div className="bx-scroll">
              <table className="bx-table">
                <thead>
                  <tr>
                    <th scope="col">Game</th><th scope="col">Selection</th><th scope="col">Price</th>
                    <th scope="col">Adjusted edge</th><th scope="col">Position</th><th scope="col">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {resolved.map((selection) => (
                    <tr key={`${selection.league_code}:${selection.event_id}`}>
                      <th scope="row"><LeagueTag league={selection.league_code} /> {selection.event_label}</th>
                      <td>{selection.selection_label ?? '—'}</td>
                      <td className="bx-num">{american(selection.price_american)}</td>
                      <td className="bx-num">{selection.adjusted_edge == null ? '—' : pctPoints(selection.adjusted_edge, 2)}</td>
                      <td>{selection.position_status ?? 'none'}</td>
                      <td>{selection.position_result ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>}
        </Panel>

        {funnel?.available && (
          <details className="bx-details">
            <summary>Experiment to date · {funnel.experiment?.name}</summary>
            <div className="bx-details__body">
              <div className="bx-funnel">
                {funnel.stages.map((stage) => (
                  <div className="bx-funnel__stage" key={stage.key}>
                    <small>{stage.label}</small>
                    <strong className="bx-num">{stage.count}</strong>
                    <span>{stage.explanation}</span>
                    {stage.dropped > 0 && <span className="bx-funnel__drop">−{stage.dropped}</span>}
                  </div>
                ))}
              </div>
              <p>
                Minimum adjusted edge {(funnel.min_adjusted_edge * 100).toFixed(1)} points; final pregame
                cutoff {funnel.cutoff_minutes} minutes before start. Read from the policy module, not
                restated here.
              </p>
              {funnel.price_restoration?.statement && <p>{funnel.price_restoration.statement}</p>}
            </div>
          </details>
        )}

        <p className="bx-dim">
          {typeof model?.notes?.provider_calls === 'string'
            ? model.notes.provider_calls
            : 'This read made no provider call and created no decision, position or settlement.'}
          {registry ? ` Registry ${model?.registry_version ?? ''}.` : ''}
          {hasNoCrossSportRanking(model) ? ' Selections are ranked within a sport only; no cross-sport score exists.' : ''}
        </p>
        </AuditWrap>
      </div>
    </div>
  )
}

function OpportunityRow({
  rank, selection, minimumEdge, zone, withSport, explanation,
  scoreboard, onRefreshScore,
}: {
  rank: number; selection: NormalizedSelection; minimumEdge: number; zone?: string
  withSport: (href: string, sport?: 'MLB' | 'NFL') => string
  explanation?: DecisionExplanation | null
  /** Observed score for this game, when one has been collected. */
  scoreboard?: Scoreboard | null
  onRefreshScore?: () => void
}) {
  const edge = selection.adjusted_edge
  const qualified = selection.decision_state === 'FINAL_QUALIFIED'
  const clears = edge != null && edge >= minimumEdge
  const tone = qualified ? 'qualified' : clears ? 'qualified' : edge != null && edge >= minimumEdge - 2 ? 'near' : 'blocked'
  const distance = edge == null ? null : edge - minimumEdge
  // The bar runs from zero to twice the threshold so the threshold sits in the
  // middle: a selection just short of it and one just past it are visibly
  // different, which is the entire point of drawing it.
  const scale = Math.max(minimumEdge * 2, 1)
  const fill = edge == null ? 0 : Math.min(100, Math.max(0, (edge / scale) * 100))
  return (
    <article className={`bx-op bx-op--${tone}`}
      aria-label={`${selection.event_label} ${selection.selection_label ?? ''}`}>
      <span className="bx-op__rank">{rank}</span>
      <div className="bx-op__game">
        <strong>
          <MatchupCrests league={selection.league_code} label={selection.event_label} />
        </strong>
        <small>{selection.selection_label ?? 'no selection recorded'} · {selection.market_label ?? 'market not stated'}</small>
        <small>
          <b className="bx-num">{american(selection.price_american)}</b>{' '}
          {selection.sportsbook ?? 'no sportsbook recorded'}
          {selection.final_decision_cutoff ? ` · locks ${clockOf(selection.final_decision_cutoff, zone)}` : ''}
        </small>
      </div>
      <div className="bx-op__edge">
        <b className="bx-num">{edge == null ? '—' : pctPoints(edge, 2)}</b>
        <small>adjusted edge</small>
      </div>
      <div className="bx-threshold">
        <div className="bx-threshold__track">
          <span className="bx-threshold__fill"
            style={{
              width: `${fill}%`,
              background: tone === 'qualified' ? 'var(--bx-good)' : tone === 'near' ? 'var(--bx-warn)' : 'var(--bx-line-strong)',
            }} />
          <span className="bx-threshold__mark" style={{ left: '50%' }} aria-hidden="true" />
        </div>
        <div className="bx-threshold__legend">
          <span>0%</span>
          <span>{minimumEdge.toFixed(1)}% min</span>
        </div>
        {distance != null && (
          <span className="bx-dim">
            {distance >= 0
              ? `${signedPoints(distance, 2)} pts above the minimum`
              : `${Math.abs(distance).toFixed(2)} pts short of the minimum`}
          </span>
        )}
      </div>
      <div style={{ display: 'grid', gap: 6, justifyItems: 'end' }}>
        <span className={`bx-chip ${qualified ? 'bx-chip--good' : tone === 'near' ? 'bx-chip--warn' : ''}`}>
          {qualified
            ? <><CheckCircle2 size={12} aria-hidden="true" /> Qualified</>
            : clears
              ? 'Clears · awaiting final'
              : tone === 'near'
                ? <><AlertTriangle size={12} aria-hidden="true" /> Near threshold</>
                : 'Below threshold'}
        </span>
        {selection.detail_href && (
          <PrefetchLink className="bx-btn" to={withSport(selection.detail_href, selection.league_code as 'MLB' | 'NFL')}>
            Open <ArrowRight size={13} />
          </PrefetchLink>
        )}
      </div>
      {scoreboard?.available && scoreboard.status !== 'SCHEDULED' && (
        <div style={{ gridColumn: '1 / -1' }}>
          <GameScoreboard game={scoreboard} onRefresh={onRefreshScore}>
            {/* The recorded selection stays beside the live score. A team
                leading is not a settled wager, and the two lines sitting
                together is what keeps that visible. */}
            <p className="scorebd__selection" data-testid="scoreboard-selection">
              Recorded selection: <b>{selection.selection_label ?? '—'}</b>
              {' '}{selection.market_label ?? ''}{' '}
              <b className="bx-num">{american(selection.price_american)}</b>
              {selection.virtual_stake != null && (
                <> · stake <b className="bx-num">${selection.virtual_stake.toFixed(2)}</b></>
              )}
              <span className="bx-dim"> · progress is provisional until settlement</span>
            </p>
          </GameScoreboard>
        </div>
      )}

      {explanation && (
        <div style={{ gridColumn: '1 / -1' }}>
          <WagerExplanation explanation={explanation} />
        </div>
      )}
      {(selection.outstanding_gates ?? []).length > 0 && (
        <p className="bx-dim" style={{ gridColumn: '1 / -1' }}>
          {(selection.outstanding_gates ?? []).join(' · ')}
        </p>
      )}
    </article>
  )
}

/**
 * Crests for an "AWAY @ HOME" label.
 *
 * The decisions feed ships the matchup as one string rather than two team
 * records, so this parses it rather than inventing an API change for a visual
 * task. A label that does not split cleanly falls back to printing itself --
 * a decision row must never disappear because its title was shaped oddly.
 */
function MatchupCrests({ league, label }: { league: 'MLB' | 'NFL' | string; label: string }) {
  const parts = (label ?? '').split('@').map((part) => part.trim())
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    return <><LeagueTag league={league} /> {label}</>
  }
  const [away, home] = parts
  return (
    <span className="bx-op__crests">
      <LeagueTag league={league} />
      <TeamLogo league={league} abbreviation={away} size={30} />
      <span>{away}</span>
      <i>at</i>
      <TeamLogo league={league} abbreviation={home} size={30} />
      <span>{home}</span>
    </span>
  )
}

/**
 * The coverage funnel, loaded on its own so a slow or missing diagnosis never
 * delays the decisions themselves. The decisions are the page; this explains
 * the ones that are absent.
 */
function PriceCoverage() {
  const resource = useApiResource<CoverageFunnelData>(
    useCallback((signal: AbortSignal) => endpoints.priceCoverageFunnel(signal), []),
    { key: 'coverage/price-funnel', staleTimeMs: STALE_TIME.slate },
  )
  if (!resource.data) {
    return (
      <p className="bx-muted" role="status">
        {resource.state === 'loading'
          ? 'Reading today\u2019s coverage\u2026'
          : resource.error ?? 'Coverage diagnosis is unavailable.'}
      </p>
    )
  }
  return <CoverageFunnel data={resource.data} />
}

/**
 * The controlling engine, loaded separately so an authority read never delays
 * the decisions themselves.
 */
function ModelEra() {
  const resource = useApiResource<ModelEraData>(
    useCallback((signal: AbortSignal) => endpoints.modelEra(signal), []),
    { key: 'research/model-era', staleTimeMs: STALE_TIME.reference },
  )
  if (!resource.data) {
    return (
      <p className="bx-muted" role="status">
        {resource.state === 'loading'
          ? 'Reading the controlling engine\u2026'
          : resource.error ?? 'The controlling engine is unavailable.'}
      </p>
    )
  }
  return <ModelEraBadge data={resource.data} />
}

/** The audit renders directly when it is already inside a lazy disclosure. */
function AuditWrap({ auditOnly, summary, children }: { auditOnly: boolean; summary: string; children: import('react').ReactNode }) {
  if (auditOnly) return <>{children}</>
  return <details className="cc-below"><summary>{summary}</summary>{children}</details>
}
