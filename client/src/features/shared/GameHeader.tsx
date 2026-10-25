/**
 * Verdict-first game header: the score (live or final), our paper bet and its
 * result, and the way into the simulator. Sits at the top of every game brief.
 */
import { Link } from 'react-router-dom'
import { Dices } from 'lucide-react'
import { ScoreBar } from './ScoreBug'
import { allPositions, american, resultOf, usd, useLedgerData } from '../pulse/pulseData'

export function GameHeader({ gameId, sport, away, home, startsAt }: {
  gameId: string; sport: string; away: { abbr: string; name: string }; home: { abbr: string; name: string }; startsAt?: string | null
}) {
  const ledger = useLedgerData()
  const bet = allPositions(ledger.data).find((p) => p.game_id === gameId) ?? null
  const r = bet ? resultOf(bet) : null
  return (
    <div className="game-header">
      <ScoreBar gameId={gameId} sport={sport} away={away} home={home} startsAt={startsAt} />
      <div className="game-header__row">
        {bet ? (
          <span className={`game-header__bet game-header__bet--${(r ?? 'open').toLowerCase()}`}>
            <small>Our paper bet</small>
            <strong>{bet.selection} {american(bet.entry.american)}</strong>
            <span>{usd(bet.stake)} at {bet.entry.sportsbook ?? 'book'}</span>
            <b>{r === 'OPEN' ? 'Pending' : r === 'WIN' ? `Won ${usd(bet.profit_loss, true)}` : r === 'LOSS' ? `Lost ${usd(bet.profit_loss, true)}` : 'Push'}</b>
          </span>
        ) : (
          <span className="game-header__nobet">No paper bet on this game.</span>
        )}
        <Link className="game-header__sim" to={`/games/${gameId}/simulation?sport=${sport}`}><Dices size={15} /> Simulate this game</Link>
      </div>
    </div>
  )
}
