/**
 * YOU CALL IT: the strip under the live field. LIVE (game in progress): call the NEXT snap before it shows up
 * in the feed; it resolves when the play is logged. FILM SESSION (any other time): walk this game's real snaps
 * one by one, call each, and see what happened and what the model said (half XP).
 * The model's odds stay hidden until you lock, so the call is yours.
 */
import { useEffect, useMemo, useState } from 'react'
import type { FbPlay } from './footballData'
import { parseFbPlay } from './footballData'
import { hit as sfxHit, miss as sfxMiss } from '../../progress/sound'
import { GAMES_ON } from '../../release/flags'
import { applyTilt, classOfPlay, forecastFor, nextState, predict, teamTilt, type Grid, type PlayState } from './playcall'
import {
  OPTION_LABEL, OPTIONS, actualOption, isRight, optionProbs, resolveCall, saveCall, topOption, useYouCalls, xpOfCall,
  type Option, type YouCall,
} from './youCallStore'

interface Props {
  event: string
  plays: readonly FbPlay[]
  grid: Grid | null
  homeId: string
  abbrOf: (teamId: string | null | undefined) => string
  live: boolean
  isNfl: boolean
}

const ord = (d: number) => `${d}${['', 'st', 'nd', 'rd', 'th'][d] ?? 'th'}`
const pct = (p: number) => `${Math.round(p * 100)}%`
const actualOf = (p: FbPlay) => { const a = classOfPlay(parseFbPlay(p), p.text); return actualOption(a.cls, a.category) }
const spot = (s: PlayState, off: string) => `${ord(s.down)} & ${s.distance} · ${off} ball, ${s.yardsToEndzone} yds to go to the end zone · Q${s.period}`

export function YouCallIt({ event, plays, grid, homeId, abbrOf, live, isNfl }: Props) {
  const calls = useYouCalls()
  const [mode, setMode] = useState<'LIVE' | 'FILM'>(live ? 'LIVE' : 'FILM')
  useEffect(() => { setMode(live ? 'LIVE' : 'FILM') }, [live])
  const [cursor, setCursor] = useState<number | null>(null)
  const [flash, setFlash] = useState<{ key: string; right: boolean; xp: number } | null>(null)

  // ---- LIVE: the snap after the latest logged play
  const liveSnap = useMemo(() => {
    if (!grid || !plays.length) return null
    const ns = nextState(plays, homeId)
    if (!ns) return null
    const probs = applyTilt(predict(grid, ns.state), teamTilt(grid, plays, plays.length, ns.offenseId, homeId))
    return { key: `${event}:${plays[plays.length - 1].id}`, state: ns.state, offenseId: ns.offenseId, opt: optionProbs(probs) }
  }, [grid, plays, homeId, event])

  // Resolve LIVE calls once the next real snap is in the feed.
  useEffect(() => {
    for (const c of calls) {
      if (c.result || c.mode !== 'LIVE' || c.event !== event) continue
      const afterId = c.key.slice(event.length + 1)
      const i = plays.findIndex((p) => p.id === afterId)
      if (i < 0) continue
      for (let j = i + 1; j < plays.length; j++) {
        const a = actualOf(plays[j])
        if (!a) continue
        const right = isRight(c.pick, a)
        resolveCall(c.key, { actual: a, right, modelRight: isRight(c.modelPick, a), text: plays[j].text })
        setFlash({ key: c.key, right, xp: xpOfCall({ ...c, result: { actual: a, right, modelRight: isRight(c.modelPick, a), text: null } }) })
        break
      }
    }
  }, [plays, calls, event])
  useEffect(() => { if (!flash) return; if (flash.right) sfxHit(); else sfxMiss(); const t = window.setTimeout(() => setFlash(null), 2600); return () => window.clearTimeout(t) }, [flash])

  // ---- FILM: this game's real scrimmage snaps, in order
  const film = useMemo(() => {
    if (!grid) return []
    const out: number[] = []
    plays.forEach((p, i) => { if (actualOf(p) && forecastFor(grid, plays, i, homeId)) out.push(i) })
    return out
  }, [grid, plays, homeId])
  const filmIdx = cursor ?? film[0] ?? null
  const filmSnap = useMemo(() => {
    if (!grid || filmIdx == null) return null
    const f = forecastFor(grid, plays, filmIdx, homeId)
    const p = plays[filmIdx]
    if (!f) return null
    return { key: `${event}:${p.id}:film`, state: f.state, offenseId: p.offense ?? p.start.team ?? '', opt: optionProbs(f.probs), play: p }
  }, [grid, plays, filmIdx, homeId, event])

  if (!isNfl) {
    return <section className="yc" aria-label="You call it"><header className="yc__head"><b>YOU CALL IT</b></header><p className="yc__muted">The play-call model is trained on NFL snaps, so You Call It runs on NFL games only.</p></section>
  }

  const snap = mode === 'LIVE' ? liveSnap : filmSnap
  const mine = snap ? calls.find((c) => c.key === snap.key) : undefined
  const game = calls.filter((c) => c.event === event && c.result)
  const you = game.filter((c) => c.result!.right).length
  const model = game.filter((c) => c.result!.modelRight).length
  let streak = 0
  for (const c of [...game].sort((a, b) => b.at.localeCompare(a.at))) { if (c.result!.right) streak++; else break }
  const xpHere = game.reduce((t, c) => t + xpOfCall(c), 0)
  const last = mode === 'LIVE' ? [...game].sort((a, b) => b.at.localeCompare(a.at))[0] : undefined

  const call = (o: Option) => {
    if (!snap || mine) return
    const c: YouCall = { key: snap.key, event, mode, pick: o, modelPick: topOption(snap.opt), pModel: snap.opt[o], at: new Date().toISOString() }
    saveCall(c)
    if (mode === 'FILM' && filmSnap) {
      const a = actualOf(filmSnap.play)!
      const right = isRight(o, a)
      const result = { actual: a, right, modelRight: isRight(c.modelPick, a), text: filmSnap.play.text }
      resolveCall(c.key, result)
      setFlash({ key: c.key, right, xp: xpOfCall({ ...c, result }) })
    }
  }
  const nextFilm = () => { const k = film.indexOf(filmIdx ?? -1); if (k >= 0 && k < film.length - 1) setCursor(film[k + 1]) }
  const jumpQ4 = () => { const i = film.find((x) => (plays[x].period ?? 0) >= 4); if (i != null) setCursor(i) }
  const shown = mine != null

  return (
    <section className={`yc${flash ? (flash.right ? ' is-right' : ' is-wrong') : ''}`} aria-label="You call it">
      <header className="yc__head">
        <b>YOU CALL IT</b>
        <div className="yc__tabs" role="tablist">
          <button role="tab" aria-selected={mode === 'LIVE'} className={mode === 'LIVE' ? 'is-on' : ''} disabled={!live} onClick={() => setMode('LIVE')} title={live ? '' : 'Live when the game is on'}>Live</button>
          <button role="tab" aria-selected={mode === 'FILM'} className={mode === 'FILM' ? 'is-on' : ''} onClick={() => setMode('FILM')}>Film session</button>
        </div>
        <span className="yc__score" title="This game">YOU {you}/{game.length} · MODEL {model}/{game.length}{streak >= 2 ? <em> 🔥{streak}</em> : null}{GAMES_ON && xpHere ? <em> +{xpHere} XP</em> : null}</span>
      </header>
      {!grid ? <p className="yc__muted">Loading the play-call model…</p>
        : !snap ? <p className="yc__muted">{mode === 'LIVE' ? 'Waiting for the next snap with a down and distance.' : 'No scrimmage plays to call in this game yet.'}</p>
        : (
          <div className="yc__body">
            <div className="yc__sit"><b>{mode === 'LIVE' ? 'NEXT SNAP' : 'CALL THIS SNAP'}</b><span>{spot(snap.state, abbrOf(snap.offenseId))}</span></div>
            <div className="yc__opts">
              {OPTIONS.map((o) => {
                const res = mine?.result
                const isPick = mine?.pick === o
                const hit = res && isRight(o, res.actual)
                return (
                  <button key={o} type="button" className={`yc__opt${isPick ? ' is-pick' : ''}${hit ? ' is-hit' : ''}${res && isPick && !res.right ? ' is-miss' : ''}`} disabled={shown} onClick={() => call(o)}>
                    <b>{OPTION_LABEL[o]}</b>
                    <small>{shown ? `model ${pct(snap.opt[o])}${topOption(snap.opt) === o ? ' ★' : ''}` : 'tap to call'}</small>
                    {shown ? <i style={{ width: `${snap.opt[o] * 100}%` }} /> : null}
                  </button>
                )
              })}
            </div>
            <div className="yc__out" aria-live="polite">
              {mine?.result ? (
                <>
                  <b className={mine.result.right ? 'is-up' : 'is-down'}>{mine.result.right ? `✔ CALLED IT${GAMES_ON && xpOfCall(mine) ? ` +${xpOfCall(mine)} XP` : ''}` : '✘ MISSED'}</b>
                  <span>{mine.result.text ?? ''}</span>
                  {mode === 'FILM' ? <span className="yc__nav"><button type="button" onClick={nextFilm}>Next snap →</button><button type="button" onClick={jumpQ4}>Jump to Q4</button></span> : null}
                </>
              ) : mine ? <span>Locked: <b>{OPTION_LABEL[mine.pick]}</b>. The model had it at {pct(mine.pModel)}. Waiting for the snap…</span>
                : last ? <span className={`yc__last ${last.result!.right ? 'is-up' : 'is-down'}`}>Last call: <b>{last.result!.right ? '✔ called it' : '✘ missed'}</b> ({OPTION_LABEL[last.pick]}{xpOfCall(last) ? `, +${xpOfCall(last)} XP` : ''}) · {last.result!.text ?? ''} · <i>call the next snap</i></span>
                : <span className="yc__muted">The model&apos;s odds stay hidden until you call.{GAMES_ON ? ' Rarer calls pay more XP; beat the model’s top call for +20.' : ''}{mode === 'LIVE' ? ' If your TV is ahead of the feed, play fair.' : ' Film session pays half.'}</span>}
            </div>
          </div>
        )}
      {flash ? <div className={`yc__flash ${flash.right ? 'is-right' : 'is-wrong'}`} aria-hidden="true">{flash.right ? `CALLED IT${flash.xp ? ` +${flash.xp}` : ''}` : 'MISSED'}</div> : null}
    </section>
  )
}
