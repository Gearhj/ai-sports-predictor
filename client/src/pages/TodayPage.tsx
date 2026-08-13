import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, Filter, RefreshCw } from 'lucide-react'
import { ForecastCard } from '../components/ForecastCard'
import { DataModeBanner, DataStatePanel, formatCurrency, formatSignedPct, MetricCard, PageHeader } from '../components/ui'
import { demoDashboard } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatDate, formatTimestamp, timeSortValue } from '../services/format'
import type { ForecastGame } from '../types/domain'

type SortKey = 'opportunity' | 'time' | 'edge' | 'confidence' | 'readiness'

const opportunityRank: Record<ForecastGame['opportunity'], number> = {
  'STRONG EDGE': 4,
  'STANDARD EDGE': 3,
  WATCH: 2,
  'NO BET': 1,
}

export function TodayPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.dashboard, demo ? { demoData: demoDashboard } : {})
  const [sort, setSort] = useState<SortKey>('opportunity')
  const games = useMemo(() => [...(data?.games ?? [])].sort((a, b) => {
    if (sort === 'time') return timeSortValue(a.start_time) - timeSortValue(b.start_time)
    if (sort === 'edge') return (b.model_edge ?? -Infinity) - (a.model_edge ?? -Infinity)
    if (sort === 'confidence') return (b.confidence ?? -Infinity) - (a.confidence ?? -Infinity)
    if (sort === 'readiness') return (b.data_readiness ?? -Infinity) - (a.data_readiness ?? -Infinity)
    return opportunityRank[b.opportunity] - opportunityRank[a.opportunity] || (b.model_edge ?? -Infinity) - (a.model_edge ?? -Infinity)
  }), [data?.games, sort])

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page today-page">
        <PageHeader eyebrow="MLB · DAILY RESEARCH DESK" title="Today’s MLB Forecasts" description="Live schedule and provenance-aware forecasts." />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel state={state} emptyTitle="No live games or forecasts yet" emptyDetail="The API is connected. Run the live schedule and forecast pipeline to populate this desk." />
      </div>
    )
  }
  const dataHealthDetail = data.summary.games === 0
    ? 'No games discovered'
    : data.summary.complete_forecasts === data.summary.games
      ? 'All games have published forecasts'
      : `${data.summary.games - data.summary.complete_forecasts} game(s) without a published forecast`

  return (
    <div className="page today-page">
      <PageHeader
        eyebrow="MLB · DAILY RESEARCH DESK"
        title="Today’s MLB Forecasts"
        description={formatDate(data.date)}
        actions={(
          <div className="date-control" aria-label="Date navigation">
            <button type="button" aria-label="Previous date" disabled><ChevronLeft size={16} /></button>
            <span><CalendarDays size={15} /> Today</span>
            <button type="button" aria-label="Next date" disabled><ChevronRight size={16} /></button>
          </div>
        )}
      />

      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <section className="metric-grid metric-grid--six" aria-label="Daily forecast summary">
        <MetricCard label={demo ? 'Example games' : 'Today’s games'} value={data.summary.games} detail={`${data.summary.complete_forecasts} ${demo ? 'illustrative' : 'published'} forecast(s)`} />
        <MetricCard label={demo ? 'Example edges' : 'Qualifying edges'} value={data.summary.qualifying_edges} detail={demo ? 'Illustrative classification' : 'Server-configured classifications'} accent="#d97332" />
        <MetricCard label={demo ? 'Example bankroll' : 'Official bankroll'} value={formatCurrency(data.summary.official_bankroll)} detail={demo ? 'Illustrative ledger' : 'Virtual experiment'} trend={data.summary.official_bankroll > 1000 ? 'positive' : 'neutral'} />
        <MetricCard label={demo ? 'Example return' : 'Season return'} value={formatSignedPct(data.summary.season_return)} detail={demo ? 'Illustrative sample' : 'Since $1,000 opening'} trend={data.summary.season_return > 0 ? 'positive' : data.summary.season_return < 0 ? 'negative' : 'neutral'} />
        <MetricCard label={demo ? 'Example readiness' : 'Data health'} value={`${Math.round(data.summary.overall_data_health)} / 100`} detail={dataHealthDetail} />
        <MetricCard label={demo ? 'Snapshot type' : 'Last refresh'} value={demo ? 'Illustrative' : data.summary.last_refresh ? formatTimestamp(data.summary.last_refresh) : 'Not recorded'} detail={demo ? 'Static snapshot · never live' : 'Latest persisted lifecycle refresh'} />
      </section>

      <section className="desk-note">
        <div className="desk-note__signal"><span /> ASP SIGNAL MONITOR</div>
        <p><strong>{demo ? `${data.summary.qualifying_edges} illustrative disagreement is shown as a UI example.` : `${data.summary.qualifying_edges} model-market disagreements clear today’s provisional threshold.`}</strong> Forecast probability and confidence are separate measures; expand a matchup to inspect uncertainty and source provenance.</p>
        <button type="button" className="secondary-button" onClick={refresh} disabled={loading || demo}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh data
        </button>
      </section>

      <div className="section-heading">
        <div><span className="eyebrow">FORECAST SLATE</span><h2>{games.length} featured matchups</h2></div>
        <label className="sort-control">
          <Filter size={14} />
          <span>Sort by</span>
          <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} aria-label="Sort forecasts">
            <option value="opportunity">Best opportunity</option>
            <option value="time">Game time</option>
            <option value="edge">Model edge</option>
            <option value="confidence">Confidence</option>
            <option value="readiness">Data readiness</option>
          </select>
        </label>
      </div>

      <div className="forecast-list">
        {games.map((game) => <ForecastCard game={game} demo={demo} key={game.id} />)}
      </div>

      <p className="page-footnote">ASP forecasts are probabilistic estimates for research, education, and entertainment. “Edge” is the difference between model probability and normalized no-vig market probability—not a guarantee of outcome or profit.</p>
    </div>
  )
}
