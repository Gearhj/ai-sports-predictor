/**
 * Focus mode's "why AISP" layer (owner 10-27: "too bare … provide some benefits of AISP so that they know they're on
 * a superior platform … still feel like an immersive experience"). Quiet, fast pieces, no heavy animation:
 *
 *   TopPlays     tonight's strongest AISP calls as swipeable cards: a confidence ring, AISP vs market, the edge in
 *                points, the best US price, any key player out, and one tap to pick.
 *   EdgeBar      AISP's number and the market's on one track, with the gap labelled.
 *   InjuryBadge  the key players out for a game, from AISP's availability board (every sport, ESPN public feeds).
 *   TrustStrip   AISP's paper record by sport and its last ten results, so the numbers have a track record.
 *   SportChips   one-tap sport filter with counts.
 */
import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { Crest } from '../flow/Crest'
import { matches, type EspnTeam } from '../flow/Alerts'
import { allPositions, american, pct, resultOf, usd, type PulseGame } from '../pulse/pulseData'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { cacheKey } from '../../services/queryCache'
import type { Ledger } from '../../services/commandCenter'

export const SPORT_COLOR: Record<string, string> = { NFL: '#34d399', CFB: '#c084fc', NBA: '#ff9f43', NHL: '#67e8f9', MLB: '#ff6b6b' }

// ------------------------------------------------------------ availability --
export interface KeyPlayer { espn_athlete_id: string; name: string; role: string; status: string; raw_status?: string | null }
export interface AvailTeam { abbreviation: string; name?: string; key_players: KeyPlayer[]; notable: { name: string; status: string; raw_status: string; long_term: boolean; position?: string }[] }
export interface AvailGame { league: string; espn_event_id: string; name: string; starts_at: string; state: 'CLEAR' | 'KEY_OUT' | 'UNKNOWN'; reason: string; teams: Record<string, AvailTeam> }

export function useAvailability(day: string) {
  const loader = useCallback(async (signal: AbortSignal) => apiRequest<{ games: AvailGame[] }>(`/availability?date=${day}`, {}, signal), [day])
  return useApiResource<{ games: AvailGame[] }>(loader, { key: cacheKey('availability', { day }), staleTimeMs: 120_000 })
}

const asTeam = (t: AvailTeam | undefined): EspnTeam => ({ abbr: t?.abbreviation ?? '', name: t?.name ?? '', short: '', location: '', score: 0 })
export function availabilityFor(g: PulseGame, board: AvailGame[] | undefined): AvailGame | null {
  const lg = g.sport.toLowerCase()
  return (board ?? []).find((a) => a.league === g.sport && matches(asTeam(a.teams.home), g.home.abbr, lg) && matches(asTeam(a.teams.away), g.away.abbr, lg)) ?? null
}
/** The key players listed out / doubtful / questionable, most serious first. */
export function keyOut(a: AvailGame | null): { team: string; name: string; status: string; role: string }[] {
  if (!a) return []
  const rank: Record<string, number> = { OUT: 0, DOUBTFUL: 1, QUESTIONABLE: 2 }
  return Object.values(a.teams).flatMap((t) => t.key_players.filter((k) => k.status in rank).map((k) => ({ team: t.abbreviation, name: k.name, status: k.status, role: k.role })))
    .sort((x, y) => rank[x.status] - rank[y.status])
}

export function InjuryBadge({ a, compact = false }: { a: AvailGame | null; compact?: boolean }) {
  const out = keyOut(a)
  if (!a) return null
  if (!out.length) return a.state === 'CLEAR' ? <span className="fx-inj is-clear" title="Every key player is available on the latest injury report">✓ Key players in</span> : null
  const first = out[0]
  const last = first.name.split(' ').slice(-1)[0]
  return (
    <span className={`fx-inj is-${first.status.toLowerCase()}`} title={out.map((o) => `${o.team} ${o.name} (${o.role}) ${o.status}`).join('\n')}>
      ⚠ {first.team} {compact ? last : first.name} {first.status === 'QUESTIONABLE' ? 'Q' : first.status === 'DOUBTFUL' ? 'D' : 'OUT'}{out.length > 1 ? ` +${out.length - 1}` : ''}
    </span>
  )
}

// ----------------------------------------------------------------- visuals --
/** A confidence ring: AISP's chance for its side, drawn as an arc. */
export function Ring({ p, color = '#fbbf24', size = 64 }: { p: number; color?: string; size?: number }) {
  const r = size / 2 - 5, c = 2 * Math.PI * r
  return (
    <svg className="fx-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} className="fx-ring__track" />
      <circle cx={size / 2} cy={size / 2} r={r} className="fx-ring__arc" stroke={color} strokeDasharray={`${c * p} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="54%" textAnchor="middle" className="fx-ring__txt">{Math.round(p * 100)}%</text>
    </svg>
  )
}

/** AISP and the market on one 0-100% track for one side. */
export function EdgeBar({ model, market, team }: { model: number; market: number | null; team: string }) {
  const edge = market == null ? null : (model - market) * 100
  const tone = edge == null ? 'none' : Math.abs(edge) > 10 ? 'warn' : edge >= 2 ? 'up' : edge <= -2 ? 'down' : 'flat'
  return (
    <div className={`fx-edge is-${tone}`}>
      <div className="fx-edge__track">
        {market != null && <i className="fx-edge__mkt" style={{ left: `${market * 100}%` }} title={`Market ${pct(market)}`} />}
        <i className="fx-edge__aisp" style={{ left: `${model * 100}%` }} title={`AISP ${pct(model)}`} />
        {market != null && <em style={{ left: `${Math.min(model, market) * 100}%`, width: `${Math.abs(model - market) * 100}%` }} />}
      </div>
      <span className="fx-edge__lab">
        <b className="is-aisp">AISP {pct(model)}</b>
        {market != null ? <> · Market {pct(market)} · <b>{tone === 'warn' ? `${edge!.toFixed(1)} pts · model warning` : tone === 'flat' ? 'market agrees' : `${edge! > 0 ? '+' : ''}${edge!.toFixed(1)} pts on ${team}`}</b></> : ' · no fresh market yet'}
      </span>
    </div>
  )
}

export function Countdown({ iso }: { iso: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(t) }, [])
  const ms = Date.parse(iso) - now
  if (!Number.isFinite(ms) || ms <= 0) return null
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000)
  return <span className="fx-count">{h >= 24 ? `in ${Math.floor(h / 24)}d ${h % 24}h` : h ? `in ${h}h ${m}m` : `in ${m}m`}</span>
}

// ------------------------------------------------------------- top plays --
export interface Play { g: PulseGame; side: 'HOME' | 'AWAY'; model: number; market: number | null; edge: number | null; kind: 'BET' | 'LEAN' }

/** AISP's bets first, then the model's biggest edges inside the 10-point warning line (the owner's gap rule). */
export function topPlays(games: PulseGame[]): Play[] {
  const out: Play[] = []
  for (const g of games) {
    if (g.phase === 'FINAL' || g.modelHome == null) continue
    const sel = (g.position?.selection ?? '').toUpperCase()
    if (g.position) {
      const side = sel.includes(g.home.abbr.toUpperCase()) || sel.includes('HOME') ? 'HOME' : 'AWAY'
      const model = side === 'HOME' ? g.modelHome : 1 - g.modelHome
      const market = g.marketHome == null ? null : side === 'HOME' ? g.marketHome : 1 - g.marketHome
      out.push({ g, side, model, market, edge: market == null ? null : (model - market) * 100, kind: 'BET' })
    } else if (g.lean && g.lean.gap >= 0.02 && g.lean.gap <= 0.10 && g.phase === 'UPCOMING') {   // gap is a probability
      out.push({ g, side: g.lean.side, model: g.lean.model, market: g.lean.market, edge: g.lean.gap * 100, kind: 'LEAN' })
    }
  }
  return out.sort((a, b) => (a.kind === b.kind ? (b.edge ?? 0) - (a.edge ?? 0) : a.kind === 'BET' ? -1 : 1)).slice(0, 8)
}

export function TopPlays({ plays, board, onPick, picked, price }: { plays: Play[]; board: AvailGame[] | undefined; onPick: (p: Play) => void; picked: (gameId: string) => boolean; price: (p: Play) => { american: number; book: string } | null }) {
  if (!plays.length) return null
  return (
    <section className="fx-plays" aria-label="AISP's top plays">
      <header><h2>AISP’s top plays</h2><small>Its bets first, then the model’s biggest edges inside the 10-point warning line</small></header>
      <div className="fx-plays__rail">
        {plays.map((p, i) => {
          const t = p.side === 'HOME' ? p.g.home.abbr : p.g.away.abbr
          const a = availabilityFor(p.g, board)
          return (
            <article key={p.g.id} className={`fx-play is-${p.kind.toLowerCase()}`} style={{ '--sc': SPORT_COLOR[p.g.sport] ?? '#94a3b8', animationDelay: `${i * 70}ms` } as CSSProperties}>
              <span className="fx-play__tag">{p.kind === 'BET' ? '★ AISP BET' : 'MODEL EDGE'} · {p.g.sport}</span>
              <div className="fx-play__main">
                <Ring p={p.model} color={p.kind === 'BET' ? '#fbbf24' : '#5eead4'} />
                <span><Crest sport={p.g.sport} abbr={t} size={34} /><b>{t}</b><small>{p.g.away.abbr} @ {p.g.home.abbr}</small><Countdown iso={p.g.startsAt} /></span>
              </div>
              <EdgeBar model={p.model} market={p.market} team={t} />
              <div className="fx-play__foot">
                <InjuryBadge a={a} compact />
                {picked(p.g.id) ? <span className="fx-picked">✓ On your picks</span>
                  : price(p) ? <button type="button" className="fm-pick" onClick={() => onPick(p)}>+ Pick {t} {american(price(p)!.american)} <small>{price(p)!.book}</small></button>
                    : <span className="fx-count" title="AISP buys US-book prices about 75 minutes before the start">Prices at T-75</span>}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- trust --
export function TrustStrip({ ledger }: { ledger: Ledger | null | undefined }) {
  if (!ledger) return null
  const recent = allPositions(ledger).filter((p) => ['WIN', 'LOSS', 'PUSH'].includes(resultOf(p)))
    .sort((a, b) => Date.parse(b.settled_at ?? '') - Date.parse(a.settled_at ?? '')).slice(0, 10).reverse()
  const sports = (ledger.sports ?? []).filter((s) => s.record && (s.record.WIN + s.record.LOSS) > 0)
  return (
    <section className="fx-trust" aria-label="AISP's track record">
      <div className="fx-trust__head"><b>AISP’s paper record</b><small>every bet recorded before the game, settled by the final score</small></div>
      <div className="fx-trust__row">
        {sports.map((s) => (
          <span key={s.sport} className="fx-trust__sport" style={{ '--sc': SPORT_COLOR[s.sport] ?? '#94a3b8' } as CSSProperties}>
            <i>{s.sport}</i><b>{s.record.WIN}–{s.record.LOSS}</b><em className={s.profit_loss >= 0 ? 'is-up' : 'is-down'}>{usd(s.profit_loss, true)}</em>
          </span>
        ))}
        <span className="fx-trust__form" title="Last ten settled AISP bets, oldest to newest">
          <i>LAST 10</i>{recent.map((p) => <em key={p.position_id} className={`is-${resultOf(p).toLowerCase()}`} title={`${p.matchup ?? ''} ${p.selection ?? ''} ${american(p.entry?.american ?? null)} ${resultOf(p)}`} />)}
        </span>
      </div>
    </section>
  )
}

export function SportChips({ games, value, onChange }: { games: PulseGame[]; value: string; onChange: (s: string) => void }) {
  const counts = games.reduce<Record<string, number>>((m, g) => ({ ...m, [g.sport]: (m[g.sport] ?? 0) + 1 }), {})
  const sports = Object.keys(counts).sort()
  if (sports.length < 2) return null
  return (
    <div className="fx-chips" role="group" aria-label="Sport">
      <button type="button" aria-pressed={value === 'ALL'} onClick={() => onChange('ALL')}>All <small>{games.length}</small></button>
      {sports.map((s) => <button key={s} type="button" aria-pressed={value === s} style={{ '--sc': SPORT_COLOR[s] } as CSSProperties} onClick={() => onChange(s)}>{s} <small>{counts[s]}</small></button>)}
    </div>
  )
}
