import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Radio } from 'lucide-react'
import { Panel } from './ui'
import { endpoints } from '../services/api'
import type { MarketPulseGameDetail } from '../types/domain'

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

/**
 * Compact, READ-ONLY market-pulse panel for Game Analysis. It never changes the
 * displayed forecast, ranking, recommendation, or any stake — it only shows the
 * observed sportsbook / prediction-market beliefs beside the AISP forecast.
 */
export function MarketPulsePanel({ gameId, demo = false }: { gameId: string; demo?: boolean }) {
  const [data, setData] = useState<MarketPulseGameDetail | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    endpoints
      .marketPulseGame(gameId, controller.signal)
      .then((d) => active && setData(d))
      .catch(() => active && setData(null))
      .finally(() => active && setLoaded(true))
    return () => {
      active = false
      controller.abort()
    }
  }, [gameId])

  const row = data?.row ?? null
  if (!loaded) return null
  if (!row || row.providers.length === 0) {
    // Honest: no comparable governed prediction-market contract for this game.
    return null
  }

  return (
    <Panel title="Market pulse" eyebrow="CROWD & MARKET · RESEARCH ONLY" className="section-panel" action={<span className="info-label"><Radio size={13} /> Not a model input</span>}>
      <div className="pulse-mini-grid">
        <div><span>AISP</span><strong>{pct(row.aisp_home_probability)}</strong></div>
        <div><span>Sportsbook</span><strong
          title={row.sportsbook_home_probability == null ? 'No reviewed sportsbook quote' : undefined}
          aria-label={row.sportsbook_home_probability == null ? 'Sportsbook home win probability unavailable — no reviewed quote' : undefined}
        >{pct(row.sportsbook_home_probability)}</strong></div>
        <div><span>Polymarket</span><strong>{pct(row.polymarket_home_probability)}</strong></div>
        <div><span>Kalshi</span><strong>{pct(row.kalshi_home_probability)}</strong></div>
        <div><span>Consensus</span><strong>{row.consensus_eligible ? pct(row.research_consensus_home_probability) : '—'}</strong></div>
      </div>
      <p className="pulse-mini-note">
        Home win probability. Cross-market 30m move {row.movement_30m == null ? '—' : `${(row.movement_30m * 100).toFixed(1)} pts`} ·
        quality {row.best_quality_tier ?? '—'} · <Link to={`${demo ? '/demo' : ''}/market-pulse/${gameId}`}>full market pulse →</Link>
      </p>
    </Panel>
  )
}

export default MarketPulsePanel
