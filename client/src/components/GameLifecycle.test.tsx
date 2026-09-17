import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GameLifecyclePanel, phaseOf } from './GameLifecycle'
import type { ForecastGame } from '../types/domain'

const base = {
  status: 'FINAL', start_time: '2026-09-16T19:07:00Z', lifecycle_stage: 'NOT_STARTED',
  time_to_first_pitch_seconds: null, final_forecast_status: 'NOT_STARTED', final_snapshot_frozen: null,
  away_team: { abbreviation: 'DET' }, home_team: { abbreviation: 'TOR' },
} as unknown as ForecastGame

describe('game lifecycle wording', () => {
  it('never tells a reader a final game has not started', () => {
    render(<GameLifecyclePanel game={base} />)
    const panel = screen.getByLabelText('Forecast lifecycle details')
    expect(panel).toHaveTextContent('Game final')
    expect(panel).toHaveTextContent('No final-pregame snapshot recorded')
    expect(panel).not.toHaveTextContent('Not started')
  })

  it('knows pregame, started and final', () => {
    const now = Date.parse('2026-09-16T18:00:00Z')
    expect(phaseOf({ status: 'SCHEDULED', start_time: '2026-09-16T19:07:00Z' }, now)).toBe('PREGAME')
    expect(phaseOf({ status: 'SCHEDULED', start_time: '2026-09-16T17:07:00Z' }, now)).toBe('STARTED')
    expect(phaseOf({ status: 'LIVE', start_time: '2026-09-16T19:07:00Z' }, now)).toBe('STARTED')
    expect(phaseOf({ status: 'final', start_time: '2026-09-16T19:07:00Z' }, now)).toBe('FINAL')
  })
})
