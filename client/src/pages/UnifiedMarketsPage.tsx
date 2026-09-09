import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, Radio, RefreshCw } from 'lucide-react'
import { PageHeader } from '../components/ui'
import { useSportSelection } from '../hooks/useSportSelection'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest, endpoints } from '../services/api'
import { formatTimestamp } from '../services/format'
import { resolveOperationalDate } from '../services/operationalDate'
import { comparableModel, conditionalHome, marketLabels, mlbOnly, mlbReviewedGaps, nflEdges, pregameProviders, quoteFreshness, type MarketFamily, type Freshness } from '../services/unifiedMarkets'
import type { MarketBoardData, MarketBoardRow, MarketPulseBoard, MarketPulseRow, PredictionProviderProbability } from '../types/domain'
import type { NflEngine, NflEvent, NflMarketBlock, NflWeekOverview, NflWeeks } from '../types/nfl'
import './UnifiedMarketsPage.css'

type MarketEvent = NflEvent & { prediction_market: { providers: PredictionProviderProbability[]; display_note: string | null } }
type NflBoard = Omit<NflWeekOverview, 'schema_version' | 'events'> & { schema_version: string; events: MarketEvent[] }
type Board = { data_mode: 'live'; scope: string; weeks: NflWeeks | null; nfl: NflBoard | null; mlb: MarketPulseBoard | null; books: MarketBoardData | null; warnings: string[] }
const pct = (n: number | null | undefined) => n == null ? '—' : `${(n * 100).toFixed(1)}%`
const signed = (n: number | null | undefined, digits = 1) => n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(digits)}`
const stamp = (s: string | null | undefined) => s ? formatTimestamp(s) : 'time unavailable'
const price = (n: number | null | undefined) => n == null ? 'Unavailable' : signed(n, 0)
const families: Exclude<MarketFamily, 'ALL'>[] = ['MONEYLINE', 'SPREAD', 'TOTAL']

function Crowd({ providers, home, now, nfl }: { providers: PredictionProviderProbability[]; home: string; now: string; nfl: boolean }) {
  const observations = pregameProviders(providers)
  return <section className="um-crowd" aria-label="Prediction markets">
    <h4><Radio size={14} aria-hidden="true" /> Crowd · {home} home-win observations</h4>
    {observations.length ? <div className="um-crowd-quotes">{observations.map((p) => <div key={p.provider}>
      <span>{p.provider === 'POLYMARKET' ? 'Polymarket' : p.provider === 'KALSHI' ? 'Kalshi' : p.provider}</span><strong>{pct(p.midpoint)}</strong>
      <small>{quoteFreshness(p.observed_at, now).toLowerCase()} · {stamp(p.observed_at)}</small>
      <small>{p.semantics_status === 'SEMANTICS_MATCHED' ? 'Settlement reviewed' : 'Settlement unreviewed · research only'}</small>
    </div>)}</div> : <p>No matched pregame observation is available. NFL/MLB forecasts stay visible without market coverage.</p>}
    {nfl && observations.length > 0 && <p>Research home-win contract observations; tie/settlement rules may differ from a two-way sportsbook. No crowd edge is inferred.</p>}
  </section>
}

function NflMarket({ family, engine, home, away }: { family: Exclude<MarketFamily, 'ALL'>; engine: NflEngine; home: string; away: string }) {
  const block = engine.market_comparison?.markets.find((b) => b.market_type === family)
  const model = block ? comparableModel(block) : null
  const headline = family === 'MONEYLINE' ? pct(conditionalHome(engine)) : family === 'SPREAD' ? `${signed(-engine.expected_margin_home)} ${home}` : engine.expected_total_score.toFixed(1)
  const label = family === 'MONEYLINE' ? `${home} · conditional on no tie` : family === 'SPREAD' ? 'Mean-implied home handicap' : 'Model expected total'
  return <section className="um-market" aria-label={`${marketLabels.NFL[family]} comparison`}>
    <h4>{marketLabels.NFL[family]}</h4><div className="um-model-line"><strong>{headline}</strong><span>{label}</span></div>
    {!block?.quote ? <p className="um-no-price">Sportsbook unavailable{block?.quotes?.length ? ' · incomplete two-sided quote' : ''}</p> : <>
      <p className={`um-freshness ${block.fresh ? 'is-fresh' : ''}`}>{block.book} · {block.fresh ? 'fresh quote' : 'stale · reference only'}<br /><span>{stamp(block.observed_at)}</span></p>
      <div className="um-quote-lines">{(['a', 'b'] as const).map((side) => {
        const q = block.quote![side]
        const name = q.side === 'HOME' ? home : q.side === 'AWAY' ? away : q.side === 'OVER' ? 'Over' : 'Under'
        const gap = block.comparison_available && model?.[side] != null && block.market_no_vig ? model[side]! - block.market_no_vig[side] : null
        return <div key={side}><span>{name}{q.point != null ? ` ${family === 'SPREAD' ? signed(q.point) : q.point}` : ''}</span><b>{price(q.american)}</b>
          <small>No-vig {pct(block.market_no_vig?.[side])} · model {pct(model?.[side])}</small>
          <small>{gap == null ? 'Comparable edge unavailable' : `${signed(gap * 100)} pp model − market`}</small></div>
      })}</div>
      {block.model && block.model.push > 0 && <p>Push {pct(block.model.push)}. Model/no-vig comparison is conditional on no push; win/loss/push remain separate.</p>}
    </>}
    {!block?.comparison_available && <p className="um-market-note">{block?.reason ?? 'No fresh, same-book two-sided price. No edge calculated.'}</p>}
  </section>
}

function NflCard({ event, family, now }: { event: MarketEvent; family: MarketFamily; now: string }) {
  const [selected, setSelected] = useState('NFL_M1_XGBOOST')
  const engine = event.engines.find((e) => e.engine_id === selected) ?? event.engines[0]
  const { withSport } = useSportSelection()
  const game = event.canonical_game_id
  return <article className="um-card um-card--nfl">
    <header><div><span className="um-sport">NFL · WEEK {event.week}</span><h3>{event.away.abbreviation} <span>@</span> {event.home.abbreviation}</h3><p>{event.away.name} at {event.home.name}</p></div><span className="um-state">{event.state.toLowerCase()}</span></header>
    <p className="um-kickoff">{stamp(event.kickoff_utc)} · Research shadow</p>
    {engine ? <>
      <div className="um-score"><span>Expected score</span><strong>{event.away.abbreviation} {engine.expected_away_score.toFixed(1)} <i>—</i> {event.home.abbreviation} {engine.expected_home_score.toFixed(1)}</strong></div>
      <p className="um-threeway">{event.away.abbreviation} {pct(engine.away_win_probability)} · Tie {pct(engine.tie_probability)} · {event.home.abbreviation} {pct(engine.home_win_probability)}</p>
      {event.engines.length > 1 && <label className="um-engine">Model<select value={engine.engine_id} onChange={(e) => setSelected(e.target.value)}>{event.engines.map((e) => <option key={e.engine_id} value={e.engine_id}>{e.model_label}</option>)}</select></label>}
      <div className="um-markets">{families.filter((f) => family === 'ALL' || family === f).map((f) => <NflMarket key={f} family={f} engine={engine} home={event.home.abbreviation} away={event.away.abbreviation} />)}</div>
      {(family === 'ALL' || family === 'MONEYLINE') && <Crowd providers={event.prediction_market.providers} home={event.home.abbreviation} now={now} nfl />}
      <details className="um-evidence"><summary>Model & source evidence</summary><p>{engine.model_label} · {engine.engine_version} · {engine.snapshot_type} v{engine.snapshot_version}</p><p>Source evidence {stamp(engine.timestamps.source_evidence_utc)}. Published {stamp(engine.timestamps.published_utc)}. A publication timestamp is not an input refresh.</p><p>{engine.evidence_basis.note}</p><p>{engine.evidence_readiness.explanation}</p><p>Forecast {engine.forecast_id}</p><p>No wagering authority. No execution in AISP.</p></details>
    </> : <p>No imported forecast is available.</p>}
    {game && <footer><Link to={withSport(`/games/${game}`, 'NFL')}>Game analysis <ArrowRight size={14} /></Link>{event.lab_available && <Link to={withSport(`/games/${game}/simulation`, 'NFL')}>Simulation Lab <ArrowRight size={14} /></Link>}</footer>}
  </article>
}

function MlbCard({ row, books, family, now }: { row: MarketPulseRow; books: MarketBoardRow[]; family: MarketFamily; now: string }) {
  const { withSport } = useSportSelection()
  return <article className="um-card um-card--mlb"><header><div><span className="um-sport">MLB</span><h3>{row.away_abbrev} <span>@</span> {row.home_abbrev}</h3><p>{row.away_team} at {row.home_team}</p></div><span className="um-state">{row.status.toLowerCase()}</span></header>
    <p className="um-kickoff">{stamp(row.starts_at)}</p><div className="um-markets">{families.filter((f) => family === 'ALL' || family === f).map((f) => {
      const matching = books.filter((b) => b.market_type === ({ MONEYLINE: 'Moneyline', SPREAD: 'Run line', TOTAL: 'Total' } as const)[f])
      return <section className="um-market" key={f}><h4>{marketLabels.MLB[f]}</h4>
        {f === 'MONEYLINE' && <div className="um-model-line"><strong>{pct(row.aisp_home_probability)}</strong><span>{row.home_abbrev} Gen 1 win probability · {row.aisp_forecast_fresh ? 'recent forecast' : 'older / unavailable forecast'}</span></div>}
        {matching.length ? matching.map((b) => <div className="um-mlb-quote" key={b.id}><b>{b.selection}</b><span>Model fair {price(b.asp_fair)} · consensus {price(b.consensus)}</span><small>{b.books.map((q) => `${q.book} ${price(q.price)}`).join(' · ')}</small><small>{quoteFreshness(b.observed_at, now).toLowerCase()} · {stamp(b.observed_at)}</small></div>) : <p className="um-no-price">Sportsbook unavailable · no persisted {marketLabels.MLB[f].toLowerCase()} price</p>}
        {matching.length > 0 && <p className="um-market-note">Stored book references; consensus may include implied prices without a paired no-vig quote. No new edge is inferred.</p>}
      </section>
    })}</div>
    {(family === 'ALL' || family === 'MONEYLINE') && <><Crowd providers={row.providers} home={row.home_abbrev ?? 'Home'} now={now} nfl={false} />{mlbReviewedGaps(row, now).length > 0 && <p className="um-market-note">Home-win model − fresh reviewed crowd: {mlbReviewedGaps(row, now).map((gap) => `${signed(gap * 100)} pp`).join(' / ')}. Research comparison, not a bet instruction.</p>}</>}
    <details className="um-evidence"><summary>Source & review evidence</summary><p>{row.display_note ?? row.consensus_note}</p>{row.provider_coverage?.map((p) => <p key={p.provider}>{p.provider}: {p.reason}. Observed {stamp(p.last_snapshot_at)}.</p>)}<p>Gen 1 and V2.1 remain unchanged. This comparison creates no decision or position.</p></details>
    <footer><Link to={withSport(`/games/${row.game_id}`, 'MLB')}>Game analysis <ArrowRight size={14} /></Link><Link to={withSport(`/games/${row.game_id}/simulation`, 'MLB')}>Diamond Simulator <ArrowRight size={14} /></Link></footer>
  </article>
}

export function UnifiedMarketsPage() {
  const { sport } = useSportSelection()
  const [params, setParams] = useSearchParams()
  const day = resolveOperationalDate(params.get('date'))
  const season = Number(params.get('season')) || null
  const week = Number(params.get('week')) || null
  const [family, setFamily] = useState<MarketFamily>('ALL')
  const [freshness, setFreshness] = useState<'ALL' | Freshness>('ALL')
  const [sort, setSort] = useState('TIME')
  const [query, setQuery] = useState('')
  const loader = useCallback(async (signal: AbortSignal): Promise<Board> => {
    const warnings: string[] = []
    const safe = async <T,>(name: string, operation: () => Promise<T>): Promise<T | null> => {
      try { return await operation() } catch (error) { if (signal.aborted) throw error; warnings.push(`${name}: ${error instanceof Error ? error.message : 'unavailable'}`); return null }
    }
    const [weeks, mlb, books] = await Promise.all([
      sport !== 'MLB' && ['ALL', 'NFL'].includes(sport) ? safe('NFL schedule', () => endpoints.nflWeeks(signal)) : null,
      sport !== 'NFL' && ['ALL', 'MLB'].includes(sport) ? safe('MLB crowd evidence', () => endpoints.marketPulse(day, signal, 'MLB')) : null,
      sport !== 'NFL' && ['ALL', 'MLB'].includes(sport) ? safe('MLB sportsbook', () => apiRequest<MarketBoardData>(`/markets/board?date=${day}`, {}, signal)) : null,
    ])
    const selected = season && week ? { season, week } : weeks?.current
    const nfl = weeks && selected ? await safe('NFL markets', () => apiRequest<NflBoard>(`/nfl/markets/${selected.season}/${selected.week}`, {}, signal)) : null
    return { data_mode: 'live', scope: sport, weeks, nfl, mlb, books, warnings }
  }, [sport, day, season, week])
  const resource = useApiResource(loader)
  const data = resource.data?.scope === sport ? resource.data : null
  const now = data?.nfl?.now_utc ?? new Date().toISOString()
  const update = (key: string, value: string) => setParams((old) => { const next = new URLSearchParams(old); next.set(key, value); return next }, { replace: true })
  const cards = useMemo(() => {
    const nfl = (data?.nfl?.events ?? []).map((event) => {
      const engine = event.engines.find((e) => e.engine_id === 'NFL_M1_XGBOOST') ?? event.engines[0]
      const blocks: NflMarketBlock[] = engine?.market_comparison?.markets.filter((b) => family === 'ALL' || b.market_type === family) ?? []
      const states = blocks.map((b) => b.fresh ? 'FRESH' as const : b.quote || b.quotes?.length ? 'STALE' as const : 'MISSING' as const)
      if (family === 'ALL' || family === 'MONEYLINE') states.push(...pregameProviders(event.prediction_market.providers).map((p) => quoteFreshness(p.observed_at, now)))
      const edges = nflEdges(blocks, family)
      return { key: event.event_id, league: 'NFL', time: event.kickoff_utc ?? '', names: `${event.away.name} ${event.home.name} ${event.away.abbreviation} ${event.home.abbreviation}`, states, gap: edges.length ? Math.max(...edges.map(Math.abs)) : -1, edge: edges.length ? Math.max(...edges) : -1, node: <NflCard key={event.event_id} event={event} family={family} now={now} /> }
    })
    const mlb = mlbOnly(data?.mlb?.rows ?? []).map((row) => {
      const books = data?.books?.rows.filter((b) => b.game_id === row.game_id) ?? []
      const filteredBooks = books.filter((b) => family === 'ALL' || b.market_type === ({ MONEYLINE: 'Moneyline', SPREAD: 'Run line', TOTAL: 'Total' } as const)[family])
      const states = filteredBooks.map((b) => quoteFreshness(b.observed_at, now))
      if (family === 'ALL' || family === 'MONEYLINE') states.push(...pregameProviders(row.providers).map((p) => quoteFreshness(p.observed_at, now)))
      // Only reviewed, fresh, pregame MLB home-win observations are comparable.
      const gaps = family === 'ALL' || family === 'MONEYLINE' ? mlbReviewedGaps(row, now) : []
      return { key: row.game_id, league: 'MLB', time: row.starts_at ?? '', names: `${row.away_team} ${row.home_team} ${row.away_abbrev} ${row.home_abbrev}`, states, gap: gaps.length ? Math.max(...gaps.map(Math.abs)) : -1, edge: -1, node: <MlbCard key={row.game_id} row={row} books={books} family={family} now={now} /> }
    })
    return [...mlb, ...nfl].filter((row) => row.names.toLowerCase().includes(query.toLowerCase()) && (freshness === 'ALL' || (freshness === 'MISSING' ? row.states.length === 0 || row.states.every((s) => s === 'MISSING') : row.states.includes(freshness))))
      .sort((a, b) => (sort === 'TIME' ? 0 : sort === 'EDGE' ? b.edge - a.edge : b.gap - a.gap) || a.time.localeCompare(b.time))
  }, [data, family, freshness, sort, query, now])
  const best = Math.max(-1, ...cards.map((c) => c.gap))
  return <div className="page unified-markets-page">
    <PageHeader eyebrow="THE PRICE OF THE GAME" title="Markets & Crowd" description="Model expectations, sportsbook lines and crowd observations. Separate markets. Clear sources. No invented prices." actions={<button className="secondary-button" onClick={resource.refresh}><RefreshCw size={15} /> Reload stored evidence</button>} />
    <section className="um-overview"><div><span>On your board</span><strong>{cards.length} games</strong><small>{sport === 'ALL' ? 'MLB daily slate + NFL selected week' : sport === 'NFL' ? `NFL ${data?.nfl?.season ?? ''} · Week ${data?.nfl?.week ?? '—'}` : `MLB · ${day}`}</small></div><div><span>Fresh comparable difference</span><strong>{best < 0 ? 'Unavailable' : `${(best * 100).toFixed(1)} pp`}</strong><small>Largest reviewed model/market gap; not a betting signal</small></div><div className="um-overview-note"><Radio size={23} aria-hidden="true" /><p>Sportsbooks, Kalshi and Polymarket stay separate. NFL evidence is research only. Reloading this page makes no provider call.</p></div></section>
    <div className="um-toolbar" aria-label="Market filters">
      {sport !== 'NFL' && <label>MLB date<input type="date" value={day} onChange={(e) => update('date', e.target.value)} /></label>}
      {sport !== 'MLB' && <label>NFL week<select aria-label="NFL market week" value={data?.nfl ? `${data.nfl.season}-${data.nfl.week}` : ''} onChange={(e) => { const [s, w] = e.target.value.split('-'); setParams((old) => { const next = new URLSearchParams(old); next.set('season', s); next.set('week', w); return next }, { replace: true }) }}>{data?.weeks?.weeks.map((w) => <option key={`${w.season}-${w.week}`} value={`${w.season}-${w.week}`}>{w.season} · Week {w.week}</option>)}</select></label>}
      <label>Market<select value={family} onChange={(e) => setFamily(e.target.value as MarketFamily)}><option value="ALL">All markets</option><option value="MONEYLINE">Moneyline</option><option value="SPREAD">{sport === 'MLB' ? 'Run line' : sport === 'NFL' ? 'Point spread' : 'Run line / point spread'}</option><option value="TOTAL">{sport === 'MLB' ? 'Total runs' : sport === 'NFL' ? 'Total points' : 'Total'}</option></select></label>
      <label>Evidence freshness<select value={freshness} onChange={(e) => setFreshness(e.target.value as typeof freshness)}><option value="ALL">All evidence</option><option value="FRESH">Fresh observation</option><option value="STALE">Stale reference</option><option value="MISSING">Missing prices</option></select></label>
      <label>Sort<select value={sort} onChange={(e) => setSort(e.target.value)}><option value="TIME">Game time</option><option value="GAP">Disagreement</option><option value="EDGE">Model edge</option></select></label>
      <label>Find a team<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Team or abbreviation" /></label>
    </div>
    <p className="um-scope-note">Fresh = observed within 4 hours, not a wagering-eligibility decision. NFL spread/total probabilities apply at the displayed book line; home-win crowd contracts are not spread/total prices.</p>
    {resource.loading && <p role="status">Loading persisted market evidence…</p>}
    {(resource.error || data?.warnings.length) ? <p className="um-error" role="alert">{resource.error ?? data?.warnings.join(' · ')}</p> : null}
    <div className="um-grid">{cards.map((card) => card.node)}</div>
    {!resource.loading && cards.length === 0 && <p className="um-empty">No games match these filters. Choose all evidence to retain games with missing prices.</p>}
    {data && <details className="um-evidence um-board-evidence"><summary>Source status & technical evidence</summary><p>Board read {stamp(now)}. This is not a source refresh.</p><p>{data.books?.feed_status?.reason}</p><p>{data.mlb?.state_reason}</p><p>NFL reference forecast drives sorting. Other imported models remain selectable per game. Unknown or incompatible gaps sort after comparable evidence; MLB historical references are not relabeled as current edges.</p><p>Prediction observations use exact canonical event mappings and pregame captures. A four-hour display freshness label never overrides semantic review or source restrictions.</p></details>}
  </div>
}
