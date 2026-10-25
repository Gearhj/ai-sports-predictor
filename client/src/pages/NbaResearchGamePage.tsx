/**
 * One NBA game: its production package (prospective), any HISTORICAL 2025-26
 * reconstruction, the retained final, and each channel's score -- plus the
 * earlier research forecast when one exists.
 *
 * A separate route from the governed `/games/:gameId` analysis view on purpose:
 * NBA holds no price and no position, and rendering it there would surround the
 * forecast with empty market and decision panels that read as data which failed
 * to load rather than as authority that does not exist.
 */
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { PageHeader } from '../components/ui'
import { NbaResearchCard } from '../components/nba/NbaResearchCard'
import { EvidenceBadge, NbaChannelPanel, NbaChannelScores } from '../components/nba/NbaChannels'
import { useNbaResearch } from '../hooks/useNbaResearch'
import { useNbaGen1Game } from '../hooks/useNbaGen1'
import { GameStory } from '../components/story/GameStory'

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/New_York' }) + ' ET'
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function short(sha: string | null | undefined): string {
  return sha ? `${sha.slice(0, 12)}…` : '—'
}

export function NbaResearchGamePage() {
  const { gameId } = useParams<{ gameId: string }>()
  const { data, loading, error } = useNbaResearch(gameId)
  const gen1 = useNbaGen1Game(gameId)
  const forecasts = data?.forecasts ?? []
  const game = gen1.data
  const home = game?.teams.home?.abbreviation ?? 'Home'
  const away = game?.teams.away?.abbreviation ?? 'Away'

  return (
    <div className="page">
      <PageHeader
        eyebrow="NBA · GEN1"
        title={game ? `${away} at ${home}` : 'NBA game'}
        description={'Ridge expected points, the Elo win probability and the joint-distribution probabilities are '
          + 'three separately estimated channels, shown side by side and never blended. No NBA price or position exists.'}
      />

      <p className="nbares__back">
        <Link to="/nba/research"><ArrowLeft size={13} aria-hidden="true" /> All NBA forecasts</Link>
      </p>

      {/* The story reads AISP's canonical id; an ESPN id in the URL is resolved first. */}
      {(game?.game_id ?? (gameId && UUID.test(gameId) ? gameId : null)) && (
        <GameStory gameId={(game?.game_id ?? gameId) as string} />
      )}

      {game && (
        <section className="nbares" data-testid="nba-gen1-game">
          <header className="nbares__head">
            <span className="nbares__label">ESPN {game.espn_game_id ?? '—'} · {game.status.toLowerCase()}</span>
            <h3>{away} at {home}</h3>
            <small className="bx-dim">Tipoff {when(game.tipoff_utc)}</small>
          </header>
          {game.final && (
            <p data-testid="nba-final">
              Final: {away} {game.final.away} – {home} {game.final.home}
              {game.finals[0] && (
                <small className="bx-dim"> · {game.finals[0].source}{game.finals[0].overtime ? ' · overtime' : ''} ·
                  source object {short(game.finals[0].source_object_sha256)} · no completion clock in the source</small>
              )}
            </p>
          )}

          {game.production_forecasts.map((f) => (
            <article key={f.forecast_id} className="nbag__block" data-testid="nba-production-forecast">
              <p><EvidenceBadge evidenceClass={f.evidence_class} />{f.label}</p>
              <NbaChannelPanel channels={f.channels} home={home} away={away} />
              <dl className="nbares__facts nbag__facts">
                <div><dt>Forecast</dt><dd>{f.forecast_id}</dd></div>
                <div><dt>Package · forecast bytes</dt><dd>{short(f.package_index_sha256)} · {short(f.forecast_sha256)}</dd></div>
                <div><dt>Model handoff</dt><dd>{short(f.handoff_sha256)}</dd></div>
                <div><dt>Publication clock</dt><dd>{when(f.clocks.designated_publication_utc)} ({f.clocks.publication_clock_source})</dd></div>
                <div><dt>Decision eligibility</dt><dd>{f.eligibility.now?.state?.replace(/_/g, ' ').toLowerCase() ?? 'no discovery'}</dd></div>
                <div><dt>Orientation</dt><dd>{f.orientation ?? '—'}</dd></div>
              </dl>
            </article>
          ))}

          {game.historical_forecasts.map((h) => (
            <article key={h.codex_game_id} className="nbag__block" data-testid="nba-historical-forecast">
              <p><EvidenceBadge evidenceClass={h.evidence_class} />{h.qualification}</p>
              <NbaChannelPanel channels={h.channels} home={home} away={away} />
              <dl className="nbares__facts nbag__facts">
                <div><dt>Assessment receipt</dt><dd>{short(h.assessment_receipt_sha256)}</dd></div>
                <div><dt>Joint receipt · cells</dt><dd>{short(h.joint_receipt_sha256)} · {short(h.joint_counts_int64_le_sha256)}</dd></div>
                <div><dt>Point model</dt><dd>{h.point_model.id} · {short(h.point_model.sha256)}</dd></div>
              </dl>
            </article>
          ))}

          <NbaChannelScores scores={game.evaluations} />

          <p className="nbares__unavailable" data-testid="nba-price-note">
            No NBA price: {game.price.detail}
          </p>
        </section>
      )}

      {loading && !data && <p className="muted" role="status">Loading…</p>}
      {error && !data && !game && (
        <p className="muted" role="status">This NBA game is unavailable right now.</p>
      )}

      {forecasts.map((forecast) => (
        <NbaResearchCard key={forecast.forecast_id} forecast={forecast} />
      ))}

      {data && forecasts.length > 0 && <p className="muted nbares__scope">{data.note}</p>}
      {data && forecasts.length === 0 && !game && (
        <p className="muted" data-testid="nba-research-empty">No forecast has been imported for this game.</p>
      )}
    </div>
  )
}
