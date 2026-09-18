/**
 * Governance is the pinned handoff plus the clock — never the bundle lineage.
 *
 * The forecast rows for Week 2 all carry the producer's own
 * `NOT_PROMOTED` / `RESEARCH_SHADOW` lineage, so reading governance off the
 * forecast would leave every governed game mislabelled. And DET @ BUF kicks off
 * before the handoff's effective instant: relabelling it would claim evidence
 * that did not exist when it was forecast.
 */
import { describe, expect, it } from 'vitest'

import {
  NFL_PRODUCTION_BASELINE, governanceBadge, isGoverned, monitoringNote, slateBadge,
  type NflProductionStatus,
} from './nflGovernance'

const EFFECTIVE = '2026-09-20T17:00:00+00:00'

const accepted: NflProductionStatus = {
  status: NFL_PRODUCTION_BASELINE,
  accepted: true,
  fixture: false,
  production_label_allowed: true,
  handoff_sha256: '29d00078f389608ccd5a5e1db3db89b8594e45c7e7a9892f2d54e26f38557279',
  identity: {
    model_id: 'NFL_M1_XGBOOST',
    configuration_id: 'NFL_M1_2026_RESEARCH_SHADOW_V1',
    draw_count: 10000,
    effective_from_utc: EFFECTIVE,
    calibration_authority: { minimumSettledGamesForPublishedCalibration: 100 },
  },
}

describe('NFL governance', () => {
  it('governs a kickoff at or after the effective instant', () => {
    expect(isGoverned(accepted, EFFECTIVE)).toBe(true)                      // CAR @ ATL, 1:00 PM ET
    expect(isGoverned(accepted, '2026-09-21T00:20:00+00:00')).toBe(true)    // IND @ KC
    expect(isGoverned(accepted, '2026-09-22T00:15:00+00:00')).toBe(true)    // NYG @ LAR, Monday
  })

  it('leaves a kickoff before the effective instant as research', () => {
    // DET @ BUF, Thursday 8:15 PM ET — three days before activation.
    expect(isGoverned(accepted, '2026-09-18T00:15:00+00:00')).toBe(false)
  })

  it('fails closed when no handoff is pinned, refused, or a fixture', () => {
    expect(isGoverned(null, EFFECTIVE)).toBe(false)
    expect(isGoverned({ ...accepted, production_label_allowed: false }, EFFECTIVE)).toBe(false)
    expect(isGoverned({ ...accepted, fixture: true }, EFFECTIVE)).toBe(false)
    expect(isGoverned({ ...accepted, status: 'NFL_M1_SHADOW_ONLY' }, EFFECTIVE)).toBe(false)
    expect(isGoverned({ ...accepted, identity: null }, EFFECTIVE)).toBe(false)
    expect(isGoverned(accepted, null)).toBe(false)
    expect(isGoverned(accepted, 'not a date')).toBe(false)
  })

  it('never shows a raw status code to a consumer', () => {
    const badge = governanceBadge(true)
    expect(badge.label).toBe('Production baseline')
    for (const text of [badge.label, badge.title, governanceBadge(false).label,
      slateBadge(accepted, true), monitoringNote(accepted, 16)]) {
      expect(text).not.toMatch(/RESEARCH_SHADOW_V1|NFL_M1_PRODUCTION_BASELINE|_V1\b/)
    }
  })

  it('states the sample honestly and claims nothing about profitability', () => {
    const note = monitoringNote(accepted, 16)
    expect(note).toContain('16 games scored so far — too early to establish performance')
    expect(note).toContain('not calibrated')
    expect(note).toMatch(/calibration review at 100 settled games/)
    for (const forbidden of [/\bproven\b/i, /validated for profitability/i, /\boptimi[sz]ed\b/i,
      /permanently calibrated/i, /\bguarantee/i]) {
      expect(note).not.toMatch(forbidden)
    }
  })

  it('keeps the research note when no handoff is pinned', () => {
    const none = { ...accepted, production_label_allowed: false }
    expect(slateBadge(none, false)).toBe('Research shadow')
    expect(monitoringNote(none, 16)).toContain('research forecasts')
  })

  it('does not claim a production slate when no game on it is governed', () => {
    expect(slateBadge(accepted, false)).toBe('Research shadow')
    expect(slateBadge(accepted, true)).toBe('NFL M1 production baseline')
  })
})
