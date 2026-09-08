/**
 * The outcome banner and the transient field treatments.
 *
 * Emphasis only: a pulse, a banner, a run chip. Nothing here draws a ball
 * flight, a fielder or a spray angle, because the record contains no such
 * evidence — only the outcome class, the base state and the runs.
 */
import { outcomeLabel, outcomeTone } from '../../services/replayModel'
import type { ShadowPathEvent } from '../../types/shadow'

export function PlayOutcomeOverlay({ event, accent }: {
  event: ShadowPathEvent
  accent: string
}) {
  const tone = event.runsScored > 0 && event.outcome !== 'HOME_RUN' ? 'score' : outcomeTone(event.outcome)
  const newOuts = event.outsAfter - event.outsBefore

  return (
    <div className="outcome-overlay" aria-live="polite">
      <div
        key={event.pa}
        className={`outcome-banner outcome-banner--${tone}`}
        style={{ ['--accent' as string]: accent }}
      >
        <span className="outcome-banner__label">{outcomeLabel(event.outcome)}</span>
        {event.runsScored > 0 && (
          <span className="outcome-banner__runs">
            +{event.runsScored} {event.runsScored === 1 ? 'run' : 'runs'}
          </span>
        )}
        {newOuts > 0 && event.runsScored === 0 && (
          <span className="outcome-banner__outs">
            {event.outsAfter >= 3 ? 'Side retired' : `${newOuts === 1 ? 'Out' : `${newOuts} outs`}`}
          </span>
        )}
      </div>

      {/* A home run is a persisted outcome class, so a whole-park treatment is
          supported. It shows the event, not an invented trajectory. */}
      {event.outcome === 'HOME_RUN' && (
        <div key={`hr-${event.pa}`} className="outcome-homer" aria-hidden="true" />
      )}
      {event.pitchingChange && (
        <div key={`pc-${event.pa}`} className="outcome-change" role="status">
          <strong>Pitching change</strong>
          <span>Team bullpen enters · governed pitcher state</span>
        </div>
      )}
    </div>
  )
}

export function FinalOverlay({ awayTeam, homeTeam, awayScore, homeScore, innings }: {
  awayTeam: string
  homeTeam: string
  awayScore: number
  homeScore: number
  innings: number
}) {
  return (
    <div className="final-overlay" role="status">
      <span className="final-overlay__eyebrow">Final in this simulated path</span>
      <strong className="final-overlay__score">
        {awayTeam} {awayScore} – {homeTeam} {homeScore}
      </strong>
      <span className="final-overlay__meta">
        {innings} innings{innings > 9 ? ' · extra innings' : ''}
      </span>
    </div>
  )
}
