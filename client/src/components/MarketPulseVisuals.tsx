import { useEffect, useMemo, useState } from 'react'
import { AnalyticsChart, chartTheme } from '../charts/AnalyticsChart'
import { Panel, TableScroll } from './ui'
import { endpoints } from '../services/api'
import type { MarketPulseGameDetail, MarketPulseRow, PredictionProviderProbability } from '../types/domain'

/** Source styling: colour PLUS a distinct symbol so the chart never depends on colour alone. */
export const SOURCE_STYLE = {
  ASP: { label: 'ASP', color: chartTheme.navy, symbol: 'diamond' },
  SPORTSBOOK: { label: 'Sportsbook (no-vig)', color: chartTheme.teal, symbol: 'rect' },
  POLYMARKET: { label: 'Polymarket', color: chartTheme.cyan, symbol: 'circle' },
  KALSHI: { label: 'Kalshi', color: chartTheme.orange, symbol: 'triangle' },
} as const

export type ComparisonMark = {
  source: keyof typeof SOURCE_STYLE
  value: number // 0..1 home-win probability
  reviewed: boolean // reviewed evidence (normal styling) vs research-only (hollow, grey)
  detail: string
}

export type ComparisonRow = {
  game_id: string
  label: string
  marks: ComparisonMark[]
  reviewedMin: number | null
  reviewedMax: number | null
  sortKey: number
  note: string | null
}

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}

function providerDetail(p: PredictionProviderProbability | undefined, reviewed: boolean): string {
  if (!p) return ''
  const bits = [
    `source time ${p.observed_at ?? '—'}`,
    `kind ${p.observation_kind ?? '—'}${p.timeline_cut ? ` (${p.timeline_cut.replace(/_/g, ' ').toLowerCase()})` : ''}`,
    `bid ${pct(p.best_bid)} · ask ${pct(p.best_ask)} · mid ${pct(p.midpoint)}`,
    `last ${pct(p.last_trade)} · depth ${p.top_depth ?? '—'} · spread ${p.bid_ask_spread == null ? '—' : (p.bid_ask_spread * 100).toFixed(1) + ' pts'}`,
    `mapping ${p.mapping_status} (${p.mapping_authority ?? 'AUTO'})`,
    `semantics ${p.semantics_status} (${p.semantics_authority ?? 'NONE'})`,
    `freshness ${p.freshness_seconds == null ? '—' : Math.round(p.freshness_seconds / 60) + ' min'}${p.latest_in_play ? ' · latest observation is in-play (retained separately)' : ''}`,
    reviewed ? 'REVIEWED EVIDENCE' : 'RESEARCH ONLY — semantics unreviewed',
  ]
  return bits.join('\n')
}

/** Build the per-game comparison rows from board rows (pure; unit-tested). */
export function buildComparisonRows(rows: MarketPulseRow[]): ComparisonRow[] {
  const out: ComparisonRow[] = []
  for (const row of rows) {
    const marks: ComparisonMark[] = []
    if (row.aisp_home_probability != null) {
      marks.push({ source: 'ASP', value: row.aisp_home_probability, reviewed: true, detail: `ASP forecast · fresh ${row.aisp_forecast_fresh ? 'yes' : 'no'}` })
    }
    if (row.sportsbook_home_probability != null) {
      marks.push({ source: 'SPORTSBOOK', value: row.sportsbook_home_probability, reviewed: true, detail: 'Sportsbook no-vig consensus (latest observation)' })
    }
    for (const provider of ['POLYMARKET', 'KALSHI'] as const) {
      const value = provider === 'POLYMARKET' ? row.polymarket_home_probability : row.kalshi_home_probability
      const p = row.providers?.find((x) => x.provider === provider)
      const cov = row.provider_coverage?.find((c) => c.provider === provider)
      // Excluded / in-play / final evidence receives no mark at all.
      if (value == null || !p || (p.observation_kind && p.observation_kind !== 'FINAL_PREGAME' && p.observation_kind !== 'PREGAME')) continue
      if (cov && cov.state !== 'QUOTED_REVIEWED' && cov.state !== 'QUOTED_UNREVIEWED') continue
      const reviewed = p.semantics_status === 'SEMANTICS_MATCHED'
      marks.push({ source: provider, value, reviewed, detail: providerDetail(p, reviewed) })
    }
    const reviewedMarket = marks.filter((m) => m.reviewed && m.source !== 'ASP').map((m) => m.value)
    const reviewedMin = reviewedMarket.length ? Math.min(...reviewedMarket) : null
    const reviewedMax = reviewedMarket.length ? Math.max(...reviewedMarket) : null
    const asp = marks.find((m) => m.source === 'ASP')?.value ?? null
    const disagreement = reviewedMin != null && reviewedMax != null ? reviewedMax - reviewedMin : 0
    const aspGap = asp != null && reviewedMarket.length ? Math.max(...reviewedMarket.map((v) => Math.abs(v - asp))) : 0
    const note = marks.length === 0
      ? (row.display_note ?? 'No probability evidence')
      : marks.some((m) => !m.reviewed) ? 'Hollow marks are research-only (semantics unreviewed)' : null
    out.push({
      game_id: row.game_id, label: `${row.away_abbrev ?? '—'} @ ${row.home_abbrev ?? '—'}`, marks, reviewedMin, reviewedMax,
      sortKey: Math.max(disagreement, aspGap), note,
    })
  }
  return out.sort((a, b) => b.sortKey - a.sortKey)
}

export function MarketProbabilityComparison({ rows }: { rows: MarketPulseRow[] }) {
  const data = useMemo(() => buildComparisonRows(rows), [rows])
  if (data.length === 0) return null
  const labels = data.map((d) => d.label)
  const withMarks = data.filter((d) => d.marks.length > 0).length
  const series: Record<string, unknown>[] = []
  // Range connecting the minimum and maximum REVIEWED market probabilities.
  series.push({
    name: 'Reviewed market range', type: 'custom', z: 1,
    renderItem: (_params: unknown, api: { value: (i: number) => number; coord: (v: [number, number]) => [number, number] }) => {
      const lo = api.value(1); const hi = api.value(2)
      if (Number.isNaN(lo) || Number.isNaN(hi)) return null
      const a = api.coord([lo, api.value(0)]); const b = api.coord([hi, api.value(0)])
      return { type: 'line', shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: chartTheme.grid, lineWidth: 6, opacity: 0.9 } }
    },
    data: data.map((d, i) => [i, d.reviewedMin == null ? NaN : d.reviewedMin * 100, d.reviewedMax == null ? NaN : d.reviewedMax * 100]),
    tooltip: { show: false },
  })
  for (const key of Object.keys(SOURCE_STYLE) as Array<keyof typeof SOURCE_STYLE>) {
    const style = SOURCE_STYLE[key]
    const reviewedPoints: unknown[] = []
    const researchPoints: unknown[] = []
    data.forEach((d, i) => {
      for (const m of d.marks) {
        if (m.source !== key) continue
        const point = { value: [m.value * 100, i], detail: m.detail, name: d.label }
        ;(m.reviewed ? reviewedPoints : researchPoints).push(point)
      }
    })
    series.push({
      name: style.label, type: 'scatter', symbol: style.symbol, symbolSize: 13, z: 3, data: reviewedPoints,
      itemStyle: { color: style.color, borderColor: '#ffffff', borderWidth: 1 },
    })
    series.push({
      name: `${style.label} · research-only`, type: 'scatter', symbol: style.symbol, symbolSize: 13, z: 2, data: researchPoints,
      itemStyle: { color: 'transparent', borderColor: '#8a98a7', borderWidth: 2 },
    })
  }
  const option = {
    grid: { top: 12, left: 96, right: 24, bottom: 56, containLabel: false },
    legend: { bottom: 0, textStyle: { color: chartTheme.muted, fontSize: 11 }, itemWidth: 14 },
    tooltip: {
      trigger: 'item',
      formatter: (p: { seriesName: string; data?: { name?: string; detail?: string; value?: number[] } }) => {
        const d = p.data
        if (!d || !d.value) return ''
        return `<b>${d.name ?? ''}</b> · ${p.seriesName}<br/>${(d.value[0]).toFixed(1)}% home win<br/>${(d.detail ?? '').replace(/\n/g, '<br/>')}`
      },
    },
    xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%', color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    yAxis: { type: 'category', data: labels, inverse: true, axisLabel: { color: chartTheme.text, fontSize: 11 }, axisTick: { show: false } },
    series,
  }
  const height = Math.max(180, 34 * data.length + 80)
  return (
    <Panel title="Market probability comparison" eyebrow="HOME-WIN PROBABILITY · ASP ◆ · SPORTSBOOK ■ · POLYMARKET ● · KALSHI ▲ · SORTED BY DISAGREEMENT" className="section-panel">
      <p className="pulse-health-meta">
        One 0–100% axis per game. Filled marks are reviewed evidence; hollow grey marks are research-only (semantics unreviewed) and are
        labelled as such; in-play, final and no-price observations receive no mark. The grey bar joins the minimum and maximum reviewed market probabilities.
        {' '}{withMarks} of {data.length} games carry at least one mark.
      </p>
      <AnalyticsChart option={option as never} height={height} ariaLabel="Home-win probability comparison across ASP, sportsbook, Polymarket and Kalshi, one axis per game" />
      <TableScroll ariaLabel="Probability comparison table">
        <table className="data-table pulse-compare-table">
          <caption className="visually-hidden">The same comparison as text: home-win probability by source per game, sorted by disagreement</caption>
          <thead><tr><th scope="col">Game</th><th scope="col">ASP</th><th scope="col">Sportsbook</th><th scope="col">Polymarket</th><th scope="col">Kalshi</th><th scope="col">Reviewed range</th><th scope="col">Note</th></tr></thead>
          <tbody>
            {data.map((d) => {
              const cell = (key: keyof typeof SOURCE_STYLE) => {
                const m = d.marks.find((x) => x.source === key)
                if (!m) return <td key={key}>—</td>
                return <td key={key}>{pct(m.value)}{m.reviewed ? '' : <small className="pulse-research-only"> research-only</small>}</td>
              }
              return (
                <tr key={d.game_id}>
                  <th scope="row">{d.label}</th>
                  {cell('ASP')}{cell('SPORTSBOOK')}{cell('POLYMARKET')}{cell('KALSHI')}
                  <td>{d.reviewedMin == null ? '—' : `${pct(d.reviewedMin)} – ${pct(d.reviewedMax)}`}</td>
                  <td><small>{d.note ?? ''}</small></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </TableScroll>
    </Panel>
  )
}

/** Cell states for the coverage matrix (text + glyph; never colour alone). */
export const MATRIX_STATE: Record<string, { glyph: string; label: string; cls: string }> = {
  REVIEWED_PREGAME: { glyph: '●', label: 'Reviewed pregame', cls: 'reviewed' },
  UNREVIEWED: { glyph: '○', label: 'Unreviewed', cls: 'unreviewed' },
  EXCLUDED: { glyph: '✕', label: 'Excluded (in-play / final)', cls: 'excluded' },
  NO_MARKET: { glyph: '—', label: 'No market', cls: 'nomarket' },
  NOT_QUOTED: { glyph: '◌', label: 'Not quoted', cls: 'notquoted' },
  PROVIDER_ERROR: { glyph: '!', label: 'Provider error', cls: 'error' },
}

export function matrixState(row: MarketPulseRow, column: 'SPORTSBOOK' | 'POLYMARKET' | 'KALSHI'): keyof typeof MATRIX_STATE {
  if (column === 'SPORTSBOOK') return row.sportsbook_home_probability == null ? 'NOT_QUOTED' : 'REVIEWED_PREGAME'
  const cov = row.provider_coverage?.find((c) => c.provider === column)
  if (!cov) return 'NO_MARKET'
  switch (cov.state) {
    case 'QUOTED_REVIEWED': return 'REVIEWED_PREGAME'
    case 'QUOTED_UNREVIEWED': return 'UNREVIEWED'
    case 'QUOTED_STRUCTURALLY_EXCLUDED': return 'EXCLUDED'
    case 'MAPPED_NOT_QUOTED': return 'NOT_QUOTED'
    case 'PROVIDER_ERROR': case 'PROVIDER_DISABLED': return 'PROVIDER_ERROR'
    default: return 'NO_MARKET'
  }
}

export function ProviderCoverageMatrix({ rows }: { rows: MarketPulseRow[] }) {
  if (rows.length === 0) return null
  const columns = ['SPORTSBOOK', 'POLYMARKET', 'KALSHI'] as const
  return (
    <Panel title="Provider coverage matrix" eyebrow="ROWS = CANONICAL GAMES · COLUMNS = SPORTSBOOK · POLYMARKET · KALSHI" className="section-panel">
      <TableScroll ariaLabel="Provider coverage matrix">
        <table className="data-table pulse-matrix">
          <caption className="visually-hidden">Coverage state per canonical game and provider</caption>
          <thead><tr><th scope="col">Game</th>{columns.map((c) => <th key={c} scope="col">{c === 'SPORTSBOOK' ? 'Sportsbook' : c === 'POLYMARKET' ? 'Polymarket' : 'Kalshi'}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.game_id}>
                <th scope="row">{row.away_abbrev} @ {row.home_abbrev}</th>
                {columns.map((c) => {
                  const state = matrixState(row, c)
                  const meta = MATRIX_STATE[state]
                  const cov = row.provider_coverage?.find((x) => x.provider === c)
                  return (
                    <td key={c} className={`pulse-matrix__cell pulse-matrix__cell--${meta.cls}`} title={cov?.reason ?? meta.label} data-state={state}>
                      <span aria-hidden="true">{meta.glyph}</span> <span className="pulse-matrix__label">{meta.label}</span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="pulse-health-meta">Legend: ● reviewed pregame · ○ unreviewed (research-only) · ✕ excluded (in-play / final / no usable price) · ◌ listed, not quoted · — no market · ! provider error.</p>
    </Panel>
  )
}

/** Selected-game market movement (no interpolation across missing intervals). */
export function MarketMovementView({ rows, selectedGameId, onSelect, loader }: {
  rows: MarketPulseRow[]
  selectedGameId: string | null
  onSelect: (gameId: string) => void
  loader?: (gameId: string, signal?: AbortSignal) => Promise<MarketPulseGameDetail>
}) {
  const [detail, setDetail] = useState<MarketPulseGameDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = loader ?? ((gameId: string, signal?: AbortSignal) => endpoints.marketPulseGame(gameId, signal))
  useEffect(() => {
    if (!selectedGameId) return
    let active = true
    const controller = new AbortController()
    setDetail(null); setError(null)
    load(selectedGameId, controller.signal).then((d) => active && setDetail(d)).catch((e) => active && setError(String(e?.message ?? e)))
    return () => { active = false; controller.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedGameId])
  if (rows.length === 0) return null
  const timeline = Array.isArray(detail?.timeline) ? detail!.timeline : []
  const times = timeline.map((p) => p.time)
  const markers = Array.isArray(detail?.timeline_markers) ? detail!.timeline_markers! : []
  const seriesOf = (name: string, key: 'aisp' | 'sportsbook' | 'polymarket' | 'kalshi', color: string, symbol: string) => ({
    name, type: 'line', connectNulls: false, symbol, symbolSize: 7, lineStyle: { width: 2 }, itemStyle: { color },
    data: timeline.map((p) => {
      const pregameFlag = key === 'polymarket' ? p.polymarket_pregame : key === 'kalshi' ? p.kalshi_pregame : null
      return { value: p[key], pregame: pregameFlag }
    }),
  })
  const option = {
    grid: { top: 24, left: 52, right: 24, bottom: 56 },
    legend: { bottom: 0, textStyle: { color: chartTheme.muted, fontSize: 11 } },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (v == null ? 'no observation' : `${Number(v).toFixed(1)}%`) },
    xAxis: { type: 'category', data: times, axisLabel: { color: chartTheme.muted, fontSize: 10, formatter: (v: string) => v.slice(11, 16) } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%', color: chartTheme.muted }, splitLine: { lineStyle: { color: chartTheme.grid } } },
    series: [
      seriesOf('ASP', 'aisp', SOURCE_STYLE.ASP.color, 'diamond'),
      seriesOf('Sportsbook (no-vig)', 'sportsbook', SOURCE_STYLE.SPORTSBOOK.color, 'rect'),
      seriesOf('Polymarket', 'polymarket', SOURCE_STYLE.POLYMARKET.color, 'circle'),
      { ...seriesOf('Kalshi', 'kalshi', SOURCE_STYLE.KALSHI.color, 'triangle'),
        markLine: markers.length ? {
          symbol: 'none', label: { formatter: (p: { name: string }) => p.name, color: chartTheme.muted, fontSize: 10 },
          lineStyle: { type: 'dashed', color: chartTheme.red },
          data: markers.map((m) => ({ name: m.label, xAxis: times.find((t) => t >= m.time) ?? times[times.length - 1] })),
        } : undefined },
    ],
  }
  return (
    <Panel title="Selected-game market movement" eyebrow="ASP · SPORTSBOOK NO-VIG · POLYMARKET · KALSHI · DECISION CUTOFF · FIRST PITCH" className="section-panel">
      <label className="pulse-select">
        <span>Game</span>
        <select value={selectedGameId ?? ''} onChange={(e) => onSelect(e.target.value)} aria-label="Select game for market movement">
          {rows.map((r) => <option key={r.game_id} value={r.game_id}>{r.away_abbrev} @ {r.home_abbrev}</option>)}
        </select>
      </label>
      {error && <p className="empty-hint">{error}</p>}
      {!error && selectedGameId && !detail && <p className="empty-hint">Loading observations…</p>}
      {detail && timeline.length === 0 && <p className="empty-hint">No persisted observations for this game yet. Nothing is interpolated.</p>}
      {detail && timeline.length > 0 && (
        <>
          <AnalyticsChart option={option as never} height={300} ariaLabel="Market movement for the selected game: ASP, sportsbook, Polymarket and Kalshi home-win probability over time with decision cutoff and first pitch markers" />
          <p className="pulse-health-meta">
            {timeline.length} observation minutes · gaps are left open (no interpolation) · markers: {markers.map((m) => `${m.label} at ${m.time.slice(11, 16)} UTC`).join(' · ') || 'none'}.
          </p>
        </>
      )}
    </Panel>
  )
}
