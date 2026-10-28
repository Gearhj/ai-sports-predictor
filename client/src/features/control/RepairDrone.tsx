/**
 * THE REPAIR DRONE (Mission Control, plan page list). Every 5 minutes AISP's gate alarm checks every game; when a
 * known gate fails it fixes it itself (re-buys a price, reruns an import, runs a decision checkpoint, clears a stuck
 * lock, issues an NFL week grant) and logs it. This panel shows that log in plain words, live, with a drone that
 * flies out each time a new repair lands. Source: aisp-analysis/gate-alarm/heal.jsonl via the test-mode preview.
 */
import { useEffect, useRef, useState } from 'react'
import { etClock } from '../pulse/pulseData'
import './drone.css'

interface Heal { at: string; heal: string; job?: string; why?: string; status?: string; error?: string; issued?: unknown; terminated?: boolean }

const JOB_WORDS: Record<string, string> = {
  'collect-nfl-pregame-odds': 'bought the NFL price', 'collect-nhl-odds-api': 'bought the NHL price', 'collect-nba-odds-api': 'bought the NBA price',
  'collect-cfb-odds-api': 'bought the CFB price', 'collect-odds-checkpoint': 'bought the MLB price',
  'import-nfl-final-pair': 're-ran the NFL forecast + approval import', 'consume-model-exchange-research-artifacts': 're-ran the producer import',
  'generate-nfl-final-pregame-forecasts': 'started the NFL final forecast', 'generate-nhl-forecasts': 'regenerated NHL forecasts',
  'evaluate-nfl-paper-checkpoint': 'ran the NFL decision checkpoint', 'evaluate-nba-paper-checkpoint': 'ran the NBA decision checkpoint',
  'evaluate-nhl-paper-checkpoint': 'ran the NHL decision checkpoint', 'evaluate-cfb-paper-checkpoint': 'ran the CFB decision checkpoint',
  'freeze-shadow-experiment': 'ran the MLB decision checkpoint', 'refresh-weather': 'refreshed the weather',
}
const words = (h: Heal): { line: string; tone: 'ok' | 'bad' | 'go' | 'wait' } => {
  const job = JOB_WORDS[h.job ?? ''] ?? h.job ?? 'a job'
  switch (h.heal) {
    case 'TRIGGER': return { line: `Dispatched: ${job}. Why: ${h.why ?? ''}`, tone: 'go' }
    case 'TRIGGER_RESULT': return h.status === 'succeeded' ? { line: `Fixed: ${job} succeeded.${h.why ? ` (${h.why})` : ''}`, tone: 'ok' } : { line: `Repair failed: ${job} (${h.error ?? h.status ?? 'unknown'}). Claude is alerted.`, tone: 'bad' }
    case 'SKIP_JOB_RUNNING': return { line: `Held off: ${job} was already running. Why it looked: ${h.why ?? ''}`, tone: 'wait' }
    case 'WEEK_AUTHORITY': return { line: `Issued the missing NFL week grant (${JSON.stringify(h.issued ?? '')}).`, tone: 'ok' }
    case 'NFLLABS_LOCK_TERMINATE': return { line: `Cleared a stuck research session blocking the NFL import${h.terminated ? '' : ' (it had already gone)'}.`, tone: 'ok' }
    default: return { line: `${h.heal}${h.error ? `: ${h.error}` : ''}`, tone: h.heal.endsWith('ERROR') ? 'bad' : 'wait' }
  }
}

export function RepairDrone() {
  const [heals, setHeals] = useState<Heal[] | null>(null)
  const [flying, setFlying] = useState(false)
  const last = useRef<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => fetch('/preview-api/heals').then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (!alive || !d) return
      const list = (d.heals as Heal[]).slice().reverse()
      const newest = list[0] ? `${list[0].at}|${list[0].heal}|${list[0].job ?? ''}` : null
      if (last.current && newest && newest !== last.current) { setFlying(true); window.setTimeout(() => setFlying(false), 2400) }
      last.current = newest
      setHeals(list)
    }).catch(() => { if (alive) setHeals((h) => h ?? []) })
    void load()
    const t = window.setInterval(load, 30_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])
  const real = (heals ?? []).filter((h) => !/test/i.test(h.why ?? ''))
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
  const todays = real.filter((h) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(h.at)) === today)
  const fixes = todays.filter((h) => (h.heal === 'TRIGGER_RESULT' && h.status === 'succeeded') || h.heal === 'WEEK_AUTHORITY' || h.heal === 'NFLLABS_LOCK_TERMINATE').length
  return (
    <section className={`sl-card rd${flying ? ' is-flying' : ''}`} aria-label="The repair drone: automatic fixes">
      <div className="rd__bay" aria-hidden="true">
        <span className="rd__drone">🛸</span>
        <span className="rd__beam" />
      </div>
      <div className="rd__main">
        <header className="rd__head">
          <h2>THE REPAIR DRONE</h2>
          <span className="rd__status"><i className="rd__dot" /> Patrolling every 5 minutes · {fixes} fix{fixes === 1 ? '' : 'es'} today</span>
        </header>
        <p className="rd__how">When a gate AISP owns fails (no price, a missing import, an undecided game, a stuck lock), the drone fixes it on the spot and logs it here. Fixes only: it never places a bet or spends money.</p>
        {heals == null ? <p className="rd__empty">Contacting the drone…</p> : !real.length ? <p className="rd__empty">No repairs needed yet. Every gate has passed on its own.</p> : (
          <ol className="rd__log">
            {real.slice(0, 8).map((h, i) => { const w = words(h); return <li key={`${h.at}${h.heal}${i}`} className={`is-${w.tone}${i === 0 && flying ? ' is-new' : ''}`}><time>{etClock(h.at)}</time><span>{w.line}</span></li> })}
          </ol>
        )}
      </div>
    </section>
  )
}
