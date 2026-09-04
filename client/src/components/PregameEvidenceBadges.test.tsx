import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { PregameEvidenceBadges, ageText } from './PregameEvidenceBadges'
import type { PregameEvidence } from '../types/domain'

const evidence: PregameEvidence = {
  reference_at: '2026-09-04T19:17:00Z', cutoff_at: '2026-09-04T22:55:00Z', reference_is_cutoff: false,
  starter_home: { state: 'PROBABLE_STABLE', status: 'PROBABLE', source: 'MLB_STATS_API', observed_at: '2026-09-04T18:35:00Z', age_minutes_at_reference: 42, freshness: 'FRESH', stable_since: null, label: 'Starter: PROBABLE · observed 42m before reference · FRESH', reasons: [] },
  starter_away: { state: 'PROBABLE_UNSTABLE', status: 'PROBABLE', source: 'MLB_STATS_API', observed_at: '2026-09-04T13:05:00Z', age_minutes_at_reference: 372, freshness: 'STALE', stable_since: null, label: 'Starter: PROBABLE · observed 6h 12m before reference · STALE', reasons: ['Latest observation is not current at the evaluation reference (older than 240 minutes)'] },
  starter_block_category: 'STALE',
  starter_block_reason: 'Latest observation is not current at the evaluation reference (older than 240 minutes)',
  lineup: { state: 'PENDING_ALLOWED_FOR_MONEYLINE', label: 'Lineup: NOT YET PUBLISHED', home: { source_state: 'EXPECTED_NOT_YET_PUBLISHED', label: 'NOT YET PUBLISHED', observed_at: null }, away: { source_state: 'EXPECTED_NOT_YET_PUBLISHED', label: 'NOT YET PUBLISHED', observed_at: null }, source: null, reasons: [] },
  source_note: 'MLB_STATS_API publishes probable starters only; a PROBABLE starter is the normal, terminal pregame state.',
  any_fresh: true,
}

afterEach(() => cleanup())

describe('PregameEvidenceBadges', () => {
  it('formats ages compactly', () => {
    expect(ageText(42)).toBe('42m')
    expect(ageText(372)).toBe('6h 12m')
    expect(ageText(-3)).toBe('future-dated')
  })

  it('shows source status, age at reference, freshness, lineup state and the exact block', () => {
    render(<PregameEvidenceBadges evidence={evidence} />)
    expect(screen.getByText(/Home starter PROBABLE · observed 42m before reference · FRESH/)).toBeInTheDocument()
    expect(screen.getByText(/Away starter PROBABLE · observed 6h 12m before reference · STALE/)).toBeInTheDocument()
    expect(screen.getByText('Lineup: NOT YET PUBLISHED')).toBeInTheDocument()
    expect(screen.getByText('Blocked: STALE')).toBeInTheDocument()
    expect(screen.getByText(/evaluation instant; cutoff not yet reached/)).toBeInTheDocument()
    expect(screen.getByText(/older than 240 minutes/)).toBeInTheDocument()
    expect(screen.getByText(/PROBABLE starter is the normal, terminal pregame state/)).toBeInTheDocument()
  })

  it('omits the block badge and detail in compact mode when the starter passes', () => {
    render(<PregameEvidenceBadges evidence={{ ...evidence, starter_block_category: 'PASS', starter_block_reason: null }} compact />)
    expect(screen.queryByText(/Blocked:/)).not.toBeInTheDocument()
    expect(screen.queryByText('Evidence detail')).not.toBeInTheDocument()
  })
})
