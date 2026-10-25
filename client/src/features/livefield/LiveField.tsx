/**
 * AISP Live View (TEST MODE): the field page.
 *
 * iPad-first. Left: the diamond with the defence, runners, count and the
 * batter at the plate. Right: batter and pitcher cards with headshots and
 * lines, the linescore, then the probability river and the Booth.
 *
 * Research and paper only. Every number says where it came from: AISP (stored
 * model and market), MLB Stats API (the live field feed, shown only on the
 * preview server), or the Booth (a language model that explains, never predicts).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useNow } from '../../components/activeWagers/useActiveWagers'
import { useScoreboard } from '../../hooks/useScoreboard'
import { etToday } from '../gamenight/gameNightData'
import { Booth } from './Booth'
import { UmpireView } from './UmpireView'
import { FootballField } from './football/FootballField'
import { LeadersCard, PlayList, StatsCard, TendenciesCard } from './football/FootballPanels'
import { latestAnimatable, ordinalDown, parseFbPlay, playbookSigns, useFootballLive, type FbPlay } from './football/footballData'
import { NextPlayPanel } from './football/NextPlayPanel'
import { unlockAudio } from './football/fx'
import { MomentumChart } from './football/MomentumChart'
import { FootballDashboard } from './football/FootballDashboard'
import { defenseFacts, fiveRows, projectSnap, useYards } from './football/situation'
import type { EdgeState } from './football/DecisionEdge'
import { GamePulse, type PulseCard } from './football/GamePulse'
import { AttackMap } from './football/AttackMap'
import { useFourth } from './football/fourth'
import { computeEpa, useEp } from './football/ep'
import type { Impact } from './football/FootballField'
import { CLASSES, LABELS, applyTilt, classOfPlay, forecastFor, nextState, predict, teamTilt, usePlaycallGrid, wpaFor, type Forecast } from './football/playcall'
import { Diamond, Headshot } from './Diamond'
import { ArenaLive } from './arena/ArenaLive'
import { TeamLogo } from '../../components/TeamLogo'
import { LiveShell, useFavorites } from './football/LiveShell'
import { River, ordinal } from './River'
import {
  PREVIEW_ONLY, SPORT_LABEL, ageSeconds, boothContext, etClock, gameLabel, orderSlate, pct, ratePct,
  useBooth, useLatest, useLiveSlate, useMlbLive, useStory, useWpSeries, type BattingLine, type MlbLive, type PitchingLine, type WpPoint,
} from './liveFieldData'
import type { MissionGame } from '../mission/missionData'
import './livefield.css'
import './liveview.css'

function slateDate(iso: string): string {
  // MLB schedules by the local (ET) calendar day of first pitch.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso))
}

export function LiveField() {
  const [params, setParams] = useSearchParams()
  const now = useNow(15_000)
  const day = etToday(now)
  const slate = useLiveSlate(day)
  const games = useMemo(() => orderSlate(slate.data?.games ?? []), [slate.data])
  const wanted = params.get('game')
  const mlbFirst = games.find((g) => g.status === 'live' || g.phase === 'STARTED') ?? games.find((g) => g.sport === 'MLB') ?? games[0] ?? null
  const game: MissionGame | null = games.find((g) => g.game_id === wanted) ?? mlbFirst
  const gameId = game?.game_id ?? null
  useEffect(() => {
    if (game && wanted !== game.game_id) setParams((prev) => { const o = new URLSearchParams(prev); o.set('game', game.game_id); return o }, { replace: true })
  }, [game, wanted, setParams])

  const story = useStory(gameId)
  const scores = useScoreboard({ gameIds: gameId ? [gameId] : [], enabled: Boolean(gameId) })
  const score = gameId ? scores.games[gameId] ?? null : null
  // ?demo=nfl:20261018:HOU:JAX (league:date:away:home) replays a finished football game for review.
  const demo = params.get('demo')?.split(':') ?? null
  const isMlb = game?.sport === 'MLB' && !demo
  const field = useMlbLive({ date: game ? slateDate(game.starts_at) : day, home: game?.home.abbr ?? null, away: game?.away.abbr ?? null, enabled: Boolean(isMlb) })
  const live: MlbLive | null = field.data?.found ? field.data : null
  const isFb = Boolean(demo && demo.length >= 4) || game?.sport === 'NFL' || game?.sport === 'CFB'
  const fbRes = useFootballLive({
    league: demo ? (demo[0] === 'cfb' || demo[0] === 'college-football' ? 'college-football' : 'nfl') : game?.sport === 'CFB' ? 'college-football' : 'nfl',
    date: demo ? demo[1] : (game ? slateDate(game.starts_at) : day).replace(/-/g, ''),
    home: demo ? '' : game?.home.abbr ?? '', away: demo ? '' : game?.away.abbr ?? '',
    homeName: demo ? demo[3] : game?.home.name ?? game?.home.abbr ?? '', awayName: demo ? demo[2] : game?.away.name ?? game?.away.abbr ?? '',
    enabled: Boolean(isFb && (game || demo)), live: !demo && (game?.status === 'live' || game?.phase === 'STARTED'),
  })
  const fb = fbRes.data?.found ? fbRes.data : null
  const fbMissing = String(fbRes.error ?? '').includes(PREVIEW_ONLY)
  const [pickedId, setPickedId] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [slow, setSlow] = useState(false)
  const [sound, setSound] = useState(false)
  const [view, setView] = useState<'qb' | 'side'>('side')
  const [sportFilter, setSportFilter] = useState<string | null>(null)
  const [favs, toggleFav] = useFavorites()
  const [pulse, setPulse] = useState<PulseCard | null>(null)
  const highlight = useMemo(() => (pulse ? new Set(pulse.ids) : null), [pulse])
  const latest = useMemo(() => latestAnimatable(fb?.plays), [fb?.plays])
  const latestId = latest?.id ?? null
  // ESPN often rewrites a play after it is first posted (a run becomes a 'Rushing Touchdown'): replay it when it changes
  const latestKey = latest ? `${latest.id}|${latest.type ?? ''}|${parseFbPlay(latest).touchdown ? 'td' : ''}` : null
  // When several plays land in one refresh (touchdown, extra point and kickoff together), spotlight the big one
  // first (touchdown, turnover, made field goal) so its animation and banner run, then return to the latest.
  const prevLatest = useRef<string | null>(null)
  const [spotId, setSpotId] = useState<string | null>(null)
  useEffect(() => {
    if (!latestId || !latestKey) return undefined
    const plays = fb?.plays ?? []
    const prev = prevLatest.current
    prevLatest.current = latestId
    setPickedId(null)
    if (prev && prev !== latestId) {
      const from = plays.findIndex((p) => p.id === prev)
      const fresh = from >= 0 ? plays.slice(from + 1) : []
      const star = fresh.find((p) => { const k = parseFbPlay(p); return k.touchdown || k.kind === 'interception' || (p.turnover && k.kind !== 'punt' && k.kind !== 'kickoff') || (k.kind === 'fieldgoal' && k.good) })
      if (star && star.id !== latestId) {
        setSpotId(star.id)
        setNonce((n) => n + 1)
        const t = window.setTimeout(() => { setSpotId(null); setNonce((n) => n + 1) }, 11_000)
        return () => window.clearTimeout(t)
      }
    }
    setSpotId(null)
    setNonce((n) => n + 1)
    return undefined
  }, [latestKey, gameId]) // eslint-disable-line react-hooks/exhaustive-deps
  const shownPlay = (pickedId ? fb?.plays?.find((p) => p.id === pickedId) : null) ?? (spotId ? fb?.plays?.find((p) => p.id === spotId) : null) ?? latest
  const { grid: playGrid } = usePlaycallGrid()
  const fbPlays = fb?.plays
  const homeTeamId = fb?.teams?.home.id ?? ''
  const shownIdx = shownPlay && fbPlays ? fbPlays.findIndex((p) => p.id === shownPlay.id) : -1
  const isNfl = fbRes.data?.league === 'nfl' || game?.sport === 'NFL' || (demo ? demo[0] === 'nfl' : false)
  const forecast: Forecast | null = useMemo(() => (playGrid && fbPlays && shownIdx >= 0 ? forecastFor(playGrid, fbPlays, shownIdx, homeTeamId) : null), [playGrid, fbPlays, shownIdx, homeTeamId])
  const shownParsed = useMemo(() => (shownPlay ? parseFbPlay(shownPlay) : null), [shownPlay])
  const actual = useMemo(() => classOfPlay(shownParsed, shownPlay?.text), [shownParsed, shownPlay])
  const nextForecast = useMemo(() => {
    if (!playGrid || !fbPlays || !fb?.teams) return null
    const ns = nextState(fbPlays, homeTeamId)
    if (!ns) return null
    const base = predict(playGrid, ns.state)
    const tilt = teamTilt(playGrid, fbPlays, fbPlays.length, ns.offenseId, homeTeamId)
    const team = ns.offenseId === fb.teams.home.id ? fb.teams.home.abbr : fb.teams.away.abbr
    return { forecast: { base, probs: applyTilt(base, tilt), tilt, state: ns.state } as Forecast, team }
  }, [isNfl, playGrid, fbPlays, fb?.teams, homeTeamId])
  const offenseTeam = shownPlay && fb?.teams ? ((shownPlay.offense ?? shownPlay.start.team) === fb.teams.home.id ? fb.teams.home : fb.teams.away) : null
  const wpa = shownPlay ? wpaFor(fb?.win_probability, shownPlay.id, (shownPlay.offense ?? shownPlay.start.team) === homeTeamId) : null
  const burst = Boolean(shownParsed?.touchdown) || shownParsed?.kind === 'interception' || Boolean(shownPlay?.turnover && shownParsed?.fumble) || (wpa != null && Math.abs(wpa) >= 12)
  const lastWall = fbPlays && fbPlays.length ? fbPlays[fbPlays.length - 1].wallclock : null
  const lastPlayAge = lastWall ? Math.max(0, Math.round((now - Date.parse(lastWall)) / 1000)) : null
  const impact: Impact | null = useMemo(() => {
    if (!shownPlay || !shownParsed || !fb?.teams) return null
    const series = fb.win_probability
    const i = series ? series.findIndex((w) => w.playId === shownPlay.id) : -1
    const after = i > 0 && series ? series[i].home * 100 : null
    const before = i > 0 && series ? series[i - 1].home * 100 : null
    const yards = shownPlay.yards ?? 0
    let label: string | null = null
    let intensity = 0.5
    let kind: Impact['kind'] = 'big'
    let side: Impact['side'] = 'off'
    const pickSix = shownParsed.kind === 'interception' && shownParsed.touchdown
    if (shownParsed.kind === 'interception') { label = pickSix ? 'PICK SIX!' : 'INTERCEPTION'; intensity = 1; kind = 'pick'; side = 'def' }
    else if (shownParsed.fumble && shownPlay.turnover) { label = 'FUMBLE!'; intensity = 0.95; kind = 'fumble'; side = 'def' }
    else if (shownParsed.touchdown) { label = 'TOUCHDOWN'; intensity = 1; kind = 'td' }
    else if (shownPlay.turnover && shownParsed.kind !== 'punt' && shownParsed.kind !== 'kickoff') { label = 'TURNOVER'; intensity = 0.9; kind = 'turnover'; side = 'def' }
    else if (shownParsed.kind === 'fieldgoal' && shownParsed.good) { label = 'FIELD GOAL'; intensity = 0.6; kind = 'fg' }
    else if (shownParsed.kind === 'sack' && wpa != null && Math.abs(wpa) >= 4) { label = 'SACK'; intensity = 0.65; kind = 'sack'; side = 'def' }
    else if ((wpa != null && Math.abs(wpa) >= 8) || yards >= 25) { label = yards >= 25 ? `BIG PLAY +${yards}` : 'MOMENTUM SWING'; intensity = Math.min(0.95, 0.55 + Math.abs(wpa ?? 0) / 40) }
    if (!label) return null
    return { label, before, after, wpa, offHome: (shownPlay.offense ?? shownPlay.start.team) === homeTeamId, intensity, kind, side }
  }, [shownPlay, shownParsed, fb?.teams, fb?.win_probability, wpa, homeTeamId])
  const epGrid = useEp()
  const fourthTables = useFourth()
  const epaSeries = useMemo(() => {
    const raw = epGrid && fbPlays && homeTeamId ? computeEpa(epGrid, fbPlays, homeTeamId) : []
    if (isNfl || !raw.length) return raw
    // The expected-points model is fit to the NFL, which scores more than college. For college games the series is
    // centred on this game's own average snap, so the chart shows who is beating the game's average, not an inflated total.
    const mean = raw.reduce((t, e) => t + e.epa, 0) / raw.length
    return raw.map((e) => ({ ...e, epa: e.epa - mean }))
  }, [epGrid, fbPlays, homeTeamId, isNfl])
  const wpaById = useMemo(() => {
    const m = new Map<string, number>()
    const series = fb?.win_probability
    if (!series || !fbPlays) return m
    const byId = new Map(series.map((w, i) => [w.playId, i]))
    for (const p of fbPlays) {
      const i = byId.get(p.id)
      if (i == null || i < 1) continue
      const off = (p.offense ?? p.start.team) === homeTeamId
      const d = (series[i].home - series[i - 1].home) * 100
      m.set(p.id, off ? d : -d)
    }
    return m
  }, [fb?.win_probability, fbPlays, homeTeamId])
  const yards = useYards()
  const edge: EdgeState | null = useMemo(() => {
    if (!epGrid || !fourthTables || !fb?.teams || !fbPlays) return null
    const own = (yl: number, offenseId: string | null) => (offenseId === homeTeamId ? yl : 100 - yl)
    const abbr = (id: string | null) => (id === fb.teams!.home.id ? fb.teams!.home.abbr : fb.teams!.away.abbr)
    const didOf = (pl: FbPlay): 'go' | 'punt' | 'fg' | null => { const k = parseFbPlay(pl).kind; return k === 'punt' ? 'punt' : k === 'fieldgoal' ? 'fg' : ['pass', 'incomplete', 'interception', 'sack', 'rush'].includes(k) ? 'go' : null }
    if (shownPlay && shownPlay.start.down === 4 && shownPlay.start.distance != null && shownPlay.start.yardLine != null) {
      const offId = shownPlay.offense ?? shownPlay.start.team
      return { own: own(shownPlay.start.yardLine, offId), distance: shownPlay.start.distance, team: abbr(offId), did: didOf(shownPlay), label: `Q${shownPlay.period} ${shownPlay.clock}`, mode: 'now' }
    }
    if (shownPlay && shownPlay.start.down === 3 && shownPlay.start.distance != null && shownPlay.start.yardLine != null) {
      const offId = shownPlay.offense ?? shownPlay.start.team
      return { own: own(shownPlay.start.yardLine, offId), distance: shownPlay.start.distance, team: abbr(offId), did: null, label: `Q${shownPlay.period} ${shownPlay.clock}`, mode: 'if-stalls' }
    }
    for (let i = fbPlays.length - 1; i >= 0; i -= 1) {
      const pl = fbPlays[i]
      if (pl.start.down === 4 && pl.start.distance != null && pl.start.yardLine != null && didOf(pl)) { const offId = pl.offense ?? pl.start.team; return { own: own(pl.start.yardLine, offId), distance: pl.start.distance, team: abbr(offId), did: didOf(pl), label: `Last 4th down · Q${pl.period} ${pl.clock}`, mode: 'last' } }
    }
    return null
  }, [epGrid, fourthTables, fb?.teams, fbPlays, shownPlay, homeTeamId])
  const projected = useMemo(() => (yards && forecast ? projectSnap(yards, forecast.state) : null), [yards, forecast])
  const callRows = useMemo(() => (forecast ? fiveRows(forecast) : null), [forecast])
  const defFacts = useMemo(() => {
    if (!fb?.teams || !fbPlays || !offenseTeam) return null
    const defId = offenseTeam.id === fb.teams.home.id ? fb.teams.away.id : fb.teams.home.id
    return defenseFacts(fbPlays, epaSeries, defId, offenseTeam.id)
  }, [fb?.teams, fbPlays, offenseTeam, epaSeries])
  const insightText = useMemo(() => {
    if (!forecast || !offenseTeam || !fb?.teams) return ''
    const defAbbr = offenseTeam.id === fb.teams.home.id ? fb.teams.away.abbr : fb.teams.home.abbr
    const s0 = forecast.state
    const pass = forecast.probs.slice(0, 6).reduce((t, v) => t + v, 0)
    const run = forecast.probs.slice(6, 9).reduce((t, v) => t + v, 0)
    const lean = pass > run + 0.15 ? 'pass-favorable' : run > pass + 0.15 ? 'run-favorable' : 'balanced'
    const diffWord = s0.scoreDiff > 0 ? 'leading' : s0.scoreDiff < 0 ? 'trailing' : 'tied'
    const parts = [`${offenseTeam.abbr} is in a ${lean} situation (${ordinalDown(s0.down)} & ${s0.distance}, ${diffWord}, Q${s0.period}): the model has pass ${Math.round(pass * 100)}%, run ${Math.round(run * 100)}%.`]
    if (forecast.tilt.n >= 6 && Math.abs(forecast.tilt.delta) >= 0.04) parts.push(`${offenseTeam.abbr} has thrown ${Math.round(Math.abs(forecast.tilt.delta) * 100)} points ${forecast.tilt.delta > 0 ? 'more' : 'less'} than expected over its last ${forecast.tilt.n} snaps.`)
    if (defFacts && defFacts.shortPassN >= 4 && defFacts.shortPassEpa != null) parts.push(`${defAbbr} has allowed ${defFacts.shortPassEpa >= 0 ? '+' : ''}${defFacts.shortPassEpa.toFixed(2)} EPA per short pass this game (${defFacts.shortPassN} thrown).`)
    else if (defFacts && defFacts.dropbacks >= 6 && defFacts.epaPerDropback != null) parts.push(`${defAbbr} has allowed ${defFacts.epaPerDropback >= 0 ? '+' : ''}${defFacts.epaPerDropback.toFixed(2)} EPA per dropback this game.`)
    if (defFacts && defFacts.thirdFaced >= 4) parts.push(`${defAbbr} has stopped ${defFacts.thirdStops} of ${defFacts.thirdFaced} third downs.`)
    return parts.join(' ')
  }, [forecast, offenseTeam, fb?.teams, defFacts])
  const modelName = isNfl ? 'AISP playcall v1' : 'AISP playcall v1 (NFL-trained)'
  const ghosts = useMemo(() => (forecast ? CLASSES.map((c, i) => ({ cls: c, p: forecast.probs[i] })).filter((g) => g.p >= 0.04).sort((a, b) => b.p - a.p).slice(0, 6) : []), [forecast])
  const fbWp: WpPoint[] = useMemo(() => {
    if (!fb?.win_probability?.length) return []
    const period = new Map((fb.plays ?? []).map((p) => [p.id, p.period]))
    const playById = new Map((fb.plays ?? []).map((p) => [p.id, p]))
    let prev = 1
    let lastPeriod = 0
    return fb.win_probability.map((w, i) => {
      const pd = period.get(w.playId) ?? prev
      prev = pd ?? prev
      const first = prev !== lastPeriod
      lastPeriod = prev
      const pl = playById.get(w.playId)
      const k = pl ? parseFbPlay(pl) : null
      const label = pl && k ? (k.touchdown ? `${(pl.offense ?? pl.start.team) === homeTeamId ? fb.teams?.home.abbr : fb.teams?.away.abbr} TD` : k.kind === 'interception' ? `${(pl.offense ?? pl.start.team) === homeTeamId ? fb.teams?.home.abbr : fb.teams?.away.abbr} INT` : pl.turnover && !/punt|kickoff/i.test(pl.type ?? '') ? `${(pl.offense ?? pl.start.team) === homeTeamId ? fb.teams?.home.abbr : fb.teams?.away.abbr} TO` : k.kind === 'fieldgoal' && k.good ? `${(pl.offense ?? pl.start.team) === homeTeamId ? fb.teams?.home.abbr : fb.teams?.away.abbr} FG` : null) : null
      return { atBatIndex: i, inning: prev, half: first ? 'top' : 'bottom', home: w.home, event: label, awayScore: null, homeScore: null }
    })
  }, [fb?.win_probability, fb?.plays, fb?.teams, homeTeamId])

  const feedMissing = String(field.error ?? '').includes(PREVIEW_ONLY)
  const wp = useWpSeries(live)
  const modelHome = story.data?.model_vs_market?.model_home ?? null
  const marketHome = story.data?.market?.consensus_no_vig_home ?? story.data?.model_vs_market?.market_home_no_vig ?? null

  const ctx = useLatest(boothContext({
    now, game: demo ? null : game, story: demo ? null : story.data ?? null, field: isMlb ? live : null,
    scoreboard: score ? { status: score.status, status_detail: score.status_detail, away_score: score.away_score, home_score: score.home_score } : null,
    football: fb ? {
      source: 'ESPN play-by-play (free public feed, test-mode preview server)', retrieved_at: fb.retrieved_at, status: fb.status?.state === 'in' ? fb.status : undefined,
      score: { away: fb.teams?.away.score, home: fb.teams?.home.score, away_team: fb.teams?.away.abbr, home_team: fb.teams?.home.abbr }, situation: fb.situation,
      shown_play: shownPlay ? { offense: offenseTeam?.abbr, defense: offenseTeam ? (offenseTeam.id === fb.teams?.home.id ? fb.teams?.away.abbr : fb.teams?.home.abbr) : undefined, period: shownPlay.period, clock: shownPlay.clock, down_distance: `${ordinalDown(shownPlay.start.down)} & ${shownPlay.start.distance}`, spot: shownPlay.start.possessionText, kind: parseFbPlay(shownPlay).kind, text: shownPlay.text } : null,
      recent_plays: (fb.plays ?? []).slice(-8).map((p) => ({ period: p.period, clock: p.clock, down: p.start.down, distance: p.start.distance, text: p.text })),
      team_stats: fb.stats, live_home_win_probability_now: fbWp.length ? fbWp[fbWp.length - 1].home : null,
      how_this_team_plays_this_game: offenseTeam && fbPlays ? playbookSigns(fbPlays, offenseTeam.id, shownPlay?.start.down ?? null, shownPlay?.start.distance ?? null, null, null).map((x) => `${x.label}: ${x.detail}`) : [],
      next_play_model_top_calls: forecast ? CLASSES.map((c, i) => ({ call: LABELS[c].name, chance: Math.round(forecast.probs[i] * 100) })).sort((x, y) => y.chance - x.chance).slice(0, 4) : undefined,
      momentum_expected_points_added: epaSeries.length ? { latest_play: Math.round((epaSeries[epaSeries.length - 1]?.epa ?? 0) * 100) / 100 } : undefined,
    } : undefined,
  }))
  const booth = useBooth(ctx)
  const [fs, setFs] = useState(false)
  useEffect(() => {
    const onChange = () => setFs(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])
  const toggleFs = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void document.documentElement.requestFullscreen?.()
  }

  const homeAbbr = fb?.teams?.home.abbr ?? game?.home.abbr ?? 'HOME'
  const awayAbbr = fb?.teams?.away.abbr ?? game?.away.abbr ?? 'AWAY'
  const feedAge = ageSeconds(live?.retrieved_at, now)
  const inPlay = live?.status?.abstract === 'Live'
  const finalFeed = live?.status?.abstract === 'Final'
  const riverEl = game ? <River points={isFb ? fbWp : inPlay || finalFeed ? wp : []} modelHome={modelHome} marketHome={marketHome} homeAbbr={homeAbbr} awayAbbr={awayAbbr} live={isFb ? fb?.status?.state === 'in' : Boolean(inPlay)} unit={isFb ? 'quarter' : 'inning'} compact={Boolean(isFb)} /> : null
  const boothEl = game ? <Booth turns={booth.turns} busy={booth.busy} ask={booth.ask} sport={isFb ? (game.sport === 'CFB' ? 'CFB' : 'NFL') : game.sport} /> : null

  if (game && !demo && (game.sport === 'NBA' || game.sport === 'NHL')) {
    return (
      <div className="lf lf--football lf--arena" data-sport={game.sport}>
        <ArenaLive game={game} games={games} onPickGame={(id) => setParams((prev) => { const o = new URLSearchParams(prev); o.set('game', id); o.delete('demo'); return o })} sportFilter={sportFilter} onSport={setSportFilter} booth={boothEl} />
      </div>
    )
  }

  if (game && isFb && fb?.teams) {
    return (
      <div className="lf lf--football" data-sport={game.sport}>
        <FootballDashboard
          fb={fb} games={games} gameId={demo ? null : gameId} onPickGame={(id) => setParams((prev) => { const o = new URLSearchParams(prev); o.set('game', id); o.delete('demo'); return o })} sportFilter={sportFilter} onSport={setSportFilter}
          shownPlay={shownPlay ?? null} pickedId={pickedId} latestId={latestId} onPick={(id) => { setPickedId(id); setNonce((n) => n + 1) }} nonce={nonce} onReplay={() => setNonce((n) => n + 1)}
          slow={slow} onSlow={() => setSlow((v) => !v)} sound={sound} onSound={() => { if (!sound) unlockAudio(); setSound((v) => !v) }} view={view} onView={setView}
          isNfl={isNfl} grid={playGrid} forecast={forecast} rows={callRows} facts={defFacts} insight={insightText} actual={actual} wpa={wpa} ghosts={ghosts} burst={burst || Boolean(impact)} impact={impact}
          epa={epaSeries} wpaById={wpaById} epGrid={epGrid} fourthTables={fourthTables} edge={edge} yards={yards} projected={projected}
          pulse={pulse} onPulse={setPulse} highlight={highlight} river={riverEl} booth={boothEl} lastPlayAge={lastPlayAge} modelName={modelName}
          teamNote={`Expected points: ${epGrid?.trainedOn ?? 'NFL snaps'}, ${epGrid ? Math.round(epGrid.corr * 100) : '—'}% match with nflfastR on plays it never saw.${isNfl ? '' : ' College view: centred on this game own average snap, because the NFL-fit model overstates college scoring.'}`}
        />
      </div>
    )
  }

  return (
    <div className={`lf${game ? ' lf--football lf--mlb' : ''}`} data-sport={game?.sport ?? ''}>
      {game ? (
        <LiveShell games={games} gameId={gameId} onPick={(id) => setParams((prev) => { const o = new URLSearchParams(prev); o.set('game', id); o.delete('demo'); return o })} sportFilter={sportFilter} onSport={setSportFilter} favs={favs} onFav={toggleFav}
          strip={{ league: game.sport, week: null, season: null, venue: live?.venue ?? null, city: null, weather: live?.weather?.condition ? { text: live.weather.condition, temp: live.weather.temp ? Number(live.weather.temp) : null } : null, model: game.sport === 'MLB' ? 'MLB V18' : game.sport, broadcast: null, live: Boolean(inPlay), clock: inPlay && live?.status ? `${live.status.inning_state ?? ''} ${live.status.inning_ordinal ?? ''}`.trim() : '', status: finalFeed ? 'FINAL' : 'PREGAME', date: game.starts_at }}
          onBooth={() => document.querySelector('.lf-booth, .booth')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>
          <main className="lf-main">
            <section className="lf-score" aria-label="Score">
              <div className="lf-score__team">
                {game.sport === 'MLB' ? <TeamLogo league="MLB" abbreviation={awayAbbr} size={60} /> : null}<span className="lf-score__abbr">{awayAbbr}</span>
                <span className="lf-score__name">{fb?.teams?.away.name ?? game.away.name ?? awayAbbr}{live?.teams?.away.record && (live.teams.away.record.wins + live.teams.away.record.losses) > 0 ? <small> {live.teams.away.record.wins}-{live.teams.away.record.losses}</small> : null}</span>
              </div>
              <div className="lf-score__mid">
                <span className="lf-score__num">{fb?.teams?.away.score ?? live?.score?.away ?? score?.away_score ?? '–'}</span>
                <span className="lf-score__state">
                  {fb ? (fb.status?.state === 'in' ? `Q${fb.status.period} ${fb.status.clock}` : fb.status?.short ?? 'Football')
                    : inPlay && live?.status ? `${live.status.inning_state ?? ''} ${live.status.inning_ordinal ?? ''}`.trim()
                    : finalFeed ? 'FINAL'
                    : live?.status?.detailed ?? score?.status_detail ?? (game.status === 'scheduled' ? `First pitch ${etClock(game.starts_at)}` : game.status.toUpperCase())}
                </span>
                <span className="lf-score__num">{fb?.teams?.home.score ?? live?.score?.home ?? score?.home_score ?? '–'}</span>
              </div>
              <div className="lf-score__team lf-score__team--home">
                {game.sport === 'MLB' ? <TeamLogo league="MLB" abbreviation={homeAbbr} size={60} /> : null}<span className="lf-score__abbr">{homeAbbr}</span>
                <span className="lf-score__name">{fb?.teams?.home.name ?? game.home.name ?? homeAbbr}{live?.teams?.home.record && (live.teams.home.record.wins + live.teams.home.record.losses) > 0 ? <small> {live.teams.home.record.wins}-{live.teams.home.record.losses}</small> : null}</span>
              </div>
              <div className="lf-score__meta">
                {live?.series ? <span>{live.series}{live.series_game ? ` · Game ${live.series_game}` : ''}</span> : <span>{SPORT_LABEL[game.sport] ?? game.sport}</span>}
                {live?.venue ? <span>{live.venue}</span> : null}
                {live?.weather?.condition ? <span>{live.weather.condition}{live.weather.temp ? ` · ${live.weather.temp}°` : ''}</span> : null}
                <span className="lf-muted">AISP score: {score?.available ? `${score.away_score ?? '–'}-${score.home_score ?? '–'} (${score.freshness?.state ?? 'state unknown'})` : 'not collected yet'}</span>
              </div>
            </section>

            <div className="lf-grid">
              <div className="lf-col lf-col--field">
                {isMlb ? (
                  <>
                    <Diamond field={field.data ?? null} homeAbbr={homeAbbr} awayAbbr={awayAbbr} />
                    {feedMissing ? <p className="lf-note">The field feed is only wired on the test-mode preview server (127.0.0.1:5199).</p> : null}
                    {field.data && !field.data.found ? <p className="lf-note">{field.data.reason}</p> : null}
                    {field.error && !feedMissing ? <p className="lf-note">Field feed error: {String(field.error)}</p> : null}
                    {boothEl}
                    {riverEl}
                    <Plays field={live} />
                  </>
                ) : isFb ? (
                  <>
                    {fb ? (
                      <>
                        <FootballField view={view} fb={fb} play={shownPlay} nonce={nonce} slow={slow} ghosts={ghosts} actualCls={actual.cls} burst={burst || Boolean(impact)} impact={impact} sound={sound} />
                        <div className="fb-controls">
                          <span className="fb-seg" role="group" aria-label="Camera"><button type="button" className={view === 'qb' ? 'is-on' : ''} onClick={() => setView('qb')}>QB view</button><button type="button" className={view === 'side' ? 'is-on' : ''} onClick={() => setView('side')}>50-yard line</button></span>
                          <button type="button" className="lf-chipbtn" onClick={() => setNonce((n) => n + 1)}>↻ Replay this play</button>
                          <button type="button" className="lf-chipbtn" onClick={() => setSlow((v) => !v)} aria-pressed={slow}>{slow ? 'Slow motion: on' : 'Slow motion'}</button>
                          <button type="button" className="lf-chipbtn" onClick={() => { if (!sound) unlockAudio(); setSound((v) => !v) }} aria-pressed={sound}>{sound ? 'Stadium sound: on' : 'Stadium sound: off'}</button>
                          {pickedId && shownPlay ? <button type="button" className="lf-chipbtn fb-review" onClick={() => { setPickedId(null); setNonce((n) => n + 1) }}>Reviewing Q{shownPlay.period} {shownPlay.clock} · Return to live</button> : <span className="lf-muted lf-small">Following the latest play{lastPlayAge != null ? ` · last play logged ${lastPlayAge < 90 ? `${lastPlayAge} s` : `${Math.round(lastPlayAge / 60)} min`} ago` : ''}{fb.status?.state === 'in' && lastPlayAge != null && lastPlayAge > 240 ? (/half|end of|delay|suspend|weather/i.test(fb.status.detail ?? '') ? ` · ${fb.status.detail}` : ' · feed may be delayed or play stopped') : ''}</span>}
                        </div>
                        {fb.teams ? <GamePulse plays={fb.plays ?? []} epa={epaSeries} away={fb.teams.away} home={fb.teams.home} active={pulse?.key ?? null} onShow={setPulse} /> : null}
                        {fb.teams ? <MomentumChart highlight={highlight} wpaById={wpaById} onPick={(id) => { setPickedId(id); setNonce((n) => n + 1) }} epa={epaSeries} away={fb.teams.away} home={fb.teams.home} shownId={shownPlay?.id ?? null} live={fb.status?.state === 'in'} note={`Expected points: ${epGrid?.trainedOn ?? 'NFL snaps'}, ${epGrid ? Math.round(epGrid.corr * 100) : '—'}% match with nflfastR on plays it never saw.${isNfl ? '' : ' College view: centred on this game own average snap, because the NFL-fit model overstates college scoring.'}`} /> : null}
                        {fb.teams ? <AttackMap highlight={highlight} focusTeam={pulse?.teamId ?? null} plays={fb.plays ?? []} epa={epaSeries} away={fb.teams.away} home={fb.teams.home} shownId={shownPlay?.id ?? null} defaultTeamId={shownPlay ? (shownPlay.offense ?? shownPlay.start.team) : null} onPick={(id) => { setPickedId(id); setNonce((n) => n + 1) }} /> : null}
                        {boothEl}
                        {riverEl}
                        <PlayList highlight={highlight} plays={fb.plays ?? []} selectedId={pickedId} latestId={latestId} onSelect={(id) => { setPickedId(id); setNonce((n) => n + 1) }} />
                        <LeadersCard fb={fb} />
                      </>
                    ) : (
                      <section className="lf-field lf-field--other"><div className="lf-other">
                        <h3>{SPORT_LABEL[game.sport] ?? game.sport} field view</h3>
                        {fbMissing ? <p>The football feed is only wired on the test-mode preview server (127.0.0.1:5199).</p> : fbRes.data && !fbRes.data.found ? <p>{fbRes.data.reason}</p> : fbRes.error ? <p>Football feed error: {String(fbRes.error)}</p> : <p>Loading the play-by-play…</p>}
                      </div></section>
                    )}
                  </>
                ) : (
                  <section className="lf-field lf-field--other">
                    <div className="lf-other">
                      <h3>{SPORT_LABEL[game.sport] ?? game.sport} field view</h3>
                      <p>The baseball diamond and the football field are built. Basketball and hockey views come later; until then this page shows AISP's score and pregame numbers for this game.</p>
                      <p className="lf-muted">AISP status: {score?.status ?? 'unknown'}{score?.status_detail ? ` · ${score.status_detail}` : ''}</p>
                    </div>
                  </section>
                )}
              </div>

              <aside className="lf-col lf-col--cards">
                {isFb && fb ? (
                  <>
                    <NextPlayPanel forecast={forecast} next={nextForecast} grid={playGrid} team={offenseTeam?.abbr ?? 'the offense'} actualClass={shownPlay ? actual.cls : null} actualCategory={shownPlay ? actual.category : null} wpa={wpa} league={isNfl ? 'nfl' : 'college-football'} live={fb.status?.state === 'in'} />
                    <StatsCard fb={fb} />
                    <TendenciesCard fb={fb} offenseId={shownPlay?.offense ?? shownPlay?.start.team ?? null} />
                  </>
                ) : null}
                {isMlb ? (
                  <>
                    <MatchupCard live={live} />
                    <UmpireView atBat={live?.at_bat} hit={live?.last_hit} />
                    <Linescore live={live} homeAbbr={homeAbbr} awayAbbr={awayAbbr} />
                  </>
                ) : null}
              </aside>
            </div>

            {!isMlb && !isFb ? <>{riverEl}{boothEl}</> : null}

            <footer className="lf-foot lf-muted">
              Research and paper only. Pregame: AISP model {pct(modelHome, 1)} {homeAbbr}, market no-vig {marketHome == null ? 'not stored' : pct(marketHome, 1)}{story.data?.facts?.length ? ` (${story.data.facts.map((f) => f.text).join(' ')})` : ''}. Field, headshots and live win probability: MLB Stats API via the preview server{feedAge != null ? `, ${feedAge}s old` : ''}, never stored, never used for settlement. The Booth explains; it never predicts.
            </footer>
          </main>
        </LiveShell>
      ) : (
        <>
          <div className="lf-bar" role="banner">
            <div className="lf-bar__lead">
              <Link to="/" className="lf-brand"><b>AISP</b> LIVE VIEW</Link>
              <span className="lf-pill lf-pill--test">TEST MODE · preview build</span>
            </div>
            <label className="lf-pick">
              <span className="lf-sr">Game</span>
              <select value={gameId ?? ''} onChange={(e) => setParams((prev) => { const o = new URLSearchParams(prev); o.set('game', e.target.value); return o })} disabled={!games.length}>
                {!games.length ? <option value="">{slate.data ? 'No games on the board tonight' : slate.error ? 'Board unavailable' : 'Loading tonight…'}</option> : null}
                {games.map((g) => <option key={g.game_id} value={g.game_id}>{g.sport} · {gameLabel(g)} · {etClock(g.starts_at)}{g.status === 'live' || g.phase === 'STARTED' ? ' · LIVE' : g.status === 'final' || g.phase === 'FINAL' ? ' · FINAL' : ''}</option>)}
              </select>
            </label>
            <div className="lf-bar__right">
              <span className="lf-muted">{new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' }).format(now)} · board as of {etClock(slate.data?.generated_at)}</span>
              <button type="button" className="lf-btn" onClick={toggleFs}>{fs ? 'Exit full screen' : 'Full screen'}</button>
            </div>
          </div>

          <main className="lf-main"><p className="lf-note">{slate.error ? 'Tonight\'s board could not be read.' : slate.data ? 'No games on tonight\'s board.' : 'Loading tonight\'s board…'}</p></main>
        </>
      )}
    </div>
  )
}

function orderSlot(order: number | null | undefined): string {
  if (order == null) return ''
  const slot = order >= 100 ? Math.floor(order / 100) : order
  return slot >= 1 && slot <= 9 ? ` · #${slot} in the order` : ''
}

function statOr(v: string | number | null | undefined, fallback = '—'): string { return v == null || v === '' ? fallback : String(v) }

function MatchupCard({ live }: { live: MlbLive | null }) {
  const bt = live?.offense?.batter ?? null
  const bs: BattingLine | null = live?.batter_stats ?? null
  const br = live?.regular?.batter ?? null
  const pt = live?.defense?.pitcher ?? null
  const ps: PitchingLine | null = live?.pitcher_stats ?? null
  const pr = live?.regular?.pitcher ?? null
  const probable = !pt && live?.teams ? (live.status?.is_top === false ? live.teams.away.probable : live.teams.home.probable) : null
  const pitches = ps?.game.numberOfPitches ?? ps?.game.pitchesThrown ?? null
  return (
    <section className="lf-card lf-matchup" aria-label="Matchup">
      <div className="lf-matchup__side">
        <div className="lf-card__who">
          <Headshot person={bt} size={56} />
          <div>
            <span className="lf-card__tag">AT BAT{live?.offense?.bat_side ? ` · bats ${live.offense.bat_side}` : ''}{orderSlot(live?.offense?.batting_order)}</span>
            <h3>{bt?.name ?? (live ? 'Nobody at the plate' : '—')}</h3>
            <span className="lf-muted lf-small">{bs?.position ?? ''}{bs?.jersey ? ` · #${bs.jersey}` : ''}{bs?.game.summary ? ` · today ${bs.game.summary}` : ''}</span>
          </div>
        </div>
        {br ? (
          <div className="lf-stats lf-stats--mini">
            <span><b>{statOr(br.avg)}</b><small>AVG</small></span>
            <span><b>{statOr(br.obp)}</b><small>OBP</small></span>
            <span><b>{statOr(br.slg)}</b><small>SLG</small></span>
            <span><b>{statOr(br.hr)}</b><small>HR</small></span>
            <span><b>{ratePct(br.k, br.pa, 0)}</b><small>K%</small></span>
            <span><b>{ratePct((br.bb ?? 0) + (br.hbp ?? 0), br.pa, 0)}</b><small>BB%</small></span>
          </div>
        ) : <p className="lf-muted lf-small">Regular-season line loads with the first pitch.</p>}
      </div>
      <div className="lf-matchup__vs">vs</div>
      <div className="lf-matchup__side">
        <div className="lf-card__who">
          <Headshot person={pt ?? probable} size={56} />
          <div>
            <span className="lf-card__tag">{pt ? `PITCHING${live?.defense?.pitch_hand ? ` · throws ${live.defense.pitch_hand}` : ''}` : 'PROBABLE STARTER'}</span>
            <h3>{pt?.name ?? probable?.name ?? (live ? 'Pitcher not announced' : '—')}</h3>
            <span className="lf-muted lf-small">{ps ? `today ${statOr(pitches)} P · ${statOr(ps.game.inningsPitched)} IP · ${statOr(ps.game.strikeOuts)} K · ${statOr(ps.game.baseOnBalls)} BB · ${statOr(ps.game.hits)} H · ${statOr(ps.game.earnedRuns)} ER` : ''}</span>
          </div>
        </div>
        {pr ? (
          <div className="lf-stats lf-stats--mini">
            <span><b>{statOr(pr.era)}</b><small>ERA</small></span>
            <span><b>{statOr(pr.whip)}</b><small>WHIP</small></span>
            <span><b>{statOr(pr.k9)}</b><small>K/9</small></span>
            <span><b>{ratePct(pr.k, pr.bf, 0)}</b><small>K%</small></span>
            <span><b>{ratePct((pr.bb ?? 0) + (pr.hbp ?? 0), pr.bf, 0)}</b><small>BB%</small></span>
            <span><b>{statOr(pr.go_ao)}</b><small>GO/AO</small></span>
          </div>
        ) : <p className="lf-muted lf-small">Regular-season line loads with the first pitch.</p>}
      </div>
      <p className="lf-muted lf-small lf-matchup__src">{live?.regular?.season ?? '2026 regular season (MLB Stats API)'} · today from the live box score</p>
    </section>
  )
}

function Linescore({ live, homeAbbr, awayAbbr }: { live: MlbLive | null; homeAbbr: string; awayAbbr: string }) {
  const innings = live?.innings ?? []
  if (!innings.length) return null
  const n = Math.max(9, innings.length)
  const cells = Array.from({ length: n }, (_, i) => innings[i] ?? { num: i + 1, away: null, home: null })
  return (
    <section className="lf-card lf-card--line" aria-label="Linescore">
      <table className="lf-line">
        <thead><tr><th scope="col"></th>{cells.map((c) => <th key={c.num} scope="col">{c.num}</th>)}<th scope="col">R</th><th scope="col">H</th><th scope="col">E</th></tr></thead>
        <tbody>
          <tr><th scope="row">{awayAbbr}</th>{cells.map((c) => <td key={c.num}>{c.away ?? ''}</td>)}<td><b>{live?.score?.away ?? ''}</b></td><td>{live?.score?.away_hits ?? ''}</td><td>{live?.score?.away_errors ?? ''}</td></tr>
          <tr><th scope="row">{homeAbbr}</th>{cells.map((c) => <td key={c.num}>{c.home ?? ''}</td>)}<td><b>{live?.score?.home ?? ''}</b></td><td>{live?.score?.home_hits ?? ''}</td><td>{live?.score?.home_errors ?? ''}</td></tr>
        </tbody>
      </table>
    </section>
  )
}

function Plays({ field }: { field: MlbLive | null }) {
  const plays = [...(field?.recent_plays ?? [])].reverse()
  if (!plays.length) return null
  return (
    <section className="lf-plays" aria-label="Last plays">
      <h3>Last plays <small className="lf-muted">MLB Stats API</small></h3>
      <ol>
        {plays.slice(0, 8).map((p) => (
          <li key={`${p.atBatIndex}-${p.endTime}`} className={p.isScoringPlay ? 'is-score' : ''}>
            <span className="lf-plays__when">{p.half === 'bottom' ? '▼' : '▲'}{ordinal(p.inning)}</span>
            <span className="lf-plays__text">{p.description}</span>
            <span className="lf-plays__score">{p.awayScore ?? ''}–{p.homeScore ?? ''}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
