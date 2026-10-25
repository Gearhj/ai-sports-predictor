/**
 * The game reel: one chip per held game (our bets first), then the other
 * games on tonight's board that have a stored forecast. On a phone it is a
 * single <select>.
 */
import { memo } from 'react'
import { isLive } from '../mission/missionData'
import { Crest } from '../flow/Crest'
import { bugState, type FocusTarget } from './gameNightData'
import { PHONE_QUERY, useMediaQuery } from './gnHooks'
import type { BoardState } from './TopBar'

export type ReelState = 'LIVE' | 'FINAL' | 'STARTED' | 'UPCOMING'

export function reelState(t: FocusTarget, now: number): ReelState {
  if (t.held) {
    const s = bugState(t.held, now)
    if (s === 'LIVE') return 'LIVE'
    if (s === 'SETTLED' || s === 'FINAL_AWAITING_SETTLEMENT') return 'FINAL'
    if (s === 'STARTED_NO_LIVE_FEED' || s === 'VOID_AWAITING_SETTLEMENT') return 'STARTED'
    return 'UPCOMING'
  }
  const g = t.game
  if (g && isLive(g)) return 'LIVE'
  if (g && (g.phase === 'FINAL' || g.status === 'final')) return 'FINAL'
  const start = Date.parse(t.startsAt ?? '')
  return Number.isFinite(start) && start <= now ? 'STARTED' : 'UPCOMING'
}

const RECENT_FINALS = 4
const finishedAt = (t: FocusTarget) => Date.parse(t.held?.settledAt ?? '') || Date.parse(t.startsAt ?? '') || 0
const SETTLED = ['WIN', 'LOSS', 'PUSH']

const STATE_WORD: Record<ReelState, string> = { LIVE: 'LIVE', FINAL: 'FINAL', STARTED: 'STARTED', UPCOMING: 'UPCOMING' }

const Chip = memo(function Chip({ target, state, on, onFocus }: { target: FocusTarget; state: ReelState; on: boolean; onFocus: (id: string) => void }) {
  const result = target.held?.result ?? 'OPEN'
  const settled = SETTLED.includes(result)
  return (
    <button type="button" className={`gn-reel__chip gn-sportline--${target.sport.toLowerCase()} ${on ? 'is-on' : ''}`} aria-pressed={on}
      onClick={() => onFocus(target.gameId)}
      aria-label={`${target.sport} ${target.matchup}, ${STATE_WORD[state].toLowerCase()}${target.held ? (settled ? `, our bet settled ${result.toLowerCase()}` : ', we hold a bet') : ''}`}>
      <span className={`gn-tag gn-tag--${target.sport.toLowerCase()}`}>{target.sport}</span>
      <span className="gn-reel__matchup"><Crest sport={target.sport} abbr={target.away.abbr} size={18} />{target.away.abbr} @ <Crest sport={target.sport} abbr={target.home.abbr} size={18} />{target.home.abbr}</span>
      <span className={`gn-reel__state gn-reel__state--${state.toLowerCase()}`}>{state === 'LIVE' && <i className="gn-live-dot" aria-hidden="true" />}{STATE_WORD[state]}</span>
      {target.held && (settled
        ? <span className={`gn-reel__held gn-reel__held--${result.toLowerCase()}`} aria-hidden="true">{result.charAt(0)}</span>
        : <span className="gn-reel__held" aria-hidden="true">$</span>)}
    </button>
  )
})

export function GameReel({ targets, focusId, onFocus, now, board }: { targets: FocusTarget[]; focusId: string | null; onFocus: (id: string) => void; now: number; board: BoardState }) {
  const phone = useMediaQuery(PHONE_QUERY)
  const held = targets.filter((t) => t.held)
  const more = targets.filter((t) => !t.held && t.hasForecast)
  if (!targets.length) {
    // "No games" is only said once the board has been read; before that it is a reading, after a failure it is unknown.
    if (board === 'loading') return <p className="gn-reel__empty" role="status" aria-busy="true">Reading tonight&apos;s board…</p>
    if (board === 'failed') return <p className="gn-reel__empty gn-reel__empty--failed" role="status">Tonight&apos;s board could not be read; the games are unknown.</p>
    return <p className="gn-reel__empty">No games on tonight&apos;s board yet.</p>
  }
  if (phone) {
    return (
      <label className="gn-reel gn-reel--select">
        <span>Focus game</span>
        <select value={focusId ?? ''} onChange={(e) => onFocus(e.target.value)}>
          {focusId && !targets.some((t) => t.gameId === focusId) && <option value={focusId}>Linked game</option>}
          {held.length > 0 && (
            <optgroup label="Our bets">
              {held.map((t) => <option key={t.gameId} value={t.gameId}>{t.sport} · {t.away.abbr} @ {t.home.abbr} · {STATE_WORD[reelState(t, now)]} · $</option>)}
            </optgroup>
          )}
          {more.length > 0 && (
            <optgroup label="More games">
              {more.map((t) => <option key={t.gameId} value={t.gameId}>{t.sport} · {t.away.abbr} @ {t.home.abbr} · {STATE_WORD[reelState(t, now)]}</option>)}
            </optgroup>
          )}
        </select>
      </label>
    )
  }
  // Bets still in play, then the most recent finals; older finals fold into "More games".
  const inPlay = held.filter((t) => reelState(t, now) !== 'FINAL')
  const finals = held.filter((t) => reelState(t, now) === 'FINAL').sort((a, b) => finishedAt(b) - finishedAt(a))
  const shown = [...inPlay, ...finals.slice(0, RECENT_FINALS)]
  const folded = finals.slice(RECENT_FINALS)
  const extra = [...folded, ...more]
  const focusedIsMore = extra.some((t) => t.gameId === focusId)
  const moreNote = [
    folded.length ? `${folded.length} earlier ${folded.length === 1 ? 'final' : 'finals'} with our bet` : '',
    more.length ? `${more.length} more with a stored forecast` : '',
  ].filter(Boolean).join(' · ')
  return (
    <div className="gn-reel" role="group" aria-label="Choose the game in focus">
      <div className="gn-reel__row">
        {shown.map((t) => <Chip key={t.gameId} target={t} state={reelState(t, now)} on={t.gameId === focusId} onFocus={onFocus} />)}
        {!held.length && <span className="gn-reel__note">No bet on tonight&apos;s board.</span>}
      </div>
      {extra.length > 0 && (
        <details className="gn-reel__more" open={focusedIsMore || undefined}>
          <summary>More games <small>({moreNote})</small></summary>
          <div className="gn-reel__row">
            {extra.map((t) => <Chip key={t.gameId} target={t} state={reelState(t, now)} on={t.gameId === focusId} onFocus={onFocus} />)}
          </div>
        </details>
      )}
    </div>
  )
}
