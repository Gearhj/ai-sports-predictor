# AISP MLB Pregame Freshness — Operational Integration Record

Date: 2026-09-04 (America/New_York) · Operator: Claude Fable 5.1, non-elevated session as `james` · Nothing pushed · main untouched.

## 1–4. Branches, HEADs, git status
- Primary `C:\Users\james\ai-sports-predictor`, branch `codex/multisport-platform-architecture`: **before 44076e4 → after 951fa4e** (+ this record's commit).
- Completed: worktree `C:\Users\james\worktrees\aisp-pregame-evidence-freshness`, branch `claude/pregame-evidence-freshness-integrity` @ `951fa4e`, clean.
- main: `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.
- Status before: primary clean apart from the preserved untracked `docs/MONTE_CARLO_SIMULATION_THEATER.md` and `install-supervision.ps1`; staged 0; no Git locks; no other agent process referencing the repo; 9 worktrees enumerated. After: identical (plus this record).
- Environment: backend 127.0.0.1:8010, Vite 5173 (PID 32840, serving the primary client), PostgreSQL `asp` (`postgresql+psycopg://[REDACTED_CREDENTIALS]@localhost:5432/asp`), migration head `20260831_0024` (source = live; no migration in the branch).

## 5–6. Integration method and commits
`git merge --ff-only claude/pregame-evidence-freshness-integrity` (44076e4 was an ancestor; 19 files, +1,251/−29; `git diff --check` clean). Commits integrated, oldest first:
1. `1d67bf0` fix(pregame): correct starter and lineup as-of authority
2. `49ec2c0` fix(pregame): make refresh cadence and final-phase freezing reliable
3. `9bd7671` fix(experiment): consume fresh pregame authority deterministically
4. `aeeddb2` feat(ui): expose concise pregame freshness evidence and monitor the pipeline
5. `40d5d73` test: prove cutoff and event-state integrity for pregame evidence
6. `4e5af73` docs: record pregame freshness audit
7. `8091da1` test: align shadow-experiment fixtures with the final freeze window
8. `951fa4e` docs: full-suite classification addendum for the pregame freshness audit

## 7. Tests (run in the primary after fast-forward, before restart)
- Backend: `test_pregame_evidence_freshness.py` (14) + `test_practical_policy_v2.py`, `test_shadow_experiment.py`, `test_qualification_and_pulse_states.py`, `test_bankroll.py`, `test_experiment_identity_v21.py`, `test_gate2_lifecycle_read_service.py`, `test_market_pulse_canonical_coverage.py` — all passed (exit 0).
- Client: `tsc --noEmit` clean; 39 tests across 5 affected files passed; `vite build` succeeded (10.3 s).
- Behavioral proofs reproduced on the live database with the integrated code: (1) reference before cutoff = evaluation instant; (2) reference after cutoff = cutoff; (3) `created_at − available_as_of` = 0.04 s on the latest row and is never consulted; (4) observations consulted = exactly those with `available_as_of ≤ reference` (33 of 33 for WSH@LAD home); (5) WSH@LAD home evidence: FRESH 26.4 min at the instant vs 123.2 min if aged against its 21:55 cutoff; (6) heartbeat bounded/idempotent — unit-proven and now live (§21); (7) DET@CLE g1/g2 distinct ids, 66/66 observations each; (8) funnel games 16, distinct 16, reconciliation sums to 16; (9) genuine stale stays blocked — unit-proven (no genuinely stale game exists on this slate); (10) constants unchanged: current 240 min, stable observations 2, spacing 10 min, cutoff 15 min, final window 15 min, heartbeat 10 min; (11) V1 funnel 16 scheduled / 0 qualified / 0 positions, policy edge 0.02, readiness 70, cutoff 15 — unchanged; (12) V2.1 policy `AISP_PRACTICAL_PREGAME_POLICY_V2_1` checksum `7e439d4b…`, both experiments' decisions resolve to `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (DEVELOPMENT)`; (13)/(14) page loads: only `127.0.0.1:8010/api/v1/*` requests, decisions/positions unchanged (§20).

## 8–11. Backend restart, revision, frontend, health
- Topology verified: 8010 listener uvicorn PID 39084 (started 14:49:52, pre-integration code) ← venv launcher ← `start-backend.cmd` loop PID 23244 (the effective supervisor; the scheduled task shows Running/267009 but owns no wrapper). Restart method: stopped only the uvicorn process; loop 23244 relaunched from the primary checkout ("uvicorn exited … restarting" / "starting uvicorn on 8010" at 16:18:39). No launcher created; supervision not modified.
- After: PID **52076**, started 16:18:39 (after the 16:18:38 fast-forward), command line `python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8010`, exactly one listener, one uvicorn process. Revision proof (no build-commit endpoint exists): the live `/experiment/qualification-funnel?policy=v2` now returns `starter_evidence_reconciliation` and 16/16 `pregame_evidence` blocks, and `/dashboard/today` carries 16 `pregame_evidence` blocks — fields that exist only from `9bd7671`/`aeeddb2`.
- `/healthz` 200; `/readyz` `{"status":"ready","database":"reachable","scheduler":"running"}` ~4 s after relaunch and at the end of acceptance.
- Frontend: not restarted; Vite 5173 served the integrated modules (`PregameEvidenceBadges.tsx`, `QualificationFunnel.tsx?t=…`) on load; no duplicate Vite.

## 12. Operational 16-game matrix (integrated code, live DB, reference 16:17 ET)
| Event id | Matchup | Cutoff ET | Latest eligible starter obs ET | Reference ET | Age @reference | Starter state (home/away) | Lineup state | V2.1 starter stage | Current final reason |
|---|---|---|---|---|---:|---|---|---|---|
| 7fb33822 | DET@CLE g1 | 13:55 | 13:35 | 13:55 (cutoff) | 19.6 m | PROBABLE_STABLE / PROBABLE_STABLE | CONFIRMED_COMPLETE | PASS | Latest quote is stale (fresh_quote gate) |
| 673fcdd6 | MIL@CIN | 17:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | CONFIRMED_COMPLETE | PASS | Adjusted edge −2.3 pts below 2.0 |
| 57e3d36e | ATL@PHI | 18:25 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | CONFIRMED_COMPLETE | PASS | Adjusted edge −1.3 pts |
| 6aec2378 | LAA@PIT | 18:30 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −0.3 pts |
| 995738ef | BOS@BAL | 18:50 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | CONFIRMED_COMPLETE | PASS | Adjusted edge −1.5 pts |
| faba1dc2 | SF@NYM | 18:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | CONFIRMED_COMPLETE | PASS | provisionally qualified (no gate failed) |
| 49d7b0a4 | CHC@MIA | 18:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −1.5 pts |
| c9ce9242 | DET@CLE g2 | 19:00 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −1.8 pts |
| 4657322d | MIN@CWS | 19:25 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −2.7 pts |
| f598dffd | TB@TEX | 19:50 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | provisionally qualified |
| 56043d23 | AZ@HOU | 19:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge +0.7 pts below 2.0 |
| a735ef7b | TOR@KC | 19:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −0.3 pts |
| 6c53a13a | STL@COL | 20:25 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −3.4 pts |
| 0e5d234a | NYY@SD | 21:25 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Adjusted edge −1.9 pts |
| 2bba6294 | WSH@LAD | 21:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | Extreme model–market disagreement — review required |
| 93a3205f | ATH@SEA | 21:55 | 15:51 | 16:17 | 25.9 m | STABLE / STABLE | PENDING_ALLOWED | PASS | provisionally qualified |

## 13–15. Reconciliations and funnel before/after
- Starter states: PASS 16 / STALE 0 / MISSING 0 / UNSTABLE 0 / CONFLICT 0 / EVENT 0 / NOT_EVALUATED 0 — sums to 16; every game once. (Latest observation 15:51 ET rather than the audit's 14:35 because the 30-minute schedule refresh ran again; ages 19.6–25.9 min; legitimately all fresh.)
- Lineups: CONFIRMED_COMPLETE 5 (DET@CLE g1, MIL@CIN, ATL@PHI, BOS@BAL, SF@NYM), PENDING_ALLOWED_FOR_MONEYLINE 11 (source: home/away CONFIRMED or NOT YET PUBLISHED as shown on the cards); 0 partial-blocking, 0 conflicts.
- V2.1 funnel on 8010 **before integration (old code, 16:1x ET)**: starters eligible 9, lineup 9, qualified 2; top blockers "Adjusted EV not positive" (7) and "Starter evidence not CONFIRMED/PROBABLE_STABLE … not current at the decision cutoff" (5); no reconciliation field. **After restart (integrated code)**: starters eligible 15 (the 16th, DET@CLE g1, is PASS but failed the earlier fresh-quote gate), lineup 15, critical 14, adjusted edge 3, provisional qualified 3, positions 0; reconciliation PASS 16; blockers only adjusted EV/edge, stale quote, extreme disagreement. No false future-cutoff staleness remains in any live evaluation; the string "not current at the decision cutoff" appears once on the V2.1 page, inside the immutable "Latest daily decisions" reason text of historical rows frozen by the old code (correctly not rewritten).

## 16–18. V1, V2.1 identity, ledger
V1 untouched (funnel 16/0/0; policy report unchanged; no V1 file in the branch beyond shared test fixtures). V2.1 policy id/checksum unchanged; both experiments on `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b`. V2.1 ledger from persistence before and after: 8 positions, $20.00 risked, −$0.22, 3 W / 5 L, 5 closing-line observations, bankroll $999.78 (page).

## 19–21. Provider calls, protected writes, scheduler writes
- Provider calls by this task: **0** (integration, tests, restart and page loads; browser network logs show only `127.0.0.1:8010` and `5173`; no provider host).
- Protected writes by this task (baseline → final): experiments 3→3, positions 8→8, results 8→8, CLV 5→5, model_versions 2→2, forecasts 287→287, decisions 12,001→12,001, PM refresh runs 628→628, pregame alerts 0→0. Zero of each.
- Scheduler-appended writes since restart (operational, integrated code): 1 `poll-availability-events` MLB ingestion run (11 starter heartbeats, 14 lineups persisted) and 22 heartbeat starter observations — the repaired poll operating as designed; no decision rows yet (fingerprint-deduplicated MORNING snapshots; FINAL rows only inside each game's final window from 17:40 ET onward). Two `MLB request failed: ConnectTimeout` poll failures in the log are timestamped 16:48Z/17:16Z (12:48/13:16 ET) — the previous process, before integration.

## 22. Browser acceptance (real 5173 → 8010, 1920×1080 and 1024×768)
- Daily Command Center: 16 games across 19 cards; badges "Home/Away starter PROBABLE · observed 20–28m before reference · FRESH", "Lineup: home CONFIRMED · away NOT YET PUBLISHED" etc.; block reasons in badge tooltips (full expandable detail on the funnel page); FRESH/STALE/MISSING classes distinct; `scrollWidth` 1905/1009 ≤ viewport; console errors none; requests only to 8010.
- V2.1 experiment: tab "Practical pregame policy challenger — V2.1", badge SAME MODEL · DIFFERENT DECISION POLICY, funnel eyebrow V2.1, reconciliation line "pass 16 (sums to 16)", 16 inspectable games each with expandable evidence detail, starter stage 15, qualified 3 provisional, positions 0; $999.78 / 0 open / 8 settled / 3–5 / 62.5% CLV coverage unchanged; console none.
- Shadow Board: "Probable pitcher changed…" and "Lineup status None → CONFIRMED" events with before/after cards, "Live availability rescores 11", NO_BET framing and "not executable" disclosure; no event shown as a wager; history unchanged.
- Regression: Market Pulse 16 rows, Market Board 126 rows, Model Performance, Historical Research, V1 experiment ("Strict control — V1", ASP MODEL BANKROLL — 2026 V1) all open; no overflow; no console errors.

## 23. Performance (measured, not repaired)
| Endpoint | Warm latency | Size | Queries |
|---|---|---|---|
| `/experiment/qualification-funnel?policy=v1` | 0.87–0.95 s | 8 KB | 587 |
| `/experiment/qualification-funnel?policy=v2` | 0.93–1.05 s | 44 KB | 732 |
| `/dashboard/today` (Daily Command Center) | 1.74–1.77 s | 164 KB | — |
| `/today` | 1.73–2.14 s | 38 KB | — |
The ≈45-queries-per-game N+1 in both funnels is pre-existing and remains an explicit next task.

## 24–26. Files, commits, pushes
Files: the 19 of the fast-forward (server `evidence_states.py`, `practical_policy.py`, `shadow_experiment_service.py`, `jobs/orchestrator.py`, `qualification_service.py`, `read_service.py`, `schemas/api.py`, `pregame_evidence.py`, `pregame_evidence_monitor.py`, tests ×3; client `PregameEvidenceBadges.tsx` + test, `QualificationFunnel.tsx`, `OpportunityCard.tsx`, `types/domain.ts`, `styles.css`; docs) plus this record. Commits created here: this record only. Pushes: none.

## 27. Remaining issues
1. Funnel N+1 (587 / 732 queries) — next task.
2. Supervision still rests on the legacy `start-backend.cmd` loop; the scheduled task remains non-functional (documented in the prior integration record; out of scope here).
3. Heartbeat (10 min) and final-window (15 min) constants live in code; recording them in a future V2.1 policy version is James's decision.
4. Historical decision rows frozen by the old code retain their "not current at the decision cutoff" reason text (immutable by design).

## 28. Exact recommended next task
Replace the per-game N+1 in `QualificationService.funnel` / `funnel_v2` (and the shared card/forecast/opportunity reads they call) with set-based prefetches keyed by game id — target ≤ 40 queries per funnel — with a query-count regression test, without touching policy thresholds or evidence semantics.
