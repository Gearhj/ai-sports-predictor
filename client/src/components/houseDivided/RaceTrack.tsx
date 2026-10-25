/**
 * THE RACE (owner 10-24, a HOLY SHIT moment for Compete): James's models, Scotty and Red Lou as runners on one
 * track, placed by how far each bankroll has moved from the shared start line (every started sport combined).
 * The runners sprint to their spots once when the page opens and then hold still. Beside it, THE NEXT LOCK: the
 * next competition game whose picks close (15 minutes before it starts), counting down, with a button to pick.
 *
 * Reads GET /house-divided?sport=ALL and today's Mission Control; virtual competition only, no real money.
 */
import { useCallback, useMemo, type CSSProperties } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { houseDivided } from '../../services/houseDivided'
import { operationalToday } from '../../services/operationalDate'
import { useMissionControl, useTick, countdownText, type MissionGame } from '../../features/mission/missionData'
import { Crest } from '../../features/flow/Crest'
import './raceTrack.css'

const COMPETITION = new Set(['MLB', 'NFL', 'NBA', 'CFB'])
const money = (v: number, signed = false) => `${signed && v > 0 ? '+' : v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export function RaceTrack({ onPicks }: { onPicks: () => void }) {
  const loader = useCallback((signal: AbortSignal) => houseDivided.board('ALL', signal), [])
  const board = useApiResource(loader, { key: 'house-divided-race', staleTimeMs: 60_000, refreshIntervalMs: 120_000 })
  const now = useTick(1000)
  const mission = useMissionControl(operationalToday(new Date(now)))
  const standings = useMemo(() => (board.data?.standings ?? []) as Array<{ participant: string; display_name: string; color: string; is_model: boolean; starting_balance: number; bankroll: number; settled_count: number; open_count: number; record: { WIN: number; LOSS: number; PUSH: number }; roi: number | null }>, [board.data])
  const nextLock = useMemo(() => {
    const games = (mission.data?.games ?? []).filter((g: MissionGame) => COMPETITION.has(g.sport) && g.phase === 'PREGAME')
    const withLock = games.map((g) => ({ g, lock: Date.parse(g.starts_at) - 15 * 60_000 })).filter((x) => x.lock > now).sort((a, b) => a.lock - b.lock)
    return withLock[0] ?? null
  }, [mission.data, Math.floor(now / 30_000)]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!standings.length) return null

  const deltas = standings.map((s) => s.bankroll - s.starting_balance)
  const span = Math.max(50, ...deltas.map((d) => Math.abs(d))) * 1.15
  const best = Math.max(...deltas)
  const leaders = standings.filter((s) => Math.abs(s.bankroll - s.starting_balance - best) < 0.005)
  const allEven = deltas.every((d) => Math.abs(d) < 0.005)
  const humans = standings.filter((s) => !s.is_model)
  const idle = humans.filter((s) => s.settled_count + s.open_count === 0)
  const model = standings.find((s) => s.is_model)

  return (
    <section className="rt" aria-label="The race">
      <div className="rt__track">
        <header><h2>The Race</h2><small>everyone starts at the line · bankroll moved since each sport's start, all sports combined</small></header>
        <div className="rt__lanes">
          <span className="rt__line" aria-hidden="true"><b>START</b></span>
          {standings.map((s, i) => {
            const d = s.bankroll - s.starting_balance
            const x = 50 + (d / span) * 45
            const lead = !allEven && leaders.some((l) => l.participant === s.participant)
            return (
              <div key={s.participant} className="rt__lane" style={{ ['--c' as string]: s.color } as CSSProperties}>
                <span className="rt__name"><b>{s.display_name}</b><small>{s.is_model ? 'official model decisions' : 'picks by hand'}</small></span>
                <span className="rt__run">
                  <i className="rt__trail" style={{ left: d >= 0 ? '50%' : `${x}%`, width: `${Math.abs(x - 50)}%`, animationDelay: `${i * 120}ms` }} />
                  <span className={`rt__runner${lead ? ' is-lead' : ''}`} style={{ left: `${x}%`, animationDelay: `${i * 120}ms` }}>
                    {lead ? <em aria-hidden="true">👑</em> : null}
                    <i>{s.display_name.slice(0, 1)}</i>
                  </span>
                </span>
                <span className="rt__num"><b className={d > 0 ? 'is-up' : d < 0 ? 'is-down' : ''}>{money(d, true)}</b><small>{s.record.WIN}–{s.record.LOSS}{s.record.PUSH ? `–${s.record.PUSH}` : ''} · {s.open_count} open</small></span>
              </div>
            )
          })}
        </div>
        <p className="rt__call">
          {(() => {
            if (allEven) return 'Dead heat: nobody has moved off the line yet.'
            const names = leaders.map((l) => l.display_name).join(' and ')
            const modelLeads = Boolean(model && leaders.some((l) => l.participant === model.participant))
            const gap = model && !modelLeads ? best - (model.bankroll - model.starting_balance) : 0
            const idleLeaders = leaders.filter((l) => !l.is_model && l.settled_count + l.open_count === 0)
            if (!modelLeads && idleLeaders.length === leaders.length) return `${names} ${leaders.length > 1 ? 'lead' : 'leads'} the machine by ${money(gap)} without making a single pick. Standing still is beating the models right now.`
            if (!modelLeads) return `${names} ${leaders.length > 1 ? 'lead' : 'leads'} the machine by ${money(gap)}.`
            return `The models lead${idle.length ? `; ${idle.map((x) => x.display_name).join(' and ')} ${idle.length === 1 ? 'has' : 'have'} not made a pick yet` : ''}.`
          })()}
        </p>
      </div>
      <div className="rt__lock">
        <h3>The Next Lock</h3>
        {nextLock ? (
          <>
            <span className="rt__lockgame"><Crest sport={nextLock.g.sport} abbr={nextLock.g.away.abbr} size={34} /><small>@</small><Crest sport={nextLock.g.sport} abbr={nextLock.g.home.abbr} size={34} /></span>
            <b className="rt__lockname">{nextLock.g.matchup}</b>
            <span className="rt__clock">{countdownText(new Date(nextLock.lock).toISOString(), now) ?? 'now'}</span>
            <small>picks close {new Date(nextLock.lock).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })} ET, 15 minutes before the start</small>
            <button type="button" onClick={onPicks}>Beat the machine · make a pick</button>
          </>
        ) : <p>No more competition games lock today.</p>}
      </div>
    </section>
  )
}
