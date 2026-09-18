/**
 * The College Football research board and the single-game view.
 *
 * Deliberately small, like the NBA research surface it follows. College
 * Football has a virtual paper account but no bindable price, so this is not the start of a CFB
 * product — it is the view that makes an imported forecast discoverable and
 * readable without routing it through governed surfaces that cannot serve it.
 *
 * THE EMPTY STATE MATTERS AS MUCH AS THE CARD
 * -------------------------------------------
 * One imported forecast is not a slate. A date with nothing published has to
 * say that plainly, because a blank board otherwise reads as a broken page —
 * and because AISP has no admitted College Football schedule source, so the
 * games it cannot show are genuinely unknown to it rather than filtered out.
 */
import { GameStory } from '../components/story/GameStory'
import { useParams, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { CfbForecastCard } from '../components/cfb/CfbForecastCard'
import { useCfbGame, useCfbResearch } from '../hooks/useCfbResearch'
import '../components/cfb/cfb.css'
import { PaperStatusStrip } from '../components/readiness/PaperStatusStrip'

function Authority({ note }: { note: string }) {
  return (
    <p className="cfbpage__authority" role="note" data-testid="cfb-authority-note">
      {note}
    </p>
  )
}

export function CfbResearchPage() {
  const [params] = useSearchParams()
  const date = params.get('date')
  const { data, loading, error } = useCfbResearch(date)

  return (
    <div className="cfbpage">
      <PageHeader
        eyebrow="College Football"
        title="College Football forecasts"
        description={
          'Prospective forecasts verified from the producer’s slate discovery, shown with their original '
          + 'publication clock. Each game shows whether its forecast is currently decision-eligible, what the '
          + 'paper checkpoint (AISP_CFB_PAPER_POLICY_V1, virtual only) recorded, and why no price is held.'
        }
      />
      <PaperStatusStrip sport="CFB" />

      {loading && <p role="status">Loading research forecasts…</p>}
      {error && (
        <p role="alert" className="cfbpage__error">
          The College Football research read is unavailable.
        </p>
      )}

      {data && data.count === 0 && (
        <div className="cfbpage__empty" data-testid="cfb-empty-state">
          <p>{data.empty_state}</p>
          <p className="bx-dim">{data.coverage_note}</p>
        </div>
      )}

      {data && data.count > 0 && (
        <>
          <p className="cfbpage__coverage bx-dim" data-testid="cfb-coverage-note">
            {data.coverage_note}
          </p>
          <div className="cfbpage__list">
            {data.forecasts.map((forecast) => (
              <CfbForecastCard key={forecast.forecast_id} forecast={forecast} />
            ))}
          </div>
          <Authority note={data.authority.note} />
        </>
      )}
    </div>
  )
}

export function CfbResearchGamePage() {
  const { gameId } = useParams<{ gameId: string }>()
  const { data, loading, error } = useCfbGame(gameId)

  return (
    <div className="cfbpage">
      <PageHeader
        eyebrow="College Football"
        title={data ? data.game.matchup : 'College Football forecast'}
        description="Prospective forecast under a virtual-only paper policy. No real-money authority."
      />
      {loading && <p role="status">Loading the forecast…</p>}
      {error && (
        <p role="alert" className="cfbpage__error">
          No College Football research forecast is available for that game.
        </p>
      )}
      {data && (
        <>
          {data.canonical_game?.bound && data.canonical_game.game_id && <GameStory gameId={data.canonical_game.game_id} />}
          <CfbForecastCard forecast={data} />
          <Authority note={data.authority.note} />
        </>
      )}
    </div>
  )
}
