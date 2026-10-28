/**
 * THE UNDERDOG REPORT maths (Scotty, 10-28: "how many times did the underdog win?"). Pure and tested.
 * A row is one finished game; the underdog is the side the closing no-vig market priced under 50%.
 */
export interface DogRow {
  sport: string
  date: string
  home: string | null
  away: string | null
  dog_team: string | null
  fav_team: string | null
  dog_p: number
  dog_best_decimal: number | null
  dog_won: boolean
  score: string
  aisp: { side: string; on_dog: boolean; result: string | null; stake: number; payout: number } | null
}

export interface Tally { n: number; won: number; rate: number; expected: number; flatProfit: number; flatBets: number }

/** Win count, actual rate, the market's own expected rate, and the result of $1 on every underdog at the best price. */
export function tally(rows: DogRow[]): Tally {
  let won = 0, exp = 0, profit = 0, bets = 0
  for (const r of rows) {
    if (r.dog_won) won += 1
    exp += r.dog_p
    if (r.dog_best_decimal && r.dog_best_decimal > 1) { bets += 1; profit += r.dog_won ? r.dog_best_decimal - 1 : -1 }
  }
  const n = rows.length
  return { n, won, rate: n ? won / n : 0, expected: n ? exp / n : 0, flatProfit: profit, flatBets: bets }
}

/** Underdog size bands by the market's closing chance, coin-flips first. */
export const BANDS: { key: string; label: string; lo: number; hi: number }[] = [
  { key: 'coin', label: 'Coin-flip dogs (45–50%)', lo: 0.45, hi: 0.5 },
  { key: 'small', label: 'Small dogs (40–45%)', lo: 0.4, hi: 0.45 },
  { key: 'mid', label: 'Clear dogs (33–40%)', lo: 0.33, hi: 0.4 },
  { key: 'big', label: 'Big dogs (20–33%)', lo: 0.2, hi: 0.33 },
  { key: 'long', label: 'Long shots (under 20%)', lo: 0, hi: 0.2 },
]
export const bandOf = (p: number) => BANDS.find((b) => p >= b.lo && p < b.hi) ?? BANDS[0]

/** AISP's paper positions split by whether it backed the underdog. */
export function aispSplit(rows: DogRow[]) {
  const side = (onDog: boolean) => {
    const ps = rows.filter((r) => r.aisp && r.aisp.on_dog === onDog && (r.aisp.result === 'WIN' || r.aisp.result === 'LOSS'))
    const w = ps.filter((r) => r.aisp!.result === 'WIN').length
    const staked = ps.reduce((a, r) => a + r.aisp!.stake, 0)
    const back = ps.reduce((a, r) => a + r.aisp!.payout, 0)
    return { n: ps.length, w, l: ps.length - w, net: back - staked, staked }
  }
  return { dog: side(true), fav: side(false) }
}

/** Biggest upsets: the longest-priced underdogs that won. */
export const upsets = (rows: DogRow[], k = 8) => rows.filter((r) => r.dog_won).sort((a, b) => a.dog_p - b.dog_p).slice(0, k)

/** American odds from a decimal price. */
export const americanOf = (d: number | null) => (d == null || d <= 1 ? null : d >= 2 ? Math.round((d - 1) * 100) : -Math.round(100 / (d - 1)))
