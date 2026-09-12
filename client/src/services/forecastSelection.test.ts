import { describe, expect, it } from 'vitest'
import {
  availabilityNote, badgeAttribution, engineShortName, forecastCacheScope, isCurrent,
  type ForecastIdentity,
} from './forecastSelection'

const identity = (over: Partial<ForecastIdentity> = {}): ForecastIdentity => ({
  engine_id: 'MLB_V18_WHOLE_GAME', engine_version: 'mlb-v18-whole-game-a6e7e9',
  model_label: 'V18 whole-game (research shadow)',
  forecast_id: 'mlb-v18-daily-2123', snapshot_type: 'PROSPECTIVE', snapshot_version: 1,
  source_cutoff_utc: '2026-09-12T14:49:42Z', published_utc: '2026-09-12T14:57:58Z',
  recorded_utc: '2026-09-12T15:00:00Z',
  home_win_probability: 0.6024, away_win_probability: 0.3976,
  expected_home_score: 4.81, expected_away_score: 4.08,
  availability: 'AVAILABLE', availability_detail: '', decision_eligible: true,
  ...over,
})

describe('cache separation', () => {
  it('separates two models of the same game', () => {
    const v18 = forecastCacheScope({ gameId: 'g1', identity: identity() })
    const gen1 = forecastCacheScope({
      gameId: 'g1',
      identity: identity({ engine_id: 'AISP_BASELINE', forecast_id: 'uuid-gen1' }),
    })
    expect(v18).not.toEqual(gen1)
  })

  it('separates two revisions of the same model', () => {
    const first = forecastCacheScope({ gameId: 'g1', identity: identity({ snapshot_version: 1 }) })
    const second = forecastCacheScope({
      gameId: 'g1', identity: identity({ snapshot_version: 2, forecast_id: 'mlb-v18-daily-999' }),
    })
    expect(first).not.toEqual(second)
  })

  it('separates a current read from an explicitly historical one', () => {
    const now = forecastCacheScope({ gameId: 'g1', identity: identity() })
    const past = forecastCacheScope({ gameId: 'g1', identity: identity(), horizon: '2026-09-10' })
    expect(now).not.toEqual(past)
    expect(now.horizon).toBe('current')
  })

  it('separates two games', () => {
    expect(forecastCacheScope({ gameId: 'g1', identity: identity() }))
      .not.toEqual(forecastCacheScope({ gameId: 'g2', identity: identity() }))
  })
})

describe('presenting a forecast condition', () => {
  it('says nothing when a forecast is current', () => {
    expect(availabilityNote(identity())).toBe('')
    expect(isCurrent(identity())).toBe(true)
  })

  it('prefers the backend sentence when there is one', () => {
    const note = availabilityNote(identity({
      availability: 'INVALIDATED',
      availability_detail: 'An input changed after this forecast was published: lineup.',
    }))
    expect(note).toMatch(/An input changed/)
  })

  it('has a plain sentence for every condition, even without a detail', () => {
    for (const availability of
      ['MISSING', 'SUPERSEDED', 'NOT_PREGAME', 'OUTSIDE_ERA', 'INVALIDATED'] as const) {
      const note = availabilityNote(identity({ availability, availability_detail: '' }))
      expect(note.length).toBeGreaterThan(10)
    }
  })

  it('never reports a limited forecast as current', () => {
    for (const availability of
      ['MISSING', 'SUPERSEDED', 'NOT_PREGAME', 'OUTSIDE_ERA', 'INVALIDATED'] as const) {
      expect(isCurrent(identity({ availability, decision_eligible: false }))).toBe(false)
    }
  })
})

describe('naming what a badge describes', () => {
  it('names the model and the side, and says it is not a decision', () => {
    const text = badgeAttribution({ identity: identity(), side: 'DET' })
    expect(text).toContain('DET')
    expect(text).toContain('V18')
    expect(text).toMatch(/Not a recorded decision/)
  })

  it('shortens engine ids a reader would not recognise', () => {
    expect(engineShortName('MLB_V18_WHOLE_GAME')).toBe('V18')
    expect(engineShortName('AISP_BASELINE')).toBe('Gen 1')
    expect(engineShortName('NFL_M1_XGBOOST')).toBe('M1')
    expect(engineShortName(null)).toBe('Unknown model')
  })
})

describe('naming the engine that produced the number', () => {
  // The failure this covers was live: a game with no V18 forecast fell back to
  // Gen 1's probability, and the chip beside it still read "V18". The number
  // and its label came from two different models.
  it('attributes a band to the engine that supplied it, not the selected one', () => {
    const missing = identity({ availability: 'MISSING', home_win_probability: null })
    const text = badgeAttribution({
      identity: missing, side: 'OAK win', displayedEngineId: 'AISP_BASELINE',
    })
    expect(text).toContain('Gen 1')
    expect(text).not.toContain('V18')
  })

  it('falls back to the selected engine when no displayed engine is given', () => {
    expect(badgeAttribution({ identity: identity(), side: null })).toContain('V18')
    expect(engineShortName('AISP_BASELINE')).toBe('Gen 1')
  })
})
