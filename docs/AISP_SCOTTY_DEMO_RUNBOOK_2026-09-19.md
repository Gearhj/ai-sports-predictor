# Scotty demo runbook — weekend of 19–20 September 2026

Private, local demonstration of AISP on the supervised backend. The rehearsal
was run on 2026-09-16 against the production-served frontend on port 8010
(not Vite).

## Release identity

| | |
|---|---|
| Release branch | `claude/aisp-scotty-demo-release-v1` |
| Backend running | pid **36700**, restarted 2026-09-16 11:11 ET by the *ASP Backend 8010* task, from original commit `5a3872a` (the later commits are client and docs only) |
| Frontend served | `client/dist` built at original commit `fb7090a` (check `/frontendz`, field `build.commit`) |
| Database | PostgreSQL 16, `asp`, Alembic head `20260915_0043` |
| Release rehearsal | 2026-09-16, 11:15–11:25 ET |
| Published SHAs | Original SHAs above are the ones that ran. Their GitHub equivalents are listed in `docs/REPOSITORY_AND_BACKUP.md`. |

**Before the demo:**
- Open `http://127.0.0.1:8010/frontendz`. The commit must be `fb7090a…` or later from this branch.
- Open `http://127.0.0.1:8010/readyz`. It must read `ready / reachable / running`.

## Demo path

Use a desktop browser at `http://127.0.0.1:8010`. Everything is research and a
virtual $1,000-per-sport paper experiment. There is no real money.

| # | URL | What it demonstrates | Expected honest states |
|---|---|---|---|
| 1 | `/` | Today's Edge Board, the combined $4,000 virtual bankroll, and the agenda timeline across MLB/NFL/NBA/CFB | Cards before about T-60 read "Forecast expected by …". Markets show their own capture time. |
| 2 | `/show-board` | Show Prep: the next day's rundown by sport, projected scores for NFL and CFB, and talking points | Day-before collection is **off**, so moneylines read "No preview price held" or "Stale only · captured …". CFB reads "Forecast held · not in AISP schedule, so no price can be bound". "Market not posted" is never shown. |
| 3a | `/games/<mlb-id>?sport=MLB` | MLB analysis: V18 projected score, win probability, fair price, sportsbook table with observation times, and model-vs-market | Before V18 publishes, the page shows **Forecast pending** with the reason and no numbers. Gen 1 appears only as a labelled baseline comparison. |
| 3b | `/games/<mlb-id>/simulation?sport=MLB` | The V18 Monte Carlo Diamond Theater, which opens by default. Try Broadcast Replay, Play Geometry and Simulation Population, and the playback speed controls. | Games without a saved V18 simulation fall back to the Gen 1 distribution, labelled as the baseline. |
| 4a | `/games/<nfl-id>?sport=NFL` | NFL M1: expected score, home/away/tie probabilities, snapshot provenance, and market comparison | Outside the price window, the quote is "Stored reference quote — not a current price" and no edge is computed. |
| 4b | `/games/14a64986-3d92-48af-ba69-15731e936d54/simulation?sport=NFL` | NFL Simulation Lab (DEN @ KC, Week 1): Simulation Population, Watch one game, Outcome Explorer and Scenario Comparison | Week 2 games have no play-level evidence yet. For them the lab opens on the Outcome Explorer. |
| 5 | `/decisions` | Daily Decisions: forecast → price → evaluated → qualified → positioned, with plain reasons | NBA: "Season not started · research example". CFB: "Market comparison blocked". NFL: "Stale reference only" until its collection window opens. |
| 6 | `/performance` (tabs MLB / NFL / NBA / College Football) | Champion identity and prospective scores: MLB V18 (n=85 as of 16 Sep) and NFL M1 (n=16) | NBA and CFB have no scored prospective games. |
| 7 | `/experiment` | Immutable paper results: four virtual accounts, open positions, settlements with entry price, book and provider | Positions exist only where a forecast, eligible policy and fresh price qualified inside a window. |

Supporting research pages:
- `/nba/research`: the BOS @ DET early research example, with its season-not-started and price-blocker strip.
- `/college-football/research`: the Syracuse @ Pittsburgh forecast, with its market-blocker strip.

## Best games to show

- **MLB, live on the day.** Open `/decisions`. From about 60 minutes before each first pitch, a game shows a V18 forecast. Pick one showing **Qualified** or **Position recorded** if any; otherwise any game with "Evaluated".
  - Reliable fallback with a full story: NYY @ MIN, 15 Sep: `/games/36c6b673-852d-47d9-bdb0-e4545b5dc580?sport=MLB`. It has a V18 forecast (MIN 44.1%), a qualified paper position (MIN +150 at PointsBet via SportsGameOdds, stake $2.49, LOSS) and a V18 simulation.
- **NFL, Sunday 20 Sep.** Week 2 games have M1 forecasts, for example IND @ KC: `/games/18562ce4-a5a0-46ce-855d-67bec0070213?sport=NFL`. Current prices exist only after each game's collection window opens, about 75 minutes before kickoff.
  - Lab: DEN @ KC, as in step 4b.
- **College Football.** Syracuse @ Pittsburgh (Thu 17 Sep) is the only imported CFB forecast. By the weekend it is a past game. Show it as the retained champion example with its blocker.
- **NBA.** BOS @ DET (captured for 20 Oct) is a research example only.

## Expected unavailable states (say these plainly)

- **CFB market comparison is blocked** (`SGO_LEAGUE_IDENTITY_UNVERIFIED;NO_CANONICAL_GAME_IDENTITY`).
  - SportsGameOdds' public docs list College Football as `NCAAF`, but AISP has no admitted CFB schedule, so a price could not be bound to a game.
  - No object was spent (see the dry run below).
- **NBA has no price and no live season.** The regular season has not started. BOS @ DET is not live or final.
- **Show Prep holds no day-before prices.** Day-before collection is disabled by policy. Its absence is AISP's capture state, not a statement about sportsbooks.
- **MLB before V18 publishes shows "Forecast pending".** No Gen 1 number is substituted.
- **WNBA, NHL and UFC are "Planned"** and disabled in the sport selector.

## CFB paid-data dry run (not executed)

| | |
|---|---|
| Identity check | `GET https://api.sportsgameodds.com/v2/leagues?leagueID=NCAAF`, 1 object (SportsGameOdds bills every response at least 1 object) |
| Price capture | `GET /v2/events?leagueID=NCAAF&startsAfter=<game day 00:00Z>&startsBefore=<+1 day>&oddsAvailable=true&limit=1`, at most 1 object per returned event (`limit=1`) |
| Maximum charge | 2 objects |
| Decision value today | **None.** Without an admitted CFB schedule, a returned quote cannot be bound to a canonical game, so no comparison, assessment or position could follow. Admit a CFB schedule first. |
| Status | **Not requested.** It needs James's explicit approval. |

## If a page fails during the demo

1. **Blank page or spinner.** Refresh once. The client cache is non-authoritative.
2. **API errors ("unavailable").** Check `http://127.0.0.1:8010/readyz`.
   - If it is slow or failing, restart **only** through the supervisor. From an elevated or normal PowerShell:

     ```powershell
     Stop-ScheduledTask -TaskName 'ASP Backend 8010'; Start-Sleep 3; Start-ScheduledTask -TaskName 'ASP Backend 8010'
     ```

   - Wait for a new listener pid on 8010 (about 20–60 s) before trusting `/readyz`. Never start uvicorn or Vite by hand.
3. **NFL lab blank.** Open the Outcome Explorer directly with `…/simulation?sport=NFL&view=explorer`.
4. **Frontend rollback.** The previous build is in `.runtime/frontend-rollback/dist-a3223ec`. Copy it over `client/dist`; no restart is needed.
5. **Backend rollback.** Check out `6f4f01f`'s `server/` in the original checkout and restart through the task. That was the previously deployed backend.

## Fixed for this release

- **P0: MLB game page showed two models at once.** V18's probability appeared beside Gen 1's projected score, fair price, components, drivers and simulation.
- **P0: finished games listed twice on the decision board.** One row held the final evaluation, the other "not yet evaluated".
- **P0: Diamond Simulator opened on Gen 1 for V18 games,** and called it the official forecast.
- **P0: NFL Simulation Lab crashed on a first, uncached visit** (React #310).
- **P0: missing capture labelled as a sportsbook fact.** Show Prep said "Market not posted" where AISP simply held no capture.
- **P1: NFL Simulation Lab opened on an empty population panel** for games without play-level evidence.
- **P1: wrong forecast state on Daily Decisions.** NFL/NBA/CFB rows read "Forecast preparing" although forecasts were imported, and did not name the price blocker.
- **P1: NBA and CFB pages denied the paper account and policy** that the Experiment page shows.
- **P1: final game shown as awaiting a result.** A final game's research card read "no final outcome recorded yet".
- **P1: MLB pending state gave the wrong reason and a raw timestamp.** It said "inputs are incomplete" instead of the actual reason, and showed a raw ISO timestamp.

## Known disclosures

- **No release manifest.** Codex's four-sport release manifest had not been published by 16 Sep. The demo uses the champion and forecast imports AISP had already verified: MLB V18 champion authority, NFL M1 bundles, and the NBA/CFB research packages with their paper-eligibility supplements.
- **MLB decision-time quote age.** MLB paper positions were judged fresh at decision time under the existing policy, even where the quote was up to about 4 hours old. Entry quotes show their own capture time.
- **Pre-existing test failures remain.** 10 in the focused backend set and 125 in the client suite, identical before and after this release.
