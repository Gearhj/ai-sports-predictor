# Data Sources, Fields, and Operational Constraints

## MLB Stats API

The schedule adapter requests the public MLB Stats API and normalizes schedule/game identifiers, scheduled start, status, venue, home/away teams, season records, probable pitchers, and final scores when available. Provider IDs are retained only in source-mapping records.

Operational risk: the feed is public but not a contractual commercial SLA. Response shapes and hydrations can change. Raw responses and schema versions make regressions diagnosable.

## Baseball Savant / Statcast

The feature contract reserves xwOBA, barrel rate, hard-hit rate, exit velocity, launch-angle summaries, strikeout/walk indicators, pitcher velocity/pitch mix, and expected-results fields. Acquisition is isolated so `pybaseball` or direct sanctioned exports can be replaced. In Phase 1, `STATCAST_ENABLED=false` is the safe default and the adapter is archive-oriented; it does not yet promote Statcast fields into the canonical team feature snapshots used by live forecasts. The UI therefore reports Statcast as unavailable instead of filling synthetic values.

Operational/licensing risk: historical acquisition can be expensive and upstream access rules can change. Before commercial redistribution, review MLB/Baseball Savant terms and attribution requirements. Cached data must not be presented as newly live.

## The Odds API

The adapter uses sport key `baseball_mlb` and normalizes `h2h`, `spreads`, and `totals` from every returned bookmaker. It stores bookmaker identity, outcome/selection, point/line, American and decimal prices, implied probability, observation time, event ID, and source. Quota remaining/used/last-request headers are captured when supplied.

Polling defaults to disabled. Enabling it or manually refreshing can consume credits; interval, market, and region settings are configurable. A normal refresh without a configured key fails explicitly rather than substituting fixtures. Raw snapshots are append-only. Review the subscription's storage, display, attribution, and redistribution terms before public/commercial deployment.

## Open-Meteo

The initial adapter requests hourly temperature, humidity, precipitation/probability, pressure, wind speed/direction, and weather code using the venue coordinate reference. No key is required for the development configuration.

Operational risk: forecast times may not align exactly with first pitch, roof status can supersede outdoor forecasts, and commercial usage must comply with the selected Open-Meteo service terms and attribution rules.

## Demo fixtures

Bundled fixtures are available only through the explicit `/demo` frontend
routes. A deliberately seeded disposable database may also exercise the backend
fixture pipeline. API and provider failures never activate either mechanism;
live pages remain empty or unavailable. Every illustrative page carries the
persistent label `ILLUSTRATIVE DEMO — NOT LIVE RESULTS` and must never be
interpreted as a current market observation.

## Raw archive

Accepted adapter responses are written under `data/raw/<source>/YYYY/MM/DD/` with a checksum and ingestion manifest reference before canonical database normalization. A payload that fails inside provider-schema parsing is not yet guaranteed to be archived; closing that diagnostic gap is production-hardening work. Raw, curated, and feature data are runtime artifacts and excluded from Git. Retention and redistribution policies must be reviewed before production use.
