/**
 * Small browser hooks the wall shares: media queries, element boxes, page
 * visibility, on-screen detection, and a guarded 2D-canvas probe.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
  const [matches, setMatches] = useState(get)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(query)
    const listener = () => setMatches(mql.matches)
    listener()
    mql.addEventListener?.('change', listener)
    return () => mql.removeEventListener?.('change', listener)
  }, [query])
  return matches
}

export const PHONE_QUERY = '(max-width: 760px)'
export const WIDE_QUERY = '(min-width: 1180px)'

/** An element's content box, kept current with ResizeObserver (jsdom: the fallback). */
export function useBox<T extends HTMLElement>(fallback = { width: 640, height: 480 }) {
  const ref = useRef<T | null>(null)
  const [box, setBox] = useState(fallback)
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const rect = node.getBoundingClientRect()
    if (rect.width > 0) setBox({ width: Math.round(rect.width), height: Math.round(rect.height) })
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0) setBox((prev) => (Math.round(width) === prev.width && Math.round(height) === prev.height ? prev : { width: Math.round(width), height: Math.round(height) }))
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return { ref, ...box }
}

/** False while the tab is hidden: every animation and timer on the wall stops. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return visible
}

/** Whether an element is on screen (true where IntersectionObserver is missing). */
export function useOnScreen<T extends Element>(ref: React.RefObject<T | null>): boolean {
  const [on, setOn] = useState(true)
  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => setOn(entries.some((e) => e.isIntersecting)), { threshold: 0.05 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [ref])
  return on
}

/**
 * A real 2D canvas, or nothing. jsdom's stub (and some locked-down browsers)
 * return a context without drawing methods; the wall then shows its text
 * tables instead of drawing.
 */
export function context2d(canvas: HTMLCanvasElement | null): CanvasRenderingContext2D | null {
  if (!canvas || typeof canvas.getContext !== 'function') return null
  try {
    const ctx = canvas.getContext('2d')
    return ctx && typeof (ctx as Partial<CanvasRenderingContext2D>).fillRect === 'function' ? ctx : null
  } catch {
    return null
  }
}

let probeResult: boolean | null = null
export function canDraw2d(): boolean {
  if (probeResult != null) return probeResult
  if (typeof document === 'undefined') return (probeResult = false)
  probeResult = context2d(document.createElement('canvas')) != null
  return probeResult
}

export const devicePixelRatioCapped = () => Math.min(2, Math.max(1, (typeof window !== 'undefined' && window.devicePixelRatio) || 1))

/** True if the page can use requestAnimationFrame. */
export const hasRaf = () => typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'

/**
 * The TV wall has no scrollbars: a column whose content is taller than its box
 * pages through it by itself, one screenful every `everyMs`, and returns to the
 * top after the last page. Reduced motion jumps instead of gliding. Stops while
 * the tab is hidden or the pointer is over the column (someone is reading it).
 */
export function useAutoScroll(ref: React.RefObject<HTMLElement | null>, enabled: boolean, opts: { everyMs?: number; reduced?: boolean; itemSelector?: string } = {}) {
  const everyMs = opts.everyMs ?? 8_000
  const reduced = Boolean(opts.reduced)
  const itemSelector = opts.itemSelector ?? null
  useEffect(() => {
    const node = ref.current
    if (!enabled || !node || typeof window === 'undefined') return
    let hovering = false
    const enter = () => { hovering = true }
    const leave = () => { hovering = false }
    node.addEventListener('pointerenter', enter)
    node.addEventListener('pointerleave', leave)
    const timer = window.setInterval(() => {
      if (hovering || document.visibilityState === 'hidden') return
      const room = node.scrollHeight - node.clientHeight
      if (room <= 4) { if (node.scrollTop) node.scrollTop = 0; return }
      const atEnd = node.scrollTop >= room - 4
      let top = atEnd ? 0 : Math.min(room, node.scrollTop + Math.max(40, node.clientHeight * 0.8))
      if (!atEnd && itemSelector) {
        // Page by whole items: the next one that is not fully in view starts the next page, so no card is cut in half.
        const base = node.getBoundingClientRect().top - node.scrollTop
        const viewBottom = node.scrollTop + node.clientHeight
        let items: HTMLElement[] = []
        try { items = [...node.querySelectorAll<HTMLElement>(itemSelector)] } catch { items = [] }
        const next = items
          .map((el) => { const r = el.getBoundingClientRect(); return { top: r.top - base, bottom: r.bottom - base } })
          .filter((b) => b.bottom > viewBottom + 2 && b.top > node.scrollTop + 2)
          .sort((a, b) => a.top - b.top)[0]
        if (next) top = Math.min(room, Math.max(0, next.top - 8))
      }
      if (typeof node.scrollTo === 'function') {
        try { node.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' }) } catch { node.scrollTop = top }
      } else node.scrollTop = top
    }, everyMs)
    return () => {
      window.clearInterval(timer)
      node.removeEventListener('pointerenter', enter)
      node.removeEventListener('pointerleave', leave)
      node.scrollTop = 0
    }
  }, [ref, enabled, everyMs, reduced, itemSelector])
}

/** The element currently fullscreen (null when none), kept current. */
export function useFullscreenElement(): Element | null {
  const [el, setEl] = useState<Element | null>(() => (typeof document !== 'undefined' ? document.fullscreenElement ?? null : null))
  useEffect(() => {
    if (typeof document === 'undefined') return
    const update = () => setEl(document.fullscreenElement ?? null)
    document.addEventListener('fullscreenchange', update)
    return () => document.removeEventListener('fullscreenchange', update)
  }, [])
  return el
}
