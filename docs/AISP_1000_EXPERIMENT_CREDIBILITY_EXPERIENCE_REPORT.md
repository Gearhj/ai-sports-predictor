# AISP $1,000 Experiment — Credibility Experience Redesign & Market Pulse Count Reconciliation

Date: 2026-09-04 (America/New_York) · Operator: Claude Fable 5.1, non-elevated session as `james` · Nothing pushed · main untouched · 8010 not restarted · Provider calls by this task: 0 · Backend writes by this task: 0.

Verdict: **AISP_EXPERIMENT_CREDIBILITY_EXPERIENCE_READY** — with one read-model correction (item 6) and three pre-existing, time-dependent test failures on the untouched primary (item 24) stated explicitly.

## 1. Branches and HEADs
- Primary `C:\Users\james\ai-sports-predictor`, branch `codex/multisport-platform-architecture` @ `bb1a55df134b4bc6b3bf0e47c856ed0b91332493` — untouched (clean except the preserved untracked `docs/MONTE_CARLO_SIMULATION_THEATER.md` and `install-supervision.ps1`).
- Work: worktree `C:\Users\james\worktrees\aisp-experiment-credibility-experience`, branch `claude/experiment-credibility-experience-v1`, base `bb1a55d`. Five commits (item 28). Not merged, not pushed.
- main `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` before and after.

## 2. Safety inventory
No Git locks, no other agent process, no stash use, no reset/clean/force checkout, no history rewrite. The live 8010 backend (PID 46964, scheduler running) was never stopped; every page open in this task was read-only. Migration head `20260831_0024` = live; the branch adds no migration and no table. `DATABASE_URL` redacted throughout.

## 3. Protected baseline (start → end)
| Table | Start | End | Cause of change |
|---|---|---|---|
| shadow_experiments | 3 | 3 | — |
| shadow_experiment_decisions | 12,487 | 12,622 | live 8010 scheduler (749 decisions in the last 3 h at the end check) |
| experiment positions | 8 | **9** | **live 8010 scheduler froze V2.1 `SF ML` (SF @ NYM, +172, $2.50) at 18:52:52 ET on 2026-09-04** — OPEN, not settled |
| position_results | 8 | 8 | — |
| results with CLV | 5 | 5 | — |
| model_versions | 2 | 2 | — |
| forecasts | 292 | 293 | live scheduler rescore |
The side backend for acceptance (8011) ran with `SCHEDULER_ENABLED=false`, `EVENT_RELATIVE_SCHEDULER_ENABLED=false`, `LIVE_SHADOW_ENABLED=false`, `ODDS_POLLING_ENABLED=false`; every measured read issued 0 INSERT/UPDATE/DELETE statements. The 9th position is scheduler-authored and is rendered honestly by the new page as an OPEN row (item 12).

## 4. Phase 1 — data contract findings (before changes)
- Bare `/experiment` resolved to **V1** (`experimentKey` defaulted to `'v1'`), contradicting the active practical experiment.
- `/bankroll?experiment=v2` already carried `equity_curve` (9 points, one per settlement), `positions` (id, date, game_id, selection, market, price, stake, result, profit_loss, closing_price, clv), `betting` (net, risked, record, max_drawdown $, CLV mean), `closing_quote_coverage` (5 of 8), `decision_metrics`, model identity. Missing for the mandated ledger: matchup, bankroll after each settlement, model/market probability, adjusted edge, decision identity, settlement time, beat-close, staking-policy id, policy checksum, start date; the equity points carried no settlement identity, high-water mark or drawdown.
- All of those exist as persisted records: `position_results` (bankroll_before/after, closing_american_odds, closing_line_value, beat_close, settled_at), `shadow_experiment_decisions` (model_probability, no_vig_probability, edge, evidence.adjusted_edge, phase, recommended_at, sportsbook), `shadow_experiments` (staking_policy_version, policy_config.checksum, start_date). Read-only extension permitted and applied (item 5).

## 5. Read-only response extension (no table, no write)
- `PositionSchema` (+13 optional fields, all `None` when not recorded — never zero): matchup, sportsbook, bankroll_after, settlement_sequence, settled_at, beat_close, model_probability, market_probability, raw_edge, adjusted_edge, decision_id, decision_phase, recommended_at.
- `equity_curve` points (+): sequence (0 = opening), position_id, selection, result, profit_loss, high_water, drawdown, settled_at.
- `BankrollData` (+): staking_policy, policy_checksum, experiment_start_date. `BettingPerformance` (+): bankroll_chain_reconciled.
- `/experiment/comparison` rows (+): clv_sample_size, total_risked, started_at.
- Matchups come from one bulk `game_teams ⋈ teams` read per ledger (constant, not per row): V2.1 bankroll 9 → **10** statements; V1 9 → 9; comparison 18 → 18; funnels 26/26 → 26/26 (item 23).

## 6. Integrity finding — simultaneous settlements and the persisted bankroll chain
Two V2.1 positions settled with the identical `settled_at` (2026-09-04 01:35:01.558809 ET): `STL ML` (−$2.50) and `ATH ML` (+$4.70). The read model ordered ties by `recommended_at`, which put the win first (…997.58 → 1,002.28 → 999.78). The settlement engine's own persisted chain (`bankroll_before/after`) applied the loss first (…997.58 → **995.08** → 999.78). Final bankroll and net P/L were identical either way, but the intermediate path differed, so the published **max drawdown was $7.50 (0.75 %) while the persisted chain reached $10.00 (1.00 %)**.
Repair (read model only, no evidence touched): `ShadowExperimentService._settlement_order` keeps `settled_at` as the primary key and, within equal timestamps, follows the persisted chain (`bankroll_before == running balance`), falling back to `recommended_at` only when no chain exists. The read now reports `bankroll_chain_reconciled = true` (every persisted `bankroll_after` reproduced exactly) and the page shows a visible warning if that ever becomes false. Live result after repair: max drawdown **$10.00 / 1.0 %**, peak $1,005.08, current drawdown 0.527 %, current $999.78, net −$0.22, 3–5–0, CLV −0.153 % on 5 of 8 — all reconciled point-for-point against `position_results`.

## 7. Experiment selection (Phase 2)
- Bare `/experiment` → **V2.1** (active practical experiment). `?experiment=v1` → V1 strict control. `?experiment=v2` accepted as an alias. Selection is URL-bound: refresh, back and forward preserve it (verified in the browser: click V1 → `/experiment?experiment=v1`; back → `/experiment` with V2.1 selected; forward → V1 again).
- Selector (role `tablist`, two `tab`s with `aria-selected`): "V2.1 — Practical Pregame Policy Challenger / ACTIVE PRACTICAL EXPERIMENT · SAME FORECAST MODEL" and "V1 — Strict Control / CONTROL · SAME FORECAST MODEL". The version label is still derived from the backend policy id.

## 8. Hierarchy (Phase 3) — as rendered on the side stack
A. Hero: eyebrow "PRACTICAL PREGAME POLICY CHALLENGER — V2.1", title unchanged, one-sentence contract ("A $1,000 virtual bankroll follows one frozen forecasting model under one frozen decision policy: every position is recorded before first pitch, settled exactly once, and never edited or removed."), badges ACTIVE · VIRTUAL RESEARCH EXPERIMENT · SAME MODEL · DIFFERENT DECISION POLICY · EARLY EVIDENCE · NO REAL WAGERS. No promotional language (tested).
B. Scoreboard above the fold (two rows of six): Starting bankroll, Current bankroll, Net P/L, ROI on risked capital (tooltip + visible "Net P/L ÷ $20.00 risked — not ÷ bankroll"), Settled positions, Open positions; Record, Amount risked, Average stake, Max drawdown ($ and % of opening, "peak-to-trough in settlement order"), Current drawdown, CLV sample ("−0.153% · 5 of 8 settled with a closing quote · missing quotes never count as zero").
C. Frozen contract strip: experiment + role, model `asp_mlb_ensemble 2.0.0-gate2-08cca634c81b (development)` with identity source, policy `AISP_PRACTICAL_PREGAME_POLICY_V2_1`, staking policy `AISP_QUARTER_KELLY_EVIDENCE_V2` (25 % Kelly · max 1 %), start date 2026-09-01 (registered 2026-08-31T11:35:00-04:00), opening $1,000; drawer "Checksums & audit identity" (experiment id, policy checksum `7e439d4b…20a8d2`, code identity, engine, markets, edge threshold).
D. "Bankroll after each settlement": step curve from the opening bankroll, one step per settlement in settlement order, opening benchmark and high-water mark series, tooltip naming the settlement; plus an accessible "Settlement path" table (Open + #1…#8: date, settlement, result, P/L, bankroll after, high-water mark, drawdown). V1: "No V1 positions have settled. The opening bankroll is not a performance result."
E. "Every position" — eyebrow "NO CHERRY-PICKING · IMMUTABLE POSITION HISTORY", chronological (#1 → #8, unsettled rows trail), 16 columns (#, Date, Matchup, Selection, Market, Decision price, Stake, Result, P/L, Bankroll after, Closing price, CLV, Model prob., Market prob., Adjusted edge, Decision identity) with a per-row expansion (position id, decision id, phase, recommended at, settled at, sportsbook at decision, raw edge, beat-the-close, game link). Result pills WIN/LOSS/PUSH/VOID/OPEN as text. Missing closing evidence is "Unavailable", never 0.
F. Decision metrics (candidate evaluations ≠ qualified evaluations ≠ distinct selections ≠ frozen positions) and "V1 vs V2.1" with Role, Status, Forecasting model, Decision policy, Staking policy, Start date, Games evaluated, Candidate evaluations, Qualified evaluations, Qualified selections (distinct), Frozen positions, Settled positions, Record, Amount risked, Average stake, Net P/L, Current bankroll, CLV sample. Live values at acceptance: V2.1 5,257 candidate evaluations / 438 qualified evaluations / 18 distinct qualified selections / 8 frozen (the mandate's 317 / 15 / 8 were the morning values; the live scheduler has evaluated further slates since).
G. "Today's experiment result" card: POSITIONS FROZEN / PROVISIONAL CANDIDATES / AWAITING FINAL EVIDENCE / NO BET — POLICY CONDITIONS NOT MET ("No position today. A zero-position day is a valid experiment result.") / NO GAMES / LOADING / UNAVAILABLE, derived only from the funnel payload already fetched and the ledger. At acceptance it read PROVISIONAL CANDIDATES (3 qualified, 0 frozen) and, after the scheduler froze `SF ML`, POSITIONS FROZEN.
H. Funnel moved below the comparison inside a collapsed `<details>` "How today's slate was screened · Point-in-time qualification funnel · date"; per-game evidence, reconciliation and diagnostics are unchanged when expanded. Daily decision log and monthly ledger are collapsed below it.
I. Collapsed methodology ("How this experiment works"), immutability ("Why the ledger cannot be edited"), staking policy, identity & audit metadata, responsible-use callout.

## 9. Scoreboard reconciliation (live, V2.1)
Sum of ledger P/L −$0.22 = net P/L = betting.net_profit_loss; last "bankroll after" $999.78 = current bankroll = betting.current_bankroll; amount risked $20.00 = 8 × $2.50 (now $22.50 with the scheduler's 9th OPEN position); record 3–5–0 = betting.record; CLV −0.153 % on 5 of 8 = closing_quote_coverage; max drawdown $10.00 = 1,005.08 − 995.08 from the persisted chain. Tested in `BankrollPage.credibility.test.tsx` and `test_experiment_credibility_evidence.py`.

## 10. Identity strip
Rendered from the read only (no hard-coded ids). SAME MODEL · DIFFERENT DECISION POLICY appears exactly once (hero badge); the strip carries role, model, policy, staking policy, start date, opening bankroll; checksums are in the drawer.

## 11. Equity curve
Starts at the opening bankroll, advances only on settlements, chronological in settlement order with the persisted chain as tie-breaker, high-water mark and drawdown per point, all 8 V2.1 settlements (Open, #1 997.50, #2 1,001.08, #3 1,005.08, #4 1,002.58, #5 1,000.08, #6 997.58, #7 995.08, #8 999.78). No curve is drawn for an experiment without settlements.

## 12. Position ledger
8 settled rows plus the scheduler's OPEN `SF ML` row (rendered with "—" for P/L, bankroll after, closing price and CLV, and its own "Open positions" panel). Losses (5) are shown with the same prominence as wins (3).

## 13. V1 vs V2.1 comparison
Separate ledgers, never pooled; V1 shows "No settled positions", $0.00 risked, CLV "—".

## 14. Today's experiment result
See 8G. The card never invents a position: POSITIONS FROZEN requires `positions_created > 0` or a ledger row on that date.

## 15. Funnel presentation
Collapsed by default, lower on the page, unchanged content and read model when expanded; the page derives the result card from the same single funnel request (no extra request).

## 16. Sample-size honesty (Phase 4)
"8 settled positions are far too few to distinguish skill from noise. No profitability claim is made." (count-driven), plus "Every figure below is a bookkeeping fact about this virtual ledger, not evidence of an edge." EARLY EVIDENCE badge from `decision_metrics.evidence_stage`. No significance thresholds invented; neutral styling (amber note, no green/red hero).

## 17. Market Pulse discrepancy — root cause (Phase 5)
Reproduced live on 2026-09-04: strip "Quoted 16/16 · 0 reviewed · 16 unreviewed" vs banner "15 of 16 canonical game(s) have persisted quotes whose market semantics are not yet reviewed". Two authorities: the strip counted `coverage_state == QUOTED_UNREVIEWED` (any persisted snapshot with unreviewed semantics), the banner counted rows whose `unreviewed_observation` flag was set, and that flag requires a usable midpoint. DET @ CLE (game 1) was FINAL with an in-play Polymarket snapshot and no midpoint: quoted (16) but not an observation (15). By the evening a second game (MIL @ CIN) had an in-play capture, and the split was 14 + 2.

## 18. Market Pulse repair
New mutually exclusive coverage state `QUOTED_STRUCTURALLY_EXCLUDED` (reason: game not pregame / quote captured in-play / no usable midpoint), ranked after `QUOTED_UNREVIEWED`, contributing no price and no observation. `_coverage_counts` now publishes `exclusive_sum` (and `unreviewed_observations`) and the banner counts `coverage_state == QUOTED_UNREVIEWED` — the same authority as the strip — adding "N further quoted game(s) are structurally excluded (final, in-play or no usable price) and are not observations." Client: strip detail "0 reviewed · 12 unreviewed · 4 excluded (not pregame)", a reconciliation line "Each game counted once: … = 16 of 16 — reconciles." (turns red with "DOES NOT RECONCILE" if it ever fails), consensus and semantics-review cards labelled "overlapping measure", and a "Quoted · excluded (not pregame)" pill with the row note "QUOTED — STRUCTURALLY EXCLUDED (NOT PREGAME / IN-PLAY / NO USABLE PRICE)". Live at acceptance: 12 unreviewed + 4 excluded = 16 canonical, banner "12 of 16 … 4 further …". No refresh, approval or semantics change was made.

## 19. Visual direction (Phase 6)
Existing ASP tokens only (dark nav, light canvas, teal accents, navy strip, amber notes). New CSS is additive (`experiment-*`, `ledger-*`, `equity-path-table`, `pulse-coverage-reconciliation`, `visually-hidden`); the Phase 1.5 typography guard (no px font below 11 px) passes.

## 20. Responsive (Phase 7)
| Viewport | documentElement scrollWidth / clientWidth | Scoreboard columns | Notes |
|---|---|---|---|
| 1920×1080 | 1905 / 1905 | 6 | full hierarchy above the fold through the scoreboard |
| 1440×900 | 1425 / 1425 | 6 | — |
| 1024×768 | 1009 / 1009 | 3 | ledger and comparison scroll inside their own regions |
| 390×844 (mobile emulation, reloaded) | 390 / 390 | 2 | selector stacks, contract strip one column, badges wrap |
No page-level horizontal overflow at any size; wide tables scroll inside `TableScroll` regions.

## 21. Accessibility
Selector is a `tablist` with `aria-selected`; Tab moves from the V2.1 tab to the V1 tab and on to the eight ledger "Details" buttons (`aria-expanded`, `aria-controls`); tables carry `<caption>`s and column/row headers, scroll regions are focusable with labels ("Settlement path", "Complete position ledger", "Experiment comparison", "Daily decisions"); status is never colour-only (WIN/LOSS pills are text; result card titles are text); collapsed `<details>` remain in the DOM for assistive technology; the ROI tooltip is also written out in the card detail.

## 22. Page requests, size, latency (side stack 8011, single worker, warm)
| Request | Size | Warm latency | Statements |
|---|---|---|---|
| GET /bankroll?experiment=v2 | 12.3 KB | 1.89 s | 10 (was 9) |
| GET /bankroll?experiment=v1 | 6.3 KB | 0.88 s | 9 (unchanged) |
| GET /experiment/comparison | 2.6 KB | 2.82 s | 18 (unchanged) |
| GET /experiment/qualification-funnel?policy=v2 | 42.2 KB | 0.47 s | 26 (unchanged) |
| GET /experiment/qualification-funnel?policy=v1 | 8.1 KB | 0.45 s | 26 (unchanged) |
| GET /prediction-markets/board | 36.7 KB | 2.39 s | 162 (unchanged, outside scope) |
The experiment page issues exactly three page requests (bankroll, comparison, funnel) plus the app shell's registry/config reads — the same set as before; `ERR_ABORTED` duplicates are React StrictMode development aborts. Bankroll and comparison latency is dominated by `_decision_metrics` over 12.6 k decisions and was the same on the primary (2.11 s / 2.83 s); no cache was added.

## 23. Read-model non-regression
Funnels 26 / 26 statements on both checkouts (16 games, 2026-09-04); the ledger's one extra bulk statement is constant in the number of positions; zero writes in every measured read.

## 24. Tests
New: `server/tests/test_experiment_credibility_evidence.py` (11: chain tie-break ×3, ledger/equity reconciliation, read-only bounded bankroll read, final-game exclusion, in-play exclusion, banner/strip single authority, pregame quote still an observation, unquoted rows unaffected, board read makes no write), `client/src/pages/BankrollPage.credibility.test.tsx` (31: experiment selection 4, primary evidence 7, equity curve 5, position history 7, funnel & today's result 5, safety 3), `client/src/pages/MarketPulsePage.reconciliation.test.tsx` (4). Updated for the new copy/route: `BankrollPage.test.tsx`, `BankrollPage.v2.test.tsx`, `BankrollPage.identity.test.tsx`, `MarketPulsePage.coverage.test.tsx`, `App.data-states.test.tsx`.
Mapping to the mandate's 45 items: experiment selection 1–8 → selection ×4 + v2/identity switching tests; primary evidence 9–15 → primary evidence ×7; equity curve 16–22 → equity ×5 + backend ledger reconciliation + chain ×3; position history 23–30 → history ×7 + backend ledger fields; funnel presentation 31–35 → funnel/result ×5; market pulse 36–40 → backend ×6 + client ×4; safety 41–45 → safety ×3 + backend read-only ×2.
Results: client full suite **168 / 169** passed on the final code (the single miss, `phase15.integrity › never substitutes the canonical fixture for an unknown demo id`, timed out only under the full-suite load and passes alone: 5 / 5); `tsc --noEmit` clean; `vite build` succeeded (8.8 s). Backend: the new file 11 / 11; the touched-module suites pass except three **pre-existing, time-of-day-dependent failures reproduced identically on the untouched primary `bb1a55d`**: `test_board_seeds_every_canonical_game_even_without_any_market`, `test_disabled_provider_is_labelled_disabled_but_persisted_quotes_still_show` (both seed a game at now + 5 h, which crosses midnight ET in the evening), and `test_funnel_read_model::test_no_sql_inside_the_per_game_loop` / `test_funnels_stay_within_the_query_budget[16|30]` (51 / 79 statements from lazy `players` loads in the test fixture — identical on the primary; the live funnels are still 26 statements). None is caused by this branch; none was modified here.

## 25. Browser acceptance (side stack 5174 → 8011, in-app browser)
A `/experiment`: V2.1 selected, badges, contract, scoreboard ($1,000 / $999.78 / −$0.22 / −1.1 % / 8 / 0 · 3–5–0 / $20.00 / $2.50 / $10.00 / 0.5 % / −0.153 % 5 of 8), contract strip, equity path 9 rows, ledger 8 rows with expansion, comparison, result card, collapsed funnel; no console errors. B `?experiment=v1`: V1 selected, ACTIVE · COLLECTING DATA, V1 empty equity copy, empty ledger copy, CLV Unavailable, "Strict control · same forecast model as the challenger". C switching, back/forward and refresh preserve the URL-bound selection. D Crowd & Market Pulse: 16 rows, banner 12 of 16 + 4 excluded, strip 12 unreviewed · 4 excluded, reconciliation "16 of 16 — reconciles", four excluded pills. E regression: Daily Command Center (12 cards, evidence badges), Market Board (130 rows), Shadow Board (85 articles), Model Performance (21 articles), Historical Research (8 articles) all render on the side stack without a retry banner.
The side-stack browser is left open at the top of the redesigned V2.1 Experiment page at desktop size.

## 26. Regression pages
No console errors on any page; only `127.0.0.1:8011/api/v1/*` and Vite module requests — no provider host contacted.

## 27. Protected writes, provider calls, scheduler activity
This task: 0 provider calls, 0 prediction-market refreshes, 0 semantics approvals, 0 bets created or backfilled, 0 evidence rows altered, 0 model retraining, 0 ASP Model Studio changes. All protected-table drift (item 3) is attributable to the live 8010 scheduler, including the new OPEN V2.1 position at 18:52:52 ET.

## 28. Files and commits (branch `claude/experiment-credibility-experience-v1`)
1. `feat(experiment): establish credibility-first evidence hierarchy` — `client/src/pages/BankrollPage.tsx` (rewrite), `client/src/components/QualificationFunnel.tsx` (`onData`), `client/src/types/domain.ts`, `client/src/styles.css`, updated page tests, `App.data-states.test.tsx`.
2. `feat(experiment): add immutable bankroll and position storytelling` — `server/app/services/shadow_experiment_service.py`, `server/app/schemas/api.py`, `server/app/services/read_service.py`.
3. `fix(market-pulse): reconcile quote and semantics counts` — `server/app/prediction_markets/read_service.py`, `server/app/schemas/prediction_market.py`, `client/src/pages/MarketPulsePage.tsx`, `MarketPulsePage.coverage.test.tsx`, `domain.ts`, `styles.css`.
4. `test: prove public experiment evidence integrity` — the three new test files.
5. `docs: record credibility experience redesign` — this report.
6. `style(experiment): trim trailing blank line` — whitespace-only follow-up (git diff --check) plus this line.
Full hashes are listed in the final response. No push; primary and main untouched; not integrated into 8010.

## 29. Residuals and next
- Integrate (fast-forward) and restart 8010 so the public page and Market Pulse serve the new read model; the corrected max drawdown ($10.00) will then replace the understated $7.50.
- The three pre-existing test failures (item 24) deserve their own fix: seed test games at a fixed pregame hour on the operating date, and eliminate the lazy `players` loads inside the funnel fixture path.
- Bankroll/comparison latency (~2–3 s, dominated by `_decision_metrics` over the growing decisions table) is unchanged by this branch and remains a separate performance task, as does the Market Pulse board's 162-statement read.
