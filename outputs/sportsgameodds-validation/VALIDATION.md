# SportsGameOdds — one authorized validation request

2026-09-09. One request. Not retried. **Fallback NOT activated** — three blockers below.

| | |
|---|---|
| Branch | `claude/aisp-nfl-wow-and-odds-recovery-v1` |
| Commit | `589b3ce5d9d3d0e202bbfc4c4ae00ce4d3f6327d` |
| Requests made | **1** |
| Objects consumed | **2** of the 2,500 monthly Amateur allowance |
| HTTP | 200, 2,843,414 bytes, sha256 `b5b16a58…f13b582` |

Request, exactly as specified:
`GET https://api.sportsgameodds.com/v2/events?leagueID=MLB&limit=2&oddsAvailable=true`,
`X-Api-Key` header. The dry run was run first and matched.

**Credential handling.** Loaded from `.env` into `Settings`; confirmed only as a
boolean and a length. It is sent in a header, never a URL. Every header whose
name contains key/auth/token is redacted before printing or writing. No value,
prefix or hash was produced anywhere — a hash of a 32-character secret is a
crackable record of it.

---

## What the response established

| # | Point | Result |
|---|---|---|
| 1 | MLB league and event identifiers | **PASS** — `leagueID: "MLB"`, `sportID: "BASEBALL"`, `eventID` opaque strings (`eDWGjWWVmAYJif2tMkoz`) |
| 2 | Home/away identity and orientation | **PASS** — `teams.home` / `teams.away`, each with `teamID` (`DETROIT_TIGERS_MLB`), `names.long/medium/short`, explicit `statEntityID` |
| 3 | Scheduled start and timezone | **PASS** — `status.startsAt` ISO-8601 with `Z`, i.e. UTC |
| 4 | Event status | **PASS** — `started`, `live`, `completed`, `ended`, `cancelled`, `delayed`, plus period detail |
| 5 | Bookmaker identity | **PASS** — 9 books on both events: betmgm, bovada, caesars, **draftkings**, espnbet, **fanduel**, pointsbet, unibet, williamhill |
| 6 | Complete two-sided moneyline | **PASS** — `points-home-game-ml-home` ↔ `points-away-game-ml-away`, paired explicitly by `oddID`/`opposingOddID`; all 9 books carry both sides on both events |
| 7 | American/decimal interpretation | **PARTIAL** — American only, as strings (`"-125"`, `"+3300"`). **No decimal field exists anywhere.** AISP must convert; `available: false` appears and must be treated as not offered |
| 8 | Bookmaker-level update timestamps | **PASS, with a caveat** — `lastUpdatedAt` per book **per side**. In 4 of 18 event-book pairs the two sides disagree: draftkings 63.8s (both events), espnbet 133.8s, bovada 207.9s |
| 9 | Pagination | **PASS** — `nextCursor` returned. A second page was **not** fetched; that is a second request |
| 10 | Object accounting | **PASS** — no usage headers of any kind are returned. The provider's published definition is one object per item returned by `/events`, so this cost **2**. Consumption must be counted locally |
| 11 | Missing market vs no event | **PASS** — an absent event is absent from `data`; a market nobody priced has an empty `byBookmaker` (561 of 2,615 keys) and `bookOddsAvailable: false` |
| 12 | Pregame point-in-time suitability | **NOT DEMONSTRATED** — both returned events were already **live** (`started: true`, 9th inning). Structure supports pregame (`openBookOdds` shows -122/-125), but no pregame payload was observed |

**Free-tier notice, verbatim:** *"Response is missing 11468 bookmaker odds.
Upgrade your API key to access all data from this query."* The tier withholds
data and says so. The moneyline itself was complete for all 9 books.

**Amateur tier (published):** 2,500 objects/month, 10 requests/min, 10-minute
update frequency. A 10-minute refresh sits comfortably inside AISP's 60-minute
freshness target and 240-minute window.

## Licensing — read separately, no objects consumed

Their terms prohibit selling, sublicensing, scraping or distributing the Data as
a standalone offering, and prohibit redistribution through data dumps, bulk
exports or database access. Data may be used inside applications providing
material value independent of the Service. Storage is permitted while the
subscription is active and must be destroyed on termination. Provider
attribution should remain intact.

They do **not** clearly authorize broader commercial display, so per your
instruction SportsGameOdds is treated as a **private research/development
source only**. The captured payload is git-ignored for the same reason; only the
derived record is committed.

---

## Why the fallback was not activated

### Blocker 1 — the payload contradicts the adapter's structural contract

The adapter maps payloads with dotted paths over **nested lists**
(`event → books[] → markets[] → outcomes[]`), reading identity from leaf values.
SportsGameOdds puts identity in dictionary **keys**:

```
event.odds["points-home-game-ml-home"].byBookmaker["draftkings"].odds
            ^ market + side identity        ^ bookmaker identity
```

`_iterate` returns a dict as a single item and there is no way to read the key,
so the manifest model cannot express this shape. Every required *field* exists;
the *traversal model* does not fit. Your instruction for this case was explicit —
stop after the single request and report the evidence, do not create mappings —
so no manifest was written. Fixing it means teaching the mapping model
dict-keyed iteration, which is a design change, not a manifest.

### Blocker 2 — a research provider cannot price a governed decision

`GOVERNED_SOURCES = {THE_ODDS_API}`, and that filter is applied on the decision
path in four places: `bankroll/positions.py:110`, `funnel_read_context.py:330`
and `:348`, and `prospective_shadow_service.py`. MLB experiment games are
`source_mode = LIVE`, so `authorised_sources_for_mode("LIVE")` returns the
governed set and SportsGameOdds rows are excluded.

So even with perfect ingestion, these quotes would land as research evidence and
**would not reach decision evaluation**. Making them count means adding
SportsGameOdds to the governed set — a provider-governance policy change, which
this session's own constraint forbids ("no policy change is authorized") and
which contradicts the fallback's design rule that it must never masquerade as
the primary provider. That is your decision, not mine.

### Blocker 3 — pregame behaviour is unobserved

Both returned events were live. The one authorized request is spent, and
`startsAfter` filtering would be a second one.

### A fourth thing that needs deciding, not fixing

AISP's completeness rule requires both sides observed at the same instant.
SportsGameOdds timestamps each side independently. Mapping the per-side stamp
onto `observed_at` fails that rule for draftkings, espnbet and bovada. The
defensible alternative is to treat the market's `observed_at` as the later of
the two side stamps — the moment both displayed prices were jointly live — but
that is a semantic decision about someone else's data and should be made
deliberately, not inside a mapping file.

---

## State: nothing changed

| | |
|---|---|
| `sportsgameodds_enabled` | `False` |
| mapping | none — `mapping_state: UNMAPPED` |
| fallback window | unset, therefore **closed** |
| Provider ledger | 654 rows, **all `THE_ODDS_API`** |
| Odds snapshots | 222,318 rows, **all `THE_ODDS_API`** |
| Governed sources | `['THE_ODDS_API']` |
| Experiment bankrolls | 1000.00, 1000.00, 1000.00 — unchanged |
| Positions | 11 — unchanged, none created or backfilled |
| Thresholds / policies | untouched; no decision, position or settlement written |
| The Odds API credits | **0 spent** — ledger unchanged since 2026-09-08 |

Current MLB slate coverage is therefore unchanged: **0 of 15 covered, 9 at risk,
5 cutoff missed**, every one `QUOTA_GUARD_BLOCKED`. No game received a usable
quote from this validation, because no ingestion was activated.

Also fixed this round: the operator view built the fallback adapter without its
settings, so it reported a configured credential and base URL as absent. It now
reports `credential_configured: True`, `base_url_configured: True`,
`mapping_state: UNMAPPED`, `ready_to_enable: False`.

## Tests

Focused only. 38 in `test_nfl_play_level_and_funnel.py`, all passing, plus the
61 across it and `test_odds_zero_cost_collection.py`. Six new ones pin what this
request established: object-is-an-event, no usage headers, both sides at every
book, side-timestamp skew, the withheld-odds notice with pagination unfetched,
and the harness refusing more than two events.

## What would unblock activation

1. Teach the mapping model dict-keyed iteration, then author the manifest from
   this captured response (sha256 `b5b16a58…`) — evidence-based, not guessed.
2. Decide the `observed_at` semantics for independently timestamped sides.
3. Decide whether SportsGameOdds may price governed MLB decisions. Without this,
   ingestion produces research rows only and the coverage gap stays open.
4. One further request with `startsAfter` to observe a genuine pregame payload.

Items 1, 2 and 4 are work. Item 3 is a governance decision.
