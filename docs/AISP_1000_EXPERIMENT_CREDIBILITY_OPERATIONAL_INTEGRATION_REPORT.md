# AISP $1,000 Experiment Credibility Redesign — Operational Integration Record

Date: 2026-09-04 (America/New_York) · Operator: Claude Fable 5.1, non-elevated session as `james` · Nothing pushed · main untouched · Provider calls by this task: 0 · Backend writes by this task: 0.

Verdict: **AISP_EXPERIMENT_CREDIBILITY_OPERATIONALLY_READY** — with two narrow acceptance fixes made on the primary during this integration (items 15–16 and 13) and the pre-existing test failures classified against the untouched primary (item 6).

## 1. Primary branch/HEAD before and after
- Primary physical checkout `C:\Users\james\ai-sports-predictor`, branch `codex/multisport-platform-architecture`.
- Before: `bb1a55df134b4bc6b3bf0e47c856ed0b91332493`. After fast-forward: `9204d361c23448ebfb88cad859eda1892dbc8afa`. After the two acceptance fixes: `2c1f3e124ba4d5c54cbcb00843b14d9bc0726777` (plus this record's commit, item 29).

## 2. Completed branch/HEAD
Worktree `C:\Users\james\worktrees\aisp-experiment-credibility-experience`, branch `claude/experiment-credibility-experience-v1` @ `9204d361c23448ebfb88cad859eda1892dbc8afa`, clean. `bb1a55d` verified as a direct ancestor; the six subjects matched the expected list exactly.

## 3. Main HEAD
`92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.

## 4. Integration method
`git merge --ff-only claude/experiment-credibility-experience-v1` after re-checking that the primary was clean (only the preserved untracked `docs/MONTE_CARLO_SIMULATION_THEATER.md` and `install-supervision.ps1`), no Git locks, no staged or tracked modifications, eleven worktrees enumerated. A `codex.exe` process was running on the machine but nothing in the primary checkout was modified before, during or after; the status was re-verified immediately before the merge. Migration head `20260831_0024` = live; no migration in the branch.

## 5. Exact commits integrated
1. `dd598204023073dd44be0692bf2dd1f27bd4b68c` feat(experiment): establish credibility-first evidence hierarchy
2. `1ec90cdd7a6bfe43beb1c5c91a006278845d5e8b` feat(experiment): add immutable bankroll and position storytelling
3. `eb43f36970ccb017609d09e7d8f3b5b2c740dec9` fix(market-pulse): reconcile quote and semantics counts
4. `222648717da7997005aa8e484ed2ff94864c1da6` test: prove public experiment evidence integrity
5. `0448cddd354068d1744dd36c8b47405f013f9c35` docs: record credibility experience redesign
6. `9204d361c23448ebfb88cad859eda1892dbc8afa` style(experiment): trim trailing blank line
Fast-forward diff: 19 files, +1,792 / −195; `git diff --check` clean.
Acceptance fixes committed on the primary afterwards (item 22): `1aa40ec2395d08f80092c4f6f47dd93083821433` fix(experiment): exclude open exposure from the ROI denominator; `2c1f3e124ba4d5c54cbcb00843b14d9bc0726777` fix(experiment): never render another experiment's payload under the selected tab.

## 6. Tests
Phase 1 in the completed worktree: backend `test_experiment_credibility_evidence`, `test_shadow_experiment`, `test_market_pulse_canonical_coverage`, `test_qualification_and_pulse_states`, `test_experiment_identity_v21`, `test_bankroll`, `test_funnel_read_model`, `test_practical_policy_v2`, `test_pregame_evidence_freshness`: **126 passed, 4 failed**. The 4 failures — `test_disabled_provider_is_labelled_disabled_but_persisted_quotes_still_show`, `test_no_sql_inside_the_per_game_loop`, `test_funnels_stay_within_the_query_budget[16]`, `[30]` — fail identically on the untouched primary `bb1a55d` at the same clock (19:50 ET); `test_board_seeds_every_canonical_game_even_without_any_market` passed on both at that clock. No new regression. Not repaired (scope). Client page suites 58/58, `tsc --noEmit` clean, `vite build` succeeded (11.2 s), `git diff --check` clean.
After integration and the two fixes on the primary: client full suite **171/171**, `tsc` clean, `vite build` succeeded (9.1 s), page suites 57/57.

## 7. Backend PID before and after
Before: uvicorn PID **46964** (started 17:37:43) ← launcher PID 33528 ← `cmd /c C:\Users\james\AppData\Local\asp\start-backend.cmd` loop PID 23244. Restart method: stopped only PID 46964; the loop relaunched. After: uvicorn PID **22700** (started 19:50:51) ← launcher PID 24060 ← the same `start-backend.cmd` loop; exactly one listener on 127.0.0.1:8010; two python processes with `port 8010` on their command line (launcher + worker), the same shape as before. Scheduled task "ASP Backend 8010" left untouched (shows Running, owns no wrapper). Frontend Vite 5173 PID 32840 (running since 08-31) not restarted; HMR picked up the client changes. Side stack 8011/5174 from the earlier task is still running and was not used for this acceptance.

## 8. Backend revision after restart
Command line `python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8010` launched from the primary checkout (relative `.venv\Scripts\python.exe` in the launcher). Revision proof: live 8010 `/bankroll?experiment=v2` returns `staking_policy = AISP_QUARTER_KELLY_EVIDENCE_V2`, `betting.bankroll_chain_reconciled = true` and per-position `bankroll_after` — fields that exist only from `1ec90cd`; `/prediction-markets/board` returns `coverage.exclusive_sum` and `QUOTED_STRUCTURALLY_EXCLUDED` — fields that exist only from `eb43f36`.

## 9. Health/readiness
`/readyz` 200 `{"status":"ready","database":"reachable","scheduler":"running"}` about 3 s after relaunch; `/healthz` 200.

## 10. Frontend state
Vite 5173 unchanged (single process); the redesigned page and the two fixes served through HMR and confirmed by full reloads. No duplicate Vite.

## 11. Bare-route result
`http://127.0.0.1:5173/experiment` → V2.1 selected (`aria-selected` true on "V2.1 — Practical Pregame Policy Challenger"), eyebrow "PRACTICAL PREGAME POLICY CHALLENGER — V2.1", badges Active · Virtual research experiment · SAME MODEL · DIFFERENT DECISION POLICY · EARLY EVIDENCE · No real wagers, no console errors, no horizontal overflow.

## 12. V1 route result
`?experiment=v1` → "STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT", V1 tab selected, experiment "ASP MODEL BANKROLL — 2026 V1 · Role · strict control", ACTIVE · COLLECTING DATA, current $1,000.00, "No V1 positions have settled…", empty ledger copy, CLV "Unavailable · No legitimate closing sample"; no V2.1 figure or name present.

## 13. V2.1 route result and switching
`?experiment=v2` → V2.1 (alias). Click V1 → `/experiment?experiment=v1`; back → `/experiment` (V2.1); forward → V1; F5 preserves the selection. Acceptance found that during a switch the resource hook keeps the previous experiment's payload in a live state until the new read returns, so V1 figures rendered under the V2.1 tab for a few seconds with the eyebrow "CHALLENGER — V1". Fixed in `2c1f3e1`: a payload whose `experiment_key` differs from the selected tab is treated as loading. Re-verified live: 0.7 s after each switch the page shows CHECKING LIVE DATA with the correct eyebrow and no figure of the other experiment.

## 14. Current ledger reconciliation (persistence, recomputed)
| Quantity | Before integration (19:4x ET) | End of task (20:02 ET) |
|---|---|---|
| Frozen positions | 9 | **10** |
| Settled | 8 | 8 |
| Open | 1 (SF ML +172, $2.50) | **2** (SF ML +172; TB ML, $2.50 each) |
| Settled W/L/P/V | 3 / 5 / 0 / 0 | unchanged |
| Settled amount risked | $20.00 | $20.00 |
| Open exposure | $2.50 | $5.00 |
| Total historical stake | $22.50 | $25.00 |
| Settled P/L | −$0.22 | −$0.22 |
| Current bankroll | $999.78 | $999.78 |
| Legitimate CLV | 5 observations, mean −0.153% | unchanged |
| High-water mark | $1,005.08 | unchanged |
| Max drawdown (persisted chain) | $10.00 / 1.00% | unchanged |
| Ledger digest (settled 8 + open rows) | `aef3a328460d6645` (9 rows) | `0f46bd4e56754939` (10 rows; settled rows identical) |
The tenth position (TB ML) was recommended and placed at 19:47:52 ET by the scheduler in the pre-restart backend process and became visible shortly after the restart; this task created none.

## 15. Open-versus-settled reconciliation
Live page: Net P/L −$0.22 "Settled positions only"; Record 3–5–0; Settled positions 8; Open positions 2 with "$5.00 open exposure · not in P/L, record or ROI"; Amount risked $20.00 "8 settled · $5.00 open exposure shown separately"; Average stake $2.50 "Total stake $25.00 ÷ 10 frozen positions"; open rows show "—" for P/L, bankroll after, closing price and CLV; the open rows sit in their own "Open positions" panel and at the tail of the complete ledger labelled OPEN. Current bankroll $999.78 is labelled "After every settlement, no deposits or withdrawals" and is never presented as a return.

## 16. ROI denominator
Acceptance found the redesigned card dividing settled P/L by `betting.total_risked`, which includes open stakes (−0.22 ÷ 22.50 = −0.98%, shown as −1.0% with "÷ $22.50 risked"). Fixed in `1aa40ec`: ROI = settled P/L ÷ settled stake only (−0.22 ÷ 20.00 = **−1.1%**, "Net P/L ÷ $20.00 settled stake — open exposure excluded, never ÷ bankroll"), with a test covering an open position. The backend `betting.roi` field is unchanged (it remains net ÷ total stake) and is no longer used for the card.

## 17. Equity-curve reconciliation
Live 8010: 9 points (sequence 0–8), starting $1,000.00, one point per settlement, results LOSS WIN WIN LOSS LOSS LOSS LOSS WIN, final $999.78 = current bankroll, no open position in the curve. Settlement-path table on the page: Open $1,000.00; #1 $997.50; #2 $1,001.08; #3 $1,005.08; #4 $1,002.58; #5 $1,000.08; #6 $997.58; #7 $995.08 (drawdown $10.00); #8 $999.78 (drawdown $5.30); high-water $1,005.08 throughout from #3.

## 18. Maximum-drawdown reconciliation
Persisted chain replayed from `position_results.bankroll_before/after`: STL ML (−$2.50, 997.58 → 995.08) is applied before ATH ML (+$4.70, 995.08 → 999.78) although both carry `settled_at` 2026-09-04 01:35:01; every persisted `bankroll_after` is reproduced; `bankroll_chain_reconciled = true`; max drawdown **$10.00 / 1.0%** on the page (previously published $7.50 / 0.75%). The page renders a `role=alert` warning instead of figures from a broken chain (tested); no warning is shown live.

## 19. Position-history reconciliation
Complete ledger: 10 rows (8 settled + 2 OPEN), chronological #1–#8 then open rows, columns #, Date, Matchup, Selection, Market, Decision price, Stake, Result, P/L, Bankroll after, Closing price, CLV, Model prob., Market prob., Adjusted edge, Decision identity; expansion of #1 shows position id `bd0f24dc…`, decision id `2c5670f1…`, FINAL_PREGAME, recommended/settled timestamps, sportsbook FanDuel, raw edge +7.43 pts, beat-the-close No. All five losses visible by default; every value matches the persisted rows listed in item 14.

## 20. CLV reconciliation
5 of 8 settled positions carry a closing quote (#1 0%, #2 −0.816%, #3 0%, #5 −2.846%, #6 +2.899%); mean −0.153% = page "CLV sample −0.153% · 5 of 8 settled with a closing quote"; #4, #7, #8 show "Unavailable" for closing price and CLV; open rows show "—". Comparison: "CLV sample (settled only) −0.153% · 5 of 8 settled".

## 21. Sample-size language
"8 settled positions are far too few to distinguish skill from noise. No profitability claim is made." — count-driven from `settled_positions` (verified by test for other counts); EARLY EVIDENCE badge from `decision_metrics.evidence_stage`.

## 22. Funnel placement and query counts
Funnel is below the comparison inside a collapsed `<details>` "How today's slate was screened · Point-in-time qualification funnel · 2026-09-04", 16 stages and the starter-evidence reconciliation ("pass 16 (sums to 16)") present when expanded; `read_model.version = AISP_FUNNEL_SET_BASED_READ_MODEL_V1` on both live funnels. Statements with the integrated primary code (engine counter, 16 games): **V1 26, V2.1 26** (budget 40); bankroll V2.1 10, V1 9; comparison 18; Market Pulse board 162 (unchanged, outside scope). Zero writes in every measured read. Page reads: bankroll, comparison, funnel plus the app shell's registry/config.

## 23. Market Pulse category reconciliation (live 5173 → 8010, 2026-09-04 = current canonical date)
Banner: "7 of 16 canonical game(s) have persisted quotes whose market semantics are not yet reviewed … 9 further quoted game(s) are structurally excluded (final, in-play or no usable price) and are not observations." Strip: Quoted 16/16 "0 reviewed · 7 unreviewed · 9 excluded (not pregame)"; Listed-not-quoted 0/16; consensus 0/16 "overlapping measure". Reconciliation line: 0 reviewed · 7 semantics pending · 9 structurally excluded · 0 listed-not-quoted · 0 mapping review · 0 mapping rejected · 0 no market mapped · 0 no provider = **16 of 16 — reconciles**; nine "Quoted · excluded (not pregame)" pills; semantics-review card labelled "overlapping measure (per provider)"; the reviewed gap card shows "—" with the unreviewed observation marked "not decision eligible". Persistence check: 7 rows with `unreviewed_observation` = 7 `QUOTED_UNREVIEWED` rows; `exclusive_sum` 16. No prediction-market observation enters the experiment (the experiment reads only sportsbook quotes via the decision records; `decision_eligible` 0).

## 24. Provider calls
Zero by this task. Page loads contacted only `127.0.0.1:8010` (and Vite on 5173); no provider host in any network log.

## 25. Protected writes
By this task: experiments 3→3, policy checksums unchanged (`7e439d4b…` for V2.1), model versions 2→2, settlements 8→8, CLV 5→5, historical ledger rows unchanged, no decision/position/settlement created or rewritten, no prediction-market refresh or approval.

## 26. Scheduler-authored writes (reported separately)
Live scheduler: decisions 12,717 (19:4x) → 12,727 (19:51, post-restart) → 12,735 (20:02; 8 recommended after the 19:50:51 restart); one V2.1 position TB ML placed 19:47:52 by the pre-restart process; prediction-market snapshots 70,726 → 71,108 (192 observed after the restart); forecasts 293 → 293; no settlements. The page rendered the new open position honestly without any change.

## 27. Browser acceptance (real 5173 → 8010)
A bare route ✓; B explicit routes, refresh, back/forward, no cross-tab leak (after `2c1f3e1`) ✓; C scoreboard reconciled (after `1aa40ec`) ✓; D drawdown $10.00 / 1.0% ✓; E equity curve and settlement path ✓; F immutable ledger with OPEN rows ✓; G sample-size sentence ✓; H funnel collapsed, 16 stages, marker present, 26/26 statements, no writes ✓. Viewports: 1920×1080 (1905/1905, 6 columns), 1440×900 (1425/1425, 6), 1024×768 (1009/1009 after settle, 3), 390×844 mobile emulation (390/390, 2) — no page-level overflow; the only wider reading (1382 at 1024) was a transient immediately after the emulated resize and cleared within 3 s and on reload. Market Pulse ✓ (item 23). Regression: Daily Command Center (13 cards), Historical Research (8 articles), Market Board (130 rows), Shadow Board (85 articles), Model Performance (21 articles) — all render, no retry banner, no console errors. The in-app browser is left at the top of `/experiment` (V2.1) at desktop size.

## 28. Files changed
Integrated (19 files): `client/src/pages/BankrollPage.tsx`, `client/src/components/QualificationFunnel.tsx`, `client/src/pages/MarketPulsePage.tsx`, `client/src/types/domain.ts`, `client/src/styles.css`, `server/app/services/shadow_experiment_service.py`, `server/app/services/read_service.py`, `server/app/schemas/api.py`, `server/app/prediction_markets/read_service.py`, `server/app/schemas/prediction_market.py`, tests (`BankrollPage.credibility.test.tsx` new, `MarketPulsePage.reconciliation.test.tsx` new, `test_experiment_credibility_evidence.py` new, five updated), `docs/AISP_1000_EXPERIMENT_CREDIBILITY_EXPERIENCE_REPORT.md`. Acceptance fixes: `client/src/pages/BankrollPage.tsx`, `BankrollPage.credibility.test.tsx`, `BankrollPage.test.tsx`.

## 29. Integration-report commit
This record, committed on `codex/multisport-platform-architecture` after `2c1f3e1` (hash in the final response).

## 30. Pushes
None. main untouched.

## 31. Backlog (not done here, by instruction)
- Bankroll/comparison latency (~2–3 s, `_decision_metrics` over 12.7 k decisions) and the 162-statement Market Pulse board read.
- The four time-of-day / fixture-sensitive backend tests (midnight-crossing seeds at now+5h; lazy `players` loads in the funnel fixture) — identical on the untouched primary.
- One full-client-suite timeout observed earlier (`phase15.integrity` provenance) passed in the full run this time (171/171).
- Windows supervision (scheduled task owns no wrapper), single-worker CPU throughput, broader public caching, prediction-market semantic approvals, Kalshi configuration, `betting.roi` still defined as net ÷ total stake in the backend contract (the page no longer uses it), and the side stack 8011/5174 still running from the earlier task.
