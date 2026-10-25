/**
 * Act III, Gap Watch: James's model-warning rule as a scoreboard.
 *
 *   gap = model (two-way) − the same book's no-vig price at decision time
 *   gap > 10 points is a MODEL WARNING, not an edge
 *
 * Tonight's tally is computed live from the decision board. The season
 * galaxy is loaded only on request (one wager-results read plus one decision
 * board per NFL slate day, at most two at a time), because NFL no-vig is never
 * stored and must be derived from each decision's price pair.
 *
 * Exploratory throughout: small samples, no multiple-comparison adjustment,
 * and the 10-point line was drawn after most of these bets.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey, fetchQuery } from '../../services/queryCache'
import { decisionsKey, expectSchema, loadDecisions, type DecisionBoard, type DecisionItem, type SportCode } from '../../services/commandCenter'
import type { WagerRow } from '../../components/command/WagerResults'
import { useWidth } from '../../components/command/chartKit'
import { american, usd } from '../mission/missionData'
import {
  GAP_WARNING_PTS, decisionGap, gapPts, gapText, isModelWarning, normalizeResult, sameBookNoVig, seededBootstrapRoi, signedPct, zoneTally,
  type BetResult, type ComparisonBasis, type GapBet, type GapReading, type ZoneTally,
} from './gap'
import { useOptionalDecisionBoard, type HeldBet } from './gameNightData'
import { mmdd } from './Heartbeat'
import { AsOfChip, DECISIONS_STALE_AFTER_MS, etShort, type FreshResource } from './TopBar'

export type SportFilter = 'ALL' | 'NFL' | 'NHL' | 'CFB' | 'MLB'
const FILTERS: SportFilter[] = ['ALL', 'NFL', 'NHL', 'CFB', 'MLB']

export interface GapChip extends GapBet {
  label: string
  sport: string
  gameId: string
  item: DecisionItem
  reading: GapReading | null
}

const shortSelection = (s: string | null | undefined) => (s ?? '').replace(/\s+ML$/i, '').trim() || '—'

/** Funded decisions on a board as gap chips (an item without a price pair keeps gap null: "unplaced"). */
export function gapChips(items: readonly DecisionItem[]): GapChip[] {
  return items.filter((i) => i.position?.position_id).map((i) => {
    const reading = decisionGap(i)
    return {
      key: i.position!.position_id, gap: reading?.gap ?? null,
      result: normalizeResult(i.position!.status, i.position!.result),
      stake: i.position!.stake, profit: i.position!.profit_loss,
      label: shortSelection(i.selection), sport: i.sport, gameId: i.game_id, item: i, reading,
    }
  })
}

/** What the NFL model numbers on a set of chips are, chosen from each decision's own basis. */
export function nflBasisNote(readings: readonly (GapReading | null)[]): string {
  const nfl = readings.filter((r): r is GapReading => r != null && r.sport === 'NFL')
  if (!nfl.length) return ''
  const missing = nfl.filter((r) => r.basis !== 'NON_TIE_DERIVED').length
  if (!missing) return ' NFL model is tie-adjusted (pWin ÷ (1 − pTie)).'
  if (missing === nfl.length) return ' NFL tie probability was not recorded for these decisions, so the raw model is shown.'
  return ` NFL model is tie-adjusted (pWin ÷ (1 − pTie)) except ${missing} ${missing === 1 ? 'decision' : 'decisions'} with no recorded tie probability, shown raw.`
}

function recordText(t: ZoneTally): string {
  return `${t.wins}–${t.losses}${t.pushes ? `–${t.pushes}` : ''}`
}

function ZoneBoard({ title, tally, names, warning }: { title: string; tally: ZoneTally; names: string[]; warning: boolean }) {
  return (
    <div className={`gn-zone ${warning ? 'gn-zone--warning' : 'gn-zone--corridor'}`}>
      <p className="gn-zone__title">{title}</p>
      <p className="gn-zone__score"><b>{recordText(tally)}</b><span className={tally.profit >= 0 ? 'is-up' : 'is-down'}>{usd(tally.profit, true)}</span></p>
      <p className="gn-zone__meta">{tally.settled} settled{tally.open ? ` · ${tally.open} open` : ''}{names.length ? ` · ${names.join(', ')}` : ''}</p>
    </div>
  )
}

/** Intervals on the gap axis: colliding ones stack into rows, nothing scrolls sideways. */
export function stackIntervals(items: readonly { key: string; left: number; right: number }[]): Map<string, number> {
  const rows: number[] = []
  const out = new Map<string, number>()
  for (const c of [...items].sort((a, b) => a.left - b.left)) {
    let row = rows.findIndex((right) => right + 4 <= c.left)
    if (row < 0) { row = rows.length; rows.push(0) }
    rows[row] = c.right
    out.set(c.key, row)
  }
  return out
}

type LabelSide = 'left' | 'right' | 'none'
/** The dot sits this far in from the chip's outer edge (its radius, padding and border). */
const DOT_INSET = 11.5

/**
 * The gap axis. Every bet is a dot at its EXACT gap; its label sits on the far
 * side of the dot from the warning line (left for a gap of 10 or less, right
 * above it), so no label straddles the line and which side a bet is on reads at
 * a glance. `dots` (the TV band, and any axis narrower than 560 px) draws the
 * dots alone, so a cluster stays a row or two tall.
 */
const GapAxis = memo(function GapAxis({ chips, onFocus, reducedMotion, dots = false }: { chips: GapChip[]; onFocus: (gameId: string) => void; reducedMotion: boolean; dots?: boolean }) {
  const { ref, width } = useWidth<HTMLDivElement>(800)
  const placed = useMemo(() => chips.filter((c) => c.gap != null), [chips])
  const lo = Math.min(-5, ...placed.map((c) => Math.floor(c.gap!)))
  const hi = Math.max(25, ...placed.map((c) => Math.ceil(c.gap!)))
  const pad = 26
  const W = Math.max(280, width)
  const x = (g: number) => pad + ((g - lo) / (hi - lo)) * (W - pad * 2)
  const narrow = W < 560
  const asDots = dots || narrow
  const labelWidth = (c: GapChip) => 36 + c.label.length * 7.6 + gapText(c.gap).replace(' pts', '').length * 6.8 + 2 * DOT_INSET + 6
  const laid = placed.map((c) => {
    const cx = x(c.gap!)
    if (asDots) return { chip: c, cx, side: 'none' as LabelSide, left: cx - 8, right: cx + 8 }
    const w = labelWidth(c)
    let side: LabelSide = isModelWarning(c.gap) ? 'right' : 'left'
    if (side === 'left' && cx + DOT_INSET - w < 2) side = 'right'
    else if (side === 'right' && cx - DOT_INSET + w > W - 2) side = 'left'
    return side === 'left'
      ? { chip: c, cx, side, left: cx + DOT_INSET - w, right: cx + DOT_INSET }
      : { chip: c, cx, side, left: cx - DOT_INSET, right: cx - DOT_INSET + w }
  })
  const rows = stackIntervals(laid.map((l) => ({ key: l.chip.key, left: l.left, right: l.right })))
  const rowCount = Math.max(1, ...[...rows.values()].map((r) => r + 1))
  const rowH = asDots ? 18 : 30
  const axisY = 18 + rowCount * rowH
  const ticks: number[] = []
  for (let t = Math.ceil(lo / 5) * 5; t <= hi; t += 5) ticks.push(t)
  return (
    <div className={`gn-axis ${asDots ? 'gn-axis--dots' : ''} ${narrow ? 'gn-axis--narrow' : ''}`} ref={ref} style={{ height: axisY + 30 }}>
      <div className="gn-axis__line" style={{ top: axisY, left: pad, right: pad }} />
      {ticks.map((t) => (
        <span key={t} className="gn-axis__tick" style={{ left: x(t), top: axisY + 4 }}>{t > 0 ? `+${t}` : t}</span>
      ))}
      <div className="gn-axis__warn" style={{ left: x(GAP_WARNING_PTS), top: 4, height: axisY - 4 }}><span>model warning</span></div>
      {laid.map(({ chip: c, cx, side }, i) => {
        const row = rows.get(c.key) ?? 0
        const top = axisY - (row + 1) * rowH + 2
        const zone = isModelWarning(c.gap) ? 'warning' : 'corridor'
        const anchor = side === 'left' ? cx + DOT_INSET : side === 'right' ? cx - DOT_INSET : cx
        const tx = side === 'left' ? '-100%' : side === 'right' ? '0%' : '-50%'
        const dot = <i className="gn-gapchip__dot" aria-hidden="true" />
        const name = `${c.sport} ${c.label}: gap ${gapText(c.gap)}, ${c.result === 'OPEN' ? 'open' : c.result.toLowerCase()}${zone === 'warning' ? ', model warning' : ''}`
        return (
          <span key={c.key}>
            <span className="gn-axis__stem" style={{ left: cx, top: top + (asDots ? 7 : 13), height: Math.max(0, axisY - top - (asDots ? 7 : 13)) }} aria-hidden="true" />
            <button type="button" onClick={() => onFocus(c.gameId)}
              className={`gn-gapchip gn-gapchip--${c.result.toLowerCase()} gn-gapchip--${zone} gn-gapchip--${side} ${reducedMotion ? '' : 'is-dropping'}`}
              style={{ left: anchor, top, ['--gn-tx' as string]: tx, animationDelay: reducedMotion ? undefined : `${i * 60}ms` }}
              title={asDots ? `${c.sport} ${c.label} ${gapText(c.gap)}` : undefined} aria-label={name}>
              {side === 'right' || side === 'none' ? dot : null}
              {side !== 'none' && <><span className={`gn-tag gn-tag--${c.sport.toLowerCase()}`}>{c.sport}</span><span>{c.label}</span><small>{gapText(c.gap).replace(' pts', '')}</small></>}
              {side === 'left' ? dot : null}
            </button>
          </span>
        )
      })}
      <span className="gn-axis__unit" style={{ top: axisY + 16 }}>model minus market, points at decision →</span>
    </div>
  )
})

/** At phone width the axis shows dots only; this is the readable list beside it, biggest gap first. */
function GapList({ chips, onFocus }: { chips: GapChip[]; onFocus: (gameId: string) => void }) {
  const sorted = [...chips].filter((c) => c.gap != null).sort((a, b) => (b.gap ?? 0) - (a.gap ?? 0))
  return (
    <ol className="gn-gaplist">
      {sorted.map((c) => {
        const warning = isModelWarning(c.gap)
        return (
          <li key={c.key}>
            <button type="button" className={`gn-gaplist__row gn-gapchip--${c.result.toLowerCase()} ${warning ? 'is-warning' : ''}`} onClick={() => onFocus(c.gameId)}>
              <i className="gn-gapchip__dot" aria-hidden="true" />
              <span className={`gn-tag gn-tag--${c.sport.toLowerCase()}`}>{c.sport}</span>
              <span className="gn-gaplist__name">{c.label}</span>
              <b className="gn-nowrap">{gapText(c.gap)}</b>
              <span className="gn-gaplist__result">{c.result === 'OPEN' ? 'open' : c.result === 'WIN' ? 'W' : c.result === 'LOSS' ? 'L' : c.result === 'PUSH' ? 'P' : c.result}{warning ? ' · model warning' : ''}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

/** A two-line Gap Watch tally for the rail, so the warning rule is on screen beside the game too. */
export function GapMini({ board, label }: { board: DecisionBoard | null; label: string }) {
  const chips = useMemo(() => gapChips(board?.items ?? []), [board])
  const tally = useMemo(() => zoneTally(chips), [chips])
  if (!board || !chips.length) return null
  const row = (title: string, t: ZoneTally, warning: boolean) => (
    <p className={`gn-mini__row ${warning ? 'is-warning' : ''}`}>
      <span>{title}</span><b>{recordText(t)}</b><span className={t.profit >= 0 ? 'is-up' : 'is-down'}>{usd(t.profit, true)}</span>
      {t.open ? <small>{t.open} open</small> : null}
    </p>
  )
  return (
    <section className="gn-mini" aria-label={`Gap watch, ${label}`}>
      <h3>Gap watch · {label} <small>exploratory</small></h3>
      {row(`Gap ≤ ${GAP_WARNING_PTS}`, tally.corridor, false)}
      {row(`Gap > ${GAP_WARNING_PTS}`, tally.warning, true)}
    </section>
  )
}

export function GapWatch({ today, prevDay, tonightLabel, todayBoard, boardResource, onFocus, onFocusExternal, reducedMotion, compact }: {
  today: string
  /** The slate the "Yesterday" toggle loads. */
  prevDay: string
  /** What the tonight tally covers ("tonight", or after midnight "tonight · 10-19 and 10-20 slates"). */
  tonightLabel: string
  todayBoard: DecisionBoard | null
  /** The read behind `todayBoard`, for its as-of chip. */
  boardResource: FreshResource
  onFocus: (gameId: string) => void; onFocusExternal: (held: HeldBet) => void; reducedMotion: boolean; compact?: boolean
}) {
  const [sport, setSport] = useState<SportFilter>('ALL')
  const [showYesterday, setShowYesterday] = useState(false)
  const prev = useOptionalDecisionBoard(prevDay, showYesterday)
  const board = showYesterday ? prev.data : todayBoard
  const resource: FreshResource = showYesterday ? prev : { ...boardResource, data: todayBoard }
  const chips = useMemo(() => gapChips(board?.items ?? []).filter((c) => sport === 'ALL' || c.sport === sport), [board, sport])
  const tally = useMemo(() => zoneTally(chips), [chips])
  const names = (warning: boolean) => chips.filter((c) => c.gap != null && c.reading?.warning === warning && c.result !== 'OPEN').map((c) => c.label)
  const settled = tally.corridor.settled + tally.warning.settled
  const unplaced = chips.filter((c) => c.gap == null)
  const failed = showYesterday ? Boolean(prev.error) : Boolean(boardResource.error)
  const { width, ref } = useWidth<HTMLDivElement>(800)
  const phoneList = !compact && width < 560
  return (
    <section className="gn-gap" aria-labelledby="gn-gap-title" ref={ref}>
      <header className="gn-gap__head">
        <div>
          <p className="gn-kicker">Act III · Gap watch</p>
          <h2 id="gn-gap-title">The model-warning scoreboard <small className="gn-nowrap">{showYesterday ? `· ${prevDay} slate` : `· ${tonightLabel}`}</small></h2>
          <p className="gn-gap__exploratory"><b>EXPLORATORY</b> · A model-vs-market gap over {GAP_WARNING_PTS} points is James&apos;s model-warning sign, not an edge.</p>
        </div>
        <div className="gn-gap__tools">
          <AsOfChip label="Decisions" resource={resource} generatedAt={board?.generated_at ?? null} staleAfterMs={DECISIONS_STALE_AFTER_MS} />
          <span className="gn-seg" role="group" aria-label="Sport">
            {FILTERS.map((f) => (
              <button key={f} type="button" className={`gn-seg__btn ${sport === f ? 'is-on' : ''}`} aria-pressed={sport === f} onClick={() => setSport(f)}>{f === 'ALL' ? 'All' : f}</button>
            ))}
          </span>
          <button type="button" className={`gn-btn ${showYesterday ? 'is-on' : ''}`} aria-pressed={showYesterday} onClick={() => setShowYesterday((v) => !v)}
            title={`The ${prevDay} slate`}>Yesterday</button>
        </div>
      </header>
      {!board && (
        <p className="gn-gap__empty">{failed ? 'The decision board could not be read; the tally is unknown.' : 'Reading the decision board…'}</p>
      )}
      {board && (
        <>
          <div className="gn-zones">
            <ZoneBoard title={`GAP ≤ ${GAP_WARNING_PTS} PTS`} tally={tally.corridor} names={names(false)} warning={false} />
            <ZoneBoard title={`GAP > ${GAP_WARNING_PTS} PTS`} tally={tally.warning} names={names(true)} warning />
          </div>
          {chips.length > 0 ? (
            <>
              <GapAxis chips={chips} onFocus={onFocus} reducedMotion={reducedMotion} dots={compact || phoneList} />
              {phoneList && <GapList chips={chips} onFocus={onFocus} />}
            </>
          ) : <p className="gn-gap__empty">No funded bet on this slate{sport === 'ALL' ? '' : ` for ${sport}`}.</p>}
          {unplaced.length > 0 && <p className="gn-caption">Unplaced (no two-sided same-book price at decision, never estimated): {unplaced.map((c) => c.label).join(', ')}.</p>}
          <p className="gn-caption">
            {settled} settled {settled === 1 ? 'bet' : 'bets'} on {showYesterday ? 'this slate' : `${tonightLabel === 'tonight' ? 'tonight’s' : 'these'} decision board${tonightLabel === 'tonight' ? '' : 's'}`} (a bet decided on another slate is counted there); this is a look, not a test.
            {' '}Market = the same book&apos;s two prices when AISP decided, vig removed.{nflBasisNote(chips.map((c) => c.reading))}
          </p>
        </>
      )}
      {!compact && <SeasonGalaxy today={today} onFocusExternal={onFocusExternal} reducedMotion={reducedMotion} />}
    </section>
  )
}

// ---------------------------------------------------------- season galaxy --

interface WagerPayload { schema_version: string; generated_at_utc: string; data_as_of_utc: string | null; rows: WagerRow[] }

const wagerKey = () => cacheKey('wager-results', { sport: 'ALL', engine: '', era: '', date_from: '', date_to: '' })
const loadWagers = async (signal: AbortSignal) =>
  expectSchema(await apiRequest<WagerPayload>('/research/wager-results?sport=ALL', {}, signal), 'AISP_WAGER_RESULTS_V1')

export interface Star {
  row: WagerRow
  x: number | null
  y: number | null
  gap: number | null
  result: BetResult
  /** NFL: the decision the market was derived from (its price pair and freshness at decision). */
  item: DecisionItem | null
}

/** NFL slate days in the rows (derived, never hard-coded). */
export function nflSlateDays(rows: readonly WagerRow[]): string[] {
  return [...new Set(rows.filter((r) => r.sport === 'NFL' && r.slate_date).map((r) => r.slate_date!))].sort()
}

/** Each funded bet as a star: market no-vig at decision (x) against the model's comparison probability (y). */
export function seasonStars(rows: readonly WagerRow[], nflDecisions: ReadonlyMap<string, DecisionItem>): Star[] {
  return rows.map((row) => {
    let noVig: number | null = row.no_vig_probability
    let item: DecisionItem | null = null
    if (row.sport === 'NFL' && row.decision_id) {
      item = nflDecisions.get(row.decision_id) ?? null
      noVig = item?.market_integrity ? sameBookNoVig(item.market_integrity.selected_american, item.market_integrity.opposite_american) : null
    }
    const y = row.probability_valid === false ? null : row.comparison_probability
    const gap = noVig != null && y != null ? gapPts(y, noVig) : null
    return { row, x: noVig, y, gap, result: normalizeResult(row.status, row.result), item }
  })
}

function useSeasonNfl(days: readonly string[], enabled: boolean) {
  const [state, setState] = useState<{ items: Map<string, DecisionItem>; done: number; failed: number }>({ items: new Map(), done: 0, failed: 0 })
  const key = days.join(',')
  useEffect(() => {
    if (!enabled || !key) return
    let cancelled = false
    const queue = key.split(',')
    const items = new Map<string, DecisionItem>()
    let done = 0, failed = 0
    const worker = async () => {
      while (queue.length && !cancelled) {
        const day = queue.shift()!
        const board = await fetchQuery<DecisionBoard>(decisionsKey(day), loadDecisions(day), { staleTimeMs: STALE_TIME.slate }).catch(() => null)
        if (board) for (const item of board.items) items.set(item.decision_id, item)
        else failed += 1
        done += 1
        if (!cancelled) setState({ items: new Map(items), done, failed })
      }
    }
    void Promise.all([worker(), worker()])
    return () => { cancelled = true }
  }, [key, enabled])
  return state
}

function SeasonGalaxy({ today, onFocusExternal, reducedMotion }: { today: string; onFocusExternal: (held: HeldBet) => void; reducedMotion: boolean }) {
  const [asked, setAsked] = useState(false)
  if (!asked) {
    return (
      <div className="gn-galaxy gn-galaxy--closed">
        <button type="button" className="gn-btn gn-btn--wide" onClick={() => setAsked(true)}>
          Load the season <small>(1 read, then 1 per NFL slate day, two at a time)</small>
        </button>
        <span className="gn-caption">Every funded bet since Sep 1 as a star: model against the same-book market at decision.</span>
      </div>
    )
  }
  return <GalaxyLoaded today={today} onFocusExternal={onFocusExternal} reducedMotion={reducedMotion} />
}

function GalaxyLoaded({ onFocusExternal, reducedMotion }: { today: string; onFocusExternal: (held: HeldBet) => void; reducedMotion: boolean }) {
  const res = useApiResource<WagerPayload>(loadWagers, { key: wagerKey(), staleTimeMs: STALE_TIME.operational })
  const rows = useMemo(() => res.data?.rows ?? [], [res.data])
  const days = useMemo(() => nflSlateDays(rows), [rows])
  const nfl = useSeasonNfl(days, rows.length > 0)
  const ready = rows.length > 0 && nfl.done >= days.length
  const stars = useMemo(() => (ready ? seasonStars(rows, nfl.items) : []), [ready, rows, nfl.items])
  if (!res.data) {
    return <div className="gn-galaxy"><p className="gn-gap__empty">{res.error ? `The season could not be read (${res.error}).` : 'Reading every funded bet since Sep 1…'}</p></div>
  }
  if (!ready) {
    return <div className="gn-galaxy"><p className="gn-gap__empty" role="status">Deriving NFL market prices: {nfl.done} of {days.length} slate days read…</p></div>
  }
  return <Galaxy stars={stars} asOf={res.data.data_as_of_utc ?? res.data.generated_at_utc} nflFailed={nfl.failed} onFocusExternal={onFocusExternal} reducedMotion={reducedMotion} />
}

function zoneOf(s: Star): 'warning' | 'corridor' | null {
  if (s.gap == null) return null
  return Math.round(s.gap * 10) / 10 > GAP_WARNING_PTS ? 'warning' : 'corridor'
}

function rangeText(r: [number, number] | null) {
  return r ? `95% range ${signedPct(r[0])} to ${signedPct(r[1])}` : '95% range: too few bets'
}

const basisOf = (b: WagerRow['comparison_basis'], sport: string): ComparisonBasis =>
  b === 'NON_TIE_DERIVED' ? 'NON_TIE_DERIVED' : b === 'TIE_PROBABILITY_NOT_RECORDED' || (sport === 'NFL' && b == null) ? 'TIE_PROBABILITY_NOT_RECORDED' : 'RAW_NO_TIE_OUTCOME'

/**
 * A season star as the stage's held bet. For an NFL star the gap is the one its
 * decision priced (with that decision's own freshness); for the other sports
 * the stored no-vig is used and freshness at decision is "not recorded", never
 * assumed.
 */
export function heldFromWager(star: Pick<Star, 'row' | 'gap' | 'x' | 'item'>): HeldBet {
  const { row, gap, x: noVig, item } = star
  const [away, home] = (row.matchup ?? '').split('@').map((x) => x.trim())
  const result = normalizeResult(row.status, row.result)
  const fromDecision = item ? decisionGap(item) : null
  const reading: GapReading | null = fromDecision ?? (gap != null && noVig != null && row.comparison_probability != null ? {
    sport: row.sport, noVig, model: row.comparison_probability, rawModel: row.raw_probability ?? row.comparison_probability,
    basis: basisOf(row.comparison_basis, row.sport), gap, warning: isModelWarning(gap), sportsbook: row.sportsbook,
    selectedAmerican: row.entry_american ?? null, oppositeAmerican: null, observedAt: null, freshAtDecision: null,
  } : null)
  return {
    positionId: row.position_id, gameId: row.game_id, sourceGameId: null, sport: row.sport as SportCode, matchup: row.matchup,
    away: away || 'Away', home: home || 'Home', awayName: null, homeName: null, selection: row.selection,
    side: row.selected_side, american: row.entry_american, sportsbook: row.sportsbook, stake: row.stake, toWin: null,
    startsAt: row.starts_at ?? '', slateDate: row.slate_date, open: result === 'OPEN', sweatState: null, live: null,
    result, profit: row.net_profit, settledAt: row.settled_at, final: null, ledger: null, decision: item,
    gap: reading, score: null, game: null,
  }
}

function asOfText(iso: string | null): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  return Number.isFinite(t) ? ` · as of ${etShort(t)} ET ${mmdd(iso)}` : ''
}

const Galaxy = memo(function Galaxy({ stars, asOf, nflFailed, onFocusExternal, reducedMotion }: {
  stars: Star[]; asOf: string | null; nflFailed: number; onFocusExternal: (held: HeldBet) => void; reducedMotion: boolean
}) {
  const { ref, width } = useWidth<HTMLDivElement>(560)
  const S = Math.max(260, Math.min(width, 560))
  const pad = 30
  const sx = (p: number) => pad + p * (S - pad * 2)
  const sy = (p: number) => S - pad - p * (S - pad * 2)
  const placed = useMemo(() => stars.filter((s) => s.x != null && s.y != null), [stars])
  const unplaced = useMemo(() => stars.filter((s) => s.x == null || s.y == null), [stars])
  const ordered = useMemo(() => [...placed].sort((a, b) => Date.parse(a.row.decision_at ?? '') - Date.parse(b.row.decision_at ?? '')), [placed])
  const [active, setActive] = useState<number | null>(null)

  const zones = useMemo(() => {
    const bets = placed.map((s) => ({ key: s.row.position_id, gap: s.gap, result: s.result, stake: s.row.stake, profit: s.row.net_profit }))
    const t = zoneTally(bets)
    const settledRows = (zone: 'warning' | 'corridor') => placed
      .filter((s) => zoneOf(s) === zone && ['WIN', 'LOSS', 'PUSH'].includes(s.result))
      .map((s) => ({ stake: s.row.stake, profit: s.row.net_profit ?? 0 }))
    return {
      corridor: { tally: t.corridor, range: seededBootstrapRoi(settledRows('corridor')) },
      warning: { tally: t.warning, range: seededBootstrapRoi(settledRows('warning')) },
    }
  }, [placed])

  const bySport = useMemo(() => {
    const sports = [...new Set(placed.map((s) => s.row.sport))].sort()
    return sports.map((sport) => {
      const bets = placed.filter((s) => s.row.sport === sport).map((s) => ({ key: s.row.position_id, gap: s.gap, result: s.result, stake: s.row.stake, profit: s.row.net_profit }))
      return { sport, ...zoneTally(bets) }
    })
  }, [placed])

  const brightest = useMemo(() => placed.filter((s) => s.result === 'WIN').sort((a, b) => (a.x ?? 1) - (b.x ?? 1)).slice(0, 5), [placed])

  const focusStar = useCallback((s: Star) => {
    onFocusExternal(heldFromWager(s))
    if (typeof window.scrollTo === 'function') { try { window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' }) } catch { /* jsdom */ } }
  }, [onFocusExternal, reducedMotion])

  const onKey = (e: KeyboardEvent) => {
    if (!ordered.length) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setActive((a) => Math.max(0, Math.min(ordered.length - 1, (a ?? -1) + (e.key === 'ArrowRight' ? 1 : -1))))
    } else if ((e.key === 'Enter' || e.key === ' ') && active != null) {
      e.preventDefault(); focusStar(ordered[active])
    } else if (e.key === 'Escape') setActive(null)
  }
  const tip = active != null ? ordered[active] : null
  const corridorPoly = `${sx(0)},${sy(0)} ${sx(1)},${sy(1)} ${sx(0.9)},${sy(1)} ${sx(0)},${sy(0.1)}`
  const warnPoly = `${sx(0)},${sy(0.1)} ${sx(0.9)},${sy(1)} ${sx(0)},${sy(1)}`
  const twinkle = useRef(new Set<string>())
  if (!twinkle.current.size) placed.forEach((s, i) => { if (i % 4 === 1) twinkle.current.add(s.row.position_id) })

  return (
    <div className="gn-galaxy">
      <header className="gn-galaxy__head">
        <h3>Season gap galaxy <small>· {stars.length} funded bets since Sep 1{asOfText(asOf)}</small></h3>
        <p className="gn-caption">
          Exploratory look-back: {zones.corridor.tally.settled + zones.warning.tally.settled} settled bets, small samples, no multiple-comparison adjustment.
          The {GAP_WARNING_PTS}-pt line is James&apos;s rule (set Oct 17), drawn after most of these bets, so this is a look-back, not a test.
          Market = same-book no-vig at decision, not the closing line.{nflFailed ? ` ${nflFailed} NFL slate day(s) could not be read; their bets are unplaced.` : ''}
        </p>
      </header>
      <div className="gn-galaxy__body">
        <div className="gn-galaxy__sky" ref={ref}>
          <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} className="gn-galaxy__svg" role="group" tabIndex={0} onKeyDown={onKey}
            aria-label={`Scatter of ${placed.length} funded bets: market probability across, model probability up. Arrow keys step through the bets in decision order; Enter focuses one.`}
            onBlur={() => setActive(null)}>
            <rect x={pad} y={pad} width={S - pad * 2} height={S - pad * 2} className="gn-galaxy__frame" />
            <polygon points={corridorPoly} className="gn-galaxy__corridor" />
            <polygon points={warnPoly} className={`gn-galaxy__warn ${reducedMotion ? '' : 'is-shimmer'}`} />
            <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} className="gn-galaxy__diag" />
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
              <g key={t}>
                <text x={sx(t)} y={S - pad + 14} className="gn-galaxy__tick" textAnchor="middle">{Math.round(t * 100)}%</text>
                <text x={pad - 4} y={sy(t) + 3} className="gn-galaxy__tick" textAnchor="end">{Math.round(t * 100)}%</text>
              </g>
            ))}
            <text x={S - pad} y={S - 4} className="gn-galaxy__tick" textAnchor="end">market (same-book no-vig at decision) →</text>
            <text x={10} y={pad - 10} className="gn-galaxy__tick">↑ model</text>
            {ordered.map((s, i) => {
              const r = 2 + Math.sqrt(Math.max(0, s.row.stake)) * 1.6
              const delay = reducedMotion ? undefined : `${(i / Math.max(1, ordered.length)) * 2.5}s`
              const tw = twinkle.current.has(s.row.position_id) && !reducedMotion
              return (
                <g key={s.row.position_id} className={`gn-star gn-star--${s.result.toLowerCase()} ${reducedMotion ? '' : 'is-igniting'} ${tw ? 'is-twinkle' : ''} ${active === i ? 'is-active' : ''}`}
                  style={{ animationDelay: delay, ['--tw' as string]: `${3 + (i % 5)}s` }}
                  onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive((a) => (a === i ? null : a))} onClick={() => focusStar(s)}>
                  <circle cx={sx(s.x!)} cy={sy(s.y!)} r={r + 1.6} className="gn-star__ring" style={{ stroke: `var(--gn-${s.row.sport.toLowerCase()})` }} />
                  <circle cx={sx(s.x!)} cy={sy(s.y!)} r={r} className="gn-star__core" />
                </g>
              )
            })}
            {/* Zone labels last, on a dark backing, so no star prints over them. */}
            <g className="gn-galaxy__labels" aria-hidden="true">
              <text x={sx(0.04)} y={sy(0.95)} className="gn-galaxy__zonelabel gn-galaxy__zonelabel--warn">Model-warning zone</text>
              <text x={sx(0.04)} y={sy(0.95) + 13} className="gn-galaxy__zonestat">{zones.warning.tally.settled} settled · {recordText(zones.warning.tally)} · {usd(zones.warning.tally.profit, true)} ({signedPct(zones.warning.tally.roi)})</text>
              <text x={sx(0.97)} y={sy(0.05) - 13} className="gn-galaxy__zonelabel" textAnchor="end">Agreement corridor (≤ +{GAP_WARNING_PTS})</text>
              <text x={sx(0.97)} y={sy(0.05)} className="gn-galaxy__zonestat" textAnchor="end">{zones.corridor.tally.settled} settled · {recordText(zones.corridor.tally)} · {usd(zones.corridor.tally.profit, true)} ({signedPct(zones.corridor.tally.roi)})</text>
            </g>
          </svg>
          {tip && (
            <p className="gn-galaxy__tip" role="status" aria-live="polite">
              <b>{tip.row.sport} {tip.row.matchup}</b> · {tip.row.selection} {american(tip.row.entry_american)} · {mmdd(tip.row.decision_at)} ·
              model {((tip.y ?? 0) * 100).toFixed(1)}% vs market {((tip.x ?? 0) * 100).toFixed(1)}% ({gapText(tip.gap)}) · {tip.result === 'OPEN' ? 'open' : `${tip.result.toLowerCase()} ${usd(tip.row.net_profit, true)}`}
            </p>
          )}
        </div>
        <div className="gn-galaxy__side">
          <div className="gn-galaxy__zones">
            <p><b className="gn-zone__title">Warning zone (gap &gt; {GAP_WARNING_PTS})</b> {zones.warning.tally.settled} settled · {zones.warning.tally.wins}-{zones.warning.tally.losses}{zones.warning.tally.pushes ? `-${zones.warning.tally.pushes}` : ''} · {usd(zones.warning.tally.profit, true)} ({signedPct(zones.warning.tally.roi)}) · {rangeText(zones.warning.range)}</p>
            <p><b className="gn-zone__title">Corridor (gap ≤ {GAP_WARNING_PTS})</b> {zones.corridor.tally.settled} settled · {zones.corridor.tally.wins}-{zones.corridor.tally.losses}{zones.corridor.tally.pushes ? `-${zones.corridor.tally.pushes}` : ''} · {usd(zones.corridor.tally.profit, true)} ({signedPct(zones.corridor.tally.roi)}) · {rangeText(zones.corridor.range)}</p>
          </div>
          <details className="gn-galaxy__sports">
            <summary>By sport</summary>
            <table>
              <thead><tr><th scope="col">Sport</th><th scope="col">Gap ≤ {GAP_WARNING_PTS}</th><th scope="col">Gap &gt; {GAP_WARNING_PTS}</th></tr></thead>
              <tbody>
                {bySport.map((s) => (
                  <tr key={s.sport}>
                    <th scope="row">{s.sport}</th>
                    <td>{s.corridor.wins}-{s.corridor.losses} · {signedPct(s.corridor.roi)} <small>n={s.corridor.settled}</small></td>
                    <td>{s.warning.wins}-{s.warning.losses} · {signedPct(s.warning.roi)} <small>n={s.warning.settled}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          {brightest.length > 0 && (
            <div className="gn-galaxy__bright">
              <h4>Brightest stars <small>lowest market probability, settled wins</small></h4>
              <ul>
                {brightest.map((s) => (
                  <li key={s.row.position_id}>
                    <button type="button" className="gn-result gn-result--win" onClick={() => focusStar(s)}>
                      <b>{s.row.selection.replace(/\s+ML$/i, '')} {american(s.row.entry_american)}</b>
                      <span>market {((s.x ?? 0) * 100).toFixed(1)}% · {usd(s.row.net_profit, true)} · {mmdd(s.row.settled_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="gn-caption">A long-odds win is mostly one game&apos;s luck.</p>
            </div>
          )}
          {unplaced.length > 0 && (
            <p className="gn-caption">Unplaced (no same-book price pair or no valid probability): {unplaced.length} {unplaced.length === 1 ? 'bet' : 'bets'}.</p>
          )}
        </div>
      </div>
    </div>
  )
})
