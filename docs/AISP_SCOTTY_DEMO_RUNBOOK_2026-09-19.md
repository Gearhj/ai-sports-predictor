# Scotty demo runbook — weekend of 19–20 September 2026

Private, local demonstration of AISP on the supervised backend. The rehearsal
was run on 2026-09-16 against the production-served frontend on port 8010
(not Vite).

## Release identity

| | |
|---|---|
| Release branch | `claude/aisp-scotty-demo-release-v1` |
| Backend running | pid **43956**, restarted 2026-09-16 13:09 ET by the *ASP Backend 8010* task, from original commit `49409ee` (GitHub `7f79360`, branch `claude/aisp-mlb-league-safety-v1`). This adds league isolation: MLB jobs and slates select and accept only MLB games. The client is unchanged since `7861a53`. |
| Frontend served | `client/dist` built at original commit `7861a53` (check `/frontendz`, field `build.commit`) |
| Model Studio release | `demo-releases-v1` logical address `f0e87d7a…8889a`, manifest SHA-256 `05250af2…56fc8`, verified and reconciled 2026-09-16. The consumer receipt is under `asp-model-exchange/demo-releases-v1/consumer-receipts/`. |
| Database | PostgreSQL 16, `asp`, Alembic head `20260915_0043` |
| Release rehearsal | 2026-09-16, 11:15–11:25 ET (full); 12:00–12:15 ET (affected pages after the release import) |
| Published SHAs | Original SHAs above are the ones that ran. Their GitHub equivalents are listed in `docs/REPOSITORY_AND_BACKUP.md`. |

**Before the demo:**
- Open `http://127.0.0.1:8010/frontendz`. The commit must be `7861a53…` or later from this branch.
- Open `http://127.0.0.1:8010/readyz`. It must read `ready / reachable / running`.

## Demo path

Use a desktop browser at `http://127.0.0.1:8010`. Everything is research and a
virtual $1,000-per-sport paper experiment. There is no real money.

| # | URL | What it demonstrates | Expected honest states |
|---|---|---|---|
| 1 | `/` | Today's Edge Board, the combined $4,000 virtual bankroll, and the agenda timeline across MLB/NFL/NBA/CFB | Cards before about T-60 read "Forecast expected by …". Markets show their own capture time. |
| 2 | `/show-board` (Saturday: `/show-board?date=2026-09-19`) | Show Prep: the next day's rundown by sport, projected scores for NFL and CFB, and talking points | Day-before collection is **off**, so moneylines read "No preview price held" or "Stale only · captured …". "Market not posted" is never shown. For Saturday, Coastal Carolina @ Delaware shows "No Show Preview" plus its **research forecast** (Delaware 73%, research only), and **Analyze** opens its CFB research page. |
| 3a | `/games/<mlb-id>?sport=MLB` | MLB analysis: V18 projected score, win probability, fair price, sportsbook table with observation times, and model-vs-market | Before V18 publishes, the page shows **Forecast pending** with the reason and no numbers. Gen 1 appears only as a labelled baseline comparison. |
| 3b | `/games/<mlb-id>/simulation?sport=MLB` | The V18 Monte Carlo Diamond Theater, which opens by default. Try Broadcast Replay, Play Geometry and Simulation Population, and the playback speed controls. | Games without a saved V18 simulation fall back to the Gen 1 distribution, labelled as the baseline. |
| 4a | `/games/<nfl-id>?sport=NFL` | NFL M1: expected score, home/away/tie probabilities, snapshot provenance, and market comparison | Outside the price window, the quote is "Stored reference quote — not a current price" and no edge is computed. |
| 4b | `/games/14a64986-3d92-48af-ba69-15731e936d54/simulation?sport=NFL` | NFL Simulation Lab (DEN @ KC, Week 1): Simulation Population, Watch one game, Outcome Explorer and Scenario Comparison | Week 2 games have no play-level evidence yet. For them the lab opens on the Outcome Explorer. |
| 5 | `/decisions` | Daily Decisions: forecast → price → evaluated → qualified → positioned, with plain reasons | NBA: "Season not started · research example". CFB: "Market comparison blocked". NFL: "Stale reference only" until its collection window opens. |
| 6 | `/performance` (tabs MLB / NFL / NBA / College Football) | Champion identity and prospective scores: MLB V18 (n=85 as of 16 Sep) and NFL M1 (n=16) | NBA and CFB have no scored prospective games. |
| 7 | `/experiment` | Immutable paper results: four virtual accounts, open positions, settlements with entry price, book and provider | Positions exist only where a forecast, eligible policy and fresh price qualified inside a window. |

Supporting research pages:
- `/nba/research`: the BOS @ DET early research example, with its season-not-started and price-blocker strip.
- `/college-football/research`: both imported CFB research forecasts.
  - Syracuse @ Pittsburgh (Thu 17 Sep) has an exact paper-eligibility record but no canonical game binding.
  - **Coastal Carolina @ Delaware** (Sat 19 Sep, 11:30 ET) is bound to AISP's schedule by CFBD game 401869940. It is research only: it has no eligibility record.
  - Each card shows the three channels separately:
    - joint simulation (primary, drives the distributions);
    - calibrated Ridge-margin winner probability;
    - Elo benchmark.
  - The expected score is labelled as the Ridge point channel.

## Best games to show

- **MLB, live on the day.** Open `/decisions`. From about 60 minutes before each first pitch, a game shows a V18 forecast. Pick one showing **Qualified** or **Position recorded** if any; otherwise any game with "Evaluated".
  - Reliable fallback with a full story: NYY @ MIN, 15 Sep: `/games/36c6b673-852d-47d9-bdb0-e4545b5dc580?sport=MLB`. It has a V18 forecast (MIN 44.1%), a qualified paper position (MIN +150 at PointsBet via SportsGameOdds, stake $2.49, LOSS) and a V18 simulation.
- **NFL, Sunday 20 Sep.** Week 2 games have M1 forecasts, for example IND @ KC: `/games/18562ce4-a5a0-46ce-855d-67bec0070213?sport=NFL`. Current prices exist only after each game's collection window opens, about 75 minutes before kickoff.
  - Lab: DEN @ KC, as in step 4b.
- **College Football, Saturday 19 Sep.** Coastal Carolina @ Delaware, 11:30 ET: `/college-football/games/401869940`. It is the upcoming weekend research example.
  - **Figures.** Joint Delaware 73.15%, Ridge 72.40%, Elo 55.82%. Ridge expected points are Coastal Carolina 22.77, Delaware 32.95.
  - **Status.** It is a prospective research snapshot from 14 Sep inputs, not a final pregame forecast. It has no assessment and no paper position.
  - **Syracuse @ Pittsburgh.** Thu 17 Sep; a past game by the weekend. Show it only as the example that carries an exact paper-eligibility record.
- **NBA.** BOS @ DET (captured for 20 Oct) is a research example only.

## Expected unavailable states (say these plainly)

- **CFB market comparison is blocked.**
  - **Coastal Carolina @ Delaware** (`SGO_LEAGUE_IDENTITY_UNVERIFIED`): the game is now bound to AISP's schedule. There is still no verified SportsGameOdds `NCAAF` access, no reviewed provider team mapping and no CFB quote-ingestion path.
  - **Syracuse @ Pittsburgh** (`…;NO_CANONICAL_GAME_IDENTITY`): not bound. It has an exact paper-eligibility record, so it is deliberately left as it was.
  - No object was spent (see the dry run below).
- **The weekend CFB forecast is research only.** It has no paper-eligibility record, so it cannot be assessed or positioned, and it does not inherit Syracuse–Pittsburgh's record.
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
| Matching after binding | A returned `NCAAF` event for Coastal Carolina @ Delaware at 15:30Z would have exactly **one** candidate AISP game (checked on 16 Sep). But AISP has no reviewed SportsGameOdds→CFBD team mapping and no CFB quote-ingestion path. NFL deliberately never matches on names and a date. |
| Decision value today | **None.** The weekend forecast has no paper-eligibility record, so no assessment or position could follow even with a price. At most, a price could support a research-only model-vs-market display, and only after a reviewed CFB ingestion path exists. |
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
5. **Backend rollback.** Check out `b454697`'s `server/` in the original checkout (`git checkout b454697 -- server`) and restart through the task. That was the backend before league isolation. The earlier fallback is `6f4f01f`.

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

- **Model Studio release reconciled (16 Sep).** The release declares objects and grants no authority. Reconciliation found:
  - **NFL:** 16 Week 2 CURRENT forecasts and 16 eligibility supplements, all reused exactly.
  - **NBA:** both packages reused (point/Elo `85e3d741`, joint `00cd4990`). Only the paired-residual baseline is the selected arm; the close-score arm is retained, not promoted.
  - **CFB:** Syracuse–Pittsburgh reused; Coastal Carolina–Delaware newly imported and bound.
  - **MLB:** the champion declaration matches AISP's active V18 authority. The release's MIA–AZ object is a historical 15 Sep example and was not imported as a weekend forecast.
- **MLB weekend forecasts come from the natural V18 owner**, about 60 minutes before each game. Until one arrives the page correctly reads "Forecast pending".
- **MLB decision-time quote age.** MLB paper positions were judged fresh at decision time under the existing policy, even where the quote was up to about 4 hours old. Entry quotes show their own capture time.
- **Pre-existing test failures remain.** They are identical before and after this release:
  - backend: 10 in the focused set, plus 3 others;
  - client: 125, of which two are flaky.
