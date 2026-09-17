import { describe, expect, it } from 'vitest'
import { selectionLabel } from './SettledWagerHistory'

describe('selectionLabel', () => {
  it('names the team for a bare side', () => {
    expect(selectionLabel('AWAY', 'DEN at KC')).toBe('DEN ML')
    expect(selectionLabel('HOME', 'DEN @ KC')).toBe('KC ML')
    expect(selectionLabel('WSH ML', 'PHI at WSH')).toBe('WSH ML')
    expect(selectionLabel('AWAY', null)).toBe('AWAY')
  })
})
