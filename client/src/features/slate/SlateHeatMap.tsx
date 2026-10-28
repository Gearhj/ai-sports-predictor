/**
 * THE SLATE HEAT MAP (Games, plan page list): tonight as one D3 treemap. Sports are blocks; each game is a tile whose
 * SIZE is AISP's interest in it (its paper bet counts most, then the size of a fresh model-over-market edge, then a
 * base share) and whose colour is the sport. Live games breathe. YOUR calls (Arena or locked My Picks) glow emerald;
 * where your side and AISP's differ, the tile cracks. Click a tile to open the game. Model estimate and sportsbook
 * price, never a wager signal.
 */
import * as d3 from 'd3'
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Crest } from '../flow/Crest'

const SPORT_ICON: Record<string, string> = { NFL: '🏈', CFB: '🏈', NBA: '🏀', NHL: '🏒', MLB: '⚾' }
const SPORT_NAME: Record<string, string> = { NFL: 'NFL', CFB: 'College Football', NBA: 'NBA', NHL: 'NHL', MLB: 'MLB' }
import { pickOf } from '../flow/slip'
import { useArena } from '../arena/arenaStore'
import { usePreviewPicks } from '../../services/previewPicks'
import { etClock, pct, type PulseGame } from '../pulse/pulseData'
import './heatmap.css'

const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
type Side = 'HOME' | 'AWAY'

/** Over the owner's 10-point rule a model-vs-market gap is a warning (record-only), never "interest". */
export const GAP_RULE = 10
/** A tile's weight: AISP bet 4; otherwise 1 + a fresh positive edge (up to the 10-point rule) / 3. A gap over the rule gets the base 1. */
export function weightOf(g: PulseGame): number {
  if (g.position) return 4
  const p = pickOf(g)
  const edge = p?.edge != null && p.edge > 0 && p.edge <= GAP_RULE ? p.edge : 0
  return 1 + edge / 3
}

interface Tile { g: PulseGame; x0: number; y0: number; x1: number; y1: number }

export function SlateHeatMap({ games, onOpen }: { games: PulseGame[]; onOpen: (g: PulseGame) => void }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [w, setW] = useState(900)
  const hasGames = games.length > 0
  useLayoutEffect(() => {
    const el = ref.current // only on screen once there are games, so re-attach when they arrive
    if (!el) return undefined
    const ro = new ResizeObserver(() => setW(el.clientWidth))
    ro.observe(el); setW(el.clientWidth)
    return () => ro.disconnect()
  }, [hasGames])
  const arena = useArena(true)
  const picks = usePreviewPicks()
  const H = Math.max(320, Math.min(520, 80 + games.length * 28))
  const { tiles, groups } = useMemo(() => {
    type Node = { name: string; g?: PulseGame; value?: number; children?: Node[] }
    const bySport = d3.groups(games, (g) => g.sport)
    const root = d3.hierarchy<Node>({ name: 'slate', children: bySport.map(([s, list]) => ({ name: s, children: list.map((g) => ({ name: g.id, g, value: weightOf(g) })) })) })
      .sum((d) => d.value ?? 0).sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
    d3.treemap<Node>().size([w, H]).paddingInner(4).paddingOuter(2).paddingTop((d) => (d.depth === 1 ? 32 : 2)).round(true)(root)
    const rect = root as d3.HierarchyRectangularNode<Node>
    return {
      tiles: rect.leaves().map((l) => ({ g: l.data.g!, x0: l.x0, y0: l.y0, x1: l.x1, y1: l.y1 })) as Tile[],
      groups: (rect.children ?? []).map((c) => ({ sport: c.data.name, x0: c.x0, y0: c.y0, x1: c.x1, y1: c.y1, n: c.children?.length ?? 0 })),
    }
  }, [games, w, H])

  const maxArea = Math.max(1, ...tiles.map((t) => (t.x1 - t.x0) * (t.y1 - t.y0)))
  if (!games.length) return null
  const mineOf = (g: PulseGame): Side | null => arena.calls.find((c) => c.game_id === g.id && c.mode === 'LIVE')?.side ?? picks.find((p) => p.game_id === g.id && p.status === 'locked')?.side ?? null
  return (
    <section className="sl-card hmap" aria-label="Slate heat map">
      <header className="hmap__head">
        <h2>The Slate Heat Map</h2>
        <span>Bigger tile = more AISP interest (its bet, then its edge) · <i className="hmap__k hmap__k--you" /> your call · <i className="hmap__k hmap__k--clash" /> you vs AISP · live games breathe</span>
      </header>
      <div className="hmap__board" ref={ref} style={{ height: H }}>
        {/* Owner 10-27: each sport reads as its own block -- a coloured header band with its icon and name, and a tinted frame. */}
        {groups.map((gr) => (
          <div key={gr.sport} className="hmap__sport" style={{ left: gr.x0, top: gr.y0, width: gr.x1 - gr.x0, height: gr.y1 - gr.y0, '--sc': COLOR[gr.sport] ?? '#94a3b8' } as CSSProperties}>
            <span className="hmap__group"><i aria-hidden="true">{SPORT_ICON[gr.sport] ?? '●'}</i>{gr.x1 - gr.x0 < 240 ? gr.sport : SPORT_NAME[gr.sport] ?? gr.sport}{gr.x1 - gr.x0 >= 150 ? <small>{gr.n} game{gr.n === 1 ? '' : 's'}</small> : null}</span>
          </div>
        ))}
        {tiles.map((t) => {
          const g = t.g
          const p = pickOf(g)
          const mine = mineOf(g)
          const clash = Boolean(mine && p && mine !== p.side)
          const live = g.phase === 'LIVE' || g.phase === 'STARTED'
          const tw = t.x1 - t.x0, th = t.y1 - t.y0
          const big = tw > 170 && th > 90
          // Owner 10-27: bigger tiles carry bigger type and crests (up to about double), shrinking with the edge.
          const k = Math.max(0.9, Math.min(2, 1 + Math.sqrt((tw * th) / maxArea), th / 72, tw / 160))
          const side = p?.side === 'HOME' ? g.home : p?.side === 'AWAY' ? g.away : null
          return (
            <button key={g.id} type="button" onClick={() => onOpen(g)} title={`${g.away.abbr} @ ${g.home.abbr} · ${etClock(g.startsAt)} ET${p?.edge != null ? ` · edge ${p.edge.toFixed(1)} pts` : ''}`}
              className={`hmap__tile${live ? ' is-live' : ''}${g.phase === 'FINAL' ? ' is-final' : ''}${mine ? ' is-mine' : ''}${clash ? ' is-clash' : ''}${g.position ? ' has-bet' : ''}`}
              style={{ left: t.x0, top: t.y0, width: tw, height: th, '--sc': COLOR[g.sport] ?? '#94a3b8', '--k': k } as CSSProperties}>
              <span className="hmap__crests"><Crest sport={g.sport} abbr={g.away.abbr} size={Math.round(24 * k)} /><Crest sport={g.sport} abbr={g.home.abbr} size={Math.round(24 * k)} /></span>
              <b className="hmap__name">{g.away.abbr} @ {g.home.abbr}</b>
              {th > 52 ? <small className="hmap__line">{live && g.score ? `${g.score.away}–${g.score.home} · ${g.score.detail}` : g.phase === 'FINAL' && g.score ? `Final ${g.score.away}–${g.score.home}` : `${etClock(g.startsAt)} ET`}</small> : null}
              {p?.edge != null && p.edge > GAP_RULE ? <small className="hmap__warn" title="Model and market disagree by more than 10 points: your record-only rule treats this as a warning, not an edge">⚠ {p.edge.toFixed(0)}-pt gap · warning</small>
                : big && side && p ? <small className="hmap__pick">AISP: {side.abbr} {pct(p.model)}{p.edge != null ? ` · ${p.edge >= 0 ? '+' : ''}${p.edge.toFixed(1)} pts` : ''}</small> : null}
              {g.position ? <em className="hmap__bet">AISP BET</em> : null}
              {clash ? <svg className="hmap__crack" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M58 0 L48 22 L62 38 L44 58 L57 74 L46 100" /></svg> : null}
            </button>
          )
        })}
      </div>
    </section>
  )
}
