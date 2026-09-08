import { useMemo, useState } from 'react'
import { ArrowUpDown, BookOpenCheck, Filter, Search, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { DataModeBanner, DataStatePanel, formatMoneyline, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { demoMarketBoard } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import { formatDate, formatTime, formatTimestamp, recencySortValue } from '../services/format'
import { MarketFeedNotice } from '../components/MarketFeedNotice'
import type { MarketType } from '../types/domain'

export function MarketBoardPage({ demo = false }: { demo?: boolean }) {
  const { data, state, loading, error, refresh } = useApiResource(endpoints.markets, demo ? { demoData: demoMarketBoard } : {})
  const [market, setMarket] = useState<'All' | MarketType>('All')
  const [query, setQuery] = useState('')
  const [minimumEdge, setMinimumEdge] = useState<number | null>(null)

  const rows = useMemo(() => (data?.rows ?? []).filter((row) =>
    (market === 'All' || row.market_type === market)
    && row.matchup.toLowerCase().includes(query.toLowerCase())
    && (minimumEdge === null || (row.edge != null && row.edge >= minimumEdge)),
  ).sort((a, b) => (b.edge ?? -Infinity) - (a.edge ?? -Infinity)), [data?.rows, market, minimumEdge, query])

  const freshestValue = rows.reduce<string | null>((latest, row) => !latest || recencySortValue(row.observed_at) > recencySortValue(latest) ? row.observed_at : latest, null)
  const freshest = freshestValue ? formatTimestamp(freshestValue) : 'Unavailable'
  const availableEdges = rows.flatMap((row) => row.edge == null ? [] : [row.edge])
  const bestEdge = availableEdges.length ? Math.max(...availableEdges) : null

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page market-board-page">
        <PageHeader eyebrow="MLB · MARKET INTELLIGENCE" title="Market Board" description="Compare observed live prices only when evidence exists." />
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        {/* The feed's own reason first: a reachable API says nothing about
            whether sportsbook data is current. */}
        <MarketFeedNotice status={data?.feed_status} hasRows={false} />
        <DataStatePanel
          state={state}
          emptyTitle="No current sportsbook observations"
          emptyDetail={
            data?.feed_status?.reason
            ?? 'No eligible sportsbook snapshot is persisted for this board. Reaching the AISP API is not evidence that market data is current.'
          }
        />
      </div>
    )
  }

  return (
    <div className="page market-board-page">
      <PageHeader
        eyebrow="MLB · MARKET INTELLIGENCE"
        title="Market Board"
        description="Compare book-level prices, ASP fair value, and normalized market consensus."
        actions={<span className="as-of-label">{formatDate(data.date)}</span>}
      />
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <MarketFeedNotice status={data.feed_status} hasRows={rows.length > 0} />

      <section className="metric-grid metric-grid--four">
        <MetricCard label="Markets shown" value={rows.length} detail={`${data.books.length} sportsbooks monitored`} />
        <MetricCard label="Largest model edge" value={bestEdge == null ? '—' : `${bestEdge > 0 ? '+' : ''}${bestEdge.toFixed(1)} pts`} detail="After no-vig normalization" trend={bestEdge != null && bestEdge > 0 ? 'positive' : 'neutral'} />
        <MetricCard label="Freshest observation" value={freshest} detail="Snapshot append-only" />
        <MetricCard label="Odds quota" value={data.quota_remaining ?? '—'} detail={data.quota_remaining === null ? 'Header unavailable' : 'Requests remaining'} />
      </section>

      <Panel className="market-board-panel">
        <div className="board-toolbar">
          <div className="segmented-control" aria-label="Market type">
            {(['All', 'Moneyline', 'Run line', 'Total'] as const).map((value) => (
              <button type="button" key={value} onClick={() => setMarket(value)} className={market === value ? 'is-active' : ''}>{value}</button>
            ))}
          </div>
          <label className="search-control"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter matchup" aria-label="Filter by matchup" /></label>
          <label className="select-control"><Filter size={14} /><select value={minimumEdge ?? 'all'} onChange={(event) => setMinimumEdge(event.target.value === 'all' ? null : Number(event.target.value))} aria-label="Minimum model edge"><option value="all">Any edge</option><option value={2}>2+ pts</option><option value={3}>3+ pts</option><option value={4}>4+ pts</option></select></label>
        </div>

        <TableScroll>
          <table className="data-table odds-board">
            <thead>
              <tr><th>Matchup / selection</th><th>ASP fair</th><th>Consensus</th><th>Model edge <ArrowUpDown size={12} /></th>{data.books.map((book) => <th key={book}>{book}</th>)}<th>Updated</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td><Link to={`${demo ? '/demo' : ''}/games/${row.game_id}`}><strong>{row.matchup}</strong><span>{formatTime(row.start_time)} · {row.market_type}</span><b>{row.selection}</b></Link></td>
                  <td className="asp-price">{formatMoneyline(row.asp_fair)}</td>
                  <td>{formatMoneyline(row.consensus)}</td>
                  <td>{row.edge == null ? '—' : <span className={row.edge >= 3 ? 'edge-chip edge-chip--strong' : 'edge-chip'}>{row.edge > 0 ? '+' : ''}{row.edge.toFixed(1)}</span>}</td>
                  {data.books.map((book) => {
                    const quote = row.books.find((entry) => entry.book === book)
                    return <td key={book} className={quote?.best ? 'best-price' : ''}>{quote ? formatMoneyline(quote.price) : '—'}{quote?.best && <small> BEST</small>}</td>
                  })}
                  <td className="muted">{formatTimestamp(row.observed_at)}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={data.books.length + 5} className="empty-row">No markets match the selected filters.</td></tr>}
            </tbody>
          </table>
        </TableScroll>
      </Panel>

      <div className="analysis-grid market-explainer-grid">
        <Panel title="How to read the board" eyebrow="MARKET METHODOLOGY">
          <div className="definition-list">
            <div><BookOpenCheck size={17} /><p><strong>ASP fair</strong><span>The American price implied by ASP’s forecast probability.</span></p></div>
            <div><SlidersHorizontal size={17} /><p><strong>Consensus</strong><span>Normalized midpoint across currently reporting books, with vig removed.</span></p></div>
            <div><ArrowUpDown size={17} /><p><strong>Model edge</strong><span>ASP probability minus no-vig market probability, measured in percentage points.</span></p></div>
          </div>
        </Panel>
        <Panel title="Data policy" eyebrow="TRANSPARENT BY DESIGN">
          <p className="body-copy">Every observed price is timestamped and appended. Best price highlights do not imply a recommendation. Missing books remain blank rather than being estimated, and historical closing lines remain unavailable until observed.</p>
          <p className="callout-note">Development polling is conservative to protect API quota. Configure frequency server-side.</p>
        </Panel>
      </div>

      <p className="page-footnote">Prices may move or become unavailable. DEMO prices are synthetic fixtures and must never be interpreted as current sportsbook offers.</p>
    </div>
  )
}
