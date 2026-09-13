# AISP MLB ensemble candidate handoff — contract V1

**Schema** `AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1`
**Consumer** `server/app/shadow_import/mlb_ensemble_candidate.py`
**Status** Implemented and tested on the AISP side. **Awaiting a package from Model Studio.**

This is the minimum AISP needs in order to import the ensemble release candidate
and show it beside the frozen baseline. It deliberately mirrors the V18 daily
slate handoff, so Studio's existing publisher machinery should need extension
rather than replacement.

AISP never loads or runs the candidate's estimator. It imports numbers Studio
already produced, exactly as it does for V18.

---

## Handoff document

```jsonc
{
  "schemaVersion": "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1",
  "handoffSha256": "<64hex>",   // SHA-256 of this document's canonical bytes
                                // WITHOUT this field, json.dumps(sort_keys=True,
                                // separators=(",",":")). Self-address is checked first.
  "bundleId": "mlb-ensemble-candidate-<...>",
  "slateDate": "2026-09-12",
  "generatedAt": "2026-09-12T14:05:00+00:00",

  // PROSPECTIVE = genuine future pregame forecasts.
  // HISTORICAL_REPLAY = a labelled rerun over games already played.
  // Both are accepted. Only PROSPECTIVE can ever be prospective evidence.
  "artifactType": "PROSPECTIVE",

  // All three MUST be false. Checked before any payload is interpreted;
  // a package claiming authority is refused, not quarantined and read anyway.
  "executionAllowed": false,
  "wageringAuthority": false,
  "activatesModel": false,

  "candidate": {
    "engineId": "MLB_ENSEMBLE_CANDIDATE",   // exact string, distinct from baseline
    "engineVersion": "<string>",
    "modelArtifactSha256": "<64hex>",
    "manifestSha256": "<64hex>"             // optional
  },

  // The COMPLETE frozen system, not just the PA estimator. Every digest below
  // is checked against something AISP already stores from the champion
  // authority supplement. One mismatch rejects the whole package before import.
  "baseline": {
    "engineId": "MLB_V18_WHOLE_GAME",          // exact string
    "engineVersion": "<string>",
    "modelArtifactSha256": "<64hex>",          // = authority.model_artifact_sha256 (PA estimator)
    "championManifestSha256": "<64hex>",       // = authority.champion_manifest_sha256
    "frozenBindingsSha256": "<64hex>",         // = authority.frozen_bindings_sha256
                                               //   (fitted preprocessor + ordered feature contract)
    "treatmentManifestSha256": "<64hex>",      // = authority.evidence.development_integrity_sha256
    "registrationReceiptSha256": "<64hex>",    // = authority.evidence.registration_receipt_sha256
    "parentBundleId": "<string>",              // = authority.parent_bundle_id
    "trainingRunId": "<string>"                // = authority.training_run_id
  },

  // At least one BASE component. Roles: BASE | CALIBRATION | DURATION.
  // The BASE component's sha256 MUST equal baseline.modelArtifactSha256 --
  // that equality is what makes "composed on frozen V18" verifiable rather
  // than asserted.
  "components": [
    {"componentId": "V18_WHOLE_GAME",           "role": "BASE",        "sha256": "<64hex>", "status": "FROZEN"},
    {"componentId": "CALIBRATION_<...>",        "role": "CALIBRATION", "sha256": "<64hex>", "status": "TESTED"},
    {"componentId": "STARTER_DURATION_ADAPTER", "role": "DURATION",    "sha256": "<64hex>", "status": "SUPPORTED"}
  ],

  "forecasts": [
    {
      "sourceGameId": "825101",              // MLB gamePk
      "objectSha256": "<64hex>",
      "relativePath": "objects/<objectSha256>",  // directory name MUST be the digest
      "manifestFile": "manifest.json",
      "forecastFile": "forecast.json"
    }
  ]
}
```

## Object manifest — `manifest.json`

```jsonc
{
  "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_OBJECT_MANIFEST_V1",
  "objectSha256": "<64hex>",     // self-address, over the document without this field
  "sourceGameId": "825101",
  "artifacts": [
    {"relativePath": "forecast.json", "sha256": "<64hex>", "bytes": 4321}
  ]
}
```

Every listed artifact's bytes are hashed and byte-counted **before** the
forecast is read as anything but bytes.

## Per-game forecast — `forecast.json`

```jsonc
{
  "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_GAME_FORECAST_V1",
  "artifactType": "PROSPECTIVE",          // MUST match the handoff's
  "forecastId": "<globally unique string>",  // the idempotency key
  "eventId": "<string>",
  "sourceGameId": "825101",               // MUST match the handoff entry
  "canonicalGameId": "<AISP game uuid>",  // optional but strongly wanted: without
                                          // it the row cannot join to a game, and
                                          // no comparison or score is possible
  "baselineForecastId": "<the V18 forecastId this was composed on>",  // required;
                                          // MUST resolve to a stored V18 forecast for the
                                          // SAME canonical game, gamePk, home/away orientation
                                          // and scheduled start

  // Per-game binding to that stored baseline forecast. Required.
  "baselineBinding": {
    "parentBundleSha256":       "<64hex>",  // = stored lineage_detail.parentBundleSha256
    "acceptanceProtocolSha256": "<64hex>",  // = stored lineage_detail.acceptanceProtocolV2Sha256
                                            //   (the accepted transition artifact)
    "numericalArtifactSha256":  "<64hex>",  // = stored model_manifest_sha256
    "featureVectorSha256":      "<64hex>",  // = stored feature_vector_sha256
    "simulatorId":              "<string>", // = stored simulator_id
    "simulatorVersion":         "<string>", // = stored simulator_version
    "simulatorSeed":            12345,      // = stored simulator_seed
    "simulationCount":          5000,       // = stored uncertainty.simulationCount
    "sourceCutoffUtc": "2026-09-12T20:01:00+00:00"  // = stored source_cutoff_utc
  },
  "homeTeam": "MIA", "awayTeam": "LAD",   // required
  "season": 2026,                          // required, integer

  // Three clocks, kept distinct. capturedAt is the evidence cutoff;
  // publishedAt is when the forecast was frozen.
  "capturedAt":     "2026-09-12T20:01:00+00:00",
  "publishedAt":    "2026-09-12T20:10:00+00:00",
  "scheduledStart": "2026-09-12T23:10:00+00:00",
  "snapshotVersion": 1,

  "probabilities": {"homeWin": 0.5812, "awayWin": 0.4188},
  "expectations": {
    "homeRuns": 4.91, "awayRuns": 4.02,
    "totalRuns": 8.93, "homeMinusAwayRuns": 0.89
  },

  // Optional, and worth a lot: marginals give the scorer real CRPS.
  "distributions": {
    "homeRunDistribution": {"0": 0.10, "1": 0.14, "...": 0.0},
    "awayRunDistribution": {"...": 0.0},
    "totalRunDistribution": {"...": 0.0},
    "runDifferentialDistribution": {"...": 0.0}
  },

  "simulation": {
    "simulatorId": "<string>",      // required
    "simulatorVersion": "<string>", // required
    "seed": 4242,                   // required, integer
    "count": 5000,
    "simulationCount": 5000
  },

  "eligibility": {"eligible": true, "reason": null},

  // Required, and the point of the whole surface. Every declared component
  // must appear. A component that did not run MUST give a fallbackReason --
  // a component that silently vanishes is not auditable, and AISP refuses it.
  "componentsApplied": [
    {"componentId": "V18_WHOLE_GAME", "ran": true, "fallbackReason": null},
    {"componentId": "CALIBRATION_<...>", "ran": true, "fallbackReason": null},
    {
      "componentId": "STARTER_DURATION_ADAPTER",
      "ran": false,
      "fallbackReason": "No fitted starter-duration curve for either probable starter",
      "detail": "baseline bullpen template used instead"
    }
  ],

  // OPTIONAL. Supply this ONLY if the decomposition is real. Without it AISP
  // shows the total difference and states that the per-component split is
  // unavailable. It will NOT divide the total by subtraction -- see below.
  "componentAttribution": { /* Studio's own schema */ }
}
```

## What AISP will not do, and why you may need to supply more

1. **It will not split the baseline-to-candidate difference into per-component
   probability points by subtraction.** Composing a calibration layer and a
   duration adapter onto a base is not additive, and a "+1.2 from calibration,
   +0.6 from duration" caption invented that way would be a fabricated number a
   reader cannot audit. If you want a per-component split shown, supply
   `componentAttribution` with whatever makes it valid — held-out deltas,
   Shapley values, ablation runs — and say which.

2. **It will not describe a calibration band as a difficult matchup.** A wide
   band is the calibration component's own confidence. If you intend a claim
   about the matchup itself, that is a different artifact and needs its own
   field.

3. **A replay cannot enter prospective performance.** Registered replays carry
   `HISTORICAL_REPLAY_RESEARCH_SHADOW` status and the research scorer refuses
   them with reason `HISTORICAL_REPLAY_NOT_PROSPECTIVE_EVIDENCE`. They reach no
   decision and no position. This is not a limitation to work around.

## Rejection reasons you may hit

| Reason | Fix |
|---|---|
| `handoff does not self-address` | Recompute `handoffSha256` over canonical bytes without that field |
| `refuses a package claiming authority` | Set all three safety flags false |
| `BASE component digest does not equal the declared baseline model artifact` | Point BASE at the frozen V18 artifact |
| `not registered in AISP` | The baseline artifact must already be imported here |
| `handoff baseline declares no <field>` | The estimator digest alone does not identify the frozen system; supply the whole `baseline` block |
| `baseline <label> does not match the frozen V18 system registered in AISP` | A system dependency disagrees with the registered champion; check which |
| `does not resolve to a stored V18 forecast` | `baselineForecastId` must name a forecast AISP holds |
| `orientation disagrees` | Home and away are the other way round from the stored baseline |
| `baselineBinding declares no <field>` | The per-game binding is required in full |
| `artifactType ... contradicts the handoff's` | Make the per-game type match the handoff |
| `PROSPECTIVE forecast published ... at or after its scheduled start` | Publish before first pitch, or label the package `HISTORICAL_REPLAY` |
| `component ... did not run and gives no fallbackReason` | Every non-running component needs a reason |
| `forecast declares no simulation.simulatorId` (or version/seed/season/teams) | These are non-null in the research-shadow table |

## Where to put the package

Anywhere AISP can read. Pass the handoff path to the importer; the object
directories are resolved relative to the handoff's own directory unless a
`package_root` is given. The existing `mlb-v18-current` exchange layout works
unchanged.
