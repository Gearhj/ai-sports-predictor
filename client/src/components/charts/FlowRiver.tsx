/**
 * FlowRiver: the Mission Control Sankey as a reusable chart (2026-10-25, "we need more of that kind of graphics
 * throughout the platform"). Give it items, the columns to sort them through and what each item is worth; it
 * stacks every column, joins neighbours with bands as wide as the value that crossed, and traces: hover or tap
 * any node or band and its items light up as a bright sub-band through every other column, so you can follow
 * one group (say, every underdog) all the way to how it ended.
 */
import { useMemo, useState, type MouseEvent } from 'react'
import { useMeasuredWidth, useReducedMotion } from './d3Charts'
import './flowRiver.css'

export interface RiverColumn<T> { title: string; of: (item: T) => string; order?: string[] }

type RNode = { col: number; label: string; ids: number[]; value: number; x: number; y: number; h: number }
type RLink = { col: number; from: RNode; to: RNode; ids: number[]; value: number; sy: number; ty: number; w: number }

export function FlowRiver<T>({ items, columns, value = () => 1, colorOf, format = (v) => String(Math.round(v)), height = 340, label, describe }: {
  items: T[]
  columns: RiverColumn<T>[]
  value?: (item: T) => number
  colorOf: (col: number, label: string) => string
  format?: (v: number) => string
  height?: number
  label: string
  /** extra tooltip lines for a set of items (e.g. profit and loss) */
  describe?: (items: T[]) => string[]
}) {
  const [ref, measured] = useMeasuredWidth<HTMLDivElement>(900)
  const W = Math.max(560, measured - 2) // the well's border
  const H = height, top = 30, bottom = 8, nodeW = 12, gap = 10, padL = 6, padR = 230
  const reduced = useReducedMotion()
  const [trace, setTrace] = useState<{ ids: Set<number>; title: string; key: string } | null>(null)
  const [tip, setTip] = useState<{ x: number; y: number; title: string; lines: string[] } | null>(null)

  const layout = useMemo(() => {
    const vals = items.map((it) => Math.max(0, value(it)))
    const total = vals.reduce((a, b) => a + b, 0) || 1
    const colX = (i: number) => padL + (i * (W - padL - padR - nodeW)) / Math.max(1, columns.length - 1)
    const groups = columns.map((c) => {
      const m = new Map<string, number[]>()
      items.forEach((it, i) => { const k = c.of(it); m.set(k, [...(m.get(k) ?? []), i]) })
      const keys = [...(c.order ?? []).filter((k) => m.has(k)), ...[...m.keys()].filter((k) => !(c.order ?? []).includes(k)).sort((a, b) => sum(m.get(b)!, vals) - sum(m.get(a)!, vals))]
      return keys.map((k) => ({ label: k, ids: m.get(k)! }))
    })
    const k = Math.min(...groups.map((gs) => (H - top - bottom - gap * Math.max(0, gs.length - 1)) / total))
    const nodes: RNode[][] = groups.map((gs, ci) => {
      const span = total * k + gap * Math.max(0, gs.length - 1)
      let y = top + (H - top - bottom - span) / 2
      return gs.map((g) => {
        const v = sum(g.ids, vals)
        const n = { col: ci, label: g.label, ids: g.ids, value: v, x: colX(ci), y, h: Math.max(1.5, v * k) }
        y += v * k + gap
        return n
      })
    })
    const nodeAt = nodes.map((ns) => { const m = new Map<number, RNode>(); ns.forEach((n) => n.ids.forEach((i) => m.set(i, n))); return m })
    const links: RLink[] = []
    for (let c = 0; c < columns.length - 1; c += 1) {
      const m = new Map<string, RLink>()
      items.forEach((_, i) => {
        const from = nodeAt[c].get(i)!, to = nodeAt[c + 1].get(i)!
        const key = `${from.label}>${to.label}`
        const l = m.get(key) ?? { col: c, from, to, ids: [], value: 0, sy: 0, ty: 0, w: 0 }
        l.ids.push(i); l.value += vals[i]; m.set(key, l)
      })
      const ls = [...m.values()]
      ls.forEach((l) => { l.w = l.value * k })
      nodes[c].forEach((n) => { let y = n.y; ls.filter((l) => l.from === n).sort((a, b) => a.to.y - b.to.y).forEach((l) => { l.sy = y; y += l.w }) })
      nodes[c + 1].forEach((n) => { let y = n.y; ls.filter((l) => l.to === n).sort((a, b) => a.from.y - b.from.y).forEach((l) => { l.ty = y; y += l.w }) })
      links.push(...ls)
    }
    return { nodes, links, vals, k, total }
  }, [items, columns, value, W, H]) // eslint-disable-line react-hooks/exhaustive-deps

  const band = (x0: number, x1: number, sy: number, ty: number, w: number) => {
    const xm = (x0 + x1) / 2
    return `M${x0},${sy} C${xm},${sy} ${xm},${ty} ${x1},${ty} L${x1},${ty + w} C${xm},${ty + w} ${xm},${sy + w} ${x0},${sy + w} Z`
  }
  const tracedValue = (ids: number[]) => (trace ? ids.reduce((t, i) => t + (trace.ids.has(i) ? layout.vals[i] : 0), 0) : 0)
  const share = (v: number) => `${Math.round((v / layout.total) * 100)}%`
  const show = (e: MouseEvent, title: string, ids: number[], v: number) => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, title, lines: [`${format(v)} · ${share(v)} of all`, ...(describe ? describe(ids.map((i) => items[i])) : [])] })
  }
  const pin = (key: string, title: string, ids: number[]) => setTrace((t) => (t && t.key === key ? null : { ids: new Set(ids), title, key }))

  return (
    <div className="fr">
      <div ref={ref} className="fr__wrap" onMouseLeave={() => setTip(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={label}>
          <defs><filter id="fr-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
          {columns.map((c, i) => <text key={c.title} x={layout.nodes[i]?.[0]?.x ?? 0} y={14} className="fr__col">{c.title}</text>)}
          <g className={`fr__links${reduced ? '' : ' is-anim'}${trace ? ' is-tracing' : ''}`}>
            {layout.links.map((l, i) => {
              const x0 = l.from.x + nodeW, x1 = l.to.x
              const tv = tracedValue(l.ids)
              return (
                <g key={i} style={{ animationDelay: `${l.col * 0.35}s` }}>
                  <path d={band(x0, x1, l.sy, l.ty, l.w)} fill={colorOf(l.col + 1, l.to.label)} className="fr__band"
                    onMouseMove={(e) => show(e, `${l.from.label} → ${l.to.label}`, l.ids, l.value)} onClick={() => pin(`L${l.col}:${l.from.label}>${l.to.label}`, `${l.from.label} → ${l.to.label}`, l.ids)} />
                  {tv > 0 ? <path d={band(x0, x1, l.sy, l.ty, tv * layout.k)} fill={colorOf(l.col + 1, l.to.label)} className="fr__trace" pointerEvents="none" /> : null}
                </g>
              )
            })}
          </g>
          <g className="fr__nodes">
            {layout.nodes.flat().map((n) => {
              const tv = tracedValue(n.ids)
              return (
                <g key={`${n.col}-${n.label}`} onMouseMove={(e) => show(e, n.label, n.ids, n.value)} onClick={() => pin(`N${n.col}:${n.label}`, n.label, n.ids)} tabIndex={0} role="button"
                  aria-label={`${n.label}: ${format(n.value)}. Press to trace.`} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pin(`N${n.col}:${n.label}`, n.label, n.ids) } }}
                  className={trace && trace.key === `N${n.col}:${n.label}` ? 'is-pinned' : ''}>
                  <rect x={n.x} y={n.y} width={nodeW} height={n.h} rx={3} fill={colorOf(n.col, n.label)} filter="url(#fr-glow)" />
                  <text x={n.x + nodeW + 6} y={n.y + n.h / 2} dy="0.35em" className="fr__label">
                    <tspan className="fr__v">{format(n.value)}</tspan> {n.label}{trace && tv > 0 && tv < n.value - 1e-9 ? <tspan className="fr__tv"> · {format(tv)} traced</tspan> : null}
                  </text>
                </g>
              )
            })}
          </g>
        </svg>
        {tip ? <div className="fr__tip" style={{ left: Math.min(tip.x + 14, W - 250), top: tip.y + 12 }}><b>{tip.title}</b>{tip.lines.map((l, i) => <span key={i}>{l}</span>)}<small>{trace ? 'Click again to clear the trace' : 'Click to trace it through every column'}</small></div> : null}
      </div>
      <p className="fr__hint">{trace ? <>Tracing <b>{trace.title}</b> through every column. <button type="button" onClick={() => setTrace(null)}>Clear</button></> : 'Hover anything for the numbers. Click a block or band to trace it through every column.'}</p>
    </div>
  )
}

function sum(ids: number[], vals: number[]): number {
  return ids.reduce((t, i) => t + vals[i], 0)
}
