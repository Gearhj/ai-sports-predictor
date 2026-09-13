import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'
import { beforeEach } from 'vitest'
import { clearCache, setCacheScope } from '../services/queryCache'

// Route components are intentionally code-split. Under a fully parallel test
// run, Vite may need more than Testing Library's one-second default to
// transform the first lazy route even though the route itself is healthy.
configure({ asyncUtilTimeout: 5_000 })

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'ResizeObserver', {
  writable: true,
  value: ResizeObserverMock,
})

// jsdom does not implement window.scrollTo; the ScrollToTop route helper calls
// it on every navigation. Provide a no-op so tests stay quiet.
Object.defineProperty(window, 'scrollTo', { writable: true, value: () => {} })

// ECharts uses a 2D canvas only to measure labels even when the production
// renderer is SVG. jsdom has no layout/canvas implementation, so provide the
// smallest deterministic surface needed by route tests.
Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get: () => 800,
})
Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get: () => 400,
})
Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
  configurable: true,
  value: () => ({
    measureText: (text: string) => ({ width: text.length * 7 }),
  }) as unknown as CanvasRenderingContext2D,
})

// The query cache is module-global by design: that is what lets a page render
// from cache after navigation. Tests need the opposite — each one starts from
// an empty cache, exactly as a fresh page load would, so a response mocked in
// one test cannot be served to the next.
beforeEach(() => {
  setCacheScope('anon')
  clearCache()
})
