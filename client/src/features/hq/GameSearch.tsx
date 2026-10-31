/**
 * The header search on Home and Games (tester feedback 10-31: "the search bar doesn't work"). It used to filter the
 * game cards further down the page and nothing else: no suggestions, Enter did nothing, and a team with no game
 * that day, a player or a question just emptied the page. Now it lists the day's matching games under the box,
 * Enter (or a click) opens one, and a miss says so plainly. It still filters the page's cards through `onChange`.
 */
import { useId, useMemo, useState } from 'react'
import { etClock, type PulseGame } from '../pulse/pulseData'
import './gameSearch.css'

const live = (g: PulseGame) => g.phase === 'LIVE' || g.phase === 'STARTED'

export function matchesGame(g: PulseGame, q: string): boolean {
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  const hay = `${g.away.abbr} ${g.away.name} ${g.home.abbr} ${g.home.name} ${g.away.abbr} @ ${g.home.abbr} ${g.sport}`.toLowerCase()
  return needle.split(/\s+/).every((word) => hay.includes(word))
}

export function GameSearch({ games, value, onChange, onOpen }: {
  games: PulseGame[]
  value: string
  onChange: (q: string) => void
  onOpen: (g: PulseGame) => void
}) {
  const [focused, setFocused] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const hits = useMemo(() => (value.trim()
    ? games.filter((g) => matchesGame(g, value))
      .sort((a, b) => Number(live(b)) - Number(live(a)) || Date.parse(a.startsAt) - Date.parse(b.startsAt)).slice(0, 8)
    : []), [games, value])
  const open = focused && value.trim().length > 0
  const pick = (g: PulseGame) => { onOpen(g); onChange(''); setFocused(false) }

  return (
    <div className="hq-search gs">
      <label><span className="lf-sr">Search games</span>
        <input value={value} role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
          placeholder="Search games by team, e.g. Celtics or BOS…"
          onChange={(e) => { onChange(e.target.value); setActive(0) }}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => setFocused(false), 150)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, hits.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
            else if (e.key === 'Enter' && hits.length) { e.preventDefault(); pick(hits[Math.min(active, hits.length - 1)]) }
            else if (e.key === 'Escape') { onChange(''); setFocused(false) }
          }} />
      </label>
      {open ? (
        <ul id={listId} className="gs__list" role="listbox">
          {hits.map((g, i) => (
            <li key={g.id} role="option" aria-selected={i === active}>
              <button type="button" className={i === active ? 'is-on' : ''} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(g)}>
                <b>{g.away.abbr} @ {g.home.abbr}</b>
                <span>{g.away.name} at {g.home.name}</span>
                <small>{g.sport} · {live(g) ? (g.score ? `LIVE ${g.score.away}–${g.score.home}` : 'LIVE') : g.phase === 'FINAL' ? (g.score ? `Final ${g.score.away}–${g.score.home}` : 'Final') : `${etClock(g.startsAt)} ET`}</small>
              </button>
            </li>
          ))}
          {!hits.length ? <li className="gs__none">No game on this day matches “{value.trim()}”. Search by team name or abbreviation, or use the ‹ › arrows to change the day.</li> : null}
        </ul>
      ) : null}
    </div>
  )
}
