# NBA Gen1 consumer integration — staged, not active

Branch `claude/nba-consumer-integration-staged-v1` · handoff index sha256
`7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf`

**Nothing here is deployed.** No pin is applied, no migration is run, no
setting is set, and AISP was not restarted for NBA. `production_status` returns
`NOT_RECEIVED` on the live configuration, which is the only state that may exist
until the steps in §5 are taken deliberately.

---

## 1. What the pin actually covers

The handoff is a **directory**, not a file. The pinned sha256 is of its
`index.json`; the index carries the sha256 and byte count of every member. So
verification is two-step and total:

```
pin  ->  index.json   (7113b71f…)
index ->  ACTIVATION.md      1,185 bytes  1b5a9050…
      ->  CLAUDE_HANDOFF.md    665 bytes  14f04a8c…
      ->  manifest.json    584,450 bytes  0f62b3ff…
```

All three verify byte-for-byte against the retained package. A member one byte
different is refused before anything is parsed, and a test proves it.

## 2. Three channels, never blended

The producer's own disclosure says it: *"Ridge score and Elo winner channels are
separately estimated."* Each is named and kept apart.

| Channel | Produces | Probability? | Markets it may answer |
| --- | --- | --- | --- |
| `RIDGE_POINT_EXPECTATION` | expected home/away score (`ridge_a100_rest.joblib`, `063e69ff…`) | **no** | none |
| `ELO_WINNER_ASSOCIATION` | OLS association on `HOME_MINUS_AWAY_MARGIN` (`elo.joblib`, `6a1391e7…`) | **no** | none |
| `JOINT_PAIRED_RESIDUAL_DRAWS` | 10,000 paired HOME/AWAY full-game draws, overtime included (`residuals.parquet`, `84bd0f9e…`) | **yes** | MONEYLINE, SPREAD, TOTAL |

**The authorised probability channel for every market is the joint draw
population, and nothing else.** The reasoning is not stylistic: the Elo model's
declared `target` is a *margin*, described in the manifest as a "training-only
OLS association … not a causal estimate". It is not a calibrated win
probability, so turning its margin into one would be inventing a channel. The
Ridge model yields a point expectation, which cannot answer a moneyline, a
spread or a total on its own.

`MARKET_CHANNEL` is a **closed allow-list**: a market not in it — player props,
first half, team totals — returns `None`, not a default.

## 3. Identity: the population is the one AISP already admits

| | handoff | AISP `ADMITTED["NBA"]` |
| --- | --- | --- |
| distribution configuration | `NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1` | same |
| draw count | 10,000 | same |

The handoff's top-level `configurationIdentity` is
`NBA_GEN1_ELO_WIN_RIDGE_A100_ORDINARY_REST_V1`, which names the whole baseline
(Ridge + Elo + joint distribution). That is **not** a replacement population:
the probability channel underneath is the admitted lattice. The adapter refuses
the handoff if that ever stops being true (`DISTRIBUTION_CONFIGURATION_MISMATCH`).

The adapter also refuses if the close-score challenger is enabled; the handoff
requires it to stay disabled and the manifest records
`closeScoreChallengerEnabled: false`.

## 4. The effective clock, and BOS–DET

`prospectiveEffectiveAtUtc` = **2026-09-18T01:16:08.176807+00:00**. Only packages
created at or after that instant are governed. `governs()` is exclusive below
it to the microsecond, and fails closed on a missing or unparseable timestamp.

**BOS–DET predates it and stays a research artifact.** It is never relabelled,
and a test pins that.

## 5. Activation dependency — exactly what is still required

Activation is blocked on a producer step that is **not AISP's to take**:

1. **Codex/administrator installs the publisher.** `manifest.deployment.installed`
   is `false` and `authority.producerActivated` is `false`; the producer is
   `producerReady: true`, `status: READY_AWAITING_ACTIVATION`. The install
   command is in the package's own `ACTIVATION.md` and runs against
   `asp-model-studio`, not AISP.
2. **Then, and only then, AISP pins the handoff** — two settings, currently
   empty by design:
   ```
   nba_production_handoff_path   = <…>/handoffs/sha256/7113b71f…/index.json
   nba_production_handoff_sha256 = 7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf
   ```
3. **Restart the `ASP Backend 8010` task**, outside an open decision window.

**No database migration is required.** The adapter reads the pinned package and
AISP's existing `ADMITTED["NBA"]` registry; it adds no table, no column and no
scheduler. If forecast packages later need importing, that rides the existing
`consume-model-exchange-research-artifacts` job, which already scans the
exchange root.

Until step 1 happens, a pin would be refused anyway: `production_label_allowed`
requires status `ACTIVE`, and the package says `READY_AWAITING_ACTIVATION`. That
is deliberate — a ready producer is not an active one.

## 6. Limitations that must travel with any display

Carried in `RETAINED_LIMITATIONS` and returned in the status payload:

* simulated one-point finishes **7.78%** against **4.01%** observed — so margins
  at or near a single point are the least trustworthy part of this model, which
  matters most for spreads sitting on an integer;
* a close-score correction was tested and **rejected**; the challenger stays disabled;
* overtime games remain materially harder;
* **no profitability claim has been established**;
* Ridge and Elo channels are separately estimated.

Monitoring review points are **50 and 100 completed games**.

## 7. What is staged in this branch

| File | Purpose |
| --- | --- |
| `server/app/forecast_exchange/nba_production_handoff.py` | the fail-closed adapter |
| `server/tests/test_nba_production_handoff.py` | 16 tests, including tamper, channel-separation and BOS–DET |
| `server/app/core/config.py` | two empty pin settings |

Publication is not consumer acknowledgement and grants no wagering, decision or
position authority.
