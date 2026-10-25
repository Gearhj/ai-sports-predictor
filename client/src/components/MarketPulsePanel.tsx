import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Radio } from 'lucide-react'
import { Panel } from './ui'
import { endpoints } from '../services/api'
import { useApiResource } from '../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import type { MarketPulseGameDetail } from '../types/domain'

/**
 * A market probability, or an explicit absence.
 *
 * The shared rule: a probability is real only inside the open interval (0, 1)
 * and only with a complete two-sided observation behind it. The backend
 * already enforces the evidence half and sends null; this guards the domain so
 * a stray 0 or 1 can never surface as a confident "0.0%".
 */
function pct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value <= 0 || value >= 1) {
    return 'Not available'
  }
  return `${(value * 100).toFixed(1)}%`
}

/**
 * Compact, READ-ONLY market-pulse panel for Game Analysis. It never changes the
 * displayed forecast, ranking, recommendation, or any stake — it only shows the
 * observed sportsbook / prediction-market beliefs beside the AISP forecast.
 */
const QUALITY_LABEL: Record<string, string> = {
  ROBUST: 'robust', USABLE: 'usable', THIN: 'thin', STALE: 'stale', INCOMPLETE: 'incomplete',
  SEMANTICS_UNREVIEWED: 'market wording not yet reviewed', UNMAPPED: 'not matched to this game',
}

export const qualityLabel = (tier: string | null | undefined) => (tier ? QUALITY_LABEL[tier] ?? tier.toLowerCase().replaceAll('_', ' ') : '—')

export function MarketPulsePanel({ gameId, demo = false }: { gameId: string; demo?: boolean }) {
  // Shared-cache read: one request per game across every panel that wants it,
  // and no refetch when the reader comes back to a game they just left.
  const resource = useApiResource<MarketPulseGameDetail>(
    useCallback((signal: AbortSignal) => endpoints.marketPulseGame(gameId, signal), [gameId]),
    { key: cacheKey('game/market-pulse', { gameId }), staleTimeMs: STALE_TIME.game },
  )
  const data = resource.data

  const row = data?.row ?? null
  // Nothing is claimed about this game until the first read has settled, one
  // way or the other.
  if (resource.loading) return null
  if (!row || row.providers.length === 0) {
    // Honest: no comparable governed prediction-market contract for this game.
    return null
  }

  return (
    <Panel title="Market pulse" eyebrow="CROWD & MARKET · RESEARCH ONLY" className="section-panel" action={<span className="info-label"><Radio size={13} /> Not a model input</span>}>
      <div className="pulse-mini-grid">
        <div><span>AISP</span><strong>{pct(row.aisp_home_probability)}</strong></div>
        <div><span>Sportsbook</span><strong>{pct(row.sportsbook_home_probability)}</strong></div>
        <div><span>Polymarket</span><strong>{pct(row.polymarket_home_probability)}</strong></div>
        <div><span>Kalshi</span><strong>{pct(row.kalshi_home_probability)}</strong></div>
        <div>
          <span>Consensus</span>
          <strong>
            {row.consensus_eligible
              ? pct(row.research_consensus_home_probability)
              : 'Not available'}
          </strong>
        </div>
      </div>
      <p className="pulse-mini-note">
        Home win probability. Cross-market 30m move {row.movement_30m == null ? '—' : `${(row.movement_30m * 100).toFixed(1)} pts`} ·
        quality {qualityLabel(row.best_quality_tier)} · <Link to={`${demo ? '/demo' : ''}/market-pulse/${gameId}`}>full market pulse →</Link>
      </p>
    </Panel>
  )
}

export default MarketPulsePanel
