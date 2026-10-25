/**
 * Team identity — the three things that make a sports product look broken.
 *
 * A wrong club name on tonight's fixture, a crest that belongs to a different
 * league's team with the same abbreviation, and a colour bar you cannot see.
 * Each is individually small and each destroys trust in every correct number
 * on the same page, so each gets a test.
 */
import { describe, expect, it } from 'vitest'
import {
  contrastInk, inkColor, logoUrl, normaliseAbbreviation, onDarkColor, teamIdentity,
} from './teamIdentity'

describe('event-era identity', () => {
  it('gives the three relocated clubs the name they go by now', () => {
    // The database still holds each franchise's founding identity for these.
    expect(teamIdentity('NFL', 'LAR').name).toBe('Los Angeles Rams')
    expect(teamIdentity('NFL', 'LAC').name).toBe('Los Angeles Chargers')
    expect(teamIdentity('NFL', 'LV').name).toBe('Las Vegas Raiders')
  })

  it('keeps the historical name on a historical season', () => {
    expect(teamIdentity('NFL', 'LAR', 2014).name).toBe('St. Louis Rams')
    expect(teamIdentity('NFL', 'LV', 2018).name).toBe('Oakland Raiders')
    expect(teamIdentity('MLB', 'CLE', 2019).name).toBe('Cleveland Indians')
  })

  it('switches at the right season boundary rather than near it', () => {
    // The Rams' last St. Louis season was 2015.
    expect(teamIdentity('NFL', 'LAR', 2015).name).toBe('St. Louis Rams')
    expect(teamIdentity('NFL', 'LAR', 2016).name).toBe('Los Angeles Rams')
  })
})

describe('abbreviations do not collide across leagues', () => {
  it('resolves the same abbreviation to different clubs per league', () => {
    expect(teamIdentity('NFL', 'SF').name).toBe('San Francisco 49ers')
    expect(teamIdentity('MLB', 'SF').name).toBe('San Francisco Giants')
    expect(teamIdentity('NFL', 'WAS').name).toBe('Washington Commanders')
    expect(teamIdentity('MLB', 'WAS').name).toBe('Washington Nationals')
  })

  it('puts the league in the canonical id, so nothing can key on the abbreviation', () => {
    expect(teamIdentity('NFL', 'SF').id).toBe('NFL:SF')
    expect(teamIdentity('MLB', 'SF').id).toBe('MLB:SF')
    expect(teamIdentity('NFL', 'SF').id).not.toBe(teamIdentity('MLB', 'SF').id)
  })

  it('addresses the logo by league as well as club', () => {
    expect(logoUrl(teamIdentity('NFL', 'SF'))).toContain('/nfl/')
    expect(logoUrl(teamIdentity('MLB', 'SF'))).toContain('/mlb/')
    // NHL and NBA use ESPN's own file names where they differ from AISP's abbreviation.
    expect(logoUrl(teamIdentity('NHL', 'ANA'))).toBe('https://a.espncdn.com/i/teamlogos/nhl/500/ana.png')
    expect(logoUrl(teamIdentity('NHL', 'TBL'))).toBe('https://a.espncdn.com/i/teamlogos/nhl/500/tb.png')
    expect(logoUrl(teamIdentity('NBA', 'PHO'))).toMatch(/\/nba\/500\/phx\.png$/)
    expect(logoUrl(teamIdentity('NBA', 'GSW'))).toMatch(/\/nba\/500\/gs\.png$/)
  })

  it('normalises the spellings feeds actually send', () => {
    expect(normaliseAbbreviation('NFL', 'OAK')).toBe('LV')
    expect(normaliseAbbreviation('NFL', 'STL')).toBe('LAR')
    expect(normaliseAbbreviation('NFL', 'SD')).toBe('LAC')
    expect(normaliseAbbreviation('MLB', 'CHW')).toBe('CWS')
    // ...and leaves an unknown one alone rather than guessing.
    expect(normaliseAbbreviation('MLB', 'ZZZ')).toBe('ZZZ')
  })
})

describe('unknown teams degrade instead of breaking', () => {
  it('returns a usable identity for a club we have no record of', () => {
    const unknown = teamIdentity('NHL', 'XYZ')
    expect(unknown.known).toBe(false)
    expect(unknown.abbreviation).toBe('XYZ')
    expect(unknown.primary).toMatch(/^hsl\(/)
    // No logo is requested for a club we cannot identify: a wrong crest is
    // worse than no crest.
    expect(logoUrl(unknown)).toBeNull()
  })

  it('gives different unknown clubs different colours, stably', () => {
    const first = teamIdentity('NHL', 'AAA').primary
    const second = teamIdentity('NHL', 'BBB').primary
    expect(first).not.toBe(second)
    expect(teamIdentity('NHL', 'AAA').primary).toBe(first)
  })

  it('survives a missing abbreviation', () => {
    expect(() => teamIdentity('NFL', null)).not.toThrow()
    expect(teamIdentity('NFL', undefined).known).toBe(false)
  })
})

describe('colours stay visible wherever they are used', () => {
  it('never renders a near-black club colour on the dark featured panel', () => {
    // The Raiders are black and the Steelers are near-black. Both are used as
    // bar fills on a navy panel, where their true primary is invisible.
    for (const abbreviation of ['LV', 'PIT', 'NO', 'CHI']) {
      const colour = onDarkColor(teamIdentity('NFL', abbreviation))
      expect(luminance(colour)).toBeGreaterThan(0.1)
    }
  })

  it('prefers the club to a house colour when either of theirs will do', () => {
    // Seattle's action green is their own and reads on dark; use it.
    expect(onDarkColor(teamIdentity('NFL', 'SEA')).toUpperCase()).toBe('#69BE28')
  })

  it('picks a text-safe colour for light panels', () => {
    // Pittsburgh's gold on white is unreadable; their black is not.
    expect(luminance(inkColor(teamIdentity('MLB', 'PIT')))).toBeLessThan(0.55)
  })

  it('chooses readable ink to sit on a club colour', () => {
    expect(contrastInk('#000000')).toBe('#ffffff')
    expect(contrastInk('#FFB612')).toBe('#0b1a2b')
  })
})

/** Relative luminance, duplicated here so the test does not trust the module. */
function luminance(colour: string): number {
  const value = colour.replace('#', '')
  if (value.length !== 6) return 1
  const channel = (start: number) => {
    const srgb = parseInt(value.slice(start, start + 2), 16) / 255
    return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
}
