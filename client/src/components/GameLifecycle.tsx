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

/** Pregame lifecycle wording once the game has begun: never "not started" for a final game. */
export function phaseOf(game: Pick<ForecastGame, 'status' | 'start_time'>, now = Date.now()): 'FINAL' | 'STARTED' | 'PREGAME' {
  const status = String(game.status ?? '').toUpperCase()
  if (['FINAL', 'COMPLETED', 'CLOSED'].includes(status)) return 'FINAL'
  if (['LIVE', 'IN_PROGRESS', 'STARTED'].includes(status)) return 'STARTED'
  const start = Date.parse(game.start_time)
  return Number.isFinite(start) && start <= now ? 'STARTED' : 'PREGAME'
}

function stageText(game: ForecastGame): { stage: string; clock: string } {
  const phase = phaseOf(game)
  if (phase === 'FINAL') return { stage: 'Game final', clock: 'Pregame records shown as kept' }
  if (phase === 'STARTED') return { stage: 'Game in progress', clock: 'First pitch reached' }
  return { stage: formatLifecycleStage(game.lifecycle_stage), clock: formatTimeToFirstPitch(game.time_to_first_pitch_seconds) }
}

function finalStatusText(game: ForecastGame): string {
  if (phaseOf(game) !== 'PREGAME' && (!game.final_forecast_status || game.final_forecast_status === 'NOT_STARTED')) {
    return 'No final-pregame snapshot recorded'
  }
  return statusLabel(game.final_forecast_status)
}

export function GameLifecycleStrip({ game }: { game: ForecastGame }) {
  const { stage, clock } = stageText(game)
  return (
    <div className="game-lifecycle-strip" aria-label="Forecast lifecycle summary">
      <span className="lifecycle-stage">{stage}</span>
      <span><Clock3 size={12} /> {clock}</span>
      <span><Users size={12} /> Lineups: {game.away_team.abbreviation} {lineupLabel(game.away_lineup)} · {game.home_team.abbreviation} {lineupLabel(game.home_lineup)}</span>
      <span>Final: {finalStatusText(game)}{game.final_snapshot_frozen ? ' · frozen' : ''}</span>
    </div>
  )
}

export function GameLifecyclePanel({ game }: { game: ForecastGame }) {
  const { stage, clock } = stageText(game)
  const finalFreeze = game.final_snapshot_frozen == null
    ? 'Freeze state not recorded'
    : game.final_snapshot_frozen
      ? 'Snapshot frozen'
      : 'Snapshot not frozen'
  return (
    <section className="game-lifecycle-panel" aria-label="Forecast lifecycle details">
      <article><span>Lifecycle stage</span><strong>{stage}</strong><small>{clock}</small></article>
      <article><span>Forecast data as of</span><strong><Database size={13} /> {timestamp(game.data_as_of)}</strong><small>Persisted forecast cutoff</small></article>
      <article><span>Last source refresh</span><strong><RefreshCw size={13} /> {timestamp(game.last_refresh_at)}</strong><small>Recorded lifecycle state</small></article>
      <article><span>Next scheduled refresh</span><strong><Clock3 size={13} /> {game.next_scheduled_refresh_at ? timestamp(game.next_scheduled_refresh_at) : 'None scheduled'}</strong><small>{phaseOf(game) === 'PREGAME' ? 'Shown when a refresh is planned' : 'No refresh after first pitch'}</small></article>
      <article><span>{game.away_team.abbreviation} lineup</span><strong><Users size={13} /> {lineupLabel(game.away_lineup)}</strong><small>{game.away_lineup?.source ?? 'No source recorded'}</small></article>
      <article><span>{game.home_team.abbreviation} lineup</span><strong><Users size={13} /> {lineupLabel(game.home_lineup)}</strong><small>{game.home_lineup?.source ?? 'No source recorded'}</small></article>
      <article><span>Final forecast status</span><strong>{finalStatusText(game)}</strong><small>{finalFreeze}</small></article>
    </section>
  )
}
