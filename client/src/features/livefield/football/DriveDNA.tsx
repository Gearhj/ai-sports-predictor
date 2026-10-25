/**
 * DRIVE DNA: each offense's fingerprint this game, six measures against the NFL 2025 regular-season norm
 * (computed from nflverse): pass rate, run rate, explosive rate (15+), success rate (EPA > 0), average
 * depth of target (air yards), third-down conversion. Both offenses sit on one radar (the selected one bold)
 * beside a bar table, so the two styles and the league norm read at a glance.
 */
import { useMemo, useState } from 'react'
import type { EpaPlay } from './ep'
import { parseFbPlay, visibleColor, type FbPlay, type FbTeam } from './footballData'
import type { YardsTables } from './situation'

const SCRIM = new Set(['pass', 'incomplete', 'interception', 'sack', 'rush'])
const FALLBACK = { pass: 0.601, run: 0.399, explosive: 0.104, success: 0.44, third: 0.395, depth: 7.77, ypp: 5.43 }
const MAX = { pass: 0.85, run: 0.85, explosive: 0.22, success: 0.72, depth: 14, third: 0.75 }
const AXES = [
  { key: 'pass', label: 'Pass rate', fmt: (v: number) => `${Math.round(v * 100)}%` },
  { key: 'run', label: 'Run rate', fmt: (v: number) => `${Math.round(v * 100)}%` },
  { key: 'explosive', label: 'Explosive (15+)', fmt: (v: number) => `${Math.round(v * 100)}%` },
  { key: 'success', label: 'Success rate', fmt: (v: number) => `${Math.round(v * 100)}%` },
  { key: 'depth', label: 'Avg pass depth', fmt: (v: number) => `${v.toFixed(1)} yds` },
  { key: 'third', label: '3rd-down conv.', fmt: (v: number) => `${Math.round(v * 100)}%` },
] as const
type Key = (typeof AXES)[number]['key']

export function fingerprint(epa: EpaPlay[], plays: readonly FbPlay[], teamId: string): { n: number; vals: Record<Key, number> } {
  const mine = epa.filter((e) => e.team === teamId && SCRIM.has(e.kind))
  const n = mine.length
  const thirds = plays.filter((p) => (p.offense ?? p.start.team) === teamId && p.start.down === 3 && SCRIM.has(parseFbPlay(p).kind) && p.start.distance != null)
  const made = thirds.filter((p) => (p.yards ?? 0) >= (p.start.distance ?? 99) || parseFbPlay(p).touchdown).length
  const depths: number[] = []
  for (const p of plays) {
    if ((p.offense ?? p.start.team) !== teamId) continue
    const k = parseFbPlay(p)
    if (k.kind === 'pass') depths.push(Math.max(0, (p.yards ?? 0) - (p.yac ?? Math.round(Math.max(0, p.yards ?? 0) * 0.25))))
    else if ((k.kind === 'incomplete' || k.kind === 'interception') && k.depth) depths.push(k.depth === 'deep' ? 22 : 6)
  }
  const passes = mine.filter((e) => e.kind !== 'rush').length
  return { n, vals: { pass: n ? passes / n : 0, run: n ? (n - passes) / n : 0, explosive: n ? mine.filter((e) => e.yards >= 15).length / n : 0, success: n ? mine.filter((e) => e.epa > 0).length / n : 0, depth: depths.length ? depths.reduce((s, v) => s + v, 0) / depths.length : 0, third: thirds.length ? made / thirds.length : 0 } }
}

interface Props { plays: readonly FbPlay[]; epa: EpaPlay[]; away: FbTeam; home: FbTeam; defaultTeamId: string | null; tables: YardsTables | null }

export function DriveDNA({ plays, epa, away, home, defaultTeamId, tables }: Props) {
  const [pick, setPick] = useState<string | null>(null)
  const teamId = pick ?? defaultTeamId ?? away.id
  const team = teamId === home.id ? home : away
  const other = teamId === home.id ? away : home
  const color = visibleColor(team, '#3fd8c4')
  const otherColor = visibleColor(other, '#ffb547')
  const league = tables?.league2025 ?? FALLBACK
  const fp = useMemo(() => fingerprint(epa, plays, teamId), [epa, plays, teamId])
  const fo = useMemo(() => fingerprint(epa, plays, other.id), [epa, plays, other.id])
  const W = 500; const H = 330; const cx = W / 2; const cy = H / 2 + 4; const R = 108
  const ang = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / AXES.length
  const pt = (i: number, f: number) => [cx + Math.cos(ang(i)) * R * Math.max(0.04, Math.min(1, f)), cy + Math.sin(ang(i)) * R * Math.max(0.04, Math.min(1, f))]
  const poly = (vals: number[]) => vals.map((f, i) => pt(i, f).map((v) => v.toFixed(1)).join(',')).join(' ')
  const shape = (v: Record<Key, number>) => AXES.map((a) => v[a.key] / MAX[a.key])
  const base = AXES.map((a) => league[a.key] / MAX[a.key])
  return (
    <section className="lf-card dna" aria-label="Drive DNA">
      <header className="am__head">
        <h3>DRIVE DNA <small className="lf-muted">each offense vs the NFL norm</small></h3>
        <span className="fbd-tabs">{[away, home].map((t) => <button key={t.id} type="button" className={t.id === teamId ? 'is-on' : ''} style={{ ['--tc' as string]: visibleColor(t, '#3fd8c4') }} onClick={() => setPick(t.id)}>{t.abbr} offense</button>)}</span>
      </header>
      <div className="dna__body">
        <svg viewBox={`0 0 ${W} ${H}`} className="dna__svg" role="img" aria-label={`${team.abbr} and ${other.abbr} offense fingerprints versus the league`}>
          {[0.25, 0.5, 0.75, 1].map((r) => <polygon key={r} points={poly(AXES.map(() => r))} fill={r === 1 ? 'rgba(255,255,255,.03)' : 'none'} stroke="rgba(255,255,255,.12)" />)}
          {AXES.map((a, i) => { const [x, y] = pt(i, 1); const [lx, ly] = pt(i, 1.24); return (<g key={a.key}><line x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,.12)" /><text x={lx} y={ly} textAnchor={Math.abs(lx - cx) < 8 ? 'middle' : lx > cx ? 'start' : 'end'} dominantBaseline="middle" className="dna__axis">{a.label}</text></g>) })}
          <polygon points={poly(base)} fill="none" stroke="rgba(255,255,255,.6)" strokeDasharray="6 5" strokeWidth="1.6" />
          {fo.n ? <polygon points={poly(shape(fo.vals))} fill={otherColor} fillOpacity=".1" stroke={otherColor} strokeOpacity=".6" strokeWidth="1.8" /> : null}
          <polygon points={poly(shape(fp.vals))} fill={color} fillOpacity=".3" stroke={color} strokeWidth="3" className="fbd-dna__poly" />
          {shape(fp.vals).map((f, i) => { const [x, y] = pt(i, f); return <circle key={i} cx={x} cy={y} r="4.5" fill={color} stroke="#fff" strokeWidth="1.5" /> })}
        </svg>
        <table className="dna__table">
          <thead><tr><th>Measure</th><th style={{ color }}>{team.abbr}</th><th style={{ color: otherColor }}>{other.abbr}</th><th>NFL</th></tr></thead>
          <tbody>
            {AXES.map((a) => {
              const v = fp.vals[a.key]; const lv = league[a.key]; const o = fo.vals[a.key]
              const better = a.key === 'run' || a.key === 'pass' ? null : v >= lv
              return (
                <tr key={a.key}>
                  <td>{a.label}</td>
                  <td><span className="dna__cell"><b className={better == null ? '' : better ? 'is-up' : 'is-down'}>{fp.n ? a.fmt(v) : '—'}</b><i className="dna__bar"><u style={{ width: `${Math.min(100, (v / MAX[a.key]) * 100)}%`, background: color }} /><em style={{ left: `${Math.min(100, (lv / MAX[a.key]) * 100)}%` }} /></i></span></td>
                  <td className="dna__o">{fo.n ? a.fmt(o) : '—'}</td>
                  <td className="dna__l">{a.fmt(lv)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="lf-muted lf-tiny">{fp.n} {team.abbr} snaps · {fo.n} {other.abbr} snaps · dashed shape and the white tick = NFL 2025 average · depth = air yards</p>
    </section>
  )
}
