/**
 * MY MODEL (owner 10-27: "put some power into the user's hands ... once they develop their MC model, give them a
 * button to implement their newly developed model"). A user's model is five decisions a sharp bettor actually makes,
 * per sport, applied the same way to every game:
 *
 *   trust       how far to move from the market's price toward AISP's number (0 = market, 1 = AISP, 1.25 = beyond)
 *   confidence  do favourites win more (>1) or less (<1) often than the number says: logit(p) is multiplied by it
 *   homeEdge    extra home advantage, in the sport's own units (points, goals, runs), + helps the home team
 *   keyOut      what one key player out is worth, in the same units (QUESTIONABLE counts `questionable` of it)
 *   chaos       how wide results spread (the score grid and margins only; it never moves the win chance)
 *
 * The win chance: start from the trust blend in log-odds, turn it into a margin with the sport's spread, add the home
 * edge and the injuries (from AISP's availability board), back to a chance, then the confidence stretch.
 * Kept in this browser (aisp-my-model-v1). A teaching / personal tool, never AISP's production model.
 */
import { LEAGUE, invNorm } from './lab'

export const SPORTS = ['NFL', 'NBA', 'NHL', 'MLB', 'CFB'] as const
export interface Factors { trust: number; confidence: number; homeEdge: number; keyOut: number; questionable: number; chaos: number }
export interface MyModel { version: number; savedAt: string; sports: Record<string, Factors> }
/** The spread that turns a margin into a chance, per sport (goals / runs for NHL / MLB). */
export const SPREAD: Record<string, number> = { NFL: 13.5, CFB: 16, NBA: 12, NHL: 2.4, MLB: 4.3 }
export const UNIT: Record<string, string> = { NFL: 'pts', CFB: 'pts', NBA: 'pts', NHL: 'goals', MLB: 'runs' }

export function defaultsFor(sport: string): Factors {
  const out = LEAGUE[sport]?.outPts ?? 3
  return { trust: 1, confidence: 1, homeEdge: 0, keyOut: out, questionable: 0.5, chaos: 1 }
}
/** Slider ranges per factor (sport-aware for the unit-based ones). */
export function ranges(sport: string) {
  const s = SPREAD[sport] ?? 12, out = LEAGUE[sport]?.outPts ?? 3
  return {
    trust: { min: 0, max: 1.25, step: 0.05 }, confidence: { min: 0.6, max: 1.4, step: 0.02 },
    homeEdge: { min: -Math.round(s * 0.25 * 10) / 10, max: Math.round(s * 0.25 * 10) / 10, step: s >= 10 ? 0.5 : 0.05 },
    keyOut: { min: 0, max: Math.round(out * 2.5 * 100) / 100, step: s >= 10 ? 0.5 : 0.05 }, questionable: { min: 0, max: 1, step: 0.05 },
    chaos: { min: 0.6, max: 1.5, step: 0.05 },
  }
}

const clamp = (p: number) => Math.min(1 - 1e-6, Math.max(1e-6, p))
const logit = (p: number) => Math.log(clamp(p) / (1 - clamp(p)))
const sig = (z: number) => 1 / (1 + Math.exp(-z))
/** Standard normal CDF. */
export function phi(z: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2)
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(z * z) / 2)
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y)
}

/**
 * THE MATCHUP (owner 10-27: "shouldn't the selections be sport specific and reflect aspects of the game?"). Each sport
 * gets the reads a handicapper actually makes about ONE game, in that sport's own units. `margin` reads move the
 * home team's expected margin (+ = home better); `total` reads move the expected total score. They are kept per game
 * (aisp-my-reads-v1) and layered on your rules, so the same model can still be applied to the whole board.
 * `fact` names the availability-board players the read is about (QB1, starting goalie, probable starters, stars).
 */
export interface ReadDef { key: string; label: string; unit: string; min: number; max: number; step: number; kind: 'margin' | 'total'; scale?: number; help: string; fact?: 'qb' | 'goalie' | 'sp' | 'stars' | 'scorers' }
export const READS: Record<string, ReadDef[]> = {
  NFL: [
    { key: 'qb', label: 'Quarterback edge', unit: 'pts', min: -7, max: 7, step: 0.5, kind: 'margin', fact: 'qb', help: 'Who has the better QB tonight, in points of margin. An elite QB over a backup is worth 6 to 7.' },
    { key: 'trenches', label: 'Trenches: line play', unit: 'pts', min: -4, max: 4, step: 0.5, kind: 'margin', help: 'Pass rush against pass protection, run game against run defence.' },
    { key: 'turnovers', label: 'Turnover battle', unit: 'giveaways', min: -2, max: 2, step: 0.25, kind: 'margin', scale: 4, help: 'Expected turnover margin; each one is worth about 4 points.' },
    { key: 'rest', label: 'Rest and travel', unit: 'pts', min: -3, max: 3, step: 0.5, kind: 'margin', help: 'Short week, coming off a bye, a West-coast team at 1 PM ET.' },
    { key: 'weather', label: 'Weather (wind, rain, snow)', unit: 'pts of total', min: -10, max: 2, step: 0.5, kind: 'total', help: 'Wind over 15 mph and rain push scoring down; a dome or a perfect day nudges it up.' },
    { key: 'pace', label: 'Pace and play-calling', unit: 'pts of total', min: -8, max: 8, step: 0.5, kind: 'total', help: 'Fast, pass-heavy offences raise the total; ball-control teams lower it.' },
  ],
  CFB: [
    { key: 'qb', label: 'Quarterback edge', unit: 'pts', min: -10, max: 10, step: 0.5, kind: 'margin', fact: 'qb', help: 'College QB play swings games more than in the NFL.' },
    { key: 'talent', label: 'Talent gap (recruiting, depth)', unit: 'pts', min: -14, max: 14, step: 1, kind: 'margin', help: 'A blue-chip roster against a thin one shows up in the fourth quarter.' },
    { key: 'turnovers', label: 'Turnover battle', unit: 'giveaways', min: -2, max: 2, step: 0.25, kind: 'margin', scale: 4, help: 'Expected turnover margin; each one is worth about 4 points.' },
    { key: 'spot', label: 'Spot: motivation, letdown, rivalry', unit: 'pts', min: -5, max: 5, step: 0.5, kind: 'margin', help: 'A look-ahead to a big game, a rivalry, a bowl-eligibility push.' },
    { key: 'weather', label: 'Weather', unit: 'pts of total', min: -12, max: 2, step: 0.5, kind: 'total', help: 'Wind and rain cut passing and scoring.' },
    { key: 'pace', label: 'Tempo', unit: 'pts of total', min: -12, max: 12, step: 1, kind: 'total', help: 'Hurry-up offences add plays and points.' },
  ],
  NBA: [
    { key: 'stars', label: 'Star power tonight', unit: 'pts', min: -6, max: 6, step: 0.5, kind: 'margin', fact: 'stars', help: 'Beyond the injury report: who has the best player on the floor, and is he rolling?' },
    { key: 'rest', label: 'Rest (back-to-back, travel)', unit: 'pts', min: -4, max: 4, step: 0.5, kind: 'margin', help: 'The second night of a back-to-back is worth about 2 to 3 points against.' },
    { key: 'shooting', label: '3-point shooting form', unit: 'pts', min: -5, max: 5, step: 0.5, kind: 'margin', help: 'Hot or cold from deep over the last week or two.' },
    { key: 'size', label: 'Size and rebounding', unit: 'pts', min: -3, max: 3, step: 0.5, kind: 'margin', help: 'Second-chance points and paint control.' },
    { key: 'pace', label: 'Pace', unit: 'possessions', min: -6, max: 6, step: 0.5, kind: 'total', scale: 2.2, help: 'Each extra possession adds about 2.2 points to the total.' },
  ],
  NHL: [
    { key: 'goalie', label: 'Goaltending edge', unit: 'goals', min: -0.8, max: 0.8, step: 0.05, kind: 'margin', fact: 'goalie', help: "Tonight's starters: a hot No. 1 over a cold backup is worth half a goal or more." },
    { key: 'shots', label: 'Five-on-five control (shot share)', unit: 'goals', min: -0.6, max: 0.6, step: 0.05, kind: 'margin', fact: 'scorers', help: 'Who drives play and gets the better chances at even strength.' },
    { key: 'special', label: 'Special teams (power play vs PK)', unit: 'goals', min: -0.4, max: 0.4, step: 0.05, kind: 'margin', help: 'A strong power play against a leaky penalty kill.' },
    { key: 'rest', label: 'Back-to-back and travel', unit: 'goals', min: -0.4, max: 0.4, step: 0.05, kind: 'margin', help: 'The second game in two nights costs about 0.2 goals, more with a backup in net.' },
    { key: 'environment', label: 'Scoring environment', unit: 'goals of total', min: -1.5, max: 1.5, step: 0.1, kind: 'total', help: 'Two run-and-gun teams, or two trap teams.' },
  ],
  MLB: [
    { key: 'sp', label: 'Starting pitcher edge', unit: 'runs', min: -1.5, max: 1.5, step: 0.1, kind: 'margin', fact: 'sp', help: "Tonight's probable starters: an ace over a fifth starter is worth about a run." },
    { key: 'pen', label: 'Bullpen (rest, fatigue)', unit: 'runs', min: -0.8, max: 0.8, step: 0.1, kind: 'margin', help: 'A taxed bullpen leaks late runs.' },
    { key: 'lineup', label: "Lineup vs this pitcher's hand", unit: 'runs', min: -1, max: 1, step: 0.1, kind: 'margin', help: 'Platoon splits and who is resting tonight.' },
    { key: 'park', label: 'Park and weather', unit: 'runs of total', min: -2.5, max: 2.5, step: 0.1, kind: 'total', help: 'Wind blowing out at Wrigley, thin air at Coors, a cold night.' },
  ],
}
export type Reads = Record<string, number>
/** A read's effect in the sport's units: home margin, and total. */
export function readEffect(sport: string, reads: Reads | null | undefined): { margin: number; total: number } {
  let margin = 0, total = 0
  for (const d of READS[sport] ?? []) {
    const v = (reads?.[d.key] ?? 0) * (d.scale ?? 1)
    if (d.kind === 'margin') margin += v; else total += v
  }
  return { margin, total }
}
const RKEY = 'aisp-my-reads-v1'
/** Fired when a game's reads change, so the picker beside the grid and Build Your Model stay in step. */
export const READS_EVENT = RKEY
export function loadReads(): Record<string, Reads> {
  try { const v = localStorage.getItem(RKEY); return v ? (JSON.parse(v) as Record<string, Reads>) : {} } catch { return {} }
}
export function saveReads(gameId: string, reads: Reads): void {
  const all = loadReads()
  if (Object.values(reads).some((v) => v)) all[gameId] = reads; else delete all[gameId]
  try { localStorage.setItem(RKEY, JSON.stringify(all)) } catch { /* private window */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(READS_EVENT, { detail: gameId }))
}

export interface Absence { side: 'HOME' | 'AWAY'; status: string; name: string }
export interface Applied { p: number; steps: { label: string; p: number }[]; injuryUnits: number; readUnits: number; totalShift: number }

/** Your model's home win chance for one game. `aisp` / `market` are home two-way chances (market may be null). */
export function applyModel(sport: string, f: Factors, aisp: number, market: number | null, out: Absence[] = [], reads: Reads | null = null): Applied {
  const s = SPREAD[sport] ?? 12
  const steps: Applied['steps'] = []
  const base = market == null ? aisp : sig(logit(market) + f.trust * (logit(aisp) - logit(market)))
  steps.push({ label: market == null ? 'AISP (no market yet)' : `Trust ${Math.round(f.trust * 100)}% AISP`, p: base })
  let margin = s * invNorm(clamp(base))
  margin += f.homeEdge
  if (f.homeEdge) steps.push({ label: 'Home edge', p: phi(margin / s) })
  let injury = 0
  for (const a of out) {
    const w = a.status === 'OUT' ? 1 : a.status === 'DOUBTFUL' ? 0.75 : a.status === 'QUESTIONABLE' ? f.questionable : 0
    injury += (a.side === 'HOME' ? -1 : 1) * w * f.keyOut
  }
  margin += injury
  if (injury) steps.push({ label: 'Injuries', p: phi(margin / s) })
  const r = readEffect(sport, reads)
  margin += r.margin
  if (r.margin) steps.push({ label: 'Your matchup read', p: phi(margin / s) })
  const p = sig(f.confidence * logit(phi(margin / s)))
  if (f.confidence !== 1) steps.push({ label: 'Confidence', p })
  return { p, steps, injuryUnits: injury, readUnits: r.margin, totalShift: r.total }
}

const KEY = 'aisp-my-model-v1'
export function loadModel(): MyModel | null {
  try { const v = localStorage.getItem(KEY); return v ? (JSON.parse(v) as MyModel) : null } catch { return null }
}
export function saveModel(sport: string, f: Factors): MyModel {
  const prev = loadModel()
  const next: MyModel = { version: (prev?.version ?? 0) + 1, savedAt: new Date().toISOString(), sports: { ...(prev?.sports ?? {}), [sport]: f } }
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* private window */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(KEY))
  return next
}
export const MODEL_EVENT = KEY
