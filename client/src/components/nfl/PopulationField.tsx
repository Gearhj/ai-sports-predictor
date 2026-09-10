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

const FIELD_LENGTH = 100
const ASPECT = 0.52

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

  const yard = width / FIELD_LENGTH
  ctx.lineWidth = 1
  for (let x = 0; x <= FIELD_LENGTH; x += 5) {
    const isTen = x % 10 === 0
    ctx.strokeStyle = isTen ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.09)'
    ctx.beginPath()
    ctx.moveTo(left + x * yard, top)
    ctx.lineTo(left + x * yard, top + height)
    ctx.stroke()
  }

  // End zones. HOME attacks left, so the LEFT end zone is the one AWAY defends
  // — it is AWAY's own end zone and carries AWAY's label.
  ctx.fillStyle = 'rgba(8,45,80,.55)'
  ctx.fillRect(left, top, 10 * yard, height)
  ctx.fillRect(left + 90 * yard, top, 10 * yard, height)
  ctx.save()
  ctx.fillStyle = 'rgba(255,255,255,.5)'
  ctx.font = `600 ${Math.max(10, height * 0.085)}px "IBM Plex Mono", monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.translate(left + 5 * yard, top + height / 2)
  ctx.rotate(-Math.PI / 2)
  ctx.fillText(away, 0, 0)
  ctx.restore()
  ctx.save()
  ctx.fillStyle = 'rgba(255,255,255,.5)'
  ctx.font = `600 ${Math.max(10, height * 0.085)}px "IBM Plex Mono", monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.translate(left + 95 * yard, top + height / 2)
  ctx.rotate(Math.PI / 2)
  ctx.fillText(home, 0, 0)
  ctx.restore()

  ctx.strokeStyle = 'rgba(255,255,255,.28)'
  ctx.lineWidth = 1.5
  ctx.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1)
}

function paintPlay(
  ctx: CanvasRenderingContext2D, g: Geometry, play: PopulationPlay, alpha: number,
) {
  const family = familyOf(play)
  const colour = POPULATION_PALETTE[family]
  // Lateral lane, so a population reads as a spread rather than one thick band.
  // See `laneOffset`: y carries no cross-play meaning, so this adds no claim.
  const lane = laneOffset(play)
  const toX = (x: number) => g.left + (Math.max(0, Math.min(100, x)) / 100) * g.width
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

  /** Field box in CSS pixels, with a small inset so strokes are not clipped. */
  const geometry = (): Geometry => {
    const { width } = sizeRef.current
    const boxHeight = Math.min(height, width * ASPECT)
    return { left: 6, top: (height - boxHeight) / 2, width: width - 12, height: boxHeight }
  }

  const resize = () => {
    const wrap = wrapRef.current
    const trail = trailRef.current
    const base = baseRef.current
    if (!wrap || !trail || !base) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const width = wrap.clientWidth
    // Nothing to do when the box has not actually changed: re-sizing a canvas
    // clears it, so a no-op resize would throw away every trail.
    if (width === sizeRef.current.width && height === sizeRef.current.height
      && dpr === sizeRef.current.dpr) return
    sizeRef.current = { width, height, dpr }
    for (const canvas of [trail, base]) {
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(height * dpr))
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
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
    ctx.clearRect(0, 0, sizeRef.current.width, height)
    drawField(ctx, g, homeAbbr, awayAbbr)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [homeAbbr, awayAbbr, height, repaintKey, plays, sized])

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
      ctx.clearRect(0, 0, sizeRef.current.width, height)
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
    <div className="pop-field" ref={wrapRef} style={{ height }}>
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
