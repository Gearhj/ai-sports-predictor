# AISP Prediction-Market Pregame Completion — Kalshi & Polymarket MLB evidence, Crowd & Market Pulse visual layer

Date: 2026-09-04 (America/New_York) · Operator: Claude Fable 5.1, non-elevated session as `james` · Nothing pushed · main untouched · No account, key, wallet or trading endpoint used · Provider calls by this task: **226** (all public GET, listed in §4) · Trades placed: 0 · Experiment/forecast/decision/position/settlement mutations: 0.

## 1. Branches
- Start: primary `codex/multisport-platform-architecture` @ `32c9bc839497c0e2c6ecfbbce85ac39c8f14a654`; main `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b`.
- Work: worktree `C:\Users\james\worktrees\aisp-pm-pregame`, branch `claude/prediction-market-pregame-completion` from `32c9bc8`. Commit hashes and the final primary HEAD are in the final response (integration section).

## 2. Forensic baseline (Phase 1) — reproduced against public endpoints and persisted state
Before any change, persisted state read on 2026-09-04 21:25 ET:

| Provider | Contracts | Events | Mappings (latest) | Snapshots |
|---|---|---|---|---|
| POLYMARKET | 2,448 | 143 | 67 EXACT / 143 UNRESOLVED, all `SEMANTICS_REVIEW_REQUIRED` | 73,433 |
| KALSHI | 6,794 | 772 | 772 UNRESOLVED (720 review-required, 38 mismatch, 14 matched) | **0** |

Live board (old code) for the 16-game 2026-09-04 slate: Polymarket 3 `QUOTED_UNREVIEWED` + 13 `QUOTED_STRUCTURALLY_EXCLUDED`; Kalshi 16 `NO_MAPPED_MARKET` with reason "not listed, or not discovered"; "Semantics review pending: 16".

Per-game reconciliation (canonical id, official start, provider event/market ids, title, normalized type, status, observation times, pregame-before-first-pitch, orientation, bid/ask/mid/last/depth, mapping and semantic status, exclusion reason) was produced for **2026-09-03 (9 games, completed)**, **2026-09-04 (16 games, includes the DET @ CLE doubleheader, games 1 and 2)** and the open **2026-09-05 Kalshi listing (93 events; AISP has no 09-05 games loaded yet)**. The per-game tables are reproduced in §6–§7 from persistence after the fix; the raw per-provider values (bid, ask, midpoint, last, depth, source time, cut) are exposed on every board row (`providers[]` and `provider_coverage[]`) and on the game detail endpoint.

### Root causes (each reproduced, none inferred from prose)
| # | Symptom | Cause | Category |
|---|---|---|---|
| 1 | Kalshi 16 × NO_MAPPED_MARKET | `_discover_kalshi` listed Kalshi series with `category=Sports`, kept the first **five** whose title/ticker mentioned "mlb"/"baseball" (`[:5]`) and queried `/markets?series_ticker=` for those. `KXMLBGAME` was never among them; the persisted 6,794 Kalshi contracts were prop series (KXMLBHIT, KXMLBTB, KXMLBERA, KXMLBSB, KXMLBF5SPREAD…) | enumeration / series classification |
| 2 | Kalshi mapping "No AISP game near the provider start time" | `scheduled_start_at` was taken from the market `open_time` (listing time, days earlier), not the game time encoded in the event ticker (`KXMLBGAME-26SEP041915DETCLEG2` = 2026-09-04 19:15 ET) and the rules text | event date/time matching |
| 3 | Kalshi mapping "No AISP game matched the provider teams" | team identity was the YES sub-title ("Los Angeles D", "New York M", "A's") fed to the generic name matcher; the ticker's exact `<AWAY><HOME>` abbreviations were ignored | team aliases / orientation |
| 4 | Kalshi never quoted | `refresh()` skipped Kalshi whenever `kalshi_api_key_id` was empty ("order books require API credentials") — false: `/markets/{ticker}/orderbook` answered 200 without any header | credentials (incorrect assumption) |
| 5 | Kalshi books parsed empty | adapter read `orderbook.yes/no` while the API returns `orderbook_fp.yes_dollars/no_dollars` and `*_dollars` price fields | quote availability / payload shape |
| 6 | Polymarket 13/16 "structurally excluded" | persistence held **hundreds of valid pregame observations per game** (e.g. 734, 879, 1,230 pregame snapshots) but the board reduced each (game, provider) to its **latest** snapshot, which is in-play/final once the game starts | display reduction |
| 7 | "Semantics review pending: 16" confusing | the number is a per-provider overlapping count (both providers' events awaiting review), presented as a headline | display reduction |
| 8 | Zero reviewed semantics | the only approval path was a per-event manual mapping approval; no contract-family authority existed | semantic authority |
| 9 | Old Kalshi prop contracts labelled `FULL_GAME_MONEYLINE` | persisted by an earlier adapter version; the current classifier already rejects them (they remain as append-only history and are excluded from mapping by series) | market-type classification (historical rows) |
Not causes: provider availability (all endpoints 200), pagination (the open KXMLBGAME listing fits one page of 200; settled history needs cursoring, now implemented), quote timing for Polymarket (pregame observations existed), persistence (append-only rows intact).

## 3. What was built
- **Kalshi public REST path** (`app/prediction_markets/kalshi.py`): documented host `https://api.elections.kalshi.com/trade-api/v2` (settings default changed from `external-api.kalshi.com`); `get_series` verifies series metadata dynamically (KXMLBGAME = "Professional Baseball Game", Sports, tags Baseball, MLBGAME certification PDF); `discover_game_events` enumerates `/events?series_ticker=&status=&with_nested_markets=true` with cursor pagination (bounded pages; settled reconstruction stops once a page predates the target window); event tickers decode start time (ET), away/home abbreviations and doubleheader ordinal, cross-checked against `rules_primary`; the YES team is the ticker suffix; explicit HOME/AWAY selection at discovery; top-of-book quotes (`yes_bid/ask/last_price_dollars`, sizes, volume, OI, liquidity) are carried by the listing; `get_order_book` parses `orderbook_fp` and the legacy shape; `requires_credentials` is False and no header is ever attached; series classification distinguishes MONEYLINE / SPREAD / TOTAL / PARTIAL_GAME (F5*, INNINGWIN, RFI, TEAMTOTAL) / PLAYER_PROP (HIT, TB, ERA, SB, K…) / FUTURES (WINS, CHAMP…) / COMBO / OTHER_LEAGUE.
- **Explicit alias registry** (`aliases.py`): Kalshi ticker abbreviations → AISP, Kalshi display names → AISP, Polymarket full names → AISP; unregistered labels fail closed.
- **Mapping** (`mapping.py`): abbreviation-exact identity in the stated orientation; doubleheader ordinal (`G1`/`G2`) selects between same-team games; without an ordinal only an unambiguous exact-window start decides, else AMBIGUOUS; reversed teams → REVIEW_REQUIRED.
- **Quotes without credentials** (`jobs.py`): one public events listing per league per refresh answers every mapped Kalshi market; per-provider request/error/snapshot counts in the run record; listing quotes are persisted only for contracts with an accepted mapping.
- **Pregame capture schedule** (`jobs.py` `capture_pregame_cuts`, wired into the 5-minute finalize job): T−6h, T−2h, T−30m, T−10m, T−5m (new `T_MINUS_5_MINUTES` cut) per mapped slate moneyline HOME contract, idempotent by persisted `timeline_cut`, source-time governed; the final pregame observation is derived at read time (latest source time strictly before first pitch); a delayed ingestion beyond 120 s after first pitch never qualifies as pregame (`LATE_INGESTION` flag, no cut).
- **Polymarket pregame authority** (`read_service.py`): the board price is the final valid pregame observation (pregame-eligible, not in-play, two-sided midpoint); the latest in-play/final observation is retained and reported separately (`latest_in_play`, `latest_observed_at`); `observation_kind` = FINAL_PREGAME; a game whose only observations are in-play/final is `EXCLUDED_IN_PLAY`.
- **Semantic template authority** (`semantics.py`, migration `20260904_0025`, model `PredictionMarketSemanticTemplate`, routes `GET /prediction-markets/semantic-templates` and admin `POST /admin/prediction-markets/semantic-templates/decide`): versioned, append-only, seeded HELD_CLOSED; two templates (§9); markets inherit `SEMANTICS_MATCHED` only from an APPROVED template whose every condition they satisfy (series/sportsMarketType, exact rules pattern, named team = YES side / outcome label, teams = mapped canonical game, effective date); the legacy keyword recognizer can only flag MISMATCH, never approve.
- **Disjoint headline** (schema `GameEvidenceState`, `coverage.evidence_state_sum`, `coverage_by_provider`, `semantic_template_decisions_pending`; new coverage state `PROVIDER_ERROR`).
- **Timeline** for the movement view: per-observation Polymarket/Kalshi points with pregame flags, ASP forecast versions, sportsbook no-vig median per observation minute, decision-cutoff (T−15m) and first-pitch markers; points exist only where a source observed something.
- **Client** (`MarketPulsePage.tsx`, `MarketPulseVisuals.tsx`, chart registration of scatter/custom/mark-line): six disjoint state cards + reconciliation line; Market Probability Comparison (one 0–100% axis per game, ◆ ASP, ■ sportsbook, ● Polymarket, ▲ Kalshi, filled = reviewed, hollow grey = research-only, excluded gets no mark, grey bar joins min/max reviewed market probabilities, sorted by disagreement, tooltip with source time, kind/cut, bid, ask, mid, last, depth, spread, mapping authority, semantic authority, freshness, plus a text table); Provider Coverage Matrix (rows = games, columns = Sportsbook/Polymarket/Kalshi, glyph + label per cell); Selected-game Market Movement (ASP, sportsbook no-vig, Polymarket, Kalshi, `connectNulls: false`, decision cutoff and first pitch mark lines); overlapping provider counts, provider health, excluded activity and job registration moved into an "Audit detail" drawer.
- **Read-model performance**: the excluded-activity summary previously materialised every 24-hour snapshot row through the ORM (7.5 s of a 8.5 s board once Kalshi rows existed); it is now reduced in SQL; composite index migration `20260904_0026` on `(canonical_game_id, provider, market_type, provider_observed_at)`.

## 4. Provider calls (exact)
Diagnosis (Phase 1): Kalshi `GET /series/KXMLBGAME`, `GET /markets?series_ticker=KXMLBGAME&status=open&limit=200`, `GET /markets/KXMLBGAME-26SEP072110CINLAD-LAD/orderbook`, `GET /events?series_ticker=KXMLBGAME&status=open&with_nested_markets=true&limit=200`, `GET /events?series_ticker=KXMLBGAME&status=settled&limit=200`; Polymarket Gamma `GET /events?tag_slug=mlb&…` and `GET /events?slug=mlb-nyy-sd-2026-09-04` → **7 calls**, all HTTP 200, no credentials.
Acceptance (Phase 9, through the observer jobs with an httpx counter): **219 calls** — Kalshi `/events` ×91 (200), `/series/{KXMLBGAME,KXMLBSPREAD,KXMLBTOTAL,KXNFLGAME,KXNFLSPREAD,KXNFLTOTAL}` ×18 (200); Polymarket Gamma `/events` ×6 (200), CLOB `/book` ×104 (81 × 200, 23 × 404 for closed markets — recorded as errors, nothing fabricated). Total this task: **226**. The 09-03 settled reconstruction paged the whole settled history (36 requests) before the date-window bound was added; it is now capped at 3 pages and stops once a page predates the window. Rate-limit headers were never returned; no 429 was observed. The Odds API was not called.

## 5. Credentials
Neither provider required any. Kalshi: `requires_credentials = False`, requests carry only `Accept` and `User-Agent`; the empty `KALSHI_API_KEY_ID` no longer skips anything (`skipped == {}` and `credentials_used == False` are persisted in every run). Polymarket Gamma/CLOB reads: no key, wallet or signature. The authenticated WebSocket stays behind `KALSHI_MARKET_DATA_WS_ENABLED=false` and is not implemented for trading.

## 6. Kalshi market inventory and mapping reconciliation
Live KXMLBGAME (open): 54 events / 108 markets on 2026-09-04 21:25 ET; 14 for 09-04 (incl. `…DETCLEG2`), plus 09-05…09-07. After the fix, every canonical game on both slates maps EXACT to its KXMLBGAME winner event and to the matching KXMLBSPREAD/KXMLBTOTAL events:

| Slate | Games | Kalshi winner events mapped EXACT | Doubleheader | Semantics |
|---|---|---|---|---|
| 2026-09-03 | 9 | 9 / 9 (`KXMLBGAME-26SEP031235SFPIT` … `…2210STLLAD`, from the settled listing) | — | held closed (template HELD) |
| 2026-09-04 | 16 | 16 / 16 (`…1410DETCLEG1` → game 1 at 14:10, `…1915DETCLEG2` → game 2 at 19:15) | resolved by ordinal | held closed |
Unmapped Kalshi game events: 0 for either slate. Kalshi quotes now persisted: 41,199 snapshots (this includes settled markets' last-price rows written before the persister was restricted to mapped contracts; they carry no book and are `EXCLUDED_IN_PLAY`). Genuine **pregame** Kalshi evidence captured tonight: WSH @ LAD and ATH @ SEA (22:10 ET first pitch) — 59 observations each before first pitch, final pregame at 22:07:12 ET (midpoints 0.715 and 0.675 home), shown as `QUOTED_UNREVIEWED` / FINAL_PREGAME with the in-play observations retained separately.

## 7. Polymarket valid-pregame / unreviewed / excluded reconciliation (2026-09-04)
15 of 16 games now show their **final valid pregame observation** (cut T−10m or T−5m; 0–5 minutes before first pitch), all `QUOTED_UNREVIEWED` (template HELD); DET @ CLE game 1 has no pregame observation (its captures began after first pitch) and is `EXCLUDED_IN_PLAY`. 2026-09-03: Polymarket moneyline contracts were mapped but never quoted by the old refresh (`MAPPED_NOT_QUOTED` ×9, honest), Kalshi settled markets carry no book (`EXCLUDED_IN_PLAY` ×9): 9 = 9.

## 8. Root causes of every previously missing state
See §2. Previously hidden Polymarket pregame evidence was recovered purely by the read-model reducer; no observation was re-fetched or rewritten.

## 9. Provider-template semantic authorities created (both HELD_CLOSED, v1)
- `KALSHI_MLB_FULL_GAME_WINNER` (source hash `ec56dba82996…`): series `KXMLBGAME`; rules pattern `If <team> wins the <matchup> professional baseball game originally scheduled for <when>, then the market resolves to Yes.`; extra innings included (resolves on the game's winner); YES = named team; HOME/AWAY from `<AWAY><HOME>` ticker; settlement sources ESPN / Fox Sports / mlb.com; effective 2026-09-04.
- `POLYMARKET_MLB_FULL_GAME_WINNER` (source hash `55f0ba1d5c46…`): `sportsMarketType == moneyline`, tag `mlb`; description pattern "In the upcoming MLB game between the A and B … resolve to "A" if the A win the game … "B" if the B win the game … postponed → remains open … canceled/tie → 50-50"; outcome labels = the two team names; HOME/AWAY from the event title "Away vs. Home"; effective 2026-09-04.
Review packet: `GET /api/v1/prediction-markets/semantic-templates` (conditions, hash, status, effective date, one clearly worded decision per template).

## 10. Remaining human decisions (one per provider)
1. Approve or reject `KALSHI_MLB_FULL_GAME_WINNER` v1.
2. Approve or reject `POLYMARKET_MLB_FULL_GAME_WINNER` v1.
`POST /api/v1/admin/prediction-markets/semantic-templates/decide?provider=KALSHI&template_key=KALSHI_MLB_FULL_GAME_WINNER&status=APPROVED` (admin, audited, appends v2). After approval the next discovery cycle re-evaluates every contract; exact matches inherit `SEMANTICS_MATCHED`, deviating rules stay held. Consensus additionally requires two reviewed providers with valid pregame observations on the same canonical game and outcome within the freshness limit.

## 11. Disjoint status counts (live 09-04, side stack)
Reviewed pregame 0 · pregame awaiting review 15 · in-play/final excluded 1 · listed not quoted 0 · no mapped market 0 · provider unavailable 0 = **16 of 16 — reconciles** (`evidence_state_sum == canonical_games`). Per-provider (overlapping, audit drawer): Polymarket 15 unreviewed + 1 excluded = 16; Kalshi 2 unreviewed + 14 excluded = 16. 2026-09-03: 0/0/9/0/0/0 = 9 of 9.

## 12. Screenshots
Captured in the session on the side stack (5174 → 8011) at the pane's natural width, plus 1920×1080, 1024×768 and 390×844 checks: the Market Probability Comparison (16 rows, ◆/■ filled, ○ hollow research-only, reviewed-range bars), the Provider Coverage Matrix (16 rows × 3 providers with ●/○/✕ glyphs and labels) and the Selected-game Market Movement (WSH @ LAD: ASP, sportsbook no-vig, Polymarket, Kalshi, decision-cutoff and first-pitch mark lines, 241 observation minutes, gaps left open). The rendered SVG markup of both charts and the matrix table was exported live for verification (comparison 21.8 KB, movement 12.1 KB, matrix 15.0 KB).

## 13. Tests and build
Server (worktree): `test_prediction_market_pregame_completion.py` 26/26 new (no-key REST with no auth header; pagination across three pages finds the last-page slate event; series classification 15 cases; prop/partial/futures/combo exclusion; ticker time/teams/orientation with rules cross-check; doubleheader distinctness; Polymarket token orientation; final pregame survives in-play and final; delayed persistence never pregame; disjoint sums; consensus gating; forecasts/decisions/positions unchanged; no trading capability in either adapter; template authority held → inherited → ambiguous held); `test_prediction_market_observer`, `test_market_pulse_canonical_coverage` (credential-skip test rewritten to assert the opposite), `test_qualification_and_pulse_states`, `test_experiment_credibility_evidence`, `test_fable51_pm_alert_idempotency`, `test_shadow_experiment`, `test_experiment_identity_v21`, `test_bankroll`: all pass. Client: `MarketPulsePage.visuals.test.tsx` 7/7 new (disjoint sums, audit drawer, mark styling, hollow research-only series, matrix states, no `connectNulls`, markers), older Market Pulse tests updated to the new headline; full suite **178/178**; `tsc --noEmit` clean; `vite build` succeeded; `git diff --check` clean.

## 14. Page latency (board endpoint, 2026-09-04)
| Point | Warm | Note |
|---|---|---|
| 8010 old code, before this task's data | 2.69 s | 32.4 KB |
| 8010 old code, after Kalshi rows existed | 6.70 s | data growth exposed the ORM scan in the excluded summary |
| 8011 new code, before the SQL reduction | 6.55 s | 53 KB |
| 8011 new code, after | **2.11 s** (in-process 2.00 s) | 53 KB (adds per-provider observation detail) |

## 15. Protected tables and mutations
experiments 3 → 3; decisions 12,834 → 12,834; positions 10 → 10; results 8 → 8; forecasts 293 → 293; model_versions 2 → 2 (before and after every provider run and every page read; page-read invariance verified across board, detail, templates and bankroll reads). Append-only prediction-market tables grew (events 932 → 3,617; contracts 9,290 → 29,579; mappings 983 → 3,666; snapshots 74,553 → 116,156). Zero trades, zero orders, zero decisions/positions/settlements/bankroll changes.

## 16. Quota / rate limits
No rate-limit headers or 429s from either provider. Steady-state cost per refresh cycle is now one Kalshi events listing per league (+ up to ~16 order-book calls for the T−cut captures) and one Polymarket book per mapped token; the discovery job uses 2 series lookups + ≤10 listing pages per full-game series.

## 17. Files
Server: `app/prediction_markets/{aliases.py (new), semantics.py (new), kalshi.py, jobs.py, mapping.py, service.py, read_service.py, contracts.py}`, `app/schemas/prediction_market.py`, `app/models/{prediction_market.py, __init__.py}`, `app/audit/immutability.py`, `app/core/config.py`, `app/api/routes.py`, `alembic/versions/20260904_0025_…`, `20260904_0026_…`, `tests/test_prediction_market_pregame_completion.py` (new), `tests/test_market_pulse_canonical_coverage.py`, `tests/test_experiment_credibility_evidence.py`. Client: `src/components/MarketPulseVisuals.tsx` (new), `src/pages/MarketPulsePage.tsx`, `src/pages/MarketPulsePage.visuals.test.tsx` (new), `src/pages/MarketPulsePage.{coverage,reconciliation}.test.tsx`, `src/charts/AnalyticsChart.tsx`, `src/types/domain.ts`, `src/styles.css`. Docs: this report.

## 18. Pushes
None.

## 19. Remaining genuine blockers / backlog
- Both semantic templates await James's decision (held closed by design); until then no game is "reviewed", no consensus forms and no AISP–market gap is claimed.
- AISP had no 2026-09-05 games loaded at acceptance time, so tomorrow's Kalshi pregame quotes (503 listing quotes across 93 open events) could not be mapped yet; they map automatically once the schedule exists.
- The NFL series (`KXNFLGAME`) is enumerated but its team pairs are not in the (MLB-only) alias registry; they are counted as excluded, not mapped — an NFL registry is a separate task.
- Historical settled Kalshi last-price rows written before the persister was restricted remain as append-only observations and inflate the "incomplete order books" audit count.
- The single-worker CPU cost of the board (~2 s) and the $1,000 Experiment page were unchanged and unaffected (experiment page re-verified on the side stack: V2.1-first, ROI −1.1 % over $20.00 settled, drawdown $10.00, 10 ledger rows, no new performance graphics added — thresholds 20/50/100 settled positions not met).
