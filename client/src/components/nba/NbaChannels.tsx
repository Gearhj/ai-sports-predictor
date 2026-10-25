/**
 * The three NBA Gen1 channels, side by side and never blended.
 *
 *   Ridge expected points      POINTS. No percentage is ever drawn in this section,
 *                              and nothing here is set against a price.
 *   Elo win probability        winner fractions, the producer's declared winner channel.
 *   Joint-distribution         winner / spread / total probabilities from 10,000
 *                              paired draws; illustrative totals are labelled as
 *                              lines no sportsbook offered.
 *
 * HISTORICAL rows (the 2025-26 reconstruction) carry a visible HISTORICAL badge
 * so they are never read as a forecast published before the game.
 */
import type { NbaChannelScore, NbaChannels } from '../../types/nbaGen1'
import { NBA_HISTORICAL } from '../../types/nbaGen1'

export function pct(fraction: number | null | undefined, digits = 1): string {
  // The single multiplication by 100 in this path.
  if (fraction == null) return '—'
  return `${(fraction * 100).toFixed(digits)}%`
}

function points(value: number | null | undefined, digits = 1): string {
  return value == null ? '—' : value.toFixed(digits)
}

function num(value: number | null | undefined, digits = 3): string {
  return value == null ? '—' : value.toFixed(digits)
}

export function EvidenceBadge({ evidenceClass }: { evidenceClass: string }) {
  const historical = evidenceClass === NBA_HISTORICAL
  return (
    <span className={`nbag__badge ${historical ? 'nbag__badge--historical' : 'nbag__badge--prospective'}`}
          data-testid="nba-evidence-badge">
      {historical ? 'HISTORICAL · 2025–26 reconstruction' : 'Prospective production package'}
    </span>
  )
}

export function NbaChannelPanel({ channels, home, away }: { channels: NbaChannels; home: string; away: string }) {
  const ridge = channels.RIDGE_POINTS
  const elo = channels.ELO_WINNER
  const joint = channels.JOINT_DISTRIBUTION
  const total80 = joint.intervals?.total?.find((i) => Math.round(i.level * 100) === 80)
  const margin80 = joint.intervals?.margin?.find((i) => Math.round(i.level * 100) === 80)
  return (
    <div className="nbag__channels">
      <section className="nbag__channel" data-testid="nba-channel-ridge">
        <h4>{ridge.label} <small className="bx-dim">· points</small></h4>
        <p className="nbares__numbers">
          <span>{away} <b className="bx-num">{points(ridge.away)}</b></span>
          <span>{home} <b className="bx-num">{points(ridge.home)}</b></span>
        </p>
        <small className="bx-dim">Total {points(ridge.total)} · {home} margin {points(ridge.home_margin)} points. {ridge.note}</small>
      </section>

      <section className="nbag__channel" data-testid="nba-channel-elo">
        <h4>{elo.label} <small className="bx-dim">· probability</small></h4>
        <p className="nbares__numbers">
          <span>{away} <b className="bx-num">{pct(elo.away)}</b></span>
          <span>{home} <b className="bx-num">{pct(elo.home)}</b></span>
        </p>
        <small className="bx-dim">{elo.note}</small>
      </section>

      <section className="nbag__channel" data-testid="nba-channel-joint">
        <h4>{joint.label} <small className="bx-dim">· {joint.draw_count?.toLocaleString() ?? '—'} draws</small></h4>
        <p className="nbares__numbers">
          <span>{away} <b className="bx-num">{pct(joint.away)}</b></span>
          <span>{home} <b className="bx-num">{pct(joint.home)}</b></span>
        </p>
        <small className="bx-dim">
          Joint means {away} {points(joint.expected.away)} · {home} {points(joint.expected.home)}
          {total80 ? ` · total 80% ${total80.lower}–${total80.upper}` : ''}
          {margin80 ? ` · ${home} margin 80% ${margin80.lower} to ${margin80.upper}` : ''}. {joint.note}
        </small>
        {joint.illustrative_totals.length > 0 && (
          <table className="nbag__totals" data-testid="nba-illustrative-totals">
            <caption>Illustrative totals — lines no sportsbook offered, not prices</caption>
            <thead><tr><th>Line</th><th>Over</th><th>Push</th><th>Under</th></tr></thead>
            <tbody>
              {joint.illustrative_totals.map((t) => (
                <tr key={t.line}><td>{t.line}</td><td>{pct(t.over.win)}</td><td>{pct(t.over.push)}</td><td>{pct(t.under.win)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

export function NbaChannelScores({ scores }: { scores: NbaChannelScore[] }) {
  if (!scores.length) return null
  return (
    <div className="cc-table__scroll">
      <table className="nbag__scores" data-testid="nba-channel-scores">
        <caption>Scored per channel against the retained final — channels are never averaged together</caption>
        <thead>
          <tr><th>Evidence</th><th>Channel</th><th>Win prob. (home)</th><th>Log loss</th><th>Brier (2-way)</th>
            <th>Winner</th><th>Home / away abs. error</th><th>Margin / total error</th><th>CRPS total / margin</th></tr>
        </thead>
        <tbody>
          {scores.map((s) => {
            const probability = s.channel !== 'RIDGE_POINTS'
            return (
              <tr key={`${s.evidence_class}-${s.channel}`} data-testid={`nba-score-${s.channel}`}>
                <td>{s.evidence_class === NBA_HISTORICAL ? 'HISTORICAL' : 'Prospective'}</td>
                <td>{s.channel_label}</td>
                <td>{probability ? pct(s.home_win_probability) : 'n/a · points'}</td>
                <td>{probability ? num(s.log_loss) : 'n/a'}</td>
                <td>{probability ? num(s.two_way_brier) : 'n/a'}</td>
                <td>{s.winner_correct == null ? '—' : s.winner_correct ? 'Correct' : 'Missed'}</td>
                <td>{s.home_abs_error == null ? '—' : `${points(s.home_abs_error)} / ${points(s.away_abs_error)}`}</td>
                <td>{s.margin_error == null ? '—' : `${points(s.margin_error)} / ${points(s.total_error)}`}</td>
                <td>{s.crps ? `${num(s.crps.total, 2)} / ${num(s.crps.margin, 2)}` : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
