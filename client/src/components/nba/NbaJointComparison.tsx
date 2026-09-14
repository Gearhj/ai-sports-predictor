/**
 * Two research arms of one NBA joint simulation, side by side.
 *
 * WHAT IS NEW HERE
 * ----------------
 * A distribution. The point/Elo forecast above declares `drawCount = 0` and
 * every interval and Over/Under unavailable; these arms carry ten thousand
 * retained draws each. Showing them in the same place as that forecast, and
 * labelling which is which, is the only way a reader can tell that the
 * unavailability above was a real limitation rather than a rendering failure.
 *
 * WHAT THE TOTALS ARE NOT
 * -----------------------
 * Prices. The producer labels every line
 * ILLUSTRATIVE_NOT_RECORDED_MARKET, and that label is rendered rather than
 * dropped: it is a question the simulation can answer, not a market AISP was
 * offered. NBA has no account, no market and no wagering authority, and
 * importing these arms created none.
 */
import type { NbaJointArm, NbaJointComparison as Comparison } from '../../types/nbaJoint'

function pct(value: number | null | undefined): string {
  // The single multiplication by 100 on this path.
  return value == null ? '—' : `${(value * 100).toFixed(2)}%`
}

function points(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

function Arm({ arm }: { arm: NbaJointArm }) {
  const interval = arm.intervals?.margin?.find((band) => band.level === 0.8)
    ?? arm.intervals?.total?.find((band) => band.level === 0.8)
  return (
    <section className="nbajoint__arm" data-testid={`nba-joint-arm-${arm.arm}`}>
      <header>
        <h4>{arm.label}</h4>
        <small className="bx-dim">{arm.configuration_identity}</small>
      </header>

      <dl className="nbajoint__figures">
        <div>
          <dt>Winner</dt>
          <dd>
            BOS <b className="bx-num">{pct(arm.winner_probabilities.away)}</b>
            {' · '}
            DET <b className="bx-num">{pct(arm.winner_probabilities.home)}</b>
          </dd>
        </div>
        <div>
          <dt>Expected score</dt>
          <dd>
            BOS <b className="bx-num">{points(arm.expected_scores.away)}</b>
            {' · '}
            DET <b className="bx-num">{points(arm.expected_scores.home)}</b>
          </dd>
        </div>
        <div>
          <dt>Expected total</dt>
          <dd><b className="bx-num">{points(arm.expected_scores.total)}</b></dd>
        </div>
        <div>
          <dt>Retained draws</dt>
          <dd><b className="bx-num">{arm.draw_count.toLocaleString()}</b></dd>
        </div>
        {interval && (
          <div>
            <dt>80% interval</dt>
            <dd><b className="bx-num">{interval.lower}–{interval.upper}</b>
              <small className="bx-dim"> {interval.units}</small></dd>
          </div>
        )}
      </dl>

      {arm.illustrative_totals.length > 0 && (
        <div className="nbajoint__totals">
          <span className="nbajoint__tag">Illustrative only — not a recorded market</span>
          <table>
            <thead>
              <tr><th>Line</th><th>Over</th><th>Push</th><th>Under</th></tr>
            </thead>
            <tbody>
              {arm.illustrative_totals.map((total) => (
                <tr key={String(total.line)}>
                  <td className="bx-num">{total.line}</td>
                  <td className="bx-num">{pct(total.over?.win)}</td>
                  <td className="bx-num">{pct(total.over?.push)}</td>
                  <td className="bx-num">{pct(total.under?.win)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function NbaJointComparison({ comparison }: { comparison: Comparison | null }) {
  if (!comparison || comparison.arms.length === 0) return null
  return (
    <div className="nbajoint" data-testid="nba-joint-comparison">
      <header className="nbajoint__head">
        <span className="nbares__label">Joint simulation — research comparison</span>
        <h3>Two arms, one game</h3>
      </header>

      {/* Said plainly rather than left for a reader to discover by squinting:
          the two arms agree on who is favoured and differ only in the shape of
          the margin. Two near-identical columns invite the opposite reading. */}
      {comparison.comparison_note && (
        <p className="nbajoint__note" role="note" data-testid="nba-joint-note">
          {comparison.comparison_note}
        </p>
      )}

      <div className="nbajoint__arms">
        {comparison.arms.map((arm) => <Arm key={arm.forecast_id} arm={arm} />)}
      </div>

      <p className="nbares__unavailable" data-testid="nba-joint-authority">
        {comparison.authority.note}
      </p>
    </div>
  )
}
