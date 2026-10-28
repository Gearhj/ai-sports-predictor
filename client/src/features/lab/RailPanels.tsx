/**
 * Game Night's right rail under "Sweating now" (owner 10-27: the page looked lopsided). Two quiet panels:
 *   INJURY WATCH  every key player out / doubtful / questionable tonight, all sports, from AISP's availability board
 *   MY MODEL      the user's saved models (per sport) and their best edges tonight, one tap to the builder
 */
import { useEffect, useMemo, useState } from 'react'
import { operationalToday } from '../../services/operationalDate'
import { etClock, pct, usePulse } from '../pulse/pulseData'
import { availabilityFor, keyOut, useAvailability } from '../focus/FocusExtras'
import { MODEL_EVENT, applyModel, loadModel, loadReads, type MyModel } from './myModel'
import './lab.css'
import { ModelRecordCard, useModelTracker } from './ModelRecord'

const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }

export function InjuryWatch() {
  const today = operationalToday()
  const avail = useAvailability(today)
  const games = (avail.data?.games ?? []).filter((g) => keyOut(g).length)
  return (
    <section className="rp rp--inj" aria-label="Injury watch">
      <header><b>🩹 Injury watch</b><small>key players out or in doubt tonight · ESPN reports via AISP, every 10 min</small></header>
      {!avail.data ? <p className="rp__muted">Reading tonight’s injury reports…</p>
        : !games.length ? <p className="rp__muted">No key player is listed out or in doubt for tonight’s games.</p>
          : <ul>{games.slice(0, 10).map((g) => (
            <li key={g.espn_event_id} style={{ ['--sc' as string]: COLOR[g.league] ?? '#94a3b8' }}>
              <span className="rp__lg">{g.league}</span>
              <span><b>{g.teams.away?.abbreviation} @ {g.teams.home?.abbreviation}</b><small>{etClock(g.starts_at)}</small></span>
              <em>{keyOut(g).slice(0, 2).map((k) => `${k.name} ${k.status === 'QUESTIONABLE' ? 'Q' : k.status === 'DOUBTFUL' ? 'D' : 'OUT'}`).join(' · ')}</em>
            </li>))}</ul>}
    </section>
  )
}

export function MyModelRail({ onOpen }: { onOpen: () => void }) {
  useModelTracker()
  const today = operationalToday()
  const pulse = usePulse(today)
  const avail = useAvailability(today)
  const [m, setM] = useState<MyModel | null>(loadModel)
  useEffect(() => { const on = () => setM(loadModel()); window.addEventListener(MODEL_EVENT, on); return () => window.removeEventListener(MODEL_EVENT, on) }, [])
  const plays = useMemo(() => {
    if (!m) return []
    return pulse.games.filter((g) => g.day === today && g.phase === 'UPCOMING' && g.modelHome != null && m.sports[g.sport]).map((g) => {
      const a = availabilityFor(g, avail.data?.games)
      const out = a ? Object.entries(a.teams).flatMap(([side, t]) => t.key_players.filter((k) => ['OUT', 'DOUBTFUL', 'QUESTIONABLE'].includes(k.status))
        .map((k) => ({ side: (side === 'home' ? 'HOME' : 'AWAY') as 'HOME' | 'AWAY', status: k.status, name: k.name }))) : []
      const market = g.marketFresh ? g.marketHome : null
      const r = applyModel(g.sport, m.sports[g.sport], g.modelHome!, market, out, loadReads()[g.id] ?? null)
      const lean = r.p >= 0.5 ? 'HOME' : 'AWAY'
      const edge = market == null ? null : (lean === 'HOME' ? r.p - market : market - r.p) * 100
      return { g, p: lean === 'HOME' ? r.p : 1 - r.p, team: lean === 'HOME' ? g.home.abbr : g.away.abbr, edge }
    }).filter((x) => x.edge != null && x.edge > 0).sort((a, b) => (b.edge ?? 0) - (a.edge ?? 0)).slice(0, 4)
  }, [m, pulse.games, avail.data, today])
  return (
    <section className="rp rp--model" aria-label="My model">
      <header><b>🧪 My model</b><small>{m ? `v${m.version} · ${Object.keys(m.sports).join(', ')}` : 'not built yet'}</small></header>
      {!m ? <p className="rp__muted">Set your five decisions in <b>Build your model</b>, save it, and its best plays for tonight show up here.</p>
        : !plays.length ? <p className="rp__muted">No edge for your model yet tonight. It compares against the market price, which AISP buys about 75 minutes before each start.</p>
          : <ul>{plays.map((x) => <li key={x.g.id} style={{ ['--sc' as string]: COLOR[x.g.sport] ?? '#94a3b8' }}><span className="rp__lg">{x.g.sport}</span><span><b>{x.team} {pct(x.p)}</b><small>{x.g.away.abbr} @ {x.g.home.abbr} · {etClock(x.g.startsAt)}</small></span><em className="is-up">+{x.edge!.toFixed(1)} pts</em></li>)}</ul>}
      <button type="button" className="rp__open" onClick={onOpen}>{m ? 'Open my model ↓' : 'Build my model ↓'}</button>
      <ModelRecordCard compact />
    </section>
  )
}
