/**
 * THE TRUTH METER (owner 10-24: a HOLY SHIT feature for Model Performance, put the fan first).
 *
 * One question a fan actually asks: "when AISP says 70%, does it happen 70% of the time?" Each scored model is a
 * reliability chart: every forecast bucket is a bubble (size = games), placed at what AISP said against what
 * happened, with its 95% range. On the diagonal is honest; below it the model was too sure; above it, too shy.
 * Hover a bubble for the sentence. Beside it, MODEL VS MARKET: at the moment each decision was made, how often the
 * model's probability beat the betting market's on the same game (lower log loss), straight from the evidence.
 *
 * Reads GET /research/model-performance/overview (the same evidence as the brief below). Research only.
 */
import { useCallback, useMemo, useState } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import './truthMeter.css'

type Bin = { lower: number; upper: number; n: number; mean_predicted: number; observed: number; observed_ci95?: [number, number] | null }
type Group = { sport: string; model: string; forecast_type: string; n: number; favourite_accuracy?: number | null; calibration_bins: Bin[]; calibration_fit?: { slope: number; intercept: number } | null; period?: { first_utc: string; last_utc: string } | null }
type DecisionGroup = { sport: string; model: string; market_decision_time?: { n: number; model_better_games: number; delta_log_loss: number; delta_log_loss_ci95?: [number, number] | null } | null }
type Overview = { groups: Group[]; decision_groups: DecisionGroup[]; evidence_as_of_utc?: string }

const COLOR: Record<string, string> = { CFB: '#b88cff', MLB: '#ff6b57', NFL: '#6ea4ff', NHL: '#7cc4e4', NBA: '#ffb14a' }
const pct = (v: number) => `${Math.round(v * 100)}%`

export function TruthMeter() {
  const loader = useCallback((signal: AbortSignal) => apiRequest<Overview>('/research/model-performance/overview', {}, signal), [])
  const res = useApiResource(loader, { key: 'truth-meter-overview', staleTimeMs: 300_000 })
  const groups = useMemo(() => (res.data?.groups ?? []).filter((g) => g.forecast_type === 'ALL' && g.n >= 30 && !/candidate/i.test(g.model)), [res.data])
  const [pick, setPick] = useState<string | null>(null)
  const [hover, setHover] = useState<Bin | null>(null)
  if (!groups.length) return null
  const g = groups.find((x) => `${x.sport}|${x.model}` === pick) ?? groups[0]
  const color = COLOR[g.sport] ?? '#2dd4bf'
  const bins = g.calibration_bins.filter((b) => b.n > 0)
  const W = 520; const H = 420; const pad = 48
  const X = (p: number) => pad + p * (W - pad - 16)
  const Y = (p: number) => H - pad - p * (H - pad - 16)
  const maxN = Math.max(...bins.map((b) => b.n))
  const r = (n: number) => 6 + 20 * Math.sqrt(n / maxN)
  const weightedGap = bins.reduce((s, b) => s + Math.abs(b.observed - b.mean_predicted) * b.n, 0) / Math.max(1, bins.reduce((s, b) => s + b.n, 0))
  const top = bins.filter((b) => b.mean_predicted >= 0.5).sort((a, b) => b.n - a.n)[0] ?? bins[0]
  const verdict = weightedGap <= 0.04 ? 'Honest' : weightedGap <= 0.08 ? 'Close' : 'Off'
  const markets = (res.data?.decision_groups ?? []).filter((d) => d.market_decision_time && d.market_decision_time.n >= 20)
  const say = (b: Bin) => `When ${g.model} said ${pct(b.lower)}–${pct(b.upper)} (about ${pct(b.mean_predicted)}), it happened ${pct(b.observed)} of the time, over ${b.n} games${b.observed_ci95 ? ` (95% range ${pct(b.observed_ci95[0])}–${pct(b.observed_ci95[1])})` : ''}.`

  return (
    <section className="tm" aria-label="The Truth Meter">
      <div className="tm__chart">
        <header>
          <h2>The Truth Meter</h2>
          <small>when AISP says a team wins X% of the time, does it?</small>
          <span className="tm__chips">{groups.map((x) => <button key={`${x.sport}|${x.model}`} type="button" className={x === g ? 'is-on' : ''} style={{ ['--c' as string]: COLOR[x.sport] }} onClick={() => setPick(`${x.sport}|${x.model}`)}>{x.sport} · {x.n}</button>)}</span>
        </header>
        <div className="tm__body">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Calibration of ${g.model}`} key={`${g.sport}|${g.model}`}>
            <defs><linearGradient id="tm-over" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="rgba(248,113,113,0)" /><stop offset="1" stopColor="rgba(248,113,113,.14)" /></linearGradient></defs>
            <polygon points={`${X(0)},${Y(0)} ${X(1)},${Y(1)} ${X(1)},${Y(0)}`} fill="url(#tm-over)" />
            <polygon points={`${X(0)},${Y(0)} ${X(1)},${Y(1)} ${X(0)},${Y(1)}`} fill="rgba(96,165,250,.06)" />
            {[0, 0.2, 0.4, 0.6, 0.8, 1].map((t) => <g key={t}><line x1={X(t)} x2={X(t)} y1={Y(0)} y2={Y(1)} className="tm__grid" /><line x1={X(0)} x2={X(1)} y1={Y(t)} y2={Y(t)} className="tm__grid" /><text x={X(t)} y={H - pad + 18} textAnchor="middle" className="tm__tick">{pct(t)}</text><text x={pad - 8} y={Y(t)} dy="0.35em" textAnchor="end" className="tm__tick">{pct(t)}</text></g>)}
            <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} className="tm__diag" />
            <text x={X(0.06)} y={Y(0.1)} className="tm__diaglab" transform={`rotate(-38 ${X(0.06)} ${Y(0.1)})`}>perfectly honest</text>
            <text x={X(0.72)} y={Y(0.3)} className="tm__zone">too sure</text>
            <text x={X(0.12)} y={Y(0.72)} className="tm__zone">too shy</text>
            <text x={(X(0) + X(1)) / 2} y={H - 6} textAnchor="middle" className="tm__axis">what AISP said</text>
            <text x={14} y={(Y(0) + Y(1)) / 2} textAnchor="middle" className="tm__axis" transform={`rotate(-90 14 ${(Y(0) + Y(1)) / 2})`}>what happened</text>
            <polyline points={bins.map((b) => `${X(b.mean_predicted)},${Y(b.observed)}`).join(' ')} className="tm__path" style={{ stroke: color }} />
            {bins.map((b, i) => (
              <g key={i} className="tm__bin" style={{ animationDelay: `${i * 90}ms` }} onMouseEnter={() => setHover(b)} onMouseLeave={() => setHover(null)}>
                {b.observed_ci95 ? <line x1={X(b.mean_predicted)} x2={X(b.mean_predicted)} y1={Y(b.observed_ci95[0])} y2={Y(b.observed_ci95[1])} className="tm__ci" style={{ stroke: color }} /> : null}
                <line x1={X(b.mean_predicted)} x2={X(b.mean_predicted)} y1={Y(b.mean_predicted)} y2={Y(b.observed)} className="tm__miss" />
                <circle cx={X(b.mean_predicted)} cy={Y(b.observed)} r={r(b.n)} style={{ fill: color }} />
                <text x={X(b.mean_predicted)} y={Y(b.observed)} dy="0.35em" textAnchor="middle" className="tm__n">{b.n}</text>
              </g>
            ))}
          </svg>
          <div className="tm__read">
            <span className={`tm__verdict is-${verdict.toLowerCase()}`}><small>Verdict</small><b>{verdict}</b><em>off by {pct(weightedGap)} on average</em></span>
            <p className="tm__say">{say(hover ?? top)}</p>
            {g.favourite_accuracy != null ? <p className="tm__fact">Its favourite won <b>{pct(g.favourite_accuracy)}</b> of {g.n} games.</p> : null}
            {g.calibration_fit ? <p className="tm__fact">{g.calibration_fit.slope < 0.9 ? 'Slope under 1: its strong calls are stronger than reality, so treat big numbers with care.' : g.calibration_fit.slope > 1.1 ? 'Slope over 1: it is more cautious than it needs to be.' : 'Slope near 1: confidence tracks reality.'} <small>(fit slope {g.calibration_fit.slope.toFixed(2)})</small></p> : null}
            <small className="tm__hint">Hover a bubble · bubble size = games · whiskers = 95% range</small>
          </div>
        </div>
      </div>
      <div className="tm__market">
        <h3>Model vs Market</h3>
        <small>at the moment each decision was made, same game: whose probability was closer to the result?</small>
        <ul>
          {markets.map((d) => { const m = d.market_decision_time!; const share = m.model_better_games / m.n; return (
            <li key={`${d.sport}|${d.model}`} style={{ ['--c' as string]: COLOR[d.sport] ?? '#2dd4bf' }}>
              <span className="tm__mname"><b>{d.sport}</b><small>{d.model}</small></span>
              <span className="tm__bar" aria-label={`model closer in ${m.model_better_games} of ${m.n}`}><i style={{ width: `${share * 100}%` }} /><em>{m.model_better_games} model</em><em>{m.n - m.model_better_games} market</em></span>
              <span className={`tm__who ${m.delta_log_loss > 0 ? 'is-market' : 'is-model'}`}>{m.delta_log_loss > 0 ? 'Market ahead' : 'Model ahead'}</span>
            </li>) })}
        </ul>
        <p className="tm__honest">{markets.length && markets.every((d) => (d.market_decision_time?.delta_log_loss ?? 0) > 0) ? "Honest read: on today's evidence the betting market is the tougher forecaster in every sport. " : 'Honest read: the market is a hard benchmark; where the model is ahead, the range is still wide. '}A gap over 10 points between AISP and the market is a warning sign, not an edge.</p>
      </div>
    </section>
  )
}
