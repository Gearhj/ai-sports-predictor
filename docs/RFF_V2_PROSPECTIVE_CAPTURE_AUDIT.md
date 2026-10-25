# RFF Core v2 — prospective capture audit

Read-only audit · 2026-09-17 · deployed index `5be82f1` · alembic head
`20260917_0044` (unchanged) · **no production state written, no provider call
made, no hook deployed**

## Verdict

**`RFF_V2_PASSIVE_SNAPSHOT_REQUIRED`**

A T-90m primary reading **cannot** be reconstructed exactly today. The evidence
substrate is sound — the tables are append-only and carry knowledge times — but
**no read path in the calculation bounds on those knowledge times**, the frame
sampler is path-dependent on evidence that arrives later, and nothing anywhere
records which version of the calculation produced a reading.

The attempted dry reconstruction is the proof, not a prediction: recomputing
the T-90m reading for the four games that had already started, **4 of 4 used
evidence that did not exist at T-90m**, by 140 to 295 minutes.

---

## 1. Is the evidence immutable and timestamped?

Yes, and this is the part that already works.

| Table | Append-only | Knowledge time | Notes |
| --- | --- | --- | --- |
| `odds_snapshots` | yes — no `UPDATE`/`DELETE` anywhere in `app/` | `available_as_of` **and** `observed_at` | `payload_checksum` present; `is_closing` is read-only in practice |
| `forecasts` | rows append; `status`/`lifecycle` mutate | `available_as_of` + `generated_at` | the probability, both timestamps and the version are immutable, so the *value* is safe |
| `lineup_observations` | yes (`_PointInTimeObservation`, append-only) | `available_as_of` = `observed_at` | see the fidelity warning in §6 |
| `starter_observations` | yes | `available_as_of` = `observed_at` | same |

Knowledge time is genuinely distinct from observation time on the price side:
**257,244 of 257,244 odds rows have `available_as_of ≠ observed_at`, with a lag
up to 14.9 minutes.** Any reconstruction that bounds on `observed_at` instead of
`available_as_of` therefore leaks up to ~15 minutes of price information.

## 2. Does the calculation honour as-of? No — four unbounded read paths

| # | Path | Query | Bound on knowledge time |
| --- | --- | --- | --- |
| L1 | `MarketTapeService.tape()` | `select(OddsSnapshot …).where(Market.game_id == …)` | **none** — every snapshot for the game, whenever it arrived |
| L2 | `MarketTapeService._model_series()` | `select(Forecast …).where(game_id == …).order_by(generated_at)` | **none** — and it orders by `generated_at`, not publication time |
| L3 | `RffService._information_uncertainty()` | lineup + starter selects | **none** |
| L4 | `RffService._evidence_health()` | lineup + starter + readiness selects | **none** |

`now` is used only for staleness tests, window opening and the payload's
`generated_at_utc`. It never restricts what is read. Live this is harmless —
at wall-clock `now` the future rows do not exist yet — but it makes every
reading a function of *when you ask*, which is precisely what a prospective
capture may not be.

### L5 — the frame sampler is path-dependent

```python
step = len(instants) / MAX_FRAMES
kept = {instants[min(len(instants) - 1, int(index * step))] for index in range(MAX_FRAMES)}
```

`_thin` subsamples by index across the **whole** instant list. Adding instants
later changes `step`, so a snapshot that arrives after T-90m changes *which
earlier frames survive*. Reverse line movement, late movement and cross-book
disagreement are all computed off those surviving frames. Even evidence that
existed at T-90m therefore produces different component values once later
evidence lands — unless the as-of bound is applied *before* thinning.

## 3. Dry reconstruction (the attempt that failed)

Recomputed `for_game(now = start − 90m)` for every game that had already
started, then checked each component's reported `observed_utc` against the
as-of instant:

| Game | T-90m (UTC) | Tier | Component using future evidence | Evidence lag |
| --- | --- | --- | --- | ---: |
| MIL @ PIT | 15:05 | `RFF_PUBLISHED` | information uncertainty | **+295 min** |
| LAD @ CIN | 15:10 | `RFF_PUBLISHED` | information uncertainty | **+290 min** |
| ATH @ TB | 15:40 | `RFF_PUBLISHED` | information uncertainty | **+260 min** |
| SD @ COL | 17:40 | `RFF_PUBLISHED` | information uncertainty | **+140 min** |

All four were published at 78.1% coverage — that is, the contaminated reading
is exactly the kind that would have entered the primary analysis.

Every one of those four resolved to the **same** evidence instant,
`2026-09-17T15:59:36`, because a bulk lineup/starter ingest stamps every row it
writes with the ingest time (§6).

## 4. Evidence arriving after each reading offset

Across all ten games on the 2026-09-17 slate:

| Offset | Odds rows after | Forecasts after | Lineups after | Starters after | Games affected |
| --- | ---: | ---: | ---: | ---: | ---: |
| T-6h | **288** | **9** | 318 | 486 | **9 of 10** |
| T-3h | 0 | 2 | 283 | 210 | 4 of 10 |
| **T-90m** | 0 | 0 | **142** | **132** | **4 of 10** |
| T-30m | 0 | 0 | 42 | 88 | 4 of 10 |

The zeros at T-90m for odds and forecasts are **today's collection pattern, not
a guarantee**. Nothing in the code prevents a late price or a late forecast from
being absorbed; T-6h shows exactly what happens when collection is still active
after the offset. A latent leak is still a leak.

## 5. Per-category findings

| Category | Reconstructible from evidence alone? | Blocker |
| --- | --- | --- |
| Every sportsbook quote used | **Yes** — `odds_snapshots` is append-only with `available_as_of` | must bound on `available_as_of`, not `observed_at` (up to 14.9 min of leakage) |
| Consensus and cross-book dispersion | **Derived** — deterministic given a bounded, unthinned frame set | L1 + **L5** (sampler path-dependence) |
| Opening→reading and prior-90-minute movement | **Yes** — both are functions of bounded snapshots | L1 + L5 |
| Reverse line movement, late movement | **Derived** | L1 + L5 |
| Controlling model forecast | **Yes** — `forecasts` keeps `available_as_of`, `generated_at`, version, probability | L2, and the selector orders by `generated_at` rather than publication time |
| Lineup / starter information | **Partly** — rows are append-only and ordered | L3 + the §6 fidelity loss |
| AISP Evidence Health fields | **No** — counts and readiness are read with no time bound at all | L4 |
| RFF calculation / version identity | **No** | nothing persists it — see below |

### Calculation identity is the irreducible gap

No table records an RFF reading, and no reading is bound to the code that
produced it. `SCHEMA_VERSION`, `WEIGHTS`, the gates and the saturation points
are module constants: a later edit silently changes every historical
recomputation, with no trace. The v2 specification lock covers the *analysis*
script, not the *index* code. This alone means "recompute it later" can never
satisfy a predeclaration, however well the read paths are bounded.

## 6. Fidelity warning — lineup and starter observation times

`available_as_of == observed_at` for **all 31,106 lineup and 52,192 starter
rows**. The ingest stamps both with its own clock, so:

* the source's own observation time is **not preserved** — only when AISP saw it;
* a bulk re-ingest rewrites the apparent freshness of that evidence for every
  game at once, which is why all four games above resolved to one instant;
* information-uncertainty staleness is therefore **non-monotonic**: a re-ingest
  can make old evidence look fresh.

For as-of purposes ingest time *is* the honest knowledge time, so this does not
block capture. It does mean the information component can never be reconstructed
against the source's own clock, and a snapshot taken at the moment is the only
faithful record.

---

## 7. Proposed mutation — the smallest passive hook (NOT DEPLOYED)

Reported for approval only. Nothing below has been written, migrated or
deployed. No new scheduler, no new provider request, no change to weights,
gates, interface, validation hypotheses or authority.

### 7.1 Host: an existing job, before its provider gate

`refresh-scoreboard` (`asp-scoreboard-refresh`) runs on a **2-minute interval**,
is display-only and makes one free MLB Stats API call. The hook goes **before**
its `should_run_fast` early return, so it runs on every tick regardless of the
watch window, and the provider call after the gate is untouched.

A 2-minute cadence bounds capture error at ≤2 minutes from the target offset,
and the actual capture instant is recorded, so the error is measured rather than
assumed.

```python
# app/jobs/orchestrator.py :: refresh_scoreboard, first statement in the body
from app.services.rff_capture import capture_due_rff_snapshots

captured = capture_due_rff_snapshots(self.session, now=datetime.now(UTC))

if not should_run_fast(self.session, now=datetime.now(UTC)):
    return {"records": 0, "league": "MLB", "requests": 0,
            "reason": "NO_GAME_IN_WATCH_WINDOW", "rff_snapshots": captured}
result = await collect_mlb_scoreboard(self.session, event_date=event_date)
result["rff_snapshots"] = captured
return result
```

### 7.2 New service `app/services/rff_capture.py`

Pure read + one insert per (game, offset). For each scheduled game whose clock
has passed a target offset and has no row yet for that offset: call the existing
`RffService.for_game(now=datetime.now(UTC))` — unchanged, no new calculation —
and persist the result. Idempotent by unique constraint; a missed window is
recorded as missed rather than back-filled.

### 7.3 New table `rff_reading_snapshots` (append-only)

| Column | Type | Holds |
| --- | --- | --- |
| `id` | uuid pk | |
| `game_id` | uuid fk, indexed | game |
| `target_offset` | varchar(8) | `T-6h` / `T-3h` / `T-90m` / `T-30m` |
| `target_instant_utc` | timestamptz | start − offset |
| `captured_at_utc` | timestamptz | actual capture instant |
| `as_of_utc` | timestamptz | the `now` handed to the calculation |
| `capture_lag_seconds` | integer | `captured_at − target_instant` |
| `calculation_version` | varchar(40) | `SCHEMA_VERSION` |
| `calculation_git_sha` | varchar(40) | build identity of the running code |
| `weights_sha256` | char(64) | hash of the frozen weight table |
| `policy_sha256` | char(64) | hash of the gate constants |
| `components` | jsonb | all five: key, score, available, unavailable_reason, weight, observed_utc, stale |
| `evidence_coverage` | numeric(9,6) | |
| `known_contribution`, `lower_bound`, `upper_bound`, `interval_width` | numeric(6,2) | |
| `publication_tier` | varchar(24) | `RFF_PUBLISHED` / `PROVISIONAL_WIDE_RANGE` / `INSUFFICIENT_EVIDENCE` |
| `band_key`, `emphasize_point`, `rankable` | varchar/bool | |
| `evidence_health` | jsonb | the whole block verbatim |
| `source_hashes` | jsonb | see below |
| `created_at` | timestamptz | |

Unique constraint `(game_id, target_offset)`; **no update path, no delete
path.**

`source_hashes` pins the evidence the reading stood on, so a later dispute is
settleable without trusting the recomputation:

* `odds_snapshot_ids_sha256` — sha256 over the sorted snapshot ids in every
  frame used, plus `max(available_as_of)` and the row count;
* `forecast_id`, `forecast_version`, `forecast_available_as_of`;
* `lineup_row_ids_sha256`, `starter_row_ids_sha256` with their row counts;
* `market_tape_schema_version`.

### 7.4 Migration

One new alembic revision on head `20260917_0044`: `CREATE TABLE` plus the unique
index. No existing table, column, index or constraint is touched.

### 7.5 Cost and blast radius

| Dimension | Effect |
| --- | --- |
| Provider requests | **none** — the hook reads the database only |
| Schedulers | **none added** — hosted in `asp-scoreboard-refresh` |
| Writes | one row per (game, offset); ~40 rows/day on a 10-game slate |
| Read cost | 4 `RffService.for_game` calls per game per day, on the job thread |
| Protected state | untouched: no forecast, decision, position, settlement, bankroll, budget or experiment row |
| Index behaviour | unchanged — the hook calls the deployed calculation and stores what it returns |

### 7.6 What this does *not* fix

Capture makes readings **authentic**; it does not make the historical record
reconstructible. L1–L5 remain, so a recomputation of an old reading will still
disagree with the stored one. That is acceptable and is the point: the stored
snapshot becomes the record of what was true, and any recomputation is a
derived check, not the source. If the read paths are later bounded on
`available_as_of` (a separate, separately-authorised change), the stored
snapshots become the test set that proves the bounding is correct.

---

## 8. Recommendation

1. Approve §7 as specified, or amend it — no hook is deployed until then.
2. Start capture **before** the validation clock starts. The v2 plan counts 300
   games with a publishable T-90m reading; every game that passes T-90m without
   a snapshot is a game that can never enter the primary analysis, because its
   reading cannot be recovered.
3. Treat any T-90m reading already "reconstructed" from before capture begins as
   inadmissible. On today's evidence, 4 of 4 such readings were contaminated.
