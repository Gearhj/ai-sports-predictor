# CFB consumer integration — staged, not active (2026-09-18)

CFB stays research-only in AISP. Nothing here imports a forecast, admits a game, creates a decision, spends a provider
object or activates a surface. What exists is an independent verifier and a documented activation path.

## 1. Independent validation of what Codex published

The checks run from AISP code (`server/app/cfb/production_discovery.py`), reading bytes only.

| Item | Result |
|---|---|
| Staged handoff `cfb-gen1-production-v1/staged-handoffs/sha256/00b4d887…` | Index digest equals the directory name. All 6 members match their SHA-256 and byte counts, with no extra files. Release `5c853df0…` verified. `consumerActivated:false`, so the verdict is **STAGED_AWAITING_ACTIVATION**. |
| Discovery pointer `slate-discovery/current.json` | Schema `CFB_GEN1_SLATE_DISCOVERY_POINTER_V1`. The manifest `d8388f7d…` (577,946 B) is stored at its content address. Schema `CFB_GEN1_SLATE_DISCOVERY_V1`. Its release equals the handoff's pinned release. |
| Dispositions | 3,679 CFBD games: 57 `PUBLISHED_NEW_PROSPECTIVE`, 241 `NOT_DUE`, 101 `GAME_NOT_SCHEDULED` (final), 3,280 `EXCLUDED` (outside FBS-vs-FBS regular weeks 1–14). |
| Package schema | All 57 pass the existing `research_import.verify_package`: index address, `CFB_GEN1_PRIVATE_RESEARCH_PACKAGE_INDEX_V1`, research-only, AISP and wagering authority false, every member digest and byte count. Configuration is `CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1`. |
| Publication receipts | For all 57, the discovery-bound receipt matches its SHA-256 and bytes. The receipt's forecast, game, package address, revision key and `committedAtUtc` all equal the manifest entry. Publication time is taken as `committedAtUtc`, never the null prepared `publishedAtUtc`. |
| Clocks | For all 57: source cutoff ≤ creation ≤ committed publication ≤ kickoff − 30 min (the producer's close). |
| 10,000-draw populations | For all 57, recomputed from `baseline-joint-draws.parquet`: 10,000 rows, total = home + away, margin = home − away, no ties, no negative scores. The joint home-win probability is reproduced exactly, and all four PMFs carry the full 10,000 mass. |
| Orientation | For all 57, the home/away CFBD school ids match an **independent** raw CFBD `/games` capture (2026-09-14, native `homeId`/`awayId`). They also match the producer's own hash-bound input. The one game AISP already admitted (401869940, Coastal Carolina @ Delaware) matches AISP's own CFBD id mapping. |
| Leases | All 57 were `currentlyEligible` with an unexpired lease at check time. Leases end at the next tick, so they are current only while the publisher keeps refreshing. |
| Earlier research packages `72a91840…` (Syracuse–Pitt) and `223bbc35…` (Coastal–Delaware) | Pass the same population, receipt, clock and orientation checks. They stay research artifacts. |

Kickoffs covered: 2026-09-18 23:30 UTC through 2026-09-24 23:30 UTC. The first is Miami–Wake Forest, 401858226.

## 2. What is staged in code

- `app/cfb/production_discovery.py`: pointer → manifest → package → receipt → population → orientation → lease
  verification. It returns a report and never writes.
  - `production_status(settings)` is `NOT_RECEIVED` unless `CFB_PRODUCTION_HANDOFF_PATH` and `..._SHA256` are pinned.
  - A verified handoff with `consumerActivated:false` stays `STAGED_AWAITING_ACTIVATION`.
  - `ACTIVE` also needs `CFB_PRODUCTION_CONSUMER_ENABLED=true`.
  - Only `ACTIVE` allows a production label.
- Config keys (all empty or false): `cfb_production_handoff_path`, `cfb_production_handoff_sha256`,
  `cfb_production_consumer_enabled`.
- Tests: `tests/test_cfb_production_discovery.py` (7). They cover a clean slate, reversed orientation, a tampered
  member, an expired lease, a receipt clock mismatch, a wrong release or bad pointer, and not-received / staged /
  rejected handoffs.

## 3. Consumer surfaces: what each needs before activation

| Surface | Today | Activation requires |
|---|---|---|
| CFB forecasts (`/college-football/research`) | Reads research packages imported by `import_cfb_research_package.py` (package-local receipt). | An importer that takes packages from verified discovery entries only. It must bind the discovery receipt's `committedAtUtc` as the publication clock and admit games through `game_binding.admit_game` (exact CFBD ids, never names). A lease that is expired or `historical` is shown as historical, never current. |
| Daily Decisions | CFB links to research only (`PRICED_LEAGUES=("MLB","NFL")`). | A current price source (section 4) and a CFB decision owner. **No decisions are manufactured before fresh governed packages and prices exist.** |
| Show Prep | Reads the research forecast. CFB preview collection refuses as `SGO_LEAGUE_IDENTITY_UNVERIFIED`. | Discovery-bound forecasts. SGO NCAAF league identity verified (section 5). |
| A House Divided | CFB is "Not started". `official_experiment("CFB")` returns None. | A CFB paper decision owner, the equivalent of `nfl_paper_decision.py`, for `AISP_CFB_PAPER_POLICY_V1`. It would run at T−30…T−15 against a verified current package and a current price. Timing is tight: the producer closes at T−30 and AISP locks picks at T−15. The owner's cadence must be confirmed before the sport is started. |
| Final-score settlement | **None.** CFB games stay `SCHEDULED`. | A governed CFB final: a CFBD `/games` completed-score capture or the producer's hash-bound input schedule (`STATUS_FINAL` with scores), recorded as a `GameStateObservation`. It moves the game to FINAL through a `ingest-cfb-finals` step modelled on `nfl_final_ingest`. The existing market rules then settle unchanged (a CFB tie cannot occur; the moneyline tie rule already refunds). |

## 4. CFBD `/lines` as a current-market source

**Proven locally:**
- AISP has no CFBD client and no CFBD key setting.
- The producer's client (`asp-model-studio-source-…/scripts/acquire_cfb_gen1_current_v1.py`) never calls `/lines`: 25-call and 50 MiB caps, no retries, Bearer `CFBD_API_KEY`.
- The key is **Free tier: 1,000 calls/month, shared pool, 995 remaining, resets 2026-10-01**.
- `/lines` has never been called on this key, and no `/lines` payload exists on disk.
- The stored `/games` capture has no betting fields.
- The producer itself records that CFBD supplies no per-record publication or revision timestamp for the endpoints it uses.

**Not provable without a call** (known only from CFBD's public schema):
- per-book spread, total and home/away moneylines;
- whether spread and total prices (the juice) exist;
- whether any per-line observation timestamp exists;
- whether lines are current or closing;
- which books appear;
- whether the `/lines` id equals the `/games` id.

**What AISP needs from a price source:**
- current pregame, book-specific moneyline, spread with its price, and total with its price;
- a source observation time for the 240-minute freshness rule and the moved-price check;
- a stable game identity.

Without a per-line observation timestamp, CFBD lines cannot satisfy the freshness rule. They could at most be stored as
reference lines, never as wagerable prices.

### One bounded live validation request (not made; needs approval)

| | |
|---|---|
| Request | `GET https://api.collegefootballdata.com/lines?year=2026&week=4&seasonType=regular` (one week: the next Saturday slate) |
| Client | The producer's existing capture pattern: Bearer `CFBD_API_KEY` from its `.env.local`, response written raw to the private-sources area with a call ledger (bytes, SHA-256, status). AISP stores only derived fields. |
| Limits | 1 request, **0 retries**, 120 s timeout, **max 5 MiB** response (aborted and discarded above that). |
| Entitlement impact | 1 of 1,000 monthly Free-tier calls (995 → 994), shared pool, $0. No other endpoint is called. |
| Admission criteria (all required to proceed) | 1. ≥ 95% of the week's published slate game ids match CFBD `/games` ids exactly. 2. At least one named book (not only a consensus line) with both moneylines, spread with its price, and total with its price for ≥ 80% of those games. 3. A per-line source observation or update timestamp. 4. Values that are demonstrably pregame (changing through the week, not only opening or closing). |
| Outcome if 3 fails | CFBD becomes a **reference line source only**: labelled, never selectable, and never used for decisions or House Divided wagers. |

## 5. SportsGameOdds fallback (no CFB objects spent yet)

**September:**
- 1,981 of 2,500 objects used; the 300 reserve is untouched.
- The demand-shaped plan needs about 206 more for MLB and NFL through month end.
- That leaves about **13 spare objects**, and they are already spread across days as slack.

**Featured-slate fallback for September:**
- A new lane `sgo_cfb_events`, funded **last**, only after that day's MLB and NFL lanes are fully funded.
- It is capped at `min(month slack after all MLB and NFL need, 10)` objects for the rest of September, keeping 3 of the 13 as a safety margin.
- One object per featured game, bought once inside the 240-minute window before that game's T−15 lock.
- Candidates: the Saturday 2026-09-26 slate only, featured games chosen by a fixed rule declared in advance (for example, the earliest kickoffs among games with a current verified package). Never by edge or forecast.

**Prerequisite, one object, not spent:**
- Verify SGO's NCAAF league identity and event-to-CFBD-id binding on one stored or purchased event.
- CFB Show Prep collection currently refuses with `SGO_LEAGUE_IDENTITY_UNVERIFIED` for exactly this reason.

**October (after the 2026-10-01 reset), full slate:**

Assumptions: the same 2,500 cap and 300 reserve; MLB postseason about 1–15 objects a day for a few weeks; NFL about 16 games a week with checkpoints and previews (≈ 32 objects a week, ≈ 140 a month).

CFB need:
- ≈ 55 FBS-vs-FBS games per Saturday × 1 object ≈ 275 for five Saturdays;
- plus ≈ 25 midweek games;
- **≈ 300 objects a month**.

Everything fits under the shaped allocator with CFB as a third demand lane counted by service day, the same way NFL previews are. This needs a one-line addition to `_day_need` for CFB once the lane exists. It must be designed and approved before October 1; no quota increase is required.

## 6. Decisions needed from the owner

1. Approve (or decline) the single CFBD `/lines` validation request above.
2. Approve the producer's bounded source-refresh packet `40f4a5dc…` and the single-owner publisher installation. Both are Codex-side and already specified by the handoff.
3. After 1 and 2: approve implementing the discovery importer, the CFB final ingest and a CFB decision owner. Each is additive and flag-gated, and CFB stays research-only until the handoff is pinned and the consumer switch is turned on.
4. Approve (or decline) the September featured-slate SGO lane (≤ 10 objects) and the one-object NCAAF identity check.
