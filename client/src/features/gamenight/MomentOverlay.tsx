/**
 * Settlement Moments: a broadcast lower-third for each bet the LEDGER settles
 * while the wall is open (never for an observed final), and the watermarked
 * "Replay tonight" reel of real past settlements.
 *
 * Wins and losses get exactly the same panel, the same size and the same time
 * on screen; only a long-shot or large win adds a full-screen confetti burst
 * behind it. The overlay is portalled to <body> (an ancestor transform would
 * turn fixed into absolute), or into the element that is fullscreen, so it is
 * never hidden by fullscreen. It announces through a polite live region that
 * holds only the words (never the counting amount), never traps focus, and
 * closes on Esc or a click.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { american, usd } from '../mission/missionData'
import { gapText } from './gap'
import { LIVE_WINDOW_MS, type Moment } from './events'
import { mmdd } from './Heartbeat'
import { context2d, devicePixelRatioCapped, useFullscreenElement } from './gnHooks'
import { etSeconds, etShort } from './TopBar'

export const MOMENT_MS = 7_000
export const MOMENT_MS_REDUCED = 10_000

const SPORT_HEX: Record<string, string> = { MLB: '#e0684f', NFL: '#5b8de0', CFB: '#a37ae6', NHL: '#5fb0d4', NBA: '#e6a24a' }

/** A long shot (+300 or longer) or a win of $10 or more gets the full-screen burst. */
export function isBigWin(m: Pick<Moment, 'kind' | 'american' | 'change'>): boolean {
  return m.kind === 'WIN' && ((m.american != null && m.american >= 300) || m.change >= 10)
}

function Confetti({ color, full = false }: { color: string; full?: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const [gone, setGone] = useState(false)
  useEffect(() => {
    const canvas = ref.current
    const ctx = context2d(canvas)
    if (!canvas || !ctx || typeof window.requestAnimationFrame !== 'function') { setGone(true); return }
    const dpr = devicePixelRatioCapped()
    const w = canvas.clientWidth || 600
    const h = canvas.clientHeight || 300
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const palette = [color, '#ffd76a', '#4bd996', '#ffffff']
    // In the panel, one fountain; full screen, two cannons firing from the lower corners across the viewport.
    const parts = Array.from({ length: full ? 220 : 160 }, (_, i) => {
      if (!full) {
        return {
          x: w / 2 + (Math.random() - 0.5) * w * 0.4, y: h * 0.9,
          vx: (Math.random() - 0.5) * 9, vy: -6 - Math.random() * 9, r: 2 + Math.random() * 3, c: palette[i % palette.length], spin: Math.random() * Math.PI,
        }
      }
      const left = i % 2 === 0
      const speed = Math.sqrt(h) * (0.6 + Math.random() * 0.55)
      const lift = Math.PI / 2 - (0.2 + Math.random() * 0.6)
      return {
        x: left ? w * 0.03 : w * 0.97, y: h * 0.98,
        vx: (left ? 1 : -1) * Math.cos(lift) * speed, vy: -Math.sin(lift) * speed,
        r: 3 + Math.random() * 4, c: palette[i % palette.length], spin: Math.random() * Math.PI,
      }
    })
    const start = performance.now()
    let raf = 0
    const step = (t: number) => {
      const u = (t - start) / 1800
      ctx.clearRect(0, 0, w, h)
      for (const p of parts) {
        p.vy += 0.32; p.x += p.vx; p.y += p.vy; p.spin += 0.2
        ctx.globalAlpha = Math.max(0, 1 - u)
        ctx.fillStyle = p.c
        ctx.fillRect(p.x, p.y, p.r * 2, p.r * Math.abs(Math.cos(p.spin)) + 1)
      }
      if (u < 1) raf = window.requestAnimationFrame(step)
      else setGone(true)
    }
    raf = window.requestAnimationFrame(step)
    return () => window.cancelAnimationFrame(raf)
  }, [color, full])
  if (gone) return null
  return <canvas ref={ref} className={`gn-confetti ${full ? 'gn-confetti--full' : ''}`} aria-hidden="true" />
}

function useCountUp(target: number, ms: number, reduced: boolean) {
  const [v, setV] = useState(reduced ? target : 0)
  useEffect(() => {
    if (reduced || typeof window.requestAnimationFrame !== 'function') { setV(target); return }
    const start = performance.now()
    let raf = 0
    const step = (t: number) => {
      const u = Math.min(1, (t - start) / ms)
      setV(target * (1 - Math.pow(1 - u, 3)))
      if (u < 1) raf = window.requestAnimationFrame(step)
    }
    raf = window.requestAnimationFrame(step)
    // Frames can stall in a background window; the real amount is never left showing a partial count.
    const settle = window.setTimeout(() => setV(target), ms + 150)
    return () => { window.cancelAnimationFrame(raf); window.clearTimeout(settle) }
  }, [target, ms, reduced])
  return v
}

function highLine(m: Moment): string | null {
  if (!m.newHigh) return null
  const prev = m.newHigh.previous
  const was = prev ? ` (previous ${usd(prev.bankroll)} on ${mmdd(prev.at)})` : ''
  return m.newHigh.partOfRun
    ? `Part of the run that set a new all-time high: ${usd(m.newHigh.bankroll)}${was}`
    : `Bankroll ${usd(m.newHigh.bankroll)}: NEW ALL-TIME HIGH${was}`
}

/**
 * The words of one moment, shared by the panel and the live region. A live
 * moment that reaches the screen more than 15 minutes after it settled (it
 * waited in the queue, or the tab was asleep) says when it settled instead of
 * "just settled".
 */
export function momentWords(m: Moment, now: number = Date.now()): { kicker: string; headline: string; lines: string[] } {
  const settled = m.at ? `${etSeconds(Date.parse(m.at))} ET` : 'time not recorded'
  const at = m.at ? Date.parse(m.at) : NaN
  const kicker = m.replay ? 'REPLAY' : Number.isFinite(at) && now - at > LIVE_WINDOW_MS ? `SETTLED AT ${etShort(at)} ET` : 'JUST SETTLED'
  if (m.kind === 'BATCH') {
    const high = highLine(m)
    return {
      kicker,
      headline: `SETTLEMENT RUN · ${m.at ? etShort(Date.parse(m.at)) : ''} · ${m.batch?.count ?? 0} bets · ${usd(m.batch?.net ?? 0, true)}`,
      lines: [`Bankroll ${usd(m.bankroll)} after the run.`, ...(high ? [high] : [])],
    }
  }
  const pick = [m.selection, american(m.american)].filter(Boolean).join(' ')
  const lines: string[] = []
  const parts = [pick + (m.sportsbook ? ` at ${m.sportsbook}` : ''), m.stake != null ? `${usd(m.stake).replace('.00', '')} stake` : null, `settled ${settled}`].filter(Boolean) as string[]
  lines.push(parts.join(' · '))
  if (m.gap) {
    lines.push(m.gap.warning
      ? `gap ${gapText(m.gap.gap)}, model warning`
      : `model ${(m.gap.model * 100).toFixed(1)}% vs market ${(m.gap.noVig * 100).toFixed(1)}% (${gapText(m.gap.gap)})`)
  }
  const high = highLine(m)
  if (high) lines.push(high)
  return { kicker, headline: `${m.kind} ${usd(m.change, true)}`, lines }
}

const Panel = memo(function Panel({ moment, words, reducedMotion, onClose }: { moment: Moment; words: ReturnType<typeof momentWords>; reducedMotion: boolean; onClose: () => void }) {
  const amount = useCountUp(Math.abs(moment.change), 600, reducedMotion)
  const sign = moment.change > 0 ? '+' : moment.change < 0 ? '−' : ''
  const tone = moment.kind === 'BATCH' ? 'batch' : moment.kind.toLowerCase()
  const peak = Boolean(moment.newHigh && !moment.newHigh.partOfRun)
  // The visual panel is hidden from assistive technology (its amount counts up every frame); the words are
  // announced once, from the live region beside it.
  return (
    <div className={`gn-moment gn-moment--${tone} ${reducedMotion ? 'is-static' : ''} ${peak && !reducedMotion ? 'is-high' : ''}`}
      style={{ ['--gn-moment-sport' as string]: SPORT_HEX[moment.sport ?? ''] ?? '#3fd8c4' }} onClick={onClose} aria-hidden="true">
      {moment.kind === 'WIN' && !reducedMotion && !isBigWin(moment) && <Confetti color={SPORT_HEX[moment.sport ?? ''] ?? '#3fd8c4'} />}
      {moment.replay && <span className="gn-moment__replay">REPLAY · real settlement at {moment.at ? `${etShort(Date.parse(moment.at))} ET${mmdd(moment.at) ? ` ${mmdd(moment.at)}` : ''}` : 'an unrecorded time'}</span>}
      <div className="gn-moment__inner">
        <p className="gn-moment__kicker">
          {moment.sport && <span className={`gn-tag gn-tag--${moment.sport.toLowerCase()}`}>{moment.sport}</span>}
          {words.kicker}{moment.matchup ? ` · ${moment.matchup}` : ''}
        </p>
        <p className="gn-moment__headline">
          {moment.kind === 'BATCH' ? words.headline : <>{moment.kind} <b>{sign}{usd(amount)}</b></>}
        </p>
        {words.lines.map((l) => <p key={l} className={`gn-moment__line ${/all-time high/i.test(l) ? 'gn-moment__line--high' : ''}`}>{l}</p>)}
        <p className="gn-moment__foot">Paper research bet; no real money.</p>
      </div>
    </div>
  )
})

/** Shows the head of the queue for 7 s (10 s with reduced motion), then the next. */
export function MomentOverlay({ queue, onDone, reducedMotion, paused }: { queue: Moment[]; onDone: (id: string) => void; reducedMotion: boolean; paused: boolean }) {
  const current = queue[0] ?? null
  const [leaving, setLeaving] = useState(false)
  const fullscreen = useFullscreenElement()
  useEffect(() => {
    if (!current || paused) return
    setLeaving(false)
    const hold = reducedMotion ? MOMENT_MS_REDUCED : MOMENT_MS
    const out = window.setTimeout(() => setLeaving(true), Math.max(0, hold - 250))
    const done = window.setTimeout(() => onDone(current.id), hold)
    return () => { window.clearTimeout(out); window.clearTimeout(done) }
  }, [current, paused, reducedMotion, onDone])
  useEffect(() => {
    if (!current) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onDone(current.id) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, onDone])
  // The words are fixed when the moment reaches the screen (a late live moment says when it settled).
  const words = useMemo(() => (current ? momentWords(current, Date.now()) : null), [current])
  if (typeof document === 'undefined') return null
  // In element fullscreen only that element's subtree is drawn, so the overlay goes inside it.
  const host = fullscreen && fullscreen !== document.documentElement && fullscreen instanceof HTMLElement ? fullscreen : document.body
  const big = current != null && isBigWin(current) && !reducedMotion
  return createPortal(
    <div className="gn-moments">
      {current && big && <Confetti key={`full-${current.id}`} color={SPORT_HEX[current.sport ?? ''] ?? '#3fd8c4'} full />}
      {current && words && (
        <div className={`gn-moments__slot ${leaving && !reducedMotion ? 'is-leaving' : ''}`} key={current.id}>
          <Panel moment={current} words={words} reducedMotion={reducedMotion} onClose={() => onDone(current.id)} />
          <button type="button" className="gn-moment__close" aria-label="Close this settlement card" onClick={() => onDone(current.id)}><X size={16} aria-hidden="true" /></button>
        </div>
      )}
      <p className="gn-sr" aria-live="polite">{words ? `${words.kicker}: ${words.headline}. ${words.lines.join('. ')}` : ''}</p>
    </div>,
    host,
  )
}
