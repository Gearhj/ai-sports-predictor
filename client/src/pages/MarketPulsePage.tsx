import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Radio, ShieldAlert } from 'lucide-react'
import { DataModeBanner, DataStatePanel, formatPct, MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { formatDate } from '../services/format'
import { demoMarketPulse } from '../data/demo'
import { useApiResource } from '../hooks/useApiResource'
import { endpoints } from '../services/api'
import type { MarketPulseRow } from '../types/domain'

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function signedPts(value: number | null | undefined): string {
  if (value == null) return '—'
  const p = value * 100
  return `${p > 0 ? '+' : ''}${p.toFixed(1)} pts`
}

function tierClass(tier: string | null): string {
  return `pill pill--${(tier || 'unknown').toLowerCase()}`
}

export function MarketPulsePage({ demo = false }: { demo?: boolean }) {
  const today = new Date().toISOString().slice(0, 10)
  const [slateDate, setSlateDate] = useState(today)
  const [sport, setSport] = useState<string | null>(null)
  const loader = useMemo(
    () => (signal?: AbortSignal) => endpoints.marketPulse(slateDate, signal, sport),
    [slateDate, sport],
  )
  const { data, state, loading, error, refresh } = useApiResource(loader, demo ? { demoData: demoMarketPulse } : {})

  const header = (
    <PageHeader
      eyebrow="RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE"
      title="ASP Crowd & Market Pulse"
      description="Real-time comparison of AISP forecasts, sportsbook consensus, and prediction-market beliefs."
      actions={data ? <span className="page-actions__date">{formatDate(data.date)}</span> : undefined}
    />
  )

  if (!data || state === 'empty' || state === 'unavailable' || state === 'loading') {
    return (
      <div className="page market-pulse-page">
        {header}
        <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />
        <DataStatePanel
          state={state}
          emptyTitle="No governed prediction-market evidence yet"
          emptyDetail="Discovery and snapshots persist here once the observer is armed and mapped markets exist for this date. No events are invented for sports without governed data."
        />
      </div>
    )
  }

  const rows = data.rows
  const bestDisagreement = rows.reduce<MarketPulseRow | null>((best, r) => {
    if (r.diff_aisp_prediction == null) return best
    if (!best || Math.abs(r.diff_aisp_prediction) > Math.abs(best.diff_aisp_prediction ?? 0)) return r
    return best
  }, null)

  return (
    <div className="page market-pulse-page">
      {header}
      <DataModeBanner state={state} loading={loading} error={error} onRetry={refresh} />

      <div className="research-note" role="note">
        <ShieldAlert size={16} />
        <p>
          This is a read-only market-observation layer. Prediction-market prices are shown for research only — they never
          change a forecast, ranking, recommendation, or the $1,000 experiment, and there is no trading control anywhere.
          {data.public_display_enabled ? '' : ' Public display is gated (LOCAL_PRIVATE_RESEARCH).'}
        </p>
      </div>

      <section className="metric-grid metric-grid--four">
        <MetricCard label="Mapped games" value={`${data.summary?.mapped ?? 0} / ${rows.length}`} detail="Fail-closed identity" />
        <MetricCard label="Cross-provider consensus" value={`${data.summary?.consensus_available ?? 0}`} detail="Eligible games" />
        <MetricCard
          label="Biggest AISP–market gap"
          value={bestDisagreement ? signedPts(bestDisagreement.diff_aisp_prediction) : '—'}
          detail={bestDisagreement ? `${bestDisagreement.away_abbrev} @ ${bestDisagreement.home_abbrev}` : 'None'}
          trend="neutral"
        />
        <MetricCard label="Review required" value={`${data.summary?.review_required ?? 0}`} detail="Mapping / semantics" />
      </section>

      {data.sports_available.length > 0 && (
        <div className="tab-bar" role="tablist" aria-label="Sport filter">
          <button role="tab" className={sport == null ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(null)}>All sports</button>
          {data.sports_available.map((s) => (
            <button key={s} role="tab" className={sport === s ? 'tab-button tab-button--active' : 'tab-button'} onClick={() => setSport(s)}>{s}</button>
          ))}
          <label className="inline-field">
            <span>Date</span>
            <input type="date" value={slateDate} onChange={(event) => setSlateDate(event.target.value)} />
          </label>
        </div>
      )}

      <Panel title="Cross-market probability board" eyebrow="AISP · SPORTSBOOK · POLYMARKET · KALSHI" action={<span className="info-label"><Radio size={13} /> Home win probability</span>}>
        <TableScroll ariaLabel="Cross-market probability comparison">
          <table className="data-table market-pulse-table">
            <thead>
              <tr>
                <th>Game</th><th>AISP</th><th>Sportsbook</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th>
                <th>AISP–Mkt</th><th>Poly–Kalshi</th><th>5m</th><th>30m</th><th>Quality</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={12} className="empty-row">No mapped prediction-market games for this date.</td></tr>}
              {rows.map((row) => (
                <tr key={row.game_id}>
                  <td>
                    <Link to={`${demo ? '/demo' : ''}/market-pulse/${row.game_id}`}>
                      <strong>{row.away_abbrev ?? '—'} @ {row.home_abbrev ?? '—'}</strong>
                    </Link>
                    <small>{row.sport}</small>
                  </td>
                  <td>{pct(row.aisp_home_probability)}</td>
                  <td>{pct(row.sportsbook_home_probability)}</td>
                  <td>{pct(row.polymarket_home_probability)}</td>
                  <td>{pct(row.kalshi_home_probability)}</td>
                  <td>{row.consensus_eligible ? pct(row.research_consensus_home_probability) : <span title={row.consensus_note ?? ''}>No consensus</span>}</td>
                  <td>{signedPts(row.diff_aisp_prediction)}</td>
                  <td>{signedPts(row.diff_polymarket_kalshi)}</td>
                  <td>{signedPts(row.movement_5m)}</td>
                  <td>{signedPts(row.movement_30m)}</td>
                  <td><span className={tierClass(row.best_quality_tier)}>{row.best_quality_tier ?? 'UNMAPPED'}</span></td>
                  <td>
                    <span className={`pill pill--${row.mapping_status.toLowerCase()}`}>{row.mapping_status}</span>
                    {row.providers.some((p) => p.in_play) && <span className="pill pill--inplay">IN PLAY</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </Panel>

      <Panel title="Research market consensus" eyebrow="NOT A MODEL INPUT · NOT A WAGER RECOMMENDATION">
        <ul className="pulse-consensus-list">
          {rows.map((row) => (
            <li key={row.game_id}>
              <strong>{row.away_abbrev} @ {row.home_abbrev}</strong>
              <span>{row.consensus_eligible ? `Median ${pct(row.research_consensus_home_probability)} · dispersion ${signedPts(row.cross_market_dispersion)}` : (row.consensus_note ?? 'No consensus')}</span>
            </li>
          ))}
        </ul>
      </Panel>

      <p className="page-footnote">
        Prices are official-API observations. A missing value is shown as “—”, “No consensus”, or an honest status — never a
        fabricated zero. Prediction-market data is never used as a plate-appearance or model predictor.
      </p>
    </div>
  )
}

export default MarketPulsePage
