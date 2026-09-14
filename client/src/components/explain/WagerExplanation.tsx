/**
 * "Why this wager?" and "What happened?", as collapsed disclosures.
 *
 * ONE COMPONENT, BOTH SURFACES
 * ----------------------------
 * Daily Decisions shows a selection before it settles; the experiment ledger
 * and the historical research surfaces show the same selection afterwards. If
 * each built its own sentences they would drift, and the drift would always run
 * one way -- toward a tidier story once the result is known. Both render this,
 * from one server payload, so the rationale a reader sees after settlement is
 * the text that existed before it.
 *
 * WHAT THE LAYOUT IS CAREFUL ABOUT
 * -------------------------------
 *  - **Collapsed by default.** These are answers to a question, not content the
 *    reader has to scroll past.
 *  - **"Likely to win" is not "worth the price".** The framing line says which
 *    one applies, and for a value underdog it says plainly that the model did
 *    not make it the likely winner. That sentence is the whole point of the
 *    panel; it is not buried in the bullets.
 *  - **A recorded wager is not a qualifying opportunity.** The status line
 *    distinguishes them, so nothing implies money was committed when it was not.
 *  - **Identities go in a second disclosure.** Forecast ids, cutoffs and gate
 *    lists are evidence a reader occasionally needs and never wants first.
 */
import { useId, useState } from 'react'
import { ChevronDown, HelpCircle, ScrollText } from 'lucide-react'
import type { DecisionExplanation } from '../../types/explain'

function Disclosure({
  label, icon, children, testId,
}: {
  label: string
  icon: React.ReactNode
  children: React.ReactNode
  testId: string
}) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  return (
    <div className="explain">
      <button
        type="button"
        className="explain__toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        data-testid={`${testId}-toggle`}
      >
        {icon}
        <span>{label}</span>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={open ? 'explain__chevron is-open' : 'explain__chevron'}
        />
      </button>
      <div id={panelId} hidden={!open} className="explain__panel" data-testid={testId}>
        {children}
      </div>
    </div>
  )
}

function Details({ details }: { details: DecisionExplanation['why']['details'] }) {
  return (
    <Disclosure label="Evidence and identities" icon={<ScrollText size={13} aria-hidden="true" />}
                testId="explain-details">
      <dl className="explain__facts">
        {details.forecast_identity && (
          <div>
            <dt>Forecast</dt>
            <dd><code>{details.forecast_identity}</code></dd>
          </div>
        )}
        {details.engine_id && <div><dt>Engine</dt><dd>{details.engine_id}</dd></div>}
        {details.policy_version && (
          <div><dt>Policy</dt><dd>{details.policy_version}</dd></div>
        )}
        {details.tier && <div><dt>Tier</dt><dd>{details.tier}</dd></div>}
        {details.recommended_at_utc && (
          <div><dt>Decided</dt><dd>{details.recommended_at_utc}</dd></div>
        )}
        {details.quote_observed_at_utc && (
          <div><dt>Price observed</dt><dd>{details.quote_observed_at_utc}</dd></div>
        )}
        {details.forecast_evidence_cutoff_utc && (
          <div>
            <dt>Evidence cutoff</dt>
            <dd>{details.forecast_evidence_cutoff_utc}</dd>
          </div>
        )}
      </dl>
      {details.gates_passed.length > 0 && (
        <>
          <p className="explain__subhead">
            Policy checks passed ({details.gates_passed.length})
          </p>
          <ul className="explain__gates">
            {details.gates_passed.map((gate) => (
              <li key={gate}><code>{gate}</code></li>
            ))}
          </ul>
        </>
      )}
      <p className="explain__note">{details.bound_at_decision_time}</p>
    </Disclosure>
  )
}

export function WagerExplanation({
  explanation, whyLabel,
}: {
  explanation: DecisionExplanation
  /** "Why this opportunity?" where no wager exists. Defaults to the wager
   *  wording, so every existing caller is unchanged. */
  whyLabel?: string
}) {
  const { why, what_happened: what } = explanation ?? {}
  // A payload without a `why` is a malformed explanation, not a rendering
  // error. It must cost the reader this panel and nothing else -- the ledger
  // row, the game analysis or the history page around it still renders.
  if (!why || !Array.isArray(why.bullets)) return null
  // A research opportunity must never be labelled as a wager. The default
  // stays "Why this wager?" so the ledger and decision desk are untouched,
  // and the totals listing passes the opportunity wording explicitly.
  const label = whyLabel ?? 'Why this wager?'
  return (
    <div className="explain-group" data-testid="wager-explanation">
      <Disclosure
        label={label}
        icon={<HelpCircle size={13} aria-hidden="true" />}
        testId="explain-why"
      >
        <p
          className={`explain__status ${
            why.recorded_wager ? 'explain__status--recorded' : 'explain__status--qualifying'}`}
          data-testid="explain-status"
        >
          {why.status_line}
        </p>

        {/* The sentence the panel exists for. A value underdog must not be
            mistaken for a prediction that it wins. */}
        <p className="explain__framing" data-testid="explain-framing">
          {why.value_framing.headline}
        </p>

        <ul className="explain__bullets">
          {why.bullets.map((bullet) => (
            <li key={bullet.key}>{bullet.text}</li>
          ))}
        </ul>

        <dl className="explain__price" data-testid="explain-price">
          <div>
            <dt>Model</dt>
            <dd>{why.price.model_probability_pct?.toFixed(2) ?? '—'}%</dd>
          </div>
          <div>
            <dt>Price needs</dt>
            <dd>{why.price.break_even_probability_pct?.toFixed(2) ?? '—'}%</dd>
          </div>
          <div>
            {/* Kept visually separate from the break-even column: beating the
                price and disagreeing with the market are different claims. */}
            <dt>Market (no-vig)</dt>
            <dd>{why.price.no_vig_probability_pct?.toFixed(2) ?? '—'}%</dd>
          </div>
        </dl>
        <p className="explain__note">{why.value_framing.note}</p>

        <Details details={why.details} />
      </Disclosure>

      {what && (
        <Disclosure
          label="What happened?"
          icon={<ScrollText size={13} aria-hidden="true" />}
          testId="explain-what"
        >
          <ul className="explain__bullets">
            {what.bullets.map((bullet) => (
              <li key={bullet.key}>{bullet.text}</li>
            ))}
          </ul>
          <p className="explain__note" data-testid="explain-uncertainty">
            {what.uncertainty.text}
          </p>
          <p className="explain__note explain__note--guard">
            {what.interpretation_guard}
          </p>
        </Disclosure>
      )}
    </div>
  )
}
