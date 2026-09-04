# AISP MLB Pregame Starter & Lineup Freshness — Integrity Audit and Repair

Operator: Claude Fable 5.1 · Date audited: 2026-09-04 (America/New_York) · Provider-call budget: 0 (used: 0)

## 1–4. Worktree, branches, git state
- Starting: worktree `C:\Users\james\worktrees\aisp-pregame-evidence-freshness`, branch `claude/pregame-evidence-freshness-integrity`, created from `codex/multisport-platform-architecture` @ `44076e4` (verified: `b58018c` = code repairs, `44076e4` = integration record). Primary checkout untouched throughout.
- Final: same branch @ `40d5d73` (+ this report's commit). Primary still @ `44076e4`.
- main: `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.
- Git status before: primary clean except the two known untracked files (`docs/MONTE_CARLO_SIMULATION_THEATER.md`, `install-supervision.ps1`), no locks, no other agent process, 8 worktrees enumerated. After: worktree clean; primary unchanged. Nothing pushed.
- Safety inventory: backend 127.0.0.1:8010 PID 39084 (scheduler running), Vite 5173 PID 32840, PostgreSQL `asp` on localhost:5432 (`DATABASE_URL=postgresql+psycopg://asp_user:<redacted>@localhost:5432/asp`), migration head `20260831_0024` (source = live). Provider config by variable name only: `THE_ODDS_API_KEY`, `ODDS_POLLING_ENABLED`, `ODDS_POLL_INTERVAL_MINUTES`, `ODDS_PREGAME_REFRESH_MINUTES`, `LIVE_SHADOW_ENABLED=true`, `SCHEDULER_ENABLED`; MLB Stats API is credential-free. Active experiments: V1 `13604fc0…` (AISP_OPPORTUNITY_POLICY_V1, 2026-08-27), V2 `129b6a66…` (superseded before activation), V2.1 `43fe1b16…` (AISP_PRACTICAL_PREGAME_POLICY_V2_1, 2026-09-01).

## 5. Canonical 16-game matrix (2026-09-04, read at 15:17 ET)
All 16 canonical LIVE games; every one has 31 MLB_STATS_API starter observations per side at a 30-minute cadence (first 00:05 ET, last 14:35 ET), one stable probable identity per side, source status PROBABLE, no CONFIRMED (MLB never confirms pre-pitch — policy audit: 0 of 88 games), `created_at − available_as_of = 0 s`.

| # | Game | MLB id | First pitch ET | Cutoff ET | DH | Latest starter obs ET | Age @cutoff | Age @read | Lineups (home/away) | Baseline funnel | Repaired funnel |
|---|---|---|---|---|---|---|---:|---:|---|---|---|
| 1 | DET@CLE | 824424 | 14:10 | 13:55 | S g1 | 14:35 (13:35 ≤ cutoff) | 20m | 42m | CONFIRMED/CONFIRMED | PROBABLE_STABLE (blocked: fresh_quote) | PASS · FRESH (blocked: fresh_quote) |
| 2 | MIL@CIN | 824471 | 18:10 | 17:55 | – | 14:35 | 200m | 42m | CONFIRMED/— | PROBABLE_STABLE | PASS · FRESH |
| 3 | ATL@PHI | 823418 | 18:40 | 18:25 | – | 14:35 | 230m | 42m | CONFIRMED/CONFIRMED | PROBABLE_STABLE | PASS · FRESH |
| 4 | LAA@PIT | 823338 | 18:45 | 18:30 | – | 14:35 | 235m | 42m | —/CONFIRMED | PROBABLE_STABLE | PASS · FRESH |
| 5 | BOS@BAL | 824797 | 19:05 | 18:50 | – | 14:35 | 255m | 42m | —/— | PROBABLE_STABLE* | PASS · FRESH |
| 6 | SF@NYM | 823579 | 19:10 | 18:55 | – | 14:35 | 260m | 42m | CONFIRMED/— | PROBABLE_STABLE* | PASS · FRESH |
| 7 | CHC@MIA | 823824 | 19:10 | 18:55 | – | 14:35 | 260m | 42m | —/— | PROBABLE_STABLE* | PASS · FRESH |
| 8 | DET@CLE | 824387 | 19:15 | 19:00 | S g2 | 14:35 | 265m | 42m | —/— | PROBABLE_STABLE* | PASS · FRESH |
| 9 | MIN@CWS | 824554 | 19:40 | 19:25 | – | 14:35 | 290m | 42m | —/— | PROBABLE_UNSTABLE (stale vs cutoff) | PASS · FRESH |
| 10 | TB@TEX | 822852 | 20:05 | 19:50 | – | 14:35 | 315m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 11 | AZ@HOU | 824146 | 20:10 | 19:55 | – | 14:35 | 320m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 12 | TOR@KC | 824067 | 20:10 | 19:55 | – | 14:35 | 320m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 13 | STL@COL | 824311 | 20:40 | 20:25 | – | 14:35 | 350m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 14 | NYY@SD | 823256 | 21:40 | 21:25 | – | 14:35 | 410m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 15 | WSH@LAD | 823905 | 22:10 | 21:55 | – | 14:35 | 440m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |
| 16 | ATH@SEA | 823093 | 22:10 | 21:55 | – | 14:35 | 440m | 42m | —/— | PROBABLE_UNSTABLE | PASS · FRESH |

\* The baseline verdict for rows 5–8 flipped from UNSTABLE to STABLE between the morning read and the 15:5x re-read purely because the wall clock advanced to within 240 minutes of their cutoffs — the signature of the defect. "—" lineup = EXPECTED_NOT_YET_PUBLISHED (MLB publishes lineups ~1–3 h before first pitch; the poll persists them inside the 4-hour window: DET@CLE 62 lineup observations, MIL@CIN home 21, ATL@PHI 9/5, SF@NYM home 2).

## 6–8. Stale / missing / unstable / other, and observation ages
- Genuinely stale at the evaluation instant: **0 of 16**. Missing: 0. Unstable (identity/streak): 0. Conflict: 0. Doubleheader collision: 0 (distinct canonical ids and MLB ids; game-state rows carry `doubleheader_status='S'`, `game_number` 1/2).
- "14 stale" as reported = games whose (future cutoff − latest observation) exceeded 240 minutes at read time; exact ages listed above (200–440 min against the cutoff vs 42 min against the reference).
- The only game that "passed" (DET@CLE g1) is the one whose cutoff had already passed, so its reference equalled its cutoff.

## 9. Timestamp semantics
| Timestamp | Meaning | Source |
|---|---|---|
| provider event/update time | not exposed for probable pitchers by MLB Stats API (schedule `hydrate=probablePitcher`) | — |
| request/response/received time | `fetched_at = datetime.now(UTC)` at adapter response | mlb.py `get_schedule`, `get_game_lineups` |
| normalized/persisted | `observed_at = available_as_of = fetched_at`; `created_at` = DB insert (not used for freshness) | normalizer `ingest_schedule` / `ingest_lineups` |
| first/last seen | first/last `available_as_of` per (game, team, identity) — streak start / latest | evidence_states |
| decision cutoff | `starts_at − 15 min` (FINAL_PREGAME_CUTOFF_MINUTES) | shadow_experiment_service |
| evaluation reference (new) | `min(evaluation instant, cutoff)` | evidence_states.evaluation_reference |
| freeze time | `recommended_at` of the decision row; FINAL only inside `[cutoff − 15 min, cutoff]` | shadow_experiment_service |
All columns are `DateTime(timezone=True)`; naive values are treated as UTC; ET conversion verified (23:10Z → 19:10 ET; cutoff 18:55 ET). No DST, negative-age or date-rollover defect found; future-dated observations now fail closed.

## 10–12. Source providers, job cadence, job-run reconstruction (2026-09-04)
- Starters: MLB Stats API `/schedule?hydrate=probablePitcher` (+ `/people` for handedness); lineups: `/game/{id}/boxscore` battingOrder (CONFIRMED when 9 slots); injuries: SportsDataIO (configured, separate run records).
- Jobs: `refresh-schedule` inside `finalize-games` every 30 min (35 runs, 00:04–14:35, 532 rows, all SUCCEEDED) — the only writer of starter observations; `poll-availability-events` every 2 min (LIVE_SHADOW_ENABLED=true; 828 log lines, polled 7–15 due games per run, lineups persisted, **0 MLB ingestion runs recorded, 0 starter observations persisted when unchanged**, 1 PROBABLE_PITCHER_CHANGED + 4 LINEUP_CONFIRMED events today, 0 rescores in the sampled runs); `freeze-shadow-experiment` every 5 min (all three phases every run); `refresh-odds` 29 runs; `refresh-mlb-data`/`refresh-weather` 06:01. No failed or skipped runs among the starter/lineup jobs. Cadence itself was sufficient for a 240-minute rule; the defect was not cadence.

## 13. Latest-as-of reducer
Application: `available_as_of ≤ reference`, ordered ascending, last = latest. Independent SQL `max(available_as_of) … ≤ reference` agrees for all 16 games (and in the new test). Before the repair the reducer's upper bound was the cutoff, not the instant — correct at the cutoff, wrong before it (and, in principle, it could consult an observation that had not yet been received at the evaluation instant during replays).

## 14–17. Timezone, identity/doubleheader, starter-state, lineup-state findings
No timezone defect. Identity: starter and lineup rows bind to canonical `game_id` + `team_id`; DET@CLE g1/g2 isolated (tested). Starter states: CONFIRMED never occurs pre-pitch from this source; PROBABLE_STABLE requires ≥2 identical observations ≥10 min apart, current at the reference, forecast after streak start, no unresolved event; STALE and MISSING are now distinct verdicts (`freshness`), not conflated. Lineups: per-team source availability now reported as EXPECTED_NOT_YET_PUBLISHED / PARTIAL / AVAILABLE / CONFIRMED alongside the policy state; later observations supersede earlier ones by `available_as_of`; no player-identity failure observed (lineup entries resolve via the governed player mapper).

## 18. Event-triggered rescoring
Events persist → identity resolved → `process_event` → rescorer (forecast regenerated when allowed) → readiness recomputed → the V2.1 evaluator treats a material event newer than the forecast as unresolved (blocks until refreshed) → freeze appends MORNING snapshots while pre-cutoff and, now, exactly the final-window FINAL_PREGAME evaluation; position creation reads only the persisted at-cutoff decision. No event creates a position. The Shadow Board shows the 7 rescores today with before/after probabilities and edges and "NO_BET" framing.

## 19–20. Policy verification
- V1 (`AISP_OPPORTUNITY_POLICY_V1`): unchanged; does not consume `starter_evidence` (its gates are the readiness composite + quote/edge rules); remains the strict control.
- V2.1 (`AISP_PRACTICAL_PREGAME_POLICY_V2_1`, checksum `7e439d4b…`): persisted `policy_config` defines eligible starter states, penalties, caps, supported markets — it defines neither the freshness constant nor phase timing (both are code). Constants unchanged: MIN_STABLE_OBSERVATIONS=2, spacing 10 min, STARTER_OBSERVATION_CURRENT_MINUTES=240, FINAL_PREGAME_CUTOFF_MINUTES=15. Both experiments resolve to `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development)`. No policy/config disagreement found; nothing loosened.

## 21. Root causes
1. **As-of instant defect (primary):** freshness aged against the future cutoff instead of `min(now, cutoff)`.
2. **Final-phase labelling defect:** FINAL_PREGAME evaluated with `final=True` on every 5-minute run all day → 659 "final" rows/slate before any cutoff carrying the stale reason; the position path used only the last one (which is why positions were still correct).
3. **Freshness authority lag:** the 2-minute poll verified the source but persisted nothing when unchanged and recorded no ingestion run; freshness advanced only every 30 minutes.
Not causes: source staleness, failed/missed jobs, timezone, identity, doubleheader, cache, provider timestamps.

## 22. Repairs (commits 1d67bf0, 49ec2c0, 9bd7671, aeeddb2)
As-of reference + evidence detail; heartbeat starter observations (AISP_STARTER_HEARTBEAT_V1) + honest poll ingestion run; FINAL_PREGAME only in `[cutoff−15m, cutoff]` (AISP_FINAL_FREEZE_WINDOW_V1); funnel evidence + reconciliation; UI badges; monitor (AISP_PREGAME_EVIDENCE_MONITOR_V1). No threshold changed; no decision/position recreated.

## 23. Current-day funnel before → after (live DB, same instant)
scheduled 16 → 16 · starters eligible **7 → 15** (the 16th, DET@CLE g1, is PASS at its cutoff but had already failed the earlier fresh-quote gate) · lineup 7 → 15 · critical 7 → 14 · adjusted edge 1 → 3 · provisional qualified 1 → 3 · positions 0 → 0. Starter reconciliation after: PASS 16 / STALE 0 / MISSING 0 / UNSTABLE 0 / CONFLICT 0 / EVENT 0 (sums to 16). Top blockers after: adjusted EV not positive (11), adjusted edge below 2.0 pts (2), stale quote (1) — genuine evidence, preserved.

## 24–26. Ledger, provider calls, protected writes
V2.1 ledger before/after: 8 positions, $20.00, −$0.22, 3–5, 5 CLV observations (unchanged). Provider calls by this task: **0** (all diagnosis from persisted rows; side stack ran with scheduler off; browser network log shows only `127.0.0.1:8011/api/v1/*`). Protected writes by this task: experiments 3→3, positions 8→8, results 8→8, CLV 5→5, forecasts 283→283, `pregame_evidence.alert` audit rows 0→0; decisions 11,749→11,811 written solely by the operational 8010 scheduler (old code).

## 27. Performance (worktree code, live DB)
| Read model | Cold | Warm | Queries | Size |
|---|---|---|---|---|
| `/dashboard/today` (Daily Command Center) | 3.12 s | 2.56 s | — | 164 KB |
| `/experiment/qualification-funnel?policy=v2` | 1.39 s | 1.25 s | 732 (baseline 730; +2 for the evidence block) | 44 KB |
| `/experiment/qualification-funnel?policy=v1` | 0.99 s | 1.04 s | 587 (unchanged) | 8 KB |
| `/today` (all sports) | 2.64 s | 2.29 s | — | 38 KB |
The funnels' ~45 queries/game N+1 is pre-existing and reported, not widened; page reads make no provider call.

## 28. Tests
- New `server/tests/test_pregame_evidence_freshness.py`: 14 passed (matrix points 2, 5–8, 10–18, 20–24, 26–36, 39–40, 42). New `client/src/components/PregameEvidenceBadges.test.tsx`: 3 passed.
- Touched suites: `test_practical_policy_v2.py` (37) , `test_shadow_experiment.py`, `test_qualification_and_pulse_states.py`, `test_bankroll.py`, `test_gate2_lifecycle_read_service.py` all pass; client `tsc` clean; 32 affected client tests pass. Full server suite: see addendum below.

## 29. Browser acceptance (side stack 5174 → 8011, scheduler off; 1920×1080 and 1024×768)
Daily Command Center: 16 games, badges "Home/Away starter PROBABLE · observed 20–24m before reference · FRESH", lineups CONFIRMED / NOT YET PUBLISHED, expandable detail, no overflow (1905/1009 ≤ viewport). V2.1 experiment: funnel eyebrow V2.1, reconciliation "pass 16 (sums to 16)", 16 inspectable games with evidence, starter stage 15, qualified 3 provisional, positions 0, bankroll $999.78 / 8 settled / 3–5 unchanged. Shadow Board: "Probable pitcher changed" and "Lineup status None → CONFIRMED" events with before/after cards, NO_BET framing, rescores distinguishable. Regression: Market Pulse (16 rows), Market Board (122 rows), Model Performance, V1 experiment, Historical Research all open. Console: only the side Vite HMR-websocket retries (dev tooling; the 5173 stack shows none). No provider host in any request.

## 30–32. Files, commits, pushes
Files: server `evidence_states.py`, `practical_policy.py`, `shadow_experiment_service.py`, `jobs/orchestrator.py`, `qualification_service.py`, `read_service.py`, `schemas/api.py`, new `pregame_evidence.py`, `pregame_evidence_monitor.py`, tests (`test_pregame_evidence_freshness.py` new; `test_practical_policy_v2.py`, `test_shadow_experiment.py` fixtures); client `PregameEvidenceBadges.tsx` (+test, new), `QualificationFunnel.tsx`, `OpportunityCard.tsx`, `types/domain.ts`, `styles.css`; this report. Commits: `1d67bf0`, `49ec2c0`, `9bd7671`, `aeeddb2`, `40d5d73`, + docs. Pushes: none. Primary and main untouched.

## 33. Remaining James decisions
1. Integrate and restart 8010 (separate mandate) — until then the operational funnel still misreports mid-day staleness and writes all-day FINAL rows.
2. Whether the heartbeat interval (10 min) and final window (15 min) should be recorded in the V2.1 `policy_config` at the next policy version (they are pipeline constants today).
3. Whether the funnel N+1 (≈45 queries/game) warrants a set-based read model (out of this task's scope).

## 34. Exact proposed integration step
`git -C C:\Users\james\ai-sports-predictor merge --ff-only claude/pregame-evidence-freshness-integrity` (primary must still be at `44076e4`), run `pytest tests/test_pregame_evidence_freshness.py tests/test_practical_policy_v2.py tests/test_shadow_experiment.py`, `tsc --noEmit`, `vite build`, then restart 8010 through the existing supervision loop and confirm on the live funnel that `starter_evidence_reconciliation` sums to the slate and that the next slate's FINAL_PREGAME rows appear only inside the final window.
