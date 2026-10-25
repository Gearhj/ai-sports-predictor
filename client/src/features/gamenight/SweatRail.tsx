/**
 * Act II, the Sweat rail: a broadcast score bug for every bet still in play,
 * and a strip of tonight's settled results.
 *
 * The score is the scoreboard's OBSERVED score, frozen at its last reading
 * with its age (the source publishes no score timestamp, so the clock never
 * counts down here). NHL and College Football have no live feed and never show
 * a score in play; a failed read says "score unknown", never 0–0. An observed
 * final with the position still open says "not yet settled" in its own colour.
 */
import { memo, useLayoutEffect, useRef, useState, useEffect } from 'react'
import { TeamLogo } from '../../components/TeamLogo'
import { KICK_WORD, american, showsTeamMark, usd, useTick, type MissionSport } from '../mission/missionData'
import { BUG_ORDER, bugState, observedScore, type BugState, type HeldBet } from './gameNightData'
import { basisTag, gapText } from './gap'
import { agoText, clockText, type BoardState } from './TopBar'

const pctText = (p: number) => `${(p * 100).toFixed(1)}%`

function sinceStart(iso: string, now: number): string {
  const ms = now - Date.parse(iso)
  if (!Number.isFinite(ms) || ms < 0) return ''
  const m = Math.floor(ms / 60_000)
  return m < 60 ? `${m} m` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} m`
}

const Countdown = memo(function Countdown({ at, word }: { at: string; word: string }) {
  const now = useTick(1000)
  const ms = Date.parse(at) - now
  return <span className="gn-bugcard__line">{word} {ms > 0 ? <>in <b>{clockText(ms)}</b></> : 'now'}</span>
})

const Since = memo(function Since({ at }: { at: string }) {
  const now = useTick(30_000)
  const t = sinceStart(at, now)
  return t ? <span> · started {t} ago</span> : null
})

const Checked = memo(function Checked({ at }: { at: string | null | undefined }) {
  const now = useTick(1000)
  const t = at ? Date.parse(at) : NaN
  return Number.isFinite(t) ? <span> · checked {agoText(now - t)} ago</span> : null
})

function Diamond({ bases, outs }: { bases: string[]; outs: number | null }) {
  const on = (b: string) => bases.some((x) => x.toLowerCase().startsWith(b))
  return (
    <span className="gn-diamond" title={`Runners: ${bases.join(', ') || 'none'}${outs != null ? ` · ${outs} out` : ''}`}>
      <svg viewBox="0 0 40 28" width="30" height="21" aria-hidden="true">
        {([['second', 20, 6], ['third', 9, 16], ['first', 31, 16]] as const).map(([b, x, y]) => (
          <rect key={b} x={x - 5} y={y - 5} width="10" height="10" transform={`rotate(45 ${x} ${y})`} fill={on(b) ? '#ffc94d' : 'transparent'} stroke="#9fb3c8" strokeWidth="1.5" />
        ))}
      </svg>
      {outs != null && <small>{outs} out</small>}
      <span className="gn-sr">Runners: {bases.join(', ') || 'none'}{outs != null ? `, ${outs} out` : ''}</span>
    </span>
  )
}

export const STATE_LABEL: Record<BugState, string> = {
  LIVE: 'LIVE', FINAL_AWAITING_SETTLEMENT: 'FINAL · NOT YET SETTLED', STARTED_NO_LIVE_FEED: 'IN PLAY · NO LIVE FEED',
  VOID_AWAITING_SETTLEMENT: 'NOT PLAYED · VOIDING', UPCOMING: 'UPCOMING', SETTLED: 'SETTLED',
}

interface CardProps {
  bet: HeldBet
  state: BugState
  focused: boolean
  settlementAt: string | null
  scoresFailed: boolean
  reducedMotion: boolean
  onFocus: (gameId: string) => void
  register: (id: string, el: HTMLElement | null) => void
}

const BugCard = memo(function BugCard({ bet, state, focused, settlementAt, scoresFailed, reducedMotion, onFocus, register }: CardProps) {
  const sport = bet.sport as MissionSport
  const score = observedScore(bet)
  const showScore = score != null && (state === 'LIVE' || state === 'FINAL_AWAITING_SETTLEMENT')
  const scoreText = showScore ? `${score!.away}-${score!.home}` : ''
  const prev = useRef(scoreText)
  const [flash, setFlash] = useState(false)
  useEffect(() => {
    if (prev.current && scoreText && prev.current !== scoreText) {
      setFlash(true)
      const timer = window.setTimeout(() => setFlash(false), reducedMotion ? 10_000 : 1_200)
      prev.current = scoreText
      return () => window.clearTimeout(timer)
    }
    prev.current = scoreText
    return undefined
  }, [scoreText, reducedMotion])

  const sb = bet.score
  const liveStage = bet.live
  const gap = bet.gap
  const sourceName = sb?.source?.name ?? null
  const checkedAt = sb?.source?.last_checked_at ?? liveStage?.last_checked_at ?? null
  return (
    <li ref={(el) => register(bet.positionId, el)} className="gn-bugitem">
      <button type="button" onClick={() => onFocus(bet.gameId)} aria-pressed={focused}
        className={`gn-bugcard gn-bugcard--${state.toLowerCase()} gn-sportline--${sport.toLowerCase()} ${focused ? 'is-focused' : ''} ${flash ? (reducedMotion ? 'is-accent' : 'is-flash') : ''}`}
        title={sourceName ? `Score source: ${sourceName}. ESPN gives no score timestamp; source latency unknown.` : undefined}>
        <span className="gn-bugcard__top">
          <span className={`gn-tag gn-tag--${sport.toLowerCase()}`}>{sport}</span>
          <span className={`gn-bugcard__state gn-bugcard__state--${state.toLowerCase()}`}>{state === 'LIVE' && <i className="gn-live-dot" aria-hidden="true" />}{STATE_LABEL[state]}</span>
        </span>
        <span className="gn-bugcard__score">
          <span className="gn-bugcard__team" title={bet.awayName ?? bet.away}>
            {showsTeamMark(bet.away) ? <TeamLogo league={sport} abbreviation={bet.away} size={36} markOnly /> : <b>{bet.away}</b>}
            {showsTeamMark(bet.away) && <span className="gn-sr">{bet.away}</span>}
          </span>
          {showScore
            ? <span className="gn-bugcard__digits" aria-label={`${bet.away} ${score!.away}, ${bet.home} ${score!.home}`}>
                <span key={`a${score!.away}`} className={flash && !reducedMotion ? 'gn-flip' : ''}>{score!.away}</span>
                <i>–</i>
                <span key={`h${score!.home}`} className={flash && !reducedMotion ? 'gn-flip' : ''}>{score!.home}</span>
              </span>
            : <span className="gn-bugcard__digits gn-bugcard__digits--none">{state === 'LIVE' && scoresFailed ? 'score unknown' : '@'}</span>}
          <span className="gn-bugcard__team gn-bugcard__team--home" title={bet.homeName ?? bet.home}>
            {showsTeamMark(bet.home) ? <TeamLogo league={sport} abbreviation={bet.home} size={36} markOnly /> : <b>{bet.home}</b>}
            {showsTeamMark(bet.home) && <span className="gn-sr">{bet.home}</span>}
          </span>
        </span>
        {state === 'LIVE' && (
          <span className="gn-bugcard__line">
            {sb?.status_detail || sb?.period?.label || liveStage?.period_label || 'Live'}<Checked at={checkedAt} />
            {sport === 'MLB' && sb?.situation && <Diamond bases={sb.situation.bases_occupied ?? []} outs={sb.situation.outs ?? null} />}
          </span>
        )}
        {state === 'UPCOMING' && bet.startsAt && <Countdown at={bet.startsAt} word={KICK_WORD[sport] ?? 'Start'} />}
        {state === 'STARTED_NO_LIVE_FEED' && (
          <span className="gn-bugcard__line gn-bugcard__line--muted">
            <b>{liveStage?.label || 'No live feed'}</b>{liveStage?.detail ? ` · ${liveStage.detail}` : ''}{bet.startsAt && <Since at={bet.startsAt} />}
          </span>
        )}
        {state === 'VOID_AWAITING_SETTLEMENT' && (
          <span className="gn-bugcard__line gn-bugcard__line--muted">{liveStage?.detail || 'Not played; the settlement owner voids it.'}</span>
        )}
        {state === 'FINAL_AWAITING_SETTLEMENT' && (
          <span className="gn-bugcard__line gn-bugcard__line--awaiting">
            {score ? `Observed final ${score.away}–${score.home}` : 'Observed final'} · <b>not yet settled</b>
            {settlementAt && <SettleIn at={settlementAt} />}
          </span>
        )}
        <span className="gn-bugcard__pick">
          <b>{bet.selection} {american(bet.american)}</b>
          {bet.sportsbook ? ` · ${bet.sportsbook}` : ''} · {usd(bet.stake).replace('.00', '')}{bet.toWin != null ? ` to win ${usd(bet.toWin)}` : ''}
        </span>
        {gap ? (
          <span className={`gn-bugcard__gap ${gap.warning ? 'is-warning' : ''}`} title="At decision time, same-book price pair, vig removed.">
            <span className="gn-nowrap">model {pctText(gap.model)}{basisTag(gap)}</span> <span className="gn-nowrap">vs market {pctText(gap.noVig)}</span> <span className="gn-nowrap">· {gapText(gap.gap)}</span>
            {gap.warning && <em className="gn-warnbadge">MODEL WARNING</em>}
          </span>
        ) : (
          <span className="gn-bugcard__gap gn-bugcard__gap--none">No decision-time price pair on this slate&apos;s board</span>
        )}
      </button>
    </li>
  )
})

const SettleIn = memo(function SettleIn({ at }: { at: string }) {
  const now = useTick(1000)
  const ms = Date.parse(at) - now
  return <span title="Scheduler next run, not a promise."> · next settlement check {ms > 0 ? `in ${clockText(ms)}` : 'due now'}</span>
})

export function SweatRail({ bets, results, focusId, settlementAt, scoresFailed, reducedMotion, onFocus, board, now }: {
  bets: HeldBet[]; results: HeldBet[]; focusId: string | null; settlementAt: string | null; scoresFailed: boolean
  reducedMotion: boolean; onFocus: (gameId: string) => void; board: BoardState; now: number
}) {
  const lastChange = useRef(new Map<string, number>())
  const seen = useRef(new Map<string, string>())
  for (const b of bets) {
    const s = observedScore(b)
    const text = s ? `${s.away}-${s.home}` : ''
    const prev = seen.current.get(b.positionId)
    if (prev != null && prev !== text && text) lastChange.current.set(b.positionId, Date.now())
    seen.current.set(b.positionId, text)
  }
  const states = new Map(bets.map((b) => [b.positionId, bugState(b, now)]))
  const sorted = [...bets].sort((a, b) =>
    BUG_ORDER[states.get(a.positionId)!] - BUG_ORDER[states.get(b.positionId)!]
    || (lastChange.current.get(b.positionId) ?? 0) - (lastChange.current.get(a.positionId) ?? 0)
    || Date.parse(a.startsAt) - Date.parse(b.startsAt))

  // FLIP: a card that moves (a score changed and it rose to the top) glides there.
  const nodes = useRef(new Map<string, HTMLElement>())
  const tops = useRef(new Map<string, number>())
  const register = useRef((id: string, el: HTMLElement | null) => { if (el) nodes.current.set(id, el); else nodes.current.delete(id) }).current
  const order = sorted.map((b) => b.positionId).join('|')
  useLayoutEffect(() => {
    const next = new Map<string, number>()
    nodes.current.forEach((el, id) => {
      const top = el.getBoundingClientRect().top
      next.set(id, top)
      const before = tops.current.get(id)
      if (!reducedMotion && before != null && Math.abs(before - top) > 2 && typeof el.animate === 'function') {
        el.animate([{ transform: `translateY(${before - top}px)` }, { transform: 'translateY(0)' }], { duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)' })
      }
    })
    tops.current = next
  }, [order, reducedMotion])

  const live = sorted.filter((b) => states.get(b.positionId) === 'LIVE').length
  const atRisk = bets.reduce((s, b) => s + (b.stake ?? 0), 0)
  return (
    <div className="gn-sweat">
      <header className="gn-sweat__head">
        <h2 id="gn-sweat-title">Sweating now</h2>
        {bets.length > 0 && <span>{live > 0 && <><i className="gn-live-dot" aria-hidden="true" />{live} live · </>}{usd(atRisk)} at risk</span>}
      </header>
      {board === 'loading' && !bets.length && <p className="gn-sweat__empty" role="status" aria-busy="true">Reading open bets…</p>}
      {board === 'failed' && !bets.length && <p className="gn-sweat__empty gn-sweat__empty--failed" role="status">Open bets could not be read. Any open bet is still riding; this list is unknown, not empty.</p>}
      {board === 'ready' && !bets.length && <p className="gn-sweat__empty">No bet in play right now. When a decision window places one, its score bug appears here.</p>}
      <ul className="gn-sweat__list">
        {sorted.map((b) => (
          <BugCard key={b.positionId} bet={b} state={states.get(b.positionId)!} focused={b.gameId === focusId} settlementAt={settlementAt}
            scoresFailed={scoresFailed} reducedMotion={reducedMotion} onFocus={onFocus} register={register} />
        ))}
      </ul>
      {results.length > 0 && (
        <section className="gn-results" aria-label="Tonight's results">
          <h3>Tonight&apos;s results</h3>
          <ul>
            {results.map((r) => (
              <li key={r.positionId}>
                <button type="button" className={`gn-result gn-result--${r.result.toLowerCase()}`} onClick={() => onFocus(r.gameId)}
                  aria-label={`${r.selection} ${american(r.american)}: ${r.result.toLowerCase()} ${usd(r.profit, true)}`}>
                  <b>{r.selection.replace(/\s+ML$/i, '')} {american(r.american)}</b>
                  <span>{r.result === 'WIN' ? 'W' : r.result === 'LOSS' ? 'L' : r.result === 'PUSH' ? 'P' : r.result} {usd(r.profit, true)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
