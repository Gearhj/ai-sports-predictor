# Producer supplement request — NFL play-level population sample

**To:** Codex (Model Studio, NFL play-level engine)
**From:** AISP application
**Status:** Request. Nothing in AISP is blocked on it; the renderer ships against
what is already delivered and states its coverage exactly.

---

## 1. What was delivered, measured

Read directly from the 112 artifacts registered by
`nfl-labs-play-level-supplements/current.json`, not from the handoff summary:

| Quantity | Value |
| --- | --- |
| Artifacts (events × scenarios) | **112** = 16 events × 7 scenarios |
| `drawCount` per artifact | **1,000** |
| `populationFinals` per artifact | **1,000** (final scores, no paths) |
| `simulatedGames` per artifact (games WITH play paths) | **6** |
| Plays with paths per artifact | **841 – 1,116** |
| Total simulated games across the delivery | **112,000** |
| Total games with play paths | **672** |
| Total plays with paths | **108,466** |
| Plays in one population (`populationAccounting.plays`) | **152,149** |
| **Path coverage** | **6 / 1,000 = 0.60% of simulated games** |

Every artifact is identical in shape: `(drawCount, len(populationFinals),
len(simulatedGames)) == (1000, 1000, 6)` for all 112.

So the field can draw **1,030 plays** for `2026_01_NE_SEA` BASE — every play of
its six representatives — and **not** the 152,149 plays of the population. The
UI says exactly that, permanently, in its header.

## 2. What AISP built against the current delivery

- `GET /api/v1/nfl/games/{game_id}/play-population/{scenario_id}` returns all six
  representatives' plays with a `coverage` block carrying both counts.
- The Simulation Population view draws them, filters them, and recomputes its
  statistics from the **drawn** plays with their own denominators.
- The producer's 1,000-game aggregates (`populationAccounting`) are displayed
  beside them under a separate heading, unchanged, never merged.
- No pooling across scenarios or matchups is performed to inflate the count.

This is honest and it works. What it cannot show is the **shape of the
population** — where 152,149 plays actually end up, rather than where 1,030 of
them did.

## 3. The minimal supplement requested

Two options. **Either one alone** is sufficient; (A) is smaller and preferred.

### Option A — spatial aggregates (preferred, ~40 KB/artifact)

Add to each artifact a `populationSpatial` object summarising all plays in the
population, with no per-play records:

```jsonc
"populationSpatial": {
  "schemaVersion": "NFL_POPULATION_SPATIAL_V1",
  "basis": "ALL_SIMULATED_PLAYS_IN_POPULATION",   // 152,149, not the 6 shown
  "playCount": 152149,
  "bins": {
    // Start yardline100 x net yards gained, both integer-binned.
    // Counts only; no coordinates, no paths, no identities.
    "startYardline100": { "min": 1, "max": 99, "width": 5 },
    "netYards":         { "min": -20, "max": 80, "width": 5 },
    "cells": [
      { "playType": "RUSH",          "start": 75, "yards": 0,  "count": 4113 },
      { "playType": "PASS_COMPLETE", "start": 75, "yards": 10, "count": 1902 }
      // ... one row per (playType, start bin, yards bin) with count > 0
    ]
  },
  "byPlayType": {
    // Marginal distribution of net yards per play type, already in the
    // artifact's own quantile format.
    "RUSH": { "count": 50251, "mean": 4.31, "quantiles": { "0.05": -2, "0.5": 3, "0.95": 16 } }
  }
}
```

This lets the field render a **density** of the whole population behind the
1,030 individual paths — the drawn plays stay individually visible, and the
population's shape sits behind them as a heat layer. It is the smallest thing
that makes "1,000 games" visible without shipping 1,000 games.

### Option B — a deterministic population sample (~2.5 MB/artifact)

If per-play paths are wanted instead, add a **declared, reproducible** sample:

```jsonc
"populationSample": {
  "schemaVersion": "NFL_POPULATION_SAMPLE_V1",
  "selection": "EVERY_NTH_SIMULATION_INDEX",
  "n": 10,                       // simulation indices 0, 10, 20, ... 990
  "gameCount": 100,
  "playCount": 15230,
  "seedOfSelection": null,       // null because the rule is deterministic, not random
  "games": [ /* same shape as `simulatedGames` */ ]
}
```

Requirements on Option B:

1. **Deterministic and stated.** `selection` must name the rule. A random sample
   is acceptable only if its seed is published and reproduces the same set.
2. **Not a re-selection by outcome.** The sample must not prefer close games,
   high-scoring games or any other property of the result, or the field becomes
   a biased picture of the population.
3. **Disjoint from, or a superset of, `simulatedGames`.** State which. AISP must
   not double-count a game that appears in both.

## 4. Exact minimal handoff request

> Please add **one** of the following to each of the 112
> `NFL_LABS_PLAY_LEVEL_SIMULATION_V1` artifacts, keeping everything already
> present unchanged:
>
> **(A)** `populationSpatial` — counts of all plays in the 1,000-game population,
> binned by `(playType, startYardline100 bin, netYards bin)` at width 5, plus a
> per-playType net-yards quantile summary. No coordinates, no paths, no
> identities. Expected size ~40 KB per artifact.
>
> **or (B)** `populationSample` — a deterministic subset of simulated games
> carrying full play paths in the existing `simulatedGames` shape, with the
> selection rule named in a `selection` field, a stated `n`, and an explicit
> statement of whether it overlaps the six published representatives. Expected
> size ~2.5 MB per artifact at n=10.
>
> Please also raise `artifactSchemaVersion` and republish
> `nfl-labs-play-level-supplements/current.json` with a new handoff digest, so
> AISP's checksum verification fails loudly rather than silently reading a mixed
> set.
>
> Nothing else needs to change. AISP will keep displaying the exact counts it
> has, and will render the new field only when the new key is present.

## 5. What AISP will do on receipt

- Verify the new key against a bumped contract version; refuse to read it if the
  schema version has not moved.
- Render Option A as a density layer behind the drawn paths, labelled
  "all 152,149 simulated plays" against the drawn paths' own count.
- Render Option B as additional drawable plays, with the coverage line updated to
  the real numbers (e.g. "15,230 plays from 100 of 1,000 simulated games") and
  the selection rule shown.
- In both cases the six representatives stay individually selectable, and
  population aggregates stay on their own denominators.
