/**
 * The football Live View, laid out as the owner's design: shell (nav, sports rail, game strip), scoreboard
 * with section tabs, the LIVE PLAY VIEW beside DECISION INTELLIGENCE and the FOURTH DOWN edge, then GAME
 * PULSE · WIN PROBABILITY RIVER · DRIVE DNA, then ATTACK MAP · PLAYER IMPACT · RECENT PLAYS. One selected
 * play, team and highlight feed every panel, so one tap lights up the same evidence everywhere.
 * Research and paper only; nothing here places or suggests a wager.
 */
import { useMemo, useState, type ReactNode } from 'react'
import type { MissionGame } from '../../mission/missionData'
import { AttackMap, ROW_ZONES } from './AttackMap'
import { CoachView } from './CoachView'
import { DecisionEdge, type EdgeState } from './DecisionEdge'
import { DecisionIntel } from './DecisionIntel'
import { DriveDNA } from './DriveDNA'
import type { EpGrid, EpaPlay } from './ep'
import { FootballField, type Ghost, type Impact, type WpCard } from './FootballField'
import type { FourthTables } from './fourth'
import { ordinalDown, parseFbPlay, visibleColor, type FbLive, type FbPlay } from './footballData'
import { GamePulse, type PulseCard } from './GamePulse'
import { LiveShell, useFavorites, type StripInfo } from './LiveShell'
import { MomentumChart } from './MomentumChart'
import { LeadersCard, StatsCard, TendenciesCard } from './FootballPanels'
import { PlayerImpact } from './PlayerImpact'
import { RecentPlays } from './RecentPlays'
import { Scoreboard, type Tab } from './Scoreboard'
import type { Category, Forecast, Grid, PlayClass } from './playcall'
import type { CallRow, DefenseFacts, Projection, YardsTables } from './situation'

export interface DashProps {
  fb: FbLive
  games: MissionGame[]
  gameId: string | null
  onPickGame: (id: string) => void
  sportFilter: string | null
  onSport: (s: string | null) => void
  shownPlay: FbPlay | null
  pickedId: string | null
  latestId: string | null
  onPick: (id: string | null) => void
  nonce: number
  onReplay: () => void
  slow: boolean
  onSlow: () => void
  sound: boolean
  onSound: () => void
  view: 'qb' | 'side'
  onView: (v: 'qb' | 'side') => void
  isNfl: boolean
  grid: Grid | null
  forecast: Forecast | null
  rows: CallRow[] | null
  facts: DefenseFacts | null
  insight: string
  actual: { cls: PlayClass | null; category: Category | null }
  wpa: number | null
  ghosts: Ghost[]
  burst: boolean
  impact: Impact | null
  epa: EpaPlay[]
  wpaById: Map<string, number>
  epGrid: EpGrid | null
  fourthTables: FourthTables | null
  edge: EdgeState | null
  yards: YardsTables | null
  projected: Projection | null
  pulse: PulseCard | null
  onPulse: (c: PulseCard | null) => void
  highlight: Set<string> | null
  river: ReactNode
  booth: ReactNode
  lastPlayAge: number | null
  teamNote: string
  modelName: string
}

export function FootballDashboard(p: DashProps) {
  const { fb } = p
  const { away, home } = fb.teams!
  const st = fb.status
  const live = st?.state === 'in'
  const half = /half|end of|delay|suspend|weather/i.test(st?.detail ?? '')
  const [tab, setTab] = useState<Tab>('center')
  const [boothOpen, setBoothOpen] = useState(false)
  const [rowFocus, setRowFocus] = useState<string | null>(null)
  const [favs, toggleFav] = useFavorites()
  const offenseId = p.shownPlay ? (p.shownPlay.offense ?? p.shownPlay.start.team) : fb.situation?.possession ?? null
  const off = offenseId === home.id ? home : offenseId === away.id ? away : null
  const def = off ? (off.id === home.id ? away : home) : null
  const parsed = p.shownPlay ? parseFbPlay(p.shownPlay) : null
  const sit = p.shownPlay ? `${ordinalDown(p.shownPlay.start.down)} & ${p.shownPlay.start.distance ?? '—'}` : fb.situation?.text ?? ''
  const spot = p.shownPlay?.start.possessionText ?? fb.situation?.possessionText ?? ''
  const wp = fb.win_probability
  const wi = p.shownPlay && wp ? wp.findIndex((w) => w.playId === p.shownPlay!.id) : -1
  const homeWp = wi >= 0 && wp ? wp[wi].home : wp && wp.length ? wp[wp.length - 1].home : null
  const prevHomeWp = wi > 0 && wp ? wp[wi - 1].home : null
  const wpCard: WpCard | null = useMemo(() => {
    if (homeWp == null || !off) return null
    const offHome = off.id === home.id
    const pct = offHome ? homeWp : 1 - homeWp
    const delta = prevHomeWp == null ? null : (offHome ? homeWp - prevHomeWp : prevHomeWp - homeWp) * 100
    return { team: off.abbr, pct, delta }
  }, [homeWp, prevHomeWp, off, home.id])
  const strip: StripInfo = {
    league: p.isNfl ? 'NFL' : 'NCAAF', week: fb.week ?? null, season: fb.season ?? null, venue: fb.venue ?? null, city: fb.venue_city ?? null, weather: fb.weather ?? null,
    title: `${away.abbr} @ ${home.abbr}`, date: fb.date ?? null,
    model: p.modelName, broadcast: fb.broadcast ?? null, live, clock: live ? `Q${st?.period} ${st?.clock}` : st?.short ?? '', status: st?.state === 'post' ? 'FINAL' : st?.state === 'pre' ? 'PREGAME' : half ? (st?.detail ?? 'BREAK').toUpperCase() : 'GAME',
  }
  const formation = parsed?.formation ? parsed.formation.replace('-', ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : null
  const personnel = parsed && ['pass', 'incomplete', 'interception', 'sack', 'rush'].includes(parsed.kind) ? '1 RB | 1 TE | 3 WR' : parsed?.kind === 'fieldgoal' || parsed?.kind === 'punt' ? 'Special teams' : null
  const reviewing = Boolean(p.pickedId && p.shownPlay)
  const wpPill = off && wpCard ? { team: off.abbr, pct: wpCard.pct, color: visibleColor(off, '#3fd8c4') } : null

  const drivesCard = (
    <section className="lf-card dc" aria-label="Drive chart">
      <header className="am__head"><h3>DRIVE CHART</h3><small className="lf-muted">{fb.drives?.length ?? 0} drives · click one to replay its last play</small></header>
      <ol className="dc__list">
        {[...(fb.drives ?? [])].reverse().slice(0, tab === 'drives' ? 40 : 8).map((d) => {
          const team = d.team_id === home.id ? home : away
          const own = d.start_yl == null ? null : d.team_id === home.id ? d.start_yl : 100 - d.start_yl
          const endOwn = d.end_yl == null ? null : d.team_id === home.id ? d.end_yl : 100 - d.end_yl
          const lastId = d.play_ids[d.play_ids.length - 1]
          return (
            <li key={d.id} className={d.score ? 'is-score' : ''}>
              <button type="button" onClick={() => lastId && p.onPick(lastId)}>
                <b style={{ color: visibleColor(team, '#3fd8c4') }}>{team.abbr}</b>
                <span className="dc__bar" title={`${d.start ?? ''} to ${endOwn != null ? `own ${endOwn}` : ''}`}>{own != null && endOwn != null ? <i style={{ left: `${Math.min(own, endOwn)}%`, width: `${Math.max(1.5, Math.abs(endOwn - own))}%`, background: visibleColor(team, '#3fd8c4') }} /> : null}</span>
                <span className="dc__meta"><small>Q{d.period} {d.clock}</small>{d.desc}</span>
                <em className={d.score ? 'is-up' : /turnover|interception|fumble/i.test(d.result ?? '') ? 'is-down' : ''}>{d.result}</em>
              </button>
            </li>
          )
        })}
        {!fb.drives?.length ? <li className="lf-muted lf-small">Drives appear as ESPN logs them.</li> : null}
      </ol>
    </section>
  )
  const scoringCard = (
    <section className="lf-card sc" aria-label="Scoring">
      <header className="am__head"><h3>SCORING</h3></header>
      <ol className="sc__list">
        {(fb.scoring ?? []).map((s) => <li key={s.id}><button type="button" onClick={() => p.onPick(s.id)}><b style={{ color: visibleColor(s.team_id === home.id ? home : away, '#3fd8c4') }}>{s.team}</b><span className="sc__type">{s.type}</span><span className="sc__text">{s.text}</span><small>Q{s.period} {s.clock}</small><em>{s.away}–{s.home}</em></button></li>)}
        {!fb.scoring?.length ? <li className="lf-muted lf-small">No scoring plays yet.</li> : null}
      </ol>
    </section>
  )

  const center = (
    <>
      <div className="lv__row2 lv__row2--a">
        <GamePulse plays={fb.plays ?? []} epa={p.epa} away={away} home={home} active={p.pulse?.key ?? null} onShow={p.onPulse} wpPill={wpPill} />
        <div className="lv__river">{p.river}</div>
      </div>
      <div className="lv__row2 lv__row2--b">
        <DecisionEdge ep={p.epGrid} tables={p.fourthTables} state={p.edge} isNfl={p.isNfl} />
        <DriveDNA plays={fb.plays ?? []} epa={p.epa} away={away} home={home} defaultTeamId={offenseId} tables={p.yards} />
      </div>
      <div className="lv__row2 lv__row2--c">
        <PlayerImpact fb={fb} epa={p.epa} away={away} home={home} defaultTeamId={offenseId} active={p.pulse?.key ?? null} onShow={p.onPulse} />
        <RecentPlays highlight={p.highlight} plays={fb.plays ?? []} epa={p.epa} selectedId={p.pickedId} latestId={p.latestId} onSelect={(id) => p.onPick(id)} />
      </div>
      <CoachView plays={fb.plays ?? []} epa={p.epa} away={away} home={home} shownPlay={p.shownPlay} defaultTeamId={offenseId} grid={p.grid} ep={p.epGrid} tables={p.fourthTables} isNfl={p.isNfl} onPick={(id) => p.onPick(id)}
        onShow={(ids, label, teamId) => p.onPulse(p.pulse?.key === `coach:${label}` ? null : { key: `coach:${label}`, headline: label, detail: `${ids.length} plays`, stat: '', ids, teamId, tone: 'neutral', score: 1 })} />
    </>
  )
  const tabBody: Record<Tab, ReactNode> = {
    center,
    prob: <div className="lv__row2"><div className="lv__stack"><div className="lv__river lv__river--tall">{p.river}</div><MomentumChart highlight={p.highlight} wpaById={p.wpaById} onPick={(id) => p.onPick(id)} epa={p.epa} away={away} home={home} shownId={p.shownPlay?.id ?? null} live={live} note={p.teamNote} /></div></div>,
    props: <div className="lv__row2"><PlayerImpact fb={fb} epa={p.epa} away={away} home={home} defaultTeamId={offenseId} active={p.pulse?.key ?? null} onShow={p.onPulse} /><section className="lf-card"><h3>PLAYER PROPS</h3><p className="lf-muted lf-small">Player prop lines are not collected in test mode (the prop collector is inactive by design). This tab shows each player's involvement and expected points added instead; prop prices would appear here only if a paid feed is approved.</p><LeadersCard fb={fb} /></section></div>,
    tend: <div className="lv__row3"><DriveDNA plays={fb.plays ?? []} epa={p.epa} away={away} home={home} defaultTeamId={away.id} tables={p.yards} /><DriveDNA plays={fb.plays ?? []} epa={p.epa} away={away} home={home} defaultTeamId={home.id} tables={p.yards} /><TendenciesCard fb={fb} offenseId={offenseId} /></div>,
    drives: <div className="lv__row2">{drivesCard}<RecentPlays highlight={p.highlight} plays={fb.plays ?? []} epa={p.epa} selectedId={p.pickedId} latestId={p.latestId} onSelect={(id) => p.onPick(id)} /></div>,
    scoring: <div className="lv__row2">{scoringCard}<div className="lv__river lv__river--tall">{p.river}</div></div>,
    box: <div className="lv__row2"><StatsCard fb={fb} /><LeadersCard fb={fb} /></div>,
  }

  return (
    <LiveShell games={p.games} gameId={p.gameId} onPick={p.onPickGame} sportFilter={p.sportFilter} onSport={p.onSport} favs={favs} onFav={toggleFav} strip={strip} onBooth={() => setBoothOpen((v) => !v)}>
      <div className="lv">
        <div className="lv__top">
          <div className="lv__left">
            <Scoreboard fb={fb} situation={sit} spot={spot} offenseId={offenseId} tab={tab} onTab={setTab} reviewing={reviewing} />
            <section className="lv-field" aria-label="Live play view">
              <header className="lv-field__head">
                <h3>LIVE PLAY VIEW</h3>
                {off && def ? <span className="lv-field__chips"><i style={{ ['--tc' as string]: visibleColor(off, '#3fd8c4') }}>{off.abbr} · Offense</i><i style={{ ['--tc' as string]: visibleColor(def, '#ffb547') }}>{def.abbr} · Defense</i></span> : null}
                <span className="lv-field__form">{formation ? <b>{formation}{parsed?.kind === 'pass' || parsed?.kind === 'incomplete' ? ` · ${parsed.depth ?? ''} ${parsed.side ?? ''}`.replace(/\s+$/, '') : ''}</b> : <b>{parsed?.label ?? 'Pre-snap'}</b>}{personnel ? <small>{personnel} <u title="Personnel is the drawn schematic, not ESPN data">(drawn)</u></small> : null}</span>
                <span className="lv-field__clock">{p.shownPlay ? `Q${p.shownPlay.period} ${p.shownPlay.clock}` : live ? `Q${st?.period} ${st?.clock}` : ''}</span>
                <span className="lv-field__tools">
                  <button type="button" className={p.view === 'side' ? 'is-on' : ''} onClick={() => p.onView('side')}>View All-22</button>
                  <button type="button" className={p.view === 'qb' ? 'is-on' : ''} onClick={() => p.onView('qb')}>3D Field</button>
                  <button type="button" onClick={p.onReplay} title="Replay this play">↻</button>
                  <button type="button" className={p.slow ? 'is-on' : ''} onClick={p.onSlow} title="Slow motion">½×</button>
                  <button type="button" className={p.sound ? 'is-on' : ''} onClick={p.onSound} title="Stadium sound">{p.sound ? '🔊' : '🔈'}</button>
                </span>
              </header>
              <FootballField view={p.view} fb={fb} play={p.shownPlay} nonce={p.nonce} slow={p.slow} ghosts={p.ghosts} actualCls={p.actual.cls} burst={p.burst} impact={p.impact} sound={p.sound} projected={p.projected} wpCard={wpCard} />
              <footer className="lv-field__foot">
                {reviewing && p.shownPlay ? <button type="button" className="lf-chipbtn fb-review" onClick={() => p.onPick(null)}>Reviewing Q{p.shownPlay.period} {p.shownPlay.clock} · Return to live</button> : <span>{live ? 'Following the latest play' : strip.status}{p.lastPlayAge != null ? ` · last play logged ${p.lastPlayAge < 90 ? `${p.lastPlayAge} s` : `${Math.round(p.lastPlayAge / 60)} min`} ago` : ''}{live && p.lastPlayAge != null && p.lastPlayAge > 240 ? (half ? ` · ${st?.detail}` : ' · feed may be delayed or play stopped') : ''}</span>}
                <span>{p.shownPlay?.text}</span>
              </footer>
            </section>
          </div>
          <aside className="lv__rail">
            <AttackMap live={live} grid={p.grid} homeId={home.id} forecast={p.forecast} forecastTeamId={offenseId} focusZones={rowFocus ? ROW_ZONES[rowFocus] ?? null : null} highlight={p.highlight} focusTeam={p.pulse?.teamId ?? null} plays={fb.plays ?? []} epa={p.epa} away={away} home={home} shownId={p.shownPlay?.id ?? null} defaultTeamId={offenseId} onPick={(id) => p.onPick(id)} />
            <DecisionIntel rows={p.rows} forecast={p.forecast} grid={p.grid} offense={off?.abbr ?? 'the offense'} defense={def?.abbr ?? 'the defense'} defenseId={def?.id ?? null} offenseId={off?.id ?? null} facts={p.facts} actual={p.actual} live={live} isNfl={p.isNfl} insight={p.insight} reviewing={reviewing} onShow={p.onPulse} active={p.pulse?.key ?? null} onFocusRow={setRowFocus} />
          </aside>
        </div>
        <div className="lv-paper">
        {tabBody[tab]}
        <footer className="lf-foot lf-muted">Research and paper only. Plays, scores, win probability and box score: ESPN's public feed through the test-mode preview server, never stored and never used for settlement. Player positions on the field are drawn, not tracked. Expected points, call probabilities, projected results and fourth-down numbers are models and league tables from NFL snaps (nflverse 2015-2026): estimates, not predictions of this game or wager signals.</footer>
        </div>
      </div>
      <div className={`lv-booth${boothOpen ? ' is-open' : ''}`} aria-hidden={!boothOpen}>
        <div className="lv-booth__head"><b>Ask the Booth</b><button type="button" onClick={() => setBoothOpen(false)} aria-label="Close">✕</button></div>
        {p.booth}
      </div>
      {!boothOpen ? <button type="button" className="lv-booth__fab" onClick={() => setBoothOpen(true)}>💬 Ask the Booth</button> : null}
    </LiveShell>
  )
}
