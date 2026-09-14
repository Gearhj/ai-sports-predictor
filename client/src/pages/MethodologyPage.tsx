import { ArrowDown, Binary, BookOpen, BrainCircuit, Database, GitBranch, Info, Scale, ShieldCheck } from 'lucide-react'
import { PageHeader, Panel } from '../components/ui'
import { usePublicPolicy } from '../hooks/usePublicPolicy'

const sections = [
  { id: 'principles', label: 'Principles' },
  { id: 'data', label: 'Data & time' },
  { id: 'forecast', label: 'Forecast system' },
  { id: 'evaluation', label: 'Evaluation design' },
  { id: 'market', label: 'Market comparison' },
  { id: 'confidence', label: 'Confidence' },
  { id: 'experiment', label: 'Bankroll experiment' },
  { id: 'limitations', label: 'Limitations' },
]

import { ScoreCoverage } from '../components/scoreboard/ScoreCoverage'

export function MethodologyPage() {
  const { policy, source, loading } = usePublicPolicy()
  const policySourceLabel = source === 'api' ? 'PUBLIC API POLICY' : 'CONFIGURATION UNAVAILABLE · DESCRIPTIONS ONLY'
  const configuredPercentage = (value: number | null) => value == null ? 'Configuration unavailable' : `${value}%`

  return (
    <div className="page methodology-page">
      <PageHeader eyebrow="TRANSPARENT FORECASTING" title="Methodology" description="What ASP measures, how forecasts are produced, and where uncertainty remains." />

      <section className="methodology-hero">
        <span className="methodology-hero__icon"><BrainCircuit size={29} /></span>
        <div><h2>Probability estimates, not picks.</h2><p>AI Sports Predictor is a quantitative sports research laboratory. Reproducible models generate every forecast; an explanation layer may describe those outputs, but it does not invent prices or probabilities.</p></div>
      </section>

      <div className="methodology-layout">
        <aside className="methodology-nav"><span>ON THIS PAGE</span>{sections.map((section) => <a href={`#${section.id}`} key={section.id}>{section.label}</a>)}</aside>
        <div className="methodology-content">
          <section id="principles" className="method-section">
            <span className="section-number">01</span>
            <div>
              <p className="eyebrow">CORE PRINCIPLES</p>
              <h2>What ASP is—and is not</h2>
              <p>ASP estimates game outcomes, expected scoring, and uncertainty using information available before first pitch. It compares those estimates with observed sportsbook markets to study where model and market disagree.</p>
              <div className="principle-cards">
                <article><ShieldCheck size={18} /><strong>Transparent</strong><p>Every published forecast has a version, timestamp, model version, and data-as-of boundary.</p></article>
                <article><Binary size={18} /><strong>Reproducible</strong><p>The quantitative stack—not an LLM—calculates probabilities, fair prices, and simulations.</p></article>
                <article><Scale size={18} /><strong>Accountable</strong><p>Performance includes every eligible forecast and every official virtual position.</p></article>
              </div>
              <div className="not-list"><strong>ASP is not</strong><span>A guarantee of outcomes</span><span>A picks-selling service</span><span>A sportsbook or execution platform</span><span>Personalized financial advice</span></div>
            </div>
          </section>

          <section id="data" className="method-section">
            <span className="section-number">02</span>
            <div>
              <p className="eyebrow">CANONICAL DATA LAYER</p>
              <h2>Sources are replaceable; time boundaries are not</h2>
              <p>External feeds are normalized into ASP’s internal sports model before feature engineering. Source adapters isolate provider-specific schemas and preserve raw observations where appropriate.</p>
              <div className="pipeline" aria-label="Data and forecasting pipeline">
                {['Source adapters', 'Raw snapshots', 'Canonical data', 'Feature store', 'Forecast registry', 'Market comparison'].map((label, index) => <div key={label}><span>{index + 1}</span><strong>{label}</strong>{index < 5 && <ArrowDown size={14} />}</div>)}
              </div>
              <div className="source-grid">
                <article><Database size={17} /><strong>MLB game feed</strong><span>Schedule, venues, probable pitchers, results</span></article>
                <article><Database size={17} /><strong>Baseball Savant</strong><span>Optional Statcast context only when explicitly enabled</span></article>
                <article><Database size={17} /><strong>The Odds API</strong><span>Timestamped book-level market snapshots</span></article>
                <article><Database size={17} /><strong>Open-Meteo</strong><span>Hourly ballpark weather context</span></article>
              </div>
              <div className="formula-card"><span>POINT-IN-TIME RULE</span><code>feature.available_as_of ≤ forecast.cutoff_at &lt; game.starts_at</code><p>Historical evaluation can use only information available at the forecast cutoff. The game result never enters a pregame feature.</p></div>
            </div>
          </section>

          <section id="forecast" className="method-section">
            <span className="section-number">03</span>
            <div>
              <p className="eyebrow">FORECAST SYSTEM · {policy.modelStatus}</p>
              <h2>A transparent ensemble</h2>
              <p>No single model is treated as unquestionable. Component outputs and weights are versioned so the final forecast can be audited and reproduced.</p>
              <div className="model-stack">
                <article><span>A</span><div><strong>Team-strength baseline</strong><p>An interpretable rating prior captures persistent team quality and home field.</p></div><em>{configuredPercentage(policy.componentWeights.baseline)}</em></article>
                <article><span>B</span><div><strong>Statistical run model</strong><p>Team-level scoring distributions yield expected runs, totals, and game simulations.</p></div><em>{configuredPercentage(policy.componentWeights.run)}</em></article>
                <article><span>C</span><div><strong>Gradient boosting challenger</strong><p>A trained artifact remains a challenger until chronological evidence satisfies the explicit promotion policy.</p></div><em>{configuredPercentage(policy.componentWeights.boosting)}</em></article>
                <article className="model-stack__output"><GitBranch size={19} /><div><strong>ASP ensemble</strong><p>Expected scores · win probability · fair moneylines · uncertainty</p></div><em>{policy.componentWeights.baseline == null ? 'Configuration unavailable' : '100%'}</em></article>
              </div>
              <p className="method-note"><Info size={15} /> {loading ? 'Checking the public policy configuration…' : policySourceLabel}. Weights are provisional and component outputs are retained alongside the ensemble.</p>
            </div>
          </section>

          <section id="evaluation" className="method-section">
            <span className="section-number">04</span>
            <div>
              <p className="eyebrow">CHRONOLOGICAL MODEL GOVERNANCE</p>
              <h2>Training chooses the model; the holdout audits that choice</h2>
              <p>Baseball changes over time, so random row shuffles are not credible evidence. Games remain ordered by first pitch and grouped by game while every feature observes its original cutoff.</p>
              <div className="evaluation-flow" aria-label="Training, validation, and holdout responsibilities">
                <article><span>01</span><div><strong>Training window</strong><p>Fit model parameters using only the earliest eligible games in each rolling-origin fold.</p></div></article>
                <article><span>02</span><div><strong>Validation window</strong><p>Select hyperparameters, fit calibration, choose ensemble weights, and compare ablations. These decisions never inspect the holdout.</p></div></article>
                <article><span>03</span><div><strong>Locked holdout</strong><p>Evaluate the frozen candidate once on later games. Report the full window, sample size, metrics, and matched baselines.</p></div></article>
              </div>
              <div className="evaluation-guardrails">
                <article><strong>Rolling-origin folds</strong><p>Training expands forward and validation advances through time. Fold results reveal season or regime sensitivity before the final holdout is opened.</p></article>
                <article><strong>Cross-fitted validation</strong><p>Base models and ensemble weights are evaluated out of fold, with a second expanding-origin split for the stacker. Calibration and ablations use validation evidence only.</p></article>
                <article><strong>Matched comparisons</strong><p>Candidate and baselines are scored on the same eligible games. Missing odds history reduces the stated market sample; it is never synthesized.</p></article>
                <article><strong>Explicit promotion</strong><p>Promotion is a separate, versioned decision. Insufficient samples, leakage failures, missing artifacts, or unmet criteria block promotion.</p></article>
              </div>
              <p className="method-note"><ShieldCheck size={15} /> The Performance page separates offline chronological evidence from live forward results. Until a governed artifact exists, it reports NOT RUN or INSUFFICIENT DATA—not placeholder metrics.</p>
            </div>
          </section>

          <section id="market" className="method-section">
            <span className="section-number">05</span>
            <div>
              <p className="eyebrow">MARKET COMPARISON</p>
              <h2>From listed odds to no-vig probability</h2>
              <p>Sportsbook prices include margin. ASP first converts each side to implied probability, then normalizes a two-way market before calculating disagreement.</p>
              <div className="equation-grid"><div><span>American odds → implied probability</span><code>negative: |odds| / (|odds| + 100)</code><code>positive: 100 / (odds + 100)</code></div><div><span>Remove market margin</span><code>no-vig pᵢ = raw pᵢ / Σ raw p</code></div><div className="equation-grid__accent"><span>ASP model edge</span><code>ASP probability − market no-vig probability</code></div></div>
              <p>A displayed edge is a difference in estimated probability—not expected profit, certainty, or a promise that one model knows more than the market.</p>
            </div>
          </section>

          <section id="confidence" className="method-section">
            <span className="section-number">06</span>
            <div>
              <p className="eyebrow">TWO DIFFERENT QUESTIONS</p>
              <h2>Forecast probability ≠ confidence</h2>
              <div className="two-concepts"><article><span>57.9%</span><strong>Forecast probability</strong><p>How often the modeled home side is expected to win under current inputs.</p></article><article><span>74 / 100</span><strong>Forecast confidence</strong><p>How much trust to place in that estimate given ensemble agreement, calibration, uncertainty, sample reliability, and data readiness.</p></article></div>
              <p>Data readiness separately measures whether required feeds are present and fresh. A game can have a strong win probability but modest confidence if the lineup is unconfirmed or model components disagree.</p>
            </div>
          </section>

          <section id="experiment" className="method-section">
            <span className="section-number">07</span>
            <div>
              <p className="eyebrow">$1,000 ASP EXPERIMENT</p>
              <h2>A public simulation with no deleted losses</h2>
              <p>The official virtual bankroll asks what would have happened if ASP followed a predefined, conservative policy. It performs no financial transactions and cannot connect to sportsbook accounts.</p>
              <Panel className="policy-summary"><div className="policy-grid"><div><span>Opening bankroll</span><strong>$1,000 virtual</strong></div><div><span>Initial sizing</span><strong>{policy.bankroll.kellyFraction == null ? 'Configuration unavailable' : `${policy.bankroll.kellyFraction * 100}% Kelly`}</strong></div><div><span>Maximum position</span><strong>{policy.bankroll.maximumStakePercentage == null ? 'Configuration unavailable' : `${policy.bankroll.maximumStakePercentage}% of bankroll`}</strong></div><div><span>Position history</span><strong>Append-only</strong></div></div></Panel>
            </div>
          </section>

          {/* Where scores come from, and what each feed simply does not
              publish. The limits belong in the product: "no clock shown" is
              otherwise indistinguishable from a bug. */}
          <section id="scores" className="method-section">
            <span className="section-number">08</span>
            <div>
              <p className="eyebrow">LIVE SCORES</p>
              <h2>What the score feeds actually supply</h2>
              <p>
                Scores are collected centrally on a fixed cadence and every
                surface reads the same stored observation. Collection does not
                depend on a forecast, a price or a qualifying opportunity.
              </p>
              <ScoreCoverage />
            </div>
          </section>

          <section id="limitations" className="method-section">
            <span className="section-number">09</span>
            <div>
              <p className="eyebrow">LIMITATIONS</p>
              <h2>What can go wrong</h2>
              <ul className="limitation-list"><li><strong>Baseball is noisy.</strong> Even a well-estimated favorite loses often.</li><li><strong>Inputs change.</strong> Late scratches, lineups, weather, and market movement can materially alter a forecast.</li><li><strong>Models drift.</strong> Relationships learned from history may weaken as teams, strategy, and environments change.</li><li><strong>Data can fail.</strong> Source outages, incorrect identifiers, and stale observations are surfaced but cannot always be prevented.</li><li><strong>Backtests are not live results.</strong> Historical odds coverage may be incomplete and is never fabricated.</li></ul>
              <div className="responsible-callout"><BookOpen size={18} /><p><strong>Research, education, and entertainment only.</strong> No forecast is guaranteed. Do not chase losses or treat simulated results as a promise of future returns.</p></div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
