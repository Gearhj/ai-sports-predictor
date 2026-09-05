# AISP Prediction-Market Semantic Review Packets

Prepared 2026-09-05 02:35 ET during the bounded overnight capture certification.
Machine-readable twin: `docs/AISP_PREDICTION_MARKET_SEMANTIC_REVIEW_PACKETS.json`.

**Authority state:** both templates are `HELD_CLOSED` (seeded 2026-09-04 21:59:41 ET,
`decision_source = SEED`). No decision was executed and the approval endpoint
(`POST /admin/prediction-markets/semantic-templates/decide`) was not called. Each
packet ends in an advisory recommendation; one human decision per provider is still
required. Nothing here calls the system semantically approved.

Provider calls made for these packets: 2 (Kalshi rulebook PDF, Polymarket Gamma
events list), both public GETs without credentials. Every other fact comes from the
ASP database.

---

## Packet 1 — KALSHI · `KALSHI_MLB_FULL_GAME_WINNER` v1

| Field | Value |
|---|---|
| Provider / series | KALSHI · series ticker `KXMLBGAME` |
| Template identity / version / status | `KALSHI_MLB_FULL_GAME_WINNER` · v1 · `HELD_CLOSED` |
| Template source hash | `ec56dba82996c10aa53f8e49875925557733df22f727207d6389d8f5515108b2` |
| Source document 1 | KalshiEX LLC CFTC Reg. 40.2(a) self-certification "Will <team> win <time period> of <baseball game>?", rulebook BASEBALLGAMEWIN, dated 2026-02-12; `https://assets.kalshi.com/regulatory/product-certifications/MLBGAME.pdf`; retrieved 2026-09-05 ≈00:00 ET; Last-Modified Tue 24 Feb 2026; ETag `"f14c315c0d1b3a962b687d8d00ad2fb5"`; 326,380 bytes; sha256 `fc45c80992ac981552440e4be1226f7e8f9d4048e6bdac478cbdf56839c8c25b`; 13 pages |
| Source document 2 | Persisted `rules_primary` of the 32 mapped 2026-09-04 full-game contracts. Sample `KXMLBGAME-26SEP041410DETCLEG1-CLE`, rules sha256 `ec90b4285d7a27d4238e8362c43d8a4a37e0d2a83adeda78cfc02d9c1eeed641`: "If Cleveland wins the Detroit vs Cleveland professional baseball game 1 of the double header originally scheduled for Sep 4, 2026 at 2:10 PM EDT, then the market resolves to Yes." Settlement sources persisted: ESPN, Fox Sports, the Governing League |
| Question structure | Binary YES/NO market per team inside one event per game |
| League | MLB (`KXMLBGAME`); the rulebook is generic to organized baseball |
| Full-game scope | Yes. `<time period>` defaults to the full game "including all regulation innings and any extra innings or tie-breaking procedures" |
| Excluded market types | Sub-game periods, spreads, totals, run-first-inning, player props, futures/season wins, combos, other leagues — separate series, excluded by `classify_series` |
| Extra innings | Included ("all extra innings … are included in determining the winner") |
| Result authority | Official final result; Source Agencies in order: governing league (mlb.com/scores), ESPN, CBS Sports, Fox Sports, AP, WSJ, official broadcaster. First official result governs unless clearly erroneous and corrected before expiration |
| Postponed / rescheduled | Started within 48 h of original start: stays open, resolves on the rescheduled game. Not started within 48 h: resolves at the Exchange's last fair market price (not void) |
| Suspended | Resumed within 48 h: stays open, resolves on the completed game; otherwise last fair market price |
| Shortened | Official shortened game with a declared winner resolves on that result |
| Cancelled / void | Not started within 48 h, or pre-first-pitch forfeit: last fair market price. No void/refund outcome exists |
| Tie | No Tie strike on full-game MLB markets; a tie resolves each team strike at $0.50 |
| Orientation | YES = named team wins. HOME/AWAY from the event ticker `KXMLBGAME-<YYMMMDDHHMM><AWAY><HOME>[G<n>]`; named team must equal `yes_sub_title`; both teams must equal the mapped canonical game |
| Token orientation | No tokens; per-team YES/NO markets. HOME market → HOME selection (YES price = home win probability); AWAY market → AWAY |
| Doubleheader | G1/G2 ticker suffix and "game N of the double header" rules text; mapping uses the game ordinal (verified on the 09-04 DET@CLE pair: two distinct EXACT mappings) |
| Pregame timing rule | Markets open days ahead and trade in-play; only source timestamps strictly before canonical first pitch, ingested within the 120 s grace, are pregame; the final valid pregame observation is retained after first pitch |
| Matches ASP canonical complete-game winner | Yes for every game with an official result (regulation, extras, official shortened). Diverges only where there is no official result: Kalshi resolves at last fair market price, ASP records no result. ASP never consumes provider settlement |
| Condition check on persisted contracts | 09-04 slate: 32 mapped contracts, 28 match the v1 `rules_pattern`, 4 do not — both sides of DET@CLE G1 and G2, because the pattern requires "professional baseball game originally scheduled for" and doubleheader text reads "game 1 of the double header originally scheduled for". Those contracts stay held even after approval (fail-closed) |
| Effect of APPROVE | Every KXMLBGAME full-game contract satisfying all conditions inherits `SEMANTICS_MATCHED`; its pregame quotes become REVIEWED_PREGAME evidence. Non-matching contracts stay held. No forecast, decision, position, settlement or model artefact changes |
| Effect of REJECT | All KXMLBGAME contracts labelled `SEMANTICS_MISMATCH` (`rules_mismatch` in Excluded Market Activity); Kalshi stays research-only until a new version is approved |
| Ambiguity | (1) Non-result cases resolve at Exchange price rather than void. (2) Doubleheader phrasing is outside the v1 pattern; a v2 with an optional "game N of the double header" clause would be a new human decision. (3) The rulebook is the generic BASEBALLGAMEWIN certification; series-specific text was verified from persisted `rules_primary` |
| **Recommendation** | **RECOMMEND_APPROVE** — the filed rulebook, the persisted market text and the v1 conditions agree on a complete-game winner with official-result authority; the only gap (doubleheaders) fails closed. Not executed |

---

## Packet 2 — POLYMARKET · `POLYMARKET_MLB_FULL_GAME_WINNER` v1

| Field | Value |
|---|---|
| Provider / series | POLYMARKET · Gamma tag `mlb`, `sportsMarketType == "moneyline"` |
| Template identity / version / status | `POLYMARKET_MLB_FULL_GAME_WINNER` · v1 · `HELD_CLOSED` |
| Template source hash | `55f0ba1d5c46797be7dd102cd9f1d8eb8caa2588a07238c26806ee137aab8a45` |
| Source document 1 | Gamma market description, market 4001441, condition `0x67a56e928076b8c7342b25e902cb6a62d5aa0b8a36f51b51ba806a5913f59c5e`, "San Francisco Giants vs. New York Mets", gameStartTime 2026-09-05T20:10Z; `https://gamma-api.polymarket.com/events?tag_slug=mlb`; retrieved 2026-09-05 ≈00:00 ET; description sha256 `2ff0a8aeff85e286b6375ad99baf2b1da9d5e2c13853f432f73ed88631d86d90`; resolutionSource `https://www.mlb.com/` |
| Source document 2 | Persisted descriptions of the 16 mapped 09-04 moneyline markets (16 distinct hashes, one per game). Sample `0x0d234b…ec12` (WSH@LAD), sha256 `c160dd3272275e05d29ef50cb85f6c23e73626ee11b75efcc73f2904197e9949` |
| Question structure | One two-outcome market per game; outcomes are the two team names, each with a CLOB token id; templated description |
| League | MLB |
| Full-game scope | Yes — "if the <team> win the game", no inning qualifier; `moneyline` type separates it from spreads/totals/first-five |
| Excluded market types | Non-moneyline `sportsMarketType` values and non-MLB tags, excluded at normalization |
| Extra innings | Included implicitly (winner of the game; a tie resolves 50-50) |
| Result authority | Official final statistics recognized by the governing body (mlb.com); consensus of credible reporting only if nothing is published within 24 h |
| Postponed / rescheduled | Market remains open until the game has been completed; ASP mapping follows the canonical game's updated start |
| Suspended | Not addressed explicitly; the "remain open until completed" clause covers a resumed game |
| Shortened | Not addressed explicitly; the governing body's final statistics decide the winner |
| Cancelled / void | Cancelled with no make-up game: resolves 50-50. No void/refund |
| Tie | Resolves 50-50 |
| Orientation | Each outcome token names its team; HOME/AWAY from the event title "Away vs. Home", verified against the mapped canonical game |
| Token orientation | `clobTokenIds[i]` ↔ `outcomes[i]`; HOME team's token → HOME selection (price = home win probability), AWAY token → AWAY |
| Doubleheader | Distinct markets with distinct `gameStartTime`; mapping uses the 90-minute EXACT window plus team identity |
| Pregame timing rule | `acceptingOrders` continues in-play; only source timestamps strictly before canonical first pitch (120 s grace) are pregame; final valid pregame retained; closed books (HTTP 404) are terminalized without retry |
| Matches ASP canonical complete-game winner | Yes for every game with an official result. Diverges only in no-result cases (cancelled with no make-up, tie) where Polymarket pays 50-50 and ASP records no result |
| Condition check on persisted contracts | 09-04 slate: 16 mapped markets, 16 match the v1 `rules_pattern` (applied as a prefix match). Every description carries a trailing "primary resolution source" paragraph the pattern does not enumerate; it is identical across markets and does not change winner semantics |
| Effect of APPROVE | Every Polymarket MLB moneyline market satisfying all four conditions inherits `SEMANTICS_MATCHED`; pregame quotes become REVIEWED_PREGAME evidence. Non-matching markets stay held. No forecast, decision, position, settlement or model artefact changes |
| Effect of REJECT | All Polymarket MLB moneyline markets labelled `SEMANTICS_MISMATCH`; Polymarket stays research-only until a new version is approved |
| Ambiguity | (1) Trailing resolution-source paragraph outside the pattern (tolerated by prefix matching; a wording change inside the prefix fails closed). (2) Suspended / officially shortened games not explicitly addressed. (3) 50-50 for tie/cancel differs from ASP's no-result handling; ASP does not consume provider settlement |
| **Recommendation** | **RECOMMEND_APPROVE** — all persisted descriptions match, the winner semantics equal ASP's complete-game winner, and every divergence is a no-result case. Not executed |

---

## What a reviewer must do

One decision per provider, through the audited admin route only:

```bash
# Not executed by this task. Example only.
# POST /api/v1/admin/prediction-markets/semantic-templates/decide?provider=KALSHI&template_key=KALSHI_MLB_FULL_GAME_WINNER&status=APPROVED
```

Until a decision is recorded, every quote from both providers remains
`SEMANTICS_REVIEW_REQUIRED` / `PREGAME_UNREVIEWED` on Market Pulse, and no
consensus is computed.
