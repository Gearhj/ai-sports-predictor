# AISP NFL outcome scoring and production-consumer readiness (2026-09-17)

Branch `claude/aisp-nfl-outcome-scoring-readiness-v1`. Read-only rehearsal against the live database at
2026-09-17 13:30 ET (17:30Z). No provider was called for this work, no model was executed, and no forecast,
decision, position, settlement or bankroll was changed.

**NFL M1 has not been promoted.** Every NFL forecast in AISP today is an `NFL_M1_XGBOOST /
NFL_M1_2026_RESEARCH_SHADOW_V1` research-shadow package, some with paper-research eligibility supplements.
No Codex production handoff exists; `/research/nfl-production-status` reports `NOT_RECEIVED`.

## 1. Week 2 readiness matrix (2026 regular season, `nflverse:2026_02_*`)

All 16 games: kickoff and schedule in `America/New_York`; status `SCHEDULED`; one nflverse id and one Odds API id
each; 9–11 stored M1 snapshots (EARLY + CURRENT; FINAL_PREGAME snapshots are produced by the existing
event-gated producer shortly before kickoff and, if published before kickoff, will replace the provisional
revision below); no snapshot published at or after kickoff; one paper-research eligibility supplement each,
naming the same revision the research view currently selects; expected final source nflverse (`ingest-nfl-finals`,
hourly); no experiment position yet. **State: 16 × READY_AFTER_GOVERNED_FINAL, 0 blockers.**

The live, per-game version of this table (with full hashes) is `GET /api/v1/research/nfl-scoring-readiness?week=2`.

| # | Game (ET kickoff) | AISP game id | nflverse id / Odds API id | Controlling research revision (published → hours before kickoff) | Package / population | Paper supplement → revision | Outcome state | Automatic scoring |
|---|---|---|---|---|---|---|---|---|
| 1 | DET @ BUF (Thu Sep 17 08:15 PM ET) | `e5f9bf07-3108-4fdd-80f5-0c06d9496405` | `nflverse:2026_02_DET_BUF` / `56e8897681915f7ec92baeee952bb1ae` | CURRENT v8 (`2da1f14effb7`) · 2026-09-14T05:32 → 86.71 h · provisional | `d9c8269ce6` / `ee86cc2ac0` (10000 draws, verified) | `3fe1a49c2e` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 2 | GB @ NYJ (Sun Sep 20 01:00 PM ET) | `29f66878-2d9d-4c03-b607-9f49734116c7` | `nflverse:2026_02_GB_NYJ` / `4aeee070bb6ec60bdfae4dede61b9014` | CURRENT v9 (`4c5bc811dad2`) · 2026-09-15T05:31 → 127.47 h · provisional | `5c45928219` / `a8a2c4d794` (10000 draws, verified) | `2573a0b0a9` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 3 | PHI @ TEN (Sun Sep 20 01:00 PM ET) | `81005264-fd02-4787-a718-3aad3f6e00fc` | `nflverse:2026_02_PHI_TEN` / `fafc649c455e32a0f2ba1bd06e2039bc` | CURRENT v8 (`faa32b15cb38`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `b4d7345e5f` (10000 draws, verified) | `bab1d6e0ec` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 4 | CAR @ ATL (Sun Sep 20 01:00 PM ET) | `8ba3e58b-e02f-4ec1-be38-69ae05b75a75` | `nflverse:2026_02_CAR_ATL` / `aee7eae1849ebb103b9bf233e9741392` | CURRENT v8 (`fc46594b3bf8`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `2f93f9f1a4` (10000 draws, verified) | `5c3486bbea` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 5 | PIT @ NE (Sun Sep 20 01:00 PM ET) | `8e3b33b1-891a-41d2-9f9e-7eee2fc148fa` | `nflverse:2026_02_PIT_NE` / `9d14f87600f6edc74dbcb5c74edf6246` | CURRENT v10 (`171dd06b75d2`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `dfc528095e` (10000 draws, verified) | `d2b5817822` → CURRENT v10 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 6 | MIN @ CHI (Sun Sep 20 01:00 PM ET) | `9d26fc86-d894-4303-8a5e-9cc3990c7e14` | `nflverse:2026_02_MIN_CHI` / `0283a29e1b38ef78b29b904fd56a16dd` | CURRENT v9 (`c3dfdc684457`) · 2026-09-15T05:31 → 127.47 h · provisional | `5c45928219` / `818e27e791` (10000 draws, verified) | `4e91b57a3e` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 7 | CIN @ HOU (Sun Sep 20 01:00 PM ET) | `c6a8dbad-33fb-4b36-8fa3-bf7ef5886950` | `nflverse:2026_02_CIN_HOU` / `36ad2fa722f4915ef23d2c81351261de` | CURRENT v8 (`929f6d25780f`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `9c955d8c61` (10000 draws, verified) | `d2dad6f0c2` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 8 | CLE @ TB (Sun Sep 20 01:00 PM ET) | `e6704e33-5250-4977-ade4-fad9ad9acd7e` | `nflverse:2026_02_CLE_TB` / `0636ebff776d43f8c829a36713c80b22` | CURRENT v8 (`19acd2b4a3a6`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `69fcdc8ea8` (10000 draws, verified) | `e443b60918` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 9 | NO @ BAL (Sun Sep 20 01:00 PM ET) | `f8c8e9d0-e0cf-4013-ba69-6240313f69c9` | `nflverse:2026_02_NO_BAL` / `2143ade9684fd876b68a4e8cbf451f05` | CURRENT v8 (`1b7b662e54d9`) · 2026-09-14T05:32 → 151.46 h · provisional | `c6a67ab798` / `df3ea25688` (10000 draws, verified) | `cc6e2e3f91` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 10 | LV @ LAC (Sun Sep 20 04:05 PM ET) | `b019eb12-41a4-4be1-a229-15f68dca3a75` | `nflverse:2026_02_LV_LAC` / `71b3b83747589efdc5baef2c5cf811bf` | CURRENT v8 (`8fcfd7a86e79`) · 2026-09-14T05:32 → 154.54 h · provisional | `c6a67ab798` / `9d4f61b49a` (10000 draws, verified) | `1d45278767` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 11 | JAX @ DEN (Sun Sep 20 04:05 PM ET) | `c8b96fca-78c9-4c3c-881b-72064707676d` | `nflverse:2026_02_JAX_DEN` / `1fd6628c781f556a7a19638e97d7107f` | CURRENT v9 (`049c5abea781`) · 2026-09-15T05:31 → 130.55 h · provisional | `5c45928219` / `31e24a960b` (10000 draws, verified) | `e08b140abf` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 12 | MIA @ SF (Sun Sep 20 04:25 PM ET) | `1193932a-5ac5-49e5-9136-c8d9ac317f92` | `nflverse:2026_02_MIA_SF` / `68bc55903f50af4af4766adcc89fcc61` | CURRENT v9 (`1def133515ec`) · 2026-09-14T05:32 → 154.88 h · provisional | `c6a67ab798` / `80215bc86a` (10000 draws, verified) | `08ee31319c` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 13 | WAS @ DAL (Sun Sep 20 04:25 PM ET) | `26177ebd-1dc9-4b83-abde-eb749020191c` | `nflverse:2026_02_WAS_DAL` / `85ef85098ddd22fe4ecf45e7e3c0a9c0` | CURRENT v8 (`f7dc4e30054d`) · 2026-09-14T05:32 → 154.88 h · provisional | `c6a67ab798` / `67c41d9463` (10000 draws, verified) | `cba9ff4e4c` → CURRENT v8 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 14 | SEA @ ARI (Sun Sep 20 04:25 PM ET) | `ab584fa0-fbd6-4411-92fc-6cf8c62db96a` | `nflverse:2026_02_SEA_ARI` / `4dd8020192a544385db42374e5555303` | CURRENT v9 (`6ff5577dd878`) · 2026-09-14T05:32 → 154.88 h · provisional | `c6a67ab798` / `00d2bb9749` (10000 draws, verified) | `9ab478a023` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 15 | IND @ KC (Sun Sep 20 08:20 PM ET) | `18562ce4-a5a0-46ce-855d-67bec0070213` | `nflverse:2026_02_IND_KC` / `50bc247030707d823b9c3581d7eb5760` | CURRENT v9 (`d83357772d81`) · 2026-09-15T05:31 → 134.8 h · provisional | `5c45928219` / `bbd41e37ec` (10000 draws, verified) | `c5b256a4b7` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |
| 16 | NYG @ LAR (Mon Sep 21 08:15 PM ET) ⚑ LA/LAR alias | `313d2a19-b05e-4b9a-88ef-4f9115342d9f` | `nflverse:2026_02_NYG_LA` / `32367f882fac1a0c20a6335619922025` | CURRENT v9 (`de5e5cfd9697`) · 2026-09-14T05:32 → 182.71 h · provisional | `1538e66798` / `99a8bab649` (10000 draws, verified) | `920cad6988` → CURRENT v9 (same) | NOT_STARTED | READY_AFTER_GOVERNED_FINAL (blockers: —) |

Authority labels are kept apart: the research view is `RESEARCH_SHADOW` (the package's own status; the
probability channel's `PAPER_RESEARCH_EVIDENCE` label is shown beside it, never instead of it); the paper view
is `PAPER_RESEARCH_EVIDENCE`; the production view is empty.

Identity notes:

* **NYG @ LAR** — nflverse spells the home club `LA`; AISP holds `LAR`. Final ingestion resolves this alias
  explicitly (`same_club`), as it already did for Week 1's SF @ LAR. Not a blocker.
* **Week 1 DEN @ KC** — its paper supplement names CURRENT v7, while the research view scored FINAL_PREGAME v1.
  The two views are now scored and shown separately (paper log loss 0.963 vs research), never merged.
* nflverse publishes no status column. A game is final only when both scores appear; postponement or
  cancellation is never inferred from the feed. A game the schedule marks `postponed`, `canceled` or `live`
  is not scored even if a final observation exists.

## 2. Result source and scheduler ownership (unchanged; no scheduler added)

| Step | Job (scheduler id) | Cadence | Source | Evidence at rehearsal |
|---|---|---|---|---|
| Final ingest | `ingest-nfl-finals` (`asp-nfl-final-ingest`) | 60 min | nflverse `games.csv` (public GitHub release; no key, no credits) | last success 12:46 ET; 16 Week 1 finals reused |
| Scoring | `evaluate-nfl-research-forecasts` (`asp-nfl-research-evaluate`) | 60 min | stored forecasts and finals only | last success 12:49 ET; 16 evaluations reused |
| Settlement | `finalize-games` (`asp-finalize-games`) | 30 min | governed experiment settlement | last success 13:12 ET |
| Secondary finals | `finalize-nfl-shadow-games` | 60 min | The Odds API `/scores` | **disarmed** (`nfl_shadow_odds_enabled=False`) |

Scoring is its own job and writes only `research_forecast_evaluations`. Settlement never reads it. Within a
scoring pass each game now runs in a savepoint: a malformed forecast is recorded as
`SCORING_FAILED_ISOLATED` and the rest of the pass continues.

## 3. Scoring conventions

Game distribution (published home / away / tie probabilities; a tie is a class, never dropped):

* log loss = −ln(max(p_actual, 1e−15))
* Brier = Σ over HOME/AWAY/TIE of (p − 1[actual])², range 0–2 (summed multiclass)
* winner call = largest published probability; a TIE result is a miss for a HOME/AWAY call
* mass reconciliation: |home + away + tie − 1| ≤ 1e−6, residual recorded on every new evaluation

Scores: home, away and pooled absolute error; margin (home − away) and total error and MAE; signed errors are
predicted − actual.

Spread and total lines (read model; last stored two-sided pre-kickoff line, not a closing line): the forecast's
own paired population gives exact WIN/LOSS/PUSH masses for the HOME handicap or the OVER; the actual state is
graded from the final; the line is scored as three states — log loss −ln(p_state) and Brier Σ(p − 1)² over
WIN/LOSS/PUSH. Integer lines keep their push mass; half-point lines have none. A push is never dropped or
counted as a loss. A line with void mass is not scored.

Calibration: game-level (home-win probability, outcome) pairs are retained; binned calibration is published only
at ≥ 100 settled games. Every scorecard carries the small-sample warning.

Controlling revision: the latest snapshot per (model, model revision) whose publication time precedes kickoff.
Snapshots published at or after kickoff, replays, and snapshots without probabilities are excluded with a
reason. Revisions of one game count once. Each new evaluation records the revision's forecast id, snapshot,
model and artifact, package SHA-256, verified population SHA-256 and draw count, creation / publication /
source-cutoff times, kickoff, horizon and the reason (`LATEST_PRE_KICKOFF_PUBLICATION_PER_MODEL_IDENTITY`).

Corrections: a corrected final is a new observation (new checksum) and produces a new evaluation; the previous
one is kept and linked (`superseded_by_id`, `RESULT_CORRECTED`). Repeat ingestion and repeat scoring are
idempotent. `nfl_final_observations` is now guarded append-only.

Week 1 rehearsal (16 current evaluations, M1 research shadow): winners 10/16, log loss 0.715, Brier 0.491,
pooled score MAE 9.13, margin MAE 12.15, total MAE 12.89; 15 spread and 15 total lines scored, 0 pushes
(4 integer spreads). One week establishes nothing about skill, calibration or profitability.

## 4. Separation

Model scoring (`research_forecast_evaluations`, `/research/nfl-scorecard`), decision-policy results (shadow
experiment decisions), the virtual bankroll (experiment positions and `position_results`) and My Card remain
separate in storage, APIs and presentation. The scorecard's audit drawer links an independent settlement record
only as a reference (e.g. Week 1 DEN @ KC: SETTLED · LOSS). No price, closing line, stake or expected value is
reconstructed.

## 5. Changes

Database: none. No migration (alembic stays `20260917_0044`).

Server:

* `app/services/nfl_outcome_scoring.py` — line scoring with push, mass reconciliation, calibration gate,
  review progress (50 then 100 settled games within one frozen cohort).
* `app/services/nfl_scoring_readiness.py` — week readiness matrix and scorecard read models.
* `app/forecast_exchange/nfl_production_handoff.py` — fail-closed production-handoff adapter.
* `app/services/forecast_evaluation.py` — league and schedule-status guards, per-game savepoints, controlling
  revision and mass reconciliation recorded in `detail`.
* `app/probability/adapter.py` — `verified_nfl_population(session, row)` extracted (behaviour unchanged).
* `app/audit/immutability.py` — `NflFinalObservation` append-only.
* `app/core/config.py` — `nfl_production_handoff_path`, `nfl_production_handoff_sha256` (empty = not received).
* API: `GET /research/nfl-scorecard` (cached, warmed), `GET /research/nfl-scoring-readiness?season=&week=`,
  `GET /research/nfl-production-status`.

Client: Model Performance → NFL now leads with the NFL scorecard (coverage, small-sample warning, review
progress, plain-language metrics, predicted-vs-final chart, game rows with a collapsed audit drawer; lines, paper
view and benchmark behind a disclosure). The production label appears only for a verified
`NFL_M1_PRODUCTION_BASELINE_PROSPECTIVE_MONITORING` handoff.

## 6. What Codex must supply for a production handoff

Contract `ASP_NFL_M1_PRODUCTION_HANDOFF_V1` (AISP's proposed name — Codex may amend it; AISP will change only
the constant), UTF-8 JSON, pinned by the operator via `nfl_production_handoff_path` + `..._sha256`:

`contract`, `status` (`NFL_M1_PRODUCTION_BASELINE_PROSPECTIVE_MONITORING` | `NFL_M1_SHADOW_ONLY` |
`NFL_M1_PRODUCTION_BLOCKED`), `modelId`, `configurationId`, `modelRevision`, `modelArtifactSha256`,
`discoveryPath` (exchange-relative), `discoveryIndexSha256`, `drawCount`, `tiePolicy`, `forecastContract`,
`effectiveFromUtc`, `calibrationAuthority`, `issuedAtUtc`, `issuer`, `playLevelSubstitutionAllowed: false`;
optional `packageSha256` and `forecastPopulations` (forecast id → population SHA-256), which AISP enforces when
present. Unknown statuses, fixtures, hash mismatches, a non-M1 model (including the play-level simulator), late
forecasts, and draw-count or population mismatches are all refused.

Open questions for Codex: whether production forecasts keep the research configuration id or receive a new
one (AISP currently admits only `NFL_M1_2026_RESEARCH_SHADOW_V1`); how the discovery index is signed; whether
tie probability remains `DISCRETE_TIE_MASS_NOT_SEPARATELY_VALIDATED`.

## 7. Preproduction corrections (deployed 2026-09-17 14:16 ET, branch `claude/aisp-nfl-preproduction-corrections-v1`)

**Moneyline tie settles as a push.** `app/bankroll/settlement.determine_result` settled a moneyline tie as a LOSS
for both sides. The NFL paper policy declares `TIE_TREATMENT = STAKE_REFUNDED_PUSH` and the probability contract
prices a moneyline tie as a push, so a tie now refunds the stake with zero profit and loss, on both sides, through
the single shared rule that legacy and experiment settlement both call. MLB cannot tie (`ck_game_final_no_ties`),
spread and total grading is untouched, and no existing position was rewritten or resettled: no NFL tie has
occurred (0 settlements with equal scores). Authorized by the operator; previously staged as 005b391.

**The live channel now selects by publication time.** The NFL probability channel picked the newest snapshot by
IMPORT time. It now calls the scorer's own freeze rule (`forecast_evaluation.controlling_forecast`): the latest
snapshot of the admitted M1 model published strictly before kickoff, with version then row id as deterministic
tie-breaks only after publication time. A game whose only snapshots were published at or after kickoff is refused
rather than shown; an unverified package is refused and never substituted; the channel carries its
controlling-revision reason, publication and import times, horizon and exclusions. One implementation serves both
the scorer and the page, so they cannot disagree. MLB's channel is unchanged.

Read-only selection diff before deployment, over all 272 NFL games holding an M1 forecast: **0 selections change**,
so no probability value moves and no decision, position, settlement or evaluation is affected. Week 2 readiness
stayed 16/16 and Week 1's 16 evaluations are byte-identical before and after.

## 8. Rollback

Server: `git checkout 52c777c -- server` in the original checkout, then restart through the `ASP Backend 8010`
task. No database change to undo. Frontend: copy `.runtime/frontend-rollback/dist-52c777c` over `client/dist`.
