# NFL baseline: verified and returned to service

**Snapshot** 2026-09-12T19:52:33Z (coverage table) · job/state checks through 20:23Z
**Branch** `claude/aisp-v18-and-price-coverage-v1` · **Commit** `648f243`

---

## 1. Verdict

# `NFL BASELINE: OPERATING`

**Model identity, resolved from the live system rather than the filename:**

| | |
|---|---|
| Engine | `NFL_M1_XGBOOST` |
| Engine version | `NFL_M1_2026_RESEARCH_SHADOW_V1` |
| Model binary SHA-256 | `7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd` |
| Model manifest SHA-256 | `ab9c066052b288f3f6162d8f8558e673988fb52172c72fe00aa855aa800a45e9` |
| Feature contract SHA-256 | `ef5c4f9ce8c3dd04a473e7ecb2ea3a255154f65a1bb3fda9d2ebc19bf1cb8f4d` |
| Training cutoff | 2026-02-08 |
| Registered inputs | 197 pregame inputs, tier `LONG_HISTORY_CORE` |
| Authority | `RESEARCH_SHADOW` — not executable, no wagering authority |

The binary hash matches the handoff. The manifest and feature-contract hashes
are the rest of the contract and are verified by AISP against its frozen
authority on every producer run, so the identity rests on the registered
contract rather than on a filename.

It was **not** operating when I started: the producer had failed on every run
since 2026-09-11, and one of the two completed games could not be scored. Both
causes were AISP guards, not the model. Both are fixed and verified in
production.

---

## 2. Counts, with units and the overlaps named

Counts are of **distinct canonical games** unless stated otherwise. Stages
overlap: a game counted as forecasted is also counted as scheduled.

| Stage | Count | Unit / note |
|---|---:|---|
| Scheduled, stored | **272** | games, full 2026 season through 2027-01-10 |
| Scheduled, next 7 days | **15** | games — this is current-week coverage |
| Forecasted (upcoming) | **15 of 15** | games with a linked `CURRENT` M1 forecast |
| Imported | **15 of 15** | same games; every forecast is already an imported row |
| Displayed | **15 of 15** | same games; route verified for one, see §3 |
| Completed so far | **2** | games (NE@SEA, SF@LAR) |
| Awaiting final | **0** | games — both completed games now have governed finals |
| Scored | **2 of 2** | games with a current prospective evaluation |

Separated stage counts, because these are different units and were previously
conflated:

| | Count | Unit |
|---|---:|---|
| M1 forecast **rows** | 2,178 | rows = 1,906 `CURRENT` + 272 `EARLY` |
| Forecast **revisions per game** | 8 | immutable snapshots kept (7 `CURRENT` + 1 `EARLY`) |
| **Bundles** selected by the locator | 401 | 57 current + 344 historical |
| Imported **identities** | 2,450 | forecast children across all bundles |
| **Evaluated** games | 2 | 3 evaluation rows, 2 current (one supersession) |

The 2,178 figure from the prior inventory is **forecast rows for the whole
season**, not a current-week game count — 15 is the current-week game count.
NFL Labs supplements are a separate engine and are excluded from every number
here; no MLB engine, historical replay or Labs row is mixed in.

---

## 3. Per-game coverage, and one end-to-end example

Snapshot 2026-09-12T19:52:33Z. All 17 rows carry engine `NFL_M1_XGBOOST` /
`NFL_M1_2026_RESEARCH_SHADOW_V1`, artifact `7bec0664…`, source cutoff
`2026-08-29 10:07:34`, and route `/nfl/games/{canonical_game_id}`.

| Kickoff UTC | Matchup (away @ home) | Status | Snapshot | Rev | Imported | Elig | Evaluation | Final |
|---|---|---|---|---:|---|---|---|---|
| 2026-09-10 00:20Z | NE @ SEA | final | CURRENT v9 | 10 | 09-08 19:08 | yes | **SCORED** | 10-13 NFLVERSE |
| 2026-09-11 00:35Z | SF @ LAR | final | CURRENT v7 | 8 | 09-08 05:36 | yes | **SCORED** | 27-7 NFLVERSE |
| 2026-09-13 17:00Z | BAL @ IND | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | BUF @ HOU | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | CHI @ CAR | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | CLE @ JAX | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | NO @ DET | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | NYJ @ TEN | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | TB @ CIN | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 17:00Z | ATL @ PIT | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 20:25Z | ARI @ LAC | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 20:25Z | MIA @ LV | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 20:25Z | WAS @ PHI | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-13 20:25Z | GB @ MIN | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-14 00:20Z | DAL @ NYG | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-15 00:15Z | DEN @ KC | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |
| 2026-09-18 00:15Z | DET @ BUF | scheduled | CURRENT v7 | 8 | 09-08 05:36 | yes | upcoming | — |

**No game is missing.** Schedule coverage is complete for the season: 272 of
272 stored, every current-week game carries a linked forecast, and no schedule
refresh was needed — so no provider budget was touched.

Full machine-readable table: `outputs/nfl-baseline-verification/coverage.json`.

### End-to-end example: ATL @ PIT, 2026-09-13 17:00Z

```
canonical_game_id  70b1ac40-fbd1-4176-89c1-149ac9d3e3d4
source_event_id    2026_01_ATL_PIT

1. PRODUCER RECEIPT   started 2026-08-29 21:55:55 EDT
   producer_revision  1ccd6b42c906b14be54098b0bd4ec0a200acfd96
   model_binary       7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd
   models_fit 0 · market_fields_read False · games/created 272/272

2. BUNDLE
   bundle_sha256  dd1f3d8b32c7b445af6a9535c4cd2bbbc8460e315a3ed0d11673e69428061fc6
   logical_uri    asp-model-exchange://aisp-daily-forecast-bundles/sha256/dd1f3d…/bundle.json
   generated 2026-09-08 05:30:50 · imported 2026-09-08 05:36:52

3. IMPORTED ROW
   forecast_id    asp-sports-game-forecast:sha256:2f2534673a5429135c827d8ff7e514d0a…
   snapshot       CURRENT v7 (7 CURRENT revisions kept)
   source_cutoff  2026-08-29 10:07:34   forecast_cutoff 2026-09-08 05:30:22
   published      2026-09-08 05:30:50   imported        2026-09-08 05:36:52
   home_win 0.531 · away_win 0.4386 · exp 21.7585 / 20.7265 · readiness 78/50

4. ELIGIBILITY   forecast_cutoff < kickoff  ->  True

5. SCREEN (authenticated browser, /nfl/games/70b1ac40-…)
   "ATL @ PIT · NFL · WEEK 1 · UPCOMING · RESEARCH SHADOW"
   PIT WIN 53.1% (exp 21.76)  ATL WIN 43.9% (exp 20.73)  TIE 3.0%
   EXPECTED MARGIN +1.03 (PIT minus ATL)   EXPECTED TOTAL 42.48
   SNAPSHOT CURRENT v7 · 8 immutable snapshot(s) kept
   SOURCE EVIDENCE Aug 29, 10:07 AM EDT
   FORECAST PUBLISHED Sep 8, 5:30 AM EDT
   IMPORTED BY AISP  Sep 8, 5:36 AM EDT
```

The `2f2534…` forecast id on screen is the same row the producer receipt
created — producer → bundle → imported row → screen, one identity throughout.

The page shows only what the model supplies, and says what it does not: *"Not
consumed: quarterback status, injury report, weather, roster or depth-chart
changes, market prices"*, and *"A newer snapshot version reflects a producer
re-publication, not refreshed evidence for inputs this model does not
consume."* Market comparison is correctly withheld — *"Last observed quote is
13 days old, beyond the 240-minute freshness window"* — with the stale prices
shown as reference only and no edge computed.

### A real scored example

```
2026_01_NE_SEA   log loss 0.5558  Brier 0.3400  predicted margin +2.81  actual +3
2026_01_SF_LA    log loss 1.0966  Brier 0.8531  predicted margin +5.02  actual -20
```

Both scored against the governed `NFLVERSE` final, zero provider calls. The
second is a large miss and is reported as it stands.

---

## 4. Source freshness — what the model actually consumes

The forecasts are genuinely frozen and genuinely stale in input terms, and the
contract permits it. This is the finding, stated precisely:

- The producer reads an **immutable NFL Foundation publication**, republished
  **2026-09-09T00:37:09Z**.
- That publication's schedule/roster/player/depth-chart families were updated
  **2026-09-08/09**, but its own quality block reads
  `completedGameEvidence: NOT_YET_AVAILABLE`, `completedGames: 0`, and it
  carries the limitation `nfl-2026-completed-games-not-yet-available`:
  *"the season has not begun and nflverse publishes no 2026 completed-game PBP
  or derived stats yet."*
- The contract explicitly allows this:
  `CURRENT_SEASON.completedGameEvidenceExpectedBeforeKickoff = False`.

So the **September 9 republication is a refreshed timestamp, not a numerical
update**. The model consumed no new game evidence, which is exactly why the
producer I ran today recomputed **byte-identical** bundles
(`forecasts_created: 0`, locator SHA unchanged at `cda12efb…`). The roster and
depth-chart refresh is real but is **not** in the core predictor contract, and
is not presented as one.

The three clocks stay distinct everywhere — stored, in the API and on screen:

| Clock | Value |
|---|---|
| Model training cutoff | 2026-02-08 |
| Source-as-of (game evidence) | 2026-08-29 10:07:34 |
| Forecast cutoff / publication | 2026-09-08 05:30:22 / 05:30:50 |
| AISP import | 2026-09-08 05:36:52 |

**Limitation, plainly:** week-1 results are not in any current forecast, and
will not be until nflverse publishes 2026 completed-game data. Week-2 forecasts
therefore do not know what happened in week 1. Nothing in AISP can change that.

---

## 5. What was broken, and what I changed

### Defect A — the producer was blocked by another sport's output files

`generate-nfl-shadow-forecasts` failed on **every** run from 2026-09-11 with
`Configured Model Studio has tracked or staged changes; producer code identity
is not frozen`. The last substantive production was 2026-09-08.

Model Studio is a shared monorepo. Its worktree had 5,634 differing entries, of
which **7 were tracked** — five MLB output artifacts, one MLB transform module,
and one NFL *play-level* engine output. **None was the M1 producer.** The guard
checked the entire repository, so ordinary MLB work by Studio's owner silently
stopped NFL forecasting.

The check is now scoped to the two paths that determine what this producer
computes: `scripts/run_nfl_2026_prospective_shadow.py` and
`server/sports_nfl_m1` — verified to import only the standard library and
third-party packages, so nothing else in the repo can alter its output. The
integrity property is unchanged: the producer still runs only at the recorded
HEAD with its own code unmodified, and its binary, manifest and feature-contract
hashes are still verified against the frozen authority afterwards. The
repository-wide difference is now **recorded in the receipt**
(`studioUnrelatedDirtyPathCount`) rather than used as a refusal, and a genuine
refusal now names the offending file.

**Verified in production:** the producer ran for 418 s, 270 games, 270 feature
vectors, `models_fit: 0`, `market_fields_read: false`, binary `7bec0664…`.

### Defect B — every Rams home game was unscorable

`ingest-nfl-finals` reported `skip_reasons: ["HOME_SIDE_MISMATCH"]` and wrote
nothing for SF @ LAR, which had been final for ~19 hours.

The guard compares the feed's home club against AISP's, which is right — a
silent swap would invert every margin scored. But it compared **raw strings
across two vocabularies**. Reading all 32 club tokens from the live nflverse
release against AISP's 32: thirty-one are byte-identical, and nflverse writes
the Los Angeles Rams as `LA` where AISP holds `LAR`. That single spelling
difference made every Rams home game look like an orientation disagreement.

Comparison now runs through that one alias (`same_club`). The guard is not
weakened: two different clubs, an unrecognised token, or a missing side are all
still refused.

**Verified in production:** the next supervised run reported
`finals_detected: 2`, `observations_written: 1`, `skip_reasons: []`, and the
evaluation job then scored SF @ LA.

### Not a defect — checked and cleared

Two evaluation rows exist for NE @ SEA. That is **correct supersession, not
duplication**: the first scored an `OPERATOR_ENTERED` result, and was superseded
by the governed `NFLVERSE` final, with `is_current` true on exactly one. The
schema enforces it (`uq_research_forecast_eval_current`). No change made.

### Nothing was invented

No forecast was generated for an already-started game. No position, decision or
policy change was made. NFL model authority and the planned/unfunded NFL season
account are untouched, and no season funding was backdated. Every job ran with
`provider_calls: 0` and `sportsbook_quote_credits_spent: 0`.

---

## 6. Verification, deployment and commit

**Focused tests — 6 added, all passing**, scoped to the two defects:

| Area | Test |
|---|---|
| Identity / orientation | the feed's `LA` is the same club as our `LAR` |
| Identity / orientation | two different clubs, an unknown token and a missing side are still refused |
| Identity / orientation | a Rams home final is recorded with our orientation (13 stays the home score) |
| Identity / orientation | a genuine home/away swap is still refused for the Rams |
| Producer identity | unrelated Studio changes do not block the producer; the count is recorded |
| Producer identity | a modified NFL producer still refuses to run, and the message names the file |

One existing test pinned the exact git command and was updated to the scoped
call. Suites run: `test_nfl_final_ingest.py`, `test_nfl_shadow_activation.py`,
`test_nfl_prospective_scoring.py` — **62 passed, 0 failed**. No broad suite was
run and no unrelated cleanup was done.

**Idempotency, observed in production rather than asserted:**
`import-nfl-shadow-forecast-bundle` at 16:14 reused all 401 bundles and created
0 rows; `ingest-nfl-finals` reused the existing NE@SEA observation while writing
only the new one; `evaluate-nfl-research-forecasts` reported
`evaluations_reused: 1, evaluations_written: 1` — it did not rewrite the game
already scored.

**Scheduler ownership respected.** My out-of-band run of `ingest-nfl-finals`
correctly refused with `NOT_THE_FINAL_INGEST_OWNER`; the work was done by the
supervised owner after deployment. Deployed through the Windows Scheduled Task
"ASP Backend 8010"; the supervisor reclaimed the stale port owner and started
clean. Verified after: **one listener on 8010**, one scheduler claim
(`0abc0c4b1c7d46399d583f11ffc2cbae`), task Running.

**MLB continued operating throughout** — `freeze-shadow-experiment`,
`collect-odds-checkpoint`, `open-model-era-at-boundary`,
`evaluate-mlb-v18-research-forecasts` and the prediction-market jobs all ran in
the last hour; the funded MLB 2026 account is ACTIVE with 6 open positions.

**Commit** `648f243` — *"fix: two guards that silently stopped NFL forecasting
and scoring"*. Five files, NFL only. The season-account work from the previous
task was in the same `orchestrator.py` file and was deliberately **left
unstaged** rather than swept in; the commit was assembled by selecting only the
NFL hunks. Not merged, not pushed.

---

## 7. Blockers and next scheduled runs

**Remaining blockers: none in AISP.** Nothing here needs a Codex change, and no
Studio artifact is missing or defective.

**One external limitation**, outside anyone's control: nflverse has not
published 2026 completed-game data, so no forecast can incorporate week-1
results yet. The producer will keep recomputing identical bundles until that
upstream data appears — this is correct behaviour, not a fault, and the
unchanged historical-strength input is permitted by the feature contract.

**Next existing scheduled runs** (all on the existing supervised scheduler; no
new job, no competing heartbeat):

| Job | Cadence | Next |
|---|---|---|
| `generate-nfl-shadow-forecasts` | daily cron 05:30 local | 2026-09-13 05:30 EDT (09:30Z) |
| NFL `FINAL_PREGAME` producer | every 5 min, event-gated | continuous |
| `import-nfl-shadow-forecast-bundle` | every 30 min | ~16:44 EDT |
| `ingest-nfl-finals` | every 60 min | ~16:47 EDT |
| `evaluate-nfl-research-forecasts` | every 60 min | ~16:49 EDT |

Week-1 Sunday games kick off 2026-09-13 17:00Z; their finals and scores will be
picked up by the hourly ingest and evaluation without further intervention.

---

## 8. Still open for Codex — NFL archive restoration

Carried forward verbatim and **not** extended by this work:

> NFL evidence archiving was paused at **2026-09-11 20:52:51.349 UTC**. Restore
> on completion of the original published duration pilot, or by
> **2026-09-12 20:52:51.349 UTC**, whichever comes first. Restoration must
> verify the actual task state and an **actual archive run**, not configuration
> intent alone.

At 2026-09-12 20:23Z this leaves roughly **30 minutes**. Source: Codex's own
`ASP_MODEL_STUDIO_PROJECT_MANAGEMENT_HANDOFF_2026-09-11.md` §10, where
restoration is recorded as pending.

Observed, read-only: the only Windows Scheduled Task present is `ASP MLB V18
Research Publisher` (Running). The NFL archive is a **Codex desktop heartbeat
task**, not a Windows task, so it is not observable or restorable from AISP.
I created no competing heartbeat and did not wait on it — ordinary NFL forecast
verification proceeded independently, as instructed.
