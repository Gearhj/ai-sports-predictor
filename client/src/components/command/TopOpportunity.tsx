/**
 * Daily Decisions · the best available action for a game, and the ranked
 * outcome rows beneath it. Selection logic lives in services/topPlay.
 */
import { memo } from 'react'
import { etTime, relative, type CoverageGame, type DecisionItem, type SportCode } from '../../services/commandCenter'
import { myCard, pickKey, useMyCard, type PickOrigin } from '../../services/myCard'
import { PARLAY_UNAVAILABLE, american, legLabel, pct, type Channel, type GameAssessment } from '../../services/probability'
import {
  MARKET_ORDER, PRICE_BLOCKED_TEXT, TOP_LABEL, expectedProfitText, expectedReturnText, marketLabel, money2,
  payoutText, profitIfWinText, selectionText, shortBook, signedCents, teamShort,
  type Outcome, type TopPlay,
} from '../../services/topPlay'
import { pickFrom } from './MarketsAndCard'

export const TOP_ORIENTATION = 'Start with each game’s #1 opportunity. Open the other markets to compare payouts and model value.'
export const RESEARCH_NOTE = 'Preliminary research comparison · not an official AISP decision'
export const DIFFERS_NOTE = 'The official experiment currently governs moneyline decisions. The best cross-market value is the strongest research comparison across all priced markets.'

const pts = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} pts`)

function AddButton({ outcome, game, origin, channel, primary = false }: {
  outcome: Outcome; game: CoverageGame | null; origin: PickOrigin; channel: Channel | null; primary?: boolean
}) {
  const picks = useMyCard()
  const best = outcome.best
  const pick = best && game && best.current ? pickFrom(game, best.quote, outcome.side) : null
  if (!pick) {
    return <button type="button" className="tp-add" disabled>{best && !best.current ? 'Not current' : 'No current price'}</button>
  }
  const added = picks.some((p) => pickKey(p) === pickKey(pick))
  const masses = outcome.masses
  const add = () => {
    myCard.add({
      ...pick,
      origin,
      expectation: outcome.expectedProfit != null && outcome.expectedReturn != null && masses ? {
        expected_profit: outcome.expectedProfit, expected_return: outcome.expectedReturn,
        win: masses.win, loss: masses.loss, push: masses.push,
        model_id: channel?.binding?.modelId ?? null, forecast_id: channel?.binding?.forecastId ?? null,
      } : null,
    }, { current: true })
  }
  return (
    <button type="button" className={`tp-add${primary ? ' is-primary' : ''}`} disabled={added} onClick={add}
      aria-label={added ? `${outcome.label} ${american(best!.american)} is on My Card` : `Add ${outcome.label} ${american(best!.american)} at ${best!.sportsbook} to My Card`}>
      {added ? 'On My Card' : 'Add to My Card'}
    </button>
  )
}

/** The evidence behind one outcome: probabilities, prices, the calculation, times and identity. */
export function WhyDetails({ outcome, channel, item, summary = 'Why?' }: {
  outcome: Outcome; channel: Channel | null; item?: DecisionItem | null; summary?: string
}) {
  const b = outcome.best
  const m = outcome.masses
  const side = b?.side
  return (
    <details className="tp-why">
      <summary>{summary}</summary>
      <dl>
        {m && <div><dt>Model</dt><dd>win {pct(m.win)} · loss {pct(m.loss)} · push {pct(m.push)}{m.conditional != null && m.push > 0 ? ` · win if not a push ${pct(m.conditional)}` : ''}</dd></div>}
        {b && <div><dt>Sportsbook implied</dt><dd>{pct(1 / b.decimal)} at {american(b.american)} ({b.decimal.toFixed(3)})</dd></div>}
        <div><dt>No-vig</dt><dd>{pct(side?.no_vig)}</dd></div>
        <div><dt>Fair price</dt><dd>{american(m?.fair_american)}</dd></div>
        <div><dt>Raw edge</dt><dd>{pts(side?.edge)}</dd></div>
        {item?.adjusted?.edge != null && <div><dt>Policy-adjusted edge</dt><dd>{pts(item.adjusted.edge)} (official decision)</dd></div>}
        {m && b && outcome.expectedProfit != null && (
          <div><dt>Expected profit</dt><dd>{pct(m.win)} × ({b.decimal.toFixed(3)} − 1) − {pct(m.loss)} = {signedCents(outcome.expectedProfit)} per $1 · expected return {money2(outcome.expectedReturn!)}</dd></div>
        )}
        {outcome.blocked && <div><dt>Expected profit</dt><dd>{PRICE_BLOCKED_TEXT[outcome.blocked]}</dd></div>}
        {b && <div><dt>Quote</dt><dd>{b.sportsbook} · {b.quote.provider ?? 'provider'}{b.quote.classification === 'RESEARCH' ? ' (research-classified)' : ''} · observed {etTime(b.quote.observed_at, true)} ({b.current ? 'current' : 'stored reference'})</dd></div>}
        {channel?.forecast_created_at && <div><dt>Forecast</dt><dd>created {etTime(channel.forecast_created_at, true)} ({relative(channel.forecast_created_at)})</dd></div>}
        {channel?.binding && <div><dt>Model / population</dt><dd className="tp-ident">{channel.binding.modelId} · forecast {channel.binding.forecastId} · {channel.binding.drawCount.toLocaleString()} paired simulated scores</dd></div>}
        {outcome.books.length > 1 && (
          <div><dt>Every book</dt><dd><ul className="tp-books">{outcome.books.map((x) => (
            <li key={`${x.sportsbook}|${x.quote.observed_at}`} className={x.current ? '' : 'is-stale'}>{x.sportsbook} {american(x.american)}{x.current ? '' : ' · stored'}</li>
          ))}</ul></dd></div>
        )}
        <div><dt>Limitations</dt><dd>
          Probabilities come from one simulated population{m ? ` (Monte Carlo error about ±${(m.mcse * 100).toFixed(1)} pts)` : ''}.
          An expectation is an average over many games, not a promise for this one. Virtual research only — no real money.
        </dd></div>
      </dl>
    </details>
  )
}

function OutcomeSummary({ outcome, big = true }: { outcome: Outcome; big?: boolean }) {
  const b = outcome.best
  if (!b) return <p className="tp-dim">No sportsbook price stored.</p>
  return (
    <div className={`tp-figures${big ? '' : ' is-compact'}`}>
      <p className="tp-pay"><b>{payoutText(b.decimal)}</b><span>{profitIfWinText(b.decimal)}</span></p>
      {outcome.expectedProfit != null
        ? (
          <p className="tp-ev">
            <b className={outcome.expectedProfit >= 0 ? 'is-up' : 'is-down'}>{expectedProfitText(outcome.expectedProfit)}</b>
            <span>{expectedReturnText(outcome.expectedReturn!)}</span>
          </p>
        )
        : <p className="tp-dim">{PRICE_BLOCKED_TEXT[outcome.blocked ?? 'PENDING']}</p>}
    </div>
  )
}

function Headline({ outcome, sport }: { outcome: Outcome; sport: SportCode }) {
  const b = outcome.best
  return (
    <div className="tp-headline">
      <h4>{outcome.label}</h4>
      <p>{marketLabel(outcome.market, sport)}{b ? ` · ${american(b.american)} at ${b.sportsbook}` : ''}
        {b && !b.current ? <em className="tp-chip is-stale">Stored reference</em> : null}</p>
    </div>
  )
}

export const TopOpportunity = memo(function TopOpportunity({ play, game, item, sport, status, channel }: {
  play: TopPlay; game: CoverageGame | null; item: DecisionItem | null; sport: SportCode
  status: string; channel: Channel | null
}) {
  const { state, official, bestValue, differs } = play
  const main = state === 'OFFICIAL' ? official : state === 'MODEL_VALUE' ? bestValue : null
  const stake = item?.position?.stake
  return (
    <section className={`tp tp--${state.toLowerCase()}`} aria-label={`Top opportunity: ${TOP_LABEL[state]}`}>
      <div className="tp-top">
        <span className="tp-label">{TOP_LABEL[state]}</span>
        {state === 'OFFICIAL' && <span className="tp-status">{status}</span>}
      </div>
      {state === 'MODEL_VALUE' && <p className="tp-note">{RESEARCH_NOTE}</p>}

      {state === 'OFFICIAL' && !official && item && (
        // The decision's outcome has no stored quote today: show what the decision recorded.
        <div className="tp-headline">
          <h4>{selectionText(game, 'MONEYLINE', item.selection?.split(/\s+/)[0] === game?.home?.abbreviation ? 'HOME' : 'AWAY', null)}</h4>
          <p>Moneyline · recorded {american(item.american)} at {shortBook(item.sportsbook)}</p>
          <p className="tp-dim">{PRICE_BLOCKED_TEXT.STALE}</p>
        </div>
      )}

      {main && (
        <div className="tp-body">
          <Headline outcome={main} sport={sport} />
          <OutcomeSummary outcome={main} />
          {state === 'OFFICIAL' && item?.american != null && main.best && (item.american !== main.best.american || !shortBook(item.sportsbook).startsWith(main.best.sportsbook)) && (
            <p className="tp-dim">The decision recorded {american(item.american)} at {shortBook(item.sportsbook)}; the best current price is shown.</p>
          )}
          {stake != null && <p className="tp-stake">Virtual stake {money2(stake)} · position {item?.position?.status.toLowerCase()}{item?.position?.result ? ` · ${item.position.result.toLowerCase()}` : ''}</p>}
          <div className="tp-actions">
            <AddButton outcome={main} game={game} channel={channel} origin={state === 'OFFICIAL' ? 'OFFICIAL_PICK' : 'MODEL_VALUE'} primary />
            <WhyDetails outcome={main} channel={channel} item={state === 'OFFICIAL' ? item : null} summary="Why this?" />
          </div>
        </div>
      )}

      {state === 'OFFICIAL' && differs && bestValue && (
        <div className="tp-alt">
          <p className="tp-alt__pair">
            <span>Official AISP pick: <b>{official?.label ?? item?.selection}{official?.best ? ` ${american(official.best.american)}` : ''}</b></span>
            <span>Best cross-market model value: <b>{bestValue.label} {american(bestValue.best!.american)}</b> at {bestValue.best!.sportsbook} · <b className="is-up">{signedCents(bestValue.expectedProfit!)}</b> per $1</span>
          </p>
          <p className="tp-dim">{DIFFERS_NOTE}</p>
          <div className="tp-actions">
            <AddButton outcome={bestValue} game={game} channel={channel} origin="MODEL_VALUE" />
            <WhyDetails outcome={bestValue} channel={channel} summary="Why this value?" />
          </div>
        </div>
      )}

      {state === 'NO_POSITIVE' && (
        <p className="tp-msg">No outcome with a current price has a positive AISP expected profit, so there is no best bet here. Every payout is listed under the markets below.</p>
      )}
      {state === 'PENDING_FORECAST' && (
        <p className="tp-msg">{PRICE_BLOCKED_TEXT.PENDING}.{play.outcomes.some((o) => o.best?.current) ? ' Current prices and $1 payouts are listed under the markets below.' : ''}</p>
      )}
      {state === 'PRICE_NEEDED' && (
        <p className="tp-msg">{PRICE_BLOCKED_TEXT.STALE}. AISP ranks a game’s markets only from a current sportsbook price.</p>
      )}
    </section>
  )
})

function OutcomeRow({ outcome, game, channel }: { outcome: Outcome; game: CoverageGame | null; channel: Channel | null }) {
  const b = outcome.best
  return (
    <li className={`tp-row${b?.current ? '' : ' is-stale'}`}>
      <span className="tp-row__rank" aria-label={outcome.rank ? `Rank ${outcome.rank}` : 'Not ranked'}>{outcome.rank ? `#${outcome.rank}` : '—'}</span>
      <span className="tp-row__sel"><b>{outcome.label}</b>
        <small>{b ? `${american(b.american)} at ${b.sportsbook}${b.current ? '' : ' · stored reference'}` : 'not quoted'}</small></span>
      <span className="tp-row__pay">{b ? <>$1 → <b>{money2(b.decimal)}</b> if it wins</> : '—'}</span>
      <span className="tp-row__ev">{outcome.expectedProfit != null
        ? <b className={outcome.expectedProfit >= 0 ? 'is-up' : 'is-down'}>{signedCents(outcome.expectedProfit)} per $1</b>
        : <small>{PRICE_BLOCKED_TEXT[outcome.blocked ?? 'PENDING']}</small>}</span>
      <span className="tp-row__add"><AddButton outcome={outcome} game={game} channel={channel} origin="PERSONAL" /></span>
      <span className="tp-row__why"><WhyDetails outcome={outcome} channel={channel} /></span>
    </li>
  )
}

export function MarketSections({ play, assessment, game, sport }: {
  play: TopPlay; assessment: GameAssessment | null; game: CoverageGame | null; sport: SportCode
}) {
  const channel = assessment?.binding ?? null
  const ranked = play.outcomes.filter((o) => o.rank != null).length
  const home = teamShort(game, 'HOME')
  const away = teamShort(game, 'AWAY')
  return (
    <details className="tp-markets">
      <summary>Compare all markets · {play.outcomes.length} outcomes{ranked ? ` · ${ranked} ranked by expected profit` : ''}</summary>
      {MARKET_ORDER.map((market) => {
        const rows = play.outcomes.filter((o) => o.market === market)
        return (
          <section key={market} className="tp-market" aria-label={marketLabel(market, sport)}>
            <h5>{marketLabel(market, sport)}</h5>
            {rows.length === 0
              ? <p className="tp-dim">No {marketLabel(market, sport).toLowerCase()} quote stored.</p>
              : <ol className="tp-rows">{rows.map((o) => <OutcomeRow key={o.key} outcome={o} game={game} channel={channel} />)}</ol>}
          </section>
        )
      })}
      {(assessment?.parlay_lab ?? []).length > 0 && (
        <section className="tp-market" aria-label="Same-game combinations">
          <h5>Same-game combinations</h5>
          <ul className="tp-parlays">
            {assessment!.parlay_lab.map((p) => (
              <li key={JSON.stringify(p.legs)}>
                <b>{p.legs.map((l) => legLabel(l, home, away)).join(' + ')}</b>
                <span>AISP joint win {pct(p.probabilities.win)} · {p.parlay.quote_status === 'RECORDED_SPORTSBOOK_PARLAY_PRICE'
                  ? p.parlay.message
                  : 'Model probability available · sportsbook parlay price unavailable'}</span>
              </li>
            ))}
          </ul>
          <p className="tp-dim">{PARLAY_UNAVAILABLE} Same-game combinations are not ranked without a sportsbook parlay price.</p>
        </section>
      )}
    </details>
  )
}
