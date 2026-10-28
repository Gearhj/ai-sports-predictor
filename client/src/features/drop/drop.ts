/**
 * THE DROP: once a day (noon ET) Home reveals AISP's strongest call of the day as a card pack.
 *
 * Which game, honestly, in this order:
 *   1. AISP BET          a paper position AISP actually placed today, the one with the largest edge
 *   2. BIGGEST EDGE      no bet yet: the largest model-over-fresh-market lean that is NOT over the owner's
 *                        10-point gap rule (a gap that big is a warning, never the headline)
 *   3. MODEL'S SUREST    no fresh market at all: the model's most confident favourite, labelled as only that
 * Games already started are skipped; a pick never pretends to be a wager signal.
 */
import { pickOf } from '../flow/slip'
import type { PulseGame } from '../pulse/pulseData'

export const DROP_HOUR_ET = 12
export const GAP_RULE_PTS = 10

export type DropKind = 'AISP_BET' | 'BIGGEST_EDGE' | 'MODEL_SUREST'
export interface Drop {
  kind: DropKind
  game: PulseGame
  side: 'HOME' | 'AWAY'
  model: number
  market: number | null
  edge: number | null
  price: number | null
  book: string | null
}
export const KIND_LABEL: Record<DropKind, string> = { AISP_BET: 'AISP BET', BIGGEST_EDGE: "AISP'S BIGGEST EDGE", MODEL_SUREST: "THE MODEL'S SUREST" }

export function chooseDrop(games: readonly PulseGame[], now = Date.now()): Drop | null {
  const ahead = games.filter((g) => Date.parse(g.startsAt) > now && g.phase !== 'FINAL')
  const views = ahead.map((g) => ({ g, p: pickOf(g) })).filter((x): x is { g: PulseGame; p: NonNullable<ReturnType<typeof pickOf>> } => x.p != null)
  const make = (kind: DropKind, x: (typeof views)[number]): Drop => ({ kind, game: x.g, side: x.p.side, model: x.p.model, market: x.p.market, edge: x.p.edge, price: x.p.price, book: x.p.book })
  const byEdge = (a: (typeof views)[number], b: (typeof views)[number]) => (b.p.edge ?? -99) - (a.p.edge ?? -99)
  const bets = views.filter((x) => x.p.source === 'position').sort(byEdge)
  if (bets.length) return make('AISP_BET', bets[0])
  const leans = views.filter((x) => x.p.edge != null && x.p.edge > 0 && x.p.edge <= GAP_RULE_PTS).sort(byEdge)
  if (leans.length) return make('BIGGEST_EDGE', leans[0])
  const sure = ahead.filter((g) => g.modelHome != null).map((g) => ({ g, side: (g.modelHome! >= 0.5 ? 'HOME' : 'AWAY') as 'HOME' | 'AWAY', p: Math.max(g.modelHome!, 1 - g.modelHome!) }))
    .sort((a, b) => b.p - a.p)[0]
  return sure ? { kind: 'MODEL_SUREST', game: sure.g, side: sure.side, model: sure.p, market: null, edge: null, price: null, book: null } : null
}

/** When today's drop opens (noon ET), as epoch ms, for an ET calendar day YYYY-MM-DD. */
export function dropTime(day: string): number {
  // Noon ET is 16:00Z in daylight time and 17:00Z in standard time; pick the one that formats back to 12.
  for (const h of [16, 17]) {
    const t = Date.parse(`${day}T${String(h).padStart(2, '0')}:00:00Z`)
    const etHour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false }).format(new Date(t)))
    if (etHour === DROP_HOUR_ET) return t
  }
  return Date.parse(`${day}T16:00:00Z`)
}
