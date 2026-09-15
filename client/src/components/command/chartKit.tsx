/**
 * Shared plumbing for the command-center D3 charts: responsive width, a
 * keyboard-and-pointer tooltip, and reduced-motion detection.
 *
 * D3 owns the maths (scales, axes, shapes, transitions); React owns the DOM
 * nodes a reader focuses. Every mark that carries information is focusable,
 * announces its tooltip text, and has an equivalent row in the chart's table.
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

export function useWidth<T extends HTMLElement>(fallback = 720) {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(fallback)
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    setWidth(node.getBoundingClientRect().width || fallback)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width)
      if (next > 0) setWidth(next)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [fallback])
  return { ref, width }
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const listener = () => setReduced(query.matches)
    query.addEventListener?.('change', listener)
    return () => query.removeEventListener?.('change', listener)
  }, [])
  return reduced
}

export interface TipState { x: number; y: number; content: ReactNode }

export function useTooltip() {
  const [tip, setTip] = useState<TipState | null>(null)
  const frame = useRef<HTMLDivElement | null>(null)
  const show = (event: { clientX?: number; clientY?: number; currentTarget: Element }, content: ReactNode) => {
    const box = frame.current?.getBoundingClientRect()
    const target = event.currentTarget.getBoundingClientRect()
    const clientX = event.clientX ?? target.left + target.width / 2
    const clientY = event.clientY ?? target.top
    if (!box) return
    setTip({ x: clientX - box.left, y: clientY - box.top, content })
  }
  const hide = () => setTip(null)
  return { tip, show, hide, frame }
}

export function Tooltip({ tip, width }: { tip: TipState | null; width: number }) {
  if (!tip) return null
  const flip = tip.x > width - 240
  return (
    <div className="cc-tip" role="status" aria-live="polite"
      style={{ left: flip ? undefined : tip.x + 14, right: flip ? width - tip.x + 14 : undefined, top: Math.max(0, tip.y - 12) }}>
      {tip.content}
    </div>
  )
}

/** A collapsible text equivalent for a chart. */
export function ChartTable({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <details className="cc-table">
      <summary>Table view · {caption}</summary>
      <div className="cc-table__scroll">
        <table>
          <caption className="cc-sr">{caption}</caption>
          {children}
        </table>
      </div>
    </details>
  )
}

export function activate(handler: () => void) {
  return {
    onClick: handler,
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); handler() }
    },
  }
}

/**
 * Mount children only when they approach the viewport (or the browser is idle
 * with nothing else to do). Below-the-fold charts then cost nothing during the
 * first paint; the reserved height prevents layout jump.
 */
export function DeferUntilVisible({ children, minHeight = 240 }: { children: ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (visible) return
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') { setVisible(true); return }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '300px 0px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [visible])
  return <div ref={ref} style={visible ? undefined : { minHeight }}>{visible ? children : null}</div>
}
