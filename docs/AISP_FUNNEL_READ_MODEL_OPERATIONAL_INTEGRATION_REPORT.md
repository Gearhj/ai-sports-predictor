# AISP Funnel Set-Based Read Model — Operational Integration Record

Date: 2026-09-04 (America/New_York) · Operator: Claude Fable 5.1, non-elevated session as `james` · Nothing pushed · main untouched · Provider calls by this task: 0.

## 1–4. Branches, HEADs, git status
- Primary `C:\Users\james\ai-sports-predictor`, branch `codex/multisport-platform-architecture`: **before `9df5c0f59913afcd4f40e3b42c8db7c1076230cc` → after `0ee7146a10fc2a8dfd394ac7f4f73217d640576e`** (+ this record's commit).
- Completed: worktree `C:\Users\james\worktrees\aisp-funnel-read-model-v1`, branch `claude/experiment-funnel-set-based-read-model` @ `0ee7146a10fc2a8dfd394ac7f4f73217d640576e`, clean.
- main: `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.
- Status before: primary clean except the preserved untracked `docs/MONTE_CARLO_SIMULATION_THEATER.md` and `install-supervision.ps1`; staged 0; no Git locks; no other agent process; 10 worktrees enumerated. After: identical plus this record. Migration head `20260831_0024` = live (no migration in the branch). DB `postgresql+psycopg://asp_user:<redacted>@localhost:5432/asp`.

## 5–6. Integration
`git merge --ff-only claude/experiment-funnel-set-based-read-model` (9df5c0f… was a direct ancestor; 8 files, +1,114/−80; `git diff --check` clean). Commits integrated (full hashes verified):
1. `0ce502a23f7295f293ef1ee027ba3421fb10609f` perf(experiment): add set-based funnel evidence reader
2. `b011603eb4e38e60c420a2f1cf44fafe6143b149` refactor(experiment): evaluate policies from prefetched context
3. `6fe4a9ca67c42949ebb67705f39f648651c684bd` test(experiment): enforce query-count and equivalence contracts
4. `7b55655a9900168ad592c044638081d7e069b53d` docs: record funnel read-model evidence
5. `0ee7146a10fc2a8dfd394ac7f4f73217d640576e` docs: full-suite classification addendum for the funnel read-model report

## 7–8. Phase 1 verification (completed branch) and post-integration tests
- Focused backend tests on the completed branch (`test_funnel_read_model`, `test_experiment_identity_v21`, `test_pregame_evidence_freshness`, `test_shadow_experiment`, `test_practical_policy_v2`, `test_qualification_and_pulse_states`, `test_bankroll`, `test_gate2_lifecycle_read_service`, `test_performance_read_service`): all passed (exit 0). Re-run in the integrated primary after fast-forward: all passed.
- Live read-only reproduction with the completed branch's code, same DB: V1 587 → 26 statements, V2.1 732 → 26 (2026-09-04, 16 games); 326/390 → 27 (2026-09-03, 9 games); legacy-vs-set-based responses equivalent on both dates with a fixed `now`; dashboard 33 statements; walking all 16 games through cards, forecasts, opportunities and serialization with the context attached issued 4 statements — the fixed per-request reads inside `_daily_opportunities` (odds freshness ×2, experiment-status map ×2), none per game/team/selection. Marker `AISP_FUNNEL_SET_BASED_READ_MODEL_V1`, `prefetch_statements` 20–21.
- Client `tsc --noEmit` clean; `vite build` succeeded (10.0 s); `git diff --check` clean.

## 9–11. Backend restart, revision, health
- Topology verified: 8010 listener uvicorn PID **52076** (started 16:18:39, pre-integration) ← venv launcher ← `start-backend.cmd` loop PID 23244 (effective supervisor; scheduled task shows Running/267009 but owns no wrapper). Restart method: stopped only the uvicorn process; the loop relaunched from the primary checkout ("uvicorn exited … restarting" / "starting uvicorn on 8010" at 17:37:43). No launcher created, supervision unchanged, frontend (Vite 5173, PID 32840) not restarted.
- After: PID **46964**, started 17:37:43 (after the 17:3x fast-forward), command line `python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8010`, exactly one listener, one uvicorn process. Revision proof: the live funnels return `read_model.version = AISP_FUNNEL_SET_BASED_READ_MODEL_V1` — a field that exists only from `b011603`.
- `/healthz` 200; `/readyz` 200 `{"status":"ready","database":"reachable","scheduler":"running"}` ~4 s after relaunch and again at the end.

## 12–16. Live read model, query counts, complexity
- Live 8010 responses: V1 and V2.1 carry `read_model.version = AISP_FUNNEL_SET_BASED_READ_MODEL_V1`, `prefetch_statements = 20`, `games = 16`, `evidence_watermark = 2026-09-04T17:34:51-04:00`; V2.1 `policy = AISP_PRACTICAL_PREGAME_POLICY_V2_1`, starter-stage 15, reconciliation PASS 16; `/bankroll?experiment=v2` model `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b`.
- Statement counts (integrated primary code, same DB, engine counter): V1 **587 → 26**, V2.1 **732 → 26** on 16 games; 26/26 on 7 games (2026-08-27) and 26/26 on 17 games (2026-08-29, doubleheader) — constant, not proportional to game count. Dashboard **515 → 33**.

## 17–19. Behavioral equivalence and temporal integrity
Live pre-integration responses (old process 52076) saved for V1/V2.1 on 2026-09-04 and 2026-09-03, compared with the live post-integration responses (new process 46964) excluding only wall-clock fields (`reference_at`, ages, quote ages, `read_model`): **0 differences** on all four — identical game order and count, stage counts, starter/lineup states, quote state, edges/EV, rejection stage and reason, qualification, position state, identities, evidence timestamps. Temporal integrity proven by the branch's tests (future/post-reference evidence excluded, persisted-at irrelevant, doubleheaders distinct) and by the identical 16-game reconciliation (PASS 16) on the live stack.

## 20. Browser acceptance (real 5173 → 8010, 1920×1080 and 1024×768)
- `?experiment=v1`: "Strict control — V1" selected, ASP MODEL BANKROLL — 2026 V1, policy AISP_OPPORTUNITY_POLICY_V1, model asp_mlb_ensemble 2.0.0-gate2-08cca634c81b, funnel 16 games, $1,000.00, evidence inspectable.
- `?experiment=v2`: "Practical pregame policy challenger — V2.1" selected and preserved across F5; badge SAME MODEL · DIFFERENT DECISION POLICY; policy AISP_PRACTICAL_PREGAME_POLICY_V2_1; same model; funnel 16→15→15→3→0 with 16 inspectable evidence blocks and reconciliation "pass 16 (sums to 16)"; $999.78 / 0 open / 8 settled / $20.00 risked / 3–5; EARLY EVIDENCE visible.
- Regression: Daily Command Center (57 evidence badges), Crowd & Market Pulse (16 rows), Market Board (130 rows), Shadow Board (13 rescores), Model Performance, Historical Research all open.
- No console errors, no page overflow (scrollWidth 1905/1009 ≤ viewport), only `127.0.0.1:8010/api/v1/*` requests — no provider host; positions/decisions unchanged after every page open.

## 21–24. Ledger, provider calls, protected writes, scheduler writes
- V2.1 ledger digest (positions, stakes, P/L, results, CLV): `a45764fcd8794074` before and after — 8 positions, $20.00, −$0.22, 3 W / 5 L, 5 CLV observations; bankroll $999.78 on the page.
- Provider calls by this task: 0.
- Protected writes by this task: experiments 3→3, policy checksums unchanged, model versions 2→2, positions 8→8, results 8→8, CLV 5→5, decisions 12,337→12,337, PM refresh runs 635→635 — zero of each.
- Scheduler-authored since restart (17:37:43 ET): 2 × `poll-availability-events` MLB_STATS_API ingestion runs (heartbeat starter observations/lineups, reason: 2-minute availability poll), and 1 forecast row (289→290) from the operational rescore path between baseline and end — normal operation, not integration activity.

## 25–26. Performance and remaining bottlenecks
| Endpoint | Statements before → after | Warm latency before → after | Size |
|---|---|---|---|
| V1 funnel | 587 → 26 | 1.04 s → 0.42–0.57 s | 8.0 → 8.3 KB |
| V2.1 funnel | 732 → 26 | 1.29 s → 0.47–0.60 s | 43.7 → 43.9 KB |
| Dashboard | 515 → 33 | 1.88 s → 1.41–1.57 s | 165 KB |
Remaining throughput is constrained by single-worker Python CPU in the card/opportunity/evaluator path (requests serialize behind one uvicorn worker) and by the absence of the broader evidence-versioned public cache; the duplicate CPU-only V2.1 evaluation, worker count, scheduler separation and supervision repair remain separate tasks. No claim of readiness for thousands of users is made.

## 27–30. Files, commits, pushes, next
Files: the 8 of the fast-forward (`server/app/services/funnel_read_context.py` new, `evidence_states.py`, `practical_policy.py`, `shadow_experiment_service.py`, `read_service.py`, `qualification_service.py`, `server/tests/test_funnel_read_model.py` new, `docs/AISP_EXPERIMENT_FUNNEL_SET_BASED_READ_MODEL_REPORT.md`) plus this record. Commit created here: this record (existing integration-record convention). Pushes: none.
Next: keep the pregame evidence and read-model integrations under observation for one full slate cycle, then take the single-worker CPU bottleneck as its own task (multi-worker uvicorn with the scheduler separated, or trimming `game_card` assembly from the funnel path), before any evidence-versioned public cache.
