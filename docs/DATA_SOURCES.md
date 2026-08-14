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
  integration-test evidence but no claim of live coverage from that run. The
  later Gate 3A one-day historical trial is a separate raw-only path and does
  not change that live-promotion status.

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

The prospective Gate 3 population still has zero eligible labeled games, so its
provider-free training command must return `INSUFFICIENT_DATA / NO_PROMOTION`
without fitting a model. Gate 3A's separately labeled historical-reconstruction
population has corrected 15-row one-day v3 and 90-row seven-day v4 artifacts
with 16/43 (37.2093%) feature coverage and independently passing temporal
checks. The 90-row artifact is below the unchanged 200-row minimum, and neither
artifact has been passed to model fitting. See
`GATE_3_MLB_MODEL_TRAINING_VALIDATION_REPORT.md`
and `GATE_3A_A1_REAL_MLB_DATA_REPORT.md` for the two evidence boundaries.

## Gate 3A historical-source review

This section records the 2026-08-13 source review and subsequent bounded A1
run. It is a technical and provenance record, not legal advice. The project
owner approved MLB evidence acquisition only for local/private ASP research and
model development. Public redistribution remains prohibited pending review,
commercial deployment is not approved, raw-data resale is prohibited, and
final attribution/licensing review is still required. The completed 2025 MLB
core and single Statcast trial are the bounded A1 actions recorded here; there
is no standing approval for multi-season or Statcast expansion.

### MLB Stats API historical evidence

- **Acquired evidence:** the complete 2025 regular-season core contains 2,430
  unique official finals dated 2025-03-18 through 2025-09-28, 4,860 team-game
  rows, and 127,872 player-game rows across 184 canonical SQL checkpoints. All
  finals have an official game date; there are no revisions, duplicates, or
  rejected records. Dated transaction acquisition is not implemented.
- **Interface:** ASP has bounded historical schedule-range and v1.1 final-feed
  methods, atomic compressed archives, exact request accounting, resumable
  checkpoints, canonical persistence, strict reconstruction, and immutable
  dataset materialization. The approved local-research command is end to end;
  Odds, Statcast, and weather have no dispatch path in that MLB core command.
  Statcast uses the separate raw-only trial path described below.
- **Temporal meaning:** final and completed event facts are retrospective
  reconstruction. All acquired outcomes use the exact last recorded play end
  time as `DERIVED_LAST_EVENT_COMPLETION_PROXY`; it is not an explicit MLB
  final-publication time. Fetching a historical record today does not establish
  that ASP observed it before the historical game, so retrieval and label
  availability remain in 2026. Actual starters and lineups prove participation,
  not their exact pregame announcement time.
- **Terms/attribution:** owner-authorized for `LOCAL_PRIVATE_RESEARCH` only.
  Technical access restrictions must be respected. Final attribution,
  licensing, retention, redistribution, and any commercial-use decision remain
  pending; the authorization is not a legal conclusion.
- **Caching/archive:** 2,434 referenced Git-ignored atomic gzip files totaling
  315,853,213 bytes, with separate payload/file checksums, retrieval time,
  historical event time, endpoint/request fingerprint, checkpoint/run
  identifiers, and idempotent conflict detection. The closeout raw root has
  2,512 files totaling 323,342,002 bytes. Physical accounting is larger than
  canonical references: MLB made 2,460 attempts with 2,459 successes and one
  legacy-v1 preflight `404`; 14 duplicate successful feeds came from two
  chunks that failed closed on rain-ended schema handling, and extra discovery
  and restart schedule calls are retained in the accounting. There were zero
  retries or rate-limit events. Public redistribution is not authorized by this
  document.
- **Commercial/redistribution status:** commercial deployment not approved;
  public redistribution prohibited pending review; raw-data resale prohibited.
- **Replacement:** another officially licensed game/box-score feed can replace
  this adapter if it preserves canonical MLB game/player/team IDs, revisions,
  event timestamps, and the same knowledge-time contract.
- **Reference reviewed:** `https://www.mlb.com/official-information/terms-of-use`
  on 2026-08-13.

### Baseball Savant / Statcast history

- **Desired evidence:** date-bounded pitch and batted-ball events sufficient to
  calculate prior-only rolling xwOBA, contact quality, velocity, whiff, pitch
  mix, strikeout/walk, and platoon features. The one-day 2025-06-17 technical
  trial acquired 4,375 raw rows across 15 games, 304 batters, 133 pitchers, and
  436 unique players with 119 fields and no detected schema drift.
- **Interface:** official Statcast Search and CSV field documentation expose
  `game_date`, `game_pk`, batter/pitcher MLB IDs, pitch/event sequence, expected
  outcomes, contact, velocity, handedness, and game-type fields. ASP's
  historical trial path is deliberately single-day and raw-only. It does not
  promote rows into the existing season-leaderboard observations or canonical
  historical features.
- **Temporal meaning:** only events from completed prior games may enter a
  target game's historical pregame aggregate. Same-game events, later games,
  future seasons, and later-fetched season summaries are forbidden. The trial's
  publication time and historical eligibility are unproven, so its evidence is
  `PARTIAL / RAW_ONLY`, not a pregame feature.
- **Terms/attribution:** the project owner authorized this single local/private
  research trial. Automated archival, retention, derived-data, attribution,
  redistribution, and commercial-use conclusions remain unconfirmed. Because
  Baseball Savant is an MLB property, the MLB terms concern also applies. The
  trial is not a legal determination or approval to expand.
- **Caching/archive:** the one successful request produced a 2,523,608-byte
  archive. Payload SHA-256 is
  `bcaa171d4adfb8666ab3ee85eb0490710103825ecf973f8b07a80e55c53a4251`,
  CSV SHA-256 is
  `932d42bec7a1536374648ae6bc99827f31267bdd56f02db1955638e24ae3b055`,
  and archive SHA-256 is
  `601af8d47af4dfe66102efd4df38ecd324e8ebf38449ddc8eb0c708337e79f2d`.
  No canonical or training row was created.
- **Commercial/redistribution status:** not approved; no raw redistribution.
- **Replacement:** a licensed pitch-event source with stable MLB IDs and dated
  event semantics may replace Savant. Revalidate definitions across the
  PitchFX/Statcast/Hawk-Eye eras.
- **References reviewed:** `https://baseballsavant.mlb.com/csv-docs` and
  `https://www.mlb.com/glossary/statcast` on 2026-08-13.

#### Stage A2 provider-free Statcast promotion

Stage A2 revalidates the existing 2025-06-17 gzip wrapper, exact response
bytes, request/schema fingerprints, mappings, and event identities without
constructing a provider adapter or making a network request. The deterministic
promotion artifact is `statcast-a2-d0c7d1b78871d2f7` with
`4,375` minimal event rows and `1,311` research aggregates. Exact artifact
checksums and sizes are recorded in
`GATE_3A_A2_2025_FEATURE_MATERIALIZATION_REPORT.md`.

This is a Tier B research promotion, not proof that Statcast was available at a
historical T-minus-six-hour cutoff. The archive's 2026 ingestion timestamp is
the earliest defensible availability under
`CURRENT_ARCHIVE_INGESTION_NO_HISTORICAL_PUBLICATION_TIME`. Promoted events and
aggregates remain `PARTIALLY_RECONSTRUCTABLE`, training-ineligible, and
evaluation-ineligible; they contribute zero strict Tier A feature values.
Score, win-expectancy, run-expectancy, and future-game fields are excluded.
A2 expansion beyond the existing day is `NO_STATCAST_EXPANSION`.

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
  `HISTORICAL_CLV = UNAVAILABLE`. Gate 3A made zero historical Odds calls,
  consumed zero credits, and produced no market feature/benchmark.
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

### Stage A2 2025 feature-materialization boundary

Stage A2 reads the complete A1 core locally and makes `0` provider calls. It targets
2,428 strict Tier A outcomes and a separate 2,430-target Tier B enhanced shadow
population. The strict dataset admits `2,428` rows; the enhanced dataset admits
`2,430` research-only
rows, all labeled Tier B and training/evaluation-ineligible. Actual historical
starters and batting orders remain absent from strict features because
participation does not prove pregame publication.

Feature lineage is persisted as compact, checksum-addressed evidence-state
attestations. The attestations record source/family counts, evidence and
eligibility bounds, canonical-set and source-bundle checksums, feature-value
checksums, and target-exclusion/temporal-validity assertions. Raw payloads,
promoted event artifacts, feature matrices, and dataset artifacts remain
Git-ignored. No historical Odds or weather evidence is added, and no 2023-2024
acquisition occurs.

## Review record required before deployment

For every external source, the deployment owner must record the reviewed terms
URL and version/date, service tier, allowed purposes, attribution text and
placement, caching/retention limits, derived-data and redistribution rules,
geographic restrictions, rate/quota limits, replacement plan, reviewer, and
next review date. A blank record means **unverified**, not permitted by default.
