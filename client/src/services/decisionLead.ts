/**
 * What a Today / Daily Decisions game card leads with, mapped strictly from
 * the stored decision (`/command-center/decisions`), the market coverage read
 * (`/command-center/markets`) and the paper ledger (`/command-center/ledger`).
 *
 * THREE THINGS NEVER CONFUSED
 * ---------------------------
 *   OFFICIAL  the governed AISP decision (the experiment governs moneyline):
 *             provisional (an early MORNING / LINEUP_CONFIRMED read), final
 *             (FINAL_PREGAME: qualified, declined or blocked), or none yet.
 *   RESEARCH  a research comparison or a manual My Card pick. Never official,
 *             never funded; My Card lives only in this browser.
 *   FUNDED    a paper wager backed by a ledger position (the decision carries a
 *             position, cross-checked against the ledger read the Active
 *             Wagers panel and the $1,000 Experiment use).
 *
 * SHADOW ONLY
 * -----------
 * A final MLB V18 QUALIFIED_BET that carries a `funding_pause` record
 * (MLB_V18_FUNDING_PAUSE_V1) met the policy but was blocked before funding
 * evaluation: it is "Qualified · shadow only (funding paused)". It is never
 * funded, never counted in a total, and never "Qualified · not funded yet"
 * (which means a position may still be recorded).
 *
 * PRICES
 * ------
 * "Recorded at decision" is the price the decision stored (never refreshed).
 * The best current cross-book quote from coverage is shown apart, under a neutral
 * name, with its book and clock; it is called later than the decision price only
 * when its observation time is after the recorded quote's (both known).
 *
 * EDGE
 * ----
 * The only edge named on the lead is the RECORDED policy-adjusted edge, read
 * from the decision's recorded fields (services/decisionEdges). It is never
 * recomputed, and never taken from the stored `edge` column (which is the
 * unadjusted model minus the offered price's break-even probability). A value
 * the decision did not record reads "not available".
 */
import { etTime, relative, type CoverageGame, type DecisionItem, type LedgerPosition } from './commandCenter'
import { ALLOWANCE_DECLINE, CHECKPOINT_LABEL } from './cfbMarket'

/** Value qualified, but the owner's shared $100 had no room (fully used, or held for MLB/NHL games). */
const ALLOWANCE_SENTENCE = 'Declined at the final check for budget, not value: the value qualified, but the shared $100 for the day '
  + 'had no room left (fully used, or held for MLB/NHL games still to decide).'
import { recordedPolicyEdge, testThresholdText, valueText, type EdgeTest } from './decisionEdges'

export const NOT_AVAILABLE = 'not available'
export const OFFICIAL_EDGE_LABEL = 'Recorded policy-adjusted edge'
export const RESEARCH_EDGE_NOTE = 'Research figure · current price · unadjusted, not the policy edge'
export const MY_CARD_NOTE = 'My Card picks are unfunded and saved only in this browser'

export type LeadKind =
  | 'FUNDED_OPEN' | 'FUNDED_AWAITING' | 'FUNDED_SETTLED'
  | 'QUALIFIED' | 'QUALIFIED_SHADOW_ONLY' | 'FINAL_DECLINE' | 'FINAL_BLOCKED'
  | 'PROVISIONAL' | 'NOT_EVALUATED' | 'WINDOW_CLOSED' | 'MISSED'

/** Which authority the status carries. */
export type LeadAuthority = 'FUNDED' | 'FINAL' | 'PROVISIONAL' | 'NONE'
/** blue = scheduled and ready, orange = waiting on an input (forecast or price), red = blocked or lost. */
export type LeadTone = 'green' | 'gold' | 'red' | 'gray' | 'orange' | 'blue'

export const AUTHORITY_LABEL: Record<LeadAuthority, string> = {
  FUNDED: 'Funded paper wager',
  FINAL: 'Official AISP decision · final',
  PROVISIONAL: 'Official AISP decision · early read, not final',
  NONE: 'Official AISP decision · not made yet',
}

export interface LeadSelection {
  selection: string
  market: string
  sportsbook: string | null
  american: number | null
  /** When the recorded quote was observed, and its age at the decision (what the policy judged). */
  quoteObservedAt: string | null
  quoteAgeAtDecisionMinutes: number | null
  recordedAt: string | null
}

export interface LeadEdge {
  label: string
  value: number | null
  text: string
  /** How to read it: the threshold it was tested against, or what it is. */
  qualifier: string
}

export interface LeadFunded {
  positionId: string
  stake: number | null
  american: number | null
  sportsbook: string | null
  /** true: the ledger lists the position; false: the ledger was read and does not; null: ledger not loaded. */
  ledgerConfirmed: boolean | null
  result: string | null
  profitLoss: number | null
}

/** The immutable funding-pause record behind a shadow-only decision. Never a money figure. */
export interface LeadFundingPause {
  recordId: string
  contentSha256: string
  pausedAt: string | null
  reason: string
  status: string
  verified: boolean
}

export const SHADOW_ONLY_LABEL = 'Qualified · shadow only (funding paused)'
export const SHADOW_ONLY_SENTENCE =
  'It met the policy at the final check, but MLB V18 funding is paused pending prospective validation, so no paper position was created.'

export interface LatestSeen {
  american: number
  sportsbook: string | null
  observedAt: string | null
}

/** Neutral name for the best current cross-book quote: it is not assumed to be later than the decision. */
export const BEST_QUOTE_LABEL = 'Best current quote across books'
export const TIME_NOT_AVAILABLE = 'time not available'

export type QuoteOrder = 'LATER' | 'NOT_LATER' | 'UNKNOWN'

/** Whether the best current quote was observed after the recorded quote; UNKNOWN unless both clocks exist. */
export function quoteOrder(latestObservedAt: string | null | undefined, recordedObservedAt: string | null | undefined): QuoteOrder {
  const a = latestObservedAt ? Date.parse(latestObservedAt) : NaN
  const b = recordedObservedAt ? Date.parse(recordedObservedAt) : NaN
  if (!Number.isFinite(a) || !Number.isFinite(b)) return 'UNKNOWN'
  return a > b ? 'LATER' : 'NOT_LATER'
}

/**
 * "Caesars · observed 4:12 PM ET" (+ " · later than the decision price" only when the
 * clocks establish it). A missing book or clock is stated, never guessed.
 */
export function bestQuoteSource(latest: Pick<LatestSeen, 'sportsbook' | 'observedAt'>, recordedObservedAt: string | null | undefined): string {
  const book = shortBook(latest.sportsbook) ?? `sportsbook ${NOT_AVAILABLE}`
  const time = latest.observedAt && Number.isFinite(Date.parse(latest.observedAt)) ? `observed ${etTime(latest.observedAt)}` : `observed · ${TIME_NOT_AVAILABLE}`
  const later = quoteOrder(latest.observedAt, recordedObservedAt) === 'LATER' ? ' · later than the decision price' : ''
  return `${book} · ${time}${later}`
}

export interface DecisionLead {
  kind: LeadKind
  authority: LeadAuthority
  label: string
  tone: LeadTone
  sentence: string
  official: LeadSelection | null
  edge: LeadEdge
  funded: LeadFunded | null
  /** Set only for a shadow-only (funding-paused) decision. */
  fundingPause?: LeadFundingPause | null
  /** Before a decision: the input that is missing, if any. A missing input is what stops a position being taken. */
  waitingOn?: 'FORECAST' | 'PRICE' | null
}

export const shortBook = (name: string | null | undefined) => (name ?? '').replace(/\s*\(.*\)\s*$/, '') || null
const american = (a: number | null | undefined) => (a == null ? NOT_AVAILABLE : a > 0 ? `+${a}` : `${a}`)
const money = (v: number | null | undefined) => (v == null ? NOT_AVAILABLE : `$${Math.abs(v).toFixed(2)}`)
const signedMoney = (v: number | null | undefined) => (v == null ? NOT_AVAILABLE : `${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(2)}`)
const clock = (iso: string | null | undefined) => etTime(iso).replace(' ET', '')
const MARKET_LABEL: Record<string, string> = { MONEYLINE: 'Moneyline', SPREAD: 'Spread', RUN_LINE: 'Run line', TOTAL: 'Game total' }
export const marketName = (m: string | null | undefined) => MARKET_LABEL[String(m ?? '').toUpperCase()] ?? (m ? m.replace(/_/g, ' ').toLowerCase() : 'Market')

const PHASE_NAME: Record<string, string> = { MORNING: 'Morning read', LINEUP_CONFIRMED: 'Lineup read' }

interface Window { opensAt: string | null; closesAt: string | null }

function windowOf(item: DecisionItem | null, game: CoverageGame | null): Window {
  return {
    opensAt: game?.checkpoints.decision_window_opens_at ?? item?.decision_window?.opens_at ?? null,
    closesAt: game?.checkpoints.decision_window_closes_at ?? item?.decision_window?.closes_at ?? null,
  }
}
const windowText = (w: Window) => (w.opensAt && w.closesAt ? `the ${clock(w.opensAt)}–${clock(w.closesAt)} ET decision window` : 'the decision window')

/** The recorded policy-adjusted edge, exactly as recorded; never the stored `edge` column. */
export function leadEdge(item: DecisionItem | null): LeadEdge {
  const field = item ? recordedPolicyEdge(item) : null
  const value = field?.value ?? null
  const tests = item?.edge_metrics?.tests ?? []
  const own = tests.find((t) => t.metric === 'recorded_policy_adjusted_edge') ?? null
  const qualifier = !item || !item.selection || item.selection === '—'
    ? 'no decision recorded yet'
    : own ? `as recorded · ${testThresholdText(own)}`
      : tests[0] ? `as recorded · the policy tests ${tests[0].label.replace(/^Recorded /, '').toLowerCase()}`
        : 'as recorded at the decision'
  return { label: OFFICIAL_EDGE_LABEL, value, text: value == null ? NOT_AVAILABLE : valueText({ value, unit: 'probability_points' }), qualifier }
}

function failedTest(item: DecisionItem): EdgeTest | null {
  return item.edge_metrics?.tests.find((t) => t.passed === false) ?? null
}

/** Why a final evaluation declined, from its own recorded tests and reason. */
export function declineSentence(item: DecisionItem): string {
  const reason = item.reason ?? ''
  if (item.checkpoint_semantics?.class === 'NO_ROOM' || ALLOWANCE_DECLINE.test(reason)) return ALLOWANCE_SENTENCE
  if (/stale|not current|freshness/i.test(reason)) return 'Declined at the final check: the price was not current.'
  if (/eligib|TIER_[A-Z]_INELIGIBLE/i.test(reason)) return 'Declined at the final check: the game was not eligible under the policy.'
  if (/missing|unavailable|no (?:quote|price)/i.test(reason)) return 'Declined at the final check: the market was incomplete.'
  const t = failedTest(item)
  if (t) return `Declined at the final check: ${t.label.replace(/^Recorded /, 'recorded ')} was ${valueText(t)}; ${testThresholdText(t)}.`
  return 'Declined at the final check: it did not clear the policy thresholds.'
}

export interface LeadInput {
  item: DecisionItem | null
  game: CoverageGame | null
  /** The ledger position with the decision's position id, when the ledger has it. */
  ledgerPosition?: LedgerPosition | null
  /** Whether the ledger read has loaded (so "not found" can be told from "not loaded"). */
  ledgerLoaded?: boolean
  now: number
}

export function decisionLead({ item, game, ledgerPosition = null, ledgerLoaded = false, now }: LeadInput): DecisionLead {
  const w = windowOf(item, game)
  const startsAt = game?.starts_at ?? item?.starts_at ?? null
  const started = startsAt != null && Date.parse(startsAt) <= now
  const closed = w.closesAt != null && Date.parse(w.closesAt) <= now
  const edge = leadEdge(item)
  const hasSelection = Boolean(item?.selection && item.selection !== '—')
  const official: LeadSelection | null = item && hasSelection ? {
    selection: item.selection!,
    market: marketName(item.market),
    sportsbook: shortBook(item.sportsbook),
    american: item.american,
    quoteObservedAt: item.quote_observed_at,
    quoteAgeAtDecisionMinutes: item.quote_age_minutes,
    recordedAt: item.recommended_at,
  } : null
  const base = { official, edge, funded: null as LeadFunded | null, fundingPause: null as LeadFundingPause | null }

  // ---- FUNDED: the decision carries a ledger position.
  if (item?.position) {
    const p = item.position
    const funded: LeadFunded = {
      positionId: p.position_id,
      stake: ledgerPosition?.stake ?? p.stake,
      american: ledgerPosition?.entry?.american ?? item.american,
      sportsbook: shortBook(ledgerPosition?.entry?.sportsbook ?? item.sportsbook),
      ledgerConfirmed: ledgerPosition ? true : ledgerLoaded ? false : null,
      result: p.result,
      profitLoss: p.profit_loss,
    }
    const terms = `${money(funded.stake)} at ${american(funded.american)}${funded.sportsbook ? ` (${funded.sportsbook})` : ''}`
    if (p.status === 'SETTLED') {
      const verb = p.result === 'WIN' ? 'Won' : p.result === 'LOSS' ? 'Lost' : p.result === 'PUSH' ? 'Push' : 'Settled'
      return { ...base, funded, kind: 'FUNDED_SETTLED', authority: 'FUNDED', tone: (p.profit_loss ?? 0) > 0 ? 'green' : (p.profit_loss ?? 0) < 0 ? 'red' : 'gray',
        label: `Funded · settled ${verb} ${signedMoney(p.profit_loss)}`,
        sentence: `Paper wager settled: ${verb.toLowerCase()} ${signedMoney(p.profit_loss)} on ${terms}.` }
    }
    if (p.awaiting_settlement) {
      return { ...base, funded, kind: 'FUNDED_AWAITING', authority: 'FUNDED', tone: 'gold', label: 'Funded · settlement pending',
        sentence: `The game is final; the ${terms} paper wager is waiting for the settlement job.` }
    }
    return { ...base, funded, kind: 'FUNDED_OPEN', authority: 'FUNDED', tone: 'green', label: started ? 'Funded · in play' : 'Funded · open',
      sentence: started ? `In play: ${terms} paper wager; it settles after the final score.`
        : `Funded with ${terms} in the paper ledger; it settles after the final score.` }
  }

  const rec = item?.recommendation ?? 'NOT_YET_EVALUATED'
  const final = item?.phase === 'FINAL_PREGAME' && rec !== 'NOT_YET_EVALUATED'

  // ---- FINAL: the governed decision for this game.
  if (final && item) {
    // Shadow only: qualified, but MLB V18 funding is paused. Checked before the
    // generic QUALIFIED mapping so it can never read as "not funded yet".
    const pause = item.funding_pause
    if (pause && rec === 'QUALIFIED_BET') {
      return { ...base, kind: 'QUALIFIED_SHADOW_ONLY', authority: 'FINAL', tone: 'gray', label: SHADOW_ONLY_LABEL,
        sentence: SHADOW_ONLY_SENTENCE,
        fundingPause: { recordId: pause.record_id, contentSha256: pause.content_sha256, pausedAt: pause.paused_at,
          reason: pause.reason, status: pause.status, verified: pause.verified } }
    }
    const semantics = item.checkpoint_semantics
    if (semantics?.class === 'QUALIFIED_NOT_ACTIVATED') {
      return { ...base, kind: 'QUALIFIED', authority: 'FINAL', tone: 'gold', label: CHECKPOINT_LABEL.QUALIFIED_NOT_ACTIVATED,
        sentence: 'Qualified at the checkpoint, but activation is off, so no paper wager was funded.' }
    }
    if (semantics?.class === 'NO_ROOM' || (rec === 'DECLINED' && ALLOWANCE_DECLINE.test(item.reason ?? ''))) {
      return { ...base, kind: 'FINAL_DECLINE', authority: 'FINAL', tone: 'gray', label: 'Final · declined (no room in the shared $100)',
        sentence: ALLOWANCE_SENTENCE }
    }
    if (semantics?.class === 'NO_PRICE') {
      return { ...base, kind: 'FINAL_DECLINE', authority: 'FINAL', tone: 'red', label: 'Final · declined (no usable price)',
        sentence: 'Declined at the checkpoint: no usable two-sided price was stored.' }
    }
    if (semantics?.class === 'BLOCKED' || rec === 'BLOCKED') {
      return { ...base, kind: 'FINAL_BLOCKED', authority: 'FINAL', tone: 'red', label: 'Final · blocked',
        sentence: `Blocked by policy, so nothing was evaluated${item.plain_reasons?.[0] ? `: ${item.plain_reasons[0].replace(/\.$/, '')}` : ''}.` }
    }
    if (rec === 'QUALIFIED_BET') {
      return { ...base, kind: 'QUALIFIED', authority: 'FINAL', tone: started ? 'red' : 'green',
        label: started ? 'Qualified · not funded' : 'Qualified · not funded yet',
        sentence: started ? 'Qualified at the final check, but no paper position was recorded before the start.'
          : 'Qualified at the final check; no paper position is recorded in the ledger yet.' }
    }
    // NO_BET, and any other final disposition that did not qualify.
    return { ...base, kind: 'FINAL_DECLINE', authority: 'FINAL', tone: 'red', label: 'Final · declined', sentence: declineSentence(item) }
  }

  if (started) {
    return { ...base, kind: 'MISSED', authority: 'NONE', tone: 'gray', label: 'No final decision',
      sentence: 'The game started without a final evaluation, so no wager was made.' }
  }
  if (closed) {
    return { ...base, kind: 'WINDOW_CLOSED', authority: item && rec !== 'NOT_YET_EVALUATED' ? 'PROVISIONAL' : 'NONE', tone: 'gold',
      label: 'Window closed · final result not shown yet',
      sentence: `The decision window closed at ${clock(w.closesAt)} ET; no final evaluation is recorded yet.` }
  }

  // ---- PROVISIONAL: an early read that can change at the final check.
  if (item && rec !== 'NOT_YET_EVALUATED') {
    const phase = PHASE_NAME[item.phase] ?? 'Early read'
    const outcome = rec === 'QUALIFIED_BET' ? 'qualifies so far'
      : rec === 'PROVISIONAL_EDGE' ? 'shows an edge that is not confirmed yet'
        : rec === 'EXTREME_DISAGREEMENT' ? 'shows a model–market gap too wide to act on'
          : 'does not clear the policy so far'
    return { ...base, kind: 'PROVISIONAL', authority: 'PROVISIONAL', tone: 'gold', label: `${phase} · not final`,
      sentence: `The ${phase.toLowerCase()} ${outcome}; the official decision is made in ${windowText(w)}.` }
  }

  // ---- NOT EVALUATED: say what the decision is waiting on.
  const pendingEvidence = /PENDING_FINAL_EVIDENCE|two-sided/i.test(item?.reason ?? '')
  const windowOpen = w.opensAt != null && Date.parse(w.opensAt) <= now
  // the colour says whether anything stands in the way: orange = an input is missing, red = still missing
  // while the window is open (nothing can be bet), blue = everything is in and it simply decides on schedule
  const noForecast = Boolean(game && !game.model)
  const noPrice = !noForecast && (pendingEvidence || Boolean(game && game.price_state !== 'CURRENT_PRICE'))
  const waitingOn = noForecast ? 'FORECAST' as const : noPrice ? 'PRICE' as const : null
  let sentence: string
  let label: string
  let tone: LeadTone
  if (noForecast) {
    sentence = `Waiting for the forecast; the decision is made in ${windowText(w)}.`
    label = windowOpen ? 'Blocked · no model forecast' : 'Waiting on the model forecast'
    tone = windowOpen ? 'red' : 'orange'
  } else if (noPrice) {
    sentence = windowOpen
      ? `No complete same-book two-sided price yet; it is re-checked until ${clock(w.closesAt)} ET.`
      : `Waiting for a current two-sided sportsbook price; the decision is made in ${windowText(w)}.`
    label = windowOpen ? 'Blocked · no usable price yet' : 'Waiting on sportsbook prices'
    tone = windowOpen ? 'red' : 'orange'
  } else {
    sentence = windowOpen ? `Being evaluated now; the window closes at ${clock(w.closesAt)} ET.` : `Evaluated automatically in ${windowText(w)}.`
    label = windowOpen ? 'Deciding now' : 'Scheduled · inputs ready'
    tone = 'blue'
  }
  return { ...base, official: null, kind: 'NOT_EVALUATED', authority: 'NONE', tone, label, sentence, waitingOn }
}

/** The latest current price seen for the official selection's side, apart from the recorded one. */
export function latestSeen(item: DecisionItem | null, game: CoverageGame | null): LatestSeen | null {
  if (!item || !game || !item.selection) return null
  if (String(item.market).toUpperCase() !== 'MONEYLINE') return null
  const code = item.selection.toUpperCase().replace(/\s+ML$/, '').trim()
  const side = code === 'HOME' || code === game.home?.abbreviation?.toUpperCase() ? 'HOME'
    : code === 'AWAY' || code === game.away?.abbreviation?.toUpperCase() ? 'AWAY' : null
  if (!side || game.price_state !== 'CURRENT_PRICE') return null
  const best = game.best?.[side]
  if (!best || best.american == null) return null
  return { american: best.american, sportsbook: shortBook(best.sportsbook), observedAt: best.observed_at ?? null }
}

/** One countdown: the decision window (opens / closes) or the start. */
export function leadCountdown(item: DecisionItem | null, game: CoverageGame | null, now: number): { label: string; value: string } | null {
  const w = windowOf(item, game)
  const startsAt = game?.starts_at ?? item?.starts_at ?? null
  const status = String(game?.status ?? '').split('.').pop()!.toUpperCase()
  if (status === 'FINAL' || status === 'COMPLETED') return { label: 'Game', value: 'final' }
  if (startsAt && Date.parse(startsAt) <= now) return { label: 'Game', value: 'started' }
  const within = (iso: string | null) => (iso ? Date.parse(iso) > now : false)
  // Once the decision is final (or funded) the window no longer matters: count to the start.
  const decided = Boolean(item?.position) || (item?.phase === 'FINAL_PREGAME' && item.recommendation !== 'NOT_YET_EVALUATED')
  if (decided && startsAt) return { label: 'Starts in', value: span(startsAt, now) }
  if (within(w.opensAt)) return { label: 'Decision window opens in', value: span(w.opensAt!, now) }
  if (within(w.closesAt)) return { label: 'Decision window closes in', value: span(w.closesAt!, now) }
  if (startsAt) return { label: 'Starts in', value: span(startsAt, now) }
  return null
}

export function span(iso: string, now: number): string {
  const minutes = Math.max(0, Math.round((Date.parse(iso) - now) / 60_000))
  const h = Math.floor(minutes / 60)
  if (h >= 48) return `${Math.round(h / 24)} days`
  return h ? `${h} h ${String(minutes % 60).padStart(2, '0')} min` : `${minutes} min`
}

/** Short freshness chips: how old the recorded price was at the decision, and when it was recorded. */
export function freshness(lead: DecisionLead, latest: LatestSeen | null, now: number): string[] {
  const out: string[] = []
  const o = lead.official
  if (o) {
    out.push(o.quoteAgeAtDecisionMinutes != null ? `Price ${Math.round(o.quoteAgeAtDecisionMinutes)} min old at decision` : `Price age at decision ${NOT_AVAILABLE}`)
    out.push(o.recordedAt ? `Decision recorded ${etTime(o.recordedAt)}` : `Decision time ${NOT_AVAILABLE}`)
  }
  if (latest) {
    out.push(latest.observedAt && Number.isFinite(Date.parse(latest.observedAt))
      ? `Best current quote observed ${relative(latest.observedAt, now).replace(/^in /, '')}`
      : `Best current quote ${TIME_NOT_AVAILABLE}`)
  }
  return out
}
