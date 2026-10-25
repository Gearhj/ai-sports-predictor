/**
 * Game Center · Game Story — the visual summary under a game's matchup hero.
 *
 *   1. Model vs market      AISP win probability against the no-vig consensus
 *   2. Score population     the verified paired-score heatmap
 *   3. Margin / total       distribution explorer with the quoted line's regions
 *   4. Sportsbook ladder    every stored moneyline, best price, consensus, fair
 *   5. What matters         deterministic facts from the server
 *
 * D3 supplies scales, colour and ticks; React renders the marks so every value
 * is reachable by keyboard and mirrored in a table. Nothing is simulated in the
 * browser. A missing population or market renders a precise unavailable note,
 * never an empty chart.
 */
import * as d3 from 'd3'
import { memo, useMemo, useState, type KeyboardEvent } from 'react'
import { ChartTable, Tooltip, useTooltip, useWidth, type TipState } from '../command/chartKit'
import {
  americanFromProbability, impliedFromAmerican, regionOf, useGameStory,
  type DistributionKind, type GameStory as Story, type Region, type StoryPopulation,
} from '../../services/gameStory'
import { AUTHORITY_TEXT, american, evaluateSameGame, pct, type LegMasses } from '../../services/probability'
import { MyPickCard } from './MyPickCard'
import './story.css'

const REGION_LABEL: Record<Region, string> = { WIN: 'Win', LOSS: 'Loss', PUSH: 'Push' }
const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '')

export function GameStory({ gameId }: { gameId: string | null | undefined }) {
  const story = useGameStory(gameId)
  const data = story.data
  return (
    <section className="gs" aria-label="Game Story">
      <header className="gs__head">
        <h2>Game Story</h2>
        {data?.channel.status === 'SUPPORTED' && (
          <span className="gs__chip">{AUTHORITY_TEXT[data.channel.authority ?? ''] ?? data.channel.authority}
            {' · '}{data.population?.draw_count.toLocaleString()} paired final scores</span>
        )}
      </header>
      {!data && <p className="gs__empty" role="status">{story.error ? `The game story is unavailable: ${story.error}` : 'Loading the game story…'}</p>}
      {data && <MyPickCard story={data} />}
      {data && <StoryBody story={data} />}
    </section>
  )
}

function StoryBody({ story }: { story: Story }) {
  const away = story.game.away?.abbreviation ?? 'Away'
  const home = story.game.home?.abbreviation ?? 'Home'
  return (
    <div className="gs__grid">
      <ModelVsMarket story={story} away={away} home={home} />
      <WhatMatters story={story} />
      <div className="gs__wide">
        {story.population
          ? <ScoreHeatmap population={story.population} away={away} home={home} final={story.game.final} finalNote={story.game.final_note} />
          : <Unavailable title="Score population" reason={story.channel.error?.message ?? 'No verified paired-score population is bound to this game.'} />}
      </div>
      <div className="gs__wide">
        {story.population
          ? <DistributionExplorer story={story} population={story.population} away={away} home={home} />
          : <Unavailable title="Margin and total distribution" reason="Needs a verified paired-score population." />}
      </div>
      <div className="gs__wide">
        <BookLadder story={story} away={away} home={home} />
      </div>
    </div>
  )
}

function Unavailable({ title, reason }: { title: string; reason: string }) {
  return (
    <section className="gs-card gs-card--empty" aria-label={`${title} unavailable`}>
      <h3>{title}</h3>
      <p>Unavailable · {reason}</p>
    </section>
  )
}

/* ------------------------------------------------------------------ 1 --- */

export function ModelVsMarket({ story, away, home }: { story: Story; away: string; home: string }) {
  const mvm = story.model_vs_market
  const { ref, width } = useWidth<HTMLDivElement>(360)
  const modelHome = mvm.model_home_two_way
  const marketHome = mvm.market_home_no_vig
  if (modelHome == null && marketHome == null) {
    return <Unavailable title="Model vs market" reason="Neither a forecast nor a two-sided market is stored for this game." />
  }
  const rows = [
    { side: away, model: modelHome == null ? null : 1 - modelHome, market: marketHome == null ? null : 1 - marketHome },
    { side: home, model: modelHome, market: marketHome },
  ]
  const x = d3.scaleLinear().domain([0, 1]).range([56, Math.max(120, width - 16)])
  const rowH = 44
  return (
    <section className="gs-card" aria-label="Model versus market">
      <h3>Model vs market</h3>
      <p className="gs-sub">
        {modelHome == null ? 'No AISP forecast is bound to this game; the market is shown alone.' : `AISP two-way win probability${mvm.model_source === 'STORED_CONTROLLING_FORECAST' ? ' (stored controlling forecast)' : ''}${marketHome == null ? '.' : ''}`}
        {marketHome == null
          ? ' No two-sided sportsbook market is stored for this game, so there is nothing to compare against.'
          : `${modelHome == null ? ' The' : ' against the'} ${mvm.market_is_current ? 'current' : 'last stored, not current,'} no-vig consensus of ${mvm.market_books} book${mvm.market_books === 1 ? '' : 's'}.`}
      </p>
      <div ref={ref} className="gs-svgwrap">
        <svg width={width} height={rowH * 2 + 26} role="img"
          aria-label={rows.map((r) => `${r.side}: model ${pct(r.model)}, market ${pct(r.market)}`).join('; ')}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t} transform={`translate(${x(t)},0)`}>
              <line y1={4} y2={rowH * 2} className="gs-grid" />
              <text y={rowH * 2 + 16} textAnchor="middle" className="gs-tick">{Math.round(t * 100)}%</text>
            </g>
          ))}
          {rows.map((r, i) => {
            const y = i * rowH + 10
            const diff = r.model != null && r.market != null ? r.model - r.market : null
            return (
              <g key={r.side} transform={`translate(0,${y})`}>
                <text x={0} y={16} className="gs-label">{r.side}</text>
                {r.market != null && <rect x={x(0)} y={4} width={x(r.market) - x(0)} height={16} className="gs-bar gs-bar--market" />}
                {r.model != null && <rect x={x(0)} y={22} width={x(r.model) - x(0)} height={8} className="gs-bar gs-bar--model" />}
                {diff != null && (
                  <line x1={x(r.market!)} x2={x(r.model!)} y1={26} y2={26}
                    className={`gs-diff ${diff >= 0 ? 'is-up' : 'is-down'}`} markerEnd="url(#gs-arrow)" />
                )}
              </g>
            )
          })}
          <defs>
            <marker id="gs-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
            </marker>
          </defs>
        </svg>
      </div>
      <ul className="gs-values">
        {rows.map((r) => (
          <li key={r.side}>
            <b>{r.side}</b> model {pct(r.model)} · market {pct(r.market)}
            {r.model != null && r.market != null && <> · difference <b>{((r.model - r.market) * 100 >= 0 ? '+' : '−') + Math.abs((r.model - r.market) * 100).toFixed(1)} pts</b></>}
          </li>
        ))}
      </ul>
      <p className="gs-legend"><i className="gs-key gs-key--market" /> no-vig market (thick) <i className="gs-key gs-key--model" /> AISP model (thin)
        {mvm.model_tie ? <> · model tie mass {pct(mvm.model_tie)} (excluded from the two-way split)</> : null}</p>
    </section>
  )
}

/* ------------------------------------------------------------------ 2 --- */

export const ScoreHeatmap = memo(function ScoreHeatmap({ population, away, home, final, finalNote }: {
  population: StoryPopulation; away: string; home: string
  final: { home: number; away: number } | null; finalNote: string | null
}) {
  const { ref, width } = useWidth<HTMLDivElement>(640)
  const { tip, show, hide, frame } = useTooltip()
  const byKey = useMemo(() => new Map(population.cells.map((c) => [`${c.away}:${c.home}`, c])), [population])
  const likely = useMemo(() => new Set(population.likely_region.cells.map((c) => `${c.away}:${c.home}`)), [population])
  const awayScores = d3.range(d3.min(population.cells, (c) => c.away) ?? 0, (d3.max(population.cells, (c) => c.away) ?? 0) + 1)
  const homeScores = d3.range(d3.min(population.cells, (c) => c.home) ?? 0, (d3.max(population.cells, (c) => c.home) ?? 0) + 1)
  const [cursor, setCursor] = useState<{ away: number; home: number }>({ away: population.mode.away, home: population.mode.home })
  const pad = { left: 40, bottom: 34, top: 8, right: 8 }
  const plotW = Math.max(120, width - pad.left - pad.right)
  const cell = Math.max(3, Math.min(26, Math.floor(plotW / awayScores.length)))
  const plotH = Math.min(cell * homeScores.length, 420)
  const cellH = plotH / homeScores.length
  const x = d3.scaleBand<number>().domain(awayScores).range([pad.left, pad.left + cell * awayScores.length])
  const y = d3.scaleBand<number>().domain(homeScores).range([pad.top + plotH, pad.top])
  const maxCount = d3.max(population.cells, (c) => c.count) ?? 1
  const color = d3.scaleSequential(d3.interpolateBlues).domain([0, maxCount])
  const tickEvery = (n: number) => Math.max(1, Math.ceil(n / 10))
  const current = byKey.get(`${cursor.away}:${cursor.home}`)
  const describe = (a: number, h: number) => {
    const c = byKey.get(`${a}:${h}`)
    const count = c?.count ?? 0
    return `${away} ${a} – ${home} ${h}: ${count.toLocaleString()} of ${population.draw_count.toLocaleString()} draws (${(count / population.draw_count * 100).toFixed(2)}%)`
  }
  const move = (event: KeyboardEvent<SVGSVGElement>) => {
    const step: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }
    const d = step[event.key]
    if (!d) return
    event.preventDefault()
    const next = {
      away: Math.min(awayScores[awayScores.length - 1], Math.max(awayScores[0], cursor.away + d[0])),
      home: Math.min(homeScores[homeScores.length - 1], Math.max(homeScores[0], cursor.home + d[1])),
    }
    setCursor(next)
  }
  const svgW = pad.left + cell * awayScores.length + pad.right
  return (
    <section className="gs-card" aria-label="Score population heatmap">
      <h3>Score population</h3>
      <p className="gs-sub">Every retained paired final score ({population.draw_count.toLocaleString()} draws). Darker cells occurred more often.
        The outlined cells are the most likely region ({(population.likely_region.count / population.draw_count * 100).toFixed(1)}% of draws); the ringed cell is the single most likely score.</p>
      <div ref={ref} className="gs-svgwrap gs-svgwrap--scroll">
        <div ref={frame} style={{ position: 'relative', width: svgW }}>
          <svg width={svgW} height={pad.top + plotH + pad.bottom} tabIndex={0} role="application"
            aria-label={`Joint score heatmap. Use arrow keys to move between scores. Selected: ${describe(cursor.away, cursor.home)}`}
            onKeyDown={move} onBlur={hide} className="gs-heat">
            {population.cells.map((c) => (
              <rect key={`${c.away}:${c.home}`} x={x(c.away)} y={y(c.home)} width={cell} height={cellH}
                fill={color(c.count)} className={likely.has(`${c.away}:${c.home}`) ? 'gs-heat__likely' : undefined}
                onMouseMove={(e) => { setCursor({ away: c.away, home: c.home }); show(e, describe(c.away, c.home)) }}
                onMouseLeave={hide} />
            ))}
            <circle cx={(x(population.mode.away) ?? 0) + cell / 2} cy={(y(population.mode.home) ?? 0) + cellH / 2}
              r={Math.max(4, Math.min(cell, cellH) / 2 + 2)} className="gs-heat__mode" />
            {final && x(final.away) != null && y(final.home) != null && (
              <g className="gs-heat__final" transform={`translate(${(x(final.away) ?? 0) + cell / 2},${(y(final.home) ?? 0) + cellH / 2})`}>
                <path d={`M-${cell / 2 + 2},-${cellH / 2 + 2} L${cell / 2 + 2},${cellH / 2 + 2} M${cell / 2 + 2},-${cellH / 2 + 2} L-${cell / 2 + 2},${cellH / 2 + 2}`} />
              </g>
            )}
            <rect x={x(cursor.away)} y={y(cursor.home)} width={cell} height={cellH} className="gs-heat__cursor" />
            {awayScores.filter((v, i) => i % tickEvery(awayScores.length) === 0).map((v) => (
              <text key={`ax${v}`} x={(x(v) ?? 0) + cell / 2} y={pad.top + plotH + 14} textAnchor="middle" className="gs-tick">{v}</text>
            ))}
            {homeScores.filter((v, i) => i % tickEvery(homeScores.length) === 0).map((v) => (
              <text key={`hy${v}`} x={pad.left - 6} y={(y(v) ?? 0) + cellH / 2 + 4} textAnchor="end" className="gs-tick">{v}</text>
            ))}
            <text x={pad.left + (cell * awayScores.length) / 2} y={pad.top + plotH + 30} textAnchor="middle" className="gs-axis">{away} score →</text>
            <text x={10} y={pad.top + plotH / 2} textAnchor="middle" className="gs-axis" transform={`rotate(-90 10 ${pad.top + plotH / 2})`}>{home} score →</text>
          </svg>
          <Tooltip tip={tip} width={svgW} />
        </div>
      </div>
      <p className="gs-readout" aria-live="polite">
        <b>Selected:</b> {describe(cursor.away, cursor.home)}{current ? '' : ' (no draw landed here)'}
        {' · '}<b>Most likely:</b> {away} {population.mode.away} – {home} {population.mode.home} ({(population.mode.count / population.draw_count * 100).toFixed(2)}%)
      </p>
      {final && <p className="gs-final"><span aria-hidden="true">✕</span> Final: {away} {final.away} – {home} {final.home}. {finalNote}</p>}
      <ChartTable caption="Most likely final scores">
        <thead><tr><th>{away}</th><th>{home}</th><th>Draws</th><th>Probability</th></tr></thead>
        <tbody>
          {[...population.cells].sort((a, b) => b.count - a.count).slice(0, 25).map((c) => (
            <tr key={`${c.away}:${c.home}`}><td>{c.away}</td><td>{c.home}</td><td>{c.count}</td><td>{(c.count / population.draw_count * 100).toFixed(2)}%</td></tr>
          ))}
        </tbody>
      </ChartTable>
    </section>
  )
})

/* ------------------------------------------------------------------ 3 --- */

export function DistributionExplorer({ story, population, away, home }: {
  story: Story; population: StoryPopulation; away: string; home: string
}) {
  const [kind, setKind] = useState<DistributionKind>(story.lines.total || !story.lines.spread ? 'total' : 'margin')
  const quoted = kind === 'total' ? story.lines.total : story.lines.spread
  const [custom, setCustom] = useState<string>('')
  const [customMasses, setCustomMasses] = useState<{ line: number; sides: Record<string, LegMasses> } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { ref, width } = useWidth<HTMLDivElement>(640)
  const { tip, show, hide, frame } = useTooltip()
  // One tab stop for the whole chart; arrow keys move this index.
  const [focusIdx, setFocusIdx] = useState<number | null>(null)
  const values = kind === 'total' ? population.total : population.margin
  const binding = story.channel.binding
  const active = customMasses ?? (quoted ? { line: quoted.line, sides: quoted.sides } : null)
  const side = kind === 'total' ? 'OVER' : 'HOME'
  const other = kind === 'total' ? 'UNDER' : 'AWAY'

  const switchKind = (next: DistributionKind) => { setKind(next); setCustomMasses(null); setCustom(''); setError(null); setFocusIdx(null) }
  async function evaluateCustom() {
    const line = Number(custom.trim())
    if (!binding || custom.trim() === '') return
    if (!Number.isFinite(line)) {
      setError(kind === 'total' ? 'Enter a number, such as 8.5 or 44.' : 'Enter a number, such as -1.5 or 3.')
      return
    }
    setBusy(true); setError(null)
    try {
      const legs = kind === 'total'
        ? [{ market: 'TOTAL' as const, selection: 'OVER' as const, line }, { market: 'TOTAL' as const, selection: 'UNDER' as const, line }]
        : [{ market: 'SPREAD' as const, selection: 'HOME' as const, line }, { market: 'SPREAD' as const, selection: 'AWAY' as const, line: -line }]
      const [a, b] = await Promise.all(legs.map((leg) => evaluateSameGame(binding, [leg])))
      const masses = (r: typeof a) => ({
        ...r.contract.probabilities, conditional: null, mcse: r.contract.winProbabilityMonteCarloStandardError,
        fair_decimal: null, fair_american: r.fair_american,
      }) as LegMasses
      setCustomMasses({ line, sides: { [legs[0].selection]: masses(a), [legs[1].selection]: masses(b) } })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Evaluation failed')
    } finally {
      setBusy(false)
    }
  }

  const pad = { left: 44, right: 10, top: 10, bottom: 36 }
  const describeValue = (value: number, count: number) => {
    const region = regionFor(value)
    return `${kind === 'total' ? 'Total' : 'Margin'} ${value}: ${count} draws (${(count / population.draw_count * 100).toFixed(2)}%)${region ? ` · ${REGION_LABEL[region]}` : ''}`
  }
  const x = d3.scaleBand<number>().domain(values.map((v) => v.value)).range([pad.left, Math.max(pad.left + 60, width - pad.right)]).padding(0.08)
  const maxCount = d3.max(values, (v) => v.count) ?? 1
  const h = 200
  const y = d3.scaleLinear().domain([0, maxCount / population.draw_count]).nice().range([pad.top + h, pad.top])
  const every = Math.max(1, Math.ceil(values.length / 14))
  const regionFor = (value: number): Region | null => (active ? regionOf(kind, side, active.line, value) : null)
  const peakIndex = values.reduce((best, v, i) => (v.count > (values[best]?.count ?? -1) ? i : best), 0)
  const focused = focusIdx != null ? values[focusIdx] ?? null : null
  const keyboardTip: TipState | null = focused ? {
    x: (x(focused.value) ?? 0) + x.bandwidth() / 2,
    y: y(focused.count / population.draw_count),
    content: describeValue(focused.value, focused.count),
  } : null
  const lineLabel = (line: number) => (kind === 'total' ? `Over/Under ${line}` : `${home} ${line > 0 ? '+' : ''}${line} / ${away} ${-line > 0 ? '+' : ''}${-line}`)
  // Where the line falls on the axis (between bands for half-point lines).
  const boundary = active ? (kind === 'total' ? active.line : -active.line) : null
  const bx = (() => {
    if (boundary == null) return null
    const lower = values.filter((v) => v.value <= boundary).at(-1)
    if (!lower) return pad.left
    const pos = (x(lower.value) ?? 0) + x.bandwidth()
    return lower.value === boundary ? pos - x.bandwidth() / 2 : pos + (x.step() - x.bandwidth()) / 2
  })()

  return (
    <section className="gs-card" aria-label="Margin and total distribution">
      <div className="gs-card__head">
        <h3>{kind === 'total' ? 'Total score' : `Margin (${home} minus ${away})`} distribution</h3>
        <div className="gs-toggle" role="group" aria-label="Distribution">
          <button type="button" aria-pressed={kind === 'margin'} onClick={() => switchKind('margin')}>Margin</button>
          <button type="button" aria-pressed={kind === 'total'} onClick={() => switchKind('total')}>Total</button>
        </div>
      </div>
      <p className="gs-sub">
        {active
          ? <>Regions for <b>{kind === 'total' ? 'Over' : home} {kind === 'total' ? active.line : `${active.line > 0 ? '+' : ''}${active.line}`}</b>
            {customMasses ? ' (your line, evaluated by the server)' : story.lines.from === 'CURRENT_QUOTES' ? ' (consensus of current quotes)' : ' (consensus of stored, not current, quotes)'}.
            {' '}Win bars are solid, loss bars are hatched and a push bar is outlined.</>
          : <>No {kind === 'total' ? 'total' : 'spread'} line is quoted for this game. Enter a line to see its regions.</>}
      </p>
      <div ref={ref} className="gs-svgwrap">
        <div ref={frame} style={{ position: 'relative' }}>
          <svg width={width} height={pad.top + h + pad.bottom} tabIndex={0} role="application"
            aria-label={`${kind === 'total' ? 'Total score' : 'Margin'} distribution${active ? `, ${lineLabel(active.line)}: ${side.toLowerCase()} wins ${pct(active.sides[side]?.win)}, loses ${pct(active.sides[side]?.loss)}, pushes ${pct(active.sides[side]?.push)}` : ''}. Use the left and right arrow keys to move between values.`}
            aria-describedby={`gs-dist-live-${kind}`}
            onFocus={() => setFocusIdx((i) => i ?? peakIndex)}
            onBlur={() => { setFocusIdx(null); hide() }}
            onKeyDown={(e) => {
              const last = values.length - 1
              const next = e.key === 'ArrowRight' ? Math.min(last, (focusIdx ?? peakIndex) + 1)
                : e.key === 'ArrowLeft' ? Math.max(0, (focusIdx ?? peakIndex) - 1)
                  : e.key === 'Home' ? 0 : e.key === 'End' ? last : null
              if (next == null) return
              e.preventDefault()
              setFocusIdx(next)
            }}
            className="gs-dist-chart">
            <defs>
              <pattern id="gs-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" className="gs-hatch__bg" /><line x1="0" y1="0" x2="0" y2="5" className="gs-hatch__line" />
              </pattern>
            </defs>
            {y.ticks(4).map((t) => (
              <g key={t} transform={`translate(0,${y(t)})`}>
                <line x1={pad.left} x2={width - pad.right} className="gs-grid" />
                <text x={pad.left - 6} y={4} textAnchor="end" className="gs-tick">{(t * 100).toFixed(0)}%</text>
              </g>
            ))}
            {values.map((v) => {
              const region = regionFor(v.value)
              const p = v.count / population.draw_count
              return (
                <rect key={v.value} x={x(v.value)} y={y(p)} width={x.bandwidth()} height={pad.top + h - y(p)}
                  className={`gs-dist gs-dist--${(region ?? 'NONE').toLowerCase()}${values[focusIdx ?? -1]?.value === v.value ? ' is-focused' : ''}`}
                  fill={region === 'LOSS' ? 'url(#gs-hatch)' : undefined} aria-hidden="true"
                  onMouseMove={(e) => show(e, describeValue(v.value, v.count))}
                  onMouseLeave={hide} />
              )
            })}
            {bx != null && <line x1={bx} x2={bx} y1={pad.top} y2={pad.top + h} className="gs-line" />}
            {values.filter((_, i) => i % every === 0).map((v) => (
              <text key={v.value} x={(x(v.value) ?? 0) + x.bandwidth() / 2} y={pad.top + h + 14} textAnchor="middle" className="gs-tick">{v.value}</text>
            ))}
            <text x={pad.left + (width - pad.left) / 2} y={pad.top + h + 30} textAnchor="middle" className="gs-axis">
              {kind === 'total' ? `${away} + ${home} score` : `${home} score − ${away} score`}
            </text>
          </svg>
          <Tooltip tip={focusIdx != null ? keyboardTip : tip} width={width} />
        </div>
      </div>
      <p id={`gs-dist-live-${kind}`} className="cc-sr" aria-live="polite">
        {focusIdx != null && values[focusIdx] ? describeValue(values[focusIdx].value, values[focusIdx].count) : ''}
      </p>
      {active && (
        <dl className="gs-masses" aria-label="Server settlement masses">
          {[side, other].map((s) => (
            <div key={s}>
              <dt>{s === 'HOME' ? `${home} ${active.line > 0 ? '+' : ''}${active.line}` : s === 'AWAY' ? `${away} ${-active.line > 0 ? '+' : ''}${-active.line}` : `${s === 'OVER' ? 'Over' : 'Under'} ${active.line}`}</dt>
              <dd>Win {pct(active.sides[s]?.win)} · Loss {pct(active.sides[s]?.loss)} · Push {pct(active.sides[s]?.push)}</dd>
            </div>
          ))}
        </dl>
      )}
      {binding && (
        <form className="gs-lineform" onSubmit={(e) => { e.preventDefault(); void evaluateCustom() }}>
          <label>{kind === 'total' ? 'Try a total line' : `Try a ${home} line`}
            <input inputMode="decimal" value={custom} onChange={(e) => { setCustom(e.target.value); setError(null) }}
              placeholder={active ? `e.g. ${active.line}` : kind === 'total' ? 'e.g. 8.5' : 'e.g. -1.5'} />
          </label>
          <button type="submit" disabled={busy || custom.trim() === ''}>{busy ? 'Evaluating…' : 'Show regions'}</button>
          {customMasses && <button type="button" onClick={() => { setCustomMasses(null); setCustom('') }}>Back to quoted line</button>}
          {error && <span className="gs-error" role="alert">{error}</span>}
        </form>
      )}
      <ChartTable caption={`${kind} distribution`}>
        <thead><tr><th>{kind === 'total' ? 'Total' : 'Margin'}</th><th>Draws</th><th>Probability</th><th>Region</th></tr></thead>
        <tbody>{values.map((v) => {
          const region = regionFor(v.value)
          return <tr key={v.value}><td>{v.value}</td><td>{v.count}</td><td>{(v.count / population.draw_count * 100).toFixed(2)}%</td><td>{region ? REGION_LABEL[region] : '—'}</td></tr>
        })}</tbody>
      </ChartTable>
    </section>
  )
}

/* ------------------------------------------------------------------ 4 --- */

interface LadderPoint { side: 'AWAY' | 'HOME'; book: string; american: number; implied: number; current: boolean; observedAt: string }

export function BookLadder({ story, away, home }: { story: Story; away: string; home: string }) {
  const { ref, width } = useWidth<HTMLDivElement>(640)
  const { tip, show, hide, frame } = useTooltip()
  const quotes = (story.market?.quotes ?? []).filter((q) => q.market === 'MONEYLINE')
  if (story.game.sport === 'NBA') {
    return <Unavailable title="Sportsbook comparison" reason="Research sport: AISP collects no governed sportsbook price for this game." />
  }
  if (story.game.sport === 'CFB') {
    // Stored CFB quotes (and whether any collector is active) are stated on the
    // College Football game page with their clocks; this page does not restate them.
    return <Unavailable title="Sportsbook comparison" reason="Research sport: College Football sportsbook quotes, when stored, are shown with their clocks on the College Football game page." />
  }
  if (quotes.length === 0) {
    return <Unavailable title="Sportsbook comparison" reason="No moneyline quote is stored for this game." />
  }
  const points: LadderPoint[] = []
  for (const q of quotes) {
    for (const side of ['AWAY', 'HOME'] as const) {
      const a = q.sides[side]?.american
      if (a == null) continue
      points.push({ side, book: shortBook(q.sportsbook), american: a, implied: impliedFromAmerican(a), current: q.state === 'CURRENT', observedAt: q.observed_at })
    }
  }
  const consensusHome = story.market?.consensus_no_vig_home ?? null
  const modelHome = story.model_vs_market.model_home_two_way
  const lanes = [
    { side: 'AWAY' as const, label: away, consensus: consensusHome == null ? null : 1 - consensusHome, fair: modelHome == null ? null : 1 - modelHome },
    { side: 'HOME' as const, label: home, consensus: consensusHome, fair: modelHome },
  ]
  const extent = d3.extent([...points.map((p) => p.implied), ...lanes.flatMap((l) => [l.consensus, l.fair]).filter((v): v is number => v != null)]) as [number, number]
  const x = d3.scaleLinear().domain([Math.max(0, extent[0] - 0.03), Math.min(1, extent[1] + 0.03)]).range([56, Math.max(140, width - 16)])
  const laneH = 56
  const best = (side: 'AWAY' | 'HOME') => {
    const current = points.filter((p) => p.side === side && p.current)
    return current.length ? current.reduce((a, b) => (b.implied < a.implied ? b : a)) : null
  }
  return (
    <section className="gs-card" aria-label="Sportsbook comparison">
      <h3>Sportsbook comparison · moneyline</h3>
      <p className="gs-sub">Each dot is one book&rsquo;s stored price as an implied probability (further left pays more). Filled dots are current; hollow dots are stored, not current.
        The diamond is the best current price, the bar is the no-vig consensus and the triangle is AISP&rsquo;s fair price.</p>
      <div ref={ref} className="gs-svgwrap">
        <div ref={frame} style={{ position: 'relative' }}>
          <svg width={width} height={laneH * 2 + 28} role="img" aria-label="Moneyline prices by sportsbook">
            {x.ticks(5).map((t) => (
              <g key={t} transform={`translate(${x(t)},0)`}>
                <line y1={4} y2={laneH * 2} className="gs-grid" />
                <text y={laneH * 2 + 16} textAnchor="middle" className="gs-tick">{american(americanFromProbability(t))}</text>
              </g>
            ))}
            {lanes.map((lane, i) => {
              const cy = i * laneH + laneH / 2
              const top = best(lane.side)
              return (
                <g key={lane.side}>
                  <text x={0} y={cy + 4} className="gs-label">{lane.label}</text>
                  {lane.consensus != null && <line x1={x(lane.consensus)} x2={x(lane.consensus)} y1={cy - 14} y2={cy + 14} className="gs-consensus" />}
                  {lane.fair != null && <path d={d3.symbol(d3.symbolTriangle, 70)() ?? ''} transform={`translate(${x(lane.fair)},${cy - 16})`} className="gs-fair" />}
                  {points.filter((p) => p.side === lane.side).map((p, j) => (
                    <circle key={`${p.book}${j}`} cx={x(p.implied)} cy={cy + ((j % 3) - 1) * 5} r={5} tabIndex={0}
                      className={`gs-dot ${p.current ? 'is-current' : 'is-stale'}`}
                      aria-label={`${lane.label} ${american(p.american)} at ${p.book}, ${p.current ? 'current' : 'not current'}`}
                      onMouseMove={(e) => show(e, `${p.book}: ${lane.label} ${american(p.american)} (${p.current ? 'current' : 'not current'})`)}
                      onFocus={(e) => show(e, `${p.book}: ${lane.label} ${american(p.american)} (${p.current ? 'current' : 'not current'})`)}
                      onMouseLeave={hide} onBlur={hide} />
                  ))}
                  {top && <path d={d3.symbol(d3.symbolDiamond, 110)() ?? ''} transform={`translate(${x(top.implied)},${cy + 16})`} className="gs-best" />}
                </g>
              )
            })}
          </svg>
          <Tooltip tip={tip} width={width} />
        </div>
      </div>
      <ul className="gs-values">
        {lanes.map((lane) => {
          const top = best(lane.side)
          return (
            <li key={lane.side}><b>{lane.label}</b> best current {top ? `${american(top.american)} (${top.book})` : 'none'} · consensus {lane.consensus == null ? '—' : `${pct(lane.consensus)} (${american(americanFromProbability(lane.consensus))})`} · AISP fair {lane.fair == null ? '—' : american(americanFromProbability(lane.fair))}</li>
          )
        })}
      </ul>
      <details className="cc-table">
        <summary>Table view · every stored moneyline</summary>
        <div className="cc-table__scroll">
          <table>
            <thead><tr><th>Book</th><th>{away}</th><th>{home}</th><th>Observed</th><th>State</th></tr></thead>
            <tbody>{quotes.map((q, i) => (
              <tr key={`${q.sportsbook_key}${i}`}><td>{shortBook(q.sportsbook)}</td><td>{american(q.sides.AWAY?.american)}</td><td>{american(q.sides.HOME?.american)}</td>
                <td>{new Date(q.observed_at).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET</td>
                <td>{q.state === 'CURRENT' ? 'Current' : 'Stored reference only'}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

/* ------------------------------------------------------------------ 5 --- */

export function WhatMatters({ story }: { story: Story }) {
  const order = { DISAGREEMENT: 0, FRESHNESS: 1, MISSING: 2 } as const
  const facts = [...story.facts].sort((a, b) => order[a.kind] - order[b.kind])
  const title = { DISAGREEMENT: 'Disagreement', FRESHNESS: 'Freshness', MISSING: 'Missing' } as const
  return (
    <section className="gs-card" aria-label="What matters">
      <h3>What matters</h3>
      {facts.length === 0
        ? <p className="gs-sub">No deterministic facts are available for this game yet.</p>
        : <ul className="gs-facts">{facts.map((f, i) => <li key={i}><b>{title[f.kind]}</b><span>{f.text}</span></li>)}</ul>}
      <p className="gs-sub">Stated from stored numbers only; no cause is implied.</p>
    </section>
  )
}
