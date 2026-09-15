/**
 * Collision-aware layout for "Today on one axis".
 *
 * A 15-game MLB slate puts dozens of milestones inside a few hours, so one
 * lane per sport overprints. Two steps fix that without moving any time:
 *
 * 1. CLUSTER. Milestones of the same kind and status within a few minutes of
 *    each other become one mark that remembers every underlying event and its
 *    exact time ("4 games · 6:40 PM"). Windows cluster only when both their
 *    openings and closings coincide.
 *
 * 2. PACK. Within a lane, each band (milestones, windows, game starts) is
 *    packed into sub-rows: a mark takes the first row where it does not touch
 *    the previous mark's pixel extent, up to the band's row limit. A busy
 *    lane grows taller; a quiet lane stays one row.
 */
import type { AgendaEvent, AgendaKind, AgendaStatus } from './agenda'
import type { SportCode } from './commandCenter'

export type Band = 'mile' | 'win' | 'game'

export const BAND_OF: Record<AgendaKind, Band> = {
  forecast: 'mile', position: 'mile', settlement: 'mile', collection: 'win', decision: 'win', game: 'game',
}
export const MAX_ROWS: Record<Band, number> = { mile: 2, win: 3, game: 3 }
export const ROW_HEIGHT: Record<Band, number> = { mile: 16, win: 12, game: 22 }
export const CLUSTER_MINUTES = 6
const MIN = 60_000
const GAP_PX = 5

export interface Cluster {
  key: string
  sport: SportCode
  kind: AgendaKind
  status: AgendaStatus
  band: Band
  at: number
  until?: number
  events: AgendaEvent[]
}

export interface PlacedCluster extends Cluster {
  x0: number
  x1: number
  row: number
  label: string | null
}

export interface LaneLayout {
  sport: SportCode
  clusters: PlacedCluster[]
  rows: Record<Band, number>
  height: number
}

const et = (ms: number) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).format(ms)

export function clusterEvents(events: AgendaEvent[]): Cluster[] {
  const out: Cluster[] = []
  const sorted = [...events].sort((a, b) => a.at - b.at || a.matchup.localeCompare(b.matchup))
  for (const e of sorted) {
    const band = BAND_OF[e.kind]
    const near = out.find((c) => c.sport === e.sport && c.kind === e.kind && c.status === e.status
      && Math.abs(c.at - e.at) <= CLUSTER_MINUTES * MIN
      && (band !== 'win' || Math.abs((c.until ?? c.at) - (e.until ?? e.at)) <= CLUSTER_MINUTES * MIN))
    if (near) {
      near.events.push(e)
      if (e.until != null) near.until = Math.max(near.until ?? e.until, e.until)
    } else {
      out.push({ key: `${e.sport}|${e.kind}|${e.status}|${e.at}`, sport: e.sport, kind: e.kind, status: e.status, band, at: e.at, until: e.until, events: [e] })
    }
  }
  return out
}

/** The visible label a cluster carries, or null for a lone symbol. */
export function clusterLabel(cluster: Cluster, narrow = false): string | null {
  const n = cluster.events.length
  if (cluster.kind === 'game') {
    if (n === 1) return narrow ? null : cluster.events[0].matchup
    return narrow ? `${n} · ${et(cluster.at)}` : `${n} games · ${et(cluster.at)}`
  }
  return n > 1 ? String(n) : null
}

/** Approximate rendered width of a label at the timeline's 11px font. */
export const labelWidth = (text: string | null) => (text ? Math.ceil(text.length * 6.1) + 14 : 0)

export function layoutLane(sport: SportCode, clusters: Cluster[], x: (ms: number) => number, opts: { narrow?: boolean; right?: number } = {}): LaneLayout {
  const rowsEnd: Record<Band, number[]> = { mile: [], win: [], game: [] }
  const placed: PlacedCluster[] = []
  const ordered = clusters.filter((c) => c.sport === sport).sort((a, b) => a.at - b.at)
  for (const c of ordered) {
    const cx = x(c.at)
    const label = clusterLabel(c, opts.narrow)
    let x0: number
    let x1: number
    if (c.kind === 'decision') { x0 = cx; x1 = Math.max(cx + 4, x(c.until ?? c.at)) }
    else if (c.kind === 'collection') { x0 = cx - 3; x1 = cx + 3 + (label ? labelWidth(label) - 8 : 0) }
    else if (c.kind === 'game' && label) {
      // A labelled game start is a pill anchored at the start time; it shifts left only to stay on the chart.
      const w = labelWidth(label)
      x0 = cx - 7
      x1 = x0 + w
      if (opts.right != null && x1 > opts.right) { x1 = opts.right; x0 = x1 - w }
    } else {
      x0 = cx - 7
      x1 = cx + 7 + (label ? labelWidth(label) - 10 : 0)
      // A lone game's name flips to the left of its mark rather than running off the chart.
      if (label && opts.right != null && x1 > opts.right) { x0 = cx - 7 - (labelWidth(label) - 10); x1 = cx + 7 }
    }
    const ends = rowsEnd[c.band]
    let row = ends.findIndex((end) => end + GAP_PX <= x0)
    if (row === -1) {
      if (ends.length < MAX_ROWS[c.band]) row = ends.length
      else row = ends.indexOf(Math.min(...ends))
    }
    ends[row] = Math.max(ends[row] ?? Number.NEGATIVE_INFINITY, x1)
    placed.push({ ...c, x0, x1, row, label })
  }
  const rows: Record<Band, number> = {
    mile: Math.max(1, rowsEnd.mile.length), win: Math.max(1, rowsEnd.win.length), game: Math.max(1, rowsEnd.game.length),
  }
  const height = placed.length === 0 ? 26
    : 8 + rows.mile * ROW_HEIGHT.mile + rows.win * ROW_HEIGHT.win + rows.game * ROW_HEIGHT.game + 4
  return { sport, clusters: placed, rows, height }
}

/** Vertical centre of a band row inside its lane. */
export function rowY(layout: LaneLayout, band: Band, row: number): number {
  const top = 6
  const mile = layout.rows.mile * ROW_HEIGHT.mile
  const win = layout.rows.win * ROW_HEIGHT.win
  if (band === 'mile') return top + row * ROW_HEIGHT.mile + ROW_HEIGHT.mile / 2
  if (band === 'win') return top + mile + row * ROW_HEIGHT.win + ROW_HEIGHT.win / 2
  return top + mile + win + row * ROW_HEIGHT.game + ROW_HEIGHT.game / 2
}
