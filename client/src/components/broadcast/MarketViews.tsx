/**
 * The view that sits beside the price board: Replay. (The RFF Lab was removed 11-01 by the owner.)
 *
 * It is a view of the page you are already on, not a new destination: something you do to the board in front
 * of you.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { apiRequest } from '../../services/api'
import { MarketReplay } from './MarketReplay'
import { Panel } from './primitives'
import type {
  MarketTape,
} from '../../types/marketIntelligence'

export type Family = 'MONEYLINE' | 'SPREAD' | 'TOTAL'

export interface ReplayChoice {
  gameId: string
  league: 'MLB' | 'NFL'
  label: string
  startsAt: string
  /** Whether the board already knows this game has a persisted quote. */
  hasQuote: boolean
}

/* -- Replay ---------------------------------------------------------------- */

export function ReplayView({
  family, games, gameId, onSelectGame, selectedBook, onSelectBook,
}: {
  family: Family
  games: ReplayChoice[]
  gameId: string | null
  onSelectGame: (id: string) => void
  selectedBook: string | null
  onSelectBook: (id: string | null) => void
}) {
  const loader = useCallback(
    async (signal: AbortSignal): Promise<MarketTape> => {
      if (!gameId) throw new Error('No game is selected.')
      return apiRequest<MarketTape>(`/markets/tape/${gameId}?market=${family}`, {}, signal)
    },
    [gameId, family],
  )
  const resource = useApiResource<MarketTape>(loader, {
    key: cacheKey('markets/tape', { gameId, family }), staleTimeMs: STALE_TIME.game,
  })
  // A tape for the previous selection is not this selection's tape; showing it
  // would put one game's movement under another game's name.
  const tape = resource.data && resource.data.game_id === gameId ? resource.data : null

  return (
    <Panel eyebrow="Market replay" title="Watch the price move"
      action={
        <label className="bx-field">Game
          <select aria-label="Replay game" value={gameId ?? ''}
            onChange={(event) => onSelectGame(event.target.value)}>
            {games.length === 0 && <option value="">No game on the board</option>}
            {games.map((choice) => (
              <option key={choice.gameId} value={choice.gameId}>
                {choice.league} · {choice.label}{choice.hasQuote ? '' : ' · no price stored'}
              </option>
            ))}
          </select>
        </label>
      }>
      {games.length === 0
        ? <p className="bx-empty">
          No game is on the board for this date and filter, so there is nothing to replay.
        </p>
        : resource.loading && !tape
          ? <p className="bx-empty" role="status">Loading the stored tape…</p>
          : resource.error
            ? <p className="bx-empty" role="alert">{resource.error}</p>
            : tape
              ? <MarketReplay tape={tape} family={family} selectedBook={selectedBook}
                onSelectBook={onSelectBook} />
              : <p className="bx-empty">Select a game to replay.</p>}
    </Panel>
  )
}
