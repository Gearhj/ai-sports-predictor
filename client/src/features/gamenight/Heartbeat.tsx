/**
 * Act II, the Bankroll Heartbeat: the realized paper bankroll as a patient
 * monitor. The upper trace is the bankroll over real time; the beat strip
 * below is every settled bet as one spike, in settlement order (up for a win,
 * down for a loss, height ∝ √|change|, coloured by sport).
 *
 * The high, the low, the crossing and every ROI are computed from the ledger
 * at the as-of time shown; nothing is typed in. Open bets count only once
 * settled. Class `.gn-heart`, never `.cc-chart` (whose svg max-width clips).
 */
import * as d3 from 'd3'
import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { BankrollPoint, Ledger, SportCode } from '../../services/commandCenter'
import { operationalToday } from '../../services/operationalDate'
import { usd } from '../mission/missionData'
import { STARTING_LEVEL, allTimeHigh, lastCrossingAbove, lowPoint, pointKey, sportRoi } from './events'
import { signedPct } from './gap'
import { ledgerPositions } from './gameNightData'
import { useBox } from './gnHooks'
import { etShort } from './TopBar'

export const SPORT_STROKE: Record<string, string> = {
  MLB: 'var(--gn-mlb)', NFL: 'var(--gn-nfl)', CFB: 'var(--gn-cfb)', NHL: 'var(--gn-nhl)', NBA: 'var(--gn-nba)',
}

const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: '2-digit', day: '2-digit' })
export const mmdd = (iso: string | null | undefined) => (iso ? dayFmt.format(new Date(iso)).replace('/', '-') : '—')

/** Midnight ET of `day` as epoch ms (DST-safe: ET is UTC−4 or −5). */
export function etMidnight(day: string): number {
  for (const offset of [4, 5]) {
    const t = Date.parse(`${day}T00:00:00Z`) + offset * 3_600_000
    if (operationalToday(new Date(t)) === day && operationalToday(new Date(t - 3_600_000)) !== day) return t
  }
  return Date.parse(`${day}T05:00:00Z`)
}

/** Counts from the previous reading to a new one; the first reading appears as it is. */
function useCountUp(value: number | null, ms: number, reduced: boolean): number | null {
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (value == null || from.current == null || reduced || typeof window.requestAnimationFrame !== 'function' || from.current === value) {
      from.current = value; setShown(value); return
    }
    const start = performance.now()
    const a: number = from.current
    let raf = 0
    const step = (t: number) => {
      const u = Math.min(1, (t - start) / ms)
      setShown(a + (value - a) * (1 - Math.pow(1 - u, 3)))
      if (u < 1) raf = window.requestAnimationFrame(step)
      else from.current = value
    }
    raf = window.requestAnimationFrame(step)
    // Frames can stall in a background window; the reading always lands on the real value.
    const settle = window.setTimeout(() => { setShown(value); from.current = value }, ms + 150)
    return () => { window.cancelAnimationFrame(raf); window.clearTimeout(settle); from.current = value }
  }, [value, ms, reduced])
  return shown
}

interface HeartProps {
  ledger: Ledger | null
  restored: boolean
  stale: boolean
  failed: boolean
  asOf: number | null
  /** When tonight began (6 AM ET; see nightStartOf): the "Tonight" zoom starts here. */
  nightStart: number
  now: number
  height: number
  reducedMotion: boolean
  /** Settlement points that arrived while the page was open. */
  freshKeys: ReadonlySet<string>
  /**
   * The TV lower band: the monitor takes whatever height the band leaves it
   * (measured), and the per-sport vitals sit in the header row so nothing is
   * cut off at the band's edge.
   */
  band?: boolean
}

function HeartbeatImpl({ ledger, restored, stale, failed, asOf, nightStart, now, height, reducedMotion, freshKeys, band = false }: HeartProps) {
  const box = useBox<HTMLDivElement>({ width: 800, height })
  const { ref, width } = box
  const [scope, setScope] = useState<'season' | 'tonight'>('season')
  const [active, setActive] = useState<number | null>(null)
  const series = ledger?.combined_series ?? []
  const positions = useMemo(() => ledgerPositions(ledger), [ledger])
  const ath = useMemo(() => allTimeHigh(series), [series])
  const low = useMemo(() => lowPoint(series), [series])
  const crossing = useMemo(() => lastCrossingAbove(series, STARTING_LEVEL), [series])
  const midnight = nightStart

  const beats = useMemo(() => series
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.kind === 'SETTLEMENT' && (scope === 'season' || (p.at != null && Date.parse(p.at) >= midnight))), [series, scope, midnight])

  const totals = ledger?.totals
  const bankroll = totals?.realized_bankroll ?? null
  const shownBankroll = useCountUp(bankroll, 800, reducedMotion)
  const record = totals?.record
  const roi = useMemo(() => (ledger?.sports ?? [])
    .map((s) => ({ sport: s.sport, ...sportRoi(s.positions) }))
    .filter((s) => s.n > 0)
    .sort((a, b) => (b.roi ?? 0) - (a.roi ?? 0)), [ledger])

  const athRecent = ath && ath.at ? now - Date.parse(ath.at) <= 24 * 3_600_000 : false
  const lastPoint = series[series.length - 1]
  // "First time above $5,000 since <date>" only says something when that date is an earlier night.
  const crossedRecently = crossing && lastPoint && lastPoint.bankroll > STARTING_LEVEL
    && crossing.at != null && Date.parse(crossing.at) < nightStart
    && (() => {
      // The first point back above the level after the last dip.
      const dipIndex = series.findIndex((p, i) => i > crossing.index && p.bankroll <= STARTING_LEVEL)
      const back = series.findIndex((p, i) => i > dipIndex && p.bankroll > STARTING_LEVEL)
      return back >= 0 && series[back].at != null && now - Date.parse(series[back].at!) <= 24 * 3_600_000
    })()

  // ------------------------------------------------------------ geometry --
  const W = Math.max(280, width)
  // In the band the monitor is sized by the layout; it never grows past what is visible.
  const H = band ? Math.max(48, Math.round(box.height) || height) : height
  const traceH = Math.round(H * 0.56)
  const beatTop = traceH + 6
  const beatH = H - beatTop - 4
  const mid = beatTop + beatH / 2
  const pad = { l: 8, r: 10 }

  const trace = useMemo(() => {
    const pts = series.filter((p) => p.at != null)
    if (pts.length < 1) return null
    let shown: BankrollPoint[] = pts
    let x0 = Date.parse(pts[0].at!)
    if (scope === 'tonight') {
      const before = [...pts].reverse().find((p) => Date.parse(p.at!) < midnight)
      shown = [...(before ? [{ ...before, at: new Date(midnight).toISOString() }] : []), ...pts.filter((p) => Date.parse(p.at!) >= midnight)]
      x0 = midnight
    }
    if (!shown.length) return null
    const x1 = Math.max(now, Date.parse(shown[shown.length - 1].at!) + 60_000)
    const x = d3.scaleTime().domain([x0, x1]).range([pad.l, W - pad.r])
    const values = shown.map((p) => p.bankroll).concat(STARTING_LEVEL)
    const lo = d3.min(values) ?? STARTING_LEVEL
    const hi = d3.max(values) ?? STARTING_LEVEL
    const span = Math.max(4, hi - lo)
    const y = d3.scaleLinear().domain([lo - span * 0.12, hi + span * 0.18]).range([traceH - 4, 8])
    const extended = [...shown, { ...shown[shown.length - 1], at: new Date(x1).toISOString() }]
    const line = d3.line<BankrollPoint>().x((p) => x(Date.parse(p.at!))).y((p) => y(p.bankroll)).curve(d3.curveStepAfter)
    return { x, y, path: line(extended) ?? '', shown }
  }, [series, scope, midnight, now, W, traceH, pad.l, pad.r])

  const maxAbs = d3.max(beats, (b) => Math.abs(b.p.change ?? 0)) ?? 1
  const slot = beats.length ? (W - pad.l - pad.r) / beats.length : 0
  const spike = (change: number) => (Math.sqrt(Math.abs(change)) / Math.sqrt(maxAbs || 1)) * (beatH / 2 - 2)
  const beatPath = (k: number, change: number) => {
    const x = pad.l + k * slot
    const w = Math.max(1.5, slot)
    const h = Math.max(2, spike(change)) * (change >= 0 ? 1 : -1)
    return `M${x.toFixed(1)},${mid}L${(x + w * 0.3).toFixed(1)},${mid}L${(x + w * 0.45).toFixed(1)},${(mid - h).toFixed(1)}L${(x + w * 0.6).toFixed(1)},${(mid + h * 0.25).toFixed(1)}L${(x + w * 0.75).toFixed(1)},${mid}L${(x + w).toFixed(1)},${mid}`
  }

  const describe = (k: number | null) => {
    if (k == null || !beats[k]) return null
    const { p } = beats[k]
    const pos = p.position_id ? positions.get(p.position_id) : undefined
    return `${usd(p.change ?? 0, true)} · ${pos?.matchup ?? p.sport ?? ''}${pos?.selection ? ` · ${pos.selection}` : ''} · settled ${p.at ? `${mmdd(p.at)} ${etShort(Date.parse(p.at))} ET` : 'time not recorded'} · bankroll ${usd(p.bankroll)}`
  }
  const activeText = describe(active)

  const onKey = (e: KeyboardEvent) => {
    if (!beats.length) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      setActive((cur) => {
        const base = cur ?? beats.length - 1
        if (e.key === 'Home') return 0
        if (e.key === 'End') return beats.length - 1
        return Math.max(0, Math.min(beats.length - 1, base + (e.key === 'ArrowRight' ? 1 : -1)))
      })
    } else if (e.key === 'Escape') setActive(null)
  }
  const onPointer = (e: React.PointerEvent<SVGRectElement>) => {
    if (!beats.length || !slot) return
    const box = e.currentTarget.getBoundingClientRect()
    const k = Math.floor((e.clientX - box.left - pad.l * (box.width / W)) / (slot * (box.width / W)))
    setActive(Math.max(0, Math.min(beats.length - 1, k)))
  }

  const athX = trace && ath?.at ? trace.x(Date.parse(ath.at)) : null
  const athInView = athX != null && trace && (scope === 'season' || Date.parse(ath!.at!) >= midnight)
  const lowX = trace && low?.at ? trace.x(Date.parse(low.at)) : null
  const lowInView = lowX != null && trace && (scope === 'season' || Date.parse(low!.at!) >= midnight)
  const beatsCount = series.filter((p) => p.kind === 'SETTLEMENT').length

  const vitals = (
    <div className="gn-heart__vitals" aria-label="Return on settled stake by sport">
      {roi.map((r) => (
        <span key={r.sport} className={`gn-vital gn-sportline--${r.sport.toLowerCase()}`} title={`Small samples · n = ${r.n} settled · ${usd(r.profit, true)} on ${usd(r.stake)}`}>
          <span className={`gn-tag gn-tag--${r.sport.toLowerCase()}`}>{r.sport}</span>
          <b className={(r.roi ?? 0) >= 0 ? 'is-up' : 'is-down'}>{signedPct(r.roi)}</b>
          <small>n={r.n}</small>
        </span>
      ))}
      {roi.length > 0 && <span className="gn-vital__note">ROI on settled stake · small samples</span>}
    </div>
  )

  return (
    <section className={`gn-heart ${band ? 'gn-heart--band' : ''}`} aria-labelledby="gn-heart-title">
      <header className="gn-heart__head">
        <div>
          <p className="gn-kicker">Act II · Bankroll heartbeat</p>
          <h2 id="gn-heart-title" className="gn-heart__read">
            <span>BANKROLL <b className={athRecent ? 'gn-glint' : ''}>{bankroll == null ? (failed ? 'unknown' : '—') : usd(shownBankroll ?? bankroll)}</b></span>
            {totals && <span className={totals.profit_loss >= 0 ? 'is-up' : 'is-down'}>{usd(totals.profit_loss, true)} since {mmdd(series[0]?.at)}</span>}
            <span>{beatsCount} beats</span>
            {record && <span>{record.WIN}-{record.LOSS}-{record.PUSH}</span>}
            {totals && <span>open {usd(totals.open_exposure).replace('.00', '')}</span>}
          </h2>
          {ath && (athRecent
            ? <p className="gn-heart__badge"><b>NEW ALL-TIME HIGH</b> {usd(ath.bankroll)} at {ath.at ? `${etShort(Date.parse(ath.at))} ET` : '—'}{crossedRecently && crossing ? ` · first time above ${usd(STARTING_LEVEL).replace('.00', '')} since ${mmdd(crossing.at)}` : ''}
              {bankroll != null && bankroll < ath.bankroll ? <span className="gn-heart__since"> · now {usd(ath.bankroll - bankroll)} below it</span> : null}</p>
            : bankroll != null && <p className="gn-heart__sub">{usd(Math.max(0, ath.bankroll - bankroll))} below the high of {usd(ath.bankroll)} set {mmdd(ath.at)}</p>)}
        </div>
        <div className="gn-heart__tools">
          {band && vitals}
          <span className="gn-seg" role="group" aria-label="Heartbeat range">
            <button type="button" className={`gn-seg__btn ${scope === 'season' ? 'is-on' : ''}`} aria-pressed={scope === 'season'} onClick={() => { setScope('season'); setActive(null) }}>Season</button>
            <button type="button" className={`gn-seg__btn ${scope === 'tonight' ? 'is-on' : ''}`} aria-pressed={scope === 'tonight'} title={`Settlements since ${etShort(nightStart)} ET ${mmdd(new Date(nightStart).toISOString())}`} onClick={() => { setScope('tonight'); setActive(null) }}>Tonight</button>
          </span>
          {(restored || stale) && asOf != null && (
            <span className="gn-chip gn-chip--stale">{restored ? 'last saved view' : 'stale'}, as of {etShort(asOf)} ET</span>
          )}
        </div>
      </header>

      <div className={`gn-heart__monitor ${band ? 'gn-heart__monitor--fill' : ''}`} ref={ref} style={band ? undefined : { height: H }}>
        {!ledger && <p className="gn-heart__empty">{failed ? 'The ledger could not be read; the bankroll is unknown.' : 'Reading the ledger…'}</p>}
        {ledger && trace && (
          <svg className="gn-heart__svg" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
            aria-label={`Realized paper bankroll from ${usd(series[0]?.bankroll)} to ${usd(bankroll)}; all-time high ${usd(ath?.bankroll)} ${mmdd(ath?.at)}, low ${usd(low?.bankroll)} ${mmdd(low?.at)}.`}>
            <defs>
              <linearGradient id="gn-heart-glow" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="rgba(75,255,170,.28)" /><stop offset="1" stopColor="rgba(75,255,170,0)" />
              </linearGradient>
            </defs>
            <line className="gn-heart__base" x1={pad.l} x2={W - pad.r} y1={trace.y(STARTING_LEVEL)} y2={trace.y(STARTING_LEVEL)} />
            <text className="gn-heart__label" x={pad.l + 2} y={trace.y(STARTING_LEVEL) - 3}>{usd(STARTING_LEVEL).replace('.00', '')} start</text>
            <path className="gn-heart__trace" d={trace.path} />
            {lowInView && low && (
              <g className="gn-heart__low">
                <circle cx={lowX!} cy={trace.y(low.bankroll)} r={3.5} />
                <text x={lowX! > W * 0.7 ? lowX! - 6 : lowX! + 6} y={trace.y(low.bankroll) + 3} textAnchor={lowX! > W * 0.7 ? 'end' : 'start'}>low {usd(low.bankroll)} {mmdd(low.at)}</text>
              </g>
            )}
            {athInView && ath && (
              <g className={`gn-heart__ath ${athRecent && !reducedMotion ? 'is-new' : ''}`}>
                <line x1={athX!} x2={athX!} y1={trace.y(ath.bankroll)} y2={trace.y(ath.bankroll) - 14} />
                <path d={`M${athX!},${trace.y(ath.bankroll) - 14}l10,3.5l-10,3.5z`} />
                <text x={athX! > W * 0.62 ? athX! - 6 : athX! + 12} y={trace.y(ath.bankroll) - 6} textAnchor={athX! > W * 0.62 ? 'end' : 'start'}>ALL-TIME HIGH {usd(ath.bankroll)}</text>
              </g>
            )}
            <line className="gn-heart__mid" x1={pad.l} x2={W - pad.r} y1={mid} y2={mid} />
            <g className="gn-heart__beats" role="group" tabIndex={0} aria-label={`Beat strip: ${beats.length} settled bets in settlement order. Use the left and right arrow keys to read each one.`}
              onKeyDown={onKey} onFocus={() => setActive((a) => a ?? beats.length - 1)} onBlur={() => setActive(null)}>
              {beats.map(({ p }, k) => (
                <path key={pointKey(p)} d={beatPath(k, p.change ?? 0)}
                  className={`gn-beat ${(p.change ?? 0) >= 0 ? 'is-up' : 'is-down'} ${freshKeys.has(pointKey(p)) ? 'is-new' : ''} ${active === k ? 'is-active' : ''}`}
                  style={{ stroke: SPORT_STROKE[p.sport as SportCode] ?? 'var(--gn-ink)' }} />
              ))}
              <rect x={0} y={beatTop} width={W} height={beatH} fill="transparent" onPointerMove={onPointer} onPointerDown={onPointer} onPointerLeave={() => setActive(null)} />
            </g>
            {active != null && beats[active] && (
              <line className="gn-heart__cursorline" x1={pad.l + (active + 0.5) * slot} x2={pad.l + (active + 0.5) * slot} y1={beatTop} y2={H - 2} />
            )}
          </svg>
        )}
        {ledger && trace && !reducedMotion && <span className="gn-heart__sweep" aria-hidden="true" />}
        {activeText && <p className="gn-heart__tip" role="status" aria-live="polite">{activeText}</p>}
        {reducedMotion && freshKeys.size > 0 && <span className="gn-heart__newtag">NEW</span>}
      </div>

      {!band && vitals}
      <p className="gn-caption">
        Realized paper bankroll. Open bets count only once settled. Beats are in settlement order, not time; the trace above uses real time. Spike height is scaled; tap or use the arrow keys for the exact amount. &ldquo;Tonight&rdquo; starts at 6 AM ET, so a game that settles after midnight stays with its night.
      </p>
    </section>
  )
}

export const Heartbeat = memo(HeartbeatImpl)
