/**
 * Parlay Lab — same-game research only.
 *
 * Every leg of a combination is evaluated by the server against ONE verified
 * paired score population, and the joint result is the per-draw intersection
 * (PAIRED_DRAW_INTERSECTION). This component displays server values only; it
 * never multiplies probabilities.
 *
 * No sportsbook parlay price is stored, so no implied probability, edge or
 * expected return is shown -- the server would supply them only against an
 * actual recorded combined quote. Nothing here creates a decision, position or
 * ledger entry.
 *
 * Cross-game combinations are disabled: no category passed the frozen
 * calibration study, so the panel explains why and lists nothing beneath.
 */
import { useEffect, useMemo, useState } from 'react'
import { SPORT_NAMES, etTime, type CoverageGame } from '../../services/commandCenter'
import {
  AUTHORITY_TEXT, CROSS_GAME_MESSAGE, PARLAY_UNAVAILABLE, american, evaluateSameGame, legLabel, pct,
  type Channel, type DailyAssessments, type Leg, type Masses, type ParlayAssessment, type PopulationBinding,
} from '../../services/probability'

export interface LabGame {
  gameId: string
  label: string
  away: string
  home: string
  startsAt: string | null
  channel: Channel & { binding: PopulationBinding }
  examples: DailyAssessments['games'][number]['parlay_lab']
}

export function labGames(data: DailyAssessments | null, coverage: CoverageGame[], now = Date.now()): LabGame[] {
  const byId = new Map(coverage.map((g) => [g.game_id, g]))
  const out: LabGame[] = []
  for (const g of data?.games ?? []) {
    if (g.binding.status !== 'SUPPORTED' || !g.binding.binding) continue
    const game = byId.get(g.game_id)
    out.push({
      gameId: g.game_id, label: game?.matchup ?? g.game_id, away: game?.away?.abbreviation ?? 'Away',
      home: game?.home?.abbreviation ?? 'Home', startsAt: game?.starts_at ?? null,
      channel: g.binding as LabGame['channel'], examples: g.parlay_lab,
    })
  }
  for (const r of data?.research ?? []) {
    const paired = r.channels.find((c) => c.channel === 'PAIRED_POPULATION' && c.status === 'SUPPORTED' && c.binding)
    if (!paired) continue
    out.push({
      gameId: r.game_id, label: r.matchup ?? r.game_id, away: r.away?.name ?? r.away?.abbreviation ?? 'Away',
      home: r.home?.name ?? r.home?.abbreviation ?? 'Home', startsAt: r.starts_at,
      channel: paired as LabGame['channel'], examples: [],
    })
  }
  // Games still to start come first, soonest first; started games follow.
  const started = (g: LabGame) => (g.startsAt ? Date.parse(g.startsAt) <= now : false)
  return out.sort((a, b) => Number(started(a)) - Number(started(b))
    || (started(a) ? (b.startsAt ?? '').localeCompare(a.startsAt ?? '') : (a.startsAt ?? '').localeCompare(b.startsAt ?? '')))
}

interface Shown {
  legs: Leg[]
  probabilities: Masses
  effectiveDrawCount: number
  mcse: number | null
  fairAmerican: number | null
  population: PopulationBinding
  authority: string
  parlay: ParlayAssessment | null
  method: string
}

export function ParlayResult({ shown, game }: { shown: Shown; game: LabGame }) {
  const p = shown.probabilities
  return (
    <div className="pl-result" aria-label="Same-game result">
      <ol className="pl-legs">{shown.legs.map((leg, i) => <li key={i}>{legLabel(leg, game.home, game.away)}</li>)}</ol>
      <dl className="pl-masses">
        <div><dt>Model joint win</dt><dd className="pl-win">{pct(p.win)}</dd></div>
        <div><dt>Loss</dt><dd>{pct(p.loss)}</dd></div>
        <div><dt>Push</dt><dd>{pct(p.push)}</dd></div>
        <div><dt>Void</dt><dd>{pct(p.void)}</dd></div>
        <div><dt>Paired draws</dt><dd>{shown.effectiveDrawCount.toLocaleString()}</dd></div>
        <div><dt>Monte Carlo error</dt><dd>{shown.mcse != null ? `±${(shown.mcse * 100).toFixed(2)} pts` : '—'}</dd></div>
        <div><dt>Model fair price</dt><dd>{american(shown.fairAmerican)}</dd></div>
      </dl>
      <p className="pl-meta">
        <b>PAIRED_DRAW_INTERSECTION</b> · {AUTHORITY_TEXT[shown.authority] ?? shown.authority} ·
        forecast <code title={shown.population.forecastId}>{shown.population.forecastId.slice(0, 32)}…</code> ·
        population <code title={shown.population.populationSha256}>{shown.population.populationSha256.slice(0, 12)}</code> ·
        {' '}{shown.population.modelId}
      </p>
      {shown.legs.length > 1 && (
        <p className="pl-quote" role="note">
          {shown.parlay && shown.parlay.quote_status === 'RECORDED_SPORTSBOOK_PARLAY_PRICE'
            ? shown.parlay.message
            : PARLAY_UNAVAILABLE}
        </p>
      )}
    </div>
  )
}

const MARKET_OPTIONS: { value: Leg['market']; label: string }[] = [
  { value: 'MONEYLINE', label: 'Moneyline' }, { value: 'SPREAD', label: 'Spread' }, { value: 'TOTAL', label: 'Total' },
]

export function ParlayLab({ data, coverage }: { data: DailyAssessments | null; coverage: CoverageGame[] }) {
  const games = useMemo(() => labGames(data, coverage), [data, coverage])
  const [gameId, setGameId] = useState<string>('')
  const game = games.find((g) => g.gameId === gameId) ?? games[0] ?? null
  const [legs, setLegs] = useState<Leg[]>([])
  const [draft, setDraft] = useState<{ market: Leg['market']; selection: string; line: string }>({ market: 'MONEYLINE', selection: 'HOME', line: '' })
  const [result, setResult] = useState<Shown | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { setLegs([]); setResult(null); setError(null) }, [game?.gameId])

  const sides = draft.market === 'TOTAL' ? ['OVER', 'UNDER'] : ['HOME', 'AWAY']
  const needsLine = draft.market !== 'MONEYLINE'
  const lineValue = Number(draft.line)
  const canAdd = !needsLine || (draft.line.trim() !== '' && Number.isFinite(lineValue))

  function addLeg() {
    if (!game || !canAdd) return
    const selection = (sides.includes(draft.selection) ? draft.selection : sides[0]) as Leg['selection']
    const leg: Leg = draft.market === 'MONEYLINE'
      ? { market: 'MONEYLINE', selection, tieSettlement: (game.channel.moneyline_tie_settlement ?? 'PUSH') as Leg['tieSettlement'] }
      : { market: draft.market, selection, line: lineValue }
    setLegs((old) => [...old, leg])
    setResult(null)
  }

  async function evaluate() {
    if (!game || legs.length === 0) return
    setBusy(true); setError(null)
    try {
      const r = await evaluateSameGame(game.channel.binding, legs)
      setResult({
        legs: r.contract.legs, probabilities: r.contract.probabilities, effectiveDrawCount: r.contract.effectiveDrawCount,
        mcse: r.contract.winProbabilityMonteCarloStandardError, fairAmerican: r.fair_american,
        population: r.contract.population, authority: r.authority, parlay: r.parlay ?? null, method: r.contract.method,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Evaluation failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="pl-lab" aria-label="Parlay Lab">
      <header className="pl-lab__head">
        <h2 className="cc-section-title">Parlay Lab <span>same-game research</span></h2>
        <p>Research estimates from one verified simulation population per game. Separate from qualified single-market decisions; no parlay is ever recorded as a decision or position.</p>
      </header>
      <div className="pl-lab__grid">
        <div className="pl-panel">
          <h3>Same-game paired draws</h3>
          {games.length === 0 ? (
            <p className="cc-muted">No game on this slate has a verified paired simulation population.</p>
          ) : (
            <>
              <label className="pl-field">Game
                <select value={game?.gameId ?? ''} onChange={(e) => setGameId(e.target.value)}>
                  {games.map((g) => (
                    <option key={g.gameId} value={g.gameId}>
                      {SPORT_NAMES[g.channel.binding.sport]} · {g.label}{g.startsAt ? ` · ${etTime(g.startsAt, true)}` : ''}
                    </option>
                  ))}
                </select>
              </label>
              {game && (
                <p className="pl-meta">{game.channel.authority_label} · {game.channel.binding.drawCount.toLocaleString()} paired final scores · moneyline ties settle as {(game.channel.moneyline_tie_settlement ?? 'PUSH').toLowerCase()}</p>
              )}
              {game && game.examples.length > 0 && (
                <div className="pl-examples">
                  <h4>From today’s quoted lines</h4>
                  {game.examples.map((ex, i) => (
                    <ParlayResult key={i} game={game} shown={{
                      legs: ex.legs, probabilities: ex.probabilities, effectiveDrawCount: ex.effective_draw_count,
                      mcse: ex.mcse, fairAmerican: ex.fair_american, population: ex.population, authority: ex.authority,
                      parlay: ex.parlay, method: ex.method,
                    }} />
                  ))}
                </div>
              )}
              {game && (
                <div className="pl-builder">
                  <h4>Build a same-game combination</h4>
                  <div className="pl-builder__row">
                    <label className="pl-field">Market
                      <select value={draft.market} onChange={(e) => setDraft({ market: e.target.value as Leg['market'], selection: e.target.value === 'TOTAL' ? 'OVER' : 'HOME', line: '' })}>
                        {MARKET_OPTIONS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                      </select>
                    </label>
                    <label className="pl-field">Side
                      <select value={draft.selection} onChange={(e) => setDraft({ ...draft, selection: e.target.value })}>
                        {sides.map((s) => <option key={s} value={s}>{s === 'HOME' ? game.home : s === 'AWAY' ? game.away : s === 'OVER' ? 'Over' : 'Under'}</option>)}
                      </select>
                    </label>
                    {needsLine && (
                      <label className="pl-field">{draft.market === 'SPREAD' ? 'Line for this side' : 'Total line'}
                        <input inputMode="decimal" value={draft.line} placeholder={draft.market === 'SPREAD' ? 'e.g. -1.5' : 'e.g. 8.5'}
                          onChange={(e) => setDraft({ ...draft, line: e.target.value })} />
                      </label>
                    )}
                    <button type="button" onClick={addLeg} disabled={!canAdd}>Add leg</button>
                  </div>
                  {legs.length > 0 && (
                    <ol className="pl-legs pl-legs--edit">
                      {legs.map((leg, i) => (
                        <li key={i}>{legLabel(leg, game.home, game.away)}
                          <button type="button" aria-label={`Remove ${legLabel(leg, game.home, game.away)}`}
                            onClick={() => { setLegs(legs.filter((_, j) => j !== i)); setResult(null) }}>×</button>
                        </li>
                      ))}
                    </ol>
                  )}
                  <button type="button" className="pl-go" onClick={evaluate} disabled={busy || legs.length === 0}>
                    {busy ? 'Evaluating…' : legs.length > 1 ? 'Evaluate same-game combination' : 'Evaluate leg'}
                  </button>
                  {error && <p className="pl-error" role="alert">{error}</p>}
                  {result && <ParlayResult shown={result} game={game} />}
                </div>
              )}
            </>
          )}
        </div>
        <div className="pl-panel pl-panel--disabled" aria-disabled="true" aria-label="Cross-game combinations">
          <h3>Cross-game combinations</h3>
          <p className="pl-disabled">{CROSS_GAME_MESSAGE}</p>
          <p className="cc-muted">Unavailable · CROSS_GAME_CALIBRATION_NOT_VALIDATED</p>
        </div>
      </div>
    </section>
  )
}
