import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiveShadowStatusCard } from './LiveShadowStatusCard'
import type { LiveShadowStatus } from '../types/domain'

const STATUS: LiveShadowStatus = {
  operational_state: 'LIVE_SHADOW_ARMED',
  availability_polling_enabled: true,
  odds_provider_enabled: true,
  odds_credentials_present: true,
  required_odds_config_variable: null,
  executable: false,
  quota: { quota_remaining: 491, quota_floor: 10, daily_request_budget: 1, requests_today: 1 },
  cadence: {
    availability_slow_minutes: 10, availability_fast_minutes: 2,
    pregame_fast_window_hours: 4, odds_pregame_refresh_minutes: 5,
    quote_burst_offsets_seconds: [15, 60, 300],
  },
  last_successful_schedule_refresh: '2026-08-26T23:01:10Z',
  last_successful_event_poll: null,
  last_successful_odds_refresh: '2026-08-26T23:01:11Z',
  last_event_received: null,
  last_event_source: null,
  last_rescore_duration_seconds: null,
  active_games_monitored: 15,
  circuit_state: { last_provider_failure: null, last_failed_job: null },
}

describe('LiveShadowStatusCard', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders operational status, quota, and cadence for an admin', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => STATUS } as Response)))
    render(<LiveShadowStatusCard />)
    expect(await screen.findByText(/Live shadow operation/i)).toBeInTheDocument()
    expect(screen.getByText('LIVE SHADOW ARMED')).toBeInTheDocument()
    expect(screen.getByText('491')).toBeInTheDocument() // quota remaining
    expect(screen.getByText('15')).toBeInTheDocument() // games monitored
    expect(screen.getByText(/no executable action/i)).toBeInTheDocument()
  })

  it('renders nothing for a non-admin (endpoint rejects)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: 'forbidden' }) } as Response)))
    const { container } = render(<LiveShadowStatusCard />)
    // Give the rejected promise a microtask to settle; the card stays empty.
    await Promise.resolve()
    expect(container.querySelector('.section-panel')).toBeNull()
  })
})
