/**
 * Market Replay — watching a price move.
 *
 * WHAT IS ON THE AXIS, AND WHAT IS NOT
 * ------------------------------------
 * One market at a time, one quantity per axis. On the moneyline that quantity
 * is the two-sided no-vig probability of one side; on a run line or a total it
 * is the price of one selection at that book's own line. The two are never
 * drawn together, and switching market re-fetches rather than re-scaling,
 * because a total is a different question and not a different view of the same
 * one.
 *
 * THE THREE THINGS THAT ARE NOT THE SAME LINE
 * -------------------------------------------
 *   the consensus     a mean over books that each had a complete pair
 *   the band          the spread BETWEEN those books at that instant
 *   the model         the forecast's own history, which is not a market
 *                     observation at all and is drawn dashed for that reason
 *
 * A book that stopped quoting keeps its last price on screen, because that is
 * what the market looked like -- and the readout says when that price was
 * really observed, so "still quoting" and "last seen at noon" never look alike.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react'
import { SourceBadge, agoOf, american, clockOf, decimals, pct, signedPoints } from './primitives'
import type { MarketTape } from '../../types/marketIntelligence'

const PLOT_HEIGHT = 210
const PAD = { top: 14, right: 12, bottom: 24, left: 46 }
/** Frames per second of wall clock while playing. Fast enough to read. */
const FRAME_MS = 90
/** The same window the coverage service and the tape use. */
const FRESH_MINUTES = 240

type Family = 'MONEYLINE' | 'SPREAD' | 'TOTAL'

export function MarketReplay({
  tape, family, selectedBook, onSelectBook,
}: {
  tape: MarketTape
  family: Family
  selectedBook: string | null
  onSelectBook: (id: string | null) => void
}) {
  const priced = useMemo(
    () => tape.frames.filter((frame) => frame.consensus_no_vig_a !== null),
    [tape.frames],
  )
  const [index, setIndex] = useState(() => Math.max(0, priced.length - 1))
  const [playing, setPlaying] = useState(false)
  const timer = useRef<number | null>(null)

  // A new tape is a new market: restart at the latest observation rather than
  // holding an index that pointed into a different series.
  useEffect(() => {
    setIndex(Math.max(0, priced.length - 1))
    setPlaying(false)
  }, [tape.game_id, tape.market.market_type, priced.length])

  useEffect(() => {
    if (!playing || priced.length < 2) return undefined
    timer.current = window.setInterval(() => {
      setIndex((current) => {
        if (current >= priced.length - 1) { setPlaying(false); return current }
        return current + 1
      })
    }, FRAME_MS)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [playing, priced.length])

  if (tape.requirement) {
    return <p className="bx-empty">{tape.requirement}</p>
  }
  if (priced.length === 0) {
    return (
      <p className="bx-empty">
        {tape.frames.length} observation{tape.frames.length === 1 ? '' : 's'} are stored for this
        market, but none has a complete two-sided quote at one book, so there is nothing
        comparable to replay.
      </p>
    )
  }

  const current = priced[Math.min(index, priced.length - 1)]
  const isProbability = family === 'MONEYLINE'
  const sideLabel = tape.market.side_a?.label ?? 'Side A'
  const format = (value: number) => isProbability ? pct(value, 1) : decimals(value, 3)
  const cutoff = tape.event.decision_cutoff_utc

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <ReplayPlot tape={tape} priced={priced} currentIndex={Math.min(index, priced.length - 1)}
        selectedBook={selectedBook} isProbability={isProbability} />

      <div className="bx-replay__transport">
        <button type="button" className="bx-btn bx-btn--primary" onClick={() => setPlaying((value) => !value)}
          disabled={priced.length < 2}>
          {playing ? <Pause size={14} /> : <Play size={14} />}{playing ? 'Pause' : 'Play'}
        </button>
        <button type="button" className="bx-btn" aria-label="Step back"
          onClick={() => { setPlaying(false); setIndex((value) => Math.max(0, value - 1)) }}>
          <ChevronLeft size={15} />
        </button>
        <button type="button" className="bx-btn" aria-label="Step forward"
          onClick={() => { setPlaying(false); setIndex((value) => Math.min(priced.length - 1, value + 1)) }}>
          <ChevronRight size={15} />
        </button>
        <button type="button" className="bx-btn" onClick={() => { setPlaying(false); setIndex(0) }}>
          <RotateCcw size={14} /> Opening
        </button>
        <input className="bx-replay__slider" type="range" min={0} max={priced.length - 1}
          value={Math.min(index, priced.length - 1)} aria-label="Replay time"
          onChange={(event) => { setPlaying(false); setIndex(Number(event.target.value)) }} />
        <span className="bx-replay__clock bx-num">{clockOf(current.t)}</span>
      </div>

      <div className="bx-feature__stats">
        <div className="bx-stat">
          <small>{isProbability ? `${sideLabel} no-vig` : 'Consensus price'}</small>
          <strong className="bx-num">{format(current.consensus_no_vig_a!)}</strong>
        </div>
        <div className="bx-stat">
          <small>Books with a pair</small>
          <strong className="bx-num">{current.usable_books}</strong>
        </div>
        <div className="bx-stat">
          <small>Book spread</small>
          <strong className="bx-num">
            {current.range_a ? `${((current.range_a[1] - current.range_a[0]) * 100).toFixed(1)} pp` : '—'}
          </strong>
        </div>
        <div className="bx-stat">
          <small>Best {tape.market.side_a?.label ?? 'A'}</small>
          <strong className="bx-num">{american(current.best_a?.american)}</strong>
        </div>
        <div className="bx-stat">
          <small>Best {tape.market.side_b?.label ?? 'B'}</small>
          <strong className="bx-num">{american(current.best_b?.american)}</strong>
        </div>
        <div className="bx-stat">
          <small>Velocity</small>
          <strong className="bx-num">
            {tape.milestones.movement
              ? `${signedPoints(tape.milestones.movement.velocity_points_per_hour, 2)} pp/h`
              : '—'}
          </strong>
        </div>
      </div>

      <Milestones tape={tape} isProbability={isProbability} />

      <div className="bx-replay__books">
        <div className="bx-replay__books-head">
          <h4>Every book at {clockOf(current.t)}</h4>
          <button type="button" className="bx-btn bx-btn--ghost"
            onClick={() => onSelectBook(null)} disabled={selectedBook === null}>
            Clear selection
          </button>
        </div>
        <div className="bx-scroll">
          <table className="bx-table">
            <thead>
              <tr>
                <th scope="col">Book</th><th scope="col">Source</th>
                <th scope="col">{tape.market.side_a?.label ?? 'A'}</th>
                <th scope="col">{tape.market.side_b?.label ?? 'B'}</th>
                <th scope="col">{isProbability ? 'No-vig' : 'Implied'}</th>
                <th scope="col">Observed</th><th scope="col">Usable</th>
              </tr>
            </thead>
            <tbody>
              {tape.books.map((book) => {
                const entry = latestAt(book.series, current.t)
                const chosen = selectedBook === book.sportsbook_id
                return (
                  <tr key={book.sportsbook_id} className={chosen ? 'is-selected' : ''}
                    onClick={() => onSelectBook(chosen ? null : book.sportsbook_id)}
                    style={{ cursor: 'pointer' }}>
                    <th scope="row">{book.name}</th>
                    <td style={{ textAlign: 'left' }}><SourceBadge source={book.key} /></td>
                    <td className="bx-num">{entry ? american(entry.a) : 'not yet quoted'}</td>
                    <td className="bx-num">{entry ? american(entry.b) : 'not yet quoted'}</td>
                    <td className="bx-num">{entry ? format(entry.no_vig_a) : '—'}</td>
                    <td>{entry ? agoOf(entry.observed_at_utc, new Date(current.t).getTime()) : '—'}</td>
                    <td>{entry ? (usableAt(entry, current.t) ? 'yes' : 'stale at this instant') : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="bx-dim" style={{ marginTop: 8 }}>
          A book keeps its last price until it publishes a new one; the observed column is when
          that price was really seen, not when this frame was taken. {tape.providers.note}
        </p>
      </div>

      <p className="bx-dim">
        {tape.observed_instants.toLocaleString()} observation instants stored
        {tape.frames_sampled
          ? `, thinned to ${tape.frame_count} frames for transport — frames are dropped, never interpolated`
          : ''}
        . {tape.market.probability_meaning}.
        {cutoff ? ` Decision cutoff ${clockOf(cutoff)}.` : ''}
      </p>
    </div>
  )
}

function Milestones({ tape, isProbability }: { tape: MarketTape; isProbability: boolean }) {
  const { opening, latest, at_cutoff: atCutoff, movement } = tape.milestones
  const format = (value: number | null | undefined) =>
    value == null ? '—' : isProbability ? pct(value, 1) : decimals(value, 3)
  return (
    <div className="bx-replay__milestones">
      {[
        { key: 'open', label: 'Opening', point: opening },
        { key: 'cutoff', label: 'At cutoff', point: atCutoff },
        { key: 'latest', label: 'Latest', point: latest },
      ].map(({ key, label, point }) => (
        <div key={key} className="bx-stat">
          <small>{label}</small>
          <strong className="bx-num">{format(point?.consensus_no_vig_a)}</strong>
          <span className="bx-dim">
            {point ? `${clockOf(point.t)} · ${point.usable_books} book${point.usable_books === 1 ? '' : 's'}`
              : 'no priced frame'}
          </span>
        </div>
      ))}
      <div className="bx-stat">
        <small>Opening → latest</small>
        <strong className="bx-num">
          {movement ? `${signedPoints(movement.points, 2)} pp` : '—'}
        </strong>
        <span className="bx-dim">{movement ? `over ${movement.hours_observed.toFixed(1)}h` : ''}</span>
      </div>
    </div>
  )
}

function ReplayPlot({
  tape, priced, currentIndex, selectedBook, isProbability,
}: {
  tape: MarketTape
  priced: MarketTape['frames']
  currentIndex: number
  selectedBook: string | null
  isProbability: boolean
}) {
  const width = 900
  const height = PLOT_HEIGHT
  const times = priced.map((frame) => new Date(frame.t).getTime())
  const t0 = times[0]
  const t1 = times[times.length - 1]
  const span = Math.max(1, t1 - t0)

  const values: number[] = []
  for (const frame of priced) {
    values.push(frame.consensus_no_vig_a!)
    if (frame.range_a) values.push(frame.range_a[0], frame.range_a[1])
  }
  const modelPoints = isProbability && tape.market.home_side
    ? tape.model_probability_series.map((entry) => ({
      t: new Date(entry.generated_at_utc).getTime(),
      // The model publishes a HOME probability; the axis is side A. Flip only
      // when side A is not the home side, so both are the same quantity.
      value: tape.market.home_side === 'a' ? entry.home_win_probability : 1 - entry.home_win_probability,
    })).filter((point) => Number.isFinite(point.value))
    : []
  for (const point of modelPoints) values.push(point.value)

  const low = Math.min(...values)
  const high = Math.max(...values)
  const pad = Math.max((high - low) * 0.22, isProbability ? 0.006 : 0.01)
  const yMin = low - pad
  const yMax = high + pad
  const x = (time: number) => PAD.left + ((time - t0) / span) * (width - PAD.left - PAD.right)
  const y = (value: number) =>
    PAD.top + (1 - (value - yMin) / Math.max(1e-9, yMax - yMin)) * (height - PAD.top - PAD.bottom)

  const bandPath = buildBand(priced, times, x, y)
  const consensusPath = priced
    .map((frame, i) => `${i ? 'L' : 'M'} ${x(times[i]).toFixed(1)} ${y(frame.consensus_no_vig_a!).toFixed(1)}`)
    .join(' ')
  const bookPath = selectedBook ? buildBookPath(tape, selectedBook, times, x, y) : null
  const modelPath = buildStep(modelPoints, x, y, t1)
  const cutoffTime = tape.event.decision_cutoff_utc ? new Date(tape.event.decision_cutoff_utc).getTime() : null
  const cutoffX = cutoffTime != null && cutoffTime >= t0 && cutoffTime <= t1 ? x(cutoffTime) : null
  const headX = x(times[currentIndex])
  const ticks = [yMin + (yMax - yMin) * 0.1, (yMin + yMax) / 2, yMax - (yMax - yMin) * 0.1]
  const format = (value: number) => isProbability ? pct(value, 1) : decimals(value, 3)

  return (
    <div className="bx-replay__plot">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} role="img"
        aria-label={`Price replay: consensus from ${format(priced[0].consensus_no_vig_a!)} to ${format(priced[priced.length - 1].consensus_no_vig_a!)}`}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)}
              stroke="rgba(255,255,255,.07)" strokeDasharray="2 5" />
            <text x={PAD.left - 7} y={y(tick) + 3.5} textAnchor="end"
              fill="var(--bx-dim)" style={{ font: '600 9.5px var(--bx-mono)' }}>{format(tick)}</text>
          </g>
        ))}
        {bandPath && <path d={bandPath} fill="rgba(74,144,255,.16)" stroke="none" />}
        {modelPath && (
          <path d={modelPath} fill="none" stroke="var(--bx-warn)" strokeWidth={1.6}
            strokeDasharray="6 4" strokeLinejoin="round" />
        )}
        <path d={consensusPath} fill="none" stroke="var(--bx-accent)" strokeWidth={2}
          strokeLinejoin="round" strokeLinecap="round" />
        {bookPath && (
          <path d={bookPath} fill="none" stroke="#ffffff" strokeWidth={1.5} strokeOpacity={0.8}
            strokeLinejoin="round" />
        )}
        {cutoffX != null && (
          <g>
            <line x1={cutoffX} x2={cutoffX} y1={PAD.top} y2={height - PAD.bottom}
              stroke="var(--bx-bad)" strokeWidth={1} strokeDasharray="3 3" />
            <text x={cutoffX - 4} y={PAD.top + 9} textAnchor="end" fill="var(--bx-bad)"
              style={{ font: '600 9px var(--bx-mono)' }}>CUTOFF</text>
          </g>
        )}
        <line x1={headX} x2={headX} y1={PAD.top} y2={height - PAD.bottom}
          stroke="var(--bx-text)" strokeWidth={1} strokeOpacity={0.5} />
        <circle cx={headX} cy={y(priced[currentIndex].consensus_no_vig_a!)} r={4.5}
          fill="var(--bx-accent)" stroke="#04211f" strokeWidth={1.5} />
        <text x={PAD.left} y={height - 7} fill="var(--bx-dim)" style={{ font: '600 9.5px var(--bx-mono)' }}>
          {clockOf(priced[0].t)}
        </text>
        <text x={width - PAD.right} y={height - 7} textAnchor="end" fill="var(--bx-dim)"
          style={{ font: '600 9.5px var(--bx-mono)' }}>{clockOf(priced[priced.length - 1].t)}</text>
      </svg>
      <div className="bx-dumbbell__legend">
        <span><i style={{ background: 'var(--bx-accent)' }} />Consensus (books with a complete pair)</span>
        <span><i style={{ background: 'rgba(74,144,255,.5)' }} />Spread between books</span>
        {modelPath && <span><i style={{ background: 'var(--bx-warn)' }} />Model forecast — not a market observation</span>}
        {bookPath && <span><i style={{ background: '#fff' }} />Selected book</span>}
      </div>
    </div>
  )
}

function buildBand(
  priced: MarketTape['frames'], times: number[],
  x: (t: number) => number, y: (v: number) => number,
): string | null {
  const withRange = priced.map((frame, i) => ({ frame, t: times[i] }))
    .filter((entry) => entry.frame.range_a != null)
  if (withRange.length < 2) return null
  const top = withRange.map((entry, i) =>
    `${i ? 'L' : 'M'} ${x(entry.t).toFixed(1)} ${y(entry.frame.range_a![1]).toFixed(1)}`).join(' ')
  const bottom = [...withRange].reverse()
    .map((entry) => `L ${x(entry.t).toFixed(1)} ${y(entry.frame.range_a![0]).toFixed(1)}`).join(' ')
  return `${top} ${bottom} Z`
}

/** One book's own path, forward-filled exactly as the service does. */
function buildBookPath(
  tape: MarketTape, bookId: string, times: number[],
  x: (t: number) => number, y: (v: number) => number,
): string | null {
  const book = tape.books.find((entry) => entry.sportsbook_id === bookId)
  if (!book || (book.series ?? []).length === 0) return null
  const points: string[] = []
  let cursor = 0
  let value: number | null = null
  const series = book.series ?? []
  for (const time of times) {
    while (cursor < series.length && new Date(series[cursor].t).getTime() <= time) {
      value = series[cursor].no_vig_a
      cursor += 1
    }
    if (value == null) continue
    points.push(`${points.length ? 'L' : 'M'} ${x(time).toFixed(1)} ${y(value).toFixed(1)}`)
  }
  return points.length > 1 ? points.join(' ') : null
}

/** A forecast holds until the next one is published, so it is a step, not a slope. */
function buildStep(
  points: { t: number; value: number }[],
  x: (t: number) => number, y: (v: number) => number, end: number,
): string | null {
  if (points.length === 0) return null
  const segments: string[] = []
  points.forEach((point, index) => {
    const next = index + 1 < points.length ? points[index + 1].t : end
    segments.push(`${index ? 'L' : 'M'} ${x(point.t).toFixed(1)} ${y(point.value).toFixed(1)}`)
    segments.push(`L ${x(next).toFixed(1)} ${y(point.value).toFixed(1)}`)
  })
  return segments.join(' ')
}

/**
 * Whether a carried price is still usable AT THE FRAME BEING VIEWED.
 *
 * The stored flag belongs to the instant the book last MOVED, and a book that
 * has not moved in twelve hours would carry a "usable" from this morning into
 * this evening -- so the table would print "12h ago" and "yes" on one row and
 * contradict itself. The frame's own aggregates already recompute this; the
 * per-book cell has to as well.
 */
export function usableAt(
  entry: MarketTape['books'][number]['series'][number], frameTime: string,
): boolean {
  const age = (new Date(frameTime).getTime() - new Date(entry.observed_at_utc).getTime()) / 60_000
  return entry.usable && age <= FRESH_MINUTES
}

export function latestAt(series: MarketTape['books'][number]['series'] | undefined, at: string) {
  const moment = new Date(at).getTime()
  let chosen: MarketTape['books'][number]['series'][number] | null = null
  // A book with no change points is a book that never published a complete
  // pair. That is a legitimate state, not a malformed payload.
  for (const entry of series ?? []) {
    if (new Date(entry.t).getTime() > moment) break
    chosen = entry
  }
  return chosen
}
