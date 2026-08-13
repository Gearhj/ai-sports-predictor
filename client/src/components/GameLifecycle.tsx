import { Clock3, Database, RefreshCw, Users } from 'lucide-react'
import { formatTimestamp } from '../services/format'
import type { ForecastGame, LineupStatusSummary } from '../types/domain'

export function formatLifecycleStage(stage?: string): string {
  if (!stage || stage === 'NOT_STARTED') return 'Not started'
  const timed = stage.match(/^T_MINUS_(\d+)([HM])$/)
  if (timed) return `T−${timed[1]}${timed[2].toLowerCase()}`
  return stage.toLowerCase().replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

export function formatTimeToFirstPitch(seconds?: number | null): string {
  if (seconds == null) return 'Countdown not recorded'
  if (seconds <= 0) return 'First pitch reached'
  const totalMinutes = Math.ceil(seconds / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (!hours) return `${minutes}m to first pitch`
  return `${hours}h${minutes ? ` ${minutes}m` : ''} to first pitch`
}

function statusLabel(value?: string | null): string {
  if (!value) return 'Not recorded'
  return value.toLowerCase().replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

function lineupLabel(lineup?: LineupStatusSummary): string {
  if (!lineup || lineup.status === 'MISSING') return 'Missing'
  return `${statusLabel(lineup.status)}${lineup.complete ? ' · complete' : ' · partial'}`
}

function timestamp(value?: string | null): string {
  return value ? formatTimestamp(value) : 'Not recorded'
}

export function GameLifecycleStrip({ game }: { game: ForecastGame }) {
  return (
    <div className="game-lifecycle-strip" aria-label="Forecast lifecycle summary">
      <span className="lifecycle-stage">{formatLifecycleStage(game.lifecycle_stage)}</span>
      <span><Clock3 size={12} /> {formatTimeToFirstPitch(game.time_to_first_pitch_seconds)}</span>
      <span><Users size={12} /> Lineups: {game.away_team.abbreviation} {lineupLabel(game.away_lineup)} · {game.home_team.abbreviation} {lineupLabel(game.home_lineup)}</span>
      <span>Final: {statusLabel(game.final_forecast_status)}{game.final_snapshot_frozen ? ' · frozen' : ''}</span>
    </div>
  )
}

export function GameLifecyclePanel({ game }: { game: ForecastGame }) {
  const finalFreeze = game.final_snapshot_frozen == null
    ? 'Freeze state not recorded'
    : game.final_snapshot_frozen
      ? 'Snapshot frozen'
      : 'Snapshot not frozen'
  return (
    <section className="game-lifecycle-panel" aria-label="Forecast lifecycle details">
      <article><span>Lifecycle stage</span><strong>{formatLifecycleStage(game.lifecycle_stage)}</strong><small>{formatTimeToFirstPitch(game.time_to_first_pitch_seconds)}</small></article>
      <article><span>Forecast data as of</span><strong><Database size={13} /> {timestamp(game.data_as_of)}</strong><small>Persisted forecast cutoff</small></article>
      <article><span>Last source refresh</span><strong><RefreshCw size={13} /> {timestamp(game.last_refresh_at)}</strong><small>Recorded lifecycle state</small></article>
      <article><span>Next scheduled refresh</span><strong><Clock3 size={13} /> {timestamp(game.next_scheduled_refresh_at)}</strong><small>Null when no refresh is scheduled</small></article>
      <article><span>{game.away_team.abbreviation} lineup</span><strong><Users size={13} /> {lineupLabel(game.away_lineup)}</strong><small>{game.away_lineup?.source ?? 'No source recorded'}</small></article>
      <article><span>{game.home_team.abbreviation} lineup</span><strong><Users size={13} /> {lineupLabel(game.home_lineup)}</strong><small>{game.home_lineup?.source ?? 'No source recorded'}</small></article>
      <article><span>Final forecast status</span><strong>{statusLabel(game.final_forecast_status)}</strong><small>{finalFreeze}</small></article>
    </section>
  )
}
