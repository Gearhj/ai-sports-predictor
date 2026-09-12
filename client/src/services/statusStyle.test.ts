import { describe, expect, it } from 'vitest'
import { closedStyle, edgeDescription, hasStarted, opportunityStyle } from './statusStyle'

/**
 * These cover behaviour, not colour constants: which side a reading describes,
 * and whether a started game's band is still presented as actionable.
 */

describe('naming the side a reading describes', () => {
  it('names the away team when the published edge is on the away side', () => {
    // The real case from the screenshot: STANDARD EDGE beside a negative
    // home-side number. Both were correct; neither said whose they were.
    expect(edgeDescription({
      edgeSide: 'away', modelEdgePoints: 5.83, homeEdgePoints: -5.3,
      homeAbbrev: 'TOR', awayAbbrev: 'BAL',
    })).toEqual({ text: 'BAL +5.8 pp vs market', side: 'BAL' })
  })

  it('names the home team when the edge is on the home side', () => {
    expect(edgeDescription({
      edgeSide: 'home', modelEdgePoints: 8.4, homeEdgePoints: 8.4,
      homeAbbrev: 'SF', awayAbbrev: 'SD',
    })).toEqual({ text: 'SF +8.4 pp vs market', side: 'SF' })
  })

  it('falls back to the home-side figure, still named, when no side is published', () => {
    // NFL publishes no band and no side. The number is the home side's, and
    // saying so is better than a bare signed value.
    expect(edgeDescription({
      edgeSide: null, modelEdgePoints: null, homeEdgePoints: -2.4,
      homeAbbrev: 'KC', awayAbbrev: 'DEN',
    })).toEqual({ text: 'KC -2.4 pp vs market', side: 'KC' })
  })

  it('reports no comparable price rather than a zero', () => {
    expect(edgeDescription({
      edgeSide: null, modelEdgePoints: null, homeEdgePoints: null,
      homeAbbrev: 'KC', awayAbbrev: 'DEN',
    })).toEqual({ text: 'No comparable price', side: null })
  })
})

describe('a started game is not an open opportunity', () => {
  it.each(['LIVE', 'FINAL', 'IN_PROGRESS', 'COMPLETED', 'POSTPONED'])(
    'treats %s as started', (status) => {
      expect(hasStarted(status)).toBe(true)
    },
  )

  it.each(['SCHEDULED', 'PREGAME', '', null, undefined])(
    'treats %s as not started', (status) => {
      expect(hasStarted(status)).toBe(false)
    },
  )

  it('gives a started game the closed treatment, not the band colour', () => {
    // A finished game keeps its last pregame band. It must not be dressed as
    // something still actionable.
    expect(closedStyle().className).not.toBe(opportunityStyle('STRONG EDGE').className)
    expect(closedStyle().tone).toBe('closed')
  })
})

describe('the shared status mapping', () => {
  it('gives each band its own tone', () => {
    expect(opportunityStyle('NO BET').tone).toBe('neutral')
    expect(opportunityStyle('WATCH').tone).toBe('watch')
    expect(opportunityStyle('STANDARD EDGE').tone).toBe('edge')
    expect(opportunityStyle('STRONG EDGE').tone).toBe('strong')
  })

  it('never promotes STANDARD EDGE into STRONG EDGE', () => {
    expect(opportunityStyle('STANDARD EDGE').tone)
      .not.toBe(opportunityStyle('STRONG EDGE').tone)
  })

  it('leaves an unrecognised status neutral rather than green', () => {
    // A status this table has not met is not guessed into an edge colour.
    expect(opportunityStyle('SOMETHING NEW').tone).toBe('neutral')
    expect(opportunityStyle(null).tone).toBe('neutral')
  })

  it('is case- and whitespace-tolerant on the published label', () => {
    expect(opportunityStyle('  strong edge  ').tone).toBe('strong')
  })
})
