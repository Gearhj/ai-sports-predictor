import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, Filter, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { OpportunityCard } from '../components/OpportunityCard'
import { QualificationFunnel } from '../components/QualificationFunnel'
import { DataModeBanner, DataStatePanel, Freshness, formatCurrency, PageHeader, SkeletonCards, SkeletonTable } from '../components/ui'
import { demoDashboard } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME } from '../services/queryCache'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp, timeSortValue } from '../services/format'
import type {
  DashboardData,
  ForecastGame,
  GameOpportunity,
  OpportunitySelection,
  RecommendationState,
} from '../types/domain'

type SortKey = 'opportunity' | 'time' | 'edge' | 'ev' | 'readiness'

const STATE_RANK: Record<RecommendationState, number> = { QUALIFIED_BET: 5, PROVISIONAL_EDGE: 4, EXTREME_DISAGREEMENT: 3, WATCH: 2, NO_BET: 1 }

function ageLabel(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

// Sport-correct market labels for the filter tabs. The server already emits the
// sport-correct market family (e.g. MLB "Run line"); this maps it to a tab label
// and stays correct when NFL/NBA (Spread), NHL (Puck line), or MMA appear.
const MARKET_TAB_LABEL: Record<string, string> = {
  Moneyline: 'Moneyline',
  'Run line': 'Run Line',
  Spread: 'Spread',
  'Puck line': 'Puck Line',
  Total: 'Totals',
  Method: 'Method',
  Round: 'Round',
}

interface Row {
  game: ForecastGame
  opportunity: GameOpportunity | undefined
}

const sportOf = (row: Row) => row.opportunity?.sport ?? 'MLB'
const scoreOf = (row: Row) => row.opportunity?.opportunity_score ?? 0
const stateOf = (row: Row): RecommendationState => row.opportunity?.recommendation_state ?? 'NO_BET'

function selectionForMarket(opportunity: GameOpportunity | undefined, market: string): OpportunitySelection | null {
  if (!opportunity) return null
  if (opportunity.best?.market_type === market) return opportunity.best
  return opportunity.alternates.find((item) => item.market_type === market) ?? null
}

// A finite sentinel keeps the sort comparator well-defined.
const num = (value: number | null | undefined) => (value == null ? -1e15 : value)

export function TodayPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh, refreshing, updatedAt, stale } = useApiResource(endpoints.dashboard, demo
    ? { demoData: demoDashboard }
    : { key: 'dashboard/today', staleTimeMs: STALE_TIME.slate, refreshIntervalMs: STALE_TIME.slate })
  const [sort, setSort] = useState<SortKey>('opportunity')
  const [tab, setTab] = useState<string>('all')

  const rows = useMemo<Row[]>(() => {
    if (!data) return []
    const mixedSportPayload = dashboardHasNonMlbEvidence(data)
    const byId = new Map((data.opportunities ?? []).map((item) => [item.game_id, item]))
    // This legacy route is deliberately the MLB desk. A future league becoming
    // active in SPORT_REGISTRY_V1 must not silently turn /mlb into another
    // multi-sport runtime surface.
    return data.games
      .map((game) => ({ game, opportunity: byId.get(game.id) }))
      .filter((row) => row.opportunity
        ? sportOf(row).toUpperCase() === 'MLB'
        : !mixedSportPayload)
  }, [data])

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page today-page">
        <PageHeader eyebrow="DAILY OPPORTUNITY BOARD" title="Today’s MLB Forecasts" description="Governed model-vs-market opportunities for research and simulation." />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        {loading && (
          /* The board's own shape, held open while the slate is read. It is
             deliberately blank rather than zeroed: an empty cell cannot be
             mistaken for a real edge of 0.00%. */
          <>
            <SkeletonCards count={4} />
            <SkeletonTable rows={8} columns={6} ariaLabel="Loading today’s forecasts" />
          </>
        )}
        {/* The skeleton above already says "reading"; the panel speaks only
            when the answer is a genuine empty slate or a failure. */}
        {!loading && (
          <DataStatePanel
            state={state}
            emptyTitle="No current MLB slate is available"
            emptyDetail="The API is connected, but no current games or published forecasts are available. Historical research remains a separate evidence stream."
            unavailableTitle="Current forecasts are unavailable"
            unavailableDetail="The live API could not be reached. No historical row or illustrative result has been substituted."
            action={<Link className="secondary-button" to="/research/history">View governed 2025 history</Link>}
          />
        )}
      </div>
    )
  }

  const mixedSportPayload = dashboardHasNonMlbEvidence(data)
  const summary = data.opportunity_summary
  const scopedCounts = mixedSportPayload ? recommendationStateCounts(rows) : null
  const unscopedSummaryLabel = 'Unavailable — mixed-sport summary is not MLB-scoped'
  const dataHealth = mixedSportPayload
    ? unscopedSummaryLabel
    : data.summary.overall_data_health == null
      ? 'N/A'
      : `${Math.round(data.summary.overall_data_health)} / 100`
  const lastQuote = mixedSportPayload
    ? null
    : summary?.odds_last_refresh ?? data.summary.last_refresh

  const sportRows = rows

  const sortRows = (items: Row[]) => {
    const copy = [...items]
    copy.sort((a, b) => {
      if (sort === 'time') return timeSortValue(a.game.start_time) - timeSortValue(b.game.start_time)
      if (sort === 'edge') return num(b.opportunity?.best?.uncertainty_adjusted_edge) - num(a.opportunity?.best?.uncertainty_adjusted_edge)
      if (sort === 'ev') return num(b.opportunity?.best?.expected_value) - num(a.opportunity?.best?.expected_value)
      if (sort === 'readiness') return num(b.opportunity?.readiness) - num(a.opportunity?.readiness)
      return scoreOf(b) - scoreOf(a) || STATE_RANK[stateOf(b)] - STATE_RANK[stateOf(a)] || timeSortValue(a.game.start_time) - timeSortValue(b.game.start_time)
    })
    return copy
  }

  // Today's Best Opportunities: the top three by the single server Opportunity
  // Score (across the active sport filter). These games also carry rank markers
  // in the complete slate below.
  const topOpportunities = sportRows
    .filter((row) => stateOf(row) !== 'NO_BET')
    .sort((a, b) => scoreOf(b) - scoreOf(a))
    .slice(0, 3)
  const rankByGame = new Map(topOpportunities.map((row, index) => [row.game.id, index + 1]))

  const availableMarkets = marketsFor(sportRows)

  // Complete slate view: apply the market/no-bet lens, then group by sport.
  let slateRows = sportRows
  let marketFocus: string | null = null
  if (tab === 'nobet') {
    slateRows = sportRows.filter((row) => stateOf(row) === 'NO_BET')
  } else if (tab !== 'all') {
    marketFocus = tab
    slateRows = sportRows.filter((row) => selectionForMarket(row.opportunity, tab) != null)
  }
  const orderedSlateRows = sortRows(slateRows)

  return (
    <div className="page today-page">
      <PageHeader
        eyebrow="DAILY OPPORTUNITY BOARD"
        title="Today’s MLB Forecasts"
        description={formatDate(data.date)}
        actions={(
          <div className="today-header-actions">
            {!demo && (
              <Freshness
                label="Slate"
                updatedAt={updatedAt}
                refreshing={refreshing}
                stale={stale}
                error={error}
                onRetry={refresh}
              />
            )}
            <span className="model-chip">MODEL STATUS - {data.model_status}</span>
            <div className="date-control" aria-label="Date navigation">
              <button type="button" aria-label="Previous date" disabled><ChevronLeft size={16} /></button>
              <span><CalendarDays size={15} /> Today</span>
              <button type="button" aria-label="Next date" disabled><ChevronRight size={16} /></button>
            </div>
          </div>
        )}
      />

      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="opportunity-summary" aria-label="Daily opportunity summary">
        <div className="opportunity-summary__stat opportunity-summary__stat--qualified"><span>Qualified bets</span><strong>{scopedCounts?.QUALIFIED_BET ?? summary?.qualified ?? 0}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--provisional"><span>Provisional</span><strong>{scopedCounts?.PROVISIONAL_EDGE ?? summary?.provisional ?? 0}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--review"><span>Review required</span><strong>{scopedCounts?.EXTREME_DISAGREEMENT ?? summary?.review_required ?? 0}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--watch"><span>Watchlist</span><strong>{scopedCounts?.WATCH ?? summary?.watch ?? 0}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--pass"><span>Passes</span><strong>{scopedCounts?.NO_BET ?? summary?.no_bet ?? data.summary.games}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--wide"><span>Best board opportunity</span><strong>{mixedSportPayload ? unscopedSummaryLabel : summary?.top_opportunity ?? 'No qualified edge today'}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--wide"><span>Best V1-eligible moneyline</span><strong>{mixedSportPayload ? unscopedSummaryLabel : summary?.top_v1_moneyline ?? 'No governed moneyline candidate'}</strong></div>
        <div className="opportunity-summary__stat opportunity-summary__stat--wide"><span>Best V2-eligible moneyline</span><strong>{mixedSportPayload ? unscopedSummaryLabel : summary?.top_v2_moneyline ?? 'No V2 final decision yet'}</strong></div>
        <div className="opportunity-summary__stat"><span>Best expected value</span><strong>{mixedSportPayload ? unscopedSummaryLabel : summary?.best_expected_value != null ? `${summary.best_expected_value > 0 ? '+' : ''}${summary.best_expected_value.toFixed(1)}%` : '—'}</strong></div>
        <div className="opportunity-summary__stat"><span>Virtual daily exposure</span><strong>{mixedSportPayload ? unscopedSummaryLabel : formatCurrency(summary?.virtual_daily_exposure ?? 0)}</strong></div>
        <div className="opportunity-summary__stat"><span>Data health</span><strong>{dataHealth}</strong></div>
        <div className="opportunity-summary__stat"><span>Last quote</span><strong>{mixedSportPayload ? unscopedSummaryLabel : lastQuote ? formatTimestamp(lastQuote) : 'Not recorded'}</strong></div>
      </section>

      {mixedSportPayload && (
        <p className="opportunity-diagnostics">
          Mixed-sport dashboard response detected. MLB card counts were reconciled from explicit MLB rows; governed aggregate rankings, exposure, health, and freshness are unavailable because the response is not league-scoped.
        </p>
      )}

      <p className="opportunity-diagnostics">
        {mixedSportPayload
          ? <>MLB odds freshness unavailable — mixed-sport response is not league-scoped; provider quota and scheduler omitted</>
          : <>Odds refresh {summary?.odds_last_refresh ? formatTimestamp(summary.odds_last_refresh) : 'not recorded'}{' · '}quote age {ageLabel(summary?.odds_quote_age_seconds)}{' · '}provider quota {summary?.odds_quota_remaining ?? '—'}{' · '}scheduler {summary?.odds_scheduler_running ? 'running' : 'OFF — automatic odds refresh disabled; quotes may be stale'}</>}
      </p>

      <div className="sport-filter" role="tablist" aria-label="Sport filter">
        <button type="button" role="tab" aria-selected="true" className="is-active">MLB</button>
      </div>

      {!demo && <QualificationFunnel date={data.date} compact />}

      {topOpportunities.length > 0 && (
        <section aria-label="Today's best opportunities">
          <div className="section-heading"><div><span className="eyebrow">TODAY’S BEST OPPORTUNITIES</span><h2>Top {topOpportunities.length} by Opportunity Score</h2></div></div>
          <div className="opportunity-featured">
            {topOpportunities.map((row, index) => (
              <OpportunityCard key={row.game.id} game={row.game} opportunity={row.opportunity} rank={index + 1} variant="featured" demo={demo} />
            ))}
          </div>
        </section>
      )}

      <div className="opportunity-toolbar">
        <div className="opportunity-tabs" role="tablist" aria-label="Opportunity filters">
          <button type="button" role="tab" aria-selected={tab === 'all'} className={tab === 'all' ? 'is-active' : ''} onClick={() => setTab('all')}>All Games</button>
          {availableMarkets.map((market) => (
            <button key={market} type="button" role="tab" aria-selected={tab === market} className={tab === market ? 'is-active' : ''} onClick={() => setTab(market)}>{MARKET_TAB_LABEL[market] ?? market}</button>
          ))}
          <button type="button" role="tab" aria-selected={tab === 'nobet'} className={tab === 'nobet' ? 'is-active' : ''} onClick={() => setTab('nobet')}>No Bet</button>
        </div>
        <label className="sort-control">
          <Filter size={14} />
          <span>Sort by</span>
          <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} aria-label="Sort opportunities">
            <option value="opportunity">Best opportunity</option>
            <option value="time">Game time</option>
            <option value="edge">Model edge</option>
            <option value="ev">Expected value</option>
            <option value="readiness">Data readiness</option>
          </select>
        </label>
        <button type="button" className="secondary-button opportunity-refresh" onClick={refresh} disabled={loading || demo}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
        </button>
      </div>

      {orderedSlateRows.length === 0 ? (
        <p className="opportunity-empty">No games match this filter.</p>
      ) : (
        <section aria-label="Complete MLB slate">
          <div className="section-heading">
            <div><span className="eyebrow">COMPLETE MLB SLATE</span><h2>{orderedSlateRows.length} {orderedSlateRows.length === 1 ? 'game' : 'games'}</h2></div>
          </div>
          <div className="opportunity-grid">
            {orderedSlateRows.map((row) => (
              <OpportunityCard
                key={row.game.id}
                game={row.game}
                opportunity={row.opportunity}
                selection={marketFocus ? selectionForMarket(row.opportunity, marketFocus) : undefined}
                rankBadge={rankByGame.get(row.game.id)}
                variant="compact"
                demo={demo}
              />
            ))}
          </div>
        </section>
      )}

      <p className="page-footnote">ASP opportunities are probabilistic model-vs-market comparisons for research, education, and entertainment. “Edge” is the gap between the model probability and the no-vig market probability; the Opportunity Score blends edge, readiness, quote freshness, uncertainty, and disagreement. Virtual stakes follow a quarter-Kelly policy under strict exposure caps and are never real-money wagers or a guarantee of profit.</p>
    </div>
  )
}

function marketsFor(rows: Row[]): string[] {
  const set = new Set<string>()
  for (const row of rows) for (const market of row.opportunity?.markets_available ?? []) set.add(market)
  // Present market tabs in a stable, sport-correct order.
  const order = ['Moneyline', 'Run line', 'Spread', 'Puck line', 'Total', 'Method', 'Round']
  return order.filter((market) => set.has(market)).concat([...set].filter((market) => !order.includes(market)))
}

function dashboardHasNonMlbEvidence(data: DashboardData): boolean {
  const reportedSports = data.opportunity_summary?.sports_available ?? []
  return [...reportedSports, ...(data.opportunities ?? []).map((item) => item.sport)]
    .some((sport) => sport.trim().toUpperCase() !== 'MLB')
}

function recommendationStateCounts(rows: Row[]): Record<RecommendationState, number> {
  const counts: Record<RecommendationState, number> = {
    QUALIFIED_BET: 0,
    PROVISIONAL_EDGE: 0,
    EXTREME_DISAGREEMENT: 0,
    WATCH: 0,
    NO_BET: 0,
  }
  rows.forEach((row) => { counts[stateOf(row)] += 1 })
  return counts
}
