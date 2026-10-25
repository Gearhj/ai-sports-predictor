/**
 * The NHL research board and the single-game view. Research only.
 *
 * NHL Gen1 forecasts arrive from Model Studio through the exchange
 * (NHL_GEN1_FORECAST_CONTRACT_V1). AISP collects no NHL price and holds no NHL
 * wagering policy, so this surface shows forecasts, goalie assumptions and
 * results -- never an edge, a pick or a position.
 *
 * Real forecasts are the default. Preview fixtures and Model Studio synthetic
 * examples appear only behind "Show test data", labelled on every card.
 */
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { NhlForecastBlock, NhlForecastCard, age, etDateTime } from '../components/nhl/NhlForecastCard'
import { useNhlBoard, useNhlGame, useNhlTestData } from '../hooks/useNhlResearch'
import '../components/nhl/nhl.css'

export function NhlTestDataToggle({ hidden }: { hidden?: number }) {
  const [on, setOn] = useNhlTestData()
  // Production never offers test data: the toggle exists only where the dev-only
  // NHL_TEST_DATA_ENABLED flag is on (tests and isolated previews).
  const probe = useNhlBoard(null, false)
  if (!probe.data?.test_data_available) return null
  return (
    <label className={`nhl-toggle${on ? ' nhl-toggle--on' : ''}`} data-testid="nhl-test-toggle">
      <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
      Show test data{!on && hidden ? ` (${hidden} hidden)` : on ? ' · fixtures shown, labelled' : ''}
    </label>
  )
}

export function NhlResearchPage() {
  const [params] = useSearchParams()
  const date = params.get('date')
  const [testData] = useNhlTestData()
  const { data, loading, error } = useNhlBoard(date, testData)
  const withTest = (href: string) => (testData ? `${href}${href.includes('?') ? '&' : '?'}nhlTest=1` : href)
  return (
    <div className="nhlpage">
      <PageHeader
        eyebrow={data?.wagering.enabled ? 'NHL · Virtual paper' : 'NHL · Research only'}
        title="NHL forecasts"
        description={data?.wagering.enabled
          ? 'NHL Gen1 forecasts, run by AISP in-process from the frozen accepted model. Regulation (3-way at 60:00) '
            + 'and the final (2-way, including overtime and shootout) are separate outcomes. Goals, not points. '
            + 'The Odds API prices each game 75–35 minutes before puck drop and the virtual paper checkpoint decides '
            + '30–15 minutes before; no real money.'
          : 'NHL Gen1 research forecasts. Regulation (3-way at 60:00) and the final '
            + '(2-way, including overtime and shootout) are separate outcomes. Goals, not points. No NHL '
            + 'market price is collected and NHL wagering is disabled.'}
      />
      {loading && !data && <p role="status">Loading NHL forecasts…</p>}
      {error && !data && <p role="alert">The NHL research read is unavailable.</p>}
      {data && (
        <>
          <div className="nhl-toolbar">
            <p className="nhlpage__note" data-testid="nhl-counts">{data.counts.games} {testData ? 'games incl. test data' : 'real games'} · {data.counts.with_forecast} with a forecast · {data.counts.finals} finals</p>
            <NhlTestDataToggle hidden={data.test_data_rows} />
          </div>
          {testData && data.fixture_notice && <p className="nhlpage__fixture" role="note" data-testid="nhl-fixture-notice">{data.fixture_notice}</p>}
          {data.dates.length > 1 && (
            <nav className="nhlpage__dates" aria-label="NHL dates">
              <Link to={withTest('/nhl/research')} aria-current={!date}>All dates</Link>
              {data.dates.map((d) => <Link key={d} to={withTest(`/nhl/research?date=${d}`)} aria-current={date === d}>{d}</Link>)}
            </nav>
          )}
          {data.games.length === 0
            ? <p className="nhlpage__fixture">No real NHL forecast is held{date ? ` for ${date}` : ''}. AISP has no admitted NHL schedule source; games appear when a published forecast names them.</p>
            : <div className="nhlpage__list nhlpage__list--grid">{data.games.map((row) => <NhlForecastCard key={row.canonical_game_id} row={row} />)}</div>}
          <p className="nhlpage__note" role="note">{data.markets.statement} {data.wagering.statement}</p>
        </>
      )}
    </div>
  )
}

export function NhlResearchGamePage() {
  const { gameId } = useParams<{ gameId: string }>()
  const { data, loading, error } = useNhlGame(gameId)
  const others = data ? data.history.filter((h) => !h.is_current) : []
  return (
    <div className="nhlpage">
      <PageHeader
        eyebrow={data?.wagering.enabled ? 'NHL · Virtual paper' : 'NHL · Research only'}
        title={data ? `${data.away?.name ?? 'Away'} at ${data.home?.name ?? 'Home'}` : 'NHL game'}
        description={data?.wagering.enabled
          ? 'NHL Gen1 forecast with the paper checkpoint\'s recorded decision and position, as stored. Virtual paper only.'
          : 'NHL Gen1 research forecast. No market price, edge, recommendation or position exists for NHL.'}
      />
      {loading && !data && <p role="status">Loading the game…</p>}
      {error && !data && <p role="alert">AISP holds no NHL game or forecast for that id.</p>}
      {data && (
        <>
          <NhlForecastCard row={data} link={false} technical={(
            <>
              <h4 style={{ margin: '0.7rem 0 0.3rem' }} data-testid="nhl-history">Forecast history · {data.history.length}</h4>
              <ol style={{ margin: 0, paddingLeft: '1.1rem' }}>
                {data.history.map((h) => (
                  <li key={h.forecast_id} data-testid="nhl-history-item">
                    <b>{h.is_current ? 'Current' : 'Superseded'}</b> · {h.model_id} v{h.model_version} · created {etDateTime(h.clocks.created_utc)} · {age(h.age_seconds)}
                    <div style={{ opacity: 0.7, overflowWrap: 'anywhere', wordBreak: 'break-all' }}>{h.forecast_id}</div>
                  </li>
                ))}
              </ol>
              {others.length > 0 && (
                <details>
                  <summary>Superseded forecast detail ({others.length})</summary>
                  {others.map((h) => <NhlForecastBlock key={h.forecast_id} row={data} forecast={h} />)}
                </details>
              )}
              <h4 style={{ margin: '0.7rem 0 0.3rem' }}>Import receipts · {data.import_receipts.length}</h4>
              <ul style={{ margin: 0, paddingLeft: '1.1rem' }} data-testid="nhl-receipts">
                {data.import_receipts.map((r, i) => (
                  <li key={`${r.identity}-${i}`}><b>{r.disposition}</b> {r.kind.toLowerCase()} · {etDateTime(r.received_at)}{r.problems.length ? ` · ${r.problems.join(', ')}` : ''}</li>
                ))}
              </ul>
            </>
          )} />
          {data.revision_count > 1 && <p className="nhlpage__note">{data.revision_count} forecast revisions held; the current one is shown. History is under Technical details.</p>}
          <p className="nhlpage__note"><Link to="/nhl/research">All NHL forecasts</Link></p>
        </>
      )}
    </div>
  )
}
