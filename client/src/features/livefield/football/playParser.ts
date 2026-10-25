/**
 * Football play text -> a structured play the field can animate.
 *
 * ESPN's play-by-play carries no player tracking, so everything here comes
 * from the words of the play and its yardage: who threw, who was targeted,
 * short or deep, left / middle / right, yards, result. NFL text looks like
 * "(Shotgun) T.Lawrence pass deep left to B.Strange to HST 46 for 24 yards";
 * college text looks like "Jaxson Dart pass complete to Ja'Tavion Sanders for 15 yds".
 */

export type PlayKind = 'pass' | 'incomplete' | 'interception' | 'sack' | 'rush' | 'punt' | 'fieldgoal' | 'kickoff' | 'penalty' | 'nonplay' | 'other'

export interface ParsedPlay {
  kind: PlayKind
  formation: 'shotgun' | 'under-center' | 'pistol' | 'no-huddle' | null
  passer: string | null
  target: string | null
  rusher: string | null
  depth: 'short' | 'deep' | null
  side: 'left' | 'middle' | 'right' | null
  gap: string | null
  yards: number
  touchdown: boolean
  fumble: boolean
  good: boolean | null
  label: string
}

const NAME = String.raw`([A-Z][\w.'\-]*(?:\s(?:[A-Z][\w.'\-]*|Jr\.?|Sr\.?|II|III|IV)){0,2})`

const tidy = (n: string | null | undefined): string | null => (n ? n.replace(/\s+INTERCEPTED.*$/, '').replace(/\s+(to|for|at|and)$/i, '').replace(/(?<!(?:Jr|Sr))\.+$/, '').trim() || null : null)

export function parsePlay(type: string | null | undefined, rawText: string | null | undefined, statYards: number | null | undefined): ParsedPlay {
  // College text carries the clock and jersey numbers: "(00:11) Shotgun #10 J.Hoover pass complete short right to #80 C.Becker ..."
  const text = String(rawText ?? '').trim().replace(/^\(\d{1,2}:\d{2}\)\s*/, '').replace(/#\d+\s+/g, '')
  const t = String(type ?? '')
  const yards = typeof statYards === 'number' && Number.isFinite(statYards) ? statYards : yardsFromText(text)
  const base: ParsedPlay = { kind: 'other', formation: null, passer: null, target: null, rusher: null, depth: null, side: null, gap: null, yards, touchdown: /touchdown/i.test(text) && !/nullified/i.test(text), fumble: /fumble/i.test(text), good: null, label: '' }

  if (/timeout|two-minute|end (of )?(quarter|period|half|game)|end of|coin toss|official/i.test(t) || /^(timeout|end (quarter|half|game|of))/i.test(text) || /^official timeout/i.test(text)) {
    return { ...base, kind: 'nonplay', label: t || 'Stoppage' }
  }

  const formation = /\(shotgun\)|^shotgun\b|^no huddle[-, ]*shotgun/i.test(text) ? 'shotgun' : /\(pistol\)|^pistol\b/i.test(text) ? 'pistol' : /\(no huddle|^no huddle/i.test(text) ? 'no-huddle' : /\(under center\)/i.test(text) ? 'under-center' : null
  base.formation = formation
  const clean = text.replace(/^\([^)]*\)\s*/, '').replace(/^\([^)]*\)\s*/, '').replace(/^(?:No Huddle(?:[-,]\s*)?)?(?:Shotgun|Pistol)?\s*/i, '')

  // a penalty logged AFTER the touchdown (on the try or the kickoff) does not erase the touchdown
  const tdFirst = base.touchdown && text.search(/touchdown/i) >= 0 && text.search(/touchdown/i) < text.search(/PENALTY/)
  if (/penalty/i.test(t) || (/PENALTY/.test(text) && /no play|declined|enforced/i.test(text) && !tdFirst)) {
    return { ...base, kind: 'penalty', label: 'Penalty' }
  }
  if (/punt/i.test(t)) return { ...base, kind: 'punt', label: 'Punt' }
  if (/field goal/i.test(t) || /field goal/i.test(text)) return { ...base, kind: 'fieldgoal', good: /good/i.test(t + text) && !/no good|missed|blocked/i.test(t + text), label: 'Field goal' }
  if (/kickoff|kicks/i.test(t) || /\bkicks\b.*\byards\b/i.test(text) && /kickoff/i.test(t + text)) return { ...base, kind: 'kickoff', label: 'Kickoff' }

  const sack = /sack/i.test(t) || /\bsacked\b/i.test(text)
  if (sack) {
    const m = clean.match(new RegExp(`^${NAME}\\s+sacked`)) ?? text.match(new RegExp(`${NAME}\\s+sacked`))
    return { ...base, kind: 'sack', passer: m ? tidy(m[1]) : null, label: 'Sack' }
  }

  if (/\bpass\b/i.test(text) || /pass/i.test(t)) {
    const passerM = clean.match(new RegExp(`^${NAME}\\s+pass`))
    const dirM = clean.match(/pass\s+(?:incomplete\s+|complete\s+)?(?:(short|deep)\s+)?(left|middle|right)/i)
    const targetM = clean.match(new RegExp(String.raw`pass\s+(?:incomplete\s+|complete\s+)?(?:(?:short|deep)\s+)?(?:(?:left|middle|right)\s+)?(?:intended for|to)\s+${NAME}`))
      ?? clean.match(new RegExp(`\\bto\\s+${NAME}\\s+(?:for|to)\\b`))
    const picked = {
      passer: passerM ? tidy(passerM[1]) : null,
      target: targetM ? tidy(targetM[1]) : null,
      depth: dirM && dirM[1] ? (dirM[1].toLowerCase() as 'short' | 'deep') : null,
      side: dirM ? (dirM[2].toLowerCase() as 'left' | 'middle' | 'right') : null,
    }
    if (/intercept/i.test(text) || /intercept/i.test(t)) return { ...base, ...picked, kind: 'interception', label: 'Interception' }
    if (/incomplete/i.test(text) || /incompletion/i.test(t)) return { ...base, ...picked, kind: 'incomplete', label: 'Incomplete' }
    return { ...base, ...picked, kind: 'pass', label: base.touchdown ? 'Touchdown pass' : 'Complete' }
  }

  const rushM = clean.match(new RegExp(`^${NAME}\\s+(?:rush(?:es)?|runs?|scrambles?|kneel|(?:left|right|up the middle|middle)\\b)`))
  if (/rush|run\b|scramble|kneel/i.test(t) || rushM) {
    const gapM = clean.match(/(left end|left tackle|left guard|up the middle|middle|right guard|right tackle|right end|left|right)/i)
    return { ...base, kind: 'rush', rusher: rushM ? tidy(rushM[1]) : null, gap: gapM ? gapM[1].toLowerCase() : null, label: base.touchdown ? 'Touchdown run' : 'Run' }
  }
  return { ...base, kind: 'other', label: t || 'Play' }
}

function yardsFromText(text: string): number {
  const gain = text.match(/for\s+(-?\d+)\s+(?:yards?|yds?)/i)
  if (gain) return Number(gain[1])
  const loss = text.match(/for a loss of\s+(\d+)/i)
  if (loss) return -Number(loss[1])
  return /no gain/i.test(text) ? 0 : 0
}

export interface RosterPlayer { id: string; team: string | null; first: string; last: string; jersey?: string | null; headshot: string; pos?: string | null; ranks?: Record<string, number>; stats?: Record<string, Record<string, string>> }

/** "T.Lawrence", "Bri.Thomas" or "Jaxson Dart" -> the roster entry, if the box score knows the player. */
export function findPlayer(name: string | null | undefined, players: readonly RosterPlayer[] | undefined, team?: string | null): RosterPlayer | null {
  if (!name || !players?.length) return null
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, '')
  const pool = team ? players.filter((p) => p.team === team) : players
  const dot = name.match(/^([A-Za-z]+)\.\s*(.+)$/)
  if (dot) {
    const first = norm(dot[1])
    const last = norm(dot[2].replace(/\s+(jr|sr|ii|iii|iv)\.?$/i, ''))
    const hit = pool.filter((p) => norm(p.last).replace(/(jr|sr|ii|iii|iv)$/, '') === last && norm(p.first).startsWith(first))
    return hit.length === 1 ? hit[0] : hit[0] ?? null
  }
  const full = norm(name)
  return pool.find((p) => norm(`${p.first}${p.last}`) === full) ?? pool.find((p) => norm(p.last) === norm(name.split(/\s+/).slice(-1)[0] ?? '')) ?? null
}
