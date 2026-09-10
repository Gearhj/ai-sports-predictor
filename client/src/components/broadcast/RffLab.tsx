/**
 * RFF Lab — Ripe For Fuckery.
 *
 * The measure is meant to be fun. The thing that makes it fun rather than
 * irresponsible is that it never rounds an absence up into a finding: a
 * component with no data reads "Unavailable" in the same place a score would
 * be, the coverage that is actually behind the number is printed beside it,
 * and the range the score could occupy if the missing parts were at either
 * extreme is one glance away.
 *
 * The words are chosen with the same care. The bands are descriptive of the
 * MARKET, never of anyone's conduct, and every surface that leaves this
 * component carries the line that a high score is not evidence a game was
 * manipulated. That includes the share card, which is the one piece designed
 * to travel without the rest of the page around it.
 */
import { useState } from 'react'
import { AlertTriangle, Check, Copy, Flame, Info } from 'lucide-react'
import { agoOf, clockOf, pct } from './primitives'
import type { RffComponent, RffGame } from '../../types/marketIntelligence'

const BAND_TONE: Record<string, string> = {
  CALM: 'var(--bx-accent)',
  CURIOUS: '#4a90ff',
  SPICY: 'var(--bx-warn)',
  MAXIMUM_FUCKERY: 'var(--bx-bad)',
  UNSCORED: 'var(--bx-dim)',
}

export function bandTone(key: string): string {
  return BAND_TONE[key] ?? 'var(--bx-dim)'
}

/**
 * The gauge draws the point estimate as a needle and the plausible range as an
 * arc behind it. Drawing only the needle would say the score is known to a
 * tenth when a third of its weight is missing.
 */
export function RffGauge({ game, size = 200 }: { game: RffGame; size?: number }) {
  const tone = bandTone(game.band.key)
  // A half circle, drawn inside its own box: the arc's lowest point IS the
  // baseline, so nothing hangs below the viewBox and the needle cannot land
  // outside the dial.
  const stroke = 12
  const radius = size / 2 - stroke
  const cx = size / 2
  const cy = size / 2
  const height = cy + stroke + 26
  const at = (score: number) => Math.PI + (Math.min(100, Math.max(0, score)) / 100) * Math.PI
  const point = (angle: number, r = radius) => ({
    x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r,
  })
  const arc = (from: number, to: number, r = radius) => {
    const a = point(from, r), b = point(to, r)
    const large = to - from > Math.PI ? 1 : 0
    return `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${b.x.toFixed(1)} ${b.y.toFixed(1)}`
  }
  const range = game.score_range
  const needle = game.score == null ? null : point(at(game.score))

  return (
    <div className="bx-rff__gauge" style={{ color: tone }}>
      <svg width={size} height={height} viewBox={`0 0 ${size} ${height}`} role="img"
        aria-label={game.score == null
          ? 'No RFF score: no component has data'
          : `Provisional RFF ${game.score.toFixed(0)}, plausible range ${range?.[0].toFixed(0)} to ${range?.[1].toFixed(0)}, ${(game.evidence_coverage * 100).toFixed(0)} percent evidence coverage`}>
        <path d={arc(Math.PI, 2 * Math.PI)} fill="none" stroke="rgba(255,255,255,.09)"
          strokeWidth={stroke} strokeLinecap="round" />
        {/* The plausible range, drawn behind the needle: a third of the weight
            is missing, and a bare needle would claim a precision we do not have. */}
        {range && (
          <path d={arc(at(range[0]), at(range[1]))} fill="none" stroke={tone}
            strokeWidth={stroke} strokeOpacity={0.25} strokeLinecap="round" />
        )}
        {game.score != null && game.score > 0 && (
          <path d={arc(Math.PI, at(game.score))} fill="none" stroke={tone}
            strokeWidth={stroke} strokeLinecap="round" />
        )}
        {needle && (
          <circle cx={needle.x} cy={needle.y} r={6} fill="#04121d" stroke={tone} strokeWidth={3} />
        )}
        <text x={cx} y={cy - 6} textAnchor="middle" fill="currentColor"
          style={{ font: '800 42px/1 inherit', letterSpacing: '-.04em' }}>
          {game.score == null ? '—' : game.score.toFixed(0)}
        </text>
        <text x={cx} y={cy + 16} textAnchor="middle" fill="var(--bx-muted)"
          style={{ font: '600 10px var(--bx-mono)', letterSpacing: '.13em' }}>
          {game.band.label.toUpperCase()}
        </text>
        <text x={cx - radius} y={cy + 20} textAnchor="middle" fill="var(--bx-dim)"
          style={{ font: '600 9px var(--bx-mono)' }}>0</text>
        <text x={cx + radius} y={cy + 20} textAnchor="middle" fill="var(--bx-dim)"
          style={{ font: '600 9px var(--bx-mono)' }}>100</text>
      </svg>
      {range && (
        <span className="bx-dim" style={{ textAlign: 'center' }}>
          plausible {range[0].toFixed(0)}–{range[1].toFixed(0)} at {pct(game.evidence_coverage, 0)} coverage
        </span>
      )}
    </div>
  )
}

/** Component contributions, drawn as the weighted shares they actually are. */
export function RffComponents({ game }: { game: RffGame }) {
  return (
    <div className="bx-rff__components">
      {game.components.map((component) => (
        <ComponentRow key={component.key} component={component}
          share={game.contributions.find((entry) => entry.key === component.key)?.share_of_score ?? null} />
      ))}
    </div>
  )
}

function ComponentRow({ component, share }: { component: RffComponent; share: number | null }) {
  const [open, setOpen] = useState(false)
  const available = component.available
  return (
    <div className={`bx-rff__component${available ? '' : ' is-unavailable'}`}>
      <button type="button" className="bx-rff__component-head" aria-expanded={open}
        onClick={() => setOpen((value) => !value)}>
        <span className="bx-rff__component-name">
          {component.label}
          {component.limitation && <Info size={12} aria-label="has a stated limitation" />}
        </span>
        <span className="bx-rff__component-track" aria-hidden="true">
          {available && component.score != null && (
            <span style={{ width: `${component.score}%` }} />
          )}
        </span>
        <b className={`bx-num${available ? '' : ' bx-rff__unavailable'}`}>{component.display}</b>
        <small className="bx-dim">
          {available && share != null ? `${(share * 100).toFixed(0)}% of score` : `weight ${(component.weight * 100).toFixed(0)}%`}
        </small>
      </button>
      {open && (
        <div className="bx-details__body" style={{ paddingTop: 8 }}>
          <p><strong>How it is measured.</strong> {component.basis}</p>
          {component.requires && <p><strong>Why it is unavailable.</strong> {component.requires}</p>}
          {component.limitation && <p><strong>Limitation.</strong> {component.limitation}</p>}
          {available && Object.keys(component.detail).length > 0 && (
            <div className="bx-scroll">
              <table className="bx-table">
                <tbody>
                  {Object.entries(component.detail).map(([key, value]) => (
                    <tr key={key}>
                      <th scope="row">{key.replace(/_/g, ' ')}</th>
                      <td className="bx-num">
                        {value == null ? '—'
                          : typeof value === 'number' ? Number(value.toFixed(6)).toString()
                            : String(value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** The one surface designed to leave the page, so it carries its own caveat. */
export function RffShareCard({ game }: { game: RffGame }) {
  const [copied, setCopied] = useState(false)
  const card = game.share_card
  const text = [
    `${card.matchup ?? 'Game'} — ${card.headline ?? 'Unscored'} (${card.band})`,
    `${pct(card.coverage, 0)} evidence coverage.`,
    ...card.triggers.map((trigger) => `· ${trigger.label}: ${trigger.score?.toFixed(0) ?? '—'}`),
    card.disclaimer,
  ].join('\n')

  return (
    <div className="bx-rff__share" style={{ borderColor: bandTone(game.band.key) }}>
      <div className="bx-rff__share-head">
        <span className="bx-eyebrow"><Flame size={12} aria-hidden="true" /> RFF Index — Ripe For Fuckery</span>
        <button type="button" className="bx-btn bx-btn--ghost"
          onClick={() => {
            // Clipboard is unavailable in some contexts; the text is on screen
            // either way, so a failure changes nothing the reader can see.
            navigator.clipboard?.writeText(text).then(
              () => { setCopied(true); window.setTimeout(() => setCopied(false), 2000) },
              () => undefined,
            )
          }}>
          {copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy card'}
        </button>
      </div>
      <strong className="bx-rff__share-matchup">{card.matchup ?? 'Unnamed game'}</strong>
      <div className="bx-rff__share-score" style={{ color: bandTone(game.band.key) }}>
        {card.headline ?? 'Unscored'} <em>{card.band}</em>
      </div>
      <p className="bx-muted">{pct(card.coverage, 0)} evidence coverage · {game.band.description}</p>
      {card.triggers.length > 0 ? (
        <ul className="bx-rff__triggers">
          {card.triggers.map((trigger) => (
            <li key={trigger.label}>
              <b>{trigger.score?.toFixed(0) ?? '—'}</b> {trigger.label}
            </li>
          ))}
        </ul>
      ) : (
        <p className="bx-dim">No available component is scoring high enough to name a trigger.</p>
      )}
      <p className="bx-dim bx-rff__disclaimer">
        <AlertTriangle size={12} aria-hidden="true" /> {card.disclaimer}
      </p>
    </div>
  )
}

export function RffHeadline({ game }: { game: RffGame }) {
  const home = game.event.home?.abbreviation
  const away = game.event.away?.abbreviation
  return (
    <div className="bx-rff__headline">
      <div>
        <span className="bx-eyebrow">Provisional RFF</span>
        <h3>{away && home ? `${away} @ ${home}` : 'This game'}</h3>
        <p className="bx-muted">{game.explanation}</p>
        {game.earlier && (
          <p className="bx-dim" style={{ marginTop: 6 }}>
            Compared with the opening frame at {clockOf(game.earlier.t)}:{' '}
            {game.earlier.change > 0 ? 'up' : game.earlier.change < 0 ? 'down' : 'flat'}{' '}
            {Math.abs(game.earlier.change).toFixed(0)} points on{' '}
            {game.earlier.comparable_components.length} recomputable component
            {game.earlier.comparable_components.length === 1 ? '' : 's'}. {game.earlier.note}
          </p>
        )}
        <p className="bx-dim" style={{ marginTop: 6 }}>
          Read {agoOf(game.generated_at_utc) ?? 'just now'} ·{' '}
          {String(game.authority.statement ?? '')}
        </p>
      </div>
      <RffGauge game={game} />
    </div>
  )
}
