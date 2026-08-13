import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

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
