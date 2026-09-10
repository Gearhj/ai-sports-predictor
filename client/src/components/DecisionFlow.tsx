/**
 * Today's governed decisions, shown as their proportions rather than as four
 * separate numbers.
 *
 * Four count tiles tell you the counts. They do not tell you the SHAPE of the
 * day, which is the thing a reader is actually after at a glance: is this a
 * slate that is still developing, one that was evaluated and passed, or one
 * that is already over? A proportional bar answers that before any digit is
 * read, and the digits are still there for anyone who wants them.
 *
 * Deliberate restraint:
 *   - A zero-count group is omitted from the bar but still listed in the key,
 *     so "nothing qualified today" stays visible instead of silently vanishing.
 *     A zero-position day is a valid outcome here and must never be hidden.
 *   - Segment width is the raw proportion. No minimum width is enforced, because
 *     inflating a 1-of-125 sliver to a readable size would misstate the day.
 *   - Colours are the group colours already used by the decision board, so the
 *     bar and the sections below it agree.
 */
import type { DecisionGroup } from '../services/dailyDecision'

export type DecisionFlowGroup = {
  group: DecisionGroup
  label: string
  count: number
  href?: string
}

export function DecisionFlow({ groups, total }: { groups: DecisionFlowGroup[]; total: number }) {
  const present = groups.filter((entry) => entry.count > 0)
  const denominator = total > 0 ? total : present.reduce((sum, entry) => sum + entry.count, 0)

  return (
    // Labelled as a breakdown of DECISIONS, never of games: a single game can
    // carry more than one governed policy, so these four counts do not
    // partition the slate and must not be presented as though they do.
    <div
      className="decision-flow"
      role="group"
      aria-label={`Breakdown of ${total} governed ${total === 1 ? 'decision' : 'decisions'}`}
    >
      {denominator > 0 && (
        <div
          className="decision-flow__bar"
          role="img"
          aria-label={
            `${denominator} governed ${denominator === 1 ? 'decision' : 'decisions'}: ` +
            present.map((entry) => `${entry.count} ${entry.label}`).join(', ')
          }
        >
          {present.map((entry) => (
            <span
              key={entry.group}
              className={`decision-flow__segment decision-flow__segment--${entry.group.toLowerCase()}`}
              style={{ flexGrow: entry.count }}
              title={`${entry.label}: ${entry.count} of ${denominator}`}
            />
          ))}
        </div>
      )}
      <ul className="decision-flow__key">
        {groups.map((entry) => (
          <li
            key={entry.group}
            className={`decision-flow__item decision-flow__item--${entry.group.toLowerCase()}${
              entry.count === 0 ? ' decision-flow__item--empty' : ''
            }`}
          >
            <span className="decision-flow__swatch" aria-hidden="true" />
            <span className="decision-flow__count">{entry.count}</span>
            <span className="decision-flow__label">{entry.label}</span>
            <span className="decision-flow__share">
              {denominator > 0 ? `${Math.round((entry.count / denominator) * 100)}%` : '—'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
