/**
 * GAME PULSE: the three things that matter right now, each with its evidence one tap away.
 * Every card is computed from the recorded plays (no language model, no cost) and says only what the plays
 * show. Each card carries a two-bar comparison of the teams on that measure. "Show me" lights up exactly the
 * supporting plays on the attack map, the momentum chart and the play list. Cards are held steady: they
 * refresh every three plays or on a score or turnover, not after every snap.
 */
import { useMemo, useRef } from 'react'
import { chartPoints } from './AttackMap'
import type { EpaPlay } from './ep'
import { parseFbPlay, visibleColor, type FbPlay, type FbTeam } from './footballData'

export interface PulseCard { key: string; headline: string; detail: string; stat: string; ids: string[]; teamId: string | null; tone: 'good' | 'bad' | 'neutral'; score: number; compare?: { label: string; a: number; b: number; fmt?: (v: number) => string } }

const clamp01 = (v: number) => Math.max(0, Math.min(1, v))
const SCRIM = new Set(['pass', 'incomplete', 'interception', 'sack', 'rush'])

export function computePulse(plays: readonly FbPlay[], epa: EpaPlay[], away: FbTeam, home: FbTeam): PulseCard[] {
  const cards: PulseCard[] = []
  const epaById = new Map(epa.map((e) => [e.id, e.epa]))
  const scrim = (id: string) => epa.filter((e) => e.team === id && SCRIM.has(e.kind))
  const pctf = (v: number) => `${Math.round(v * 100)}%`

  for (const t of [away, home]) {
    const pts = chartPoints(plays, t.id, epaById).filter((p) => ['pass', 'td', 'incomplete', 'interception'].includes(p.kind) && !p.zone.startsWith('run') && p.zone !== 'sack')
    const zones = new Map<string, typeof pts>()
    pts.forEach((p) => zones.set(p.zone, [...(zones.get(p.zone) ?? []), p]))
    for (const [zone, ps] of zones) {
      if (ps.length < 4) continue
      const mean = ps.reduce((s, p) => s + (p.epa ?? 0), 0) / ps.length
      if (mean < 0.25) continue
      const comp = ps.filter((p) => p.kind === 'pass' || p.kind === 'td').length
      const yds = ps.reduce((s, p) => s + (p.kind === 'incomplete' || p.kind === 'interception' ? 0 : p.yards), 0)
      const rest = pts.filter((p) => p.zone !== zone)
      const restMean = rest.length ? rest.reduce((s, p) => s + (p.epa ?? 0), 0) / rest.length : 0
      cards.push({ key: `pass-${t.id}-${zone}`, headline: `${t.abbr} is finding space ${zone.replace('-', ' ')}`, detail: `${comp} of ${ps.length} passes there for ${yds} yards.`, stat: `+${mean.toFixed(2)} EPA per throw`, ids: ps.map((p) => p.id), teamId: t.id, tone: 'good', score: clamp01((mean * Math.sqrt(ps.length)) / 2.2), compare: { label: 'EPA/throw: this zone vs elsewhere', a: mean, b: restMean, fmt: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}` } })
    }
  }
  const thirdRate = (id: string) => { const th = plays.filter((p) => (p.offense ?? p.start.team) === id && p.start.down === 3 && SCRIM.has(parseFbPlay(p).kind) && p.start.distance != null); const made = th.filter((p) => (p.yards ?? 0) >= (p.start.distance ?? 99) || parseFbPlay(p).touchdown); return { th, made, rate: th.length ? made.length / th.length : null } }
  const tA = thirdRate(away.id), tH = thirdRate(home.id)
  for (const [t, r] of [[away, tA], [home, tH]] as const) {
    if (r.th.length < 5 || r.rate == null) continue
    const dev = r.rate - 0.4
    if (Math.abs(dev) < 0.2) continue
    cards.push({ key: `third-${t.id}`, headline: dev < 0 ? `${t.abbr} keeps stalling on 3rd down` : `${t.abbr} lives on 3rd down`, detail: `${r.made.length} of ${r.th.length} converted (league about 40%).`, stat: `${pctf(r.rate)} on 3rd down`, ids: r.th.map((p) => p.id), teamId: t.id, tone: dev < 0 ? 'bad' : 'good', score: clamp01((Math.abs(dev) * Math.sqrt(r.th.length)) / 1.1), compare: { label: '3rd-down conversion', a: tA.rate ?? 0, b: tH.rate ?? 0, fmt: pctf } })
  }
  const starts: Record<string, { own: number[]; ids: string[] }> = { [away.id]: { own: [], ids: [] }, [home.id]: { own: [], ids: [] } }
  let lastOff: string | null = null
  for (const p of plays) {
    const off = p.offense ?? p.start.team
    if (!off || !SCRIM.has(parseFbPlay(p).kind) || p.start.yardLine == null) continue
    if (off !== lastOff) { starts[off]?.own.push(off === home.id ? p.start.yardLine : 100 - p.start.yardLine); starts[off]?.ids.push(p.id); lastOff = off }
  }
  const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / (a.length || 1)
  if (starts[away.id].own.length >= 3 && starts[home.id].own.length >= 3) {
    const a = avg(starts[away.id].own), h = avg(starts[home.id].own)
    const better = a > h ? away : home
    const diff = Math.abs(a - h)
    if (diff >= 8) cards.push({ key: 'field', headline: `${better.abbr} keeps winning field position`, detail: `Drives start at its own ${Math.round(Math.max(a, h))} on average vs the other side's own ${Math.round(Math.min(a, h))}.`, stat: `${Math.round(diff)} yards a drive`, ids: starts[better.id].ids, teamId: better.id, tone: 'good', score: clamp01(diff / 18), compare: { label: 'Average drive start (own yard line)', a, b: h, fmt: (v) => `own ${Math.round(v)}` } })
  }
  const boom = (id: string) => epa.filter((e) => e.team === id && e.yards >= 20 && SCRIM.has(e.kind))
  const ba = boom(away.id), bh = boom(home.id)
  if (Math.abs(ba.length - bh.length) >= 2) {
    const w = ba.length > bh.length ? away : home
    const wp = ba.length > bh.length ? ba : bh
    cards.push({ key: 'boom', headline: `${w.abbr} owns the big plays`, detail: `${wp.length} plays of 20+ yards to ${Math.min(ba.length, bh.length)} for the other side.`, stat: `${wp.reduce((s, e) => s + e.yards, 0)} yards on those plays`, ids: wp.map((e) => e.id), teamId: w.id, tone: 'good', score: clamp01(Math.abs(ba.length - bh.length) / 4), compare: { label: 'Plays of 20+ yards', a: ba.length, b: bh.length, fmt: (v) => String(Math.round(v)) } })
  }
  const turn = (id: string) => plays.filter((p) => p.turnover && (p.offense ?? p.start.team) === id && !/punt|kickoff/i.test(p.type ?? ''))
  const ta = turn(away.id), th = turn(home.id)
  if (ta.length !== th.length) {
    const giver = ta.length > th.length ? away : home
    const taker = giver.id === away.id ? home : away
    const gp = ta.length > th.length ? ta : th
    cards.push({ key: 'turnover', headline: `${taker.abbr} is winning the turnover battle`, detail: `${giver.abbr} has given it away ${gp.length} time${gp.length > 1 ? 's' : ''}; ${taker.abbr} ${Math.min(ta.length, th.length)}.`, stat: `+${Math.abs(ta.length - th.length)} takeaway margin`, ids: gp.map((p) => p.id), teamId: taker.id, tone: 'good', score: clamp01(0.5 + Math.abs(ta.length - th.length) * 0.3), compare: { label: 'Giveaways', a: ta.length, b: th.length, fmt: (v) => String(Math.round(v)) } })
  }
  for (const t of [away, home]) {
    const runs = epa.filter((e) => e.team === t.id && e.kind === 'rush')
    if (runs.length < 8) continue
    const sum = runs.reduce((s, e) => s + e.epa, 0)
    const ypc = runs.reduce((s, e) => s + e.yards, 0) / runs.length
    if (Math.abs(sum) < 2.5) continue
    const otherRuns = epa.filter((e) => e.team !== t.id && e.kind === 'rush')
    const oy = otherRuns.length ? otherRuns.reduce((s, e) => s + e.yards, 0) / otherRuns.length : 0
    cards.push({ key: `run-${t.id}`, headline: sum < 0 ? `${t.abbr}'s running game is going nowhere` : `${t.abbr}'s running game is paying off`, detail: `${runs.length} carries, ${ypc.toFixed(1)} yards each.`, stat: `${sum >= 0 ? '+' : ''}${sum.toFixed(1)} EPA on runs`, ids: runs.map((e) => e.id), teamId: t.id, tone: sum < 0 ? 'bad' : 'good', score: clamp01(Math.abs(sum) / 6), compare: { label: 'Yards per carry', a: t.id === away.id ? ypc : oy, b: t.id === away.id ? oy : ypc, fmt: (v) => v.toFixed(1) } })
  }
  void scrim
  return cards.filter((c) => c.score >= 0.25).sort((a, b) => b.score - a.score).slice(0, 3)
}

interface Props { plays: readonly FbPlay[]; epa: EpaPlay[]; away: FbTeam; home: FbTeam; active: string | null; onShow: (card: PulseCard | null) => void; wpPill?: { team: string; pct: number; color: string } | null }

export function GamePulse({ plays, epa, away, home, active, onShow, wpPill = null }: Props) {
  const marker = `${Math.floor(plays.length / 3)}|${plays.filter((p) => p.scoring).length}|${plays.filter((p) => p.turnover).length}|${away.id}`
  const held = useRef<{ marker: string; cards: PulseCard[] }>({ marker: '', cards: [] })
  const cards = useMemo(() => {
    if (held.current.marker !== marker || !held.current.cards.length) held.current = { marker, cards: computePulse(plays, epa, away, home) }
    return held.current.cards
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marker, epa.length])
  const ca = visibleColor(away, '#3fd8c4'), ch = visibleColor(home, '#ffb547')
  return (
    <section className="lf-card gp" aria-label="Game pulse">
      <header className="am__head">
        <h3>GAME PULSE <small className="lf-muted">3 key reasons driving the game right now</small></h3>
        {wpPill ? <span className="gp__pill" style={{ ['--tc' as string]: wpPill.color }}>{wpPill.team} {Math.round(wpPill.pct * 100)}%</span> : null}
      </header>
      {cards.length ? (
        <div className="gp__cards">
          {cards.map((c, i) => {
            const cmp = c.compare
            const max = cmp ? Math.max(Math.abs(cmp.a), Math.abs(cmp.b), 1e-6) : 1
            return (
              <article key={c.key} className={`gp__card gp__card--${c.tone}${active === c.key ? ' is-active' : ''}`}>
                <span className="gp__n">{i + 1}</span>
                <h4>{c.headline}</h4>
                <p>{c.detail}</p>
                {cmp ? (
                  <div className="gp__cmp" title={cmp.label}>
                    <span><small>{away.abbr}</small><i style={{ width: `${(Math.abs(cmp.a) / max) * 100}%`, background: ca }} /><b>{(cmp.fmt ?? String)(cmp.a)}</b></span>
                    <span><small>{home.abbr}</small><i style={{ width: `${(Math.abs(cmp.b) / max) * 100}%`, background: ch }} /><b>{(cmp.fmt ?? String)(cmp.b)}</b></span>
                  </div>
                ) : null}
                <b className="gp__stat">{c.stat}</b>
                <button type="button" className="lf-chipbtn" aria-pressed={active === c.key} onClick={() => onShow(active === c.key ? null : c)}>{active === c.key ? 'Clear' : `Show me · ${c.ids.length} plays`}</button>
              </article>
            )
          })}
        </div>
      ) : <p className="lf-muted lf-small">The three things that matter appear once the plays pile up: where passes work, who stalls, who owns field position.</p>}
    </section>
  )
}
