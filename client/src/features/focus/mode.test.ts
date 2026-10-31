import { describe, expect, it } from 'vitest'
import { FOCUS_PASSTHROUGH, readMode, setMode } from './mode'

describe('view mode', () => {
  it('defaults to full, remembers focus, and lets sign-in pages through', () => {
    localStorage.removeItem('aisp-view-mode')
    expect(readMode()).toBe('full')
    setMode('focus')
    expect(readMode()).toBe('focus')
    setMode('full')
    expect(readMode()).toBe('full')
    expect(FOCUS_PASSTHROUGH.test('/login')).toBe(true)
    expect(FOCUS_PASSTHROUGH.test('/account')).toBe(true)
    expect(FOCUS_PASSTHROUGH.test('/mma')).toBe(true)
    expect(FOCUS_PASSTHROUGH.test('/live')).toBe(false)
  })
})
