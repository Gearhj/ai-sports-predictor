/**
 * THE FILM ROOM (MMQB, plan page list): roll the tape on every decision AISP made against a recorded market price.
 * Each game is a dot in one of four rooms — CALLED IT (AISP took a 2+ point stand and was right), FOOLED (stand,
 * wrong), and the two "rode the market" rooms where there was no real disagreement. Click a dot and the film strip
 * plays: what AISP said, what the market said, what AISP did, what happened, with the telestrator's read. Reads the
 * scored-games response the Performance page uses; nothing new is fetched beyond that one shared request.
 */
import * as d3 from 'd3'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { gameHref } from '../../services/commandCenter'
import { useScoredGames } from '../galaxy/useScoredGames'
import { ROOM_LABEL, STAND, clipsOf, readClip, standLine, type Clip, type FilmGame } from './film'
import './film.css'

const W = 1000, H = 300, M = { l: 20, r: 20, t: 26, b: 30 }
const COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
const WINDOWS: [string, number][] = [['Last 7 days', 7], ['Last 30 days', 30], ['Season', 400]]

export function FilmRoom() {
  const res = useScoredGames()
  const games = (res.data?.games ?? []) as FilmGame[]
  const [days, setDays] = useState(7)
  const [sport, setSport] = useState('ALL')
  const [pick, setPick] = useState<Clip | null>(null)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('aisp-film-open') !== '0' } catch { return true } })
  const toggle = () => setOpen((v) => { try { localStorage.setItem('aisp-film-open', v ? '0' : '1') } catch { /* private window */ } return !v })
  const since = useMemo(() => Date.now() - days * 86400000, [days])
  const sports = useMemo(() => ['ALL', ...Array.from(new Set(games.filter((g) => g.decision?.market_p_home != null).map((g) => g.sport))).sort()], [games])
  const clips = useMemo(() => clipsOf(games, since, sport), [games, since, sport])
  const line = standLine(clips)
  // Open on the latest stand (the interesting tape), else the latest clip.
  useEffect(() => { setPick([...clips].reverse().find((c) => c.stand >= STAND) ?? clips[clips.length - 1] ?? null) }, [clips])
  const svg = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const el = svg.current
    if (!el || !open) return
    const s = d3.select(el); s.selectAll('*').remove()
    const maxStand = Math.max(0.12, d3.max(clips, (c) => c.stand) ?? 0.12)
    const x = d3.scaleSqrt().domain([0, maxStand]).range([M.l + 10, W - M.r - 10])
    const mid = (M.t + H - M.b) / 2, yWon = (M.t + mid) / 2 + 4, yLost = (mid + H - M.b) / 2 + 4
    const xs = x(STAND)
    s.append('rect').attr('class', 'fr-room fr-room--called').attr('x', xs).attr('y', M.t).attr('width', W - M.r - xs).attr('height', mid - M.t)
    s.append('rect').attr('class', 'fr-room fr-room--fooled').attr('x', xs).attr('y', mid).attr('width', W - M.r - xs).attr('height', H - M.b - mid)
    s.append('rect').attr('class', 'fr-room fr-room--rode').attr('x', M.l).attr('y', M.t).attr('width', xs - M.l).attr('height', H - M.b - M.t)
    s.append('line').attr('class', 'fr-split').attr('x1', M.l).attr('x2', W - M.r).attr('y1', mid).attr('y2', mid)
    s.append('line').attr('class', 'fr-bar').attr('x1', xs).attr('x2', xs).attr('y1', M.t - 8).attr('y2', H - M.b)
    s.append('text').attr('class', 'fr-tag fr-tag--called').attr('x', W - M.r - 8).attr('y', M.t + 14).attr('text-anchor', 'end').text('CALLED IT · stood apart and won')
    s.append('text').attr('class', 'fr-tag fr-tag--fooled').attr('x', W - M.r - 8).attr('y', H - M.b - 8).attr('text-anchor', 'end').text('FOOLED · stood apart and lost')
    s.append('text').attr('class', 'fr-tag').attr('x', xs - 6).attr('y', M.t - 12).attr('text-anchor', 'end').text('◀ RODE THE MARKET')
    s.append('text').attr('class', 'fr-tag fr-tag--bar').attr('x', xs + 6).attr('y', M.t - 12).text('2-PT STAND ▶')
    s.append('text').attr('class', 'fr-side').attr('x', M.l + 4).attr('y', yWon - 30).text('LEAN WON')
    s.append('text').attr('class', 'fr-side').attr('x', M.l + 4).attr('y', yLost + 38).text('LEAN LOST')
    s.append('g').attr('class', 'fr-ax').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).tickValues([0, 0.02, 0.05, 0.1, 0.15, 0.2].filter((v) => v <= maxStand)).tickFormat((v) => `${Math.round(+v * 100)} pts`))
    type N = d3.SimulationNodeDatum & { c: Clip }
    const nodes: N[] = clips.map((c) => ({ c, x: x(c.stand), y: c.won ? yWon : yLost }))
    const sim = d3.forceSimulation(nodes).force('x', d3.forceX<N>((d) => x(d.c.stand)).strength(1)).force('y', d3.forceY<N>((d) => (d.c.won ? yWon : yLost)).strength(0.12))
      .force('c', d3.forceCollide(6.2)).stop()
    for (let i = 0; i < 200; i++) sim.tick()
    for (const n of nodes) n.y = n.c.won ? Math.min(mid - 7, Math.max(M.t + 7, n.y!)) : Math.max(mid + 7, Math.min(H - M.b - 7, n.y!))
    s.append('g').selectAll('circle').data(nodes).join('circle')
      .attr('class', (d) => `fr-dot${d.c.g.wager?.stake ? ' is-bet' : ''}${pick && d.c.g === pick.g ? ' is-pick' : ''}`)
      .attr('fill', (d) => COLOR[d.c.g.sport] ?? '#e2e8f0').attr('cx', (d) => x(0) + (d.x! - x(0)) * 0.2).attr('cy', mid).attr('r', 0)
      .style('cursor', 'pointer').on('click', (_, d) => setPick(d.c))
      .transition().delay((_, i) => i * 6).duration(650).ease(d3.easeCubicOut).attr('cx', (d) => d.x!).attr('cy', (d) => d.y!).attr('r', 5)
  }, [open, clips, pick])

  const roomCount = (r: Clip['room']) => clips.filter((c) => c.room === r).length
  return (
    <section className="fr" aria-label="The Film Room">
      <header className="fr__head">
        <h2>THE FILM ROOM</h2>
        <span>When AISP disagreed with the market, who was right? Click any game to roll the tape.</span>
        <button type="button" className="fr__fold" aria-expanded={open} onClick={toggle}>{open ? 'Collapse ▴' : 'Roll the tape ▾'}</button>
      </header>
      {open && (
        <>
          {res.loading && !games.length && <p className="fr__muted">Cueing the tape…</p>}
          {res.error && !games.length && <p className="fr__muted">Scored games are not available right now.</p>}
          {games.length > 0 && (
            <>
              <div className="fr__controls">
                <div className="fr__chips" role="group" aria-label="Window">
                  {WINDOWS.map(([l, d]) => <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{l}</button>)}
                </div>
                <div className="fr__chips" role="group" aria-label="Sport">
                  {sports.map((s) => <button key={s} type="button" aria-pressed={sport === s} onClick={() => setSport(s)}>{s === 'ALL' ? 'All sports' : s}</button>)}
                </div>
              </div>
              <div className="fr__score">
                <div className="fr__tile fr__tile--called"><small>CALLED IT</small><b>{roomCount('CALLED_IT')}</b></div>
                <div className="fr__tile fr__tile--fooled"><small>FOOLED</small><b>{roomCount('FOOLED')}</b></div>
                <div className="fr__tile"><small>RODE THE MARKET</small><b>{roomCount('RODE_WIN') + roomCount('RODE_LOSS')}</b></div>
                <p className="fr__verdict">
                  {line.n === 0 ? `No 2-point stands in this window (${clips.length} games on tape).`
                    : <>On its <b>{line.n}</b> stands AISP went <b>{line.wins}–{line.n - line.wins}</b>. The market expected <b>{line.expected.toFixed(1)}</b> wins on those sides and AISP expected <b>{line.modelExpected.toFixed(1)}</b> —
                      {' '}{Math.abs(line.wins - line.expected) < Math.abs(line.wins - line.modelExpected) ? <b className="is-market">the market’s number was closer</b> : <b className="is-aisp">AISP’s number was closer</b>}.</>}
                </p>
              </div>
              {clips.length > 0 && <svg ref={svg} className="fr__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Every decision by how far AISP stood from the market and whether its lean won" />}
              {pick && (
                <div className="fr__strip" key={pick.g.game_id ?? pick.at}>
                  <div className="fr__frame"><small>1 · AISP SAID</small><b>{pick.team} {Math.round(pick.model * 100)}%</b><span>{pick.g.away} @ {pick.g.home} · {pick.g.sport} · {new Date(pick.at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span></div>
                  <div className="fr__frame"><small>2 · THE MARKET SAID</small><b>{pick.team} {Math.round(pick.market * 100)}%</b><span>{pick.stand >= STAND ? `${(pick.stand * 100).toFixed(1)}-pt stand` : 'within 2 pts: no stand'}</span></div>
                  <div className="fr__frame"><small>3 · AISP DID</small><b>{pick.g.wager?.stake ? `BET $${pick.g.wager.stake.toFixed(2)}` : (pick.g.decision?.recommendation ?? 'NO BET').replace(/_/g, ' ')}</b><span>{pick.g.wager?.side ? `on ${pick.g.wager.side === 'HOME' ? pick.g.home : pick.g.wager.side === 'AWAY' ? pick.g.away : pick.g.wager.side}` : 'paper decision recorded'}</span></div>
                  <div className={`fr__frame fr__frame--${pick.won ? 'won' : 'lost'}`}><small>4 · WHAT HAPPENED</small><b>{pick.g.away} {pick.g.final?.away ?? '?'} – {pick.g.final?.home ?? '?'} {pick.g.home}</b><span>{ROOM_LABEL[pick.room]}</span></div>
                  <p className="fr__tele">🎙 {readClip(pick)} <Link to={gameHref(pick.g.sport, pick.g.game_id)}>Open the game →</Link></p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
