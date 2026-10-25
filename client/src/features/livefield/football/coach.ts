/**
 * The coach's game plan, read from this game's plays: how often he throws in each situation compared with
 * what a typical NFL coach does in the exact same spots (the play-call model's pass share, i.e. pass rate
 * over expected), how the calls are working (success = the snap added expected points), the concepts he
 * keeps returning to, his opening script, tempo and formation habits, and fourth-down aggressiveness
 * against the league fourth-down tables. Everything is descriptive: what he did, never a prediction of
 * the result or a wager signal.
 */
import { parseFbPlay, type FbPlay } from './footballData'
import { categoryProbs, classOfPlay, LABELS, predict, stateForPlay, type Category, type Grid, type PlayClass } from './playcall'
import type { EpaPlay, EpGrid } from './ep'
import { advise, type FourthTables } from './fourth'

export interface SituationRow { key: string; label: string; n: number; pass: number; expPass: number; proe: number; success: number | null; ids: string[] }
export interface Concept { key: string; label: string; formation: string; cls: PlayClass | null; category: Category; n: number; success: number | null; yards: number; epa: number; ids: string[] }
export interface FourthCall { id: string; label: string; did: 'go' | 'punt' | 'fg'; best: 'go' | 'punt' | 'fg'; gap: number }
export interface CoachProfile {
  teamId: string
  snaps: number
  pass: number | null
  expPass: number | null
  proe: number | null
  shotgun: number | null
  noHuddle: number | null
  script: { n: number; pass: number; epa: number } | null
  early: { passN: number; passEpa: number | null; runN: number; runEpa: number | null }
  fourth: { faced: number; saidGo: number; wentWhenGo: number; went: number; matched: number; calls: FourthCall[] }
  rows: SituationRow[]
  concepts: Concept[]
}

const clockSecs = (clock: string | null | undefined) => { const m = /^(\d+):(\d+)/.exec(clock ?? ''); return m ? Number(m[1]) * 60 + Number(m[2]) : null }
const FORM: Record<string, string> = { shotgun: 'Gun', 'under-center': 'Under center', pistol: 'Pistol', 'no-huddle': 'No huddle' }

export function buckets(down: number, distance: number, toGoal: number, period: number | null, clock: string | null): string[] {
  const out: string[] = []
  if (down === 1) out.push('d1')
  else if (down === 2) out.push(distance >= 7 ? 'd2l' : distance <= 2 ? 'd2s' : 'd2m')
  else if (down === 3) out.push(distance >= 7 ? 'd3l' : distance <= 2 ? 'd3s' : 'd3m')
  else if (down === 4) out.push('d4')
  if (toGoal <= 20) out.push('rz')
  const s = clockSecs(clock)
  if ((period === 2 || period === 4) && s != null && s <= 120) out.push('two')
  return out
}
export const BUCKET_LABEL: Record<string, string> = { d1: '1st down', d2l: '2nd & long (7+)', d2m: '2nd & medium', d2s: '2nd & short (1-2)', d3l: '3rd & long (7+)', d3m: '3rd & medium', d3s: '3rd & short (1-2)', d4: '4th down', rz: 'Red zone (inside 20)', two: 'Two-minute drill' }
const ORDER = ['d1', 'd2l', 'd2m', 'd2s', 'd3l', 'd3m', 'd3s', 'd4', 'rz', 'two']

export function coachProfile(plays: readonly FbPlay[], teamId: string, homeId: string, grid: Grid | null, epa: readonly EpaPlay[], ep: EpGrid | null, tables: FourthTables | null): CoachProfile {
  const epaById = new Map(epa.map((e) => [e.id, e.epa]))
  const acc = new Map<string, { n: number; pass: number; exp: number; succ: number; succN: number; ids: string[] }>()
  const concepts = new Map<string, Concept & { succN: number; succ: number }>()
  let snaps = 0; let pass = 0; let exp = 0; let expN = 0; let gun = 0; let nh = 0
  const early = { passN: 0, passEpa: 0, runN: 0, runEpa: 0 }
  const script: { pass: number; epa: number; n: number } = { pass: 0, epa: 0, n: 0 }
  const fourth = { faced: 0, saidGo: 0, wentWhenGo: 0, went: 0, matched: 0, calls: [] as FourthCall[] }
  plays.forEach((p, i) => {
    if ((p.offense ?? p.start.team) !== teamId) return
    const parsed = parseFbPlay(p)
    const { cls, category } = classOfPlay(parsed, p.text)
    const down = p.start.down ?? 0
    const dist = p.start.distance ?? 0
    const own = p.start.yardLine == null ? null : teamId === homeId ? p.start.yardLine : 100 - p.start.yardLine
    // fourth-down calls against the league tables
    if (down === 4 && own != null && dist > 0 && ep && tables && (category === 'PASS' || category === 'RUN' || category === 'PUNT' || category === 'FG')) {
      const adv = advise(ep, tables, own, dist)
      const did: FourthCall['did'] = category === 'PUNT' ? 'punt' : category === 'FG' ? 'fg' : 'go'
      const chosen = adv.options.find((o) => o.key === did)
      fourth.faced += 1
      if (adv.best.key === 'go') { fourth.saidGo += 1; if (did === 'go') fourth.wentWhenGo += 1 }
      if (did === 'go') fourth.went += 1
      if (did === adv.best.key || (chosen && adv.best.ep - chosen.ep < 0.15)) fourth.matched += 1
      fourth.calls.push({ id: p.id, label: `Q${p.period} ${p.clock} · 4th & ${dist} at ${own >= 50 ? `opp ${100 - own}` : `own ${own}`}`, did, best: adv.best.key, gap: chosen ? adv.best.ep - chosen.ep : 0 })
    }
    if (category !== 'PASS' && category !== 'RUN') return
    if (/kneel/i.test(p.text ?? '')) return
    snaps += 1
    const isPass = category === 'PASS'
    if (isPass) pass += 1
    if (/shotgun/i.test(p.text ?? '') || parsed.formation === 'shotgun') gun += 1
    if (/no huddle/i.test(p.text ?? '')) nh += 1
    const e = epaById.get(p.id) ?? null
    if (script.n < 10) { script.n += 1; script.pass += isPass ? 1 : 0; script.epa += e ?? 0 }
    if (down === 1 || down === 2) { if (isPass) { early.passN += 1; early.passEpa += e ?? 0 } else { early.runN += 1; early.runEpa += e ?? 0 } }
    let pPass: number | null = null
    if (grid) {
      const st = stateForPlay(plays, i, homeId)
      if (st) { const cp = categoryProbs(predict(grid, st)); pPass = cp.PASS / Math.max(1e-6, cp.PASS + cp.RUN); exp += pPass; expN += 1 }
    }
    if (down >= 1 && down <= 4 && own != null) {
      for (const b of buckets(down, dist, 100 - own, p.period ?? null, p.clock ?? null)) {
        const a = acc.get(b) ?? { n: 0, pass: 0, exp: 0, succ: 0, succN: 0, ids: [] }
        a.n += 1; a.pass += isPass ? 1 : 0; a.exp += pPass ?? 0; a.ids.push(p.id)
        if (e != null) { a.succN += 1; a.succ += e > 0 ? 1 : 0 }
        acc.set(b, a)
      }
    }
    const form = FORM[parsed.formation ?? ''] ?? (/shotgun/i.test(p.text ?? '') ? 'Gun' : 'Base')
    const what = cls ? LABELS[cls].name : isPass ? 'Pass (no direction logged)' : 'Run'
    const key = `${form}|${cls ?? category}`
    const c = concepts.get(key) ?? { key, label: `${form} · ${what}`, formation: form, cls, category, n: 0, success: null, yards: 0, epa: 0, ids: [], succN: 0, succ: 0 }
    c.n += 1; c.yards += p.yards ?? 0; c.epa += e ?? 0; c.ids.push(p.id)
    if (e != null) { c.succN += 1; c.succ += e > 0 ? 1 : 0 }
    concepts.set(key, c)
  })
  const rows: SituationRow[] = ORDER.filter((k) => acc.has(k)).map((k) => {
    const a = acc.get(k)!
    const passShare = a.pass / a.n
    const expShare = grid ? a.exp / a.n : NaN
    return { key: k, label: BUCKET_LABEL[k], n: a.n, pass: passShare, expPass: expShare, proe: grid ? passShare - expShare : 0, success: a.succN ? a.succ / a.succN : null, ids: a.ids }
  })
  return {
    teamId, snaps,
    pass: snaps ? pass / snaps : null,
    expPass: expN ? exp / expN : null,
    proe: snaps && expN ? pass / snaps - exp / expN : null,
    shotgun: snaps ? gun / snaps : null,
    noHuddle: snaps ? nh / snaps : null,
    script: script.n ? script : null,
    early: { passN: early.passN, passEpa: early.passN ? early.passEpa / early.passN : null, runN: early.runN, runEpa: early.runN ? early.runEpa / early.runN : null },
    fourth,
    rows,
    concepts: [...concepts.values()].sort((a, b) => b.n - a.n || b.epa - a.epa).map(({ succN, succ, ...c }) => ({ ...c, success: succN ? succ / succN : null })),
  }
}

/** Plain-English read of the plan: three or four sentences a fan can act on in their own head. */
export function coachRead(p: CoachProfile, abbr: string, college: boolean): string[] {
  const out: string[] = []
  const pts = (v: number) => `${Math.round(Math.abs(v) * 100)} points`
  if (p.proe != null && p.snaps >= 12) {
    out.push(Math.abs(p.proe) >= 0.05
      ? `${abbr} is ${p.proe > 0 ? 'throwing' : 'running'} ${pts(p.proe)} more often than a typical ${college ? 'NFL ' : ''}coach would in the same downs, distances and scores.`
      : `${abbr} is calling it close to a typical ${college ? 'NFL ' : ''}coach (${p.proe >= 0 ? '+' : '-'}${Math.round(Math.abs(p.proe) * 100)} pass-rate points).`)
  }
  const { passN, passEpa, runN, runEpa } = p.early
  if (passN >= 5 && runN >= 5 && passEpa != null && runEpa != null && Math.abs(passEpa - runEpa) >= 0.1) {
    const better = passEpa > runEpa ? 'passes' : 'runs'
    out.push(`On early downs ${better} are working better: ${passEpa >= 0 ? '+' : ''}${passEpa.toFixed(2)} expected points a throw vs ${runEpa >= 0 ? '+' : ''}${runEpa.toFixed(2)} a carry.`)
  }
  const tell = p.rows.filter((r) => r.n >= 6 && Number.isFinite(r.expPass)).sort((a, b) => Math.abs(b.proe) - Math.abs(a.proe))[0]
  if (tell && Math.abs(tell.proe) >= 0.12) out.push(`Biggest tell: on ${tell.label.replace(/ \(.*\)$/, '')} ${abbr} has ${tell.pass >= 0.5 ? 'thrown' : 'run'} ${Math.round((tell.pass >= 0.5 ? tell.pass : 1 - tell.pass) * 100)}% of the time; a typical coach ${tell.pass >= 0.5 ? 'throws' : 'runs'} ${Math.round((tell.pass >= 0.5 ? tell.expPass : 1 - tell.expPass) * 100)}%.`)
  if (p.fourth.faced) out.push(`Fourth down: the league numbers said go ${p.fourth.saidGo} of ${p.fourth.faced} times; ${abbr} went for it ${p.fourth.went}, and ${p.fourth.matched} of the ${p.fourth.faced} calls matched the numbers.`)
  if (!out.length) out.push(`The plan comes into focus after a dozen or so snaps from ${abbr}.`)
  return out
}

/** The situation keys (down-and-distance bucket first) for a snap by `teamId`, or [] when it has no down. */
export function situationOf(p: FbPlay | null, teamId: string, homeId: string): string[] {
  if (!p || !p.start.down || p.start.yardLine == null) return []
  const own = teamId === homeId ? p.start.yardLine : 100 - p.start.yardLine
  return buckets(p.start.down, p.start.distance ?? 10, 100 - own, p.period ?? null, p.clock ?? null)
}
