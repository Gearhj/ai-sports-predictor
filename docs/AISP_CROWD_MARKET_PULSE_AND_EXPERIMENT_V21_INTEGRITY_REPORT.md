# AISP Crowd & Market Pulse and $1,000 Experiment (V2.1) — Integrity Audit and Repair

- Branch: `claude/market-pulse-experiment-v21-integrity` (worktree `C:\Users\james\worktrees\aisp-market-pulse-experiment-v21`, base `91a9cb3`)
- Operational date audited: 2026-09-04 (America/New_York); database `asp` on localhost:5432 (credentials redacted; read from the root `.env`, never printed)
- Operational backend 127.0.0.1:8010 was **not** modified or restarted. All live verification of the repaired code ran on a side instance (127.0.0.1:8011, `SCHEDULER_ENABLED=false`, CORS limited to the side Vite on 5174) that makes **no provider calls** on any page load.
- Nothing in this work creates a wager, alters an opening balance, position, settlement or decision, or fabricates a quote, contract, lineup, starter or model.

**Verdict: `AISP_MARKET_PULSE_AND_V21_INTEGRITY_READY`** (see §12 for the exact grounds and the residual, honestly stated limitations).

---

## 1. Slate matrix (diagnosis, read-only)

Canonical slate for 2026-09-04 (`/today`: BASEBALL LIVE, `scheduled_event_count=16`, `valid_forecast_count=16`, `final_qualified_decision_count=0`):

| # | Game | Start (ET) | Polymarket mapping | Poly contracts | Poly ML snapshot | Kalshi mapping | Board before | Board after |
|---|------|-----------|--------------------|---------------:|------------------|----------------|--------------|-------------|
| 1 | DET@CLE (g1) | 14:10 | EXACT · SEMANTICS_REVIEW_REQUIRED | 15 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 2 | MIL@CIN | 18:10 | EXACT · review | 8 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 3 | ATL@PHI | 18:40 | EXACT · review | 8 | none (non-ML only) | none | shown, blank | MAPPED_NOT_QUOTED |
| 4 | LAA@PIT | 18:45 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 5 | BOS@BAL | 19:05 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 6 | SF@NYM | 19:10 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 7 | CHC@MIA | 19:10 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 8 | DET@CLE (g2) | 19:15 | EXACT · review | 8 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 9 | MIN@CWS | 19:40 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 10 | TB@TEX | 20:05 | EXACT · review | 8 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 11 | AZ@HOU | 20:10 | EXACT · review | 8 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 12 | TOR@KC | 20:10 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 13 | STL@COL | 20:40 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |
| 14 | NYY@SD | 21:40 | EXACT · review | 8 | none | none | **hidden** | MAPPED_NOT_QUOTED |
| 15 | WSH@LAD | 22:10 | EXACT · review | 8 | 72.5% (SEMANTICS_UNREVIEWED) | none | shown, gap −15.7 pts **claimed** | QUOTED_UNREVIEWED, gap disclosed as unreviewed |
| 16 | ATH@SEA | 22:10 | EXACT · review | 8 | none | none | shown, blank | MAPPED_NOT_QUOTED |

Totals: 16 canonical / 16 Polymarket EXACT mappings (all `SEMANTICS_REVIEW_REQUIRED`) / 0 Kalshi mappings / 1 moneyline snapshot / 0 decision-eligible / 0 consensus.

## 2. End-to-end trace and root causes (all proven on the live database)

1. **Why 10 rows, not 16.** `PredictionMarketReadService.board()` seeded its row set from `select(distinct canonical_game_id) from prediction_market_snapshots`, i.e. only games that already had *some* persisted snapshot (of any market type). Six games had never been quoted at all and were silently dropped.
2. **Why six mapped games were never quoted.** `PredictionMarketJobs._active_contracts()` ordered contracts alphabetically by `provider_market_id` and took the first `prediction_market_max_active_markets=200`. The 270 contracts belonging to the 16 slate events (16–30 per event, EXACT-mapped) were competing with ~7,000 unrelated Polymarket/Kalshi contracts; **0** of the six games' contracts fell in the top 200, so `refresh()` never requested their order books. This is a query-ordering defect, not a provider failure.
3. **Why Kalshi shows nothing.** No `KALSHI_API_KEY_ID` is configured; Kalshi order books require credentials, so every Kalshi request in `refresh()` errored (132 errors/run) while still consuming the 600/hour budget. In addition, the Kalshi adapter classified *every* market whose title lacked spread/total keywords as `FULL_GAME_MONEYLINE`: of 189 distinct persisted Kalshi series prefixes, 187 are non-full-game (KXMLBTB total bases, KXMLBHIT, KXMLBERA, KXMLBRFI, KXMLBF5*, KXMLBINNINGWIN, KXMLBTEAMTOTAL, KXMLBWINS season wins, KXNCAAF*, …); only `KXMLBGAME` (winner) and `KXMLBSPREAD` are full-game.
4. **Why WSH@LAD showed a −15.7 pt "gap".** Its Polymarket snapshot is `SEMANTICS_UNREVIEWED` (mapping semantics not approved), yet `_row()` used any latest moneyline snapshot for `polymarket_home_probability` and `diff_aisp_prediction`, and the headline card called it the "Biggest AISP–market gap".
5. **Why "ELIGIBLE_MARKETS_AVAILABLE" with 0 consensus.** `_board_state` returned that state whenever `rows` was non-empty, regardless of semantics/quality.

## 3. Honest provider states (repair)

New per-provider coverage on every row (`ProviderCoverage`): `PROVIDER_DISABLED`, `NO_MAPPED_MARKET`, `MAPPING_REVIEW_REQUIRED`, `MAPPING_REJECTED`, `MAPPED_NOT_QUOTED` (with the *exact* reason: credentials missing / refresh skipped / cap-and-budget accounting from the last refresh run), `QUOTED_UNREVIEWED`, `QUOTED_REVIEWED`. A persisted observation is always shown even if the provider flag was later turned off. A new board state `RESEARCH_OBSERVATIONS_UNREVIEWED` sits between healthy-zero and eligible; `ELIGIBLE_MARKETS_AVAILABLE` is now earned only by a reviewed, ROBUST/USABLE quote.

## 4. Coverage accounting (canonical denominators)

Board payload now carries `canonical_games` and `coverage{…}` reconciling every row to exactly one state. Live 2026-09-04 after repair: canonical 16 · quoted 1 (0 reviewed, 1 unreviewed) · listed-not-quoted 15 · no market mapped 0 · mapping review 0 · consensus 0 · semantics review pending 16. The UI strip shows `16 / 16`, `1 / 16`, `15 / 16`, `0 / 16`.

## 5. Semantic qualification (unreviewed ≠ decision-eligible)

`ProviderProbability.decision_eligible = semantics_status == SEMANTICS_MATCHED and tier ∈ {ROBUST, USABLE}`. `diff_aisp_prediction` is computed **only** from reviewed quotes; unreviewed quotes populate `diff_aisp_prediction_unreviewed` and the row is labelled `UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE`. Summary carries `biggest_disagreement` (reviewed, now `null`) and `biggest_unreviewed_gap` (0.1573) separately.

## 6. UX repair (Market Pulse)

Board renders whenever the date has canonical rows (state banner + coverage strip + table); the health-only panel remains for dates with no canonical game. Provider cells show a coverage pill instead of a bare "—"; the gap column and headline card disclose unreviewed observations as such; Quality shows `NO QUOTE` (not `UNMAPPED`) for EXACT-mapped, unquoted games; the consensus list states each provider's coverage. New exclusion labels: partial-game/derivative, other league/sport.

## 7. Refresh ordering and provider budget (repair)

`_active_contracts(event_date)` now orders by evidence value: (0) slate-mapped moneylines, (1) slate-mapped spreads/totals, (2/3) other mapped, (4/5) unmapped — providers interleaved round-robin within each tier, then capped. Live dry-run: all 200 cap slots now go to slate contracts (270 exist; moneylines first). `refresh()` skips providers that cannot answer (Kalshi without credentials) and records `skipped` on the audited run instead of burning the hourly budget on guaranteed errors. **No refresh was executed by this audit**; the next scheduled refresh on 8010 will use this ordering only once the branch is integrated and the backend restarted.

## 8. Kalshi classification (repair)

`KalshiPredictionMarketSource` now keys market type on the series family (`KX<LEAGUE>GAME|SPREAD|TOTAL`); everything else is excluded with an audited reason (`player_props`, `partial_game_or_derivative`, `futures_or_season`, `other_league_or_sport`). Against the 189 persisted prefixes: 1 MONEYLINE, 1 SPREAD, 187 excluded.

## 9. Experiment inventory and V2.1 classification

| Experiment | Policy | Status | Model (from decisions→forecasts→model_versions) | Candidate evaluations | Games | Qualified evaluations | Qualified selections | Frozen positions | Settled |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| ASP MODEL BANKROLL — 2026 V1 | AISP_OPPORTUNITY_POLICY_V1 | ACTIVE (since 2026-08-27) | asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development) | 6,646→6,710* | 113 | 0 | 0 | 0 | 0 |
| ASP PRACTICAL PREGAME BANKROLL — 2026 V2 | AISP_PRACTICAL_PREGAME_POLICY_V2 | SUPERSEDED_BEFORE_ACTIVATION | — | 0 | 0 | 0 | 0 | 0 | 0 |
| ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1 | AISP_PRACTICAL_PREGAME_POLICY_V2_1 | ACTIVE (effective 2026-09-01) | asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development) | 4,083→4,147* | 55 | 317 | 15 | 8 | 8 |

\* counts moved during the audit because the operational scheduler on 8010 kept evaluating; both values are real point-in-time reads.

**Classification: `SAME_MODEL_NEW_DECISION_POLICY`.** Every V1 and V2.1 decision references forecasts from the same `model_versions` row; the experiments differ only in `decision_policy`/`policy_config`. `controlling_engine="AISP_BASELINE"` is a label, not a model identity, and the page no longer implies otherwise.

## 10. Ledger reconciliation (V2.1, unchanged, read-only)

8 positions × $2.50 = $20.00 risked; record 3–5; P/L −$0.22; bankroll 1000.00 → 999.78 = starting + Σ profit_loss; 5 of 8 settled positions have a legitimate closing quote (62.5%); aggregate CLV −0.153% over those 5 only — missing closing quotes are not imputed. The "317 qualified decisions" previously displayed were **qualified evaluations** (repeated pregame-phase snapshots over 15 distinct selections / 34 (game, phase) pairs), never positions.

## 11. Experiment page repair

- Backend (`BankrollData`, `/experiment/comparison`): `experiment_key`, `experiment_role` (STRICT_CONTROL / POLICY_CHALLENGER), `policy_label` (derived from the real policy version → "V2.1"), `policy_relationship` ("SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model"), `model_name/version/status`, `model_identity_source`, and `ExperimentDecisionMetrics` {candidate_evaluations, games_evaluated, qualified_evaluations, qualified_selections, frozen_positions, settled_positions, evidence_stage, evidence_note}. Legacy keys are kept for compatibility.
- Client: tab/eyebrow "Practical pregame policy challenger — V2.1"; SAME MODEL badge; model identity line; four distinct decision-metric cards with `EARLY EVIDENCE` and its note; "V1 vs V2.1" comparison with Role / Forecasting model / Decision policy / Candidate evaluations (per phase) / Qualified evaluations / Qualified selections (distinct) / Frozen positions; funnel labelled by the evaluated policy version; V1 unchanged.
- `practical_policy.py` reason strings that begin "V2 …" were deliberately **not** changed: they are persisted into immutable decision records.

## 12. Verdict grounds and residual limitations

Ready because: every canonical game is now a row with an honest state; no unreviewed quote can be claimed as a gap or as eligibility; the refresh-ordering defect that starved slate games is fixed and proven on live data; Kalshi props can no longer be labelled moneylines; the experiment page states the true V2.1 identity (same model, different policy) with non-conflated metrics; all immutable ledger evidence is untouched; 187 server + 131 client tests pass (see §14).

Not claimed: (a) Polymarket quotes for the 15 unquoted games will appear only after the next refresh runs the new ordering on the integrated backend — this audit did not trigger a refresh; (b) semantics review for all 16 mappings still requires human approval via the admin endpoint (`semantics_review_pending=16`); (c) Kalshi will remain `MAPPED_NOT_QUOTED`/`NO_MAPPED_MARKET` until API credentials are configured by James (never in chat) — the page now says exactly that; (d) the V2.1 evidence stage is EARLY_EVIDENCE (8 settled) and no performance claim is made.

## 13. Current-day funnel (2026-09-04, V2.1, read-only)

16 scheduled → 16 moneyline → 16 valid forecast → 16 two-sided quote → 16 fresh → 16 exact identity → **1** starter confirmed/probable-stable (14 blocked: starter observations older than 240 min at cutoff) → 1 → 1 → 1 → 1 critical-complete → 0 adjusted edge ≥ 2.0 (−3.1 pts) → 0 qualified → 0 positions. All 16 await final-pregame evaluation; a no-position day is an honest result and nothing was created.

## 14. Tests and acceptance

- Server: `tests/test_market_pulse_canonical_coverage.py` (13: canonical seeding, empty date, listed-not-quoted, Kalshi credential reason, disabled-provider/persisted-quote, unreviewed gating, reviewed eligibility, refresh ordering, refresh skip accounting, 10 series-family cases, exclusion reasons + normalizer refusal, no-provider-call guard) and `tests/test_experiment_identity_v21.py` (3: same-model identity, distinct metrics + comparison, honest empty identity). Full suite: see §15.
- Client: `MarketPulsePage.coverage.test.tsx` (4) and `BankrollPage.identity.test.tsx` (5); `BankrollPage.v2.test.tsx` fixture moved to the real V2.1 identity. Full suite 131 tests: 125 pass in the parallel run; the 6 parallel-run failures (`App.*`, `phase15.*`) all pass when run in isolation (29/29) and are outside the audited pages; one of them (`phase15 … unknown demo id`) also fails on the pristine primary, i.e. pre-existing.
- Browser acceptance on the side stack (5174 → 8011): Market Pulse and `/experiment?experiment=v2` at **1920×1080** and **1024×768** — 16 rows, banner `RESEARCH OBSERVATIONS — SEMANTICS UNREVIEWED`, coverage strip 16/16 · 1/16 · 15/16 · 0/16, WSH@LAD 72.5% "unreviewed", headline gap "—" with the unreviewed disclosure; experiment page eyebrow/tab/badge/model line/decision metrics/"V1 vs V2.1"; `document.body.scrollWidth` 1009 ≤ 1024 (no horizontal scroll). Network log confirms only `/api/v1/*` reads to 8011 — no provider host.

## 15. Performance (live DB, read-only, warm)

| Endpoint | Before | After |
|---|---|---|
| board (16 rows) | 1.9–2.2 s for 10 rows | 2.2 s, 162 queries (was 223 for 16 rows before batching mappings/contracts/snapshots/teams/consensus/alerts/league) |
| bankroll v1 / v2 | 0.8 s / 1.9 s | 1.3 s / 2.4 s (+2 identity/metric queries each) |
| experiment/comparison | 2.3 s | 3.8 s, 18 queries |
| funnel v1 / v2 | 0.8 s / 0.9 s | unchanged (583 / 728 queries — pre-existing N+1, out of scope, reported) |

Remaining board cost is ~10 queries/row inside the shared forecast/sportsbook read model (`_latest_forecast`, `_market_context`) and movement history; not changed here.

## 16. Monitoring

Existing observer job/ingestion-run evidence is reused; `refresh` runs now record `skipped` per provider, and the board's `MAPPED_NOT_QUOTED` reason surfaces the last refresh's requests/errors/cap so a starved slate is visible on the page itself. No new scheduler job was added.

## 17. Commits (this branch, not pushed)

1. `01444ef` fix(market-pulse): seed the board from the canonical slate with honest per-provider coverage
2. `3763140` fix(observer): quote slate-mapped contracts first and stop mislabelling Kalshi props as moneylines
3. `2f3c2d0` fix(experiment): name the V2.1 policy challenger honestly — same model, different decision policy
4. (this document) docs: Crowd & Market Pulse and V2.1 integrity report

Full server suite in the worktree: every test passes except 6 in `tests/test_gate3c_x1_contracts.py`, which require the git-ignored `data/curated/gen2-fd1` directory that exists only under the primary checkout (the same file passes 17/17 on the primary) — an environment dependency, not a regression.
