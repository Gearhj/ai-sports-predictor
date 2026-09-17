import { describe, expect, it } from 'vitest'
import { pastTense } from './ShowBoard'

describe('pastTense', () => {
  it('never calls a started game pending', () => {
    expect(pastTense('Preview forecast pending · No current preview price held', true))
      .toBe('No preview forecast was published · no preview price was held')
    expect(pastTense('Preview forecast pending', false)).toBe('Preview forecast pending')
  })
})
