/**
 * THE DECISION MACHINE's verdict for one game, from Mission Control's stages (owner 10-25/26: the page must make
 * "AISP failed" and "your rule held it" impossible to confuse). Same split as the morning misses report
 * (aisp-analysis/misses-report/misses_report.py):
 *   BET        a paper position was recorded
 *   NO_EDGE    AISP decided and passed: no edge big enough (AISP worked)
 *   YOUR_RULE  an owner rule held it: record-only sport, >10-point gap, QB-out, under-$1, US books only
 *   FAILED     a gate AISP owns failed: no forecast, no approval, no usable price, never decided
 *   PENDING    the game has not reached its decision window yet
 *   VOID       postponed or canceled
 * `stoppedAt` is the gate where the game's run ended (the lamp that lights red or gold).
 */
import type { MissionGame, Stage } from '../mission/missionData'

export const GATES = ['forecast', 'approval', 'prices', 'decision', 'bet'] as const
export type Gate = (typeof GATES)[number]
export const GATE_LABEL: Record<Gate, string> = { forecast: 'Forecast', approval: 'Approval', prices: 'Price', decision: 'Decision', bet: 'Bet' }
export type Verdict = 'BET' | 'NO_EDGE' | 'YOUR_RULE' | 'FAILED' | 'PENDING' | 'VOID'
export const VERDICT_LABEL: Record<Verdict, string> = { BET: 'BET PLACED', NO_EDGE: 'NO EDGE', YOUR_RULE: 'YOUR RULE', FAILED: 'AISP FAILED', PENDING: 'WAITING', VOID: 'NO GAME' }

const PLAIN: Record<string, string> = {
  NO_FINAL_PREGAME_FORECAST: 'The final forecast never arrived.',
  NO_PAPER_RESEARCH_ELIGIBILITY_RECORD: 'The forecast arrived but its approval record never imported.',
  QUOTE_TOO_STALE: 'No fresh betting price at decision time.',
  NO_TWO_SIDED_QUOTE: 'No two-sided betting price.',
  NO_FRESH_PERMITTED_PAIR: 'No fresh price from an allowed source.',
  NO_US_REGULATED_PRICE: 'Only offshore books had a price (US books only rule).',
  NO_POSITIVE_READINESS_EVIDENCE: 'No NFL week authority for this game.',
  NO_QUALIFYING_EDGE: 'No edge big enough to bet.',
  NO_QUALIFYING_VALUE: 'No edge big enough to bet.',
  OWNER_GAP_GT_10_RECORD_ONLY: 'Model and market disagreed by more than 10 points: your record-only rule.',
  OWNER_NBA_PAPER_FUNDING_RECORD_ONLY: 'NBA is record-only: your rule.',
  OWNER_CFB_PAPER_FUNDING_RECORD_ONLY: 'CFB is record-only: your rule.',
}
const ownerish = (c: string | null | undefined) => Boolean(c && /OWNER|RECORD_ONLY|QB_OUT|BELOW_MIN|UNDER_\$?1|MINIMUM_STAKE|US_REGULATED/.test(c))

export interface Read { verdict: Verdict; stoppedAt: Gate | null; reason: string; code: string | null }

export function readGame(g: MissionGame, now = Date.now()): Read {
  const st = g.stages ?? {}
  const d = st.decision ?? ({} as NonNullable<typeof st.decision>)
  const b = st.bet ?? ({} as NonNullable<typeof st.bet>)
  const code = (d.reason_code ?? d.code ?? null) as string | null
  const cls = (d.class ?? '').toUpperCase()
  const words = (c: string | null | undefined, s?: Stage) => (c && PLAIN[c]) || s?.detail || c || 'Unknown.'
  if (/postponed|cancel/i.test(`${g.status} ${g.phase}`)) return { verdict: 'VOID', stoppedAt: null, reason: 'The game was postponed or canceled.', code: null }
  // A recorded position later voided (10-25: stakes under $1, by the owner's floor) is the owner's rule, not a bet.
  if ((b.code === 'POSITION_RECORDED' || b.position_id) && (st.result?.code === 'VOID' || b.status === 'VOID')) {
    return { verdict: 'YOUR_RULE', stoppedAt: 'bet', reason: `Recorded${b.stake_usd != null ? ` at $${b.stake_usd.toFixed(2)}` : ''}, then voided by your rule${b.stake_usd != null && b.stake_usd < 1 ? ' (no stakes under $1)' : ''}.`, code: 'VOID' }
  }
  if (b.code === 'POSITION_RECORDED' || cls === 'BET' || b.position_id) return { verdict: 'BET', stoppedAt: null, reason: b.detail || `${b.side ?? ''} ${b.american ?? ''} at ${b.sportsbook ?? 'a US book'}`.trim(), code }
  if (cls === 'RECORD_ONLY' || ownerish(code) || ownerish(b.code)) return { verdict: 'YOUR_RULE', stoppedAt: ownerish(b.code) && !ownerish(code) ? 'bet' : 'decision', reason: words(ownerish(code) ? code : b.code, d), code: code ?? b.code ?? null }
  if (cls === 'PASS') return { verdict: 'NO_EDGE', stoppedAt: 'decision', reason: words(code, d), code }
  const started = Date.parse(g.starts_at) <= now || /live|final/i.test(g.status) || /STARTED|FINAL/.test(g.phase)
  if (cls === 'BLOCKED' || cls === 'NO_PRICE' || (started && d.state !== 'DONE')) {
    for (const k of ['forecast', 'approval', 'prices'] as const) {
      const s = st[k]
      if (s && s.state !== 'DONE' && s.state !== 'NOT_APPLICABLE') return { verdict: 'FAILED', stoppedAt: k, reason: words(code && cls !== '' ? code : s.code, s), code: code ?? s.code ?? null }
    }
    return { verdict: 'FAILED', stoppedAt: 'decision', reason: cls ? words(code, d) : 'Every input was ready, but the checkpoint never decided.', code }
  }
  const waiting = GATES.find((k) => (st[k]?.state ?? 'UNKNOWN') !== 'DONE') ?? null
  return { verdict: 'PENDING', stoppedAt: waiting, reason: st[waiting ?? 'decision']?.detail ?? 'Waiting for its decision window.', code: null }
}

/** How many gates light green before the run stops (all five for a bet). */
export function litGates(g: MissionGame, r: Read): number {
  if (r.verdict === 'BET') return GATES.length
  if (!r.stoppedAt) return 0
  return GATES.indexOf(r.stoppedAt)
}
