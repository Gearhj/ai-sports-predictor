/**
 * NHL on Games & Forecasts: one row per NHL game on this Eastern date.
 *
 * Each row shows the status (including Final OT and Final SO), the score in
 * goals, the research forecast (final 2-way, and regulation 3-way when the model
 * supplies it), goalie assumptions and forecast age, and -- since the NHL paper era
 * opened -- the checkpoint's recorded position for the game. Never a derived edge.
 */
import { Link } from 'react-router-dom'
import { useNhlBoard, useNhlTestData } from '../../hooks/useNhlResearch'
import { NhlTestDataToggle } from '../../pages/NhlResearchPage'
import type { NhlGameRow } from '../../types/nhlResearch'
import { StatusLabel, age, pct } from './NhlForecastCard'
import './nhl.css'

const ET = 'America/New_York'
const etToday = () => new Date().toLocaleDateString('en-CA', { timeZone: ET })
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' }) + ' ET' : 'TBD')
const goalie = (s: string) => s.toLowerCase()

function Forecast({ row }: { row: NhlGameRow }) {
  const f = row.forecast
  if (!f) return <span className="bx-dim" data-testid="nhl-slate-no-forecast">Forecast pending</span>
  const home = row.home?.abbreviation ?? 'Home'
  const away = row.away?.abbreviation ?? 'Away'
  const reg = f.probabilities.regulation
  return (
    <span>
      <small className="nhl-forecast-label">{row.forecast_label}</small>{' '}
      <b>Final {away} {pct(f.probabilities.final.away, 0)} · {home} {pct(f.probabilities.final.home, 0)}</b>
      <small> · {reg ? `regulation ${pct(reg.away, 0)} / tie ${pct(reg.tie, 0)} / ${pct(reg.home, 0)}` : 'regulation not modelled'}
        {' '}· goalies {goalie(f.goalies.away.status)} / {goalie(f.goalies.home.status)} · {age(f.age_seconds)} · v{f.model_version}</small>
    </span>
  )
}

export function NhlSlateSection({ date }: { date?: string | null }) {
  const day = date || etToday()
  const [testData] = useNhlTestData()
  const { data, loading, error } = useNhlBoard(day, testData)
  const rows = data?.games ?? []
  return (
    <section aria-label="NHL games" style={{ display: 'grid', gap: 8 }} data-testid="nhl-slate">
      <header>
        <h2 className="bx-h2" style={{ margin: 0 }}>NHL <small className="bx-dim" style={{ fontSize: '0.75rem' }}>{data?.wagering.enabled ? 'virtual paper' : 'research only'}</small></h2>
        <p className="bx-dim" style={{ margin: '2px 0 0' }}>
          NHL Gen1 forecasts · final 2-way including OT/SO, regulation 3-way when modelled · goals ·
          {' '}{data?.wagering.enabled
            ? 'The Odds API prices each game 75–35 min before puck drop · paper checkpoint 30–15 min before'
            : 'No NHL market prices collected · no edges, picks or positions'}
        </p>
        {data && <div style={{ marginTop: 6 }}><NhlTestDataToggle hidden={data.test_data_rows} /></div>}
      </header>
      {loading && !data && <p className="bx-empty" role="status">Loading NHL…</p>}
      {error && !data && <p className="bx-empty" role="alert">NHL forecasts are unavailable.</p>}
      {data && rows.length === 0 && (
        <p className="bx-empty">No real NHL forecast is held for {day}. <Link to="/nhl/research">All NHL forecasts</Link></p>
      )}
      {testData && data?.fixture_notice && rows.some((r) => r.is_fixture) && <p className="nhlpage__fixture" role="note">{data.fixture_notice}</p>}
      {rows.length > 0 && (
        <div role="table" className="nhlslate">
          {rows.map((row) => (
            <div role="row" key={row.canonical_game_id} className="nhlslate__row" data-testid={`nhl-slate-${row.source_game_id}`}>
              <span role="cell" className="bx-num">{time(row.start_utc)}</span>
              <span role="cell">
                <Link to={`/nhl/games/${row.source_game_id}`}>{row.away?.abbreviation} @ {row.home?.abbreviation}</Link>
                {row.score && <b> {row.score.away}–{row.score.home}</b>}{' '}
                {row.is_fixture && <span className="nhl-badge nhl-badge--fixture" title="Invented test data, not a real game or forecast">{row.record_class === 'SYNTHETIC_FIXTURE' ? 'SYNTHETIC' : 'FIXTURE'}</span>}
              </span>
              <span role="cell"><Forecast row={row} /></span>
              <span role="cell"><StatusLabel row={row} />{row.paper?.position
                ? <small className="nhl-forecast-label" data-testid="nhl-slate-position"> · paper position ${(row.paper.position.stake ?? 0).toFixed(2)}{row.paper.position.status === 'SETTLED' ? ` · ${(row.paper.position.result ?? 'settled').toLowerCase()}` : ' · open'}</small>
                : row.paper ? <small className="nhl-forecast-label"> · checkpoint: {(row.paper.decision.reason ?? row.paper.decision.recommendation).replace(/_/g, ' ').toLowerCase()}</small> : null}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
