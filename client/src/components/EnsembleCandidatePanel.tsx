/**
 * The ensemble release candidate beside the frozen V18 baseline.
 *
 * Display rules, enforced here rather than left to the reader:
 *
 *  - Both sides are research tracks. Neither is the controlling model, and the
 *    panel names the engine that actually is.
 *  - A labelled historical replay is marked as one everywhere it appears, and
 *    is stated to be excluded from prospective performance.
 *  - Components show what RAN and why anything fell back. A per-component
 *    probability split appears only when Model Studio supplied a real
 *    attribution; otherwise the panel says the split is unavailable rather
 *    than dividing the total difference by subtraction.
 *  - A calibration band is described as the calibration component's own
 *    confidence, never as a discovered difficult matchup.
 *  - Nothing here runs a model, and no difference is presented as an edge.
 */
import { AlertTriangle, CheckCircle2, MinusCircle } from 'lucide-react'
import { formatTimestamp } from '../services/format'
import type {
  CandidateComparison, CandidateConfiguration, CandidateScore, CandidateSide,
} from '../types/shadow'

function pct(value: number | null | undefined): string {
  return value == null ? 'Not available' : `${value.toFixed(1)}%`
}

function runs(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(2)
}

function signed(value: number | null | undefined, digits = 2, suffix = ''): string {
  if (value == null) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}${suffix}`
}

function SideCard({ side, homeTeam }: { side: CandidateSide | null; homeTeam: string | null }) {
  if (!side) {
    return (
      <article className="candidate-card candidate-card--absent">
        <header><span className="candidate-card__track">Baseline</span></header>
        <p className="candidate-card__value">Not available</p>
        <p className="candidate-card__caption">
          No frozen V18 forecast is registered for this game, so no difference can be shown.
        </p>
      </article>
    )
  }
  const isCandidate = side.track === 'RESEARCH_CANDIDATE'
  return (
    <article className={`candidate-card ${isCandidate ? 'candidate-card--candidate' : 'candidate-card--baseline'}`}>
      <header>
        <span className="candidate-card__track">{side.label}</span>
        <span className="candidate-card__authority">No wagering authority</span>
      </header>
      <p className="candidate-card__value">{pct(side.home_win_probability)}</p>
      <p className="candidate-card__caption">{homeTeam ?? 'Home'} win probability</p>
      <dl className="candidate-card__facts">
        <div>
          <dt>Predicted score</dt>
          <dd>{runs(side.expected_away_runs)} – {runs(side.expected_home_runs)} (away – home)</dd>
        </div>
        <div><dt>Engine</dt><dd>{side.engine_id}</dd></div>
        <div><dt>Version</dt><dd>{side.engine_version ?? 'Not recorded'}</dd></div>
        <div>
          <dt>Evidence cutoff</dt>
          <dd>{side.evidence_cutoff_utc ? formatTimestamp(side.evidence_cutoff_utc) : 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Published</dt>
          <dd>{side.published_utc ? formatTimestamp(side.published_utc) : 'Not recorded'}</dd>
        </div>
        <div>
          <dt>Imported</dt>
          <dd>{side.imported_utc ? formatTimestamp(side.imported_utc) : 'Not recorded'}</dd>
        </div>
        {side.simulation_count != null && (
          <div><dt>Simulations</dt><dd>{side.simulation_count.toLocaleString()}</dd></div>
        )}
        {side.model_artifact_sha256 && (
          <div><dt>Artifact</dt><dd><code>{side.model_artifact_sha256.slice(0, 12)}…</code></dd></div>
        )}
      </dl>
      <ScoreBlock score={side.score} />
    </article>
  )
}

function ScoreBlock({ score }: { score: CandidateScore | null }) {
  if (!score) {
    return (
      <p className="candidate-card__score candidate-card__score--pending">
        Not scored yet — no eligible final evidence for this forecast.
      </p>
    )
  }
  return (
    <div className="candidate-card__score">
      <p className="candidate-card__score-head">
        Final {score.away_score ?? '—'}–{score.home_score ?? '—'} ·{' '}
        {score.winner_correct ? 'called the winner' : 'missed the winner'}
      </p>
      <dl className="candidate-card__facts">
        <div><dt>Log loss</dt><dd>{score.log_loss?.toFixed(4) ?? '—'}</dd></div>
        <div><dt>Brier</dt><dd>{score.brier_score?.toFixed(4) ?? '—'}</dd></div>
        <div><dt>Margin error</dt><dd>{signed(score.margin_error)}</dd></div>
        <div><dt>CRPS margin</dt><dd>{score.crps_margin?.toFixed(3) ?? '—'}</dd></div>
        <div><dt>Result source</dt><dd>{score.result_source ?? '—'}</dd></div>
      </dl>
      {score.publication_clock_verified === false && (
        <p className="candidate-card__qualified">
          Qualified: this forecast's publication time is stored as the same value as
          its evidence cutoff, so it is not independently evidenced.
        </p>
      )}
    </div>
  )
}

function ConfigurationTable({
  configurations, orderingClaim, limitations, status,
}: {
  configurations: CandidateConfiguration[]
  orderingClaim?: string
  limitations?: string[]
  status?: string | null
}) {
  return (
    <div className="candidate-configs" data-testid="candidate-configurations">
      <h3>Configurations measured in this replay</h3>
      {/* The refusal comes BEFORE the numbers, deliberately: a reader who stops
          at the table must already have been told it orders nothing. */}
      <p className="candidate-configs__warning" data-testid="candidate-ordering-claim">
        {orderingClaim
          ?? 'One historical replay of one game. No prospective ordering.'}
      </p>
      <div className="candidate-configs__scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Config</th>
              <th scope="col">Status</th>
              <th scope="col">Home win</th>
              <th scope="col">Δ vs A</th>
              <th scope="col">Predicted score</th>
            </tr>
          </thead>
          <tbody>
            {configurations.map((config) => (
              <tr
                key={config.key}
                className={config.enabled_in_release ? 'is-enabled' : 'is-not-enabled'}
                data-testid={`candidate-config-${config.letter}`}
              >
                <th scope="row">
                  <span className="candidate-configs__letter">{config.letter}</span>
                  <span className="candidate-configs__title">{config.title}</span>
                  {config.enabled_in_release && (
                    <span className="candidate-configs__enabled">Enabled in release</span>
                  )}
                </th>
                <td>
                  <span className="candidate-configs__status">{config.status}</span>
                  <span className="candidate-configs__note">{config.note}</span>
                </td>
                <td className="is-numeric">{(config.home_win_probability * 100).toFixed(2)}%</td>
                <td className="is-numeric">
                  {config.paired_delta_vs_a.home_win_probability == null
                    ? '—'
                    : `${signed(config.paired_delta_vs_a.home_win_probability * 100, 2, ' pts')}`}
                  {config.paired_delta_vs_a.home_win_paired_standard_error != null && (
                    <span className="candidate-configs__se">
                      {' '}± {(config.paired_delta_vs_a.home_win_paired_standard_error * 100).toFixed(2)} paired MCSE
                    </span>
                  )}
                </td>
                <td className="is-numeric">
                  {runs(config.expected_away_runs)} – {runs(config.expected_home_runs)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {status && <p className="candidate-configs__status-line">{status}</p>}
      {limitations && limitations.length > 0 && (
        <ul className="candidate-configs__limitations">
          {limitations.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
    </div>
  )
}

/**
 * Which arm of the comparison this is, and what else was published.
 *
 * Survival and transition are two configurations of one engine family, not two
 * vintages of one thing. Showing only the newest made the second import look
 * like a replacement for the first. Each arm is listed with the one fact that
 * decides whether its number means anything: whether its component actually
 * ran, or whether it fell back to the baseline's own draws.
 */
function ConfigurationBar({ comparison }: { comparison: CandidateComparison }) {
  const options = comparison.available_configurations ?? []
  const selected = comparison.configuration
  if (!selected && options.length < 2) return null
  return (
    <div className="candidate-configs" data-testid="candidate-configurations">
      {options.length > 1 && (
        <ul className="candidate-configs__list">
          {options.map((option) => {
            const active = option.key === selected?.key
            return (
              <li key={option.key} className={active ? 'is-active' : undefined}>
                <a
                  href={`?configuration=${encodeURIComponent(option.key)}`}
                  aria-current={active ? 'true' : undefined}
                >
                  <b>{option.configuration_id ?? option.key}</b>
                </a>
                <small>
                  {option.component_trial_supported
                    ? 'component ran'
                    : 'fell back to baseline draws'}
                </small>
              </li>
            )
          })}
        </ul>
      )}
      {selected && (
        <dl className="candidate-configs__facts">
          <div>
            <dt>Configuration</dt>
            <dd>{selected.configuration_id ?? selected.key}</dd>
          </div>
          {selected.actual_configuration && (
            <div>
              <dt>Actually produced</dt>
              <dd>{selected.actual_configuration}</dd>
            </div>
          )}
          {selected.contract_version && (
            <div><dt>Contract</dt><dd>{selected.contract_version}</dd></div>
          )}
          {comparison.baseline_binding && (
            <div><dt>Baseline resolved by</dt><dd>{comparison.baseline_binding}</dd></div>
          )}
        </dl>
      )}
      {comparison.fallback_note && (
        <p className="candidate-configs__fallback" role="note">
          <MinusCircle size={13} aria-hidden="true" /> {comparison.fallback_note}
        </p>
      )}
    </div>
  )
}


export function EnsembleCandidatePanel({
  comparison,
  homeTeam,
}: {
  comparison: CandidateComparison
  homeTeam: string | null
}) {
  if (comparison.schema_version !== 'AISP_MLB_ENSEMBLE_CANDIDATE_COMPARISON_V1') return null
  const { difference, components, candidate_identity: identity, authority } = comparison

  return (
    <section
      className="candidate-compare"
      id="ensemble-candidate"
      data-testid="ensemble-candidate-panel"
      aria-labelledby="candidate-compare-heading"
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">RESEARCH CANDIDATE</span>
          <h2 id="candidate-compare-heading">Frozen baseline vs. ensemble candidate</h2>
        </div>
        <p className="candidate-compare__status">
          {comparison.is_replay ? (
            <span className="candidate-badge candidate-badge--replay" data-testid="candidate-artifact-type">
              <AlertTriangle size={13} aria-hidden="true" /> Historical replay
            </span>
          ) : (
            <span className="candidate-badge candidate-badge--prospective" data-testid="candidate-artifact-type">
              <CheckCircle2 size={13} aria-hidden="true" /> Prospective
            </span>
          )}
        </p>
      </div>

      <p className="candidate-compare__note">{comparison.note}</p>

      <ConfigurationBar comparison={comparison} />

      <div className="candidate-compare__grid">
        <SideCard side={comparison.baseline} homeTeam={homeTeam} />
        <SideCard side={comparison.candidate} homeTeam={homeTeam} />
      </div>

      {difference && (
        <div className="candidate-compare__difference" data-testid="candidate-difference">
          <h3>Difference — {difference.convention}</h3>
          <dl className="candidate-card__facts">
            <div>
              <dt>Home win probability</dt>
              <dd>{signed(difference.home_win_probability_points, 1, ' pts')}</dd>
            </div>
            <div>
              <dt>Expected home runs</dt>
              <dd>{signed(difference.expected_home_runs)}</dd>
            </div>
            <div>
              <dt>Expected away runs</dt>
              <dd>{signed(difference.expected_away_runs)}</dd>
            </div>
          </dl>
          {comparison.component_attribution_note && (
            <p className="candidate-compare__attribution-note">
              {comparison.component_attribution_note}
            </p>
          )}
        </div>
      )}

      {comparison.configurations && comparison.configurations.length > 0 && (
        <ConfigurationTable
          configurations={comparison.configurations}
          orderingClaim={comparison.ordering_claim}
          limitations={comparison.limitations}
          status={comparison.comparison_status}
        />
      )}

      <div className="candidate-compare__components" data-testid="candidate-components">
        <h3>Components</h3>
        <ul>
          {components.length === 0 && <li>No component record was supplied with this forecast.</li>}
          {components.map((component) => (
            <li key={component.component_id} className={component.ran ? 'is-applied' : 'is-fallback'}>
              <span className="candidate-component__icon" aria-hidden="true">
                {component.ran ? <CheckCircle2 size={13} /> : <MinusCircle size={13} />}
              </span>
              <span className="candidate-component__id">{component.component_id}</span>
              {component.role && <span className="candidate-component__role">{component.role}</span>}
              <span className="candidate-component__state">
                {component.ran ? 'ran' : 'did not run'}
              </span>
              {!component.ran && component.fallback_reason && (
                <span className="candidate-component__fallback">
                  Fallback: {component.fallback_reason}
                </span>
              )}
              {component.status && (
                <span className="candidate-component__status">{component.status}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <details className="candidate-compare__identity">
        <summary>Candidate identity and authority</summary>
        <dl className="candidate-card__facts">
          <div><dt>Engine</dt><dd>{identity.engine_id}</dd></div>
          <div><dt>Version</dt><dd>{identity.engine_version ?? 'Not recorded'}</dd></div>
          <div>
            <dt>Model artifact</dt>
            <dd><code>{identity.model_artifact_sha256?.slice(0, 16) ?? '—'}…</code></dd>
          </div>
          <div>
            <dt>Handoff</dt>
            <dd><code>{identity.handoff_sha256?.slice(0, 16) ?? '—'}…</code></dd>
          </div>
          <div><dt>Built on baseline forecast</dt><dd>{identity.baseline_forecast_id ?? '—'}</dd></div>
          <div>
            <dt>Controlling model for official decisions</dt>
            <dd data-testid="controlling-engine">
              {authority.controlling_engine_for_official_decisions}
            </dd>
          </div>
          <div><dt>Candidate wagering authority</dt><dd>None</dd></div>
          <div><dt>Candidate executed in AISP</dt><dd>Never</dd></div>
        </dl>
      </details>
    </section>
  )
}
