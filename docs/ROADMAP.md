# AI Sports Predictor Roadmap

The roadmap preserves a sport-neutral platform core while keeping Phase 1 narrowly focused on an honest, operable MLB forecasting product.

## Phase 1.5 — Game Countdown and dedicated mobile experience

**Status: future design only; not active or implemented.** The cadence, UI fields, alerts, final-forecast freeze, and dedicated mobile application described below are product requirements for a later phase. They do not describe the behavior of the current scheduler, API, web client, or provider plan.

### Event-relative Game Countdown Refresh

Future refresh orchestration should be relative to each game's scheduled first pitch rather than relying only on a global interval. Every run must preserve point-in-time inputs, source provenance, refresh timestamps, and prior forecast versions.

| Window | Future refresh responsibility |
| --- | --- |
| Overnight | Ingest final results, settle eligible positions, run evaluation, and retain the completed game's history. |
| Morning | Refresh the schedule, probable pitchers, team statistics, weather, and available market data, then generate the day's initial forecast. |
| T−6h | Refresh the game's core context and regenerate the forecast when readiness requirements are met. |
| T−3h | Repeat the core-context refresh and regenerate from newly available point-in-time inputs. |
| T−60m | Refresh confirmed pitcher, lineup when available, player availability, weather, and the complete available market, then regenerate the forecast. |
| T−30m | Refresh late game context and market data, then regenerate when material or required inputs changed. |
| T−10m | Capture the final input snapshot, publish the immutable final pregame forecast, and freeze that forecast version. |
| Postgame | Ingest the result, settle positions, update performance evaluation, and preserve the full forecast and market-movement history. |

The future UI lifecycle should expose **Time to first pitch**, **Next ASP refresh**, **Last data refresh**, **Last model refresh**, **Forecast movement**, **Market movement**, and an explicit **FINAL FORECAST** state. These values must come from persisted, auditable timestamps and forecast/market versions rather than a browser-only countdown.

This is a target cadence, not a promise of refresh frequency. Free provider tiers and other constrained data plans may not supply enough quota, update frequency, or data coverage for every window. A future implementation must be quota-aware, skip or degrade refreshes transparently, cache safely, display stale/missing states, and never fabricate an update. Higher-frequency paid data must remain an explicit operational and licensing decision.

### Dedicated mobile architecture

The future mobile product should use the same versioned ASP API, authentication, entitlement, forecast, market, and audit contracts as the desktop product, while having a dedicated mobile information architecture and interaction model. It must not simply shrink the desktop interface: **desktop is for deep research; mobile is for fast game-day decision support**.

Future mobile priorities, in intended order, are:

1. Today's Games.
2. Game countdown.
3. Current ASP probability and forecast movement.
4. Best available odds and market movement.
5. Model edge, confidence, and data readiness.
6. Official ASP position and virtual bankroll.
7. Alerts for material forecast changes and a final-forecast notification.
8. Leaderboard and community features later.
9. Fantasy features later.

Mobile delivery will require its own navigation, compact game cards, notification preferences and delivery infrastructure, offline/stale-state behavior, accessibility review, and platform release/security work. None of those capabilities are active in Phase 1.

## Phase 2 — NFL

- NFL schedule, roster, injury, depth-chart, weather, and play-by-play adapters.
- EPA, success rate, explosive-play, pressure, coverage, special-teams, and pace features with time-of-availability tracking.
- Team-strength and quarterback-strength components, spread/total/moneyline forecasts, seeded simulation, and NFL-specific calibration.
- Reuse the canonical `Sport`, `League`, `Game`, `Team`, `Player`, `Forecast`, and `Market` contracts without MLB-specific migrations.

## Phase 3 — Membership

- Patreon membership verification mapped to provider-neutral entitlements.
- Premium forecast access and durable membership synchronization.
- Stripe as a peer provider rather than a replacement for the entitlement domain.
- Email eligibility followed by independent ASP password creation; no provider password sharing.

## Phase 4 — ASP Community

- Member virtual bankrolls and hypothetical positions using captured market lines.
- Transparent leaderboards, private groups, and Humans vs. ASP comparisons.
- ASP Analyst Rating based on risk-adjusted return, calibration, closing-line value, sample reliability, and risk discipline.
- Bayesian shrinkage and `UNRATED`, `PROVISIONAL`, and `ESTABLISHED` cohorts so small samples do not dominate.

## Phase 5 — Fantasy Intelligence

- Yahoo and Sleeper integration adapters.
- Configurable fantasy scoring systems, roster imports, player projections, matchup simulation, start/sit, waiver, and trade analysis.
- Authorization boundaries that keep imported leagues and rosters private.

## Phase 6 — Market Intelligence

- Higher-frequency, quota-aware odds collection and line-movement analysis.
- Closing-line capture and CLV evaluation.
- Best-line, two-way/three-way arbitrage, middles, book discrepancies, stale-market signals, and sharp-book modeling.
- No automated wager execution or sportsbook-account connection.

## Phase 7 — More sports

- NCAAF, NHL, WNBA, and additional sports through sport-specific adapters, features, and models.
- Shared market, forecast registry, user, entitlement, bankroll, performance, and audit infrastructure.

## Cross-phase gates

Every phase must retain point-in-time correctness, immutable published forecasts, explicit demo/live provenance, reproducible model artifacts, calibration reporting, responsible positioning, and source/licensing review.
