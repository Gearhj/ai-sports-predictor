# ADR-001: Canonical, time-aware sports data

Status: accepted for Phase 1.

## Decision

ASP models provider-neutral sports entities with internal UUIDs and separate source mappings. Observation tables are append-only. Every time-sensitive record includes an observation/availability timestamp, and feature generation applies an explicit data cutoff.

Providers are accessed through adapter protocols returning canonical data-transfer objects. Raw responses are archived before normalization when allowed. Published forecasts are immutable and reference exact feature, model, and market versions.

## Consequences

- MLB providers can be changed without rewriting downstream forecasting or UI contracts.
- NFL can add sport-specific adapters and feature builders while reusing games, markets, forecasts, users, and performance domains.
- Storage volume grows because historical observations and forecast versions are retained.
- Corrections require new records or auditable status transitions instead of destructive replacement.
- Point-in-time joins are more complex but prevent silent look-ahead leakage.
