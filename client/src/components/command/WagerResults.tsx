/**
 * Wager Results: the fifth Model Performance view.
 *
 * A different population from the accuracy tabs. Those score FORECASTS; this
 * reads OFFICIAL FUNDED POSITIONS (one row per position, joined to its exact
 * funding decision) from `/research/wager-results`. The server computes every
 * total from current records; nothing here recomputes a stake, a price or a
 * settlement. The active scope is always printed, so an all-sports portfolio
 * figure never sits under a single-model heading without saying so.
 */
import * as d3 from 'd3'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import { SPORT_NAMES, etTime, expectSchema, type SportCode } from '../../services/commandCenter'
import { Tooltip, useTooltip, useWidth } from './chartKit'
import { AsOf } from './RegionState'

export interface WagerSummary {
  n: number; settled: number; wins: number; losses: number; pushes: number; voids: number; decided: number
  settled_stake: number; net_profit: number; roi: number | null; roi_ci95: [number, number] | null
  estimated_profit: number | null; estimated_roi: number | null; estimated_n: number
  avg_recorded_return: number | null
  win_rate: number | null; win_rate_ci95: [number, number] | null
  avg_raw_probability: number | null; probability_n: number
  avg_adjusted_probability: number | null; adjusted_n: number
  /** Decided (win/loss) wagers with a comparison probability: the one denominator predicted and observed share. */
  comparison_n: number; avg_comparison_probability: number | null
  expected_wins: number | null; actual_wins_with_probability: number
  comparison_win_rate: number | null; comparison_win_rate_ci95: [number, number] | null
  tie_derived_n: number; binding_excluded: number
  open_count: number; open_exposure: number; open_estimated_profit: number
}
export interface WagerBand extends WagerSummary { key: string; label: string; lower: number | null; upper: number | null }
export interface WagerRow {
  position_id: string; decision_id: string | null; decision_phase: string | null
  game_id: string; matchup: string; starts_at: string | null; slate_date: string | null
  sport: SportCode; engine: string; experiment_id: string; experiment_name: string; experiment_version: number
  recommendation_policy_version: string | null; staking_policy_version: string | null
  selection: string; selected_side: 'HOME' | 'AWAY' | null
  decision_at: string | null; placed_at: string | null
  sportsbook: string | null; entry_american: number | null; entry_decimal: number | null
  raw_probability: number | null; adjusted_probability: number | null; tie_probability: number | null
  no_vig_probability: number | null
  /** Win probability given a decided result: raw p (no-tie markets) or pWin / (1 - pTie) (NFL, derived). */
  comparison_probability: number | null
  comparison_basis: 'RAW_NO_TIE_OUTCOME' | 'NON_TIE_DERIVED' | 'TIE_PROBABILITY_NOT_RECORDED' | 'INVALID_PROBABILITY_DOMAIN' | null
  /** False when the recorded probabilities are out of domain: shown for diagnosis, never banded or averaged. */
  probability_valid: boolean
  /** OK, or why the joined decision's estimates are withheld. */
  binding_status: string
  adjustments: null | { uncertainty: number | null; starter: number | null; lineup: number | null; uncertainty_status: string | null }
  policy_adjusts: boolean
  recorded_return: number | null; return_source: string | null
  return_band: string; probability_band: string
  stake: number; estimated_profit: number | null
  status: string; result: string | null; settled: boolean; net_profit: number | null; settled_at: string | null
  flags: string[]
}
export interface WagerResultsPayload {
  schema_version: string
  generated_at_utc: string
  data_as_of_utc: string | null
  population: { key: string; label: string; note: string; exclusions: string[]; positions_outside_funded_eras: number }
  scope: { sport: string; engine: string | null; era: string | null; date_from: string | null; date_to: string | null; label: string }
  date_basis: { field: string; label: string; note: string }
  methods: Record<string, string>
  options: {
    sports: { code: SportCode; label: string; n: number }[]
    engines: { sport: SportCode; engine: string; label: string; n: number }[]
    eras: { id: string; sport: SportCode; engine: string; label: string; n: number; first_slate: string | null; last_slate: string | null
      recommendation_policy_version: string | null }[]
  }
  summary: WagerSummary
  return_bands: WagerBand[]
  probability_bands: WagerBand[]
  rows: WagerRow[]
}

type BandFilter = { kind: 'return' | 'probability'; key: string; label: string } | null

const money = (v: number | null | undefined, signed = false) => {
  if (v == null) return '—'
  const text = `$${Math.abs(v).toFixed(2)}`
  if (!signed) return v < 0 ? `−${text}` : text
  return Math.abs(v) < 0.005 ? text : `${v > 0 ? '+' : '−'}${text}`
}
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const spct = (v: number | null | undefined, d = 1) => {
  if (v == null) return '—'
  const text = Math.abs(v * 100).toFixed(d)
  return Number(text) === 0 ? `${text}%` : `${v > 0 ? '+' : '−'}${text}%`
}
const ci = (pair: [number, number] | null | undefined, d = 0, signed = true) =>
  pair ? `[${signed ? spct(pair[0], d) : pct(pair[0], d)}, ${signed ? spct(pair[1], d) : pct(pair[1], d)}]` : 'n < 2'
const american = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)
const record = (b: WagerSummary) => `${b.wins}–${b.losses}${b.pushes || b.voids ? `–${b.pushes + b.voids}` : ''}`
const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 0.005 ? '' : v > 0 ? 'is-up' : 'is-down')

function adjustmentText(r: WagerRow): string {
  if (!r.policy_adjusts) return 'None (policy applies no adjustments)'
  if (!r.adjustments) return 'Not recorded'
  const a = r.adjustments
  const parts: string[] = []
  if (a.uncertainty) parts.push(`uncertainty −${(a.uncertainty * 100).toFixed(1)} pts`)
  else parts.push(`uncertainty ${a.uncertainty_status === 'APPLIED' ? '0' : 'unrecorded'}`)
  if (a.starter) parts.push(`starter −${(a.starter * 100).toFixed(1)} pts`)
  if (a.lineup) parts.push(`lineup −${(a.lineup * 100).toFixed(1)} pts`)
  return parts.join(' · ')
}

function settlementText(r: WagerRow): string {
  if (!r.settled) return r.status === 'OPEN' ? 'Open' : r.status.charAt(0) + r.status.slice(1).toLowerCase()
  const res = (r.result ?? '').toLowerCase()
  return `Settled · ${res === 'push' || res === 'void' ? `${res} (stake refunded)` : res || 'result unrecorded'}`
}

const CSV_COLUMNS: [string, (r: WagerRow) => unknown][] = [
  ['position_id', (r) => r.position_id], ['decision_id', (r) => r.decision_id], ['slate_date_et', (r) => r.slate_date],
  ['sport', (r) => r.sport], ['engine', (r) => r.engine], ['experiment', (r) => `${r.experiment_name} (v${r.experiment_version})`],
  ['recommendation_policy', (r) => r.recommendation_policy_version], ['staking_policy', (r) => r.staking_policy_version],
  ['game', (r) => r.matchup], ['selection', (r) => r.selection], ['selected_side', (r) => r.selected_side],
  ['decision_at_utc', (r) => r.decision_at], ['placed_at_utc', (r) => r.placed_at], ['sportsbook', (r) => r.sportsbook],
  ['entry_american', (r) => r.entry_american], ['entry_decimal', (r) => r.entry_decimal],
  ['original_probability', (r) => r.raw_probability], ['policy_adjusted_probability', (r) => r.adjusted_probability],
  ['tie_probability', (r) => r.tie_probability], ['comparison_probability', (r) => r.comparison_probability],
  ['comparison_basis', (r) => r.comparison_basis], ['binding_status', (r) => r.binding_status],
  ['policy_adjustments', adjustmentText],
  ['recorded_return', (r) => r.recorded_return], ['return_band', (r) => r.return_band], ['probability_band', (r) => r.probability_band],
  ['stake', (r) => r.stake], ['estimated_profit_at_entry', (r) => r.estimated_profit],
  ['status', (r) => r.status], ['result', (r) => r.result], ['net_profit', (r) => r.net_profit], ['settled_at_utc', (r) => r.settled_at],
  ['flags', (r) => r.flags.join(';')],
]

export function wagerCsv(rows: WagerRow[]): string {
  const cell = (v: unknown) => {
    const text = v == null ? '' : String(v)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [CSV_COLUMNS.map(([h]) => h).join(','), ...rows.map((r) => CSV_COLUMNS.map(([, get]) => cell(get(r))).join(','))].join('\n')
}

function downloadCsv(rows: WagerRow[], name: string) {
  const blob = new Blob([wagerCsv(rows)], { type: 'text/csv;charset=utf-8' })
  if (typeof URL.createObjectURL !== 'function') return
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function WagerResults({ pageSport, pageEngine }: { pageSport: SportCode; pageEngine?: string | null }) {
  const [allSports, setAllSports] = useState(false)
  const [engine, setEngine] = useState<string>(pageEngine ?? '')
  const [era, setEra] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [band, setBand] = useState<BandFilter>(null)
  const [showAll, setShowAll] = useState(false)
  const sport = allSports ? 'ALL' : pageSport
  const query = useMemo(() => {
    const q = new URLSearchParams({ sport })
    if (engine) q.set('engine', engine)
    if (era) q.set('era', era)
    if (dateFrom) q.set('date_from', dateFrom)
    if (dateTo) q.set('date_to', dateTo)
    return q.toString()
  }, [sport, engine, era, dateFrom, dateTo])
  const loader = useCallback(async (signal: AbortSignal) =>
    expectSchema(await apiRequest<WagerResultsPayload>(`/research/wager-results?${query}`, {}, signal), 'AISP_WAGER_RESULTS_V1'), [query])
  const resource = useApiResource<WagerResultsPayload>(loader, {
    key: cacheKey('wager-results', { sport, engine, era, date_from: dateFrom, date_to: dateTo }), staleTimeMs: STALE_TIME.operational })
  const data = resource.data
  const current = data && data.scope.sport === sport && (data.scope.engine ?? '') === engine && (data.scope.era ?? '') === era
    && (data.scope.date_from ?? '') === dateFrom && (data.scope.date_to ?? '') === dateTo ? data : null

  // The page's model may hold no funded positions (a research-only engine): widen to the sport rather than show an empty scope.
  useEffect(() => {
    if (!data || !engine) return
    if (!data.options.engines.some((e) => e.engine === engine && (sport === 'ALL' || e.sport === sport))) setEngine('')
  }, [data, engine, sport])
  useEffect(() => { setBand(null); setShowAll(false) }, [query])

  const engines = (data?.options.engines ?? []).filter((e) => sport === 'ALL' || e.sport === sport)
  const eras = (data?.options.eras ?? []).filter((e) => (sport === 'ALL' || e.sport === sport) && (!engine || e.engine === engine))
  const rows = useMemo(() => (current?.rows ?? []).filter((r) => !band
    || (band.kind === 'return' ? r.return_band === band.key : r.probability_band === band.key)), [current, band])
  const visible = showAll ? rows : rows.slice(0, 25)
  const s = current?.summary
  const pick = (kind: 'return' | 'probability', b: WagerBand) =>
    setBand((prev) => (prev && prev.kind === kind && prev.key === b.key ? null : { kind, key: b.key, label: b.label }))

  return (
    <div className="cc-wr" data-testid="wager-results">
      <div className="cc-wr__scopebar">
        <div>
          <p className="cc-wr__population">Population: <b>official funded positions</b> — not the scored forecasts behind the other four views.</p>
          <p className="cc-wr__scope" data-testid="wager-scope">{current?.scope.label ?? (allSports ? 'All official funded positions — all sports' : `Official funded positions — ${SPORT_NAMES[pageSport]}`)}</p>
        </div>
        <AsOf resource={resource} label="wager results" />
      </div>

      <div className="cc-wr__controls">
        <div className="cc-filters" role="group" aria-label="Wager scope" style={{ margin: 0 }}>
          <button type="button" aria-pressed={!allSports} onClick={() => { setAllSports(false); setEngine(''); setEra('') }}>{SPORT_NAMES[pageSport]} only</button>
          <button type="button" aria-pressed={allSports} onClick={() => { setAllSports(true); setEngine(''); setEra('') }}>All sports</button>
        </div>
        <label className="cc-wr__field"><span>Engine</span>
          <select value={engine} onChange={(e) => { setEngine(e.target.value); setEra('') }}>
            <option value="">All engines</option>
            {engines.map((e) => <option key={`${e.sport}|${e.engine}`} value={e.engine}>{e.label} · {e.n}</option>)}
          </select>
        </label>
        <label className="cc-wr__field"><span>Policy era</span>
          <select value={era} onChange={(e) => setEra(e.target.value)}>
            <option value="">All policy eras</option>
            {eras.map((e) => <option key={e.id} value={e.id}>{e.label} · {e.recommendation_policy_version ?? ''} · {e.n}</option>)}
          </select>
        </label>
        <label className="cc-wr__field"><span>Slate from</span>
          <input type="date" value={dateFrom} max={dateTo || undefined} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="cc-wr__field"><span>to</span>
          <input type="date" value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} />
        </label>
        {(engine || era || dateFrom || dateTo) && (
          <button type="button" className="cc-more" onClick={() => { setEngine(''); setEra(''); setDateFrom(''); setDateTo('') }}>Clear filters</button>
        )}
      </div>

      {!current || !s ? (
        <p className="cc-muted" role="status">{resource.error ? `Wager results are unavailable: ${resource.error}` : 'Loading official funded positions…'}</p>
      ) : s.n === 0 ? (
        <p className="cc-empty">No official funded position in this scope. Widen the scope above; nothing is hidden.</p>
      ) : (
        <>
          <div className="cc-kpis cc-wr__kpis" data-testid="wager-summary">
            <div className="cc-kpi"><small>Settled wagers</small><b>{s.settled}</b>
              <span>{s.wins} W · {s.losses} L · {s.pushes} push · {s.voids} void</span></div>
            <div className="cc-kpi"><small>Settled stake</small><b>{money(s.settled_stake)}</b>
              <span>pushes and voids refunded, stake still counted</span></div>
            <div className={`cc-kpi ${s.net_profit < 0 ? 'cc-kpi--bad' : s.net_profit > 0 ? 'cc-kpi--good' : ''}`}><small>Net profit</small>
              <b>{money(s.net_profit, true)}</b><span>ROI {spct(s.roi)} · 95% CI {ci(s.roi_ci95)}</span></div>
            <div className="cc-kpi"><small>Estimated at entry</small><b>{money(s.estimated_profit, true)}</b>
              <span>{spct(s.estimated_roi)} est. ROI · Σ stake × recorded return{s.estimated_n < s.settled ? ` · ${s.settled - s.estimated_n} missing` : ''}{s.binding_excluded ? ` · ${s.binding_excluded} withheld (decision does not match its position)` : ''}</span></div>
            <div className="cc-kpi"><small>Open wagers</small><b>{s.open_count}</b>
              <span>{money(s.open_exposure)} open exposure · est. {money(s.open_estimated_profit, true)} at entry · not in settled figures</span></div>
            <div className="cc-kpi"><small>Data as of</small><b className="cc-wr__asof">{etTime(current.data_as_of_utc, true)}</b>
              <span>Dates: {current.date_basis.label.toLowerCase()}</span></div>
          </div>

          <section className="cc-wr__block" aria-label="Results by recorded return band">
            <h4>A · By recorded policy-adjusted return at entry</h4>
            <p className="cc-note">Recorded return = expected profit per $1 at the entry price, as the funding decision stored it. Select a band to filter the wagers below.</p>
            <ReturnChart bands={current.return_bands} selected={band?.kind === 'return' ? band.key : null} onPick={(b) => pick('return', b)} />
            <div className="cc-table__scroll">
              <table className="cc-board cc-wr__table">
                <caption className="cc-sr">Settled results by recorded return band</caption>
                <thead><tr><th>Band</th><th>n</th><th>W–L(–P/V)</th><th>Stake</th><th>Est. profit</th><th>Actual profit</th><th>Est. ROI</th><th>Actual ROI</th><th>95% CI</th><th>Open</th></tr></thead>
                <tbody>{current.return_bands.map((b) => (
                  <tr key={b.key} className={band?.kind === 'return' && band.key === b.key ? 'is-selected' : ''}>
                    <td><button type="button" className="cc-linkish" aria-pressed={band?.kind === 'return' && band.key === b.key} onClick={() => pick('return', b)}>{b.label}</button></td>
                    <td>{b.settled}</td><td>{b.settled ? record(b) : '—'}</td><td>{money(b.settled_stake)}</td>
                    <td>{money(b.estimated_profit, true)}</td><td className={tone(b.settled ? b.net_profit : null)}>{b.settled ? money(b.net_profit, true) : '—'}</td>
                    <td>{spct(b.estimated_roi)}</td><td className={tone(b.roi)}>{spct(b.roi)}</td><td>{b.settled ? ci(b.roi_ci95) : '—'}</td><td>{b.open_count}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="cc-wr__block" aria-label="Results by predicted win probability band">
            <h4>B · By predicted win probability of the selected side</h4>
            <p className="cc-note">Bands use the original recorded model probability for the team actually selected (not the home side). Predicted and observed share one denominator, settled wins and losses: the comparison probability is that raw p where the market cannot tie (MLB, CFB) and the derived non-tie probability pWin ÷ (1 − pTie) for NFL, whose ties are refunded. Higher estimated return is not higher confidence: return = p × decimal odds − 1, so a longshot can carry a high return with a low probability.</p>
            <ProbabilityChart bands={current.probability_bands} selected={band?.kind === 'probability' ? band.key : null} onPick={(b) => pick('probability', b)} />
            <div className="cc-table__scroll">
              <table className="cc-board cc-wr__table">
                <caption className="cc-sr">Settled results by predicted selected-side win probability</caption>
                <thead><tr><th>Band</th><th>n</th><th>W–L(–P/V)</th><th>Avg original p</th><th>Avg selected-side p (non-tie, derived)</th><th>Avg adjusted p</th><th>Compared</th><th>Expected wins</th><th>Actual wins</th><th>Observed win rate</th><th>Wilson 95%</th><th>Actual ROI</th><th>Open</th></tr></thead>
                <tbody>{current.probability_bands.map((b) => (
                  <tr key={b.key} className={band?.kind === 'probability' && band.key === b.key ? 'is-selected' : ''}>
                    <td><button type="button" className="cc-linkish" aria-pressed={band?.kind === 'probability' && band.key === b.key} onClick={() => pick('probability', b)}>{b.label}</button></td>
                    <td>{b.settled}</td><td>{b.settled ? record(b) : '—'}</td><td>{pct(b.avg_raw_probability)}<small>recorded</small></td>
                    <td>{pct(b.avg_comparison_probability)}{b.tie_derived_n > 0 && <small>NFL derived, n={b.tie_derived_n}</small>}</td>
                    <td>{b.adjusted_n ? <>{pct(b.avg_adjusted_probability)}<small>MLB V2.1, n={b.adjusted_n}</small></> : '—'}</td>
                    <td>{b.comparison_n}{b.comparison_n < b.decided && <small>{b.decided - b.comparison_n} without p</small>}</td>
                    <td>{b.expected_wins == null ? '—' : b.expected_wins.toFixed(1)}</td><td>{b.comparison_n ? b.actual_wins_with_probability : '—'}</td>
                    <td>{pct(b.comparison_win_rate)}</td><td>{ci(b.comparison_win_rate_ci95, 0, false)}</td><td className={tone(b.roi)}>{spct(b.roi)}</td><td>{b.open_count}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="cc-wr__block" aria-label="Official funded wagers">
            <div className="cc-wr__tablehead">
              <h4>Wagers · {rows.length}{band ? ` in ${band.kind === 'return' ? 'return' : 'probability'} band ${band.label}` : ''}</h4>
              <div className="cc-wr__actions">
                {band && <button type="button" className="cc-more" onClick={() => setBand(null)}>Show all bands</button>}
                <button type="button" className="cc-wr__export" disabled={!rows.length}
                  onClick={() => downloadCsv(rows, `aisp-wager-results-${sport.toLowerCase()}${band ? `-${band.kind}-${band.key}` : ''}.csv`)}>Export CSV · {rows.length}</button>
              </div>
            </div>
            <div className="cc-table__scroll">
              <table className="cc-board cc-wr__wagers">
                <thead><tr><th>Game</th><th>Selection</th><th>Sport · model · policy</th><th>Decision</th><th>Entry</th><th>Original p</th><th>Adjustments</th><th>Recorded return</th><th>Stake</th><th>Settlement</th><th>Net</th></tr></thead>
                <tbody>{visible.map((r) => (
                  <tr key={r.position_id}>
                    <td>{r.matchup}<small>slate {r.slate_date ?? '—'}</small></td>
                    <td>{r.selection}<small>{r.selected_side ? r.selected_side.toLowerCase() : 'side unrecorded'}</small></td>
                    <td>{r.sport} · {r.engine}<small>{r.recommendation_policy_version} · v{r.experiment_version}</small></td>
                    <td>{etTime(r.decision_at, true)}<small>{r.decision_phase?.replace(/_/g, ' ').toLowerCase() ?? 'no decision'}</small></td>
                    <td>{american(r.entry_american)}<small>{r.sportsbook ?? '—'} · {r.entry_decimal?.toFixed(2) ?? '—'}</small></td>
                    <td>{pct(r.raw_probability)}{r.adjusted_probability != null && <small>adjusted {pct(r.adjusted_probability)}</small>}{r.tie_probability != null && <small>tie {pct(r.tie_probability)} (refunded){r.comparison_probability != null ? ` · non-tie ${pct(r.comparison_probability)} derived` : ''}</small>}{r.comparison_basis === 'TIE_PROBABILITY_NOT_RECORDED' && <small>tie prob not recorded</small>}{r.comparison_basis === 'INVALID_PROBABILITY_DOMAIN' && <small>invalid probability domain · not compared</small>}</td>
                    <td className="cc-wr__wrap">{adjustmentText(r)}</td>
                    <td>{spct(r.recorded_return)}{r.flags.length > 0 && <small>{r.flags.join(', ').toLowerCase().replace(/_/g, ' ')}</small>}</td>
                    <td>{money(r.stake)}</td>
                    <td>{settlementText(r)}</td>
                    <td className={tone(r.net_profit)}>{r.settled ? money(r.net_profit, true) : '—'}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
            {rows.length > 25 && <button type="button" className="cc-more" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show fewer' : `Show all ${rows.length}`}</button>}
          </section>

          <footer className="cc-wr__foot">
            <p><b>Methods.</b> {current.methods.roi} {current.methods.roi_ci95} {current.methods.win_rate_ci95} {current.methods.expected_wins} {current.methods.estimated_profit} {current.methods.binding}</p>
            <p><b>Exploratory.</b> {current.methods.exploratory} {current.date_basis.note}</p>
            <p><b>Population.</b> {current.population.note} {current.population.exclusions.join(' ')}</p>
          </footer>
        </>
      )}
    </div>
  )
}

const BAND_HEIGHT = 250

const ReturnChart = memo(function ReturnChart({ bands, selected, onPick }: { bands: WagerBand[]; selected: string | null; onPick: (b: WagerBand) => void }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const yAxis = useRef<SVGGElement | null>(null)
  const m = { top: 14, right: 12, bottom: 40, left: 46 }
  const values = bands.flatMap((b) => [b.estimated_roi, b.roi, ...(b.roi_ci95 ?? [])]).filter((v): v is number => v != null)
  const lo = Math.max(-1, Math.min(-0.2, d3.min(values) ?? -0.2))
  const hi = Math.max(0.2, d3.max(values) ?? 0.2)
  const x = d3.scaleBand<string>().domain(bands.map((b) => b.key)).range([m.left, width - m.right]).paddingInner(0.25).paddingOuter(0.1)
  const y = d3.scaleLinear().domain([lo, hi]).nice().range([BAND_HEIGHT - m.bottom, m.top])
  const bw = x.bandwidth() / 2
  useEffect(() => {
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5).tickFormat((v) => d3.format('+.0%')(v as number)) as never)
  }, [y])
  const tipFor = (b: WagerBand) => <><b>Recorded return {b.label}</b><dl><dt>Settled</dt><dd>{b.settled} ({record(b)})</dd><dt>Estimated ROI</dt><dd>{spct(b.estimated_roi)}</dd><dt>Actual ROI</dt><dd>{spct(b.roi)} {ci(b.roi_ci95)}</dd><dt>Actual profit</dt><dd>{money(b.net_profit, true)}</dd><dt>Open</dt><dd>{b.open_count}</dd></dl></>
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={BAND_HEIGHT} role="img" aria-label="Estimated versus actual ROI by recorded return band">
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        <line x1={m.left} x2={width - m.right} y1={y(0)} y2={y(0)} className="cc-perf__zero" />
        {bands.map((b) => {
          const x0 = x(b.key) ?? 0
          const active = selected === b.key
          return (
            <g key={b.key} className={`cc-wr__band${active ? ' is-selected' : ''}`} tabIndex={0} role="button" aria-pressed={active}
              aria-label={`Return band ${b.label}: ${b.settled} settled, estimated ROI ${spct(b.estimated_roi)}, actual ROI ${spct(b.roi)}`}
              onClick={() => onPick(b)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(b) } }}
              onMouseMove={(e) => show(e, tipFor(b))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(b))} onBlur={hide}>
              <rect x={x0 - 4} y={m.top} width={x.bandwidth() + 8} height={BAND_HEIGHT - m.top - m.bottom} className="cc-wr__hit" />
              {b.estimated_roi != null && <rect x={x0} width={bw - 2} y={Math.min(y(0), y(b.estimated_roi))} height={Math.abs(y(b.estimated_roi) - y(0))} className="cc-wr__est" />}
              {b.roi != null && b.settled > 0 && <rect x={x0 + bw} width={bw - 2} y={Math.min(y(0), y(b.roi))} height={Math.max(1, Math.abs(y(b.roi) - y(0)))} className={b.roi < 0 ? 'cc-wr__act is-down' : 'cc-wr__act'} />}
              {b.roi_ci95 && <line x1={x0 + bw * 1.5 - 1} x2={x0 + bw * 1.5 - 1} y1={y(b.roi_ci95[0])} y2={y(b.roi_ci95[1])} className="cc-wr__ci" />}
              <text x={x0 + x.bandwidth() / 2} y={BAND_HEIGHT - m.bottom + 15} textAnchor="middle" className="cc-wr__tick">{b.label}</text>
              <text x={x0 + x.bandwidth() / 2} y={BAND_HEIGHT - m.bottom + 29} textAnchor="middle" className="cc-muted-text">n={b.settled}</text>
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key cc-wr__key-est" />Estimated ROI at entry</li>
        <li><i className="cc-key cc-wr__key-act" />Actual ROI (settled)</li>
        <li><i className="cc-key cc-wr__key-ci" />Bootstrap 95% CI</li>
      </ul>
    </div>
  )
})

const ProbabilityChart = memo(function ProbabilityChart({ bands, selected, onPick }: { bands: WagerBand[]; selected: string | null; onPick: (b: WagerBand) => void }) {
  const { ref, width } = useWidth<HTMLDivElement>()
  const { tip, show, hide, frame } = useTooltip()
  const yAxis = useRef<SVGGElement | null>(null)
  const m = { top: 14, right: 12, bottom: 40, left: 46 }
  const x = d3.scaleBand<string>().domain(bands.map((b) => b.key)).range([m.left, width - m.right]).paddingInner(0.25).paddingOuter(0.1)
  const y = d3.scaleLinear().domain([0, 1]).range([BAND_HEIGHT - m.bottom, m.top])
  useEffect(() => {
    if (yAxis.current) d3.select(yAxis.current).call(d3.axisLeft(y).ticks(5).tickFormat((v) => d3.format('.0%')(v as number)) as never)
  }, [y])
  const tipFor = (b: WagerBand) => <><b>Selected-side p {b.label}</b><dl><dt>Compared (W/L)</dt><dd>{b.comparison_n} of {b.decided} decided</dd><dt>Avg original p</dt><dd>{pct(b.avg_raw_probability)}</dd><dt>Avg non-tie p</dt><dd>{pct(b.avg_comparison_probability)}</dd><dt>Expected · actual wins</dt><dd>{b.expected_wins?.toFixed(1) ?? '—'} · {b.actual_wins_with_probability}</dd><dt>Observed win rate</dt><dd>{pct(b.comparison_win_rate)} {ci(b.comparison_win_rate_ci95, 0, false)}</dd></dl></>
  return (
    <div className="cc-chart" ref={(node) => { ref.current = node; frame.current = node }}>
      <svg width={width} height={BAND_HEIGHT} role="img" aria-label="Predicted versus observed win rate by selected-side probability band">
        <g ref={yAxis} className="cc-axis" transform={`translate(${m.left}, 0)`} />
        {bands.map((b) => {
          const x0 = x(b.key) ?? 0
          const cx = x0 + x.bandwidth() / 2
          const active = selected === b.key
          return (
            <g key={b.key} className={`cc-wr__band${active ? ' is-selected' : ''}`} tabIndex={0} role="button" aria-pressed={active}
              aria-label={`Probability band ${b.label}: ${b.comparison_n} compared, predicted ${pct(b.avg_comparison_probability)}, observed ${pct(b.comparison_win_rate)}`}
              onClick={() => onPick(b)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(b) } }}
              onMouseMove={(e) => show(e, tipFor(b))} onMouseLeave={hide} onFocus={(e) => show(e, tipFor(b))} onBlur={hide}>
              <rect x={x0 - 4} y={m.top} width={x.bandwidth() + 8} height={BAND_HEIGHT - m.top - m.bottom} className="cc-wr__hit" />
              {b.comparison_win_rate_ci95 && <line x1={cx} x2={cx} y1={y(b.comparison_win_rate_ci95[0])} y2={y(b.comparison_win_rate_ci95[1])} className="cc-wr__ci" />}
              {b.avg_comparison_probability != null && <line x1={cx - x.bandwidth() * 0.32} x2={cx + x.bandwidth() * 0.32} y1={y(b.avg_comparison_probability)} y2={y(b.avg_comparison_probability)} className="cc-wr__pred" />}
              {b.comparison_win_rate != null && <circle cx={cx} cy={y(b.comparison_win_rate)} r={5.5} className="cc-wr__obs" />}
              <text x={cx} y={BAND_HEIGHT - m.bottom + 15} textAnchor="middle" className="cc-wr__tick">{b.label}</text>
              <text x={cx} y={BAND_HEIGHT - m.bottom + 29} textAnchor="middle" className="cc-muted-text">n={b.comparison_n}</text>
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} width={width} />
      <ul className="cc-legend">
        <li><i className="cc-key cc-wr__key-pred" />Avg selected-side p (non-tie, derived; = original p where no tie is possible)</li>
        <li><i className="cc-key cc-key--forecast" />Observed win rate (wins ÷ wins + losses)</li>
        <li><i className="cc-key cc-wr__key-ci" />Wilson 95% interval</li>
      </ul>
    </div>
  )
})
