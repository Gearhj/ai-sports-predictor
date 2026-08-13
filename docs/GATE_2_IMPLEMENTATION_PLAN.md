# Gate 2 Implementation Plan — MLB Point-in-Time Data & Feature Intelligence

## Baseline audit

- Repository: `C:\Users\james\ai-sports-predictor`
- Branch: `main`
- Starting HEAD: `fca2ef2419c5ea3dd95f97224b424b38f4d45556`
- Starting worktree: clean
- Database: PostgreSQL through psycopg. The connected development database is
  reachable and currently contains zero sports, leagues, teams, players, games,
  roster rows, weather observations, sportsbooks, markets, odds observations,
  team/player feature rows, and forecasts.
- Provider inspection policy: no provider call was made during this audit.

The Gate 0/1 source adapters are MLB Stats API schedule/team summaries,
Open-Meteo weather, The Odds API markets, an isolated Baseball Savant CSV
adapter, and explicit demo fixtures. Weather and odds are append-oriented and
already expose `observed_at`/`available_as_of`. Statcast is archived to raw JSON
and Parquet but is not promoted into canonical player features. Recurring odds
polling, Statcast, the scheduler, and official positions remain disabled.

## Current gaps

1. `Game.starts_at`, `Game.status`, scores, venue, and the probable-pitcher
   fields on `GameTeam` are mutable. Historical forecast generation reads the
   current `Game`/`GameTeam` rows, so later schedule or starter knowledge can
   leak backward.
2. There is no canonical lineup observation or lineup-entry model.
3. Existing roster and player-team history rows are time-stamped but lack a
   unified availability vocabulary, participation probability, replacement
   evidence, and one knowledge-time query contract.
4. Forecast features are team-summary-first. Missing starter, bullpen, park,
   and recent inputs currently reach documented neutral numerical fallbacks;
   those fallbacks are not materialized as missing/provisional evidence.
5. Recent runs receive a fixed blend. There is no sample-size-aware true-talent
   or platoon shrinkage framework.
6. The Statcast adapter produces hitter rows but does not map them to canonical
   players, normalize percentage units, or make them available to feature
   snapshots.
7. Bullpen state is currently team ERA, ratings default to 1500, and park factor
   is read from team JSON with a permanent 1.0 fallback.
8. Readiness uses broad generic thresholds and has no lifecycle-specific FINAL
   blockers. The scheduler is daily/interval based rather than game-relative.
9. Forecast versions do not persist structured input-family movement evidence.

## Canonical point-in-time model

Gate 2 will introduce append-only records for:

- game status/schedule observations;
- starter observations;
- lineup observations plus ordered player entries;
- roster/availability observations, including effective team and optional
  replacement/participation probability;
- reliever availability observations;
- canonical player metric observations and true-talent snapshots;
- versioned venue park factors and team ratings;
- immutable, checksum-backed game feature snapshots;
- per-game refresh/lifecycle state; and
- forecast movement associations.

Mutable `Game` and `GameTeam` columns remain compatibility projections for
current reads, matching, and settlement. They are not authoritative inputs for
historical features after this gate. New observations are append-only through
SQLAlchemy guards, and published forecasts reference a durable feature snapshot.

## Knowledge-time query contract

`GameStateService.get_game_state(game_id, as_of_timestamp)` will be the only
service boundary used by Gate 2 feature generation, forecast generation,
historical replay, and future countdown refreshes. Every time-sensitive query
must include `available_as_of <= as_of_timestamp`; roster relationships must
also satisfy their effective time. The contract returns the most recent eligible
game, starter, lineup, roster, bullpen, weather, odds, player-metric, park, and
rating evidence without reading current mutable pitcher/status fields.

## Feature families and immutable snapshots

Each feature build will persist its game, generation time, data cutoff, schema
version, lifecycle, selected evidence identifiers, lineup/starter/bullpen/weather
and market references, feature values, completeness, missing/provisional/stale
inventories, Bayesian metadata, and SHA-256 content identity. Once referenced
by a published forecast, the snapshot cannot be modified or deleted.

Initial feature families are:

- lineup offense and handedness composition;
- starter quality, platoon, workload, rest, velocity/whiff and contact quality;
- likely-available bullpen quality and workload;
- team priors and season summaries;
- Statcast underlying skill;
- park and conservative raw weather context;
- defense and baserunning interfaces (null until attributable evidence exists);
- rest/travel/schedule context;
- opponent-adjustment interface; and
- point-in-time team rating.

No subjective morale score, winning-streak bonus, injury penalty, trade bonus,
or synthetic defense/baserunning score will be introduced.

## Bayesian and recent-form design

For each metric, ASP will combine a longer-term prior, season evidence, and
recent evidence using transparent empirical-Bayes pseudo-sample weights:

`posterior = (prior*n_prior + season*n_season + recent*n_recent*m) /
             (n_prior + n_season + n_recent*m)`

`m` is a documented evidence multiplier. Stable underlying-skill observations
use greater effective weight than equally sized noisy outcome windows. Small
windows therefore move the estimate modestly, large windows move it more, and
extreme short-window values remain shrunk. Missing recent evidence falls back to
the prior/season posterior. Platoon splits use the same structure with a larger
stabilization sample against the overall estimate.

Recent-state outputs are deltas, not binary streak labels:
`recent_outcome_delta`, `recent_expected_performance_delta`,
`recent_contact_quality_delta`, `recent_plate_discipline_delta`,
`recent_velocity_delta`, `recent_whiff_delta`, and
`posterior_true_talent_delta`.

## Source strategy

- MLB Stats API: quota-free controlled schedule, active roster/player context,
  probable starter, and lineup evidence when present. Missing lineups remain
  missing; no projection is invented by the live adapter.
- Open-Meteo: controlled game-time weather snapshots where enabled.
- The Odds API: at most one explicit Gate 2 request, protected by cooldown,
  quota-floor, and daily-budget checks. No retry for convenience.
- Baseball Savant: canonical promotion is validated from deterministic local
  fixtures first. Live acquisition remains disabled unless its existing flag is
  deliberately enabled after source review.
- Demo: explicit fixture paths only and never mixed with live games.

## Refresh lifecycle and readiness

Pure lifecycle classification will support MORNING, T_MINUS_6H, T_MINUS_3H,
T_MINUS_60M, T_MINUS_30M, T_MINUS_10M, FINAL, and POSTGAME, with a deterministic
next-refresh target and a first-pitch publication lock. Development stays manual
and conservative. Production receives the selection architecture but no
aggressive polling default.

Source-specific freshness policies will define fresh, stale, and critical ages
for schedule, lineup, starter, roster, weather, odds, team stats, Statcast,
bullpen, park, and ratings. Readiness v2 will return score, missing critical
items, stale items, provisional items, and hard blockers. FINAL is stricter than
MORNING; critically stale schedule/starter/lineup evidence can block publication.

## Migration strategy

Create a new forward Alembic revision after `20260812_0002`; do not rewrite the
baseline migrations. The migration will create the Gate 2 tables, constraints,
foreign keys, and knowledge-time indexes. Validation will cover upgrade from
the current head, fresh upgrade, downgrade/upgrade on SQLite, SQL generation for
PostgreSQL, and application metadata parity.

## Test strategy

All ordinary tests use isolated SQLite with provider keys blank and every
provider/scheduler/publication flag disabled. Deterministic tests will cover:

- later starter and lineup evidence excluded from earlier state/features;
- effective-time trades and roster/availability changes not leaking backward;
- weather, odds, Statcast, and ratings respecting the cutoff;
- postgame stats/final scores excluded from pregame snapshots;
- immutable/checksum-stable feature snapshots;
- small/large/extreme/missing recent samples and underlying-vs-outcome shrinkage;
- platoon shrinkage;
- projected/confirmed/replacement/uncertain lineups and batting-order weights;
- availability-aware bullpen aggregation;
- all countdown stages, next refresh, final freeze, and post-start lock;
- source-specific freshness and FINAL critical blockers; and
- demo/live separation plus a no-provider-call ordinary suite.

## Operational risks

- MLB feed fields and pregame lineup availability can vary by game and time.
- Free sources have no contractual completeness or commercial redistribution
  guarantee; licensing/attribution requires later review.
- Park, defense, baserunning, opponent adjustment, roof/orientation, travel, and
  richer pitcher data may remain legitimately missing in the first Gate 2 live
  slice.
- The connected database is empty, so controlled live ingestion is required
  only after migrations and offline verification pass.
- Gate 2 does not train or promote a production model and makes no superiority
  claim. Gate 3 remains responsible for training, calibration, and validation.
