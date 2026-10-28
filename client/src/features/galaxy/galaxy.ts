/**
 * THE CALIBRATION GALAXY's maths (Performance, plan page list). Every scored game becomes a star seen from the
 * favourite's side: "said" = the favourite's two-way probability, "hit" = the favourite won. Stars cluster into
 * 5-point bands; a band's centre sits at (average said, share that happened). On the diagonal = honest; below it =
 * overconfident; above it = underconfident. The same games are scored from the market's price when one was recorded,
 * so the two galaxies are always compared on the same games. Ties (NFL) are left out: a two-way favourite can't win one.
 */
export interface GalaxyGame { sport: string; game_id?: string | null; slate_date?: string | null; p_home_two_way: number | null; actual_outcome: string | null; decision?: { market_p_home: number | null } | null; home?: string | null; away?: string | null; kickoff_utc?: string | null }
export interface Star { sport: string; said: number; hit: boolean; label: string }
export interface Band { lo: number; hi: number; n: number; said: number; happened: number; stars: Star[] }
export type Source = 'MODEL' | 'MARKET'

export const BAND_EDGES = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 1.0001]

export function starsOf(games: GalaxyGame[], source: Source, sport = 'ALL', pairedOnly = false): Star[] {
  const out: Star[] = []
  for (const g of games) {
    if (sport !== 'ALL' && g.sport !== sport) continue
    if (g.actual_outcome !== 'HOME' && g.actual_outcome !== 'AWAY') continue
    const market = g.decision?.market_p_home ?? null
    if (pairedOnly && (market == null || g.p_home_two_way == null)) continue
    const p = source === 'MODEL' ? g.p_home_two_way : market
    if (p == null || !Number.isFinite(p) || p <= 0 || p >= 1) continue
    const favHome = p >= 0.5
    out.push({ sport: g.sport, said: favHome ? p : 1 - p, hit: (g.actual_outcome === 'HOME') === favHome, label: `${g.away ?? '?'} @ ${g.home ?? '?'}` })
  }
  return out
}

export function bandsOf(stars: Star[]): Band[] {
  const bands: Band[] = []
  for (let i = 0; i < BAND_EDGES.length - 1; i++) {
    const lo = BAND_EDGES[i], hi = BAND_EDGES[i + 1]
    const s = stars.filter((x) => x.said >= lo && x.said < hi)
    if (!s.length) continue
    bands.push({ lo, hi, n: s.length, said: s.reduce((a, x) => a + x.said, 0) / s.length, happened: s.filter((x) => x.hit).length / s.length, stars: s })
  }
  return bands
}

/** Average (happened − said) over every star, in points: negative = overconfident. Plus the strongest band's reading. */
export function verdictOf(bands: Band[]): { n: number; gap: number; confident: Band | null } {
  const n = bands.reduce((a, b) => a + b.n, 0)
  if (!n) return { n: 0, gap: 0, confident: null }
  const gap = bands.reduce((a, b) => a + b.n * (b.happened - b.said), 0) / n * 100
  const strong = bands.filter((b) => b.lo >= 0.7)
  const confident = strong.length ? { lo: strong[0].lo, hi: strong[strong.length - 1].hi, n: strong.reduce((a, b) => a + b.n, 0),
    said: strong.reduce((a, b) => a + b.n * b.said, 0) / strong.reduce((a, b) => a + b.n, 0),
    happened: strong.reduce((a, b) => a + b.n * b.happened, 0) / strong.reduce((a, b) => a + b.n, 0), stars: [] } : null
  return { n, gap, confident }
}

/** Plain-words reading of a gap in points. */
export function honesty(gap: number): string {
  if (Math.abs(gap) < 2) return 'honest'
  return gap < 0 ? 'overconfident' : 'underconfident'
}
