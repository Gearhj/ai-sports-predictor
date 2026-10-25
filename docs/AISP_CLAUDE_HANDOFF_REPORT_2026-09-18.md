# AISP handoff report: Claude's role, the system, AI Model Studio, and every approach tried

As of **2026-09-18, about 13:00 ET**. Written for the next chat, so it can start without re-deriving the history.
Everything here comes from the repository, the exchange folder, the live database (read-only queries) or my own work
log. Anything inferred rather than read is marked *(inferred)*. No credentials appear in this file.

---

## 1. Who does what

| Party | Role | Boundaries |
|---|---|---|
| **James (owner)** | Decides policy, spend, activation, and anything that touches bankroll or wagering authority. | Every provider spend, activation or threshold change needs his explicit approval. |
| **Claude (Claude Code, this repo)** | **Consumer, integration and operations engineer for AISP.** Builds and deploys the app (FastAPI + React). Validates and imports what Model Studio publishes. Runs the collection, decision, settlement and evaluation plumbing. Diagnoses production issues and writes the reports. | Never modifies `asp-model-studio`. Never fits or tunes models, and never changes decision thresholds or staking policy unless asked. No provider spend beyond an approved budget. Never creates user accounts or enters passwords. Restarts the backend only through the `ASP Backend 8010` scheduled task, outside decision windows. Pushes only from the sanitized GitHub checkout. |
| **Codex (AI Model Studio)** | **Producer.** Acquires model-side data, trains and evaluates models, runs studies, and publishes immutable, hash-addressed forecast packages, handoffs and eligibility supplements into `asp-model-exchange`. | Never writes into AISP. AISP never runs Studio's estimators. |

Chain of custody, in the producer's own words:

> admitted evidence → Studio model → immutable package → AISP validates and imports → AISP market/policy → governed finals → Studio assessment

A package moves through publication, import, display, decision, promotion and profit, and each stage is a separate
step with its own gate.

---

## 2. The AISP system

### Runtime
- **Backend:** FastAPI + SQLAlchemy, PostgreSQL `asp` on localhost:5432. Uvicorn on **127.0.0.1:8010**, supervised by
  the Windows scheduled task **`ASP Backend 8010`**. The backend also serves the production React build (`client/dist`);
  `/frontendz` reports the deployed commit.
- **Deploying a backend change:** `Stop-ScheduledTask` then `Start-ScheduledTask 'ASP Backend 8010'`.
  - Record the listener PID first, and confirm afterwards that a new PID is listening.
  - An old instance can answer `/readyz` for 20–40 s after the restart. See memory `asp-task-orphan-limitation`.
- **Deploying the frontend:** `powershell -File scripts/build-frontend.ps1`. It needs a clean tree and no restart.
  Rollback copies go in `.runtime/frontend-rollback/dist-<commit>`.
- **Migrations:** `alembic upgrade head`, run from `server/` with the repo `.venv`. Current head is `20260918_0046`
  (House Divided).
- **Jobs:** an APScheduler inside the backend runs about 35 jobs, all on one worker thread off the web event loop.
  The ones that matter most:
  - `finalize-games` (every 30 min; owns settlement and the MLB schedule refresh);
  - `freeze-shadow-experiment` (5 min);
  - `collect-odds-checkpoint`;
  - `collect-nfl-pregame-odds`;
  - `bridge-mlb-canonical-events` (180 min, rolling today + 2 days);
  - `recover-governed-finals`;
  - `ingest-nfl-finals` (nflverse, hourly);
  - `consume-model-exchange-research-artifacts` (15 min);
  - `evaluate-mlb-v18-research-forecasts` and `evaluate-nfl-research-forecasts`;
  - `evaluate-nfl-paper-checkpoint`;
  - `refresh-scoreboard`;
  - the prediction-market capture jobs;
  - `open-model-era-at-boundary`.
- **Two checkouts:**
  - `C:\Users\james\ai-sports-predictor` runs the deployment. Its push is disabled on purpose.
  - `C:\Users\james\ai-sports-predictor-github` is the sanitized history; push only from here.
  - Move changes with `git format-patch` → `git am`, compare `^{tree}`, and run `scripts/secret_guard.py --range`
    before any push. `docs/provenance/sanitized-history-commit-map.txt` maps SHAs between the two.
- **Current branch:** `claude/a-house-divided-v1`. Deployed backend `233b08a+`, frontend `1a53ef8`; published at
  GitHub `16ab7cc`.

### Main surfaces
Home (today's schedule and timeline), Games & Forecasts / Game Center, **Daily Decisions** (the decision board, All
Markets, My Card), **Show Prep** (day-before board), Markets & Crowd, Interactive Labs, Model Performance, Historical
Research, Shadow Board, **$1,000 Experiment**, **A House Divided** (the family virtual competition), Methodology,
and the unlisted `/encore` Easter egg.

### Key stores

Row counts are from the live database, 2026-09-18.

| Table | Rows | Meaning |
|---|---|---|
| `games` | 3,165 | Canonical games. `source_mode` is **provenance, never sport**: NFL = `RESEARCH_SHADOW`, MLB = `LIVE`. |
| `odds_snapshots` | 257,918 | Point-in-time book quotes (append-only). |
| `forecasts` / `shadow_forecasts` | 687 / 2,750 | Gen 1 AISP forecasts / imported producer forecasts (append-only). |
| `shadow_experiment_decisions` | 33,808 | Per-phase decisions (MORNING, LINEUP_CONFIRMED, FINAL_PREGAME). Most are repeated evaluations, not selections. |
| `official_asp_positions` | 52 | Virtual experiment positions. |
| `game_state_observations` | 28,169 | **The live finals store.** Status values are lowercase `final`. |
| `game_final_outcomes` | 2,564 | Governed MLB finals (includes the 2025 training labels). **NFL has none.** |
| `lineup_observations` | 31,599 | Lineups. |
| `research_forecast_evaluations` | 140 | Research scores (log loss, three-class Brier, CRPS), with a supersession chain. |
| `provider_request_ledger` | 1,602 | Every provider call and reservation. |
| `competition_*` | 0 wagers | A House Divided (live since 2026-09-18). |
| `rff_reading_snapshots` | 18 | Prospective RFF capture. |
| `player_prop_quotes` | 0 | Prop quote store, inactive. |

---

## 3. How AISP interacts with AI Model Studio

### The exchange: `C:\Users\james\asp-model-exchange\`
- **Content addressing:** every package is immutable under `sha256/<64 hex>/`.
  - Pointers (`current.json`, `discovery.json`, index files) carry `{path or relativePath, sha256, bytes}`.
  - Handoffs self-hash canonical JSON.
  - A logical address is not always the physical manifest hash.

| Folder | What it holds | AISP consumer |
|---|---|---|
| `mlb-v18-current/` | Daily V18 slate handoffs (`AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1/V2`), totals (`MLB_V18_TOTALS_*`), champion handoff, hourly `handoff-keys/` | `shadow_import/*`, `v18_champion.py`, `totals/index_attachment.py` |
| `mlb-v18-ensemble-research-v1/`, `…transition-shrinkage-v1/`, `…simulator-evidence-v1/`, `mlb-starter-duration-research-v1/` | Research candidates (`current.json` → `objects/`) | Ensemble candidate views (research only) |
| `aisp-daily-forecast-bundles/` | NFL research-shadow index (`ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1/V2`, `ASP_SPORTS_FORECAST_EXCHANGE_V1`) | `import-nfl-shadow-forecast-bundle` |
| `nfl-m1-production-v1/` | `ASP_NFL_M1_PRODUCTION_HANDOFF_V1` | `forecast_exchange/nfl_production_handoff.py`, which is pinned in `.env` |
| `nfl-labs-*supplements/`, `contracts/NFL_LABS_*` | Labs, simulation and play-level supplements | Interactive Labs |
| `nfl-week2-paper-eligibility/` | `ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1` supplements | NFL paper-policy eligibility gate |
| `nba-gen1-research-v1/`, `nba-gen1-production-v1/`, `nba-production-baseline-v1/` | NBA research and production (`STAGED_INACTIVE`) | Staged adapter on branch `claude/nba-consumer-integration-staged-v1`, not merged |
| `cfb-gen1-research-v1/` | Research packages (`CFB_GEN1_PRIVATE_RESEARCH_PACKAGE_INDEX_V1`) | `cfb/research_import.py` (manual script) |
| `cfb-gen1-production-v1/` | Slate discovery (`CFB_GEN1_SLATE_DISCOVERY_POINTER_V1` → `…_V1`), forecasts, **discovery-bound publication receipts**, staged handoff | `cfb/production_discovery.py`. **Staged**: verifies 57/57, imports nothing. |
| `show-preview-forecasts-v1/` | `AISP_SHOW_PREVIEW_FORECAST_V1` | `show_preview/exchange.py` |
| `demo-releases-v1/` | Multisport demo release | Demo reconciliation |
| `consumer-acknowledgements/` | **What AISP writes back:** market-probability, show-preview and totals acknowledgements; demo consumer receipts | AISP scripts and modules |

### Rules AISP enforces on anything it imports
- **Verify from bytes:** index digest = directory name, then every member's SHA-256 and byte count. After that, check
  the schema, the research/authority flags (`researchOnly`, `aispAuthority:false`, `wageringAuthority:false`), and the
  exact configuration identity.
- **Draw populations are exactly 10,000 draws** (NFL, NBA, CFB). PMF mass must equal the draw count. AISP recomputes
  the joint win probability from the draws.
- **Clocks:** source cutoff ≤ creation ≤ publication ≤ kickoff. Publication time comes from the receipt the producer
  designates; for CFB production that is the discovery-bound `committedAtUtc`.
- **Orientation:** home and away are checked by exact provider IDs, never by name, and against an **independent**
  table. The producer's own input files don't count.
- **Production authority** comes from a pinned handoff (path + SHA-256 in `.env`).
  - Empty pins mean `NOT_RECEIVED`.
  - A verified handoff that is not activated means `STAGED`.
  - Only an activated handoff lets the UI show a production label.
- **Model channels stay separate.** Ridge points, Elo and joint draws are never averaged, and no probability is
  derived that the producer did not supply.
- **Cross-game combinations are refused:** `/probability/cross-game` always returns 422
  `CROSS_GAME_CALIBRATION_NOT_VALIDATED`.
- `MODEL_EXCHANGE_AUTO_IMPORT_ENABLED=true` since 2026-09-15. It imports only index-declared CURRENT bundles, NFL
  supplements, labs, and V18 (under its own flag). NBA and CFB eligibility come in through an exact-version allowlist.

### Status per sport (2026-09-18)

| Sport | Controlling engine | AISP status | Decisions and positions |
|---|---|---|---|
| MLB | **V18** (`MLB_V18_WHOLE_GAME`), conditional champion since 2026-09-12, experiment `b7532da1` | Official funded era | FINAL_PREGAME decisions → positions at cutoff (start − 15 min). V2.1 policy unchanged. |
| NFL | **M1** (`NFL_M1_XGBOOST`), production baseline (prospective from week 2) via pinned handoff | Paper experiment `c25f7272` | Decided at T−30…T−15 min by `nfl_paper_decision.py`. Needs a per-forecast eligibility supplement. |
| NBA | Gen 1 (Ridge + Elo + joint) | Research; production `STAGED_INACTIVE` | None. The paper account exists but has no eligibility. |
| CFB | Gen 1 Ridge A100 paired residual | Research; production discovery verified but **staged** | None. No price source, no final ingest, no decision owner. |

---

## 4. Data

| Source | Used for | Cost / limit | Notes |
|---|---|---|---|
| **MLB Stats API** | Schedule, probables, lineups, finals | Free | Rolling today + 2-day bridge. `LEAGUE_PUBLISHES_NO_GAMES_ON_THIS_DATE` marks a verified empty day. |
| **SportsGameOdds (SGO)** | **Main odds source** for MLB and NFL (moneyline, spread, total) | Billed **per event object**; 2,500 objects per UTC billing month, **300 reserved** | Two budget guards (AISP ledger + provider usage). Daily share is **demand-shaped** (owner decision 2026-09-18). |
| **The Odds API** | Legacy odds; player-prop collector | 500 credits/month; **balance 5, below the floor of 10** | Restricted. No ingestion since 2026-09-07. |
| **nflverse** | NFL schedule and finals | Free | `ingest-nfl-finals`, hourly. |
| **CFBD** | CFB schedule and stats (**producer side only**) | Free tier 1,000 calls/month (995 left, resets Oct 1) | AISP has no CFBD client. `/lines` has never been called. |
| **Kalshi / Polymarket** | Crowd prices (research) | Free public reads | Semantics templates still held closed. |
| **Open-Meteo** | Ballpark weather | Free | |
| **Baseball Savant / Statcast** | Planned | Off | Tables never populated. Terms blocked a multi-season download. |
| **Retrosheet / Chadwick / Lahman (FD1)** | Free historical warehouse | Free | 3.74 M rows, used by V18 geometry. |

Budget state (September): 1,981 of 2,500 SGO objects used. MLB and NFL still need about 206, so **about 13 spare
objects** remain. Reserve intact.

---

## 5. Workflow (a normal day)

**MLB:**
1. **Schedule.** `bridge-mlb-canonical-events` maintains the next two days.
2. **Prices.**
   - The SGO collector buys a game's event object only once its freshness window has opened: **cutoff − 240 min**.
     Earlier purchases would go stale before the decision.
   - The quote rule is newest-per-book and ≤ 240 min old.
3. **Forecasts.** V18 slate handoffs are imported from the exchange as shadow forecasts. The Studio publishes; AISP
   verifies.
4. **Decisions.**
   - `freeze-shadow-experiment` records MORNING, then LINEUP_CONFIRMED, then FINAL_PREGAME inside the final window
     (the 15 minutes before cutoff).
   - V2.1 policy: tiers A–D, adjusted edge ≥ 2.0 pts, moneyline only.
   - The official decision is the latest FINAL_PREGAME decision at or before the cutoff.
5. **Positions.** A QUALIFIED_BET with a complete quote becomes a virtual position at the cutoff (V2 quarter-Kelly
   with evidence multiplier and caps).
6. **Settlement.** `finalize-games` settles when a game is FINAL with scores (cancelled → VOID; a two-way moneyline
   tie → PUSH). It fires about 90 s after each restart.
7. **Scoring.** `evaluate-mlb-v18-research-forecasts` reads **`game_state_observations`**, never
   `game_final_outcomes`, and scores log loss, Brier and CRPS. Results are split into verified-prospective and
   qualified-diagnostic cohorts by publication clock.

**NFL:**
- **Prices:** checkpoints 75–15 min before kickoff, plus a **Show Prep preview** buy from 15:00 to 16:30 ET the day
  before the slate. One object per game.
- **Decisions:** the paper decision at T−30…T−15 needs a producer eligibility supplement for that exact forecast
  object.
- **Finals and scoring:** finals from nflverse; `evaluate-nfl-research-forecasts` scores.

**A House Divided** (new, 2026-09-18):
- James's picks are the official model decisions, copied once after each game's cutoff.
- Scotty and Red Lou pick in a sportsbook-style lobby with a Pick Slip.
- Human picks lock at the earlier of the cutoff and the first public qualified model pick, and their terms stay
  hidden until then.
- Wagers settle through the same rules as the experiment. The Scotty login ("Elvis") is linked; Red Lou's is not.

---

## 6. Current performance (read-only, 2026-09-18)

**Research scores** (current evaluations only). Stored Brier is **three-class** (0–2); the column below is halved to
approximate the two-way figure.

| Engine | Games | Log loss | ≈ Two-way Brier | Winner accuracy | Margin MAE | Total MAE |
|---|---|---|---|---|---|---|
| MLB_V18_WHOLE_GAME | 109 | 0.660 | 0.234 | 58.7% | 3.29 | 3.12 |
| MLB_ENSEMBLE_CANDIDATE | 13 | 0.617 | 0.213 | 69.2% | 3.82 | 4.22 |
| NFL_M1_XGBOOST | 17 | 0.694 | 0.236 | 64.7% | 11.60 | 13.15 |

The ensemble candidate's 13 games are far too few to conclude anything.

**Virtual experiment positions** (all settled):

| Experiment | Positions | Staked | Won–lost | Profit / loss |
|---|---|---|---|---|
| MLB V18 conditional era `b7532da1` | 31 | $70.18 | 13–18 | **−$10.02** |
| MLB V2.1 practical pregame `43fe1b16` (dormant since V18) | 19 | $45.93 | 7–12 | −$1.48 |
| NFL paper `c25f7272` | 2 | $10.00 | 1–1 | −$2.87 |

Studio's own benchmark on 39 MLB games: the market was slightly better than the model (log loss 0.6555 vs 0.6637)
*(field order inferred)*. **No approach has yet shown a validated edge over the no-vig market.**

---

## 7. Every approach tried: do not repeat without new evidence

Status key: **PROD** = production or official · **RES** = research or shadow · **SUP** = superseded ·
**REJ** = rejected or abandoned · **BLK** = blocked · **NEVER** = never activated.
A = AISP side · S = Model Studio side.

### 7.1 MLB models and simulation

| Approach | Side | What it was | Result | Status | Lesson |
|---|---|---|---|---|---|
| Gen 1 ensemble `asp_mlb_ensemble 2.0.0-gate2` | A | Rating 0.30 + run model 0.45 + "boosting" 0.25, blended 0.60 / 0.40 with a Poisson sim | Never validated. Hand-set weights. The "boosting" part is a fixed untrained logistic. Level with the market on 14 games. | SUP by V18 (2026-09-12) | Don't ship untrained or hand-set parts as "the model". |
| Gen 1 Poisson simulator | A | Independent home/away Poisson draws | No joint score distribution | SUP | Independence can't give a joint score or a true most-likely score. |
| Gate 3 prospective training | A | Leakage-safe training on captured games | 0 usable rows → INSUFFICIENT_DATA | Framework kept | Prospective capture alone needs a full season. |
| Gate 3A historical rebuild (Tier A/B) | A | 2025 point-in-time rebuild, 544 columns | 2,428 rows, 85.4% coverage. Starter, lineup and Statcast families failed their gates. | Data asset | Historical publish times for starters and lineups can't be proven. |
| Gate 3B run-strength + Platt | A | Trained challenger | Holdout Brier 0.2419, log loss 0.6766, calibration slope 1.80. Failed calibration and OOD checks. | REJ (not promoted); **2025 holdout used up** | Don't reopen the holdout; get new seasons. |
| Gate 3B comparisons (Poisson, NB2, EB, XGBoost, Elo, logistic) | A | Same folds | All within noise. Plain Poisson 0.2404 was the best point estimate. | RES | Complexity didn't beat simple baselines. |
| Learned stack / recent form / isotonic | A | Variants | Stack gain 0.0002 (unstable). Recent form worse (0.277 vs 0.262). Isotonic worse than Platt. | REJ | — |
| Gen 2 player-level PA model | A | Player priors → PA → simulator | Never fit (source terms) | BLK | Reappeared as V18 on the Studio side *(inferred)*. |
| Readiness score gate (≥ 70) | A | Composite data-quality gate | Games score 40–57.5. Statcast and bullpen never collected. Lineups cost 9 points. | Gate dropped in V2.1 | A composite gate that can never be met blocks everything. |
| **V18** HGB plate-appearance model → base/out simulator (5,000 draws) | S | Whole-game Monte Carlo | Dev PA log loss 1.4746. Sep-12 slate log loss 0.671. Live 109 games log loss 0.660. Market slightly better on 14. | **PROD** (conditional champion). Chronology uncertified. | Keep; judge only on verified-prospective scores. |
| V14 vs V18 | S | Saved folds | Δ log loss +0.00003 (CI spans 0) | — | No V18 superiority proven. |
| Regime hybrid (89% V18 + 11% RF) | S | 5 architectures | Tiny gain. Specialists, MoE and interactions all worse. | RES | — |
| Ensemble release candidate A/B/C/D | S | Calibration + duration hook | No calibration passed | REJ | — |
| Ensemble candidate arms: survival / transition / score-limit | S+A | Arms on frozen V18 | "Do not advance" for every arm (1–5 games) | RES / REJ | One to five games prove nothing. Don't average fallback arms. |
| Transition shrinkage | S | Hierarchical shrinkage | Log loss 0.6257 → 0.6213 on 16 games, but CRPS worse | Insufficient evidence | — |
| Starter survival / duration | S | Competing-risk model | Better component fit; game-level validation blocked | BLK | — |
| Pitcher archetype survival B/C | S | Style clusters | Worse path NLL | REJ | — |
| Score-limited population | S | Reject extreme draws | One game | REJ | — |
| Doubles-rate correction | S | Diagnostic | Small miscalibration; no fix justified | REJ | — |
| V19 current-state specialists | S | — | **Temporal leak found** | Held | Bit-for-bit reproduction ≠ point-in-time validity. |
| V19 team defense (EB BIP) / Diamond Twin named defense | S | Structural add-ons | No incremental value | REJ | — |
| Head-to-head / starter shrinkage | S | 48 settings | Worse deviance | REJ | — |
| Show-preview team model (logistic + Poisson) | S+A | Day-before winner and score | Log loss 0.6822 vs league 0.6924 | NEVER (blocked on history export) | Independent score heads ≠ joint distribution. |
| Population / play geometry | S | Visualization | Display only | PROD (display) | — |

### 7.2 NFL, NBA, CFB, UFC models

| Approach | Side | Result | Status | Lesson |
|---|---|---|---|---|
| NFL M1 XGBoost (197 features, 10k paired-residual draws) | S | Week 1: 10/16 winners, log loss 0.715. Live 17 games log loss 0.694. | **PROD** baseline (prospective) | One week proves nothing; no QB, injury or weather inputs. |
| M1 tie handling (stochastic rounding) | S | Predicts 3.07% ties vs 0.28% observed | Open limitation | Don't add an overtime stage; the smooth margin law is the problem. |
| NFL overnight lab (22 pipelines, HGB-B-T2) | S | Δ log loss −0.003, CI includes 0 | RES; M1 retained | — |
| QB-context residual / drive competing risks / schedule-adjusted EPA | S | All worse | REJ | — |
| Personnel / injury cold start | S | Insufficient admissible evidence | BLK | Current data can't certify historical vintages. |
| NFL play-level engine / Labs | S | Research engine; 0.6% of simulated games carry play paths | Display only | Illustration, not a forecast. |
| NBA Gen 1: Ridge points + Elo winner + joint draws | S | Beat Elo on MAE. HGB worse. Long-gap fix kept. | RES; production STAGED_INACTIVE | Ridge and Elo are not calibrated probabilities. |
| NBA close-score transport | S | Fixed one-point margins; energy unchanged | RES | — |
| CFB Gen 1 Ridge A100 paired residual | S | 2025: log loss 0.542, accuracy 71.9% | RES; production verified but **staged** | Joint, Ridge and Elo differ (73 / 72 / 56% on one game). Never average them. |
| CFB cluster / extended-gap | S | Failed gate | REJ | — |
| UFC Gen 1 | S | Calibration slope 1.52 > 1.5 limit | No model selected | — |

### 7.3 Decision, staking and calibration policy

| Approach | Result | Status | Lesson |
|---|---|---|---|
| V1 opportunity policy (readiness ≥ 70, confirmed starter, 2-pt edge) | 169 games → **0 qualified** (starters never CONFIRMED: 0/158) | Unfunded control | Gates that can never pass stop everything. |
| V2 practical pregame | Still carried the completeness gate | SUP before activation | — |
| **V2.1 practical pregame** (tiers A–D, penalties, 2.0-pt floor, 240-min quotes, moneyline only) | Pre-V18: 19 positions, 7–12, −$1.48. **63 of 111 games had no usable price.** | **PROD** (policy used by V18) | Missing prices, not the policy, is the limit. |
| Evidence penalties V1 | Starter n=8 (a floor), lineup p95 n=57 | Frozen | Tiny samples. |
| Edge-floor matrix (1.5 / 1.75 / 2.0 / 2.5%) | One game flips the ranking | RES; 1.75% registered inert | Don't rerun this window. |
| Shadow threshold ladder (live) | Accumulating | RES | The forward-looking answer to the edge-floor question. |
| Policy counterfactual A–E; price-availability studies | Not documented | Tools | — |
| Quarter-Kelly V1 / Evidence V2 / NFL, NBA, CFB variants | V2 typical stake ≈ $2.50; NFL capped at $5 | PROD (V2, NFL) | Tie-aware Kelly: omitting (pW + pL) gives 6.69% instead of 6.99%. |
| Studio selection-policy study | 18,610 evaluations → 22 selections | Strict policy retained | Evaluation rows are not selections. |

### 7.4 Market analytics

| Approach | Result | Status | Lesson |
|---|---|---|---|
| No-vig consensus, edge, CLV | CLV coverage about half | PROD | Policy edge (vs no-vig) ≠ price edge (vs break-even). |
| Opportunity tiers (2 / 4 / 7 pts; 15 = extreme) | Display | PROD | — |
| V18 totals from paired draws | Checked | RES | **Rejected** home × away convolution (fake 0–0 finals). |
| Parlay Watch (cross-game independence) | — | SUP | — |
| **Cross-game independence study** (52,405 pairs) | No segment passed | REJ (API returns 422) | Don't treat games as independent. |
| Market-probability contract v1 (same-game per-draw intersection) | Golden vectors pinned | PROD | Never multiply leg prices. |
| Crowd & Market Pulse; Kalshi / Polymarket | An unreviewed quote made a fake 15.7-pt gap. Kalshi once mislabelled 187/189 series. | RES / display | Unreviewed quotes never create gaps or eligibility. |
| RFF v1 (8 parts, renormalized per game) | 8% coverage showed "RFF 100 · Maximum unrest" | SUP | Never divide by the available weight. |
| RFF with fixed-weight intervals (8 parts) | Maximum coverage 0.64 < 0.70 → never publishes | SUP | Parts with no data source make a dead index. |
| **RFF Core** (5 fixed weights) | 4/10 games published on day 1. Validation needs 300 games at T−90 min. | PROD display, unvalidated | Never renormalize. Never optimize on bet outcomes. |
| RFF recomputed after the fact | 4/4 used data that arrived too late | REJ | Capture live; never reconstruct. |
| RFF validation plan v1 (readings as independent) | Overstated power | SUP by v2 | The unit is the game. |
| RFF Extended (public %, book relief, social) | No source | NEVER | Price movement is not public money. |
| Studio market-intelligence RFF lab | Gain explained by a market-only control | RES | — |
| Player props (Odds API quotes; Studio calibration) | 0 rows; `INSUFFICIENT_ADMISSIBLE_EVIDENCE` | NEVER / BLK | No governed player-stat settlement; about 1,800 credits/month. |

### 7.5 Operations and analytics approaches (Claude's sessions)

| Approach | What happened | Status | Lesson |
|---|---|---|---|
| Even daily SGO split (`spendable // days`) | Weekends starved MLB (3 objects for 15 games on 09-18) | SUP by demand shaping (owner decision) | Allocate by each day's measured need. |
| NFL 84-hour advance price pass | Spent 14 of 17 objects overnight | REJ (replaced by the Show Prep preview slot) | Buy only inside the window the price must be fresh for. |
| Counting "is it due now" for demand | Midnight snapshot never grew mid-day | Fixed | Count work by the service day it falls on. |
| Filtering NFL games on `source_mode = LIVE` | Counted zero NFL games | Fixed | `source_mode` is provenance, never sport. |
| Scoring V18 from `game_final_outcomes` | "0 of 31 scorable" (wrong table) | Fixed | Score from `game_state_observations`. |
| Comparing stored Brier with market Brier | Model looked 2× worse | Fixed | The stored figure is three-class; compare two-way only. |
| Reading `job_runs` for refresh-schedule | False "last ran Aug 28" | — | Some steps log to `ingestion_runs`. |
| Replaying the qualification funnel for past dates | Always shows 0 fresh quotes | REJ | Use stored rows. |
| Settling House Divided from governed finals | NFL has none | REJ | Settle like the experiment (Game FINAL). |
| Bet Board redesign of Daily Decisions | Owner rejected it | Reverted | Don't restructure established pages without showing the owner first. |

---

## 8. Top lessons (read before starting anything)

1. **Fix price availability before touching models or thresholds.** Most MLB non-bets come from missing fresh
   quotes, not from the policy.
2. **No approach has beaten the no-vig market on a trustworthy sample.** Samples of 1–20 games decide nothing. Count
   games, not evaluation rows.
3. **Don't reopen used holdouts or inspected cohorts** (2025 holdout, MLB Final OOT Aug 14–27, quarantined games,
   the Sept 1–8 policy window). New evidence must be newly frozen.
4. **Never renormalize composites to whatever data arrived** (RFF). Show missing weight as uncertainty.
5. **Never treat games as independent** (cross-game study failed).
6. **Keep model channels and conventions separate:** three-class vs two-way Brier; Ridge / Elo / joint; research vs
   production labels.
7. **Capture evidence live; never reconstruct past states** (RFF, funnel replays). Current data doesn't certify
   historical vintages.
8. **Verify producer packages from bytes, and check orientation against an independent table.** Publication ≠
   import ≠ activation.
9. **Spend provider objects only inside the window a price must be fresh for, and only with owner approval.**
   Report both SGO budget guards.
10. **Show the owner before restructuring a page he relies on.**

---

## 9. Open decisions and next steps

1. **CFB:** approve or decline one CFBD `/lines` validation request (1 of 995 calls; details in
   `docs/CFB_CONSUMER_STAGING.md`). Then approve Codex's source refresh and publisher install. Then decide on the
   discovery importer, CFB final ingest, CFB decision owner, and a September featured-slate SGO lane (≤ 10 objects).
2. **NBA:** the staged adapter branch still needs a merge decision and activation.
3. **A House Divided:** create Red Lou's login (Claude can't create accounts), then link it. Player props need
   settlement evidence first (`docs/HOUSE_DIVIDED_PLAYER_PROPS_ACTIVATION.md`).
4. **October 1 reset:** add CFB as a demand lane in the shaped allocator before Oct 1, if CFB is approved. The Odds
   API balance renews around then.
5. **Deferred (needs a new authorization):** the backend supervisor Job Object fix (see
   `asp-task-orphan-limitation`).

## 10. Where to look

- **Methodology and history:** `docs/METHODOLOGY.md`, `docs/AISP_PROJECT_HANDOFF.md`,
  `docs/AISP_PROJECT_HANDOFF_BRIEF.md`, `docs/GATE_*`.
- **Market and RFF:** `docs/RFF_CORE*.md`, `docs/AISP_MARKET_PROBABILITY_CONSUMER_V1.md`.
- **Budget:** `docs/SGO_BUDGET_GUARD_AUDIT.md`.
- **Sports staging:** `docs/CFB_CONSUMER_STAGING.md`, the NBA staging branch,
  `docs/AISP_NFL_OUTCOME_SCORING_READINESS_2026-09-17.md`.
- **Operations:** `docs/REPOSITORY_AND_BACKUP.md`, `docs/AISP_SCOTTY_DEMO_RUNBOOK_2026-09-19.md`.
- **Model Studio:** `C:\Users\james\asp-model-studio\outputs\project-management-handoff-20260915-v1\HANDOFF.md`
  (the producer's own handoff) and its `docs/` and `outputs/` study reports cited in section 7.
- **Claude's persistent notes:** `C:\Users\james\.claude\projects\C--Users-james-ai-sports-predictor\memory\`
  (index `MEMORY.md`).
