/**
 * THE DECISION MACHINE (Daily Decisions): every game runs through AISP's five gates on screen. The lamps light
 * in order; the run stops at the gate that ended it, and a stamp slams down: BET PLACED, NO EDGE (AISP worked
 * and passed), YOUR RULE (an owner rule held it, gold) or AISP FAILED (red, with the reason in plain words).
 * Data: Mission Control's per-game stages for today or yesterday. Verdict rules: verdict.ts.
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Crest } from '../flow/Crest'
import { etClock } from '../pulse/pulseData'
import { useMissionControl, type MissionGame } from '../mission/missionData'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { GATES, GATE_LABEL, VERDICT_LABEL, litGates, readGame, type Read, type Verdict } from './verdict'
import './machine.css'

const ORDER: Verdict[] = ['FAILED', 'YOUR_RULE', 'BET', 'NO_EDGE', 'PENDING', 'VOID']
const FILTERS: Array<{ key: 'ALL' | Verdict; label: string }> = [
  { key: 'ALL', label: 'All' }, { key: 'FAILED', label: 'AISP failed' }, { key: 'YOUR_RULE', label: 'Your rule' },
  { key: 'BET', label: 'Bets' }, { key: 'NO_EDGE', label: 'No edge' }, { key: 'PENDING', label: 'Waiting' },
]

export function DecisionMachine() {
  const today = operationalToday()
  const [day, setDay] = useState(today)
  const [filter, setFilter] = useState<'ALL' | Verdict>('ALL')
  const [run, setRun] = useState(0)
  const mc = useMissionControl(day)
  const games = (mc.data?.games ?? []) as MissionGame[]
  const rows = useMemo(() => games.map((g) => ({ g, r: readGame(g) }))
    .sort((a, b) => ORDER.indexOf(a.r.verdict) - ORDER.indexOf(b.r.verdict) || Date.parse(a.g.starts_at) - Date.parse(b.g.starts_at)), [games])
  const count = (v: Verdict) => rows.filter((x) => x.r.verdict === v).length
  const shown = rows.filter((x) => filter === 'ALL' || x.r.verdict === filter)

  return (
    <section className="dm" aria-labelledby="dm-title">
      <header className="dm__head">
        <h2 id="dm-title">THE DECISION MACHINE</h2>
        <div className="dm__days" role="tablist">
          {[today, shiftDate(today, -1)].map((d) => <button key={d} role="tab" aria-selected={day === d} className={day === d ? 'is-on' : ''} onClick={() => { setDay(d); setRun((n) => n + 1) }}>{d === today ? 'Today' : 'Yesterday'}</button>)}
        </div>
        <button type="button" className="dm__replay" onClick={() => setRun((n) => n + 1)} title="Run the machine again">↻ Run it again</button>
      </header>
      <div className="dm__counters">
        {(['BET', 'NO_EDGE', 'YOUR_RULE', 'FAILED', 'PENDING'] as Verdict[]).map((v) => (
          <button key={v} type="button" className={`dm-count dm-count--${v.toLowerCase()}${filter === v ? ' is-on' : ''}`} onClick={() => setFilter(filter === v ? 'ALL' : v)}>
            <b>{count(v)}</b><small>{VERDICT_LABEL[v]}</small>
          </button>
        ))}
      </div>
      <div className="dm__filters">{FILTERS.map((f) => <button key={f.key} type="button" className={filter === f.key ? 'is-on' : ''} onClick={() => setFilter(f.key)}>{f.label}</button>)}</div>
      {mc.loading && !rows.length ? <p className="dm__empty">Starting the machine…</p> : null}
      {mc.error && !rows.length ? <p className="dm__empty">Mission Control could not be read, so the machine has nothing to run.</p> : null}
      {!mc.loading && !mc.error && !shown.length ? <p className="dm__empty">No games in this view.</p> : null}
      <ol className="dm__rows" key={`${day}:${run}:${filter}`}>
        {shown.map(({ g, r }, i) => <MachineRow key={g.game_id} g={g} r={r} i={i} />)}
      </ol>
      <p className="dm__fine">Gates come from Mission Control. <b>AISP failed</b> is on AISP to fix; <b>your rule</b> is working as designed; <b>no edge</b> means AISP decided and passed. The same split goes out each morning in the misses report. Research and paper only.</p>
    </section>
  )
}

function MachineRow({ g, r, i }: { g: MissionGame; r: Read; i: number }) {
  const lit = litGates(g, r)
  const delay = Math.min(i, 12) * 0.12
  return (
    <li className={`dm-row dm-row--${r.verdict.toLowerCase()}`} style={{ '--d': `${delay}s` } as CSSProperties}>
      <Link to={`/games/${g.game_id}`} className="dm-row__game">
        <Crest sport={g.sport} abbr={g.away.abbr} size={26} /><Crest sport={g.sport} abbr={g.home.abbr} size={26} />
        <span><b>{g.away.abbr} @ {g.home.abbr}</b><small>{g.sport} · {etClock(g.starts_at)} ET</small></span>
      </Link>
      <div className="dm-row__gates" aria-label={`Gates passed: ${lit} of ${GATES.length}`}>
        {GATES.map((k, j) => {
          const state = j < lit ? 'is-go' : r.stoppedAt === k ? `is-stop is-${r.verdict.toLowerCase()}` : 'is-off'
          return (
            <span key={k} className={`dm-gate ${state}`} style={{ '--g': `${delay + 0.15 + j * 0.16}s` } as CSSProperties} title={`${GATE_LABEL[k]}: ${g.stages?.[k]?.label ?? g.stages?.[k]?.state ?? 'unknown'}`}>
              <i /><small>{GATE_LABEL[k]}</small>
            </span>
          )
        })}
      </div>
      <div className="dm-row__verdict">
        <span className="dm-stamp" style={{ '--s': `${delay + 0.25 + Math.max(lit, 1) * 0.16}s` } as CSSProperties}>{VERDICT_LABEL[r.verdict]}</span>
        <small>{r.reason}</small>
      </div>
    </li>
  )
}
