import { describe, expect, it } from 'vitest'
import { resolveDataMode } from './useApiResource'

describe('API data provenance', () => {
  it('never labels an unmarked successful payload as live', () => {
    expect(resolveDataMode({})).toBe('empty')
    expect(resolveDataMode({ is_demo: true })).toBe('demo')
    expect(resolveDataMode({ data_mode: 'demo' })).toBe('demo')
  })

  it('labels data live only when the API explicitly says so', () => {
    expect(resolveDataMode({ is_demo: false })).toBe('live')
    expect(resolveDataMode({ data_mode: 'live' })).toBe('live')
  })

  it('preserves an explicit empty state', () => {
    expect(resolveDataMode({ data_mode: 'empty' })).toBe('empty')
  })
})
