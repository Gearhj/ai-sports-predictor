import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ShieldAlert } from 'lucide-react'
import { PageHeader, Panel, TableScroll } from '../components/ui'
import { endpoints } from '../services/api'
import type { MarketPulseGameDetail } from '../types/domain'

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)}%`
}

export function MarketPulseGamePage() {
  const { gameId } = useParams()
  const [data, setData] = useState<MarketPulseGameDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    if (!gameId) return
    endpoints
      .marketPulseGame(gameId)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e?.message ?? e)))
    return () => {
      active = false
    }
  }, [gameId])

  return (
    <div className="page market-pulse-game-page">
      <PageHeader
        eyebrow="RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE"
        title="Market pulse — game detail"
        description="Cross-market probability timeline, order books, movement events, contract semantics and mapping."
        actions={<Link to="/market-pulse" className="link-back"><ArrowLeft size={14} /> Back to board</Link>}
      />
      <div className="research-note" role="note">
        <ShieldAlert size={16} />
        <p>Read-only observation. {data?.lead_lag_note ?? 'COLLECTING LEAD-LAG EVIDENCE'}. No trading, no model input.</p>
      </div>

      {error && <Panel title="Unavailable"><p className="empty-hint">{error}</p></Panel>}
      {!data && !error && <Panel title="Loading"><p className="empty-hint">Loading market evidence…</p></Panel>}

      {data && (
        <>
          <Panel title="Cross-market probability timeline" eyebrow="AISP · SPORTSBOOK · POLYMARKET · KALSHI · CONSENSUS">
            {data.timeline.length === 0 ? (
              <p className="empty-hint">No timeline points yet — collecting evidence.</p>
            ) : (
              <TableScroll ariaLabel="Probability timeline">
                <table className="data-table">
                  <thead><tr><th>Time</th><th>Polymarket</th><th>Kalshi</th><th>Consensus</th></tr></thead>
                  <tbody>
                    {data.timeline.map((p) => (
                      <tr key={p.time}><td>{p.time.slice(11, 16)}</td><td>{pct(p.polymarket)}</td><td>{pct(p.kalshi)}</td><td>{pct(p.consensus)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            )}
          </Panel>

          <div className="analysis-grid">
            <Panel title="Order books" eyebrow="BEST BID / ASK / MID / MICRO">
              {data.order_books.length === 0 ? <p className="empty-hint">No order book captured.</p> : (
                <table className="data-table">
                  <thead><tr><th>Provider</th><th>Bid</th><th>Ask</th><th>Mid</th><th>Micro</th><th>Spread</th><th>Quality</th></tr></thead>
                  <tbody>
                    {data.order_books.map((b) => (
                      <tr key={b.provider}><td>{b.provider}</td><td>{b.best_bid ?? '—'}</td><td>{b.best_ask ?? '—'}</td><td>{b.midpoint ?? '—'}</td><td>{b.microprice ?? '—'}</td><td>{b.spread ?? '—'}</td><td>{b.quality_tier ?? '—'}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
            <Panel title="Movement & signals" eyebrow="OBSERVED MARKET SIGNAL — REVIEW ONLY">
              {data.movement_events.length === 0 ? <p className="empty-hint">No signals recorded.</p> : (
                <ul className="pulse-consensus-list">
                  {data.movement_events.slice(0, 12).map((m, i) => (
                    <li key={i}><strong>{m.alert_type}</strong><span>{m.provider} · {m.time.slice(11, 16)}{m.magnitude != null ? ` · ${(m.magnitude * 100).toFixed(1)} pts` : ''}</span></li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <div className="analysis-grid">
            <Panel title="Contract details" eyebrow="SEMANTICS PRESERVED">
              {data.contracts.length === 0 ? <p className="empty-hint">No contracts.</p> : (
                <ul className="pulse-consensus-list">
                  {data.contracts.map((c, i) => (
                    <li key={i}><strong>{c.provider} · {c.market_type} {c.selection}{c.line_value != null ? ` ${c.line_value}` : ''}</strong><span>{c.market_question ?? '—'} · {c.semantics_status} · {c.line_source}</span></li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Mapping" eyebrow="FAIL-CLOSED IDENTITY">
              {data.mappings.length === 0 ? <p className="empty-hint">No mapping.</p> : (
                <ul className="pulse-consensus-list">
                  {data.mappings.map((m, i) => (
                    <li key={i}><strong>{m.provider}</strong><span>{m.mapping_status} · {m.semantics_status} · game-time {m.game_time_agreement ?? '—'}</span></li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  )
}

export default MarketPulseGamePage
