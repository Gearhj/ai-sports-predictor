/**
 * The diamond: an SVG field with HTML player chips laid over it in percent
 * coordinates (so headshots can fall back and text stays crisp on an iPad).
 * Runners light their base, the count and outs sit in a broadcast HUD, and the
 * defence stands where the MLB feed says it stands. The two foul-territory
 * corners carry the numbers: THIS PLATE APPEARANCE on the left (offence) and
 * ON THE BASES / IN THE FIELD on the right (defence). On deck and in the hole
 * ride the footer. Display only; every number names its source.
 */
import { useEffect, useRef, useState } from 'react'
import { hitFlight, hitSlot, paOutcomes, ratePct, trajectoryWord, type Base, type DefenseSlot, type HitEvent, type MlbLive, type Person, type RegularBatting } from './liveFieldData'

const VB_W = 1000
const VB_H = 760
const SLOT_POS: Record<DefenseSlot | 'pitcher', { x: number; y: number; label: string }> = {
  pitcher: { x: 50, y: 60, label: 'P' },
  catcher: { x: 50, y: 90, label: 'C' },
  first: { x: 69, y: 50, label: '1B' },
  second: { x: 60, y: 37, label: '2B' },
  shortstop: { x: 40, y: 37, label: 'SS' },
  third: { x: 31, y: 50, label: '3B' },
  left: { x: 22, y: 20, label: 'LF' },
  center: { x: 50, y: 10, label: 'CF' },
  right: { x: 78, y: 20, label: 'RF' },
}
const BASE_POS: Record<Base | 'home', { x: number; y: number }> = {
  home: { x: 50, y: 81.5 },
  first: { x: 67, y: 58 },
  second: { x: 50, y: 34.5 },
  third: { x: 33, y: 58 },
}
const BASE_WORD: Record<Base, string> = { first: '1st', second: '2nd', third: '3rd' }

export function Headshot({ person, size = 44, className = '' }: { person: Person | null | undefined; size?: number; className?: string }) {
  const [broken, setBroken] = useState(false)
  const initials = (person?.name ?? '').split(' ').map((s) => s[0]).filter(Boolean).slice(0, 2).join('')
  if (!person || broken) {
    return <span className={`lf-head lf-head--fallback ${className}`} style={{ width: size, height: size }} aria-hidden="true">{initials || '·'}</span>
  }
  return <img className={`lf-head ${className}`} src={person.headshot} width={size} height={size} alt="" loading="lazy" onError={() => setBroken(true)} />
}

function Chip({ person, tag, x, y, hot, line, aura }: { person: Person | null; tag: string; x: number; y: number; hot?: boolean; line?: string | null; aura?: { out: boolean; delay: number } | null }) {
  return (
    <div className={`lf-chip${hot ? ' is-hot' : ''}${aura ? ` is-aura is-aura--${aura.out ? 'out' : 'hit'}` : ''}`} style={{ left: `${x}%`, top: `${y}%`, ['--aura-delay' as string]: aura ? `${aura.delay}s` : undefined }} title={person?.name ?? tag}>
      <Headshot person={person} size={hot ? 54 : 42} />
      <span className="lf-chip__tag">{tag}</span>
      <span className="lf-chip__name">{person?.name ? shortName(person.name) : '—'}</span>
      {line ? <span className="lf-chip__line">{line}</span> : null}
    </div>
  )
}

export function shortName(full: string): string {
  const parts = full.trim().split(/\s+/)
  if (parts.length < 2) return full
  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`
}

function Dots({ n, of, kind }: { n: number | null | undefined; of: number; kind: 'ball' | 'strike' | 'out' }) {
  return (
    <span className={`lf-dots lf-dots--${kind}`} aria-hidden="true">
      {Array.from({ length: of }, (_, i) => <i key={i} className={i < (n ?? 0) ? 'on' : ''} />)}
    </span>
  )
}

const sbLine = (r: RegularBatting | null | undefined) => (r && (r.sb ?? 0) + (r.cs ?? 0) > 0 ? `${r.sb ?? 0}-for-${(r.sb ?? 0) + (r.cs ?? 0)} (${ratePct(r.sb, (r.sb ?? 0) + (r.cs ?? 0), 0)})` : r ? 'no attempts' : '—')

/** Left corner: what this plate appearance looks like from the two season lines. */
function PlatePanel({ live }: { live: MlbLive }) {
  const reg = live.regular
  const est = paOutcomes(reg?.batter, reg?.pitcher)
  const batter = live.offense?.batter?.name ? shortName(live.offense.batter.name) : 'Batter'
  const pitcher = live.defense?.pitcher?.name ? shortName(live.defense.pitcher.name) : 'pitcher'
  const rows = est ? [
    { k: 'Strikeout', v: est.k, c: 'k' }, { k: 'Walk / HBP', v: est.bb, c: 'bb' }, { k: 'Out in play', v: est.out, c: 'out' },
    { k: 'Single', v: est.single, c: 'h' }, { k: 'Extra bases', v: est.xbh, c: 'xbh' }, { k: 'Home run', v: est.hr, c: 'hr' },
  ] : []
  const max = rows.reduce((m, r) => Math.max(m, r.v), 0.01)
  return (
    <div className="lf-panel lf-panel--left">
      <h4 title={est ? `Estimate from ${est.basis}: odds-ratio blend of batter, pitcher and league rates. Not an AISP model.` : undefined}>This plate appearance <em>est.</em></h4>
      <p className="lf-panel__sub">{batter} vs {pitcher}</p>
      {est ? (
        <ul className="lf-bars">
          {rows.map((r) => (
            <li key={r.k} className={`lf-pa lf-pa--${r.c}`}>
              <span className="lf-pa__k">{r.k}</span>
              <span className="lf-pa__track"><i style={{ width: `${Math.max(2, (r.v / max) * 100)}%` }} /></span>
              <span className="lf-pa__v">{(r.v * 100).toFixed(0)}%</span>
            </li>
          ))}
        </ul>
      ) : <p className="lf-panel__empty">Needs both 2026 regular-season lines (50+ PA, 50+ batters faced).</p>}
    </div>
  )
}

/** Right corner: the running game and the double play, from regular-season lines. */
function BasesPanel({ live }: { live: MlbLive }) {
  const reg = live.regular
  const runners = (['first', 'second', 'third'] as Base[]).map((b) => ({ base: b, who: live.runners?.[b] ?? null, line: reg?.runners?.[b] ?? null })).filter((r) => r.who)
  const outs = live.count?.outs ?? 0
  const onFirst = Boolean(live.runners?.first)
  const dpOn = onFirst && outs < 2
  const b = reg?.batter
  const p = reg?.pitcher
  const c = reg?.catcher
  const catcher = live.defense?.catcher?.name ? shortName(live.defense.catcher.name) : 'Catcher'
  const goao = p?.go_ao ? Number(p.go_ao) : null
  return (
    <div className="lf-panel lf-panel--right">
      <h4 title="2026 regular season, MLB Stats API. Season rates, not situational probabilities.">On the bases · in the field</h4>
      {runners.length ? runners.map((r) => (
        <div key={r.base} className="lf-fact">
          <span className="lf-fact__k">{shortName(r.who!.name ?? 'Runner')} · {BASE_WORD[r.base]}</span>
          <span className="lf-fact__v">SB {sbLine(r.line)}</span>
        </div>
      )) : (
        <div className="lf-fact"><span className="lf-fact__k">Bases empty</span><span className="lf-fact__v">{b ? `batter SB ${sbLine(b)}` : ''}</span></div>
      )}
      {runners.length ? (
        <div className="lf-fact"><span className="lf-fact__k">{catcher} (C)</span><span className="lf-fact__v">throws out {c?.cs_pct ? `${(Number(c.cs_pct) * 100).toFixed(0)}%` : '—'}</span></div>
      ) : null}
      <div className={`lf-fact${dpOn ? ' is-on' : ''}`}>
        <span className="lf-fact__k">Double play {dpOn ? 'in order' : 'off'}</span>
        <span className="lf-fact__v">{b ? `GIDP ${ratePct(b.gidp, b.pa)}` : '—'}{goao != null ? ` · ${shortName(live.defense?.pitcher?.name ?? 'P')} ${goao >= 1.2 ? 'ground-ball' : goao <= 0.8 ? 'fly-ball' : 'neutral'} pitcher` : ''}</span>
      </div>
    </div>
  )
}

export function Diamond({ field, homeAbbr, awayAbbr }: { field: MlbLive | null; homeAbbr: string; awayAbbr: string }) {
  const live = field?.found ? field : null
  const st = live?.status
  const count = live?.count
  const runners = live?.runners
  const offense = live?.offense
  const defense = live?.defense
  const inPlay = st?.abstract === 'Live'
  const battingTeam = st?.is_top ? awayAbbr : homeAbbr
  const fieldingTeam = st?.is_top ? homeAbbr : awayAbbr
  const pitchLine = live?.pitcher_stats?.game
  const pitches = pitchLine?.numberOfPitches ?? pitchLine?.pitchesThrown ?? null
  const batLine = live?.batter_stats
  const od = live?.offense?.on_deck ?? null
  const hit: HitEvent | null = live?.last_hit ?? null
  const slot = hitSlot(hit)
  const slotPos = slot ? SLOT_POS[slot] : null
  const flight = hit ? hitFlight(hit, slotPos ? { x: slotPos.x * 10, y: slotPos.y * 7.6 } : null) : null
  const [shown, setShown] = useState<string | null>(null)
  const seen = useRef<string | null>(null)
  const hitKey = hit?.key ?? null
  const timer = useRef<number | undefined>(undefined)
  const show = (key: string) => {
    window.clearTimeout(timer.current)
    setShown(key)
    timer.current = window.setTimeout(() => setShown(null), 9000)
  }
  useEffect(() => {
    if (!hitKey || seen.current === hitKey) return
    seen.current = hitKey
    const endedAt = Date.parse(hitKey.split('-').slice(1).join('-'))
    // Only a batted ball from the last few minutes plays on its own; older ones wait for Replay.
    if (Number.isFinite(endedAt) && Date.now() - endedAt > 3 * 60_000) return
    window.clearTimeout(timer.current)
    setShown(hitKey)
    timer.current = window.setTimeout(() => setShown(null), 9000)
  }, [hitKey])
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const replay = () => {
    if (!hitKey) return
    window.clearTimeout(timer.current)
    setShown(null)
    window.setTimeout(() => show(hitKey), 60)
  }
  const aura = shown && slot && hit ? { out: hit.is_out, delay: flight?.dur ?? 1 } : null
  const ih = live?.offense?.in_hole ?? null
  const odReg = live?.regular?.on_deck ?? null

  return (
    <section className="lf-field" aria-label="Field view">
      <div className="lf-field__frame" style={{ aspectRatio: `${VB_W} / ${VB_H}` }}>
        <svg className="lf-field__svg" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          <defs>
            <radialGradient id="lf-grass" cx="50%" cy="85%" r="90%">
              <stop offset="0%" stopColor="#2f8f4e" />
              <stop offset="60%" stopColor="#1f6b3a" />
              <stop offset="100%" stopColor="#174f2c" />
            </radialGradient>
            <pattern id="lf-mow" width="80" height="80" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="40" height="80" fill="rgba(255,255,255,.035)" />
            </pattern>
          </defs>
          <path d="M 500 700 L 40 240 A 560 560 0 0 1 960 240 Z" fill="url(#lf-grass)" />
          <path d="M 500 700 L 40 240 A 560 560 0 0 1 960 240 Z" fill="url(#lf-mow)" />
          <path d="M 40 240 A 560 560 0 0 1 960 240" fill="none" stroke="#8a5a2b" strokeWidth="22" opacity=".85" />
          <path d="M 40 240 A 560 560 0 0 1 960 240" fill="none" stroke="#0b1b2b" strokeWidth="6" />
          <path d="M 500 700 L 215 415 A 405 405 0 0 1 785 415 Z" fill="#b87a3e" opacity=".92" />
          <polygon points="500,620 670,450 500,280 330,450" fill="#2b8a4a" />
          <polygon points="500,620 670,450 500,280 330,450" fill="url(#lf-mow)" />
          <polygon points="500,620 670,450 500,280 330,450" fill="none" stroke="#e8d9c2" strokeWidth="5" opacity=".9" />
          <line x1="500" y1="620" x2="40" y2="160" stroke="#f4efe6" strokeWidth="4" />
          <line x1="500" y1="620" x2="960" y2="160" stroke="#f4efe6" strokeWidth="4" />
          <circle cx="500" cy="455" r="42" fill="#b87a3e" />
          <circle cx="500" cy="455" r="42" fill="none" stroke="#9b6431" strokeWidth="3" />
          <rect x="488" y="449" width="24" height="7" fill="#f4efe6" />
          <circle cx="500" cy="622" r="62" fill="#b87a3e" />
          {(['first', 'second', 'third'] as Base[]).map((b) => {
            const p = BASE_POS[b]
            const on = Boolean(runners?.[b])
            return <rect key={b} x={(p.x / 100) * VB_W - 14} y={(p.y / 100) * VB_H - 14} width="28" height="28" transform={`rotate(45 ${(p.x / 100) * VB_W} ${(p.y / 100) * VB_H})`} fill={on ? '#ffd76a' : '#f4efe6'} stroke={on ? '#ffb547' : '#c9bfae'} strokeWidth="4" className={on ? 'lf-base is-on' : 'lf-base'} />
          })}
          <polygon points="486,632 514,632 514,646 500,656 486,646" fill="#f4efe6" stroke="#c9bfae" strokeWidth="3" />
          <rect x="446" y="606" width="30" height="52" fill="none" stroke="#f4efe6" strokeWidth="3" opacity=".8" />
          <rect x="524" y="606" width="30" height="52" fill="none" stroke="#f4efe6" strokeWidth="3" opacity=".8" />
        </svg>

        {live ? (
          <>
            {(Object.keys(SLOT_POS) as (DefenseSlot | 'pitcher')[]).map((slot) => {
              const person = slot === 'pitcher' ? defense?.pitcher ?? null : defense?.[slot] ?? null
              const pos = SLOT_POS[slot]
              const line = slot === 'pitcher'
                ? [pitches ? `${pitches} P` : null, pitchLine?.inningsPitched ? `${pitchLine.inningsPitched} IP` : null, pitchLine?.strikeOuts != null ? `${pitchLine.strikeOuts} K` : null].filter(Boolean).join(' · ') || null
                : null
              return <Chip key={slot} person={person} tag={pos.label} x={pos.x} y={pos.y} hot={slot === 'pitcher'} line={line} aura={aura && hitSlot(hit) === slot ? aura : null} />
            })}
            {(['first', 'second', 'third'] as Base[]).map((b) => {
              const r = runners?.[b]
              if (!r) return null
              const p = BASE_POS[b]
              return <Chip key={`r-${b}`} person={r} tag="RUNNER" x={p.x + (b === 'first' ? 7 : b === 'third' ? -7 : 0)} y={p.y - (b === 'second' ? 9 : 6)} />
            })}
            {offense?.batter ? (
              <Chip person={offense.batter} tag={`AT BAT${offense.bat_side ? ` · ${offense.bat_side}HB` : ''}`} x={offense.bat_side === 'L' ? 58 : 42} y={84} hot
                line={batLine ? `${batLine.game.summary ?? '0-0 today'}` : null} />
            ) : null}
            {shown && flight && hit ? (
              <svg className="lf-ball-layer" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
                <g key={shown}>
                  <path d={flight.d} className="lf-ball__trail" pathLength={1} style={{ animationDuration: `${flight.dur}s` }} />
                  <circle r="8" className="lf-ball">
                    <animateMotion dur={`${flight.dur}s`} fill="freeze" path={flight.d} calcMode="spline" keyTimes="0;1" keySplines="0.25 0.5 0.5 1" />
                    {flight.peak > 0 ? <animate attributeName="r" values="7;13;7" dur={`${flight.dur}s`} fill="freeze" /> : null}
                  </circle>
                  <circle cx={flight.end.x} cy={flight.end.y} r="10" className={`lf-land${hit.is_out ? ' is-out' : ''}`} style={{ animationDelay: `${flight.dur}s` }} />
                </g>
              </svg>
            ) : null}
            {shown && hit ? (
              <div className={`lf-toast${hit.is_out ? ' is-out' : ''}`} key={`t-${shown}`} style={{ animationDelay: `${flight?.dur ?? 1}s` }}>
                <b>{hit.event ?? 'In play'}</b> · {trajectoryWord(hit.trajectory)}{hit.speed ? ` · ${hit.speed} mph` : ''}{hit.angle != null ? ` · ${hit.angle}°` : ''}{hit.distance && hit.distance > 20 ? ` · ${hit.distance} ft` : ''}{hit.is_out && hit.fielder ? ` · caught by ${hit.fielder}` : ''}
              </div>
            ) : null}
            {inPlay ? <PlatePanel live={live} /> : null}
            {inPlay ? <BasesPanel live={live} /> : null}
          </>
        ) : null}

        {live && !defense?.pitcher && !defense?.catcher && !inPlay ? (
          <div className="lf-nolineup" role="status">
            <b>Lineups are not posted yet</b>
            <span>MLB posts them about two to three hours before first pitch. The field fills in by itself.</span>
            <span className="lf-nolineup__sp">
              {(['away', 'home'] as const).map((side) => (
                <span key={side} className="lf-deck"><Headshot person={live.teams?.[side].probable ?? null} size={44} /><span><small>PROBABLE · {side === 'away' ? awayAbbr : homeAbbr}</small><br /><b>{live.teams?.[side].probable?.name ?? 'not announced'}</b></span></span>
              ))}
            </span>
          </div>
        ) : null}
        <div className="lf-hud">
          <div className="lf-hud__row">
            <span className="lf-hud__inning">
              {inPlay && st ? <><b>{st.is_top ? '▲' : '▼'}</b> {st.inning_ordinal ?? st.inning}</> : <span>{st?.detailed ?? (field ? 'No feed' : 'Loading feed…')}</span>}
            </span>
            {inPlay ? <span className="lf-hud__teams">{battingTeam} batting · {fieldingTeam} in the field</span> : null}
          </div>
          {inPlay ? (
            <div className="lf-hud__count" aria-label={`Count ${count?.balls ?? 0} balls ${count?.strikes ?? 0} strikes, ${count?.outs ?? 0} out`}>
              <span><small>B</small><Dots n={count?.balls} of={3} kind="ball" /></span>
              <span><small>S</small><Dots n={count?.strikes} of={2} kind="strike" /></span>
              <span><small>O</small><Dots n={count?.outs} of={2} kind="out" /></span>
            </div>
          ) : null}
        </div>
      </div>
      {live && (od || ih) ? (
        <div className="lf-field__foot">
          {hit ? <button type="button" className="lf-chipbtn lf-replay" onClick={replay}>↻ Replay last hit</button> : null}
          <span className="lf-deck"><small>ON DECK</small><Headshot person={od} size={34} /><b>{od?.name ? shortName(od.name) : '—'}</b>{odReg ? <em>{odReg.ops} OPS · {odReg.hr} HR</em> : null}</span>
          <span className="lf-deck"><small>IN THE HOLE</small><Headshot person={ih} size={34} /><b>{ih?.name ? shortName(ih.name) : '—'}</b></span>
          <span className="lf-field__src">Field: MLB Stats API{live.retrieved_at ? ` · as of ${new Date(live.retrieved_at).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', second: '2-digit' })} ET` : ''} · headshots MLB · display only</span>
        </div>
      ) : (
        <div className="lf-field__foot"><span className="lf-field__src">Field: MLB Stats API · headshots MLB · display only</span></div>
      )}
    </section>
  )
}
