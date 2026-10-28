/**
 * LIVE VIEW for the NBA and the NHL (owner 10-24: "the Live Views for all of the sports"). Test mode only.
 *
 * NBA: the half court with every shot both teams took (made filled, missed hollow), the newest shot flying to the rim
 * once with a SPLASH / SLAM call, HOT ZONES (each zone's field-goal rate against a typical NBA rate), GAME FLOW (the
 * score margin over the game with ESPN's live win probability), and the RUN DETECTOR that calls a scoring run while
 * it is happening.
 * NHL: the full rink with every shot, miss, block and goal (each team shown attacking its own end), the DANGER MAP
 * (how close and central each chance was), the SHOT-ATTEMPT MOMENTUM line, who is ON THE ICE right now, and a GOAL
 * callout with the goal light the moment a new goal lands.
 * Both: score bug with logos, AISP's pregame model, market and paper bet for this game, leaders, box score, play feed.
 *
 * Feed: ESPN public play-by-play via the preview server, never stored, never used for settlement. Animations run once
 * per new play and then hold still.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { MissionGame } from '../../mission/missionData'
import { stageOf } from '../../mission/missionData'
import { LiveShell, useFavorites, type StripInfo } from '../football/LiveShell'
import { elapsedAt, onNavy, periodName, teamPair, useArenaLive, type ArenaFeed, type ArenaPlay, type ArenaTeam } from './arenaData'
import { NbaArena } from './NbaArena'
import { NhlArena } from './NhlArena'
import './arena.css'
import { VibeBox } from '../../crowd/VibeBox'

const etDate = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso)).replace(/-/g, '')
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)

export function ArenaLive({ game, games, onPickGame, sportFilter, onSport, booth }: { game: MissionGame; games: MissionGame[]; onPickGame: (id: string) => void; sportFilter: string | null; onSport: (s: string | null) => void; booth: React.ReactNode }) {
  const league = game.sport === 'NBA' ? 'nba' : 'nhl'
  const live = game.status === 'live' || game.phase === 'STARTED'
  const { data, error } = useArenaLive(league, etDate(game.starts_at), game.home.abbr, game.away.abbr, live)
  const [favs, toggleFav] = useFavorites()
  const [boothOpen, setBoothOpen] = useState(false)
  const feed = data?.found ? data : null
  const strip: StripInfo = { league: league.toUpperCase(), week: null, season: null, venue: feed?.venue ?? null, city: null, weather: null, model: league === 'nba' ? 'NBA Gen1' : 'NHL Gen1', broadcast: null,
    live: feed?.status.state === 'in', clock: feed ? (feed.status.state === 'in' ? `${periodName(league, feed.status.period)} ${feed.status.clock ?? ''}` : feed.status.short ?? '') : '', status: feed?.status.state === 'post' ? 'FINAL' : 'PREGAME', date: game.starts_at }
  return (
    <LiveShell games={games} gameId={game.game_id} onPick={onPickGame} sportFilter={sportFilter} onSport={onSport} favs={favs} onFav={toggleFav} strip={strip} onBooth={() => setBoothOpen((v) => !v)}>
      <div className="ar" data-league={league}>
        {!feed ? <p className="ar-note">{error ? (error.includes('preview') ? 'The live NBA/NHL feed is only wired on the test-mode preview server (127.0.0.1:5199).' : `Live feed error: ${error}`) : data && !data.found ? data.reason : 'Loading the play-by-play…'}</p> : (
          <>
            <div className="lf-score-row"><ScoreBug feed={feed} league={league} /><VibeBox league={league} home={{ abbr: game.home.abbr, name: game.home.name }} away={{ abbr: game.away.abbr, name: game.away.name }} /></div>
            <Pregame game={game} feed={feed} />
            {league === 'nba' ? <NbaLive game={game} feed={feed} /> : <NhlLive game={game} feed={feed} />}
          </>
        )}
        {boothOpen ? <div className="ar-booth">{booth}</div> : null}
        <footer className="ar-foot">Research and paper only. Play-by-play, coordinates, players and {league === 'nba' ? 'win probability' : 'on-ice lineups'}: ESPN's public feed via the test-mode preview server, never stored and never used for settlement. AISP's pregame numbers come from Mission Control.</footer>
      </div>
    </LiveShell>
  )
}

function ScoreBug({ feed, league }: { feed: ArenaFeed; league: 'nba' | 'nhl' }) {
  const { home, away } = feed.teams
  const side = (t: ArenaTeam, right = false) => (
    <div className={`ar-sb__team${right ? ' is-home' : ''}`} style={{ ['--tc' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
      {t.logo ? <img src={t.logo} alt="" /> : null}
      <span><b><span className="ar-sb__long">{t.short || t.abbr}</span><span className="ar-sb__abbr">{t.abbr}</span></b><small>{t.record ?? ''}</small></span>
      <strong className={t.score > (t === home ? away.score : home.score) ? 'is-lead' : ''}>{t.score}</strong>
    </div>
  )
  const periods = Math.max(home.linescores.length, away.linescores.length, league === 'nba' ? 4 : 3)
  return (
    <section className="ar-sb" aria-label="Score" style={{ ['--ac' as string]: teamPair(home, away).away, ['--hc' as string]: teamPair(home, away).home } as CSSProperties}>
      {side(away)}
      <div className="ar-sb__mid">
        <b className={feed.status.state === 'in' ? 'is-live' : ''}>{feed.status.state === 'in' ? `● ${periodName(league, feed.status.period)} ${feed.status.clock ?? ''}` : feed.status.short}</b>
        <table><thead><tr><th />{Array.from({ length: periods }, (_, i) => <th key={i}>{periodName(league, i + 1)}</th>)}</tr></thead>
          <tbody>{[away, home].map((t) => <tr key={t.id}><th>{t.abbr}</th>{Array.from({ length: periods }, (_, i) => <td key={i}>{t.linescores[i] ?? ''}</td>)}</tr>)}</tbody></table>
      </div>
      {side(home, true)}
    </section>
  )
}

/** Home no-vig probability from a two-sided American pair (the fallback when no consensus was recorded). */
function noVigHome(home: number | null | undefined, away: number | null | undefined): number | null {
  const imp = (x: number) => (x > 0 ? 100 / (x + 100) : -x / (-x + 100))
  if (home == null || away == null) return null
  const h = imp(home); const a = imp(away)
  return h / (h + a)
}

/**
 * The pregame market's home probability, from the best evidence the game carries: the recorded consensus, the best
 * pair's no-vig price, the decision's market probability for its side, else the bet's own price (that one keeps its vig).
 */
function marketHome(game: MissionGame): { p: number; how: string } | null {
  const p = stageOf(game, 'prices'); const d = stageOf(game, 'decision'); const b = stageOf(game, 'bet')
  const pair = p.consensus_home ?? noVigHome(p.best?.home?.american, p.best?.away?.american)
  if (pair != null) return { p: pair, how: 'pregame market' }
  const side = (b.side ?? '').split(' ')[0].toUpperCase()
  const isHome = side === game.home.abbr.toUpperCase() ? true : side === game.away.abbr.toUpperCase() ? false : null
  if (isHome == null) return null
  if (d.market_probability != null) return { p: isHome ? d.market_probability : 1 - d.market_probability, how: 'market at the decision' }
  if (b.american != null) { const imp = b.american > 0 ? 100 / (b.american + 100) : -b.american / (-b.american + 100); return { p: isHome ? imp : 1 - imp, how: 'the bet price (with vig)' } }
  return null
}

function Pregame({ game, feed }: { game: MissionGame; feed: ArenaFeed }) {
  const f = stageOf(game, 'forecast'); const b = stageOf(game, 'bet'); const d = stageOf(game, 'decision')
  const mk = marketHome(game)
  const wp = feed.win_probability.length ? feed.win_probability[feed.win_probability.length - 1].home : null
  return (
    <section className="ar-pre" aria-label="AISP before the game">
      <span><small>AISP model, pregame</small><b>{feed.teams.home.abbr} {pct(f.home_win_prob ?? null)}</b></span>
      <span><small>Market, pregame{mk && mk.how !== 'pregame market' ? ` · ${mk.how}` : ''}</small><b>{feed.teams.home.abbr} {pct(mk?.p ?? null)}</b></span>
      {wp != null ? <span className="is-live"><small>Live win probability (ESPN)</small><b>{feed.teams.home.abbr} {pct(wp)}</b></span> : null}
      <span className={b.state === 'DONE' ? 'is-bet' : ''}><small>AISP paper bet</small><b>{b.state === 'DONE' ? (b.label ?? '').replace(/Â·/g, '·') : d.label ?? 'No bet'}</b></span>
    </section>
  )
}

// ---------------------------------------------------------------- NHL --

/** The NHL view starts its live win estimate from the pregame market (else AISP's model) and closes with the box and feed. */
function NhlLive({ game, feed }: { game: MissionGame; feed: ArenaFeed }) {
  const f = stageOf(game, 'forecast'); const b = stageOf(game, 'bet')
  const market = marketHome(game)
  const prior = market?.p ?? f.home_win_prob ?? null
  return (
    <NhlArena feed={feed} prior={prior} priorSource={market ? market.how : 'AISP pregame model'} bet={b.state === 'DONE' ? (b.label ?? '').replace(/Â·/g, '·') : null}>
      <BoxScore feed={feed} />
      <PlayFeed feed={feed} league="nhl" />
    </NhlArena>
  )
}

// ---------------------------------------------------------------- NBA --

/** The NBA view: the story, the arena, who wins, big moments, players; the run detector sits beside the court. */
function NbaLive({ game, feed }: { game: MissionGame; feed: ArenaFeed }) {
  const b = stageOf(game, 'bet')
  return (
    <NbaArena feed={feed} bet={b.state === 'DONE' ? (b.label ?? '').replace(/Â·/g, '·') : null} side={<Runs feed={feed} />}>
      <BoxScore feed={feed} />
      <PlayFeed feed={feed} league="nba" />
    </NbaArena>
  )
}

function Runs({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const runs = useMemo(() => {
    let cur: { team: string; pts: number; from: ArenaPlay; to: ArenaPlay } | null = null
    const all: Array<{ team: string; pts: number; from: ArenaPlay; to: ArenaPlay }> = []
    let ph = 0; let pa = 0
    feed.plays.forEach((p) => {
      if (p.home == null || p.away == null) return
      const dh = p.home - ph; const da = p.away - pa
      ph = p.home; pa = p.away
      if (!dh && !da) return
      const t = dh > 0 ? home.id : away.id; const v = dh > 0 ? dh : da
      if (cur && cur.team === t) { cur.pts += v; cur.to = p } else { if (cur) all.push(cur); cur = { team: t, pts: v, from: p, to: p } }
    })
    if (cur) all.push(cur)
    return { all, current: cur as { team: string; pts: number; from: ArenaPlay; to: ArenaPlay } | null }
  }, [feed.plays, home.id, away.id])
  const best = (id: string) => runs.all.filter((r) => r.team === id).sort((a, b) => b.pts - a.pts)[0]
  const name = (id: string) => (id === home.id ? home : away)
  const big = runs.current && runs.current.pts >= 7 && feed.status.state === 'in' ? runs.current : null
  return (
    <section className="ar-card ar-runs" aria-label="Scoring runs">
      <h2>Run Detector</h2>
      {big ? <div className="ar-run-now" style={{ ['--c' as string]: onNavy(name(big.team).color, name(big.team).alt) } as CSSProperties}><b>{name(big.team).abbr} ON A {big.pts}-0 RUN</b><small>since {periodName('nba', big.from.period)} {big.from.clock}</small></div> : null}
      <ul>{[away, home].map((t) => { const r = best(t.id); return <li key={t.id} style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}><b>{t.abbr}</b>{r ? <span>biggest run <strong>{r.pts}-0</strong> · {periodName('nba', r.from.period)} {r.from.clock} to {periodName('nba', r.to.period)} {r.to.clock}</span> : <span>no run yet</span>}</li> })}</ul>
    </section>
  )
}

// ---------------------------------------------------------------- NHL --

// ------------------------------------------------------------- shared --

function BoxScore({ feed }: { feed: ArenaFeed }) {
  const { home, away } = feed.teams
  const nba = feed.league === 'nba'
  const num = (v: string | undefined) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
  const rows = (id: string) => feed.athletes.filter((a) => a.team === id && (nba ? a.stats.minutes && a.stats.minutes !== '0' : a.group !== 'goalies'))
    .sort((a, b) => (nba ? num(b.stats.points) - num(a.stats.points) : num(b.stats.goals) * 2 + num(b.stats.assists) - (num(a.stats.goals) * 2 + num(a.stats.assists)) || num(b.stats.shotsTotal) - num(a.stats.shotsTotal))).slice(0, 6)
  const goalie = (id: string) => feed.athletes.find((a) => a.team === id && a.group === 'goalies' && a.stats.saves != null)
  return (
    <section className="ar-card ar-box" aria-label="Box score">
      <h2>Box Score <small>top players</small></h2>
      <div className="ar-box__grid">
        {[away, home].map((t) => (
          <table key={t.id} style={{ ['--c' as string]: onNavy(t.color, t.alt) } as CSSProperties}>
            <thead><tr><th>{t.abbr}</th>{nba ? <><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>FG</th><th>3PT</th></> : <><th>G</th><th>A</th><th>S</th><th>+/-</th><th>TOI</th></>}</tr></thead>
            <tbody>
              {rows(t.id).map((a) => <tr key={a.id}><th>{a.headshot ? <img src={a.headshot} alt="" /> : null}{a.short ?? a.name}</th>{nba ? <><td>{a.stats.minutes}</td><td className="is-big">{a.stats.points}</td><td>{a.stats.rebounds}</td><td>{a.stats.assists}</td><td>{a.stats['fieldGoalsMade-fieldGoalsAttempted']}</td><td>{a.stats['threePointFieldGoalsMade-threePointFieldGoalsAttempted']}</td></> : <><td className="is-big">{a.stats.goals}</td><td>{a.stats.assists}</td><td>{a.stats.shotsTotal}</td><td>{a.stats.plusMinus}</td><td>{a.stats.timeOnIce}</td></>}</tr>)}
              {!nba && goalie(t.id) ? (() => { const g = goalie(t.id)!; return <tr className="ar-box__g"><th>{g.headshot ? <img src={g.headshot} alt="" /> : null}{g.short} (G)</th><td colSpan={5}>{g.stats.saves} saves{g.stats.shotsAgainst ? ` of ${g.stats.shotsAgainst}` : ''}{g.stats.savePct ? ` · ${g.stats.savePct}` : ''}</td></tr> })() : null}
            </tbody>
          </table>
        ))}
      </div>
    </section>
  )
}

function PlayFeed({ feed, league }: { feed: ArenaFeed; league: 'nba' | 'nhl' }) {
  const [scoringOnly, setScoringOnly] = useState(false)
  const { home, away } = feed.teams
  const pair = teamPair(home, away)
  const col = { [home.id]: pair.home, [away.id]: pair.away }
  const skip = /substitution|stoppage|period start|period end|timeout|face off/i
  const list = feed.plays.filter((p) => !skip.test(p.type ?? '') && (!scoringOnly || p.scoring)).slice(-40).reverse()
  return (
    <section className="ar-card ar-feed" aria-label="Play by play">
      <header><h2>Play by Play</h2><button type="button" className={`ar-chip${scoringOnly ? ' is-on' : ''}`} onClick={() => setScoringOnly((v) => !v)}>{scoringOnly ? 'Scoring plays only' : 'All plays'}</button></header>
      <ol>
        {list.map((p) => { const who = p.players[0]; return (
          <li key={p.id} className={p.scoring ? 'is-score' : ''} style={{ ['--c' as string]: col[p.team ?? ''] ?? '#475569' } as CSSProperties}>
            <span className="ar-feed__when">{periodName(league, p.period)} {p.clock}</span>
            {who?.headshot ? <img src={who.headshot} alt="" /> : <i />}
            <span className="ar-feed__text">{p.scoring ? <b>{league === 'nhl' ? '🚨 ' : ''}{p.text}</b> : p.text}</span>
            <span className="ar-feed__score">{p.away ?? ''}–{p.home ?? ''}</span>
          </li>) })}
      </ol>
    </section>
  )
}
