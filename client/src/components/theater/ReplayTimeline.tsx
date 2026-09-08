/**
 * Transport controls and the scrubbable timeline.
 *
 * The scrubber is an accurate index into the persisted path — one tick per
 * plate appearance — with markers only where the record supports them:
 * pitching changes and plays that actually changed the score.
 */
import { ChevronLeft, ChevronRight, Maximize2, Minimize2, Pause, Play, RotateCcw } from 'lucide-react'
import type { ShadowPathEvent } from '../../types/shadow'

export const SPEEDS = [0.5, 1, 2, 4] as const
export type Speed = (typeof SPEEDS)[number]

export function ReplayTimeline({
  events, index, playing, speed, theater,
  onSeek, onStep, onPlayToggle, onRestart, onSpeed, onTheater,
}: {
  events: ShadowPathEvent[]
  index: number
  playing: boolean
  speed: Speed
  theater: boolean
  onSeek: (index: number) => void
  onStep: (delta: number) => void
  onPlayToggle: () => void
  onRestart: () => void
  onSpeed: (speed: Speed) => void
  onTheater: () => void
}) {
  const last = events.length - 1
  const current = events[Math.min(index, last)]
  const innings = [...new Set(events.map((event) => event.inning))].sort((a, b) => a - b)
  const changes = events
    .map((event, position) => ({ event, position }))
    .filter((item) => item.event.pitchingChange)
  const scoring = events
    .map((event, position) => ({ event, position }))
    .filter((item) => item.event.runsScored > 0)

  return (
    <div className="transport">
      <div className="transport__scrub">
        <div className="transport__track" aria-hidden="true">
          <span className="transport__fill" style={{ width: `${(index / last) * 100}%` }} />
          {scoring.map((item) => (
            <i
              key={`s-${item.position}`}
              className="transport__mark transport__mark--score"
              style={{ left: `${(item.position / last) * 100}%` }}
              title={`Run scored · PA ${item.event.pa}`}
            />
          ))}
          {changes.map((item) => (
            <i
              key={`c-${item.position}`}
              className="transport__mark transport__mark--change"
              style={{ left: `${(item.position / last) * 100}%` }}
              title={`Pitching change · PA ${item.event.pa}`}
            />
          ))}
        </div>
        <input
          type="range"
          min={0}
          max={last}
          step={1}
          value={Math.min(index, last)}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label={`Plate appearance ${index + 1} of ${events.length}`}
          className="transport__range"
        />
      </div>

      <div className="transport__row">
        <div className="transport__group" role="group" aria-label="Playback">
          <button type="button" onClick={() => onStep(-1)} disabled={index === 0}
            aria-label="Previous plate appearance">
            <ChevronLeft size={16} aria-hidden="true" />
          </button>
          <button type="button" className="transport__play" onClick={onPlayToggle}
            disabled={index >= last} aria-label={playing ? 'Pause replay' : 'Play replay'}>
            {playing ? <Pause size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
            <span>{playing ? 'Pause' : 'Play'}</span>
          </button>
          <button type="button" onClick={() => onStep(1)} disabled={index >= last}
            aria-label="Next plate appearance">
            <ChevronRight size={16} aria-hidden="true" />
          </button>
          <button type="button" onClick={onRestart} aria-label="Restart replay">
            <RotateCcw size={15} aria-hidden="true" />
          </button>
        </div>

        <span className="transport__position">
          PA <strong>{index + 1}</strong> of {events.length}
          <small>{current.half === 'TOP' ? 'Top' : 'Bottom'} {current.inning}</small>
        </span>

        <div className="transport__group transport__speeds" role="group" aria-label="Replay speed">
          {SPEEDS.map((value) => (
            <button
              key={value}
              type="button"
              className={value === speed ? 'is-active' : ''}
              aria-pressed={value === speed}
              onClick={() => onSpeed(value)}
            >
              {value}×
            </button>
          ))}
        </div>

        <label className="transport__jump">
          <span>Inning</span>
          <select
            value={current.inning}
            onChange={(event) => {
              const target = events.findIndex((item) => item.inning === Number(event.target.value))
              if (target >= 0) onSeek(target)
            }}
          >
            {innings.map((inning) => <option key={inning} value={inning}>{inning}</option>)}
          </select>
        </label>

        {changes.length > 0 && (
          <div className="transport__group transport__changes" role="group" aria-label="Jump to a pitching change">
            <span>Changes</span>
            {changes.map((item) => (
              <button key={item.position} type="button" onClick={() => onSeek(item.position)}>
                PA {item.event.pa}
              </button>
            ))}
          </div>
        )}

        <button type="button" className="transport__theater" onClick={onTheater}
          aria-pressed={theater}>
          {theater
            ? <><Minimize2 size={15} aria-hidden="true" /> Exit theater</>
            : <><Maximize2 size={15} aria-hidden="true" /> Theater mode</>}
        </button>
      </div>
    </div>
  )
}
