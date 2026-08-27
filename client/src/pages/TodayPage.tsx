import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, Filter, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { OpportunityCard } from '../components/OpportunityCard'
import { DataModeBanner, DataStatePanel, formatCurrency, PageHeader } from '../components/ui'
import { demoDashboard } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp, timeSortValue } from '../services/format'
import type { ForecastGame, GameOpportunity, OpportunitySelection, RecommendationState } from '../types/domain'

type SortKey = 'opportunity' | 'time' | 'edge' | 'ev' | 'readiness'

const STATE_RANK: Record<RecommendationState, number> = { QUALIFIED_BET: 3, WATCH: 2, NO_BET: 1 }

// Filter tabs. Market tabs map to the server's market labels and hide when no
// governed quote exists for them on the current slate.
const MARKET_TABS: Array<{ key: string; label: string; market: string }> = [
  { key: 'moneyline', label: 'Moneyline', market: 'Moneyline' },
  { key: 'runline', label: 'Run Line', market: 'Run line' },
  { key: 'totals', label: 'Totals', market: 'Total' },
  { key: 'firstfive', label: 'First Five', market: 'First five' },
]

interface Row {
  game: ForecastGame
  opportunity: GameOpportunity | undefined
}

function selectionForMarket(opportunity: GameOpportunity | undefined, market: string): OpportunitySelection | null {
  if (!opportunity) return null
  if (opportunity.best?.market_type === market) return opportunity.best
  return opportunity.alternates.find((item) => item.market_type === market) ?? null
}

function stateRank(row: Row): number {
  return STATE_RANK[row.opportunity?.recommendation_state ?? 'NO_BET']
}

// A finite sentinel keeps the sort comparator well-defined (subtracting two
// -Infinity sentinels would yield NaN and leave ordering undefined).
const num = (value: number | null | undefined) => (value == null ? -1e15 : value)

export function TodayPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.dashboard, demo ? { demoData: demoDashboard } : {})
  const [sort, setSort] = useState<SortKey>('opportunity')
  const [tab, setTab] = useState<string>('best')

  const rows = useMemo<Row[]>(() => {
    if (!data) return []
    const byId = new Map((data.opportunities ?? []).map((item) => [item.game_id, item]))
    return data.games.map((game) => ({ game, opportunity: byId.get(game.id) }))
  }, [data])

  const sorted = useMemo(() => {
    const items = [...rows]
    items.sort((a, b) => {
      if (sort === 'time') return timeSortValue(a.game.start_time) - timeSortValue(b.game.start_time)
      if (sort === 'edge') return num(b.opportunity?.best?.uncertainty_adjusted_edge) - num(a.opportunity?.best?.uncertainty_adjusted_edge)
      if (sort === 'ev') return num(b.opportunity?.best?.expected_value) - num(a.opportunity?.best?.expected_value)
      if (sort === 'readiness') return num(b.opportunity?.readiness) - num(a.opportunity?.readiness)
      // Best opportunities: qualified → watch → no-bet, then adjusted EV, then readiness, then freshest quote.
      return (
        stateRank(b) - stateRank(a) ||
        num(b.opportunity?.best?.expected_value) - num(a.opportunity?.best?.expected_value) ||
        num(b.opportunity?.readiness) - num(a.opportunity?.readiness) ||
        num(a.opportunity?.best?.quote_age_seconds) - num(b.opportunity?.best?.quote_age_seconds)
      )
    })
    return items
  }, [rows, sort])

  const availableMarkets = useMemo(() => {
    const set = new Set<string>()
    for (const row of rows) for (const market of row.opportunity?.markets_available ?? []) set.add(market)
    return set
  }, [rows])

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page today-page">
        <PageHeader eyebrow="MLB · DAILY OPPORTUNITY BOARD" title="Today’s MLB Forecasts" description="Governed model-vs-market opportunities for research and simulation." />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel
          state={state}
          emptyTitle="No current MLB slate is available"
          emptyDetail="The API is connected, but no current games or published forecasts are available. Historical research remains a separate evidence stream."
          unavailableTitle="Current forecasts are unavailable"
          unavailableDetail="The live API could not be reached. No historical row or illustrative result has been substituted."
          action={<Link className="secondary-button" to="/research/history">View governed 2025 history</Link>}
        />
      </div>
    )
  }

  const summary = data.opportunity_summary
  const dataHealth = `${Math.round(data.summary.overall_data_health)} / 100`
  const lastQuote = rows
    .map((row) => row.opportunity?.best?.quote_observed_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1) ?? data.summary.last_refresh

  // Build the visible set for the active tab.
  let visible = sorted
  let marketFocus: string | null = null
  if (tab === 'nobet') {
    visible = sorted.filter((row) => (row.opportunity?.recommendation_state ?? 'NO_BET') === 'NO_BET')
  } else if (tab !== 'best' && tab !== 'all') {
    const market = MARKET_TABS.find((item) => item.key === tab)?.market
    if (market) {
      marketFocus = market
      visible = sorted.filter((row) => selectionForMarket(row.opportunity, market) != null)
    }
  }

  const featured = tab === 'best'
    ? visible.filter((row) => (row.opportunity?.recommendation_state ?? 'NO_BET') !== 'NO_BET').slice(0, 3)
    : []
  const featuredIds = new Set(featured.map((row) => row.game.id))
  const remaining = visible.filter((row) => !featuredIds.has(row.game.id))

  return (
    <div className="page today-page">
      <PageHeader
        eyebrow="MLB · DAILY OPPORTUNITY BOARD"
        title="Today’s MLB Forecasts"
        description={formatDate(data.date)}
        actions={(
          <div className="today-header-actions">
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
        <div className="opportunity-summary__stat opportunity-summary__stat--qualified">
          <span>Qualified bets</span><strong>{summary?.qualified ?? 0}</strong>
        </div>
        <div className="opportunity-summary__stat opportunity-summary__stat--watch">
          <span>Watchlist</span><strong>{summary?.watch ?? 0}</strong>
        </div>
        <div className="opportunity-summary__stat opportunity-summary__stat--pass">
          <span>Passes</span><strong>{summary?.no_bet ?? data.summary.games}</strong>
        </div>
        <div className="opportunity-summary__stat opportunity-summary__stat--wide">
          <span>Top opportunity</span><strong>{summary?.top_opportunity ?? 'No qualified edge today'}</strong>
        </div>
        <div className="opportunity-summary__stat">
          <span>Best expected value</span><strong>{summary?.best_expected_value != null ? `${summary.best_expected_value > 0 ? '+' : ''}${summary.best_expected_value.toFixed(1)}%` : '—'}</strong>
        </div>
        <div className="opportunity-summary__stat">
          <span>Virtual daily exposure</span><strong>{formatCurrency(summary?.virtual_daily_exposure ?? 0)}</strong>
        </div>
        <div className="opportunity-summary__stat">
          <span>Data health</span><strong>{dataHealth}</strong>
        </div>
        <div className="opportunity-summary__stat">
          <span>Last quote</span><strong>{lastQuote ? formatTimestamp(lastQuote) : 'Not recorded'}</strong>
        </div>
      </section>

      <div className="opportunity-toolbar">
        <div className="opportunity-tabs" role="tablist" aria-label="Opportunity filters">
          <button type="button" role="tab" aria-selected={tab === 'best'} className={tab === 'best' ? 'is-active' : ''} onClick={() => setTab('best')}>Best Opportunities</button>
          <button type="button" role="tab" aria-selected={tab === 'all'} className={tab === 'all' ? 'is-active' : ''} onClick={() => setTab('all')}>All Games</button>
          {MARKET_TABS.filter((item) => availableMarkets.has(item.market)).map((item) => (
            <button key={item.key} type="button" role="tab" aria-selected={tab === item.key} className={tab === item.key ? 'is-active' : ''} onClick={() => setTab(item.key)}>{item.label}</button>
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

      {featured.length > 0 && (
        <section aria-label="Featured opportunities">
          <div className="section-heading"><div><span className="eyebrow">FEATURED</span><h2>Top {featured.length} opportunit{featured.length === 1 ? 'y' : 'ies'}</h2></div></div>
          <div className="opportunity-featured">
            {featured.map((row, index) => (
              <OpportunityCard key={row.game.id} game={row.game} opportunity={row.opportunity} rank={index + 1} variant="featured" demo={demo} />
            ))}
          </div>
        </section>
      )}

      <section aria-label="Slate">
        <div className="section-heading">
          <div><span className="eyebrow">{marketFocus ? marketFocus.toUpperCase() : tab === 'nobet' ? 'PASSES' : 'SLATE'}</span><h2>{remaining.length} {remaining.length === 1 ? 'game' : 'games'}</h2></div>
        </div>
        {remaining.length === 0 ? (
          <p className="opportunity-empty">No games match this filter.</p>
        ) : (
          <div className="opportunity-grid">
            {remaining.map((row) => (
              <OpportunityCard
                key={row.game.id}
                game={row.game}
                opportunity={row.opportunity}
                selection={marketFocus ? selectionForMarket(row.opportunity, marketFocus) : undefined}
                variant="compact"
                demo={demo}
              />
            ))}
          </div>
        )}
      </section>

      <p className="page-footnote">ASP opportunities are probabilistic model-vs-market comparisons for research, education, and entertainment. “Edge” is the gap between the model probability and the no-vig market probability; virtual stakes follow a quarter-Kelly policy under strict exposure caps and are never real-money wagers or a guarantee of profit.</p>
    </div>
  )
}
