/**
 * One NBA game's imported research forecast.
 *
 * A separate route from the governed `/games/:gameId` analysis view on purpose.
 * That view is built on forecasts, odds, evaluations and positions; NBA has
 * exactly one of those four, and rendering an NBA game there would surround a
 * research forecast with empty market and decision panels that read as data
 * which failed to load rather than as authority that does not exist.
 */
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { PageHeader } from '../components/ui'
import { NbaResearchCard } from '../components/nba/NbaResearchCard'
import { useNbaResearch } from '../hooks/useNbaResearch'
import { GameStory } from '../components/story/GameStory'

export function NbaResearchGamePage() {
  const { gameId } = useParams<{ gameId: string }>()
  const { data, loading, error } = useNbaResearch(gameId)
  const forecasts = data?.forecasts ?? []

  return (
    <div className="page">
      <PageHeader
        eyebrow="NBA · RESEARCH"
        title="NBA research forecast"
        description="One imported research forecast. No odds, evaluation, position or bankroll exists for NBA."
      />

      <p className="nbares__back">
        <Link to="/nba/research"><ArrowLeft size={13} aria-hidden="true" /> All NBA research forecasts</Link>
      </p>

      {gameId && <GameStory gameId={gameId} />}

      {loading && !data && <p className="muted" role="status">Loading…</p>}
      {error && !data && (
        <p className="muted" role="status">This NBA research forecast is unavailable right now.</p>
      )}
      {data && forecasts.length === 0 && (
        <p className="muted" data-testid="nba-research-empty">
          No research forecast has been imported for this game.
        </p>
      )}

      {forecasts.map((forecast) => (
        <NbaResearchCard key={forecast.forecast_id} forecast={forecast} />
      ))}

      {data && forecasts.length > 0 && <p className="muted nbares__scope">{data.note}</p>}
    </div>
  )
}
