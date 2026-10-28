/**
 * YESTERDAY IN REVIEW (Monday Morning QB, owner 10-26: "Sunday football and a lot of NBA and hockey ... why is MMQB
 * essentially empty?"). The weekly NFL report covers AISP's NFL BETS only; this covers EVERY game of yesterday in
 * every sport: did the model's favourite win, did the market's, how AISP's bets did, and what AISP missed (AISP
 * failed vs your rule, the same split as the Decision Machine and the morning misses report). From the slate,
 * ledger and Mission Control reads the site already makes; nothing new is fetched.
 */
import { useMemo, type CSSProperties } from 'react'
import { Crest } from '../flow/Crest'
import { readGame } from '../machine/verdict'
import { useMissionControl, type MissionGame } from '../mission/missionData'
import { operationalToday, shiftDate } from '../../services/operationalDate'
import { pct, usd, usePulse, type PulseGame } from '../pulse/pulseData'

const SPORT_COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }
type Side = 'HOME' | 'AWAY'

export interface ReviewGame { g: PulseGame; winner: Side; modelWin: number | null; marketWin: number | null }
export function reviewOf(games: readonly PulseGame[]): ReviewGame[] {
  return games.filter((g) => g.phase === 'FINAL' && g.score && g.score.home !== g.score.away).map((g) => {
    const winner: Side = g.score!.home > g.score!.away ? 'HOME' : 'AWAY'
    const pw = (p: number | null) => (p == null ? null : winner === 'HOME' ? p : 1 - p)
    return { g, winner, modelWin: pw(g.modelHome), marketWin: pw(g.marketHome) }
  })
}
const favRight = (p: number | null) => (p == null ? null : p > 0.5)

export function WeekendReview() {
  const today = operationalToday()
  const day = shiftDate(today, -1)
  const pulse = usePulse(today)
  const mc = useMissionControl(day)
  const rows = useMemo(() => reviewOf(pulse.games.filter((g) => g.day === day)), [pulse.games, day])
  const verdicts = useMemo(() => new Map(((mc.data?.games ?? []) as MissionGame[]).map((g) => [g.game_id, readGame(g)])), [mc.data])
  if (pulse.loading && !rows.length) return <section className="mq-card wr"><p className="sl-empty">Reading yesterday's games…</p></section>
  if (!rows.length) return null

  const sports = [...new Set(rows.map((r) => r.g.sport))]
  const stat = (list: ReviewGame[]) => {
    const m = list.filter((r) => r.modelWin != null), k = list.filter((r) => r.marketWin != null)
    const bets = list.filter((r) => r.g.position)
    const won = bets.filter((r) => (r.g.position?.result ?? '').toUpperCase() === 'WIN').length
    const lost = bets.filter((r) => (r.g.position?.result ?? '').toUpperCase() === 'LOSS').length
    const pl = bets.reduce((t, r) => t + (r.g.position?.profit_loss ?? 0), 0)
    const v = list.map((r) => verdicts.get(r.g.id)?.verdict)
    return { n: list.length, model: m.filter((r) => favRight(r.modelWin)).length, mN: m.length, market: k.filter((r) => favRight(r.marketWin)).length, kN: k.length,
      bets: bets.length, won, lost, pl, failed: v.filter((x) => x === 'FAILED').length, rule: v.filter((x) => x === 'YOUR_RULE').length, pass: v.filter((x) => x === 'NO_EDGE').length }
  }
  const all = stat(rows)
  const withBoth = rows.filter((r) => r.modelWin != null && r.marketWin != null)
  const bestCall = [...withBoth].sort((a, b) => (b.modelWin! - b.marketWin!) - (a.modelWin! - a.marketWin!))[0]
  const worstMiss = [...rows.filter((r) => r.modelWin != null && r.modelWin < 0.5)].sort((a, b) => a.modelWin! - b.modelWin!)[0]
  const upset = [...rows.filter((r) => (r.marketWin ?? r.modelWin) != null)].sort((a, b) => (a.marketWin ?? a.modelWin!) - (b.marketWin ?? b.modelWin!))[0]
  const bets = rows.filter((r) => r.g.position).sort((a, b) => (b.g.position?.profit_loss ?? 0) - (a.g.position?.profit_loss ?? 0))
  const team = (r: ReviewGame, s: Side) => (s === 'HOME' ? r.g.home.abbr : r.g.away.abbr)
  const line = (r: ReviewGame) => `${r.g.away.abbr} ${r.g.score!.away} – ${r.g.score!.home} ${r.g.home.abbr}`
  const dayName = new Date(`${day}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' })

  return (
    <section className="mq-card wr" aria-label="Yesterday in review">
      <header className="wr__head">
        <p className="wr__kicker">YESTERDAY IN REVIEW · EVERY GAME, EVERY SPORT</p>
        <h2>{dayName}: {all.n} games across {sports.join(', ')}</h2>
        <p className="wr__lede">
          The model's favourite won <b>{all.model} of {all.mN}</b>{all.kN ? <> (the market's favourite: <b>{all.market} of {all.kN}</b>)</> : null}.
          {' '}AISP bet <b>{all.bets}</b>{all.bets ? <> and went <b>{all.won}–{all.lost}</b>, <b className={all.pl >= 0 ? 'is-up' : 'is-down'}>{usd(all.pl, true)}</b></> : null}.
          {' '}{mc.data ? <>Not bet: <b className="is-down">{all.failed} AISP failed</b> (see Daily Decisions → Yesterday for each one and its fix), <b>{all.rule} held by your rules</b>, {all.pass} with no edge.</> : <>Checking what AISP missed…</>}
        </p>
      </header>
      <div className="wr__sports">
        {sports.map((s) => { const x = stat(rows.filter((r) => r.g.sport === s)); return (
          <article key={s} className="wr__sport" style={{ '--sc': SPORT_COLOR[s] ?? '#94a3b8' } as CSSProperties}>
            <h3>{s} <small>{x.n} games</small></h3>
            <dl>
              <div><dt>Model favourite won</dt><dd>{x.mN ? `${x.model}/${x.mN}` : '—'}</dd></div>
              <div><dt>Market favourite won</dt><dd>{x.kN ? `${x.market}/${x.kN}` : '—'}</dd></div>
              <div><dt>AISP bets</dt><dd>{x.bets ? `${x.won}–${x.lost} · ${usd(x.pl, true)}` : '0'}</dd></div>
              <div><dt>AISP failed / your rule / no edge</dt><dd>{mc.data ? <><span className={x.failed ? 'is-down' : ''}>{x.failed}</span> / {x.rule} / {x.pass}</> : '…'}</dd></div>
            </dl>
          </article>
        ) })}
      </div>
      <div className="wr__moments">
        {bestCall && bestCall.modelWin! > bestCall.marketWin! ? <Moment tone="good" title="The model's best call" r={bestCall} text={`${team(bestCall, bestCall.winner)} won · model ${pct(bestCall.modelWin)} vs market ${pct(bestCall.marketWin)}`} line={line(bestCall)} /> : null}
        {worstMiss ? <Moment tone="bad" title="The model's worst miss" r={worstMiss} text={`model gave ${team(worstMiss, worstMiss.winner)} only ${pct(worstMiss.modelWin)}, and they won`} line={line(worstMiss)} /> : null}
        {upset ? <Moment tone="hot" title="Upset of the day" r={upset} text={`${team(upset, upset.winner)} won at ${pct(upset.marketWin ?? upset.modelWin)} (${upset.marketWin != null ? 'market' : 'model'})`} line={line(upset)} /> : null}
        {bets[0] ? <Moment tone="good" title="AISP's best bet" r={bets[0]} text={`${bets[0].g.position?.selection ?? ''} · ${usd(bets[0].g.position?.profit_loss ?? 0, true)}`} line={line(bets[0])} /> : null}
        {bets.length > 1 ? <Moment tone="bad" title="AISP's worst bet" r={bets[bets.length - 1]} text={`${bets[bets.length - 1].g.position?.selection ?? ''} · ${usd(bets[bets.length - 1].g.position?.profit_loss ?? 0, true)}`} line={line(bets[bets.length - 1])} /> : null}
      </div>
      <p className="wr__fine">Model and market are the stored pregame two-way probabilities; "favourite won" counts games with a winner (ties excluded). The NFL bets are dissected week by week below.</p>
    </section>
  )
}

function Moment({ title, r, text, line, tone }: { title: string; r: ReviewGame; text: string; line: string; tone: 'good' | 'bad' | 'hot' }) {
  return (
    <article className={`wr__m wr__m--${tone}`}>
      <small>{title}</small>
      <span className="wr__m-teams"><Crest sport={r.g.sport} abbr={r.g.away.abbr} size={22} /><Crest sport={r.g.sport} abbr={r.g.home.abbr} size={22} /><b>{line}</b></span>
      <span>{text}</span>
    </article>
  )
}
