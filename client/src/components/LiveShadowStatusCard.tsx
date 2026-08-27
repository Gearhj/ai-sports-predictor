import { useEffect, useState } from 'react'
import { Radar, ShieldAlert } from 'lucide-react'
import { MetricCard, Panel } from './ui'
import { endpoints } from '../services/api'
import type { LiveShadowStatus } from '../types/domain'

/**
 * Admin-only operational status for the armed live prospective shadow pilot.
 * Renders on the admin Data Health page. Reads persisted status only — the
 * endpoint never triggers a provider call.
 */
export function LiveShadowStatusCard() {
  const [status, setStatus] = useState<LiveShadowStatus | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    endpoints.liveShadowStatus(controller.signal)
      .then((result) => { if (!controller.signal.aborted) setStatus(result) })
      .catch(() => { /* non-admin or offline: simply omit the card */ })
    return () => controller.abort()
  }, [])

  if (!status) return null

  return (
    <Panel
      title="Live shadow operation"
      eyebrow="ARMED PROSPECTIVE PILOT"
      className="section-panel"
      action={<span className="admin-chip"><Radar size={13} /> {status.operational_state.replaceAll('_', ' ')}</span>}
    >
      <section className="metric-grid metric-grid--four">
        <MetricCard
          label="Availability polling"
          value={status.availability_polling_enabled ? 'ENABLED' : 'DISARMED'}
          detail={`Fast ${status.cadence.availability_fast_minutes}m / slow ${status.cadence.availability_slow_minutes}m`}
        />
        <MetricCard
          label="Odds provider"
          value={status.odds_provider_enabled ? 'ENABLED' : 'NO CREDENTIALS'}
          detail={status.required_odds_config_variable
            ? `Set ${status.required_odds_config_variable}`
            : `Refresh every ${status.cadence.odds_pregame_refresh_minutes}m`}
        />
        <MetricCard
          label="Odds quota remaining"
          value={status.quota.quota_remaining ?? '—'}
          detail={`Floor ${status.quota.quota_floor} · used today ${status.quota.requests_today}`}
        />
        <MetricCard
          label="Games monitored"
          value={status.active_games_monitored}
          detail={`Pregame window T-${status.cadence.pregame_fast_window_hours}h`}
        />
      </section>
      <div className="system-notes">
        <div><ShieldAlert size={16} /><p><strong>Last schedule refresh</strong><span>{status.last_successful_schedule_refresh ?? 'none yet'}</span></p></div>
        <div><ShieldAlert size={16} /><p><strong>Last odds refresh</strong><span>{status.last_successful_odds_refresh ?? 'none yet'}</span></p></div>
        <div><ShieldAlert size={16} /><p><strong>Last event received</strong><span>{status.last_event_received ? `${status.last_event_received} · ${status.last_event_source}` : 'none yet'}</span></p></div>
      </div>
      <p className="muted" style={{ marginTop: '0.5rem' }}>Research shadow — no executable action.</p>
    </Panel>
  )
}
