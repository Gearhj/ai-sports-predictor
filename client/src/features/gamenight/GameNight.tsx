/**
 * AISP Game Night: a full-screen broadcast wall built on AISP's stored data.
 *
 *   Act I    "The 10,000": the focused game's stored draws rain onto a score grid
 *   Act II   score bugs for every bet in play, and the bankroll heartbeat
 *   Act III  Gap Watch: the model-warning rule as a scoreboard
 *
 * TV mode (?tv=1) runs it by itself: a director cuts between held games every
 * 45 s, cuts at once when a held game's score changes, and a settlement the
 * ledger records while the page is open plays as a broadcast lower-third with
 * the stage cut to that game behind it.
 *
 * "Tonight" runs from 6 AM ET to 6 AM ET (nightStartOf), so a Monday-night game
 * that settles at 12:30 AM still belongs to Monday night everywhere on the wall.
 *
 * Research and paper only. Every read is a GET of stored AISP data; nothing
 * here can place a wager, trigger a job or change data. Every number is
 * computed from live payloads with its as-of time; every animation says where
 * its data comes from.
 */
import { GamePlanBar } from '../flow/GamePlanBar'
import { AispSide } from '../flow/AispSide'
import '../slate/slate.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RegionBoundary } from '../../components/command/RegionBoundary'
import { useReducedMotion } from '../../components/command/chartKit'
import { useNow } from '../../components/activeWagers/useActiveWagers'
import { shiftDate } from '../../services/operationalDate'
import { usd } from '../mission/missionData'
import {
  decisionsByGame, decisionsByPosition, directorQueue, etToday, heldFromLedger, ledgerPositions, mergeBoards, observedScore,
  targetFromHeld, useGameNight, type FocusTarget, type HeldBet,
} from './gameNightData'
import { decisionGap, type GapReading } from './gap'
import { allTimeHigh, momentsFor, nightStartOf, pointKey, settledSince, settlementRuns, splitLiveLate, watchLedger, type Moment } from './events'
import { TopBar, etShort, nextJobRun, type BoardState, type ScoreFreshness } from './TopBar'
import { GameReel } from './GameReel'
import { StormStage, type StormInfo } from './ScoreStorm'
import { SweatRail } from './SweatRail'
import { Heartbeat, mmdd } from './Heartbeat'
import { MomentOverlay } from './MomentOverlay'
import { GapMini, GapWatch } from './GapWatch'
import { PHONE_QUERY, WIDE_QUERY, useAutoScroll, useMediaQuery, usePageVisible } from './gnHooks'
import './gamenight.css'

export const CUT_MS = 45_000
export const CUT_MS_REDUCED = 90_000
const BAND_MS = 30_000
const IDLE_MS = 4_000
const TV_REPLAY_MS = 10 * 60_000

type WakeLockSentinelLike = { release: () => Promise<void>; addEventListener?: (t: string, l: () => void) => void }

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) || el.isContentEditable
}

const SETTLED_RESULTS = ['WIN', 'LOSS', 'PUSH']

export function GameNight() {
  const [params, setParams] = useSearchParams()
  const tv = params.get('tv') === '1'
  const focusParam = params.get('focus')
  const reducedMotion = useReducedMotion()
  const visible = usePageVisible()
  const wide = useMediaQuery(WIDE_QUERY)
  const phone = useMediaQuery(PHONE_QUERY)
  const tvLayout = tv && wide
  const now = useNow(30_000)
  const day = etToday(now)
  const nightStart = nightStartOf(now)

  const [focusId, setFocusId] = useState<string | null>(focusParam)
  const [external, setExternal] = useState<FocusTarget | null>(null)
  const gn = useGameNight(day, now, focusId)
  const board: BoardState = gn.mission.data != null ? 'ready' : gn.mission.error ? 'failed' : 'loading'

  // ------------------------------------------------------------- focus --
  const queue = useMemo(() => directorQueue(gn.targets, { now, lastScoreChange: gn.scoreChanges.map }), [gn.targets, now, gn.scoreChanges.map])
  const known = (id: string | null) => Boolean(id) && (gn.targets.some((t) => t.gameId === id) || external?.gameId === id)
  const loaded = gn.mission.data != null
  const baseFocus = known(focusId) ? focusId : (queue[0] ?? gn.targets[0]?.gameId ?? null)
  const baseTarget: FocusTarget | null = external?.gameId === baseFocus ? external : gn.targets.find((t) => t.gameId === baseFocus) ?? null
  const unknownLink = loaded && focusParam && !known(focusParam)

  const chooseFocus = useCallback((id: string, opts: { manual?: boolean } = {}) => {
    setFocusId(id)
    if (external && external.gameId !== id) setExternal(null)
    if (opts.manual) {
      setParams((prev) => { const out = new URLSearchParams(prev); out.set('focus', id); return out }, { replace: true })
    }
  }, [external, setParams])
  const onFocus = useCallback((id: string) => chooseFocus(id, { manual: true }), [chooseFocus])
  const onFocusExternal = useCallback((held: HeldBet) => {
    const inBoard = gn.targets.find((x) => x.gameId === held.gameId)
    if (inBoard) { chooseFocus(held.gameId, { manual: true }); return }
    setExternal(targetFromHeld(held))
    setFocusId(held.gameId)
  }, [gn.targets, chooseFocus])

  // -------------------------------------------------------- settlements --
  const boards = gn.boards
  const byPosition = useMemo(() => decisionsByPosition(boards), [boards])
  const gaps = useMemo(() => {
    const out = new Map<string, GapReading>()
    byPosition.forEach((item, pid) => { const g = decisionGap(item); if (g) out.set(pid, g) })
    return out
  }, [byPosition])
  const ledgerData = gn.ledger.data
  const positions = useMemo(() => ledgerPositions(ledgerData), [ledgerData])
  const series = useMemo(() => ledgerData?.combined_series ?? [], [ledgerData])
  const baseline = useRef<ReadonlySet<string> | null>(null)
  const [freshKeys, setFreshKeys] = useState<ReadonlySet<string>>(new Set())
  const [moments, setMoments] = useState<Moment[]>([])
  useEffect(() => {
    if (!ledgerData) return
    const { baseline: next, fresh } = watchLedger(baseline.current, ledgerData.combined_series, gn.ledger.restored)
    baseline.current = next
    if (!fresh.length) return
    setFreshKeys((prev) => new Set([...prev, ...fresh.map(pointKey)]))
    const { live } = splitLiveLate(fresh, Date.now())
    if (!live.length) return
    const arriving = momentsFor(live, { series: ledgerData.combined_series, positions, gaps }, false)
    // A real settlement never waits behind a replay: queued replays are dropped (one on screen gives way at once),
    // and a live card already on screen finishes first.
    setMoments((q) => {
      const head = q[0]
      const keep = head && !head.replay ? [head] : []
      return [...keep, ...q.slice(keep.length).filter((m) => !m.replay), ...arriving]
    })
  }, [ledgerData, gn.ledger.restored, positions, gaps])
  // A NEW tag fades after 30 s.
  useEffect(() => {
    if (!freshKeys.size) return
    const t = window.setTimeout(() => setFreshKeys(new Set()), 30_000)
    return () => window.clearTimeout(t)
  }, [freshKeys])

  const [notice, setNotice] = useState<string | null>(null)
  /** Tonight's settlements in real order (since 6 AM ET), or every settlement at one clock (`only`). */
  const replayTonight = useCallback((only?: string | null) => {
    const points = only
      ? series.filter((p) => p.kind === 'SETTLEMENT' && p.at === only)
      : settledSince(series, nightStart)
    if (!points.length) { setNotice(`No settlement recorded since ${etShort(nightStart)} ET to replay.`); return }
    setNotice(null)
    // One header per settlement run of three or more, as it happened.
    const reel = settlementRuns(points).flatMap((run) => momentsFor(run, { series, positions, gaps }, true))
    setMoments((q) => {
      // A live card on screen keeps its place; the reel replaces any reel already queued.
      const head = q[0]
      const keep = head && !head.replay ? q.filter((m) => !m.replay) : []
      return [...keep, ...reel]
    })
  }, [series, nightStart, positions, gaps])
  const ath = useMemo(() => allTimeHigh(series), [series])
  const athTonight = ath?.at != null && Date.parse(ath.at) >= nightStart
  const onMomentDone = useCallback((id: string) => setMoments((q) => q.filter((m) => m.id !== id)), [])

  // While a moment is on screen the stage cuts to its game, and back to the previous focus when the queue empties.
  const head = moments[0] ?? null
  const momentTarget: FocusTarget | null = useMemo(() => {
    if (!head) return null
    const positionId = head.positionId ?? moments.find((m) => m.positionId)?.positionId ?? null
    const gameId = head.gameId ?? moments.find((m) => m.gameId)?.gameId ?? null
    if (!gameId) return null
    const onBoard = gn.targets.find((t) => t.gameId === gameId)
    if (onBoard) return onBoard
    const pos = positionId ? positions.get(positionId) : undefined
    if (!pos || pos.game_id !== gameId || pos.sport === 'NHL') return null // an NHL game off the board has no source id to read
    return targetFromHeld(heldFromLedger(pos, byPosition.get(pos.position_id) ?? null))
  }, [head, moments, gn.targets, positions, byPosition])
  const target = momentTarget ?? baseTarget
  const effectiveFocus = target?.gameId ?? baseFocus

  // ----------------------------------------------------------- director --
  const [directorPaused, setDirectorPaused] = useState(false)
  const momentsActive = moments.length > 0
  const cutMs = reducedMotion ? CUT_MS_REDUCED : CUT_MS
  const [nextCutAt, setNextCutAt] = useState<number | null>(null)
  // Bumped on every timer fire, so the director reschedules even when a cut lands on the game already in focus.
  const [cutTick, setCutTick] = useState(0)
  const queueRef = useRef(queue)
  queueRef.current = queue
  useEffect(() => {
    if (!tv || directorPaused || momentsActive || !visible) { setNextCutAt(null); return }
    setNextCutAt(Date.now() + cutMs)
    const timer = window.setTimeout(() => {
      const q = queueRef.current
      if (q.length) {
        const i = baseFocus ? q.indexOf(baseFocus) : -1
        chooseFocus(q[(i + 1) % q.length])
      }
      setCutTick((n) => n + 1)
    }, cutMs)
    return () => window.clearTimeout(timer)
  }, [tv, directorPaused, momentsActive, visible, cutMs, baseFocus, chooseFocus, cutTick, queue.length])
  // A held game's score changed: cut to it now.
  const lastSeq = useRef(0)
  useEffect(() => {
    const latest = gn.scoreChanges.latest
    if (!latest || latest.seq === lastSeq.current) return
    lastSeq.current = latest.seq
    if (tv && !directorPaused && gn.held.some((h) => h.gameId === latest.gameId)) chooseFocus(latest.gameId)
  }, [gn.scoreChanges.latest, tv, directorPaused, gn.held, chooseFocus])

  // TV, nothing live, nothing queued: replay tonight every ten minutes (always watermarked).
  useEffect(() => {
    if (!tv || gn.onAir || !visible) return
    const timer = window.setInterval(() => { if (!momentsActive) replayTonight() }, TV_REPLAY_MS)
    return () => window.clearInterval(timer)
  }, [tv, gn.onAir, visible, momentsActive, replayTonight])

  // The TV lower band alternates Heartbeat and Gap Watch every 30 s.
  const [band, setBand] = useState<'heart' | 'gap'>('heart')
  useEffect(() => {
    if (!tvLayout || !visible) return
    const timer = window.setInterval(() => setBand((b) => (b === 'heart' ? 'gap' : 'heart')), BAND_MS)
    return () => window.clearInterval(timer)
  }, [tvLayout, visible])

  // On the TV wall the rail has no scrollbar: it pages through the cards by itself.
  const railRef = useRef<HTMLDivElement | null>(null)
  useAutoScroll(railRef, tvLayout && visible, { reduced: reducedMotion, everyMs: 8_000, itemSelector: '.gn-sweat__head, .gn-bugitem, .gn-results, .gn-mini' })

  // ------------------------------------------------- fullscreen, wake, idle --
  // The whole document goes fullscreen (never one element): the page still scrolls, and the settlement
  // lower-thirds, which are portalled to <body>, stay visible.
  const canFullscreen = typeof document !== 'undefined' && Boolean(document.fullscreenEnabled)
    && typeof document.documentElement?.requestFullscreen === 'function'
  const [isFullscreen, setIsFullscreen] = useState(false)
  useEffect(() => {
    const update = () => setIsFullscreen(document.fullscreenElement === document.documentElement)
    document.addEventListener('fullscreenchange', update)
    return () => document.removeEventListener('fullscreenchange', update)
  }, [])
  const toggleFullscreen = useCallback(() => {
    if (!canFullscreen) return
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {})
    else void document.documentElement.requestFullscreen?.().catch(() => {})
  }, [canFullscreen])

  const canWake = typeof navigator !== 'undefined' && 'wakeLock' in navigator
  const [wakeOn, setWakeOn] = useState(false)
  const wakeLock = useRef<WakeLockSentinelLike | null>(null)
  const acquire = useCallback(async () => {
    try {
      const nav = navigator as unknown as { wakeLock?: { request: (t: 'screen') => Promise<WakeLockSentinelLike> } }
      wakeLock.current = (await nav.wakeLock?.request('screen')) ?? null
    } catch { wakeLock.current = null; setWakeOn(false) }
  }, [])
  const toggleWake = useCallback(() => {
    if (wakeOn) { void wakeLock.current?.release().catch(() => {}); wakeLock.current = null; setWakeOn(false) }
    else { setWakeOn(true); void acquire() }
  }, [wakeOn, acquire])
  useEffect(() => {
    if (!wakeOn) return
    // The browser drops the lock when the tab is hidden; take it again on return.
    const again = () => { if (document.visibilityState === 'visible') void acquire() }
    document.addEventListener('visibilitychange', again)
    return () => document.removeEventListener('visibilitychange', again)
  }, [wakeOn, acquire])
  useEffect(() => () => { void wakeLock.current?.release().catch(() => {}) }, [])
  useEffect(() => { if (!tv && wakeOn) toggleWake() }, [tv, wakeOn, toggleWake])

  const [idle, setIdle] = useState(false)
  useEffect(() => {
    if (!tv) { setIdle(false); return }
    let timer = window.setTimeout(() => setIdle(true), IDLE_MS)
    const wake = () => { setIdle(false); window.clearTimeout(timer); timer = window.setTimeout(() => setIdle(true), IDLE_MS) }
    window.addEventListener('mousemove', wake)
    window.addEventListener('keydown', wake)
    window.addEventListener('pointerdown', wake)
    return () => { window.clearTimeout(timer); window.removeEventListener('mousemove', wake); window.removeEventListener('keydown', wake); window.removeEventListener('pointerdown', wake) }
  }, [tv])

  const setTv = useCallback((on: boolean) => {
    setParams((prev) => { const out = new URLSearchParams(prev); if (on) out.set('tv', '1'); else out.delete('tv'); return out }, { replace: true })
  }, [setParams])

  // ------------------------------------------------------------ keyboard --
  const order = gn.targets.map((t) => t.gameId)
  const orderRef = useRef(order)
  orderRef.current = order
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return
      const onControl = (e.target as HTMLElement | null)?.closest?.('button, a, summary, [role="group"][tabindex]')
      switch (e.key) {
        case 'f': case 'F': toggleFullscreen(); break
        case 't': case 'T': setTv(!tv); break
        case 'r': case 'R': replayTonight(); break
        case ' ':
          if (!tv || onControl) return
          e.preventDefault(); setDirectorPaused((p) => !p); break
        case 'ArrowLeft': case 'ArrowRight': {
          if (onControl && (e.target as HTMLElement).closest('[role="group"][tabindex]')) return
          const list = orderRef.current
          if (!list.length) return
          const i = baseFocus ? list.indexOf(baseFocus) : -1
          const next = list[(i + (e.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length]
          e.preventDefault()
          onFocus(next)
          break
        }
        default:
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleFullscreen, setTv, tv, replayTonight, baseFocus, onFocus])

  // ------------------------------------------------------------ derived --
  const mission = gn.mission.data
  const jobs = mission?.health?.jobs ?? null
  const settlementAt = nextJobRun(jobs, (j) => j.key === 'settlement', now)
  const pricesAt = nextJobRun(jobs, (j) => j.role === 'PRICES', now)
  const openBets = gn.held.filter((h) => h.open)
  const settledTonight = useMemo(() => gn.held
    .filter((h) => !h.open && h.result !== 'OPEN' && h.settledAt != null && Date.parse(h.settledAt) >= nightStart)
    .sort((a, b) => Date.parse(b.settledAt ?? '') - Date.parse(a.settledAt ?? '')), [gn.held, nightStart])
  const upcoming = openBets
    .filter((h) => Date.parse(h.startsAt) > Date.now())
    .sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt))[0]
  const standby = upcoming ? { label: `${upcoming.selection} ${upcoming.sport === 'NHL' ? 'puck drop' : upcoming.sport === 'MLB' ? 'first pitch' : 'kickoff'}`, at: upcoming.startsAt } : null

  const scores: ScoreFreshness = useMemo(() => {
    const games = Object.values(gn.scoreboard.games ?? {})
    const checks = games.map((g) => Date.parse(g.source?.last_checked_at ?? '')).filter((t) => Number.isFinite(t))
    return {
      reading: gn.scoreIds.length > 0,
      checkedAt: checks.length ? Math.max(...checks) : null,
      stale: games.some((g) => g.is_live && g.freshness && g.freshness.state !== 'CURRENT'),
      failed: Boolean(gn.scoreboard.error),
    }
  }, [gn.scoreboard.games, gn.scoreboard.error, gn.scoreIds.length])

  const focusHeld = target?.held ?? null
  const byGame = useMemo(() => decisionsByGame(boards), [boards])
  const focusDecision = focusHeld?.decision ?? (target ? byGame.get(target.gameId) ?? null : null)
  const focusGap = focusHeld?.gap ?? (focusDecision ? decisionGap(focusDecision) : null)
  const focusScore = target ? gn.scoreboard.games[target.gameId] : undefined
  const focusLive = focusScore?.available && focusScore.is_live && focusScore.away_score != null && focusScore.home_score != null
    ? { away: focusScore.away_score, home: focusScore.home_score, detail: focusScore.status_detail ?? '' } : null
  const focusObserved = focusHeld ? observedScore(focusHeld) : null
  const observedFinal = focusObserved?.final ? { away: focusObserved.away, home: focusObserved.home, source: focusObserved.source }
    : (focusScore?.available && focusScore.is_final && focusScore.away_score != null && focusScore.home_score != null
      ? { away: focusScore.away_score, home: focusScore.home_score, source: focusScore.source?.name ?? null }
      : target?.game?.stages?.result?.final ? { ...target.game.stages.result.final, source: target.game.stages.result.final.source ?? null } : null)

  const [info, setInfo] = useState<StormInfo | null>(null)

  // Headline: tonight's settlements from the ledger (since 6 AM ET, so an after-midnight final still counts),
  // the open bets from the board. Built only from live payloads.
  const tonight = useMemo(() => {
    if (!ledgerData) return null
    let n = 0, profit = 0, stake = 0
    positions.forEach((p) => {
      if (String(p.status).toUpperCase() !== 'SETTLED' || !p.settled_at || Date.parse(p.settled_at) < nightStart) return
      if (!SETTLED_RESULTS.includes(String(p.result ?? '').toUpperCase())) return
      n += 1; profit += p.profit_loss ?? 0; stake += p.stake ?? 0
    })
    return { n, profit: Math.round(profit * 100) / 100, stake: Math.round(stake * 100) / 100 }
  }, [ledgerData, positions, nightStart])
  const bankroll = ledgerData?.totals.realized_bankroll ?? null

  // Gap Watch "tonight": after midnight, last night's slate is still tonight.
  const yesterdayBoard = useMemo(() => gn.extra.data?.boards.find((b) => b.date === gn.yesterday) ?? null, [gn.extra.data, gn.yesterday])
  const tonightBoard = useMemo(
    () => (gn.early ? mergeBoards([yesterdayBoard, gn.decisions.data], day) : gn.decisions.data),
    [gn.early, yesterdayBoard, gn.decisions.data, day])
  const tonightLabel = gn.early ? `tonight · ${gn.yesterday.slice(5)} and ${day.slice(5)} slates` : 'tonight'
  const prevDay = gn.early ? shiftDate(gn.yesterday, -1) : gn.yesterday

  const heartHeight = tvLayout ? 108 : phone ? 120 : 150
  const sport = target?.sport.toLowerCase() ?? 'all'
  const nightTitle = `Settled since ${etShort(nightStart)} ET ${mmdd(new Date(nightStart).toISOString())}`

  const page = (
    <div className={`gn ${tv ? 'gn--tv' : ''} ${tvLayout ? 'gn--tvlayout' : ''} ${idle ? 'gn--idle' : ''} ${reducedMotion ? 'gn--still' : ''}`}>
      {!tv ? <GamePlanBar current="outcomes" /> : null}
      <TopBar
        onAir={gn.onAir}
        board={board}
        standby={standby}
        mission={gn.mission}
        ledger={gn.ledger}
        scores={scores}
        settlementAt={settlementAt}
        pricesAt={pricesAt}
        tv={tv}
        directorPaused={directorPaused || momentsActive}
        nextCutAt={nextCutAt}
        canFullscreen={canFullscreen}
        isFullscreen={isFullscreen}
        canWake={canWake}
        wakeOn={wakeOn}
        onTv={() => setTv(!tv)}
        onFullscreen={toggleFullscreen}
        onPause={() => setDirectorPaused((p) => !p)}
        onReplay={() => replayTonight()}
        onWake={toggleWake}
      />

      <p className="gn-headline" aria-live="off">
        {tonight ? (
          <span title={nightTitle}><b>Tonight:</b> {tonight.n} settled · <span className={tonight.profit > 0 ? 'is-up' : tonight.profit < 0 ? 'is-down' : ''}>{usd(tonight.profit, true)}</span> on {usd(tonight.stake).replace('.00', '')}</span>
        ) : <span>{gn.ledger.error ? 'Tonight: unknown (the ledger could not be read)' : 'Tonight: loading…'}</span>}
        <span>{board === 'ready' ? `${openBets.length} open` : board === 'failed' ? 'open bets unknown' : 'open …'}</span>
        <span>Bankroll {bankroll == null ? (gn.ledger.error ? 'unknown' : '…') : usd(bankroll)}</span>
        {ath && <span>all-time high {usd(ath.bankroll)} {ath.at ? (athTonight ? `at ${etShort(Date.parse(ath.at))} ET` : `on ${mmdd(ath.at)}`) : ''}</span>}
        {athTonight && ath?.at && (
          <button type="button" className="gn-btn gn-btn--chip" onClick={() => replayTonight(ath.at)}>Watch tonight&apos;s new high</button>
        )}
        {notice && <span className="gn-headline__note" role="status">{notice}</span>}
        {unknownLink && <span className="gn-headline__note" role="status">The linked game is not on tonight&apos;s board; showing the director&apos;s pick instead.</span>}
      </p>

      <div className="gn-main">
        <div className="gn-act1">
          <RegionBoundary label="game reel">
            <GameReel targets={gn.targets} focusId={effectiveFocus} onFocus={onFocus} now={now} board={board} />
          </RegionBoundary>
          <div className={`gn-cut gn-cut--${sport}`} key={target?.gameId ?? 'none'}>
            <RegionBoundary label="score storm">
              {target ? (
                <StormStage key={target.gameId} target={target} bet={focusHeld} gap={focusGap} selection={focusDecision?.selection ?? null}
                  decisionAt={focusDecision?.recommended_at ?? null} live={focusLive} observedFinal={observedFinal}
                  reducedMotion={reducedMotion} tv={tvLayout} fast={momentTarget != null} onInfo={setInfo} />
              ) : (
                <div className="gn-storm gn-storm--idle">
                  <p className="gn-storm__empty">{gn.mission.error ? 'Tonight’s board could not be read, so no game can be focused.' : loaded ? 'No game with a stored forecast on tonight’s board.' : 'Reading tonight’s board…'}</p>
                </div>
              )}
            </RegionBoundary>
          </div>
        </div>
        <aside className="gn-rail" aria-labelledby="gn-sweat-title">
          <div className="gn-rail__inner" ref={railRef}>
          <RegionBoundary label="sweat rail">
            <SweatRail bets={openBets} results={settledTonight} focusId={effectiveFocus} settlementAt={settlementAt}
              scoresFailed={Boolean(gn.scoreboard.error)} reducedMotion={reducedMotion} onFocus={onFocus}
              board={board} now={now} />
          </RegionBoundary>
          <RegionBoundary label="gap summary">
            <GapMini board={tonightBoard} label={gn.early ? `${gn.yesterday.slice(5)} + ${day.slice(5)}` : 'tonight'} />
          </RegionBoundary>
          </div>
        </aside>
        {(!tvLayout || band === 'heart') && (
          <div className="gn-slot gn-slot--heart">
            <RegionBoundary label="bankroll heartbeat">
              <Heartbeat ledger={ledgerData} restored={gn.ledger.restored} stale={Boolean(gn.ledger.error)} failed={Boolean(gn.ledger.error)}
                asOf={gn.ledger.asOf} nightStart={nightStart} now={now} height={heartHeight} reducedMotion={reducedMotion} freshKeys={freshKeys}
                band={tvLayout} />
            </RegionBoundary>
          </div>
        )}
        {(!tvLayout || band === 'gap') && (
          <div className="gn-slot gn-slot--gap">
            <RegionBoundary label="gap watch">
              <GapWatch today={day} prevDay={prevDay} tonightLabel={tonightLabel} todayBoard={tonightBoard} boardResource={gn.decisions}
                onFocus={onFocus} onFocusExternal={onFocusExternal} reducedMotion={reducedMotion} compact={tvLayout} />
            </RegionBoundary>
          </div>
        )}
      </div>

      {!tvLayout && <Methods gn={gn} info={info} day={day} />}
      <footer className="gn-foot">
        Research and paper only. Nothing on this page can place a wager, trigger a job or change data. Settlement is recorded separately from observed scores.
      </footer>
      <MomentOverlay queue={moments} onDone={onMomentDone} reducedMotion={reducedMotion} paused={!visible} />
    </div>
  )
  // TV mode is the bare broadcast wall; otherwise the page sits in the platform shell with the one left menu.
  return tv ? page : <div className="hq gn-shell"><AispSide /><div className="hq-main">{page}</div></div>
}

function Methods({ gn, info, day }: { gn: ReturnType<typeof useGameNight>; info: StormInfo | null; day: string }) {
  const at = (ms: number | null | undefined) => (ms ? `${etShort(ms)} ET` : '—')
  const rows: [string, string, string, string][] = [
    ['Mission Control', `/mission-control?date=${day}`, at(gn.mission.asOf), gn.mission.data ? `${gn.mission.data.games.length} games · ${gn.mission.data.sweating?.length ?? 0} open bets` : '—'],
    ['Paper ledger', '/command-center/ledger', at(gn.ledger.asOf), gn.ledger.data ? `${gn.ledger.data.combined_series.length} bankroll points` : '—'],
    ['Decisions', `/command-center/decisions?date=${day}`, at(gn.decisions.asOf), gn.decisions.data ? `${gn.decisions.data.items.length} decisions` : '—'],
    ['Scoreboard', gn.scoreIds.length ? `/scoreboard?game_ids=… (${gn.scoreIds.length})` : 'not read (no held game in play)', at(gn.scoreboard.asOf), `${Object.keys(gn.scoreboard.games ?? {}).length} games`],
  ]
  if (info) rows.push(['Focused distribution', info.endpoint, at(info.asOf), info.draws != null ? `${info.cells ?? '—'} cells · ${info.draws.toLocaleString('en-US')} ${info.mode === 'sampled' ? 'sampled' : 'stored'} draws` : 'no population'])
  return (
    <details className="gn-methods">
      <summary>Methods and sources</summary>
      <div className="gn-methods__body">
        <p><b>This page only reads data. Research and paper only.</b></p>
        <ul>
          <li><b>No-vig price.</b> imp(a) = 100/(a+100) for a positive price, −a/(−a+100) for a negative one; the market&apos;s probability for our side is imp(ours) ÷ (imp(ours) + imp(theirs)), from the same book&apos;s two prices the decision priced.</li>
          <li><b>NFL two-way basis.</b> The NFL model has a tie, so its win probability is compared as pWin ÷ (1 − pTie), the basis /research/wager-results calls NON_TIE_DERIVED. A decision with no recorded tie probability is shown raw and labelled so.</li>
          <li><b>Shuffled exact tally vs sampled.</b> NFL, College Football and MLB replay every stored draw exactly once, in an order we shuffle; the finished grid is the stored tally. The NHL replay samples 10,000 draws from a derived grid, with replacement. The glow, comet trails and landing flashes are drawn on a separate layer and never change a cell&apos;s colour.</li>
          <li><b>NHL derivation.</b> Independent Poisson scoring on the stored expected regulation goals, over 0–15 goals, drawn only if it reproduces the stored regulation probabilities within 1e-4.
            {info?.nhlCheck ? <> Focused game: {info.nhlCheck.ok ? <>derived grid matches stored regulation probabilities ✓ (largest difference {info.nhlCheck.maxError != null ? info.nhlCheck.maxError.toExponential(1) : '—'})</> : 'the derived grid did not match, so it is not drawn.'}</> : null}</li>
          <li><b>Why decision-time, same book.</b> The gap is measured against the price AISP actually priced, at the moment it decided, from one book; a later consensus would mix in information the decision did not have.</li>
          <li><b>The 10-point rule.</b> A gap over 10 points is James&apos;s model-warning sign: more often model error than edge. It is never called an edge here. The tallies count gaps over +10; on a single game the stage also flags a gap of more than 10 below the market.</li>
          <li><b>Tonight.</b> Runs from 6 AM ET to 6 AM ET, so a game that settles after midnight stays with the night it was played.</li>
        </ul>
        <table className="gn-methods__table">
          <thead><tr><th scope="col">Dataset</th><th scope="col">Endpoint (GET)</th><th scope="col">As of</th><th scope="col">Size</th></tr></thead>
          <tbody>{rows.map(([a, b, c, d]) => <tr key={a}><th scope="row">{a}</th><td><code>{b}</code></td><td>{c}</td><td>{d}</td></tr>)}</tbody>
        </table>
      </div>
    </details>
  )
}

export default GameNight
