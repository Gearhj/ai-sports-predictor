import { useEffect, useMemo, useState } from 'react'
import { FlaskConical, Lock, ShieldAlert } from 'lucide-react'
import { MetricCard, PageHeader, Panel, TableScroll } from '../components/ui'
import { endpoints } from '../services/api'
import type { ShadowBoardData, ShadowDecisionView, ShadowEngineView, ShadowRecommendation } from '../types/domain'

const RECOMMENDATION_CLASS: Record<ShadowRecommendation, string> = {
  NO_BET: 'job-status job-status--failed',
  WATCH: 'job-status job-status--running',
  QUALIFIED_BET: 'job-status job-status--success',
}

const pct = (value: number | null): string => (value == null ? '—' : `${(value * 100).toFixed(1)}%`)
const edge = (value: number | null): string => (value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`)
const money = (value: number): string => `$${value.toFixed(2)}`

function todayIso(): string {
  // Default to the historical research slate carried by the imported bundle so
  // the transparency board is populated on first view.
  return '2024-09-16'
}

export function ShadowBoardPage() {
  const [slateDate, setSlateDate] = useState<string>(todayIso())
  const [data, setData] = useState<ShadowBoardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    endpoints.shadowBoard(slateDate, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setData(result) })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setData(null)
        setError(reason instanceof Error ? reason.message : 'Shadow board unavailable')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [slateDate])

  const games = data?.games ?? []

  return (
    <div className="page shadow-board-page">
      <PageHeader
        eyebrow="RESEARCH · MODEL STUDIO SHADOW BOARD"
        title="Shadow Betting Board"
        description="Imported Direct Gold and E10 research forecasts joined with governed odds. Probabilities and edges are research evidence only."
        actions={<span className="admin-chip"><FlaskConical size={14} /> RESEARCH SHADOW</span>}
      />

      <div className="research-note" role="note" style={{ marginBottom: '1rem' }}>
        <ShieldAlert size={16} />
        <span>
          <strong>Research shadow — not executable.</strong><br />
          <small>No real wager can be placed. Virtual stakes are simulation-only under quarter-Kelly caps. No quote is ever fabricated: a game without a governed quote is a real NO_BET.</small>
        </span>
      </div>

      <div className="section-heading">
        <div><p className="eyebrow">SLATE</p><h2>Daily shadow slate</h2></div>
        <label className="inline-field">
          <span>Slate date</span>
          <input type="date" value={slateDate} onChange={(event) => setSlateDate(event.target.value)} />
        </label>
      </div>

      {loading && <Panel title="Loading" eyebrow="SHADOW BOARD"><p className="muted">Loading the research-shadow slate…</p></Panel>}
      {!loading && error && (
        <Panel title="Shadow board unavailable" eyebrow="SHADOW BOARD">
          <p className="muted">{error}</p>
        </Panel>
      )}
      {!loading && !error && games.length === 0 && (
        <Panel title="No imported shadow forecasts for this slate" eyebrow="SHADOW BOARD">
          <p className="muted">
            An administrator imports immutable Model Studio bundles and evaluates the board.
            Pick a slate that has been imported, or ask an administrator to import one.
          </p>
        </Panel>
      )}

      {games.map((game) => (
        <Panel
          key={game.event_id}
          title={`${game.away_team_id} @ ${game.home_team_id}`}
          eyebrow={`EVENT ${game.event_date}`}
          className="section-panel"
          action={game.model_disagreement != null
            ? <span className="info-label">Model disagreement {pct(game.model_disagreement)}</span>
            : undefined}
        >
          <div className="analysis-grid">
            {game.engines.map((engine) => <EngineColumn key={engine.forecast_id} engine={engine} />)}
          </div>
        </Panel>
      ))}
    </div>
  )
}

function EngineColumn({ engine }: { engine: ShadowEngineView }) {
  const staked = useMemo(
    () => engine.decisions.reduce((sum, decision) => sum + decision.virtual_stake, 0),
    [engine.decisions],
  )
  return (
    <div className="engine-column">
      <div className="section-heading">
        <div><p className="eyebrow">ENGINE</p><h3>{engine.engine_id}</h3></div>
        <span className={engine.is_ready ? 'role-chip' : 'admin-chip'}>
          {engine.is_ready ? 'READY' : <><Lock size={12} /> NOT READY</>}
        </span>
      </div>
      <section className="metric-grid metric-grid--three">
        <MetricCard label="Home win" value={pct(engine.home_win_probability)} detail={`Exp ${engine.expected_home_score.toFixed(2)}`} />
        <MetricCard label="Away win" value={pct(engine.away_win_probability)} detail={`Exp ${engine.expected_away_score.toFixed(2)}`} />
        <MetricCard label="F5 home win" value={pct(engine.first_five_home_win_probability)} detail={`Readiness ${engine.readiness_score}/${engine.readiness_threshold}`} />
      </section>
      <TableScroll>
        <table className="data-table">
          <thead>
            <tr>
              <th>Market</th><th>Selection</th><th>Model</th><th>Book</th><th>Odds</th>
              <th>No-vig</th><th>Edge</th><th>EV</th><th>Rec</th><th>Virtual stake</th>
            </tr>
          </thead>
          <tbody>
            {engine.decisions.map((decision) => <DecisionRow key={`${decision.market}-${decision.selection}`} decision={decision} />)}
          </tbody>
        </table>
      </TableScroll>
      <p className="muted" style={{ marginTop: '0.5rem' }}>Total virtual stake: <strong>{money(staked)}</strong> (simulation only)</p>
    </div>
  )
}

function DecisionRow({ decision }: { decision: ShadowDecisionView }) {
  return (
    <tr>
      <td>{decision.market.replaceAll('_', ' ')}</td>
      <td>{decision.selection.replaceAll('_', ' ')}</td>
      <td>{pct(decision.model_probability)}</td>
      <td className="muted">{decision.book ?? '—'}</td>
      <td>{decision.best_american_odds ?? '—'}</td>
      <td>{pct(decision.no_vig_probability)}</td>
      <td>{edge(decision.no_vig_edge)}</td>
      <td>{decision.expected_value == null ? '—' : decision.expected_value.toFixed(3)}</td>
      <td><span className={RECOMMENDATION_CLASS[decision.recommendation]}>{decision.recommendation.replaceAll('_', ' ')}</span></td>
      <td>{money(decision.virtual_stake)}</td>
    </tr>
  )
}
