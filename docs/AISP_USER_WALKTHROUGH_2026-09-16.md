# AISP complete user walkthrough — 16–17 September 2026 (overnight)

Branch `claude/aisp-complete-user-walkthrough-v1`. The walkthrough started from `claude/aisp-my-card-demo-and-player-quotes-v1`:

| Starting point | Value |
|---|---|
| Deployed frontend | `51a265a` |
| Pushed commit | `d7fdb82` |
| Database | `20260917_0044` |

All testing ran against the production-served app on port 8010. No provider or sportsbook call was made.

**Personas.** Each route was read as one or more of these:

| Code | Persona |
|---|---|
| **F** | First-time visitor |
| **R** | Returning viewer |
| **V** | Virtual participant |
| **S** | Show host (Scotty / Red Lou) |

**Viewports.** Every route below was loaded at 1440, 768 and 375 px (Chrome emulation), before and after the fixes were deployed. On each load, an in-page check recorded:

- page-level horizontal overflow, and any element past the viewport that is not inside its own scroller;
- `NaN` / `undefined` / `[object Object]` / error text in the page;
- broken images;
- very short (near-empty) pages.

**Other checks:**
- **Links:** every internal link on the main pages was collected, and one example of each link pattern was opened.
- **Navigation:** back, forward and refresh were exercised on Games → Daily Decisions (All Markets).

## Walkthrough matrix

Severity key: **P1** = wrong or misleading information, a dead end, a server error or a broken mobile layout. **P2** = confusing wording or a local presentation problem. Every defect listed was fixed unless marked **open**.

### Home and Games

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/` Home | F R S | Read order (dark cards first, AISP Today last); edge cards; bankroll; agenda "Next up"; sport selector | Order correct. Next up lists only future milestones (settlements after the last first pitch). |
| `/games` | F R V S | All / Upcoming / Started / Final filters; three sort orders; NFL week selector; previous/next date; Open Game Center; Simulate | Sort by model/market gap falls back to kickoff order when no price is current (by design). |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| Home | P2 | No plain statement of what AISP is. The Best Edges subtitle used internal terms. | Intro line added; subtitle reworded. |
| Games | P2 | "Upcoming" with 0 results said "No MLB game is recorded for this date". | The message now explains that the filter is hiding the listed games. |

### Game Center and simulations

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/games/:id` (MLB final, DET @ TOR) | R S | Game Story; heatmap keys; final overlay; margin/total explorer; ladder; Evidence & audit open/close | Numbers reconcile with the API (TOR 49.9%, fair +101, consensus 55.1%). |
| `/games/:id` (NFL upcoming, DET @ BUF) | R S | Model vs market; tie mass; explorer at −7 (4.0% push), +7.5 and 51 (2.9% push); invalid input | 27.3 / 72.7 two-way, fair ±266. Orientation correct on both sides. |
| `/games/:id/simulation` (MLB) | S | Track switch; next plate appearance; play, 4×, pause; restart | Works. Play Geometry and Simulation Population are honestly unavailable for this forecast. |
| `/games/:id/simulation` (NFL) | S R | Outcome Explorer; most-likely cells; line buttons | Works. Play-level views state that they await evidence. |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| MLB Game Center | P1 | "Best current −130" shown for an 11-hour-old price; stale books badged "BEST"; eyebrow "CURRENT MONEYLINE"; narrative said "current no-vig market". | All labels are now freshness-aware (4-hour window, before first pitch). |
| MLB Game Center | P1 | "Model edge +5.2 pts" appeared under "Home win 49.9%", but it was DET's (the away team's) edge. | The edge now names its team ("DET +5.2 pts"). |
| MLB Game Center | P2 | Final game read "Lifecycle stage: Early", "Final forecast status: Not started". | Now reads "Game final" / "No final-pregame snapshot recorded". |
| Line explorer | P2 | Non-numeric input was silently ignored; the placeholder was always a baseball total. | Inline message added; placeholder uses the quoted line. |
| Evidence & audit | P2 | Raw quality code `SEMANTICS_UNREVIEWED`. | Plain label. |
| Game Story totals | P3, open | Every distribution bar is a tab stop (about 60). | The table view is available. |

### Daily Decisions and My Card

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/decisions` — AISP Decisions | R S | Cards; audit queue; Analyze / Simulate / Shop Books links; research cards | Qualified total −$1.87 matches Home and Experiment. |
| `/decisions?view=markets` | V | Stored quotes; Why?; Parlay Lab; player-prop unavailable note | No quote is current, so every outcome is "Not current". |
| `?preview=player-props` | V S | Fixture banner; six disabled buttons | Never selectable, never labelled "Current". |
| `?view=card` and `&replay=1` | V S | Empty live card; replay entry and exit; add, remove, stake; illustrative return; refresh; storage isolation | Covered by the rehearsal and focused tests. |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| AISP Decisions | P1 | Settled qualified decisions vanished from the main cards; only "Useful passed decisions" remained. | They stay under "Qualified decisions". |
| AISP Decisions | P1 | BAL @ NYM read "Passed · edge below policy" (edge 2.2 pts), but the recorded reason was a stale quote. | The label follows the reason: "price not current" / "not eligible". |
| Research card | P1 | The College Football "Research page" link opened "No forecast is available". | The CFB detail read accepts the admitted AISP game id. |
| Daily Decisions | P2 | The page ignored the global sport selector. | Follows it, with an "All sports" escape. |

### Show Prep, Markets & Crowd and Labs

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/show-board` | S | Previous/next day; Analyze links; Evidence toggles; talking points | Works. |
| `/markets` | R S | Row selection updates movement, by-book and price-movement charts; Moneyline / Spread / Totals / Parlays / RFF tabs | Works. |
| `/market-pulse/:id` | R | Detail page | Works. |
| `/labs` | S R | Date list; game cards; model choice; enter simulator | Works. |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| Show Prep | P1 | The stale NFL price read "captured 10:08 PM"; it was captured on Aug 29. | Capture time now carries its date. |
| Show Prep | P2 | Past slates said "Preview forecast pending / awaiting producer" for finished games. | Now reads "No preview forecast was published". |
| Show Prep | P2 | NFL lean 73% (tie excluded) did not match the 71% three-way figure elsewhere. | Labelled "(no tie)". |
| Markets & Crowd | P2 | The Parlays tab showed Sep 14 tickets (e.g. "DET 6–5 TOR") on the Sep 16 slate with no date. | Tickets show their recorded date. |
| Markets & Crowd | P2 | The global sport selector did not filter the board. | Seeded from the selection; honest empty state with "Show all sports". |
| Markets & Crowd | open (decision for James) | The RFF lens spells out its full profane name. | Not changed; it looks like intentional branding. |
| Market pulse detail | P1 (mobile) | 171 px horizontal scroll at 375 px. | Table wrapped in a scroller. |

### Performance, Research, Shadow Board and Experiment

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/performance` | R | Sport tabs; model select; cohort; view tabs; recent games | Sep 16 rows reconcile with Game Center and final scores. |
| `/research/history` | R | Explorer filters and rows; settled virtual positions | Works. |
| `/shadow-board` | R | Date; sport; engine columns; Open game analysis | Works. |
| `/experiment` | R S | Combined / MLB / NFL / NBA / College Football scopes | Accounts sum to $3,988.38; record 17–27 = 17–26 MLB + 0–1 NFL. |
| `/nba/research`, `/nba/games/:id` | F S | Research card; Game Story | Research-only wording holds. |
| `/college-football/research`, `/college-football/games/:id` | F S | Two forecasts; total scenario; link from Decisions | Research-only wording holds. |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| Performance | P2 | Model selector truncated names ("-duration-v1", "ransition-v1"). | Readable model names. |
| Performance | P1 | The global sport selector changed the URL to NFL but the page stayed MLB. | The component is keyed on the selected sport. |
| Historical Research | P2 | "Settled wagers" wording; the NFL pick read "AWAY". | "Settled virtual positions"; "DEN ML". |
| Historical Research | P2, open | The game explorer query takes about 9 s. | Needs query/index work. |
| Historical Research | P3, open | The NFL row shows its UTC slate date (2026-09-14 for a Sunday-night game). | — |
| Shadow Board | P1 | Every title was a pair of UUIDs; NFL teams used retired franchise names. | Names resolved server-side, identities season-aware. |
| Shadow Board | P1 (mobile) | 378 px horizontal scroll at 375 px. | Grid columns can shrink. |
| Shadow Board | P2 | Ignored the global sport selector. | Follows later changes. |
| Research pages | P2 | The header kept the previously chosen sport (e.g. "NFL" on the NBA page). | Sport implied by the path. |
| Research pages | P2 | Raw blocker codes in consumer text. | Moved to a tooltip. |
| CFB research | P2 | "No admitted College Football schedule" while one game is admitted. | Reworded. |

### Other routes

| Route | Persona | Interactions tested | Result |
|---|---|---|---|
| `/methodology` | F | Section anchors | Works. |
| `/account`, `/login` | F | Empty submit (browser validation); Create account form. No credentials were entered. | Works. |
| `/admin` | — | Not signed in | "Administrator access required"; authentication unchanged. |
| Community | F | Disabled "Future" item | Correct. |
| `?sport=WNBA` | F | Planned sport, reachable only by URL | Now shows a "planned" notice. |

| Page | Severity | Defect | Fix |
|---|---|---|---|
| Methodology | P1 (tablet) | 79 px horizontal scroll at 768 px. | Auto-fit cards. |
| `?sport=WNBA` | P2 | Showed MLB content with no explanation. | Planned notice added. |

### Platform-wide findings

| Scope | Severity | Finding | Status |
|---|---|---|---|
| API | P1 | `/api/v1/scoreboard/coverage` returned 500 (a key removed in a budget refactor). | Fixed, with a regression test. |
| API 404s | — | Geometry, NFL play-theater and play-population 404s are expected "evidence not available" reads, and the UI explains each. The asset 404s were old chunk names from before the rebuild. | Not defects. |
| Browser console | — | The only console errors were those request failures and the pane's own X-Frame-Options notice. | Not defects. |
| Client tests | P3, open | Four `ShadowBoardPage` component tests time out on base and on this branch (pre-existing). | Open. |

## Deployment

| Step | Detail |
|---|---|
| Backend | Restarted once, 2026-09-16 23:28 ET, through *ASP Backend 8010*: pid 44904 → **45172**, server at `c109fdf`. No decision window was open; the next opens 2026-09-17 12:05 ET. |
| Frontend | Built from a clean tree at `c109fdf`, then `2300abd` (frontend-only follow-up; no restart needed). |
| Rollback copies | `.runtime/frontend-rollback/dist-51a265a` (pre-walkthrough) and `dist-c109fdf`. |
| Database | Unchanged at `20260917_0044`; no migration. |

## Protected-state receipt

**Method.** Each protected table got a row count plus an MD5 over its rows, read-only. The exchange directory got a
SHA-256 of its sorted file listing (path, size, mtime).

| | Before (22:18 ET) | After (23:49 ET) |
|---|---|---|
| Decisions, positions, settlements, experiment accounts / fundings / memberships, shadow decisions, bankrolls, forecasts, champion authorities, parlay tickets, odds snapshots | fingerprinted | **identical** |
| `provider_request_ledger` | 1,480 rows | 1,484 rows: four zero-cost `sgo_account_usage` rows (quota_cost 0), made by the scheduler's routine usage check, not by this work |
| `player_prop_quotes` | 0 | 0 |
| Settings | `PLAYER_PROP_LIVE_ENABLED` false, Odds API floor 10, reserve 150 | unchanged |
| Exchange listing SHA-256 | `6e3f21d1…729d62f` (5,949 files) | identical |
| Alembic | `20260917_0044` | unchanged |

- **Provider calls by this work:** none.
- **Odds API credits:** 0.
- **SportsGameOdds objects:** 0.

## Tests against the unchanged base

| Suite | Result |
|---|---|
| Backend | 2,914 tests on the branch versus 2,912 on base; 23 failures on both, and **no new failure**. The two tests added after that run (scoreboard coverage) pass. |
| Client | 993 tests versus 982; 123 failures versus 125 on base, and **no new failure**. The failures are pre-existing (environment, timing and typography assertions). |
| Typecheck | `tsc -b` clean. |
