# AISP Market Pulse + Experiment V2.1 — Operational Integration Record

Date: 2026-09-04 (America/New_York). Operator: Claude Fable 5.1, non-elevated session as `james`.
Repository: `C:\Users\james\ai-sports-predictor`. Nothing was pushed. `main` was not touched.

## 1. Primary branch / HEAD
- Before: `codex/multisport-platform-architecture` @ `91a9cb3` (clean; untracked only `docs/MONTE_CARLO_SIMULATION_THEATER.md`, `install-supervision.ps1` — both preserved)
- After: `codex/multisport-platform-architecture` @ `b58018c` (+ this report's commit, see §20)

## 2. Completed branch / HEAD
`claude/market-pulse-experiment-v21-integrity` @ `b58018c` (worktree `C:\Users\james\worktrees\aisp-market-pulse-experiment-v21`, clean). Base `91a9cb3` = primary HEAD before → clean fast-forward ancestry (`git merge-base --is-ancestor` true).

## 3. main HEAD
Before `92f92dd` · After `92f92dd` (unchanged).

## 4. Integration method
`git merge --ff-only claude/market-pulse-experiment-v21-integrity` in the primary. No merge commit, no conflict, no reset/stash/clean/force. Pre-checks: no Git locks; no other agent process referencing the repo; 8 worktrees enumerated, none dirty on this branch; staged = none; source migration head `20260831_0024` = live `alembic_version` (no migration needed).

## 5. Commits integrated
1. `01444ef` fix(market-pulse): seed the board from the canonical slate with honest per-provider coverage
2. `3763140` fix(observer): quote slate-mapped contracts first and stop mislabelling Kalshi props as moneylines
3. `2f3c2d0` fix(experiment): name the V2.1 policy challenger honestly — same model, different decision policy
4. `b58018c` docs: Crowd & Market Pulse and $1,000 Experiment V2.1 integrity report

## 6. Test results (run in the primary after fast-forward)
- Server focused: `test_market_pulse_canonical_coverage.py`, `test_experiment_identity_v21.py`, `test_prediction_market_observer.py`, `test_qualification_and_pulse_states.py`, `test_bankroll.py`, `test_shadow_experiment.py`, `test_practical_policy_v2.py` — all passed (exit 0).
- Client: `tsc --noEmit` exit 0; focused vitest (`MarketPulsePage.coverage`, `MarketPulsePage`, `BankrollPage.identity`, `BankrollPage.v2`, `BankrollPage`) 24/24 passed; `vite build` succeeded (built in 15.2 s).
- `git diff --check` clean.
- Integration performed zero provider calls and zero writes to protected tables (§15–16).

## 7. Operational backend PID
- Before: uvicorn PID **52316** (venv launcher 50208), listening 127.0.0.1:8010, started 2026-09-03 21:34:01, running pre-integration code (`91a9cb3`).
- After: uvicorn PID **39084** (launcher 46964), started 2026-09-04 14:49:52, running `b58018c`. Exactly one listener on 8010 before and after.

## 8. Scheduled-task status (`ASP Backend 8010`)
Registered (principal `james`, S4U, action `cmd.exe /c start-backend.cmd`, wd = primary). Before: State=Running, LastRun 2026-09-03 21:34:34, LastResult 267009. Non-elevated `Stop-ScheduledTask` and `Start-ScheduledTask` both returned success (LastRun advanced to 14:48:48, State=Running) **but the task's wrapper never executed**: no task-owned `cmd.exe` appeared, and the wrapper log (`%LOCALAPPDATA%\asp\backend.log`, which every wrapper appends to) recorded no "starting uvicorn"/"uvicorn exited" lines from it. The task is therefore registered but non-functional under S4U (the `.cmd` wrapper needs a console/CRLF-safe launch). **Effective supervision is the legacy wrapper loop** `cmd.exe /c start-backend.cmd` PID 23244 (launched 2026-09-03 13:43 from a prior session's bash), which relaunches uvicorn from the primary checkout on exit.
Restart method used: stopped only the uvicorn process (52316) so the existing loop 23244 relaunched it — no new launcher was created. Exact administrator command still required to move supervision onto the task (elevated PowerShell):
`powershell -ExecutionPolicy Bypass -File "C:\Users\james\AppData\Local\asp\install-supervision.ps1" -StartNow`
(then verify a task-owned wrapper writes to backend.log; if it still does not, the wrapper must be converted to a console-less `.ps1` action — a separate task).

## 9. Health / readiness
`/healthz` 200; `/readyz` `{"status":"ready","database":"reachable","scheduler":"running"}` within ~4 s of relaunch and again at the end of acceptance.

## 10. Frontend status
Vite dev server PID 32840 on 5173 serving `C:\Users\james\ai-sports-predictor\client` (HMR picked up the fast-forwarded files; network log shows `MarketPulsePage.tsx?t=1788547565434` served fresh). Not restarted; no duplicate Vite instance. Production build verified separately (§6).

## 11. Crowd & Market Pulse acceptance (real 5173 → 8010, date 2026-09-04)
1920×1080 and 1024×768: 16 rows = canonical 16; matchups DET@CLE, MIL@CIN, ATL@PHI, LAA@PIT, BOS@BAL, SF@NYM, CHC@MIA, DET@CLE, MIN@CWS, TB@TEX, AZ@HOU, TOR@KC, STL@COL, NYY@SD, WSH@LAD, ATH@SEA (the six formerly hidden games — DET@CLE ×2, MIL@CIN, TB@TEX, AZ@HOU, NYY@SD — present as MAPPED_NOT_QUOTED). Banner "Research observations — semantics unreviewed". Coverage strip: Canonical 16/16 · Quoted 1/16 (0 reviewed · 1 unreviewed) · Listed, not quoted 15/16 (0 no market mapped · 0 mapping review) · Consensus 0/16. WSH@LAD row: 72.5% "unreviewed", gap "-15.7 pts unreviewed", status "UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE", consensus "No consensus"; API `diff_aisp_prediction=null`, `diff_aisp_prediction_unreviewed=-0.1573`, `consensus_eligible=false`. Headline gap card "—" with the unreviewed disclosure. Network: only `127.0.0.1:8010/api/v1/*` reads (registry, public/config, board) — no provider host. Console errors: none. `documentElement.scrollWidth` 1905 ≤ 1920 and 1009 ≤ 1024.

## 12. V1 experiment acceptance (`/experiment?experiment=v1`)
Tab "Strict control — V1" selected; eyebrow "THE OFFICIAL ASP EXPERIMENT · STRICT CONTROL — V1"; strip "ASP MODEL BANKROLL — 2026 V1 · ID 13604fc0… · Policy AISP_OPPORTUNITY_POLICY_V1 · Model asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development)"; $1,000.00 / 0 open / 0 settled; metrics 7020 candidate evaluations / 113 games / 0 qualified / 0 frozen / NO EVIDENCE; funnel V1 STRICT CONTROL; 9 daily-decision rows; audit metadata V1; console errors none.

## 13. V2.1 experiment acceptance (`/experiment?experiment=v2`)
Tab "Practical pregame policy challenger — V2.1"; eyebrow "PRACTICAL PREGAME POLICY CHALLENGER — V2.1 · SAME MODEL · DIFFERENT DECISION POLICY"; badge "SAME MODEL · DIFFERENT DECISION POLICY"; strip ID 43fe1b16… · Policy AISP_PRACTICAL_PREGAME_POLICY_V2_1 · Model asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development) · identity from decisions→forecasts→model_versions; bankroll $999.78; 0 open / 8 settled; risked $20.00 (8 positions); return −$0.22 (ROI −1.1%); closing-line coverage 62.5% (5 of 8); metrics 4489 candidate evaluations / 55 games / 317 qualified evaluations / 15 distinct selections / 8 frozen / **EARLY EVIDENCE**; "V1 vs V2.1" comparison rows (model identical on both sides, policies differ, 113/55, 7020/4489, 0/317, 0/15, 0/8, record 3–5, P/L −$0.22, CLV −0.153%); funnel "V2.1 PRACTICAL POLICY CHALLENGER · SAME MODEL" with V2.1-labelled steps (16 → … → 3 critical-complete → 0 qualified → 0 positions); latest settled + full history (8 positions through TOR ML 2026-09-01); audit metadata V2.1. Switching to V1 changed heading/identity/bankroll and dropped the URL param; F5 on `?experiment=v2` preserved V2.1. Console errors none at both viewports. (Candidate-evaluation counts differ from the earlier audit's 4,083/4,147 because the operational scheduler keeps evaluating; every other figure reconciles.)

## 14. Ledger reconciliation (read-only, V2.1)
DB: 8 positions, Σ stake $20.00, Σ profit_loss −$0.22, 3 WIN / 5 LOSS, 5 closing-line observations — identical before and after integration and restart, and identical to the page.

## 15. Provider-call count
0 by this task. No refresh/discover/finalize job was invoked; no admin endpoint was called; page loads issued only backend API reads. (Refresh runs in DB: 620 before → 620 after; the operational scheduler had not yet reached its next refresh interval by the end of acceptance.)

## 16. Database-write counts by protected table (this task)
| Table | Before | After | Written by this task |
|---|---:|---:|---:|
| shadow_experiments | 3 | 3 | 0 |
| official_asp_positions (experiment) | 8 | 8 | 0 |
| position_results (settlements) | 8 | 8 | 0 |
| position_results.closing_line_value (non-null) | 5 | 5 | 0 |
| prediction_market_snapshots | 62,912 | 62,912 | 0 |
| prediction_market_mappings (SEMANTICS_REVIEW_REQUIRED / MATCHED) | 928 / 14 | 928 / 14 | 0 |
| shadow_experiment_decisions | 11,449 | 11,509 | 0 — the +60 were written by the operational scheduler's `freeze-shadow-experiment` job (job 7e1b3aff-bf48-4c0d-8842-21e4006b161b, "frozen": 15 …) on its normal cadence |

## 17. Remaining semantic-review count
Board for 2026-09-04: `semantics_review_pending = 16` (all 16 Polymarket mappings); repository-wide 928 mappings at SEMANTICS_REVIEW_REQUIRED. None approved by this task.

## 18. Remaining listed-not-quoted count
15 of 16 (MAPPED_NOT_QUOTED, Polymarket); 1 QUOTED_UNREVIEWED (WSH@LAD); 0 QUOTED_REVIEWED; consensus eligible 0; Kalshi: NO_MAPPED_MARKET on all 16 (enabled, no credentials, not configured by this task); PROVIDER_DISABLED 0.

## 19. Files changed by the integration
Exactly the 17 files of the four commits (server: schemas/api.py, schemas/prediction_market.py, prediction_markets/{read_service,jobs,kalshi}.py, services/{read_service,qualification_service}.py, tests/test_market_pulse_canonical_coverage.py, tests/test_experiment_identity_v21.py; client: pages/{MarketPulsePage,BankrollPage}.tsx, components/QualificationFunnel.tsx, types/domain.ts, styles.css, pages/{MarketPulsePage.coverage,BankrollPage.identity,BankrollPage.v2}.test.tsx; docs/AISP_CROWD_MARKET_PULSE_AND_EXPERIMENT_V21_INTEGRITY_REPORT.md) plus this report.

## 20. Commit created
`docs: operational integration record for Market Pulse + V2.1 repairs` on `codex/multisport-platform-architecture` (hash in `git log -1`).

## 21. Pushes performed
None.

## 22. Exact next recommended task
Run, in an **Administrator** PowerShell, `powershell -ExecutionPolicy Bypass -File "C:\Users\james\AppData\Local\asp\install-supervision.ps1" -StartNow`, then confirm within 60 s that `backend.log` gains a task-written "starting uvicorn on 8010" line and that the 8010 listener's parent chain ends in the Task Scheduler (not bash 37336 / cmd 23244); if the task's wrapper still does not execute under S4U, replace the task action with a PowerShell launcher. Only after supervision is proven, review and approve the 16 pending Polymarket semantics via the admin endpoint (human decision), and let the scheduler's next refresh — now slate-first — quote the 15 listed games.
