/**
 * The field the simulated population accumulates onto.
 *
 * Plays arrive in batches and are painted onto a canvas that is NOT cleared
 * between frames, so the picture builds the way the evidence does: one play,
 * then ten, then a shape. That is the entire point of the view -- a single
 * play tells you nothing about a distribution, and watching eight hundred of
 * them land is the fastest honest way to see one.
 *
 * Two layers, for two different lifetimes:
 *   - the TRAIL canvas keeps every revealed play until something clears it;
 *   - the OVERLAY canvas is redrawn each frame for the plays still in flight,
 *     the markers, and the field furniture.
 *
 * Nothing here decides which plays exist. It draws the ones handed to it, in
 * the order handed to it, and stops when they run out.
 */
import { useEffect, useRef, useState } from 'react'
import {
  familyOf, isAirborne, laneOffset, POPULATION_PALETTE, samplePlay,
  type PopulationPlay,
} from './populationGeometry'
import type { DensityLayer } from './populationDensity'

/**
 * THE FIELD IS 120 YARDS, NOT 100.
 *
 * The producer's `x` runs 0..100 from goal line to goal line: it is the FIELD
 * OF PLAY and it does not include either end zone. This surface used to draw
 * the end zones inside that same 0..100 span, over x 0..10 and x 90..100 -- so
 * a snap taken two yards from the goal line, a perfectly ordinary play, was
 * painted behind the end line. Nothing was wrong with the data; the picture
 * was drawn on the wrong ruler, and the result read as a model that did not
 * know where a football field ends.
 *
 * The rectangle is now 120 yards: end zone, 100 yards of play, end zone. The
 * producer's 0..100 maps onto the middle 100, so there is no coordinate a
 * normal play can hold that lands behind an end line.
 */
const FIELD_LENGTH = 100
const END_ZONE = 10
const TOTAL_LENGTH = FIELD_LENGTH + 2 * END_ZONE
const ASPECT = 0.52

/** Screen x for a play coordinate, clipped to the field of play. */
function playX(g: { left: number; width: number }, x: number): number {
  const yard = g.width / TOTAL_LENGTH
  return g.left + (END_ZONE + Math.max(0, Math.min(FIELD_LENGTH, x))) * yard
}

export interface PopulationFieldProps {
  plays: PopulationPlay[]
  /** How many of `plays` have been revealed so far. */
  revealed: number
  /** Keep every revealed play on screen, or only the most recent batch. */
  persistTrails: boolean
  /** Bumped by the caller to force a full repaint (clear, filter change). */
  repaintKey: number
  homeAbbr: string
  awayAbbr: string
  height?: number
  /**
   * The whole population's shape, painted under the individual paths. Null
   * when the artifact carries no spatial summary -- an event past kickoff
   * keeps its older artifact, which has none.
   */
  density?: DensityLayer | null
}

interface Geometry { left: number; top: number; width: number; height: number }

function drawField(ctx: CanvasRenderingContext2D, g: Geometry, home: string, away: string) {
  const { left, top, width, height } = g
  // Restrained depth: a single vertical gradient reads as stadium lighting
  // without turning the data surface into a photograph.
  const turf = ctx.createLinearGradient(0, top, 0, top + height)
  turf.addColorStop(0, '#0e2a20')
  turf.addColorStop(0.5, '#123a2b')
  turf.addColorStop(1, '#0b241b')
  ctx.fillStyle = turf
  ctx.fillRect(left, top, width, height)

  const yard = width / TOTAL_LENGTH
  ctx.lineWidth = 1
  // Yard lines belong to the field of play only, so they start at the goal
  // line rather than at the back of an end zone.
  for (let x = 0; x <= FIELD_LENGTH; x += 5) {
    const isTen = x % 10 === 0
    ctx.strokeStyle = isTen ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.09)'
    ctx.beginPath()
    ctx.moveTo(playX(g, x), top)
    ctx.lineTo(playX(g, x), top + height)
    ctx.stroke()
  }

  // End zones sit OUTSIDE the play coordinate space. HOME attacks left, so the
  // LEFT end zone is the one AWAY defends — it is AWAY's own end zone and
  // carries AWAY's label.
  ctx.fillStyle = 'rgba(8,45,80,.55)'
  ctx.fillRect(left, top, END_ZONE * yard, height)
  ctx.fillRect(left + (END_ZONE + FIELD_LENGTH) * yard, top, END_ZONE * yard, height)
  // The goal lines are the boundary a path may reach and may not cross.
  ctx.strokeStyle = 'rgba(255,255,255,.45)'
  ctx.lineWidth = 1.6
  for (const line of [0, FIELD_LENGTH]) {
    ctx.beginPath()
    ctx.moveTo(playX(g, line), top)
    ctx.lineTo(playX(g, line), top + height)
    ctx.stroke()
  }
  ctx.lineWidth = 1
  ctx.save()
  ctx.fillStyle = 'rgba(255,255,255,.5)'
  ctx.font = `600 ${Math.max(10, height * 0.085)}px "IBM Plex Mono", monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.translate(left + (END_ZONE / 2) * yard, top + height / 2)
  ctx.rotate(-Math.PI / 2)
  ctx.fillText(away, 0, 0)
  ctx.restore()
  ctx.save()
  ctx.fillStyle = 'rgba(255,255,255,.5)'
  ctx.font = `600 ${Math.max(10, height * 0.085)}px "IBM Plex Mono", monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.translate(left + (END_ZONE + FIELD_LENGTH + END_ZONE / 2) * yard, top + height / 2)
  ctx.rotate(Math.PI / 2)
  ctx.fillText(home, 0, 0)
  ctx.restore()

  ctx.strokeStyle = 'rgba(255,255,255,.28)'
  ctx.lineWidth = 1.5
  ctx.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1)
}

/**
 * The population's distribution, as a band under the paths.
 *
 * Deliberately unlike a path: a soft filled column, not a stroke, so it never
 * reads as "more plays were drawn". `start` is offense-relative, so each bin
 * describes two absolute field positions and is mirrored onto both -- that
 * follows from the producer's normalisation rather than being assumed here.
 */
function drawDensity(ctx: CanvasRenderingContext2D, g: Geometry, density: DensityLayer) {
  const yard = g.width / TOTAL_LENGTH
  ctx.save()
  for (const cell of density.cells) {
    const weight = cell.count / density.peak
    // Square-root, because a linear ramp makes everything but the mode
    // invisible on a distribution this peaked.
    const alpha = 0.05 + 0.3 * Math.sqrt(weight)
    ctx.fillStyle = POPULATION_PALETTE[cell.family]
    ctx.globalAlpha = alpha
    for (const x of [cell.x, FIELD_LENGTH - cell.x - cell.width]) {
      const from = playX(g, x)
      const to = playX(g, x + cell.width)
      ctx.fillRect(from, g.top, Math.max(1, to - from), g.height)
    }
  }
  ctx.restore()
}

function paintPlay(
  ctx: CanvasRenderingContext2D, g: Geometry, play: PopulationPlay, alpha: number,
) {
  const family = familyOf(play)
  const colour = POPULATION_PALETTE[family]
  // Lateral lane, so a population reads as a spread rather than one thick band.
  // See `laneOffset`: y carries no cross-play meaning, so this adds no claim.
  const lane = laneOffset(play)
  // Clipped to the field of play. A path may reach a goal line and may not
  // pass it: a touchdown terminates there rather than being extended into the
  // end zone with coordinates nothing measured.
  const toX = (x: number) => playX(g, x)
  const toY = (y: number) => g.top + Math.max(0.02, Math.min(0.98, y + lane)) * g.height
  const sampled = samplePlay(play)

  ctx.save()
  ctx.globalAlpha = alpha
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  sampled.forEach((points, index) => {
    const segment = play.segs[index]
    if (!segment || points.length < 2) return
    // The producer marks which segments are airborne; those are dashed. A
    // ground carry is solid. Nothing else distinguishes them, because nothing
    // else is known.
    ctx.setLineDash(isAirborne(segment) ? [5, 4] : [])
    ctx.strokeStyle = colour
    ctx.lineWidth = family === 'KICK' ? 1 : 1.6
    ctx.beginPath()
    ctx.moveTo(toX(points[0].x), toY(points[0].y))
    points.slice(1).forEach((point) => ctx.lineTo(toX(point.x), toY(point.y)))
    ctx.stroke()
  })
  ctx.setLineDash([])

  // A turnover gets a magenta endpoint; a score gets a bright ring. Both are
  // drawn from the producer's own marker locations, never inferred from the
  // path's last point.
  if (play.to) {
    const marker = play.mk.find((m) => m.k === 'TURNOVER')
    if (marker) {
      ctx.fillStyle = POPULATION_PALETTE.TURNOVER
      ctx.beginPath()
      ctx.arc(toX(marker.p[0]), toY(marker.p[1]), 3.2, 0, Math.PI * 2)
      ctx.fill()
    }
  }
  if (play.sc) {
    const marker = play.mk.find((m) => m.k === play.sc) ?? play.mk[play.mk.length - 1]
    if (marker) {
      ctx.strokeStyle = POPULATION_PALETTE.SCORE
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(toX(marker.p[0]), toY(marker.p[1]), 4.5, 0, Math.PI * 2)
      ctx.stroke()
    }
  }
  ctx.restore()
}

export function PopulationField({
  plays, revealed, persistTrails, repaintKey, homeAbbr, awayAbbr, height = 380,
  density = null,
}: PopulationFieldProps) {
  const trailRef = useRef<HTMLCanvasElement | null>(null)
  const baseRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const paintedTo = useRef(0)
  const paintedKey = useRef(-1)
  const sizeRef = useRef({ width: 0, height: 0, dpr: 1 })
  /*
   * Bumped by every resize.
   *
   * Resizing a canvas clears it, and the trail layer is painted incrementally --
   * only the plays newly revealed since the last pass. So without this, any
   * resize wiped every trail already drawn and `paintedTo` stopped them being
   * drawn again: the field went black and stayed black. It also meant a field
   * whose first layout landed after the first plays were revealed never painted
   * them at all, which is exactly what happens when the reveal outruns the
   * ResizeObserver.
   */
  const [sized, setSized] = useState(0)

  /**
   * Field box in CSS pixels, with a small inset so strokes are not clipped.
   *
   * A football field is much wider than it is tall, so on a narrow screen the
   * box that fits is far shorter than the height requested. The canvas is sized
   * to what the field actually needs rather than to the request, so a phone does
   * not reserve a hundred wasted pixels above and below the turf.
   */
  const boxHeight = () => {
    const { width } = sizeRef.current
    return width > 0 ? Math.min(height, Math.max(120, width * ASPECT)) : height
  }
  const geometry = (): Geometry => ({
    left: 6, top: 0, width: sizeRef.current.width - 12, height: boxHeight(),
  })

  const resize = () => {
    const wrap = wrapRef.current
    const trail = trailRef.current
    const base = baseRef.current
    if (!wrap || !trail || !base) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const width = wrap.offsetWidth
    // Nothing to do when the box has not actually changed: re-sizing a canvas
    // clears it, so a no-op resize would throw away every trail.
    if (width === sizeRef.current.width && height === sizeRef.current.height
      && dpr === sizeRef.current.dpr) return
    sizeRef.current = { width, height, dpr }
    const drawn = boxHeight()
    for (const canvas of [trail, base]) {
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(drawn * dpr))
      canvas.style.width = `${width}px`
      canvas.style.height = `${drawn}px`
      const ctx = canvas.getContext('2d')
      if (ctx) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.scale(dpr, dpr) }
    }
    paintedTo.current = 0
    setSized((count) => count + 1)
  }

  useEffect(() => {
    resize()
    /*
     * Do not rely on ResizeObserver for the FIRST measurement.
     *
     * On mount the wrapper can still report a zero width, and a canvas sized to
     * zero paints nothing. ResizeObserver normally corrects that on the next
     * frame -- but it does not fire in every environment (headless captures
     * under a virtual clock are the case that caught this), and when it does
     * not, the field stays black forever with no error anywhere.
     *
     * So the first layout is picked up by two cheap follow-ups regardless, and
     * the observer is left to handle genuine resizes afterwards.
     */
    const raf = requestAnimationFrame(() => resize())
    const settle = window.setTimeout(() => resize(), 120)
    let observer: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined' && wrapRef.current) {
      observer = new ResizeObserver(() => resize())
      observer.observe(wrapRef.current)
    }
    return () => {
      cancelAnimationFrame(raf)
      window.clearTimeout(settle)
      observer?.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height])

  // The base layer only ever holds field furniture, so it is repainted when the
  // box changes and not once per frame.
  useEffect(() => {
    const ctx = baseRef.current?.getContext('2d')
    if (!ctx) return
    const g = geometry()
    ctx.clearRect(0, 0, sizeRef.current.width, boxHeight())
    drawField(ctx, g, homeAbbr, awayAbbr)
    if (density) drawDensity(ctx, g, density)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [homeAbbr, awayAbbr, height, repaintKey, plays, sized, density])

  // Trails: paint only what is newly revealed, so cost is per new play rather
  // than per frame. A repaintKey change or a rewind starts the layer over.
  useEffect(() => {
    const canvas = trailRef.current
    const ctx = canvas?.getContext('2d')
    if (!ctx) return
    const g = geometry()
    // Repaint from scratch on a rewind, on a caller-requested repaint, or when
    // trails are off. `repaintKey` is handled HERE rather than in an effect of
    // its own: a separate wipe would run after this one (React runs effects in
    // declaration order) and erase what this just drew.
    const restart = revealed < paintedTo.current || !persistTrails || repaintKey !== paintedKey.current
    paintedKey.current = repaintKey
    if (restart) {
      ctx.clearRect(0, 0, sizeRef.current.width, boxHeight())
      paintedTo.current = 0
    }
    const from = persistTrails ? paintedTo.current : Math.max(0, revealed - 40)
    for (let index = from; index < revealed && index < plays.length; index += 1) {
      // Older plays sit back a little so the newest arrivals read as the
      // leading edge without hiding what came before.
      const age = (revealed - index) / Math.max(1, revealed)
      paintPlay(ctx, g, plays[index], persistTrails ? 0.28 + 0.5 * (1 - age) : 0.85)
    }
    paintedTo.current = revealed
  }, [plays, revealed, persistTrails, repaintKey, height, sized])


  return (
    <div className="pop-field" ref={wrapRef} style={{ height: sized ? boxHeight() : height }}>
      <canvas ref={baseRef} className="pop-field__canvas" aria-hidden="true" />
      <canvas
        ref={trailRef}
        className="pop-field__canvas pop-field__canvas--trail"
        role="img"
        aria-label={
          `Schematic field showing ${Math.min(revealed, plays.length)} of ${plays.length} modeled ` +
          'play paths from the representative simulated games. Passes are cyan with a dashed ' +
          'airborne segment, runs are gold, turnovers are marked in magenta and scores are ringed. ' +
          'Modeled geometry, not measured tracking.'
        }
      />
    </div>
  )
}
