# AISP Prediction-Market Overnight Capture Certification

Window: 2026-09-04 23:57 ET → 2026-09-05 08:15 ET (monitor expiry). Slate under
certification: MLB 2026-09-05 (15 games, first pitch 16:10 ET). Prepared by
Claude Fable 5.1. Companion artefacts: semantic review packets
(`AISP_PREDICTION_MARKET_SEMANTIC_REVIEW_PACKETS.md/.json`), capture matrices
(`pm_capture_matrix/`), screenshots (`screenshots/pm_overnight_certification/`),
provider-call receipt (`AISP_PREDICTION_MARKET_OVERNIGHT_PROVIDER_CALL_RECEIPT.json`).

**Verdict: AISP_PREDICTION_MARKET_FULL_SLATE_CAPTURE_PARTIALLY_CERTIFIED.**
Both providers discovered, mapped and captured a FIRST_DISCOVERED pregame
observation for all 15 games (30/30 cells, every source timestamp before first
pitch). The T−6h / T−2h / T−30m / T−5m / FINAL milestones did not become due
inside the window (the earliest opens at 10:10 ET) and are reported NOT_DUE, not
certified. Two genuine defects were reproduced, repaired with fail-before /
pass-after tests, integrated and deployed to 8010 during the window; the
repaired cut-capture path had no due cut to exercise in real time before the
window closed. The system is not semantically approved: both templates remain
HELD_CLOSED, and the review packets end in advisory recommendations only.

---

## 1. Frozen baseline (Phase 1, 2026-09-04 23:57 ET)

| Item | Value |
|---|---|
| Primary checkout | `C:\Users\james\ai-sports-predictor`, branch `codex/multisport-platform-architecture`, HEAD `86ecaa60f27f8836f9bda43d8cd9c449288cc15b`, clean apart from the two preserved untracked files (`docs/MONTE_CARLO_SIMULATION_THEATER.md`, `install-supervision.ps1`), no lock files |
| `main` | `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` (unchanged throughout) |
| Worktree | `C:\Users\james\worktrees\aisp-pm-overnight`, branch `claude/prediction-market-overnight-capture-certification` from `86ecaa6` |
| Migration head (live) | `20260904_0026` (no migration added by this task) |
| Services | 8010 backend pid 14900 (started 22:23:53 ET 09-04 by `start-backend.cmd` loop), one listener, RSS 357 MB; 5432 PostgreSQL; 5173 frontend found DEAD at 23:59 and restarted (see §10, incident) |
| Health | `/healthz` 200; `/readyz` ready / database reachable / scheduler running |
| PM scheduler jobs | discover 60 min, refresh 10 min, consensus 10 min, alerts 10 min, finalize tick 5 min |
| Protected entities (counts) | experiments 3, model_versions 2, forecasts 293, decisions 12,834, positions 10, settlements 9, CLV 5, semantic templates 2 (both HELD_CLOSED) |
| PM evidence counts | snapshots 116,615; orderbooks 81,425; events 3,625; contracts 29,641; mappings 3,673 |
| Bankrolls | V1 1000.00, V2 1000.00, V2.1 997.28 |
| Slate state | 09-04: 16 games (12 final, 4 live). 09-05 / 09-06: 0 games in ASP; upstream MLB Stats API already listed 15 games for 09-05 (earliest 20:10Z) — BLOCKED_UPSTREAM did not apply |
| Page reads | Market Pulse, Market Board, Daily Command Center, $1,000 Experiment: only `127.0.0.1:8010` and `127.0.0.1:5173` hosts requested (headless acceptance §11); zero provider hosts |

## 2. Semantic review packets (Phase 2)

Delivered as `AISP_PREDICTION_MARKET_SEMANTIC_REVIEW_PACKETS.md` + `.json`.
Sources: the CFTC-filed Kalshi BASEBALLGAMEWIN rulebook (MLBGAME.pdf, sha256
`fc45c809…c25b`, 13 pages), persisted `rules_primary` text for the 32 mapped
09-04 Kalshi contracts, the Polymarket Gamma description (market 4001441) and
the 16 persisted 09-04 Polymarket descriptions. Pattern check against persisted
text: Kalshi 28/32 (the 4 misses are the doubleheader phrasing, which fails
closed), Polymarket 16/16 (prefix match; a trailing resolution-source paragraph
is outside the pattern). Both packets: **RECOMMEND_APPROVE** with explicit
divergences (no-result cases resolve at market price / 50-50 rather than void;
ASP never consumes provider settlement). No decision executed; the admin decide
endpoint was not called; both templates remain HELD_CLOSED (decision_count 2).

## 3. Next-slate ingestion (Phase 3)

| Item | Value |
|---|---|
| Upstream first seen | 2026-09-04 ≈23:58 ET, MLB Stats API schedule for 2026-09-05: 15 games, no doubleheaders |
| Canonicalized in ASP | 2026-09-05 00:25:32 ET by `refresh-schedule` (MLB_STATS_API, 15 records) — the hourly pipeline, not the 06:00 daily job; re-confirmed 00:54, 01:24, 01:54, 02:24 |
| Games | SF@NYM 16:10, CHC@MIA 16:10, ATL@PHI 18:05, DET@CLE 18:10, LAA@PIT 18:40, MIL@CIN 18:40, TB@TEX 19:05, BOS@BAL 19:05, MIN@CWS 19:10, TOR@KC 19:10, NYY@SD 19:15, AZ@HOU 19:15, STL@COL 20:10, WSH@LAD 21:10, ATH@SEA 21:40 (all ET, all SCHEDULED, `game_number` 1, no doubleheader) |
| Synthetic slate | none created |

## 4. Provider discovery and mapping (Phase 4)

| Provider | Discovery run | Mapped games | Primary state per game |
|---|---|---|---|
| KALSHI | 00:25:46 ET (series KXMLBGAME verified; KXMLBSPREAD / KXMLBTOTAL also mapped but excluded from the full-game evidence) | 15/15 EXACT | MAPPED_PREGAME_QUOTED × 15 |
| POLYMARKET | 01:24:55 ET (hourly discovery; events 10079411–10079425) | 15/15 EXACT | MAPPED_PREGAME_QUOTED × 15 |

Both providers' mappings carry `SEMANTICS_REVIEW_REQUIRED` (templates held).
Board headline for 09-05: 0 reviewed / 15 awaiting review / 0 in-play / 0 listed
not quoted / 0 no market / 0 provider unavailable = 15 (exclusive sum 15).
No MAPPING_REVIEW_REQUIRED, PROVIDER_ERROR or UPSTREAM_NOT_AVAILABLE states.

## 5. Time-based snapshot certification (Phase 5)

Capture matrix (from `GET /prediction-markets/capture-coverage?date=2026-09-05`,
exported to `pm_capture_matrix/AISP_PM_CAPTURE_MATRIX_2026-09-05.{json,csv}`):

| Provider | FIRST_DISCOVERED | T−6h | T−2h | T−30m | T−5m | FINAL_VALID_PREGAME |
|---|---|---|---|---|---|---|
| KALSHI | CAPTURED 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 |
| POLYMARKET | CAPTURED 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 | NOT_DUE 15 |

- First Kalshi pregame observation 00:25:30 ET (received 00:25:51); first
  Polymarket 01:34:11 ET (received 01:34:11). All 15 games on both providers.
- Every persisted 09-05 pregame observation has `provider_observed_at <
  starts_at` (`bool_and` true over 630 rows); no LATE_INGESTION flags.
- No duplicate snapshots (same provider/market/outcome/source time) since the
  22:23 restart. Observations are append-only; nothing rewritten.
- The T−6h window for the earliest game opens 10:10 ET, after the window
  closes: those milestones are NOT_DUE, never classified missed.
- Doubleheader / rescheduled / one-sided-book / closed-Polymarket-404 rules
  were exercised by tests (§9) and, for closed books, by the live 09-04 slate.

Reference matrix for the previous slate (09-04, `AISP_PM_CAPTURE_MATRIX_2026-09-04.*`):
Polymarket FIRST 15 captured / 1 no-liquidity, T−2h 15, T−30m 15, FINAL 15,
T−6h 12, T−5m 2; Kalshi FIRST 2 captured / 11 no-liquidity (last-trade only)
/ 3 missed, later cuts captured only for the two 22:10 games that started after
the pregame-authority deploy. The dominant state for missing cuts is
MISSED_SCHEDULER_RUN — the defect in §7.2.

## 6. Resource and reliability audit (Phase 6, since the 22:23:53 ET restart)

| Measure | Value |
|---|---|
| Scheduler runs | discover: 7 Kalshi (48 requests) + 7 Polymarket; refresh: 24 runs, 971 requests, 1,054 errors (all Polymarket; 947 requests / 1,054 errors on Polymarket, 24 / 0 on Kalshi) |
| Retry storm | 12 refresh runs between 22:35 and 00:27 ET re-requested closed books (HTTP 404; 81–145 errors per run) until discovery marked the markets closed. Repaired in §7.1; post-repair runs show `status_distribution {2xx: 100, 404: 0}` and `terminalized_count 0` |
| Kalshi | one public listing request per refresh (no per-market calls, no credentials) |
| Requests per game per cycle | Polymarket ≈ 100 books / 15 games (HOME+AWAY tokens, capped at 200 contracts); Kalshi 1 listing / slate |
| Duplicates | 0 |
| DB | database 3,601 MB; snapshots table 154 MB; orderbooks 57 MB. Snapshots 116,615 → 118,508 (+1,893 over the window so far) |
| Process | 8010 RSS 357 → 377 MB before restart; 135 MB after the 02:31 restart, 136 MB after 02:45. One listener at every sample |
| PostgreSQL connections | 9 (old process) → 2–6 (new process) |
| Monitor samples | 33 through 02:42 ET, every sample healthz 200 / readyz ready / 1 listener / 0 DB errors |
| Page latency | board 1.3 s, capture-coverage 0.3 s, healthz 0.07 s |

## 7. Defects reproduced and repaired (Phase 3–6 repair scope)

### 7.1 Closed-market retry storm (categories: retry storm, closed-market terminalization, provider-call budget)
Reproduced: every 10-minute refresh re-requested 81+ closed Polymarket CLOB
books belonging to FINAL games; 1,054 errors in ~2 hours; run metadata had no
status accounting. Repair (commit `4dfecf9`): SourceError carries the HTTP
status; a 404 appends a `closed` contract version (append-only, idempotent by
checksum; prior versions and every observation untouched; final valid pregame
still selected); contracts of FINAL games are terminalized without a request;
every attempted request lands in exactly one bucket
(`status_distribution`) persisted with `terminalized` ids. Tests: 5
(fail-before on 86ecaa6: KeyError/AttributeError, FINAL-game book requested).

### 7.2 Scheduler gate starved the cut capture (category: scheduler job failure / snapshot schedule)
Reproduced: the 5-minute pregame tick only ran the finalize job when a mapped
game started within 20 minutes; `capture_pregame_cuts` lives inside that job,
so the scheduled T−6h/T−2h/T−30m cuts could never be captured by the
scheduler (one `capture-pregame-cuts` run in 24 h, 0 records; every 09-04 game
MISSED_SCHEDULER_RUN at T−6h/T−2h). Repair (commit `38124c2`):
`pm_pregame_capture_action` chooses finalize inside 20 min, otherwise the new
bounded `capture-prediction-market-pregame-cuts` job when a mapped game is
inside the widest cut offset (6 h), otherwise skips (no provider spend). Test:
1 (fail-before: symbol missing). Deployed to 8010 at 02:45:19 ET; the first
real due cut is 10:10 ET (outside the window) — unverified in real time.

### 7.3 Legacy pre-authority observations (Phase 8)
33,390 book-less Kalshi listing quotes (34,242 unmapped rows in total,
received 22:01–22:07 ET 09-04, before the persister was restricted to accepted
mappings at 22:08 ET) were counted as current `incomplete_order_books`. They
are now classified `legacy_pre_authority_observations` (34,242) in Excluded
Market Activity, excluded from the current book-quality count, preserved
unchanged, with no cleanup migration. 0 unmapped Kalshi rows after the cutoff.
NFL untouched.

Not repaired (out of authorised scope, recorded): the Kalshi v1 template
pattern excludes doubleheader phrasing (fails closed; a v2 is a human
decision); the Daily Command Center requests `/api/v1/games/` which returns
404 (pre-existing, unrelated to prediction markets).

## 8. Visualization (Phase 7)

One new visualization, "Pregame evidence capture — snapshot coverage per game,
provider and milestone", inside the Market Pulse audit drawer as an expandable
section, loaded only when opened (read-only `capture-coverage` endpoint, zero
provider calls). Rows = games; grouped columns KALSHI / POLYMARKET; sub-columns
FIRST / T−6h / T−2h / T−30m / T−5m / FINAL; each cell a glyph + text state
button; clicking reveals source timestamp, received time, minutes before first
pitch, cut label, midpoint/bid/ask, late-ingestion flag; a reconciliation line
checks counts against the slate. Live check: 180 cells = 15 × 2 × 6, states
{Captured 30, Not due 150}, "counts reconcile to the slate". No chart was added
to the $1,000 Experiment page. Screenshot:
`screenshots/pm_overnight_certification/market-pulse-pregame-evidence-capture.png`.

## 9. Tests (Phase 9)

New backend tests: `test_prediction_market_overnight_certification.py` (6) and
`test_prediction_market_capture_coverage.py` (4); new client test
`PregameCaptureCoverage.test.tsx` (3). Affected suites green: pregame
completion 26, canonical coverage, observer, credibility, scheduler/pulse
states (42). Full client suite 181/181; `tsc --noEmit` clean; `vite build`
OK; `git diff --check` clean. Full backend suite: 37 failures, identical set on
pristine `86ecaa6` (environmental: worktree lacks the untracked `.runtime/`
gate3c artefacts, SQLite ALTER in migration tests, time-of-day funnel budget
and the after-midnight credibility banner test). Fail-before proofs recorded
in the commit messages.

Coverage of the 24 mandated items: schedule ingestion (§3, live), enumeration
/ pagination (existing tests), mapping incl. doubleheader (existing +
live 09-04 DET@CLE), quote parser, point-in-time selection, duplicate
prevention, retry storm, terminalization, scheduler job dispatch, budget
accounting, UI reconciliation, legacy classification, no provider calls on
reads, no trading capability, no credential use.

## 10. Integration (Phase 10)

| Step | Result |
|---|---|
| Commits | `4dfecf9` fix (terminalization, legacy, coverage endpoint + UI, monitor script) · `617ccc8` tests · `cbf5f4a` docs packets · `38124c2` fix (scheduler gate) · `3130d24` test · report commit (below) |
| Fast-forward | primary `86ecaa6` → `cbf5f4a` (02:30 ET) → `3130d24` (02:45 ET); `main` unchanged; untracked files preserved; no push |
| 8010 restarts | via `start-backend.cmd` loop: 02:31:43 ET (pid 42360) and 02:45:19 ET (pid 34560); one listener each time; healthz 200; readyz ready/scheduler running; capture-coverage 200 |
| Migration | none required; head stays `20260904_0026` |
| Side stacks | previous task's 8011/5174 stopped at 02:25 ET; no side stack used by this task |
| Incident (my error, disclosed) | The 5173 dev server was found dead at 23:59 ET and restarted without the `VITE_API_URL` environment (the repo-root `.env` points Vite at `localhost:8000`, a different app), so Market Pulse showed "API unavailable" between 23:59 and 02:36 ET. Restarted from the client directory with `VITE_API_URL=http://127.0.0.1:8010/api/v1` at 02:36 ET (pid 43764); verified serving 8010 |

## 11. Live browser acceptance (Phase 11, headless Chrome over CDP, 02:37 ET)

| Page | Hosts requested | Console errors | Overflow |
|---|---|---|---|
| Daily Command Center | 5173, 8010 | 1 (pre-existing 404 on `/api/v1/games/`) | none |
| Market Board | 5173, 8010 | 0 | none |
| Crowd & Market Pulse | 5173, 8010 | 0 | none (capture matrix scrolls inside its container) |
| $1,000 Experiment | 5173, 8010 | 0 | none |
| Historical Research | 5173, 8010 | 0 | none |
| Shadow Board | 5173, 8010 | 0 | none |
| Model Performance | 5173, 8010 | 0 | none |
| Methodology | 5173, 8010 | 0 | none |

No kalshi.com / polymarket.com host appears in any page load. No in-play quote
is labelled pregame (all 09-05 games SCHEDULED; the 09-04 rows keep their
final pregame observation with in-play retained separately).

## 12. Window close-out

Monitor (`server/scripts/pm_capture_monitor.py`, read-only, 5-minute cadence,
expiry 08:15 ET): 99 samples from 00:01:42 to 08:14:20 ET, exited on its own at
08:15:00 ET (0 monitor processes remaining). Every sample: healthz 200, readyz
ready, exactly one 8010 listener, 0 DB errors. RSS 357–453 MB on the pre-restart
process, 135–266 MB after the 02:45 restart (260 MB at close). PostgreSQL
connections 2–9. Full sample file: `pm_capture_matrix/pm_capture_monitor_2026-09-05.json`.

Final capture matrix (08:16 ET, `pm_capture_matrix/AISP_PM_CAPTURE_MATRIX_2026-09-05.*`):
FIRST_DISCOVERED CAPTURED 15/15 on KALSHI and 15/15 on POLYMARKET; T−6h, T−2h,
T−30m, T−5m and FINAL_VALID_PREGAME NOT_DUE 15/15 each (earliest T−6h window
opens 10:10 ET). 0 captured cells with a source time at or after first pitch;
0 LATE_INGESTION flags; 0 duplicate snapshots in the window. Twelve of the
fifteen games already carry T−12h-labelled refresh observations on both
providers, which confirms live source-time labelling on the repaired backend.

Scheduler activity in the window (receipt
`AISP_PREDICTION_MARKET_OVERNIGHT_PROVIDER_CALL_RECEIPT.json`): discover 16 Kalshi
runs (94 requests, 0 errors) + 16 Polymarket runs; refresh 48 runs, 3,978
requests, 437 errors — 435 of them the pre-repair closed-book 404 storm
(22:35–00:27 ET) and 2 isolated Polymarket book timeouts after the repair
(06:47 and 07:47 ET, 1 of 101 requests each, classified `timeout`, retryable,
nothing terminalized). Every post-repair run carries a `status_distribution`;
`terminalized_count` 0 (no closed books were requested after the repair). The
5-minute pregame tick ran throughout and correctly skipped
(`no_mapped_game_in_window`) because no mapped game was inside 6 h.

Task-initiated provider calls: 3 (Kalshi rulebook PDF, Polymarket Gamma events,
MLB Stats API schedule) against a cap of 250; no credentials, no Odds API, no
trading endpoint, no wallet.

Protected entities at close: experiments 3, model_versions 2, positions 10,
settlements 9, semantic templates 2 (both HELD_CLOSED), CLV unchanged.
Forecasts 293 → 306 and decisions 12,834 → 13,293 changed only through the
scheduler's own daily pipeline and policy jobs for the 09-05 slate — no write
by this task touched them. Legacy pre-authority observations: 34,242,
preserved. Snapshots 116,615 → 124,071; database 3,601 → 3,838 MB.

Observed but out of scope (not repaired): the unrelated `finalize-games` job
logs a `uq_team_rating_observation` unique-violation for event_date 2026-09-04
on every run; the Daily Command Center requests `/api/v1/games/` (404).

Verdict rationale: discovery, mapping and FIRST_DISCOVERED capture are certified
for the full slate on both providers with point-in-time correctness; the timed
cuts and the final pregame observation could not become due inside the bounded
window, and the repaired cut-capture path therefore has no real-time capture
evidence yet. The first real check is the 10:10–10:15 ET tick for SF@NYM and
CHC@MIA (expected: a `capture-prediction-market-pregame-cuts` JobRun and two
T_MINUS_6_HOURS observations per provider).
