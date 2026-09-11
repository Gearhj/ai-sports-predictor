/**
 * Who a team is: the name they go by now, their colours, and their mark.
 *
 * THREE PROBLEMS THIS SOLVES, ALL OF THEM REAL IN THIS DATABASE
 * -------------------------------------------------------------
 * 1. STALE IDENTITY. `teams.name` was seeded from a historical franchise
 *    table, so LAR reads "St. Louis Rams", LAC reads "San Diego Chargers" and
 *    LV reads "Oakland Raiders" -- on 2026 fixtures. A fan sees that and stops
 *    trusting everything else on the page, correctly. Names here are the
 *    identity in force for the event being displayed, resolved by season, so a
 *    2015 Rams game can still read "St. Louis" while tonight's reads
 *    "Los Angeles".
 *
 * 2. COLLIDING ABBREVIATIONS. "WAS", "SF", "LAA/LA", "CHI", "NY*" and others
 *    exist in more than one league. Keying anything off the abbreviation alone
 *    guarantees a Nationals crest on a Commanders card eventually. Every
 *    lookup here takes a LEAGUE and an abbreviation, and the canonical id is
 *    `MLB:SF`, never `SF`.
 *
 * 3. NO ASSETS. There were none in the repo. See ``logoUrl`` below for what
 *    ships and what the constraints are.
 *
 * COLOURS ARE DATA, NOT DECORATION
 * --------------------------------
 * Each team's primary is used as an accent -- a rail, a bar, a ring -- and is
 * chosen to stay legible on a white panel. Where a team's true primary is a
 * pale colour (Miami's aqua, Pittsburgh's gold), the darker of its official
 * pair is used for anything carrying text, because a design that renders
 * "Steelers" in gold on white is unreadable however authentic the hex is.
 */

export type LeagueCode = 'MLB' | 'NFL' | 'NBA' | 'WNBA' | 'NHL' | 'UFC' | string

export interface TeamIdentity {
  /** `NFL:LAR`. The only safe key for a logo or a colour. */
  id: string
  league: LeagueCode
  abbreviation: string
  /** "Los Angeles Rams" -- the name in force for the season asked about. */
  name: string
  /** "Rams" -- for tight rows where the full name will not fit. */
  nickname: string
  /** "Los Angeles" */
  place: string
  primary: string
  secondary: string
  /** True when this row is a real identity rather than a generated fallback. */
  known: boolean
}

interface Franchise {
  place: string
  nickname: string
  primary: string
  secondary: string
  /** Earlier identities, newest first: [lastSeasonUsed, place, nickname]. */
  formerly?: [number, string, string][]
}

/* -- NFL ------------------------------------------------------------------
 * Colours are the clubs' published primary/secondary. Three franchises carry
 * `formerly` because this database still holds their pre-relocation names.
 */
const NFL: Record<string, Franchise> = {
  ARI: { place: 'Arizona', nickname: 'Cardinals', primary: '#97233F', secondary: '#000000' },
  ATL: { place: 'Atlanta', nickname: 'Falcons', primary: '#A71930', secondary: '#000000' },
  BAL: { place: 'Baltimore', nickname: 'Ravens', primary: '#241773', secondary: '#9E7C0C' },
  BUF: { place: 'Buffalo', nickname: 'Bills', primary: '#00338D', secondary: '#C60C30' },
  CAR: { place: 'Carolina', nickname: 'Panthers', primary: '#0085CA', secondary: '#101820' },
  CHI: { place: 'Chicago', nickname: 'Bears', primary: '#0B162A', secondary: '#C83803' },
  CIN: { place: 'Cincinnati', nickname: 'Bengals', primary: '#FB4F14', secondary: '#000000' },
  CLE: { place: 'Cleveland', nickname: 'Browns', primary: '#311D00', secondary: '#FF3C00' },
  DAL: { place: 'Dallas', nickname: 'Cowboys', primary: '#041E42', secondary: '#869397' },
  DEN: { place: 'Denver', nickname: 'Broncos', primary: '#0C2340', secondary: '#FB4F14' },
  DET: { place: 'Detroit', nickname: 'Lions', primary: '#0076B6', secondary: '#B0B7BC' },
  GB: { place: 'Green Bay', nickname: 'Packers', primary: '#203731', secondary: '#FFB612' },
  HOU: { place: 'Houston', nickname: 'Texans', primary: '#03202F', secondary: '#A71930' },
  IND: { place: 'Indianapolis', nickname: 'Colts', primary: '#002C5F', secondary: '#A2AAAD' },
  JAX: { place: 'Jacksonville', nickname: 'Jaguars', primary: '#006778', secondary: '#D7A22A' },
  KC: { place: 'Kansas City', nickname: 'Chiefs', primary: '#E31837', secondary: '#FFB81C' },
  LAC: {
    place: 'Los Angeles', nickname: 'Chargers', primary: '#0080C6', secondary: '#FFC20E',
    formerly: [[2016, 'San Diego', 'Chargers']],
  },
  LAR: {
    place: 'Los Angeles', nickname: 'Rams', primary: '#003594', secondary: '#FFA300',
    formerly: [[2015, 'St. Louis', 'Rams']],
  },
  LV: {
    place: 'Las Vegas', nickname: 'Raiders', primary: '#000000', secondary: '#A5ACAF',
    formerly: [[2019, 'Oakland', 'Raiders']],
  },
  MIA: { place: 'Miami', nickname: 'Dolphins', primary: '#008E97', secondary: '#FC4C02' },
  MIN: { place: 'Minnesota', nickname: 'Vikings', primary: '#4F2683', secondary: '#FFC62F' },
  NE: { place: 'New England', nickname: 'Patriots', primary: '#002244', secondary: '#C60C30' },
  NO: { place: 'New Orleans', nickname: 'Saints', primary: '#101820', secondary: '#D3BC8D' },
  NYG: { place: 'New York', nickname: 'Giants', primary: '#0B2265', secondary: '#A71930' },
  NYJ: { place: 'New York', nickname: 'Jets', primary: '#125740', secondary: '#000000' },
  PHI: { place: 'Philadelphia', nickname: 'Eagles', primary: '#004C54', secondary: '#A5ACAF' },
  PIT: { place: 'Pittsburgh', nickname: 'Steelers', primary: '#101820', secondary: '#FFB612' },
  SEA: { place: 'Seattle', nickname: 'Seahawks', primary: '#002244', secondary: '#69BE28' },
  SF: { place: 'San Francisco', nickname: '49ers', primary: '#AA0000', secondary: '#B3995D' },
  TB: { place: 'Tampa Bay', nickname: 'Buccaneers', primary: '#D50A0A', secondary: '#0A0A08' },
  TEN: { place: 'Tennessee', nickname: 'Titans', primary: '#0C2340', secondary: '#4B92DB' },
  WAS: {
    place: 'Washington', nickname: 'Commanders', primary: '#5A1414', secondary: '#FFB612',
    formerly: [[2021, 'Washington', 'Football Team']],
  },
}

/* -- MLB ------------------------------------------------------------------ */
const MLB: Record<string, Franchise> = {
  ARI: { place: 'Arizona', nickname: 'Diamondbacks', primary: '#A71930', secondary: '#E3D4AD' },
  ATL: { place: 'Atlanta', nickname: 'Braves', primary: '#13274F', secondary: '#CE1141' },
  BAL: { place: 'Baltimore', nickname: 'Orioles', primary: '#DF4601', secondary: '#000000' },
  BOS: { place: 'Boston', nickname: 'Red Sox', primary: '#BD3039', secondary: '#0C2340' },
  CHC: { place: 'Chicago', nickname: 'Cubs', primary: '#0E3386', secondary: '#CC3433' },
  CWS: { place: 'Chicago', nickname: 'White Sox', primary: '#27251F', secondary: '#C4CED4' },
  CIN: { place: 'Cincinnati', nickname: 'Reds', primary: '#C6011F', secondary: '#000000' },
  CLE: { place: 'Cleveland', nickname: 'Guardians', primary: '#00385D', secondary: '#E50022',
    formerly: [[2021, 'Cleveland', 'Indians']] },
  COL: { place: 'Colorado', nickname: 'Rockies', primary: '#33006F', secondary: '#C4CED4' },
  DET: { place: 'Detroit', nickname: 'Tigers', primary: '#0C2340', secondary: '#FA4616' },
  HOU: { place: 'Houston', nickname: 'Astros', primary: '#002D62', secondary: '#EB6E1F' },
  KC: { place: 'Kansas City', nickname: 'Royals', primary: '#004687', secondary: '#BD9B60' },
  LAA: { place: 'Los Angeles', nickname: 'Angels', primary: '#BA0021', secondary: '#003263' },
  LAD: { place: 'Los Angeles', nickname: 'Dodgers', primary: '#005A9C', secondary: '#EF3E42' },
  MIA: { place: 'Miami', nickname: 'Marlins', primary: '#00A3E0', secondary: '#41748D' },
  MIL: { place: 'Milwaukee', nickname: 'Brewers', primary: '#12284B', secondary: '#FFC52F' },
  MIN: { place: 'Minnesota', nickname: 'Twins', primary: '#002B5C', secondary: '#D31145' },
  NYM: { place: 'New York', nickname: 'Mets', primary: '#002D72', secondary: '#FF5910' },
  NYY: { place: 'New York', nickname: 'Yankees', primary: '#132448', secondary: '#C4CED3' },
  OAK: { place: 'Athletics', nickname: 'Athletics', primary: '#003831', secondary: '#EFB21E' },
  ATH: { place: 'Athletics', nickname: 'Athletics', primary: '#003831', secondary: '#EFB21E' },
  PHI: { place: 'Philadelphia', nickname: 'Phillies', primary: '#E81828', secondary: '#002D72' },
  PIT: { place: 'Pittsburgh', nickname: 'Pirates', primary: '#27251F', secondary: '#FDB827' },
  SD: { place: 'San Diego', nickname: 'Padres', primary: '#2F241D', secondary: '#FFC425' },
  SEA: { place: 'Seattle', nickname: 'Mariners', primary: '#0C2C56', secondary: '#005C5C' },
  SF: { place: 'San Francisco', nickname: 'Giants', primary: '#27251F', secondary: '#FD5A1E' },
  STL: { place: 'St. Louis', nickname: 'Cardinals', primary: '#C41E3A', secondary: '#0C2340' },
  TB: { place: 'Tampa Bay', nickname: 'Rays', primary: '#092C5C', secondary: '#8FBCE6' },
  TEX: { place: 'Texas', nickname: 'Rangers', primary: '#003278', secondary: '#C0111F' },
  TOR: { place: 'Toronto', nickname: 'Blue Jays', primary: '#134A8E', secondary: '#1D2D5C' },
  WSH: { place: 'Washington', nickname: 'Nationals', primary: '#AB0003', secondary: '#14225A' },
  WAS: { place: 'Washington', nickname: 'Nationals', primary: '#AB0003', secondary: '#14225A' },
}

const FRANCHISES: Record<string, Record<string, Franchise>> = { NFL, MLB }

/** Feeds spell the same club differently; normalise before looking up. */
const ALIASES: Record<string, Record<string, string>> = {
  MLB: { CHW: 'CWS', SDP: 'SD', SFG: 'SF', TBR: 'TB', KCR: 'KC', WSN: 'WSH', ATH: 'OAK' },
  NFL: { OAK: 'LV', SD: 'LAC', STL: 'LAR', LA: 'LAR', JAC: 'JAX', WSH: 'WAS', ARZ: 'ARI' },
}

/** A stable colour for a club we do not know, so unknown teams still differ. */
function fallbackColor(seed: string): string {
  let hash = 0
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) % 360
  }
  return `hsl(${hash} 42% 34%)`
}

export function normaliseAbbreviation(league: LeagueCode, abbreviation: string): string {
  const key = (abbreviation || '').trim().toUpperCase()
  return ALIASES[league]?.[key] ?? key
}

/**
 * The identity a team held in a given season.
 *
 * ``season`` is the season of the EVENT being rendered, not today. Omit it and
 * you get the current identity, which is what almost every surface wants; pass
 * it on a historical page so a 2015 fixture does not get retconned.
 */
export function teamIdentity(
  league: LeagueCode, abbreviation: string | null | undefined, season?: number | null,
): TeamIdentity {
  const abbr = normaliseAbbreviation(league, abbreviation ?? '')
  const franchise = FRANCHISES[league]?.[abbr]
  if (!franchise) {
    return {
      id: `${league}:${abbr || 'UNK'}`, league, abbreviation: abbr || '—',
      name: abbr || 'Unknown team', nickname: abbr || '—', place: '',
      primary: fallbackColor(`${league}:${abbr}`), secondary: '#94a3b8', known: false,
    }
  }
  let { place, nickname } = franchise
  if (season != null && franchise.formerly) {
    for (const [lastSeason, formerPlace, formerNickname] of franchise.formerly) {
      if (season <= lastSeason) {
        place = formerPlace
        nickname = formerNickname
        break
      }
    }
  }
  return {
    id: `${league}:${abbr}`, league, abbreviation: abbr,
    name: place && place !== nickname ? `${place} ${nickname}` : nickname,
    nickname, place,
    primary: franchise.primary, secondary: franchise.secondary, known: true,
  }
}

/**
 * Where a club's crest comes from, and the strings attached.
 *
 * The repository shipped no logo assets, so these are fetched from ESPN's
 * public team-logo CDN, addressed by canonical league + abbreviation. That is
 * the pragmatic choice for an internal research product and it is NOT a
 * licence: the marks are the clubs' registered trademarks, and anything
 * publicly distributed or commercial needs permission or its own artwork.
 *
 * Set `VITE_TEAM_LOGOS=off` to ship without them; every surface then draws the
 * generated colour mark instead, which is also what happens automatically if
 * an image fails to load or the machine is offline. No layout depends on the
 * image arriving.
 */
export const TEAM_LOGOS_ENABLED =
  String(import.meta.env.VITE_TEAM_LOGOS ?? 'on').toLowerCase() !== 'off'

const LOGO_LEAGUE_PATH: Record<string, string> = { NFL: 'nfl', MLB: 'mlb' }

export function logoUrl(identity: TeamIdentity): string | null {
  if (!TEAM_LOGOS_ENABLED || !identity.known) return null
  const path = LOGO_LEAGUE_PATH[identity.league]
  if (!path) return null
  return `https://a.espncdn.com/i/teamlogos/${path}/500/${identity.abbreviation.toLowerCase()}.png`
}

/**
 * A colour safe to put text on, or to run as a rail beside text.
 *
 * Some official primaries are near-white or near-yellow. Rather than quietly
 * substituting a house colour, this swaps to the club's own SECONDARY when the
 * primary is too light -- still their colour, just the half of it that works.
 */
export function inkColor(identity: TeamIdentity): string {
  return relativeLuminance(identity.primary) > 0.55 ? identity.secondary : identity.primary
}

function relativeLuminance(hex: string): number {
  const value = hex.replace('#', '')
  if (value.length !== 6) return 0
  const channel = (start: number) => {
    const srgb = parseInt(value.slice(start, start + 2), 16) / 255
    return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
}

/**
 * A team colour that survives a dark panel.
 *
 * Half the clubs in both leagues wear navy or black. On the featured matchup's
 * navy ground their true primary is invisible, and a bar you cannot see is
 * worse than no bar -- so this picks whichever of their own two colours reads
 * against dark, and lifts it only if BOTH are too dark to see. It is still
 * their colour; it is not a house palette standing in for one.
 */
export function onDarkColor(identity: TeamIdentity): string {
  const primary = relativeLuminance(identity.primary)
  const secondary = relativeLuminance(identity.secondary)
  const best = secondary > primary ? identity.secondary : identity.primary
  if (Math.max(primary, secondary) >= 0.16) return best
  return lighten(best, 0.42)
}

function lighten(hex: string, amount: number): string {
  const value = hex.replace('#', '')
  if (value.length !== 6) return hex
  const mix = (start: number) => {
    const channel = parseInt(value.slice(start, start + 2), 16)
    return Math.round(channel + (255 - channel) * amount).toString(16).padStart(2, '0')
  }
  return `#${mix(0)}${mix(2)}${mix(4)}`
}

/** Readable text colour to sit ON a team colour. */
export function contrastInk(hex: string): string {
  return relativeLuminance(hex) > 0.45 ? '#0b1a2b' : '#ffffff'
}
