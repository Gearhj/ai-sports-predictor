/** Compare precomputed BASE and pace distributions, never run a browser model. */
import { useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { NflDriveScenarioGroup, NflDriveSupplement, NflScenarioSupplement } from '../../types/nfl'
import { pct } from './OutcomeExplorer'
import { driveClock, recordedDriveGeometry, RESULT_LABEL } from './DriveTheater'
import { FootballField } from './FootballField'
import './nfltheater.css'

export function isDriveScenarioGroup(value: NflScenarioSupplement | NflDriveScenarioGroup): value is NflDriveScenarioGroup {
  return 'baseline' in value && Array.isArray(value.scenarios)
}

const signed = (value: number, suffix = '') => `${value > 0 ? '+' : ''}${value.toFixed(2)}${suffix}`

function ScenarioCard({ supplement, awayTeam, homeTeam, pace }: { supplement: NflDriveSupplement; awayTeam: string; homeTeam: string; pace: boolean }) {
  const e = supplement.explorer
  return <article className={`nfl-scenario-card${pace ? ' nfl-scenario-card--pace' : ''}`}>
    <span className="nfl-kicker">{pace ? 'PRECOMPUTED SENSITIVITY' : 'FITTED DRIVE ENGINE'}</span>
    <h3>{pace ? `Pace · ${supplement.scenario?.paceDurationMultiplier ?? '—'}× drive duration` : 'BASE'}</h3>
    <div className="nfl-scenario-score"><span>{awayTeam} expected<strong>{e?.expected.away.toFixed(1) ?? '—'}</strong></span><span>{homeTeam} expected<strong>{e?.expected.home.toFixed(1) ?? '—'}</strong></span></div>
    <dl className="nfl-scenario-metrics"><div><dt>{homeTeam} win</dt><dd>{pct(e?.outcomes.home)}</dd></div><div><dt>{awayTeam} win</dt><dd>{pct(e?.outcomes.away)}</dd></div><div><dt>Tie</dt><dd>{pct(e?.outcomes.tie)}</dd></div><div><dt>{homeTeam} margin</dt><dd>{e ? signed(e.expected.margin_home) : '—'}</dd></div><div><dt>Total</dt><dd>{e?.expected.total.toFixed(2) ?? '—'}</dd></div><div><dt>Stored games</dt><dd>{supplement.simulation_count?.toLocaleString() ?? '—'}</dd></div></dl>
  </article>
}

export function ScenarioComparison({ supplements, requirement, homeTeam, awayTeam, m1Label }: {
  supplements: Array<NflScenarioSupplement | NflDriveScenarioGroup>; requirement: string | null; homeTeam: string; awayTeam: string; m1Label: string
}) {
  const group = supplements.find(isDriveScenarioGroup)
  const base = group?.baseline
  const pace = group?.scenarios[0]
  const [representativeLabel, setRepresentativeLabel] = useState('Typical')
  const [driveIndex, setDriveIndex] = useState(0)
  if (!base?.explorer || !pace?.explorer) return <section className="theater-empty" aria-label="Scenario Comparison"><h3>Scenario Comparison — awaiting a verified pair</h3><p>{requirement ?? 'A matching BASE and pace distribution has not been imported for this game.'}</p><p>Quarterback, injury, weather and tactical changes are unavailable: no delivered artifact supports those controls.</p></section>
  const baseline = base.explorer
  const alternative = pace.explorer
  const paths = base.paths ?? []
  const labels = paths.filter((g) => pace.paths?.some((p) => p.label === g.label)).map((g) => g.label)
  const selectedLabel = labels.includes(representativeLabel) ? representativeLabel : labels[0]
  const baseGame = paths.find((p) => p.label === selectedLabel)
  const paceGame = pace.paths?.find((p) => p.label === selectedLabel)
  const maxIndex = Math.max(baseGame?.drives.length ?? 0, paceGame?.drives.length ?? 0) - 1
  const selectedIndex = Math.min(driveIndex, Math.max(0, maxIndex))
  const chart = (kind: 'margin' | 'total') => {
    const points = (s: NflDriveSupplement) => kind === 'margin' ? s.explorer!.margin_distribution.map((p) => [p.margin, p.probability]) : s.explorer!.total_distribution.map((p) => [p.total, p.probability])
    return {
      animation: false,
      color: ['#356f9b', '#20a07e'],
      legend: { data: ['BASE', 'Pace'], bottom: 0, textStyle: { color: '#567180' } },
      grid: { left: 45, right: 14, top: 16, bottom: 52 },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => pct(v, 3) },
      xAxis: { type: 'value', name: kind === 'margin' ? `${homeTeam} − ${awayTeam}` : 'Total points', nameLocation: 'middle', nameGap: 23, axisLabel: { color: '#6f8390' } },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => pct(v, 0), color: '#6f8390' } },
      series: [{ name: 'BASE', type: 'line', showSymbol: false, data: points(base), areaStyle: { opacity: .1 } }, { name: 'Pace', type: 'line', showSymbol: false, data: points(pace), areaStyle: { opacity: .08 } }],
    }
  }
  return <section className="nfl-scenarios" aria-label="Scenario Comparison">
    <span className="nfl-kicker">ONE ENGINE · TWO RECORDED ASSUMPTIONS</span><h2>What changes when drives take less time?</h2>
    <p className="nfl-scenarios__intro">BASE and pace are separate, persisted outputs from the independent drive engine. Pace scales drive duration by {pace.scenario?.paceDurationMultiplier ?? '—'}; this is an assumed sensitivity test, not a causal coaching claim or a change to {m1Label}. Quarterback, injury, weather and tactical controls are unavailable because the delivered artifacts do not support them.</p>
    <div className="nfl-scenario-grid"><ScenarioCard supplement={base} homeTeam={homeTeam} awayTeam={awayTeam} pace={false} /><ScenarioCard supplement={pace} homeTeam={homeTeam} awayTeam={awayTeam} pace /></div>
    <div className="nfl-scenario-deltas" aria-label="Pace minus BASE"><span>PACE MINUS BASE<strong>Distribution shift</strong></span><span>{homeTeam} win<strong>{signed((alternative.outcomes.home - baseline.outcomes.home) * 100, ' pp')}</strong></span><span>Expected margin<strong>{signed(alternative.expected.margin_home - baseline.expected.margin_home)}</strong></span><span>Expected total<strong>{signed(alternative.expected.total - baseline.expected.total)}</strong></span><span>Final tie<strong>{signed((alternative.outcomes.tie - baseline.outcomes.tie) * 100, ' pp')}</strong></span></div>
    <div className="nfl-distribution-shifts"><article><h3>Margin distribution</h3><ReactECharts option={chart('margin')} style={{ height: 250 }} opts={{ renderer: 'svg' }} notMerge /></article><article><h3>Total distribution</h3><ReactECharts option={chart('total')} style={{ height: 250 }} opts={{ renderer: 'svg' }} notMerge /></article></div>
    {baseGame && paceGame && <section className="nfl-scenario-sync" aria-label="Representative drive comparison"><h3>Two representative games, one drive control</h3><label>Supplied category<select aria-label="Comparison representative" value={selectedLabel} onChange={(e) => { setRepresentativeLabel(e.target.value); setDriveIndex(0) }}>{labels.map((label) => <option key={label}>{label}</option>)}</select><span>Drive {selectedIndex + 1}</span></label><input type="range" min="0" max={maxIndex} value={selectedIndex} onChange={(e) => setDriveIndex(Number(e.target.value))} aria-label="Comparison drive" /><div className="nfl-scenario-grid">{[{ game: baseGame, label: 'BASE' }, { game: paceGame, label: 'Pace' }].map(({ game, label }) => {
      const ended = selectedIndex >= game.drives.length
      const d = game.drives[Math.min(selectedIndex, game.drives.length - 1)]
      return <FootballField key={label} awayTeam={awayTeam} homeTeam={homeTeam} awayScore={d.awayScoreAfter} homeScore={d.homeScoreAfter} caption={`${label} · ${game.label}${ended ? ' · Final' : ` · Drive ${d.driveNumber}`}`} {...driveClock(d)} possession={d.possession} drive={{ ...recordedDriveGeometry(d), tone: d.possession === 'HOME' ? 'home' : 'away', result: RESULT_LABEL[d.outcome] ?? d.outcome }}><span>{game.drives.length} total drives · final {awayTeam} {game.awayScore}–{homeTeam} {game.homeScore}{game.reachedOvertime ? ' · OT' : ''}</span></FootballField>
    })}</div><p className="muted">Aligned by drive index, not by play or matched random outcome. These supplied category representatives may be different simulation IDs ({baseGame.simulationIndex} and {paceGame.simulationIndex}); their individual difference is descriptive, not an effect estimate.</p></section>}
    <details className="nfl-lab-evidence"><summary>Comparison evidence</summary><p>{group?.engine_id}. Both distributions remain raw research-shadow evidence with no wagering authority. BASE artifact: {base.supplement_sha256}; pace artifact: {pace.supplement_sha256}.</p></details>
  </section>
}
