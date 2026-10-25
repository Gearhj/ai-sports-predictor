# A House Divided — player-prop wagering: activation assessment

Status on 2026-09-18: **not available**. The lobby shows a *Player Props* category for every game, with the
sport's groups listed, and the message **"Player props are not available yet for this game."** No prop can be
added to a Pick Slip. Nothing here was enabled, no provider was called, and no quota was spent.

A virtual prop wager needs three things, and none of them is in place yet:

1. A current stored sportsbook quote.
2. A player identity bound to the canonical game.
3. A governed way to settle it.

## What exists today

| Piece | State |
|---|---|
| Quote storage | Table `player_prop_quotes` (contract `AISP_PLAYER_PROP_QUOTE_V1`, `server/app/player_props/`). **0 rows.** |
| Supported markets (storage) | MLB only: `batter_hits`, `batter_total_bases`, `batter_home_runs`, `pitcher_strikeouts`. There are no RBIs, no NFL and no NBA prop markets. |
| Identity binding | Fails closed. A row is `RESOLVED` only when the event binds to one canonical MLB game and the name binds to one player on that game's teams, through lineup, starter, probable-pitcher or roster evidence. Anything else is `QUARANTINED` and never selectable. |
| Collector | `app/player_props/collector.py`: The Odds API, one game, one market, one region per request. It is inactive: `PLAYER_PROP_LIVE_ENABLED=false`, and the research credits are set to 0 per month and 0 per day. |
| Settlement | **None.** AISP stores no governed per-player final statistics (strikeouts, hits, home runs, total bases, RBIs, yards, receptions, points…). The existing settlement rules cover only moneyline, spread/run line and total. |
| Assessment | Model Studio lists player-prop probabilities as `INSUFFICIENT_EVIDENCE`. That is fine for a user's own virtual pick, which needs no AISP assessment. |

## What activation requires

### 1. Provider objects and cost

**The Odds API** (the collector that exists today):
- **Cost:** each request costs *markets × regions* credits for **one game**. For a full MLB slate of 15 games with the
  four supported markets and one region, that is **60 credits per collection pass**. A price must be under 240 minutes
  old at the pick lock, so each game needs at least one pass inside that window. That makes **≈60 credits per slate
  day, or ≈1,800 a month**, before any refresh.
- **Balance:** the account had **5 credits against a floor of 10** at last check. The owner has also restricted The Odds
  API. A live prop test is not possible until both change.
- **Game identity:** each game also needs a stored The Odds API event id. None exists for upcoming games, because
  main-market prices now come from SportsGameOdds.

**SportsGameOdds** (the provider we already pay per game object):
- Its parser already recognizes player-prop odds inside the same per-game object we buy for the main markets
  (`sgo_parser`, kind `PLAYER_PROP`). Those rows are discarded today.
- If the objects we already buy carry the needed prop markets, collection could cost **no extra objects**. That must be
  **measured on stored payloads before anyone relies on it**, including which markets, which books and how fresh.
- A new ingest path from SGO prop odds into `player_prop_quotes` would still need the same identity rules.
- It would draw on the same daily allocation and the 300-object reserve. It must not grow the monthly total without an
  owner decision.

### 2. Identity data

- **MLB:** lineups or confirmed starters before the lock. Batter props bind through lineup evidence, pitcher props
  through starter or probable-pitcher evidence. The roster fallback exists but is the weakest tier.
- **NFL:** a governed roster or active/inactive feed and a player registry per team. Neither exists in the prop
  binding yet.
- **NBA:** staged. Not before NBA activation.
- The provider's player name must never create a player. Ambiguous names, for example two players with the same name,
  must stay quarantined.

### 3. Settlement evidence (the real blocker)

- A **governed, revisioned per-player final box score** is needed for every settled game. It must be stored with its
  source, retrieval time and revision, like `game_final_outcomes` for team scores.
- **Rules per market:**
  - over/under at the line;
  - the push rule at whole-number lines;
  - "did not play" handling. Most books void a prop if the player takes no plate appearance, never pitches, or is
    inactive. That rule must be written down, tested and shown on the slip.
  - stat corrections after the final: whether they re-settle, and until when.
- Only after the steps above can a prop wager settle exactly once, the same way team markets do now.

## Proposed order (each step needs owner approval)

1. **Free.** Measure whether stored SGO event payloads already contain the MLB prop markets, and how fresh they are.
   This only reads data we already have.
2. Add a governed MLB player box-score source and store it. The MLB Stats API box score is free and already used for
   schedules.
3. Write and test the settlement rules above, the did-not-play void first.
4. Ingest SGO prop odds into `player_prop_quotes` within the existing allocation. If that isn't possible, approve an
   Odds API budget for step 1's gap.
5. Turn on MLB props in the House Divided lobby: only `RESOLVED`, current and settleable rows, best price plus Shop
   books, and the same lock, stale-price and moved-price rules as team markets.
6. NFL and NBA follow once each sport has identity and box-score coverage.
