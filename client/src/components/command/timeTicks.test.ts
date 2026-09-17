import { describe, expect, it } from 'vitest'
import { timeTicks } from './MarketVisuals'

describe('timeTicks', () => {
  it('never repeats a label, even for observations in the same minute', () => {
    const at = new Date('2026-08-30T02:08:10Z')
    const ticks = timeTicks([at, new Date('2026-08-30T02:08:40Z')], 900)
    const labels = ticks.map((t) => t.label)
    expect(new Set(labels).size).toBe(labels.length)
    expect(labels).toEqual(['10:08 PM'])
  })

  it('adds the date when observations span Eastern days, and caps the count', () => {
    const ticks = timeTicks([new Date('2026-08-29T20:00:00Z'), new Date('2026-09-16T18:00:00Z')], 2000)
    expect(ticks.length).toBeLessThanOrEqual(6)
    expect(ticks.every((t) => /^[A-Z][a-z]{2} \d+, \d+:\d{2} [AP]M$/.test(t.label))).toBe(true)
  })

  it('uses fewer ticks on narrow charts', () => {
    const domain: [Date, Date] = [new Date('2026-09-16T12:00:00Z'), new Date('2026-09-16T20:00:00Z')]
    expect(timeTicks(domain, 280).length).toBeLessThanOrEqual(3)
  })
})
