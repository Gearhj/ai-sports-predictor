# AISP Experiment Funnels — Set-Based Read Model (N+1 removal)

Operator: Claude Fable 5.1 · 2026-09-04 (America/New_York) · Provider-call budget 0 (used 0) · Nothing pushed · main untouched.

## 1–5. Worktree, branches, git, runtime authority
- Start: worktree `C:\Users\james\worktrees\aisp-funnel-read-model-v1`, branch `claude/experiment-funnel-set-based-read-model` created from `codex/multisport-platform-architecture` @ `9df5c0f` (verified; implementation state through `951fa4e`). Final: same branch @ `6fe4a9c` + docs commit. Primary untouched at `9df5c0f`. main `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.
- Git status before: primary clean except the two preserved untracked files; no locks; no other agent; 9 worktrees. After: worktree clean.
- Runtime: operational backend 127.0.0.1:8010 (PID 52076, scheduler running — never restarted here), Vite 5173, PostgreSQL `asp` (`postgresql+psycopg://asp_user:<redacted>@localhost:5432/asp`), migration head `20260831_0024` = live; **no migration and no index added**. Scheduled jobs that append evidence (finalize-games/refresh-schedule, availability poll, freeze, odds, PM observer) kept running on 8010 throughout; diagnosis used read-only transactions.

## 6–9. Baseline (reproduced with an SQLAlchemy `before_cursor_execute` counter)
| Request | Games | Statements | Unique fingerprints | DB time | Service time | Bytes |
|---|---:|---:|---:|---:|---:|---:|
| V1 2026-09-04 | 16 | **587** | 26 | 0.49 s | 1.56 s (cold) | 8.5 KB |
| V2.1 2026-09-04 | 16 | **732** | 26 | 0.40 s | 0.96 s | 46.5 KB |
| V1 / V2.1 2026-08-27 | 7 | 196 / 197 | 22 | 0.09 / 0.05 s | | |
| V1 / V2.1 2026-08-29 (doubleheader) | 17 | 619 / 773 | 24 | 0.37 / 0.31 s | | |
| V1 / V2.1 2026-09-03 (positions) | 9 | 326 / 390 | 28 | 0.15 / 0.14 s | | |
| `/dashboard/today` 2026-09-04 | 16 | 515 | | | 2.6 s | 165 KB |

Ranked repeated families (V2.1, 16 games; executions per request → caller → loop):
game_teams ×128 (`_sides`, `_matchup`, evaluator home/away — game loop) · availability_events ×112 (`_unresolved_availability_events` ×3 per evaluation, `_availability_context` — game and side loops) · lineup_observations ×90 (lineup evidence per side, `_lineup_summary`) · starter_observations ×64+32 (starter evidence per side, `_pitcher_summary`) · forecasts ×32 (`_latest_forecast` twice per game) · players ×32, pitchers ×32, leagues ×32, teams ×32 (`_pitcher_summary`, `_game_sport`, `_sport`, `_matchup`) · shadow_experiments ×18 (`active_experiment` per game + `experiment_active`) · game_refresh_states, game_feature_snapshots, venues, markets (latest-quote batch), shadow_rescores, forecast_components ×16 each (`game_card`, `_availability_context`, `_market_inputs`, `_component_disagreement`).
**Root cause:** every helper on the funnel path resolved its inputs with its own per-game/per-team/per-side query, and the V2.1 funnel evaluated each game twice (once for the opportunity view, once for the funnel), so statements grew ≈ 37 (V1) / 46 (V2.1) per game.

## 10–13. Read-context architecture, exact loads, temporal preservation, snapshot
`server/app/services/funnel_read_context.py`: `FunnelReadContext` (dicts keyed by canonical game id, (game, team), forecast id, experiment id) built by `build_funnel_read_context(session, settings, games, now)`, attached to `Session.info["funnel_prefetch"]` for one request and detached in `finally`. Loads (each one statement, all bounded by the slate's id list): sides+teams; leagues; venues; rescores (newest completed first) → after-forecast ids; forecasts on the CURRENT_DEVELOPMENT track for the game's source mode, PUBLISHED or rescore outputs, highest version per game (= `_latest_forecast`); forecast components; latest refresh state per (game, mode) via `ROW_NUMBER`; latest feature snapshot per (game, mode) via `ROW_NUMBER` with the original four-key order plus referenced snapshot ids; complete lineup histories; complete starter histories (both ordered `available_as_of, id`); players and pitchers for every referenced pitcher id; team features only for games without a snapshot; availability events (received_at desc, id desc); the latest quote batch per (market, sportsbook) for all slate games with the same grouped-max join as `_latest_market_snapshots` and a deterministic tie-breaker; active experiments; the latest FINAL_PREGAME decision at/before each game's cutoff per experiment (`ROW_NUMBER` over a `starts_at − 15 min` join); the per-experiment bankroll (one settlement scan each). Helpers consult the context and otherwise run their original query — jobs, other pages and tests that attach nothing are unchanged.
Temporal: reducers apply identical filters on the in-memory histories (`available_as_of ≤ min(evaluation instant, cutoff)`, `received_at ≤ reference`), lineup latest = last eligible row, market rows respect the game's `source_mode`; frozen decision text comes from persisted rows only; doubleheaders are keyed by canonical game id.
Snapshot: `begin_read_snapshot` pins `REPEATABLE READ` on PostgreSQL before the request's first statement (no locks, released with the session; no-op on SQLite). Responses carry `read_model{version, generated_at, evidence_watermark, prefetch_statements}`.

## 14–16. Equivalence (fixed `now`, `read_model` stripped)
| Date | V1 | V2.1 |
|---|---|---|
| 2026-09-04 (16, DH) | equal (587 → 26) | equal (732 → 26) |
| 2026-08-27 (7) | equal (196 → 26) | equal (197 → 26) |
| 2026-08-29 (17, DH) | equal after tie-breaker (619 → 26) | equal (773 → 26) |
| 2026-09-03 (9, positions) | equal (326 → 27) | equal (390 → 27) |
Dashboard 2026-09-04: equal to the pristine primary after excluding wall-clock fields (`reference_at`, ages, quote ages), 515 → 33 statements.
**Intentional difference (authority: Phase 4 deterministic ordering):** the legacy quote query ordered only by `observed_at desc`; five alternate TOTAL markets for AZ@SF (2026-08-29) share one `observed_at`, so equal-score candidates were ordered by unspecified physical row order (legacy "Over 8", bulk plan "Over 8.5"). Both paths now order by `(observed_at desc, market.id, outcome.id, sportsbook.id)`; the tie resolves identically on both, which for that historical row selects "Over 8.5". Covered by the equivalence tests; no other field differed on any date.

## 17–19. Counts after, complexity, latency
- V1 26–27 statements, V2.1 26–27 (budget 40). Test slates: 1/5/16/30 synthetic games → V1 21–22 / V2.1 20–21 statements, 1-vs-30 difference ≤ 2 (test asserts ≤ 5); zero SQL while walking every game through cards, forecasts, evaluator and evidence with the context attached.
- Latency (same DB, same instant, HTTP): V1 warm 0.98 s → 0.42 s (−57%), V2.1 1.15 s → 0.54 s (−53%); dashboard 1.85 → 1.45 s; `/today` 1.73 → 1.43 s. In-process: V1 DB 0.43 → 0.20 s, service 1.5 → 0.35 s; V2.1 DB 0.31 → 0.18 s. Response bytes unchanged apart from the `read_model` block (+~220 B). Rows materialised: complete slate histories (≈1,000 starter rows, ≈700 lineup rows, ≈80 events, latest quote batch) — no Cartesian amplification (dedup key per game/outcome/book).

## 20–21. Bounded concurrency (side stack 8011, scheduler off, 40 requests per level)
| Endpoint | conc | p50 | p95 | p99 | errors | checksums | peak active DB |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1 | 1 / 5 / 10 / 20 | 0.47 / 1.54 / 3.20 / 6.75 s | 0.52 / 1.81 / 3.89 / 7.10 | 0.53 / 1.98 / 3.90 / 7.44 | 0 | 1 | 1 |
| V2.1 | 1 / 5 / 10 / 20 | 0.52 / 1.82 / 3.79 / 8.18 s | 0.65 / 2.30 / 4.71 / 10.74 | 0.74 / 2.35 / 4.80 / 10.84 | 0 | 1 | 1 |
Legacy (8010, bounded to conc 1/5, 20 requests): V1 1.01 / 3.44 s, V2.1 0.99 / 4.57 s p50 — the set-based path is ≈2.2–2.5× faster per request at concurrency 5. No pool waits (pool_size 10, peak active 1); latency scales linearly because one uvicorn worker executes the remaining Python evaluation serially — the request-level N+1 is removed; the next bottleneck is CPU in `game_card`/`_daily_opportunities`/the evaluator, not the database. No claim is made beyond local bounded concurrency.

## 22–24. Protected writes, provider calls, ledger
Before → after (live DB): experiments 3→3, positions 8→8, results 8→8, CLV 5→5, model versions 2→2, forecasts 289→289, PM refresh runs 631→631, policy checksums unchanged; decisions 12,177→12,193 written solely by the operational 8010 scheduler. Provider calls by this task: 0. V2.1 ledger from persistence: 8 positions, $20.00, −$0.22, 3–5, 5 CLV observations, $999.78 on the page — unchanged.

## 25–27. Tests, browser acceptance, migrations
- `server/tests/test_funnel_read_model.py` 13/13 (budget at 1/5/16/30, constancy, no SQL in loop incl. serialization, V1/V2.1 equivalence, comparison unchanged, reference/source-time safety, doubleheader isolation, snapshot no-op on SQLite, zero writes + no HTTP, metadata). Touched-module suites (`qualification_and_pulse_states`, `practical_policy_v2`, `pregame_evidence_freshness`, `shadow_experiment`, `gate2_lifecycle_read_service`, `bankroll`, `experiment_identity_v21`, `performance_read_service`, `market_pulse_canonical_coverage`) all pass; client `tsc` clean and `vite build` OK (no client change). Full server suite (worktree, `test_gate3c_x1_contracts.py` deselected): 16 failures, none in this branch's scope — the FD1 family (`test_gate3c_fd1_readiness_actual_operator` ×7, `test_gate3c_x1_io`) needs the primary-only `.runtime/gate3c-fd1` / `data/curated/gen2-fd1` directories (`FD1EvidenceError: The authoritative FD1 curated root is missing`) and passes on the pristine primary except one `readiness_actual` artifact test that fails there too; the SQLite-migration family (`test_gate3c_fd1_recovery_migration` ×2, `test_gate3c_forecast_track_separation`, `test_gate3c_prospective`, `test_migration_runtime`) fails identically on the pristine primary; `test_gate3c_x1_publisher_state` passes in isolation in the worktree (order-dependent in the full run). Every other test passes.
- Browser (side stack 5174→8011, 1920×1080 and 1024×768): `?experiment=v1` and `?experiment=v2` URL-bound, identities/badge correct, V2.1 funnel 16→15→15→3→0 with 16 inspectable games and starter/lineup detail, V1 funnel 16 games, ledger unchanged; Daily Command Center (57 badges), Market Pulse (16 rows), Market Board, Shadow Board (13 rescores), Model Performance, Historical Research open; no overflow; no provider host; console shows only the side Vite HMR-socket retries.
- Migrations/indexes: none (no EXPLAIN evidence required a new index; the bulk queries use existing `(game_id, team_id, available_as_of)` and game-id indexes).

## 28–32. Files, commits, pushes, remaining, integration step
Files: `server/app/services/funnel_read_context.py` (new), `evidence_states.py`, `practical_policy.py`, `shadow_experiment_service.py`, `read_service.py`, `qualification_service.py`, `server/tests/test_funnel_read_model.py` (new), this report. Commits: `0ce502a` perf, `b011603` refactor, `6fe4a9c` test, + docs. Pushes: none.
Remaining: (1) per-request CPU in the card/opportunity/evaluator path (~0.35–0.5 s) serializes behind one worker — multi-worker uvicorn or moving `game_card` field assembly off the funnel path are the next levers; (2) V2.1 still evaluates each game twice (view + funnel) — now CPU-only, kept for exact equivalence; (3) `_odds_freshness`, `_experiment_status_map`, `_positions_created` remain 5 fixed statements per request.
Integration step: `git -C C:\Users\james\ai-sports-predictor merge --ff-only claude/experiment-funnel-set-based-read-model` (primary must still be at `9df5c0f`), run `pytest tests/test_funnel_read_model.py tests/test_qualification_and_pulse_states.py tests/test_practical_policy_v2.py tests/test_shadow_experiment.py`, restart 8010 through the existing loop, then confirm `/experiment/qualification-funnel?policy=v2` carries `read_model.version = AISP_FUNNEL_SET_BASED_READ_MODEL_V1` with `prefetch_statements` ≈ 20 and the 16-game reconciliation unchanged.
