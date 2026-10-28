/**
 * Focus mode, round 3 (owner 10-27: "the other three are pretty bare ... it needs to be explicit where we are waiting
 * on sportsbook lines or a refreshed model ... the user might think the platform is broken"; Focus = the phone app).
 *
 *   Pipeline    per game, AISP's three steps as chips: Model ✓ / Lines ⏳ 5:45 PM / AISP decides 6:30 PM, straight
 *               from Mission Control, so a waiting state always says what it is waiting for and when.
 *   UpNext      the next games to start, with their pipeline and countdown (Live tab before games go live).
 *   Exposure    your slip by sport as a ring, with total risk and the best case.
 *   BestCalls   AISP's best recent winning bets (Record tab).
 */
import type { CSSProperties } from 'react'
import { Crest } from '../flow/Crest'
import { allPositions, american, etClock, resultOf, usd, type PulseGame } from '../pulse/pulseData'
import type { MissionGame, Stage } from '../mission/missionData'
import type { Ledger } from '../../services/commandCenter'
import { Countdown, SPORT_COLOR } from './FocusExtras'

const clock = (iso: string | null | undefined) => (iso ? etClock(iso) : '')

function chip(kind: string, st: Stage | undefined, dueFallback: string | null | undefined, words: { done: string; wait: string; blocked: string }) {
  const state = st?.state ?? 'UNKNOWN'
  if (state === 'DONE') return <span key={kind} className="fp-chip is-done">✓ {words.done}</span>
  if (state === 'WAITING') return <span key={kind} className="fp-chip is-wait" title={st?.detail ?? ''}>⏳ {words.wait} {clock(st?.due_at ?? dueFallback) || (st?.label ?? '')}</span>
  if (state === 'BLOCKED') return <span key={kind} className="fp-chip is-blocked" title={st?.detail ?? ''}>⚠ {words.blocked}{st?.label ? `: ${st.label}` : ''}</span>
  if (state === 'NOT_APPLICABLE') return null
  return <span key={kind} className="fp-chip">… {words.wait}</span>
}

/** AISP's pipeline for one game: is the model fresh, are the lines in, when does AISP decide. */
export function Pipeline({ mc }: { mc: MissionGame | null | undefined }) {
  if (!mc) return null
  const s = mc.stages
  const decided = s.decision?.state === 'DONE'
  return (
    <div className="fp-pipe" aria-label="AISP's pipeline for this game">
      {chip('model', s.forecast, mc.clocks?.forecast_due_at, { done: 'Model ready', wait: 'Model refresh', blocked: 'Model' })}
      {chip('lines', s.prices, mc.clocks?.buy_opens_at, { done: 'Lines in', wait: 'Lines at', blocked: 'Lines' })}
      {decided ? <span className="fp-chip is-done">✓ AISP decided</span>
        : chip('decision', s.decision, mc.clocks?.decision_opens_at, { done: 'AISP decided', wait: 'AISP decides', blocked: 'Decision' })}
    </div>
  )
}

export function UpNext({ games, mcById }: { games: PulseGame[]; mcById: Map<string, MissionGame> }) {
  const next = games.filter((g) => g.phase === 'UPCOMING').sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt)).slice(0, 4)
  if (!next.length) return null
  return (
    <section className="fp-next" aria-label="Up next">
      <h2>Up next</h2>
      <div className="fm-cards">
        {next.map((g) => (
          <article key={g.id} className="fp-next__card" style={{ '--sc': SPORT_COLOR[g.sport] ?? '#94a3b8' } as CSSProperties}>
            <span className="fp-next__top"><i>{g.sport}</i><b>{etClock(g.startsAt)}</b><Countdown iso={g.startsAt} /></span>
            <span className="fp-next__teams"><Crest sport={g.sport} abbr={g.away.abbr} size={30} /><b>{g.away.abbr}</b><em>@</em><b>{g.home.abbr}</b><Crest sport={g.sport} abbr={g.home.abbr} size={30} /></span>
            <Pipeline mc={mcById.get(g.id)} />
          </article>
        ))}
      </div>
    </section>
  )
}

export function Exposure({ rows }: { rows: { sport: string; stake: number; win: number; open: boolean }[] }) {
  const open = rows.filter((r) => r.open && r.stake > 0)
  if (!open.length) return null
  const by = new Map<string, number>()
  open.forEach((r) => by.set(r.sport, (by.get(r.sport) ?? 0) + r.stake))
  const total = [...by.values()].reduce((a, b) => a + b, 0)
  const best = open.reduce((a, r) => a + r.win, 0)
  const R = 46, C = 2 * Math.PI * R
  let acc = 0
  return (
    <section className="fp-expo" aria-label="Your exposure">
      <svg viewBox="0 0 120 120" width="120" height="120" aria-hidden="true">
        <circle cx="60" cy="60" r={R} className="fp-expo__track" />
        {[...by.entries()].map(([s, v]) => { const len = (v / total) * C; const el = <circle key={s} cx="60" cy="60" r={R} className="fp-expo__arc" stroke={SPORT_COLOR[s] ?? '#94a3b8'} strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-acc} transform="rotate(-90 60 60)" />; acc += len; return el })}
        <text x="60" y="56" textAnchor="middle" className="fp-expo__big">{usd(total).replace('.00', '')}</text>
        <text x="60" y="74" textAnchor="middle" className="fp-expo__small">at risk</text>
      </svg>
      <div className="fp-expo__legend">
        {[...by.entries()].map(([s, v]) => <span key={s}><i style={{ background: SPORT_COLOR[s] }} />{s} <b>{usd(v)}</b></span>)}
        <span className="fp-expo__best">If every pick wins: <b>+{usd(best).replace('$', '$')}</b></span>
      </div>
    </section>
  )
}

export function BestCalls({ ledger }: { ledger: Ledger | null | undefined }) {
  const wins = allPositions(ledger ?? null).filter((p) => resultOf(p) === 'WIN' && (p.entry?.american ?? 0) > 0)
    .sort((a, b) => (b.profit_loss ?? 0) - (a.profit_loss ?? 0)).slice(0, 4)
  if (!wins.length) return null
  return (
    <section className="fp-best" aria-label="AISP's best calls">
      <h2>AISP’s best calls</h2>
      <div className="fm-cards">
        {wins.map((p) => (
          <article key={p.position_id} className="fp-best__card" style={{ '--sc': SPORT_COLOR[p.sport] ?? '#94a3b8' } as CSSProperties}>
            <i>{p.sport} · {p.settled_at ? new Date(p.settled_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}</i>
            <b>{p.selection} {american(p.entry?.american ?? null)}</b>
            <small>{p.matchup}</small>
            <em>+{usd(p.profit_loss ?? 0).replace('$', '$')} on {usd(p.stake ?? 0)}</em>
          </article>
        ))}
      </div>
    </section>
  )
}
