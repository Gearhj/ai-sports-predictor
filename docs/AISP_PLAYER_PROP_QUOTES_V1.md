# AISP player-prop quotes (v1) and the Model Studio disposition

Contract `AISP_PLAYER_PROP_QUOTE_V1`. Code: `server/app/player_props/`. Table `player_prop_quotes`
(migration `20260917_0044`).

## Model Studio disposition (recorded 2026-09-16)

| | |
|---|---|
| Passive player counters | Available in Model Studio |
| Player-prop probabilities | `INSUFFICIENT_EVIDENCE` |
| Permitted in AISP | Quote display; the user's own virtual selection (My Card) |
| Not permitted in AISP | AISP probability, fair price, edge, expected return, decision, position, confidence |
| Future assessed path | Only after prospective Model Studio calibration evidence exists |

The disposition is served with every player-prop payload (`producer_disposition`). Every quote is labelled
**"Sportsbook market — no AISP assessment"**. The table has no model, probability or edge columns, and tests assert
that none are ever added.

## What is stored

Each provider outcome in a supported market becomes one row:

- **Provider:** provider, event id, sport key, market key, outcome name, player display name, line.
- **Price:** sportsbook, American price and decimal price.
- **Timing:** provider observed time (the market's `last_update`, falling back to the book's), ingestion time and
  source mode.
- **Integrity:** SHA-256 of that outcome's provider JSON. The raw payload is not stored.
- **Budget link:** the provider-ledger reservation that paid for the row.

Binding fields:

- canonical game, MLB Stats API game reference, canonical player, team, outcome side and line;
- identity status (`RESOLVED` or `QUARANTINED`), the identity evidence, the quarantine reason and whether the row is
  selectable.

Supported markets: `batter_hits`, `batter_total_bases`, `batter_home_runs` (Over/Under or Yes/No) and
`pitcher_strikeouts`.

### Wording

The consumer sees a plain description, with the provider's exact terms shown underneath it:

| Provider outcome | Consumer wording |
|---|---|
| `batter_hits` Over 0.5 | **1+ hit** |
| `batter_hits` Over 1.5 | 2+ hits |
| `batter_hits` Under 0.5 | No hit |
| `batter_home_runs` Yes | 1+ home run |
| `pitcher_strikeouts` Over 6.5 | 7+ strikeouts |

## Identity fails closed

1. **Game.** The event binds to exactly one canonical MLB game, using the stored THE_ODDS_API event mapping first and
   the league-scoped matcher otherwise. Any of the following quarantines every outcome of the event:
   - the event maps to a non-MLB game;
   - the participants disagree with the game, including a reversed home/away;
   - no unique match exists.
2. **Player.** The display name (accent-, punctuation- and suffix-insensitive) must bind to exactly one existing player
   on one of the game's two teams. Team evidence is taken only from governed records available before the reference
   time (the earlier of ingestion and first pitch), strongest first:
   1. this game's lineup;
   2. its starter observation;
   3. its probable pitcher;
   4. the latest season roster row.

   `Player.team_id` is not evidence. A name is never turned into a new player.
3. **Quarantine reasons.** Every refusal is stored as quarantined, non-selectable metadata with its reason:
   - `GAME_UNRESOLVED`, `GAME_NOT_MLB`, `EVENT_PARTICIPANT_CONFLICT`;
   - `PLAYER_UNKNOWN`, `PLAYER_NO_GOVERNED_EVIDENCE`, `PLAYER_AMBIGUOUS` (for example two "Will Smith"s, one on each
     team), `PLAYER_WRONG_TEAM`;
   - `MARKET_UNSUPPORTED`, `OUTCOME_UNRECOGNIZED`;
   - `ORIENTATION_CONFLICT` (an Over/Under pair that disagrees on the line);
   - `LINE_MISSING`, `PRICE_MISSING`.
4. **Duplicates.** Re-ingesting the same observation is a no-op, enforced by the unique key.

## Reads and the UI

`/command-center/assessments` → `player_props` lists only `RESOLVED` rows for the slate's MLB games. It shows the
newest observation per player, market, outcome, line and book, with freshness measured at read time (240 minutes).
Quarantined rows are counted by reason, never listed.

**All Markets:**

- A game shows a Player Props section only when it has stored quotes, grouped by player and then by prop.
- Each row shows the book, line, price, observed time and freshness, labelled "Sportsbook market — no AISP assessment".
- A current quote can be added to My Card as a straight user virtual pick. A stale quote is display-only.
- There are no prop parlays and no cross-game products.

The interface preview at `/decisions?view=markets&preview=player-props` renders a provider-shaped **test fixture**. It is
labelled "Test fixture — not live sportsbook data" and nothing in it can be selected.

The historical replay contains no player props, because none were stored at any past moment.

## The collector (inactive)

`app/player_props/collector.py` and `python -m scripts.player_prop_quote_request`.

### Dry run (default)

The dry run writes nothing and calls nothing. It names exactly one request:

```
GET /v4/sports/baseball_mlb/events/{eventId}/odds?regions=us&markets=batter_hits&oddsFormat=american&dateFormat=iso
```

- **Scope:** one canonical MLB game, one market and region `us`.
- **Predicted cost:** 1 credit (1 market × 1 region). The provider's `x-requests-last` header is authoritative.
- **Credentials:** the receipt never contains the key.

The dry run reports each of these checks:

- canonical MLB game;
- not started;
- provider event bound;
- lane is `OTHER_RESEARCH` (player-prop markets never draw on the protected MLB lanes);
- `PLAYER_PROP_LIVE_ENABLED`;
- the shared daily Odds API request slot;
- the monthly and daily budget above the 150-credit reserve;
- the research-lane allocation, which also leaves every protected lane's unspent allocation intact;
- the provider floor, with no recovery probe taken from here;
- the cooldown;
- no identical request already reserved today.

### Execute

`--execute` still refuses unless every check passes. When they do, it:

1. reserves the credit in `provider_request_ledger` and commits;
2. reads the key from settings;
3. makes one GET;
4. records `SUCCEEDED` or `FAILED` with the provider's reported cost and remaining balance;
5. stores the quotes, linked to the reservation.

There is no loop, schedule or slate-wide collection.

### Settings

| Setting | Default | Effect |
|---|---|---|
| `PLAYER_PROP_LIVE_ENABLED` | `false` | Must be true to execute |
| `PLAYER_PROP_RESEARCH_MONTHLY_CREDITS` | `0` | The research lane's monthly allocation for props |
| `PLAYER_PROP_RESEARCH_DAILY_CREDITS` | `0` | Its daily cap |

### State on 2026-09-16

The dry run for PHI @ NYM (2026-09-17 19:15 ET) is blocked on four checks:

- **`provider_event_bound`:** no stored Odds API event id. The newest stored Odds API quote is from 2026-09-07.
- **`live_execution_enabled`:** the flag is off.
- **`monthly_and_daily_budget`** and **`provider_floor`:** the provider balance is **5**, below the floor of 10.
- **`research_lane_allocation`:** the lane is also refused, because of that same floor.

The balance renews with the UTC billing month.

## A separately authorized one-game, one-market live test (owner action)

1. **Wait for a healthy balance.** After the provider balance renews (UTC month), let one governed MLB moneyline request
   record an in-window balance above the floor. Until then the lane accounting is `PROVIDER_BALANCE_UNKNOWN`, and
   optional lanes stay locked.
2. **Choose the game.** Pick one upcoming MLB game whose Odds API event id is stored. Its moneyline collection stores
   it on `odds_snapshots.source_event_id`, or an explicit `external_id_mappings` row can be added.
3. **Enable the test** in `.env` for the test only:
   - `PLAYER_PROP_LIVE_ENABLED=true`
   - `PLAYER_PROP_RESEARCH_MONTHLY_CREDITS=1`
   - `PLAYER_PROP_RESEARCH_DAILY_CREDITS=1`

   The CLI reads settings at start, so no backend restart is needed.
4. **Dry run** from `server\`. It must print `"may_execute": true` and `"predicted_credits": 1`:
   `..\.venv\Scripts\python.exe -m scripts.player_prop_quote_request --game-id <game-uuid>`
5. **Execute** the single request:
   `..\.venv\Scripts\python.exe -m scripts.player_prop_quote_request --game-id <game-uuid> --execute`
6. **Revert the flags** to their defaults. Check the result's `credits_spent` and `remaining_reported`, and the
   `ingest` counts: resolved, quarantined and reasons.
