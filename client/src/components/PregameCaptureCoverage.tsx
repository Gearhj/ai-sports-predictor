import { useEffect, useState } from 'react'
import { TableScroll } from './ui'
import { endpoints } from '../services/api'
import type { PregameCaptureCell, PregameCaptureCoverage as Coverage } from '../types/domain'

/**
 * Pregame snapshot coverage — the capture-certification audit matrix.
 * Rows = canonical games; grouped columns = provider; sub-columns = milestones.
 * Every cell is exactly one disjoint state rendered as glyph + text (never colour
 * alone); clicking a cell reveals the persisted observation behind it. Loaded only
 * when the audit section is opened; the read is DB-only (zero provider calls).
 */
export const CAPTURE_STATE: Record<string, { glyph: string; label: string; cls: string }> = {
  CAPTURED: { glyph: '●', label: 'Captured', cls: 'captured' },
  NOT_DUE: { glyph: '…', label: 'Not due', cls: 'notdue' },
  NO_LIQUIDITY: { glyph: '◌', label: 'No liquidity', cls: 'noliquidity' },
  MARKET_NOT_OPEN: { glyph: '◷', label: 'Market not open', cls: 'notopen' },
  PROVIDER_UNAVAILABLE: { glyph: '?', label: 'Provider unavailable', cls: 'unavailable' },
  MISSED_SCHEDULER_RUN: { glyph: '⊘', label: 'Missed scheduler run', cls: 'missed' },
  FAILED_PROVIDER_CALL: { glyph: '!', label: 'Failed provider call', cls: 'failed' },
  MAPPING_UNAVAILABLE: { glyph: '—', label: 'Mapping unavailable', cls: 'nomapping' },
  STRUCTURALLY_INELIGIBLE: { glyph: '✕', label: 'Structurally ineligible', cls: 'ineligible' },
  NOT_APPLICABLE: { glyph: '·', label: 'Not applicable', cls: 'na' },
}

export const MILESTONE_LABEL: Record<string, string> = {
  FIRST_DISCOVERED: 'FIRST',
  T_MINUS_6_HOURS: 'T−6h',
  T_MINUS_2_HOURS: 'T−2h',
  T_MINUS_30_MINUTES: 'T−30m',
  T_MINUS_5_MINUTES: 'T−5m',
  FINAL_VALID_PREGAME: 'FINAL',
}

/** Per-provider, per-milestone state counts must reconcile to the canonical slate. */
export function coverageReconciles(cov: Coverage): boolean {
  return cov.providers.every((p) =>
    cov.milestones.every((m) => {
      const cells = cov.rows.map((r) => r.providers[p]?.cells.find((c) => c.milestone === m)).filter(Boolean)
      const counted = Object.values(cov.counts[p]?.[m] ?? {}).reduce((a, b) => a + b, 0)
      return cells.length === cov.canonical_games && counted === cov.canonical_games && cov.totals[p]?.[m] === cov.canonical_games
    }),
  )
}

const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : '—'
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

function CellDetail({ cell, provider, game }: { cell: PregameCaptureCell; provider: string; game: string }) {
  const meta = CAPTURE_STATE[cell.state] ?? { glyph: '?', label: cell.state, cls: 'na' }
  return (
    <div className="pulse-capture__detail" role="region" aria-live="polite" aria-label="Selected capture cell">
      <p>
        <b>{game}</b> · {provider} · {MILESTONE_LABEL[cell.milestone] ?? cell.milestone}: <b>{meta.label}</b>
        {cell.state === 'CAPTURED' && cell.observed_before_start === false && <span className="pulse-capture__warn"> · source time NOT before first pitch</span>}
      </p>
      <p className="pulse-health-meta">{cell.reason}</p>
      {cell.observed_at && (
        <dl className="pulse-capture__dl">
          <dt>Source timestamp</dt><dd>{fmtTime(cell.observed_at)}</dd>
          <dt>Received</dt><dd>{fmtTime(cell.received_at)}</dd>
          <dt>Before first pitch</dt><dd>{cell.seconds_before_start == null ? '—' : `${Math.round(cell.seconds_before_start / 60)} min`}</dd>
          <dt>Cut label</dt><dd>{cell.timeline_cut ?? '—'}</dd>
          <dt>Midpoint / bid / ask</dt><dd>{pct(cell.midpoint)} / {pct(cell.best_bid)} / {pct(cell.best_ask)}</dd>
          <dt>Late ingestion</dt><dd>{cell.late_ingestion ? 'yes' : 'no'}</dd>
        </dl>
      )}
    </div>
  )
}

export function PregameCaptureMatrix({ cov }: { cov: Coverage }) {
  const [selected, setSelected] = useState<{ game: string; provider: string; milestone: string } | null>(null)
  const sel = selected
    ? cov.rows.find((r) => r.game_id === selected.game)?.providers[selected.provider]?.cells.find((c) => c.milestone === selected.milestone)
    : undefined
  const selRow = selected ? cov.rows.find((r) => r.game_id === selected.game) : undefined
  const reconciles = coverageReconciles(cov)
  return (
    <div className="pulse-capture">
      <p className="pulse-health-meta">
        {cov.canonical_games} canonical games · {cov.providers.length} providers × {cov.milestones.length} milestones · generated {fmtTime(cov.generated_at)} ·{' '}
        <span data-testid="capture-reconcile">{reconciles ? 'counts reconcile to the slate' : 'COUNTS DO NOT RECONCILE'}</span>
      </p>
      <TableScroll ariaLabel="Pregame snapshot coverage matrix">
        <table className="data-table pulse-matrix pulse-capture__table">
          <caption className="visually-hidden">Pregame snapshot coverage: one state per game, provider and milestone</caption>
          <thead>
            <tr>
              <th scope="col" rowSpan={2}>Game</th>
              <th scope="col" rowSpan={2}>First pitch</th>
              {cov.providers.map((p) => <th key={p} scope="colgroup" colSpan={cov.milestones.length}>{p}</th>)}
            </tr>
            <tr>
              {cov.providers.map((p) => cov.milestones.map((m) => <th key={`${p}-${m}`} scope="col"><abbr title={m}>{MILESTONE_LABEL[m] ?? m}</abbr></th>))}
            </tr>
          </thead>
          <tbody>
            {cov.rows.map((r) => (
              <tr key={r.game_id}>
                <th scope="row">{r.label}{r.doubleheader_status ? ` (${r.doubleheader_status})` : ''}</th>
                <td>{fmtTime(r.starts_at)} · {r.game_status}</td>
                {cov.providers.map((p) => cov.milestones.map((m) => {
                  const cell = r.providers[p]?.cells.find((c) => c.milestone === m)
                  const meta = cell ? (CAPTURE_STATE[cell.state] ?? { glyph: '?', label: cell.state, cls: 'na' }) : CAPTURE_STATE.NOT_APPLICABLE
                  const active = selected?.game === r.game_id && selected.provider === p && selected.milestone === m
                  return (
                    <td key={`${p}-${m}`} className={`pulse-matrix__cell pulse-capture__cell--${meta.cls}`}>
                      <button type="button" className={`pulse-capture__btn${active ? ' is-active' : ''}`} aria-pressed={active}
                        aria-label={`${r.label} ${p} ${MILESTONE_LABEL[m] ?? m}: ${meta.label}`}
                        onClick={() => setSelected(active ? null : { game: r.game_id, provider: p, milestone: m })}>
                        <span aria-hidden="true">{meta.glyph}</span><span className="pulse-matrix__label">{meta.label}</span>
                      </button>
                    </td>
                  )
                }))}
              </tr>
            ))}
            {cov.providers.map((p) => (
              <tr key={`sum-${p}`} className="pulse-capture__sum">
                <th scope="row">{p} captured</th>
                <td>of {cov.canonical_games}</td>
                {cov.providers.map((q) => cov.milestones.map((m) => (
                  <td key={`${q}-${m}`}>{q === p ? `${cov.counts[p]?.[m]?.CAPTURED ?? 0}` : ''}</td>
                )))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {sel && selRow && <CellDetail cell={sel} provider={selected!.provider} game={selRow.label} />}
      <p className="pulse-health-meta pulse-capture__legend">
        {Object.entries(CAPTURE_STATE).map(([k, v]) => <span key={k}><span aria-hidden="true">{v.glyph}</span> {v.label}</span>)}
      </p>
      <p className="pulse-health-meta">{cov.disclosure}</p>
    </div>
  )
}

export function PregameCaptureCoverage({ slateDate, open }: { slateDate: string; open: boolean }) {
  const [cov, setCov] = useState<Coverage | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    const ctrl = new AbortController()
    setError(null)
    endpoints.marketPulseCaptureCoverage(slateDate, ctrl.signal).then(setCov).catch((e: unknown) => {
      if (ctrl.signal.aborted) return
      setError(e instanceof Error ? e.message : 'Capture coverage unavailable')
    })
    return () => ctrl.abort()
  }, [slateDate, open])
  if (!open) return null
  if (error) return <p className="pulse-health-meta" role="alert">Capture coverage unavailable: {error}</p>
  if (!cov) return <p className="pulse-health-meta">Loading capture coverage…</p>
  return <PregameCaptureMatrix cov={cov} />
}
