# NFL play-level contract V2 — `populationSpatial` handoff

**Status:** published and imported. AISP is serving it.
**Date:** 2026-09-10

Option A of `NFL_POPULATION_SAMPLE_SUPPLEMENT_REQUEST.md`, implemented and
published. Each artifact now carries counts of **every** simulated play in its
1,000-game population, so the field can show the shape of the whole population
behind the ~1,000 individual paths that actually ship.

---

## 1. Exact digests

| Object | SHA-256 | Bytes |
| --- | --- | --- |
| Contract `NFL_LABS_PLAY_LEVEL_CONTRACT_V2.json` | `1e008bf3c666a7ae983182be84427a3e7ff0c116707d847e8ef5d212c1b883ab` | 18,996 |
| Index `nfl-labs-play-level-supplements/current.json` | `a2bab5d273589d87c42a6fc1a3a870225c7ef20a41a85b8a928e8da7cc53cc67` | 89,771 |
| Handoff receipt `handoff.json` | `7bbeb6d27fc60d6b520925dbfccc6b62ec7dca279edd03a32b20f3f91e53d37f` | 149,528 |

Index schema `NFL_LABS_PLAY_LEVEL_CURRENT_V2`; handoff schema
`NFL_PLAY_LEVEL_HANDOFF_V1`. Predecessor index (superseded, retained for
comparison) was `14498229e33ad7832dead65a2cd0f259c821c0a555f038aa82283fd65fe16b60`,
89,729 bytes.

## 2. What is in the index

| | |
| --- | --- |
| Artifacts indexed | **112** (unchanged; nothing disappeared) |
| Regenerated at `NFL_LABS_PLAY_LEVEL_SIMULATION_V2` | **105** |
| Carried forward at `..._V1` | **7** |
| Events | 16 |
| Artifact bytes | 321,509,082 total (2,553,860 – 3,282,796 each) |
| Plays summarised by `populationSpatial` | **15,979,439** |
| Cells | 85,238 |

**The 7 that are still V1 are `2026_01_NE_SEA`, all seven scenarios.** That game
kicked off at 2026-09-10T00:20Z. The publisher's `pregame_clock` refuses
inference after kickoff — `KICKOFF_REACHED_BEFORE_EVENT_SCENARIO_INFERENCE` —
which is the control that stops a "pregame" forecast being minted after the
fact. It was not bypassed. Those artifacts are re-verified and carried forward
unchanged rather than dropped, because dropping them would silently remove a
game AISP already serves. **They carry no `populationSpatial` and must not be
rendered as though they did.**

## 3. Production run

| | |
| --- | --- |
| Game simulations | 105,000 |
| Representative replays | 630 |
| Impossible states | **0** |
| Runtime | 3,552.8 s (59 min) |
| Fits or statistical re-evaluations | **0** |

The run reused the frozen numerical validation
(`RENDERER_AND_PUBLICATION_ONLY_SUCCESSOR_NUMERICAL_MODEL_AND_EVALUATION_UNCHANGED`)
and logged *"zero new fits or evaluations"*. No model, forecast, policy or
wagering authority was altered.

## 4. Deterministic replay, verified

Before publishing, `2026_01_SF_LA` BASE was re-simulated from the same seeds and
model: `populationFinals` and `populationAccounting` reproduced **byte-for-byte**.

After publishing, all 105 regenerated artifacts were compared against their V1
predecessors across **29 fields** — `populationFinals`, `simulatedGames`,
`representativeFinals`, `scoreSummary`, `quantiles`, all three distributions,
`populationAccounting`, `scenario`, `scenarioDelta`, `m1Comparison`, `seed`,
`drawCount` and the rest:

```
no population disappeared from the index: OK
byte-for-byte preservation across 29 fields x 105 populations: OK
populationSpatial reconciles on all 105 V2 populations
```

Reproduce with:

```
cd server && python -m scripts.verify_nfl_population_spatial <predecessor-index.json>
```

Fields that legitimately differ: `schemaVersion`, `forecastId` (the case
identity now includes the artifact schema version, so a V2 artifact is a new
identity), `contract`, `declaration`, `validationReceipt`, the three timestamps,
and `populationSpatial` itself.

## 5. The block

```jsonc
"populationSpatial": {
  "schemaVersion": "NFL_POPULATION_SPATIAL_V1",
  "basis": "ALL_SIMULATED_PLAYS_IN_POPULATION",
  "games": 1000,
  "playCount": 151135,
  "reconciliation": { "populationAccountingPlays": 151135, "binnedPlayCount": 151135,
                      "reconciles": true, "excludedPlayTypes": ["PERIOD_END"],
                      "excludedPlayCount": 4031, "statement": "..." },
  "fieldDirection": { "convention": "OFFENSE_RELATIVE_YARDLINE100_NO_MIRRORING_REQUIRED", ... },
  "bins": { "start": {...}, "yards": {...}, "clipping": "NONE_..." },
  "byPlayType": { "RUSH": { "plays": 50251, "netYards": {"count":..,"mean":..,"quantiles":{..}} }, ... },
  "cells": [ { "playType": "RUSH", "start": 75, "yards": 0, "count": 4113 }, ... ],
  "cellCount": 801
}
```

### The count, and why it is smaller than you might expect

`playCount` counts every simulated play **except period-end markers**, which is
exactly how `populationAccounting.plays` is defined. It is therefore smaller
than `sum(playTypeCounts)` by precisely the number of those markers — 151,135
vs 155,166 on the sample above, a difference of 4,031. Both the producer and
AISP refuse the artifact if `playCount != populationAccounting.plays`.

### Field direction

**No mirroring is applied, because none is needed.** `start` is
`stateBefore.yardline100`: yards from the goal line the *possessing* team is
attacking, 0 at that goal line and 100 at its own. It is already
offense-relative, so a bin means the same thing for either team and either
physical direction. Binning it directly **is** the normalisation, and the
artifact states the convention in `fieldDirection`.

A consumer drawing a left-to-right absolute field should mirror each bin onto
both absolute positions (`x` and `100-x`), which is what AISP's renderer does.

### Nothing is clipped

`UNDERFLOW`, `OVERFLOW` and `NA` are explicit bins. A 96-yard touchdown clipped
into the top bin would read as an ordinary long gain with nothing anywhere
reporting it. Plays with no net yards — kicks, tries, penalties — take `NA`
rather than zero, because a punt did not gain nothing. On the sample population:
4 underflow, 26 overflow, 28,466 `NA`. Every counted play contributes to
**exactly one** cell.

## 6. Import instructions

```
cd server
python -m scripts.import_nfl_play_level_supplements --root C:\Users\james\asp-model-exchange
python -m scripts.import_nfl_play_level_supplements --root C:\Users\james\asp-model-exchange --apply \
    --receipt outputs/nfl-population-spatial/import-receipt.json
```

The first form is read-only. `--apply` is append-only and rolls back if any
protected table changes inside its transaction. The applied receipt records
`imported: 105`, `reused: 7`, `protected_tables_unchanged: true`,
`model_executions: 0`, `provider_calls: 0`, `official_writes: 0`.

Consumers must:

1. Read `schemaVersion` **per artifact**; both V1 and V2 appear in the index.
2. Verify `populationSpatial.playCount == populationAccounting.plays` and that
   the cell counts sum to it, before rendering any population-wide density.
   AISP re-derives both rather than trusting the field — nothing downstream can
   check the block against the plays, since 994 of the 1,000 games ship none.
3. Treat a missing block as "this population has no summary", never as an empty
   one.

## 7. Where the code lives

Producer changes are on the branch `codex-support/nfl-population-spatial-v2`
in a **separate git worktree** at `C:\Users\james\asp-model-studio-spatial`, so
Codex's working tree and the in-flight MLB regime/hybrid study were not touched:

- `server/sports_nfl_m1/play_spatial.py` — new; the accumulator.
- `scripts/publish_nfl_play_level.py` — V2 versions, accumulate in the existing
  loop, carry-forward for past-kickoff events, content-based compatibility guard.
- `server/sports_nfl_m1/play_geometry.py` — V2 versions and the reconciliation
  check.
- `shared/contracts/NFL_LABS_PLAY_LEVEL_CONTRACT_V2.json` — new.
- `tests/test_nfl_population_spatial.py` — 11 focused tests.

**The accumulator is not in `PopulationAccounting`, where it naturally belongs.**
`_assert_validation_compatibility` refuses validation reuse if
`play_validation.py` changes, so that placement would have forced a fresh fit
and historical evaluation — re-deriving frozen models to add a summary. It is a
new module called from the publisher's existing loop, so it costs no extra
simulation and the numerical design is untouched.

**One guard was changed.** `_assert_validation_compatibility` compared
`file_record` equality, which includes the absolute path a file was read from —
so identical bytes read from a second checkout compared unequal and it fired on
code that had not changed. It now compares `sha256` and `byteCount`, which is
the question the guard is actually asking.

**One tracked file in Codex's repo changed:**
`outputs/nfl-play-level-engine-v1/CURRENT.json`, the NFL play engine's own
pointer. That is what should change when NFL play-level publishes, and it is
unrelated to the MLB study. Everything else written there is immutable
content-addressed additions.
