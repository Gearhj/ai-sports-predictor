# Data Sources, Provenance, and Operational Constraints

This register describes ASP's current acquisition boundaries. It is an
engineering inventory, not a legal conclusion or a promise that an upstream
service will remain available. Terms, attribution, caching, storage, and
commercial-use requirements must be reviewed against the service tier actually
selected before any public or commercial deployment. Until that review is
recorded, redistribution permission is **unverified**.

Development ingestion is manual and conservative. Scheduler, Odds polling,
Statcast acquisition, and official position publication default to disabled.
Provider or API failures never substitute demo fixtures.

## Source register

### MLB Stats API

- **Boundary:** `https://statsapi.mlb.com/api/v1`, isolated behind
  `MLBStatsAPIAdapter`.
- **Fields used:** schedule/game identifiers, scheduled start, game status,
  venue, home/away teams, season records, probable pitchers, pitcher hand when
  the optional people lookup succeeds, and observed final scores.
- **Refresh policy:** schedule and probable-pitcher context are eligible at
  `MORNING`, `T_MINUS_6H`, `T_MINUS_3H`, `T_MINUS_60M`, `T_MINUS_30M`, and
  `T_MINUS_10M`. `POSTGAME` may refresh status and scores. Development runs only
  through an explicit operator action; event-relative production dispatch is a
  future, separately enabled policy.
- **Freshness policy:** schedule context is fresh through 1 hour, stale through
  6 hours, and critical after 6 hours. Team/player statistics are fresh through
  24 hours, stale through 36 hours, and critical after 36 hours. Late lifecycle
  stages may impose stronger completeness requirements without relabeling an old
  observation as new.
- **Attribution/terms:** the endpoint is operated within MLB's public web
  properties, but ASP has not established a contractual SLA, redistribution
  right, or commercial-use conclusion. Required attribution and trademark use
  remain subject to documented review.
- **Caching/storage:** accepted responses are written to the ignored raw archive
  before normalization. Canonical point-in-time rows retain source and
  availability timestamps. Retention duration and deletion policy are not yet
  approved for deployment.
- **Replacement strategy:** provider IDs remain in external mappings and the
  downstream model uses provider-neutral entities. A licensed baseball feed can
  replace the adapter, but identifier mapping, status semantics, pitcher
  confirmation, historical availability, and final-time behavior must be
  revalidated.
- **Known uncertainty:** response shapes and hydrate behavior are not treated as
  a stable commercial contract. Season-to-date endpoints cannot reconstruct a
  historical point-in-time observation unless ASP actually archived it then.

### Baseball Savant / Statcast

- **Boundary:** Baseball Savant's CSV leaderboard acquisition, isolated behind
  `BaseballSavantStatcastAdapter`.
- **Fields used when enabled:** hitter xwOBA, barrel rate, hard-hit rate,
  average exit velocity, launch angle, and sample size; pitcher xERA, velocity,
  whiff rate, strikeout rate, walk rate, and sample size. Accepted rows map to
  canonical players, persist point-in-time Statcast observations, and promote
  versioned empirical-Bayes player talent estimates. Coverage gates prevent a
  sparse extract from being described as team-level measured coverage.
- **Refresh policy:** disabled by default. It may run only when explicitly
  enabled after schema, access, and operational review. When enabled, an extract
  is fresh through 24 hours, stale through 72 hours, and critical after 72
  hours. When disabled it is `not_applicable`, not missing, and it cannot block a
  Gate 2 forecast.
- **Attribution/terms:** access, attribution, caching, storage, and commercial
  redistribution requirements are unverified. No permission is inferred from
  technical accessibility.
- **Caching/storage:** successful extracts are checksummed in the raw archive
  and curated Parquet area. Cached data must retain its actual acquisition time
  and must never be presented as newly live. Retention remains pending review.
- **Replacement strategy:** the adapter boundary permits a reviewed export,
  `pybaseball`, or a licensed Statcast-compatible supplier. Column definitions,
  minimum-sample behavior, player identifiers, and point-in-time availability
  require a new schema/version review for every replacement.
- **Known uncertainty:** the community-facing CSV schema may change without
  notice and historical acquisition may be expensive or restricted. The
  2026-08-13 controlled-live validation left Statcast disabled and made no
  Statcast request, so the implemented live promotion path has fixture and
  integration-test evidence but no claim of live coverage from that run.

### The Odds API

- **Boundary:** sport key `baseball_mlb`; `h2h`, `spreads`, and `totals` for the
  configured region, isolated behind `TheOddsAPIAdapter`.
- **Fields used:** event and bookmaker identity, market/outcome, point, American
  and decimal prices, implied probability, provider observation time, ASP
  availability time, and quota remaining/used/last-request headers when present.
- **Refresh policy:** polling defaults to disabled. Development acquisition is a
  deliberate manual action after key, cooldown, quota floor, and daily-budget
  preflight. A controlled live validation permits **at most one Odds API HTTP
  request** and no automatic retry. Future production cadence must budget each
  lifecycle window and transparently skip a request when quota is unavailable.
- **Freshness policy:** an observation is fresh through 15 minutes, stale through
  30 minutes, and critical after 30 minutes. Critical or absent Odds block a
  claim about a current market comparison, but do not cause ASP to fabricate a
  price or prevent a baseball-only forecast.
- **Attribution/terms:** subscription-tier rules for display, attribution,
  storage, derived data, historical use, and redistribution are unverified and
  must be reviewed before public or commercial use.
- **Caching/storage:** raw responses and normalized snapshots are append-only.
  Book, market, provider-event, observed, and available timestamps must remain
  attached. Retention and public display duration must follow the reviewed
  subscription terms; no current policy conclusion is claimed.
- **Replacement strategy:** bookmaker and event identity are provider-neutral
  downstream. A replacement must revalidate team matching, doubleheaders,
  market/period keys, line movement, suspended outcomes, timestamps, region
  coverage, quota accounting, and storage/display rights.
- **Known uncertainty:** credit cost varies with configured markets and regions;
  response headers are the observed authority for a completed request. A key's
  presence does not authorize polling. In the 2026-08-13 controlled validation,
  exactly **one HTTP request** for `h2h`, `spreads`, and `totals` in the US region
  succeeded and the provider reported a **quota cost of 3 credits**
  (`requests_last=3`, `requests_used=3`, 497 remaining). This is one network
  request charged three credits, not three requests, and it does not establish
  the cost of a future request.

### Open-Meteo

- **Boundary:** hourly forecast endpoint, isolated behind `OpenMeteoAdapter`.
- **Fields used:** temperature, humidity, precipitation and probability,
  pressure, weather code, wind speed/direction, forecast-valid time, and ASP
  availability time at maintained venue coordinates.
- **Refresh policy:** eligible for explicit refresh once schedule venues are
  known, and required from `T_MINUS_3H` through the final pregame refresh. A
  future dispatcher should cache identical venue/date requests and avoid
  duplicate acquisition. Development does not imply an automatic cadence.
- **Freshness policy:** fresh through 3 hours, stale through 6 hours, and
  critical after 6 hours. Roof status and venue changes remain separate evidence
  and can override the usefulness of an outdoor forecast.
- **Attribution/terms:** the applicable service tier, attribution placement,
  rate limits, caching, storage, and commercial-use conditions are unverified.
  They must be reviewed and recorded before deployment.
- **Caching/storage:** accepted raw payloads and normalized snapshots may be
  retained with distinct acquisition and forecast-valid timestamps. A cached
  forecast is never relabeled as a new observation. Deployment retention is
  pending review.
- **Replacement strategy:** a weather provider can replace the adapter while
  preserving canonical units and timestamps. Venue coordinates, hourly
  alignment, forecast issue time, severe-weather fields, roof status, licensing,
  and rate limits must be revalidated.
- **Known uncertainty:** nearest-hour weather may not represent conditions at
  first pitch, and outdoor observations do not establish whether a retractable
  roof will be open.

### ASP demo fixtures

- **Boundary:** bundled, deterministic examples under explicit development-only
  demo tooling.
- **Refresh policy:** none. Fixtures are static and never participate in a live
  lifecycle or provider fallback.
- **Attribution/storage:** ASP-created illustrative data, persistently labeled
  `ILLUSTRATIVE DEMO — NOT LIVE RESULTS`. If deliberately seeded into a disposable
  database, records retain `DEMO` provenance.
- **Replacement strategy:** fixtures may evolve only with their contract tests;
  they must never reuse current bookmaker branding or appear on live routes.

## Game-relative lifecycle

Every game is evaluated from its own current `starts_at`, so a reschedule moves
all future targets and two games in a doubleheader remain independent.

| Stage | Intended responsibility |
| --- | --- |
| `MORNING` | Establish the day's schedule, probable pitchers, team/player context, and initial weather context. |
| `T_MINUS_6H` | Refresh core schedule and team/player evidence. |
| `T_MINUS_3H` | Refresh core evidence and require non-critical weather. |
| `T_MINUS_60M` | Refresh late pitcher, weather, and permitted market context. |
| `T_MINUS_30M` | Recheck material late context without inventing changes. |
| `T_MINUS_10M` | Perform the last eligible pregame acquisition and publication attempt. |
| `FINAL` | Freeze the successfully published final pregame forecast. No later pregame publication is permitted, and first pitch closes publication even if no freeze record exists. |
| `POSTGAME` | Refresh observed final state, then permit settlement and evaluation; never generate a pregame forecast. |

These stages are policy targets, not a claim that every provider can support
every window. Missing budget, disabled sources, stale evidence, or upstream
failure must produce an auditable skip, stale, critical, or missing state.

## Controlled live-ingestion discipline

Before any controlled live run, verify configuration without printing values,
keep scheduled polling and official position publication disabled, and select
the current MLB date explicitly. The permitted sequence is:

1. MLB schedule and probable-pitcher context.
2. MLB team/player context for discovered games.
3. Weather for known venue coordinates.
4. Statcast only when separately enabled and reviewed; otherwise report
   `not_applicable`.
5. At most one Odds API request after an atomic budget/cooldown/quota preflight.
6. Pregame forecast generation only while `as_of < starts_at` and before a final
   freeze.

There is no automatic retry in the one-request Odds validation. A failed request
remains a failed attempt and cannot cause demo data, a second request, or a
fabricated quota value.

## Raw archive and retention

Accepted adapter responses are written beneath
`data/raw/<source>/YYYY/MM/DD/` with source, UTC ingestion time, source event time
when supplied, checksum, status, record count, and schema version. Normalized
observations preserve `available_as_of` independently from provider event or
forecast-valid time. Raw, curated, and feature artifacts are excluded from Git.

Payloads that fail before provider-schema validation are not yet guaranteed to
be archived. Closing that diagnostic gap, defining encryption/access controls,
setting retention/deletion periods, and confirming each source's storage and
redistribution terms are required before production use.

## Provider-free Gate 3 training boundary

`python -m app.cli train-mlb-models --as-of 2026-08-13T16:00:00Z` never acquires
provider data. It reads an already-migrated database, selects only frozen `LIVE`
pregame snapshots and append-only final observations available by the cutoff,
and writes checksum-addressed diagnostic or model artifacts under the ignored
`.runtime/gate3/` tree. It does not create an administrator, start a scheduler,
construct adapters, retry an upstream request, or use demo fixtures.

Raw archives are provenance inputs to normalization, not direct training rows.
An archived payload becomes eligible only through the canonical point-in-time
contract; a current mutable provider response cannot reconstruct what was known
at an earlier forecast cutoff. Missing historical fields and closing lines remain
missing. Dataset manifests retain source mode, source-evidence checksums,
feature/schema versions, cutoff, exclusions, coverage, and leakage results.

The current local database has zero eligible labeled games, so the provider-free
command must return `INSUFFICIENT_DATA / NO_PROMOTION` without fitting a model.
See `GATE_3_MLB_MODEL_TRAINING_VALIDATION_REPORT.md` for the full boundary.

## Gate 3A historical-source review

This section records the 2026-08-13 pre-backfill review. It is a technical and
provenance record, not legal advice. No historical provider payload was acquired
as part of the review. A missing or unclear permission is recorded as
`LEGAL_REVIEW_REQUIRED`, not treated as approval.

### MLB Stats API historical evidence

- **Desired evidence:** regular-season schedule identity, final outcomes,
  innings, box scores, player participation, actual starters, and dated
  transactions. No historical Gate 3A records have yet been acquired.
- **Interface:** ASP now has bounded historical schedule, final-feed, and
  box-score adapter methods; an atomic compressed archive; resumable archive
  checkpoints; and separate canonical persistence/materialization services.
  The public backfill command remains deliberately raw-archive-only and reports
  `ARCHIVED_CANONICAL_PENDING`; it is not yet an end-to-end canonical backfill.
  Dated transaction acquisition is not implemented.
- **Temporal meaning:** final and completed event facts may be archived as
  retrospective reconstruction. Fetching a historical record today does not
  establish that ASP observed it before the historical game, and the current
  retrieval timestamp must not be backdated. Actual starters and lineups prove
  participation, not their exact pregame announcement time.
- **Terms/attribution:** `LEGAL_REVIEW_REQUIRED`. MLB's current Terms of Use
  include restrictions on automated scripts and disproportionate load across
  MLB Digital Properties. A responsible owner must determine applicability to
  the Stats API, local research, derived features, and the proposed request
  pattern before acquisition. No attribution determination has been made.
- **Caching/archive:** implemented local, Git-ignored, atomic gzip evidence with
  separate payload/file checksums, retrieval time, historical event time,
  endpoint/request fingerprint, checkpoint/run identifiers, and idempotent
  conflict detection. No Gate 3A historical payload has been acquired. Public
  redistribution is not authorized by this document.
- **Commercial/redistribution status:** uncertain; `LEGAL_REVIEW_REQUIRED`.
- **Replacement:** another officially licensed game/box-score feed can replace
  this adapter if it preserves canonical MLB game/player/team IDs, revisions,
  event timestamps, and the same knowledge-time contract.
- **Reference reviewed:** `https://www.mlb.com/official-information/terms-of-use`
  on 2026-08-13.

### Baseball Savant / Statcast history

- **Desired evidence:** date-bounded pitch and batted-ball events sufficient to
  calculate prior-only rolling xwOBA, contact quality, velocity, whiff, pitch
  mix, strikeout/walk, and platoon features. No Statcast history has been
  acquired locally.
- **Interface:** official Statcast Search and CSV field documentation expose
  `game_date`, `game_pk`, batter/pitcher MLB IDs, pitch/event sequence, expected
  outcomes, contact, velocity, handedness, and game-type fields. ASP's current
  adapter implements season-level batting and pitching leaderboards only; those
  full-season extracts are not point-in-time training evidence for earlier
  games.
- **Temporal meaning:** only events from completed prior games may enter a
  target game's historical pregame aggregate. Same-game events, later games,
  future seasons, and later-fetched season summaries are forbidden.
- **Terms/attribution:** public technical documentation exists, but automated
  archival, retention, derived-data, redistribution, and commercial-use rights
  have not been confirmed. Because Baseball Savant is an MLB property, the MLB
  terms concern also applies. Status: `LEGAL_REVIEW_REQUIRED`.
- **Caching/archive:** if approved, use a one-day controlled trial, concurrency
  one, bounded pacing/retry, compressed date partitions, raw and normalized
  checksums, and a circuit breaker. Stop on rejection or schema drift.
- **Commercial/redistribution status:** uncertain; no raw redistribution.
- **Replacement:** a licensed pitch-event source with stable MLB IDs and dated
  event semantics may replace Savant. Revalidate definitions across the
  PitchFX/Statcast/Hawk-Eye eras.
- **References reviewed:** `https://baseballsavant.mlb.com/csv-docs` and
  `https://www.mlb.com/glossary/statcast` on 2026-08-13.

### Open-Meteo historical context

- **Desired evidence:** temperature, humidity, wind, precipitation, and
  pressure near first pitch. No historical weather has been acquired.
- **Interface:** ASP currently uses only `https://api.open-meteo.com/v1/forecast`.
  Open-Meteo documents an `/v1/archive` reanalysis API and separate historical
  forecast/single-run products. Reanalysis describes reconstructed conditions;
  it is not proof of the forecast available at a historical cutoff.
- **Attribution/license:** Open-Meteo states that API data are CC BY 4.0 and
  requires attribution, a license link, and an indication of changes.
- **Plan status:** current pricing states that historical, climate, ensemble,
  and satellite-radiation APIs require the Professional plan or higher; the
  free API is limited to non-commercial use. ASP has not established such an
  entitlement. `HISTORICAL_WEATHER = UNAVAILABLE_ON_CURRENT_PLAN`.
- **Caching/archive:** none acquired. If a permitted plan is later confirmed,
  archive the exact dataset/model/run, coordinates, issue/valid time, units,
  attribution, checksum, and retrieval time. Preserve weather as missing in the
  meantime.
- **Commercial/redistribution status:** CC BY 4.0 data terms apply, while API
  service-tier and commercial endpoint terms require review.
- **Replacement:** a licensed issued-forecast archive or observation/reanalysis
  source may replace it, but reconstructed conditions and issued forecasts must
  remain distinct.
- **References reviewed:** `https://open-meteo.com/en/license`,
  `https://open-meteo.com/en/terms`,
  `https://open-meteo.com/en/pricing`, and
  `https://open-meteo.com/en/docs/historical-weather-api` on 2026-08-13.

### The Odds API historical markets

- **Desired evidence:** timestamp-matched pregame moneyline pairs and defensible
  closing observations. No historical Odds data or closing series has been
  acquired; the local archive contains one current controlled acquisition and
  no `is_closing` rows.
- **Interface:** ASP implements only current
  `/v4/sports/baseball_mlb/odds`. Official documentation describes historical
  snapshot and event endpoints, but those are not implemented locally.
- **Plan/quota:** official documentation states historical endpoints are
  available only on paid usage plans. Historical odds cost 10 credits per
  region per market. The current entitlement has not been proven to include
  them and the remaining current-data quota is scarce.
- **Status:** `HISTORICAL_MARKET_DATA = UNAVAILABLE_ON_CURRENT_PLAN` and
  `HISTORICAL_CLV = UNAVAILABLE`. Gate 3A will make zero historical Odds calls,
  consume zero credits, and train without a market feature/benchmark.
- **Caching/archive:** no historical archive is authorized. Prospective current
  snapshots continue to retain exact observed/available timestamps under their
  existing quota and terms controls.
- **Attribution, archival, redistribution, commercial use:** service-tier and
  contract review are required before any historical storage; no permission is
  inferred here.
- **Replacement:** a licensed, timestamped bookmaker feed may replace it while
  preserving book, market, participant, odds, observed time, availability time,
  and an explicit closing methodology.
- **References reviewed:** `https://the-odds-api.com/liveapi/guides/v4/`,
  `https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html`, and
  `https://the-odds-api.com/historical-odds-data/` on 2026-08-13.

### Historical raw and derived-data boundary

Approved historical payloads belong under
`data/raw/historical/<source>/YYYY/MM/` and remain Git-ignored. PostgreSQL owns
canonical identity, append-only outcome/event summaries, source policy,
checkpointing, and feature lineage; large event-level analytical material may
use versioned Parquet under ignored curated/feature roots. Local possession is
not permission to publish raw data. Retention, access controls, deletion,
attribution, and redistribution remain source-specific review items.

Historical reconstruction and ASP's prospective archive are never equivalent.
The latter is the future gold standard because ASP can prove the exact lineup,
starter, bullpen, weather, market, forecast, and outcome state available at
each lifecycle checkpoint.

## Review record required before deployment

For every external source, the deployment owner must record the reviewed terms
URL and version/date, service tier, allowed purposes, attribution text and
placement, caching/retention limits, derived-data and redistribution rules,
geographic restrictions, rate/quota limits, replacement plan, reviewer, and
next review date. A blank record means **unverified**, not permitted by default.
