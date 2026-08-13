# Gate 2 Implementation Report — MLB Point-in-Time Data & Feature Intelligence

## Status and scope

Gate 2 is implemented as a point-in-time MLB forecasting and provenance slice.
It adds append-only knowledge-time observations, deterministic feature
intelligence, immutable feature snapshots, lifecycle-aware readiness, and a
controlled-live validation path. This is a **DEVELOPMENT** implementation. It
does not represent a trained or production-validated model, makes **no model
superiority claim**, and does not authorize public or commercial use of any
external data source.

The report records the repository and local controlled-live evidence available
on 2026-08-13. No provider was called while preparing this report, and no
further provider call is authorized or needed for this documentation step.

## Delivered architecture

Gate 2 introduced these operational contracts:

- `GameStateService.get_game_state(game_id, as_of_timestamp)` selects only
  source-mode-matched evidence known by the cutoff. Observed home/away identity
  is retained in append-only game-state evidence rather than inferred from a
  later mutable projection.
- Canonical observations cover schedule/status, probable or confirmed starter,
  lineup and ordered entries, roster availability, reliever workload,
  Statcast/player talent, versioned park factors, ratings, weather, and odds.
- `FeatureSnapshotService` freezes selected evidence, feature values,
  missing/stale/provisional inventories, coverage metadata, and a deterministic
  semantic checksum. Published forecasts reference the immutable snapshot.
- Empirical-Bayes helpers combine a longer-term prior, season evidence, and
  optional recent evidence with explicit effective samples. Small samples
  shrink more than large ones; missing recent evidence remains missing; no
  streak, morale, trade, or narrative bonus is introduced.
- Lineup offense is player-first and weighted by batting order and expected
  plate appearances. Confirmed evidence outranks projected evidence. Missing
  participation or replacement evidence is not presented as observed.
- Bullpen availability is derived only from explicit cumulative workload,
  expected role weight, and availability evidence. Sparse or unusable bullpen
  rows remain missing and earn no readiness credit.
- Statcast promotion maps canonical players, normalizes percentage units,
  retains sample sizes, supports hitter and pitcher underlying-skill metrics,
  and persists versioned talent estimates. Sparse player coverage remains
  visible but does not earn operational completeness or readiness credit.
- Lifecycle stages span `MORNING` through `POSTGAME`, with deterministic
  refresh targets, stricter late-stage blockers, and a first-pitch publication
  lock. A historical live replay uses its evidence cutoff but records actual
  computation time, remains a draft, and is excluded from public performance.
- LIVE and DEMO evidence are isolated in queries, duplicate keys, snapshots,
  and forecasts. Unsupported source modes fail closed.

## Controlled-live validation record

The local PostgreSQL evidence records one controlled validation for the
2026-08-13 MLB date. The evidence is intentionally reported as observed, not
generalized into a guarantee about future provider behavior.

| Item | Observed result |
| --- | --- |
| MLB schedule | 9 LIVE games normalized |
| MLB team/player refresh | 18 team-stat snapshots, 468 roster observations, and 18 starter observations persisted |
| Weather | 8 normalized snapshots; one venue lacked coordinates and remained unavailable |
| Odds transport | Exactly **one Odds API HTTP request** was attempted and succeeded; no retry |
| Odds quota accounting | The one HTTP request cost **3 provider credits** because `h2h`, `spreads`, and `totals` were requested for the configured US region |
| Odds response | 11 provider events, 472 normalized price observations, `requests_last=3`, `requests_used=3`, and 497 credits remaining |
| Raw archives | Checksummed raw artifacts exist for MLB Stats API, Open-Meteo, and The Odds API |
| Forecast artifacts | 9 games received immutable Gate 2 snapshots and published morning forecasts; a later local regeneration created a second version per game |
| Statcast | Disabled for this controlled-live run; 0 canonical Statcast observations and 0 player talent estimates |

One HTTP request and three quota credits describe different units. The request
count measures network calls; the quota cost is the provider-reported charge
for the markets and region in that single call. They must not be described as
three HTTP requests.

## Honest data-completeness record

The controlled-live run did not observe complete Gate 2 feature intelligence:

- **Lineups:** 0 canonical lineup observations. Both club lineups remained
  missing rather than being inferred from a box score or current roster.
- **Bullpen:** 0 reliever workload/availability observations. Bullpen
  availability and bullpen xERA remained missing; no team-ERA value was
  relabeled as an availability observation.
- **Statcast:** disabled and not called. Hitter contact-quality, pitcher xERA,
  and Statcast-derived talent fields remained missing.
- **Park factors:** no reviewed venue factor evidence was present. The numerical
  model may use a neutral 1.0 prior, but the snapshot labels the park feature as
  provisional and does not count it as observed coverage.
- **Recent form, rest/travel, defense, baserunning, roof orientation, and
  opponent adjustment:** remained missing where attributable evidence did not
  exist.
- **Weather:** 8 of 9 games had normalized weather. The venue without
  coordinates remained missing.

Morning forecasts were publishable because core schedule, starter identity,
team summaries, weather where available, and market evidence satisfied the
configured development readiness policy. Readiness was 72.5 for eight games
and 62.5 for the game without weather. Publication does not imply full feature
coverage, production calibration, or decision quality.

## Model and evaluation status

The registered Gate 2 model is `asp_mlb_ensemble` version
`2.0.0-gate2-08cca634c81b`, linked to feature version `mlb_pregame/2.0.0`.
Its registry status is **DEVELOPMENT**. The ensemble contains an interpretable
record-strength baseline, a statistical run model, and a deterministic
development challenger. The registry explicitly records:

- no trained production artifact;
- no production holdout evaluation;
- `superiority_claim=false`; and
- no claim of better calibration, accuracy, profitability, or market edge.

The observed live forecasts are suitable for provenance and system-behavior
inspection only. A chronological training/evaluation design, calibration,
benchmark comparison, sample-size analysis, and leakage-controlled holdout are
Gate 3 work.

## Verification completed

Gate 2 verification covers migration and metadata parity, append-only guards,
cutoff exclusion of later evidence, observed team identity, LIVE/DEMO
separation, semantic checksum behavior, lineup precedence, sparse-evidence
coverage gates, empirical-Bayes shrinkage, bullpen monotonicity, Statcast
normalization, lifecycle transitions, historical-replay draft isolation,
provider quota reservation, and API/client lifecycle contracts.

The final provider-disabled backend run completed with **124 passing tests**.
The focused Gate 2 selection completed with **74 passing tests**. Frontend
verification completed with **36 passing tests** and a successful production
build. Ordinary tests disable external providers and consume no provider quota.
Alembic upgraded through revision `0003` and reported no metadata drift.

Live-data browser acceptance passed at desktop (1440x900) and mobile
(390x844) viewports. The dashboard and game analysis showed LIVE provenance,
the exact forecast cutoff, explicit missing lineup/Statcast/bullpen states,
and the corrected arbitrary-consensus-total simulation probability. Both
viewports had no document-level horizontal overflow or browser-console errors.

## Operational and legal constraints

- Scheduler, recurring odds polling, Statcast acquisition, and official
  position publication remain disabled by default.
- Controlled odds acquisition is guarded by an atomic reservation ledger,
  cooldown, quota floor, and daily request budget. A failed controlled request
  cannot trigger a retry or demo fallback.
- No provider call should be made merely to reproduce this report. The raw
  archive, canonical database rows, ingestion runs, and request ledger are the
  evidence for the recorded validation.
- MLB Stats API, Baseball Savant, The Odds API, and Open-Meteo terms,
  attribution, caching, retention, derived-data, redistribution, service-tier,
  and commercial-use requirements remain **unverified**. Technical access is
  not permission. A documented legal/terms review is required before public or
  commercial deployment.
- Provider availability and schemas can change. Missing data must continue to
  produce missing, stale, provisional, disabled, or unavailable states rather
  than fabricated values or silent demo substitution.

## Gate decision

Gate 2 engineering objectives are implemented and verified for development:
point-in-time provenance, feature transparency, lifecycle control, source-mode
isolation, and controlled-live acquisition are present. The gate does **not**
approve production model promotion, automated provider polling, official
positions, commercial data use, or a superiority claim. Those remain blocked
on Gate 3 evaluation and documented provider/legal review.
