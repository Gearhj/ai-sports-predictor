/**
 * THE FILM ROOM's maths (MMQB, plan page list). For every scored game that had a decision with a recorded market
 * price, AISP's "lean" is the side its model liked more than the market; the stand is how far apart they were (points
 * of win chance). Took a stand = 2+ points (AISP's edge bar). Each game lands in one of four rooms by stand and
 * result, so a week's tape reads as: when AISP disagreed with the market, who was right?
 */
export interface FilmGame {
  sport: string; game_id?: string | null; kickoff_utc?: string | null; slate_date?: string | null; home?: string | null; away?: string | null
  p_home_two_way: number | null; actual_outcome: string | null; final?: { home: number; away: number } | null
  decision?: { market_p_home: number | null; recommendation?: string; selected?: boolean; funded?: boolean; selected_side?: string | null } | null
  wager?: { side: string | null; result: string | null; stake: number | null } | null
}
export type Room = 'CALLED_IT' | 'FOOLED' | 'RODE_WIN' | 'RODE_LOSS'
export interface Clip { g: FilmGame; lean: 'HOME' | 'AWAY'; team: string; model: number; market: number; stand: number; won: boolean; room: Room; at: number }

export const STAND = 0.02
export const ROOM_LABEL: Record<Room, string> = { CALLED_IT: 'CALLED IT', FOOLED: 'FOOLED', RODE_WIN: 'RODE THE MARKET · WON', RODE_LOSS: 'RODE THE MARKET · LOST' }

export function clipsOf(games: FilmGame[], sinceMs: number, sport = 'ALL'): Clip[] {
  const out: Clip[] = []
  for (const g of games) {
    if (sport !== 'ALL' && g.sport !== sport) continue
    const m = g.decision?.market_p_home, p = g.p_home_two_way
    if (m == null || p == null || (g.actual_outcome !== 'HOME' && g.actual_outcome !== 'AWAY')) continue
    const at = Date.parse(g.kickoff_utc ?? g.slate_date ?? '')
    if (!Number.isFinite(at) || at < sinceMs) continue
    const lean = p >= m ? 'HOME' : 'AWAY'
    const model = lean === 'HOME' ? p : 1 - p, market = lean === 'HOME' ? m : 1 - m
    const stand = model - market, won = g.actual_outcome === lean
    const room: Room = stand >= STAND ? (won ? 'CALLED_IT' : 'FOOLED') : won ? 'RODE_WIN' : 'RODE_LOSS'
    out.push({ g, lean, team: (lean === 'HOME' ? g.home : g.away) ?? '?', model, market, stand, won, room, at })
  }
  return out.sort((a, b) => a.at - b.at)
}

/** The stands' record against what the market expected: wins, losses, and the market's expected wins on them. */
export function standLine(clips: Clip[]): { n: number; wins: number; expected: number; modelExpected: number } {
  const s = clips.filter((c) => c.stand >= STAND)
  return { n: s.length, wins: s.filter((c) => c.won).length, expected: s.reduce((a, c) => a + c.market, 0), modelExpected: s.reduce((a, c) => a + c.model, 0) }
}

/** The telestrator's one-line read of a clip. */
export function readClip(c: Clip): string {
  const pts = (c.stand * 100).toFixed(1)
  const m = Math.round(c.market * 100), a = Math.round(c.model * 100)
  if (c.room === 'CALLED_IT') return `AISP saw ${c.team} at ${a}% when the market said ${m}% — a ${pts}-point stand — and ${c.team} won. That is the job.`
  if (c.room === 'FOOLED') return `AISP took a ${pts}-point stand on ${c.team} (${a}% vs the market's ${m}%) and ${c.team} lost. One clip proves nothing; a season of these is the scorecard.`
  return `AISP and the market were within 2 points on ${c.team} (${a}% vs ${m}%), so there was no stand to take — ${c.won ? 'the lean won' : 'the lean lost'} and it says little about AISP.`
}
