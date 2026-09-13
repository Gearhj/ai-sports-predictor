# Status colours, understandable markets, and baseball Labs navigation

**As of** 2026-09-12 17:25Z · **Branch** `claude/aisp-v18-and-price-coverage-v1`
**Commit** `066cbc7` · Deployed through the supervisor path.

The screenshot game resolves to **COL @ DET, 2026-09-12 13:10 EDT**,
canonical `b163682f-fbad-4ee0-86e9-771bc0449208`, source event `824224`,
status **FINAL, DET 11 – COL 7**. It is not the September 11 duration pilot.

---

## 1. Games & Forecasts — one status mapping, and the side named

One table in `services/statusStyle.ts` now drives cards, rows and legends.
Previously each surface ran its own string test, so the same status could look
different in two places and an unrecognised one fell through unstyled.

| Status | Fill | Text | Border | Verified live |
|---|---|---|---|---|
| NO BET | `#eceff3` | `#414b57` | `#c3ccd7` | yes — HOU, MIL, STL |
| WATCH | `#fdf4d8` | `#6d4c13` | `#e3c469` | yes — MIA |
| STANDARD EDGE | `#e8f5ec` | `#1f6437` | `#8cc7a2` | fixture (no upcoming game held it) |
| STRONG EDGE | `#d8f0e0` | `#14532d` | `#3f9a68` | fixture (no upcoming game held it) |

STANDARD EDGE is a quieter green, **not** relabelled as STRONG. An unrecognised
status stays neutral rather than being promoted into an edge colour. The pill
has a fixed min-width and wraps; measured at 1512×950 the decision column ends
at 1213px and the Analysis button starts at 1231px — **no collision**, and none
at 1024×820 either, with no horizontal page scroll.

### The selection-side question, answered

It is **not** a calculation defect. From the live payload:

```
BAL @ TOR   opportunity = STANDARD EDGE   edge_side = away   model_edge = +5.83
            row rendered: -5.3 pp        (home-side model minus market)
```

The band describes whichever side the model prefers; the number was always the
home side's. Both were correct and neither said whose it was. Rows now read
**`BAL +5.8 pp vs market`**, using the backend's own edge signed against its own
side. Verified across the slate: `SF +8.4` (STRONG EDGE), `CHC +2.5` (WATCH),
`COL +0.7` (NO BET).

Market disagreement and a persisted decision are also kept apart. For COL @ DET
every recorded decision is **NO_BET** — under the V18 era with
`TIER_D_INELIGIBLE: Lineup evidence MISSING_BLOCKING`, and under Gen 1 with
"no position is permitted" — while the card band is a separate reading.

### Started games are records, not invitations

8 of 15 games on the board were LIVE or FINAL and still wore a pregame band.
They now render dashed and muted with **"last pregame reading"** beneath.
Calculations and thresholds are untouched.

---

## 2. Markets & Crowd — the meaning before expansion

The collapsed row carried an unlabelled miniature plot. It now reads, from the
live page:

```
COL at DET · final
DET WIN
MODEL 58.9%   SPORTSBOOK CONSENSUS 59.6%   MODEL − CONSENSUS -0.7 pp
BEST RECORDED DET WIN PRICE -159 · Pointsbet
SPORTSBOOK PRICE AGE  Pregame price · last recorded 4h ago
```

Every value names its team or contract. A missing comparison renders as an
explicit unavailable state with the reason (for run lines and totals, that both
sides must exist at the same line). The model is labelled **Gen 1 model**,
because it is — see §4.

### The chart, named for what it computes

Inspected before naming. The grey band is the **minimum and maximum implied
probability across individual books, each carrying its own margin** — already
labelled "Individual books (margin included)", with the no-vig consensus drawn
as a separate point. It is not a box plot, quartile range, confidence interval
or prediction interval, and nothing was renamed. It moved into the drawer under
a titled heading with its legend and the existing caption.

### Ambiguity 1 — 59.5% beside 40.0% / 41.0%

Traced rather than guessed. `midpoint` is oriented to the home team by the
backend; `best_bid`/`best_ask` are the venue's own contract, and venues list
different sides:

```
POLYMARKET  midpoint 0.595   bid/ask 0.40 / 0.41   -> raw book is the COL contract
KALSHI      midpoint 0.595   bid/ask 0.59 / 0.60   -> raw book is the DET contract
```

Both were shown under one "DET win" heading. The table now has a **Quoted side**
column resolved from the numbers, and the bid/ask are **withheld with a reason**
when the side cannot be established — near even money, or a book matching
neither side. No price is ever complemented to make the two agree.

(Between my first and second read the snapshot moved and Kalshi's book was also
on COL; the resolver labelled both correctly. The mechanism, not one snapshot,
is the point.)

### Ambiguity 2 — "FRESH 4h ago" on a live game

Three facts were collapsed into one green badge. From the payload:
`in_play: false`, `observation_kind: FINAL_PREGAME`, `seconds_before_start: 475`,
`freshness_seconds: 13692` — on a game that is **FINAL**. Game status, the
observation kind and the recording age are now separate, and the page says so
explicitly: a started game shows the last price recorded *before* first pitch,
and the age is when the price was recorded, not when the page refreshed.

"SGO FALLBACK" moved out of the collapsed row into the drawer's source detail;
the row keeps book name, price and age. The provenance sentence is behind a
`<details>` summary. The prediction-market table scrolls instead of clipping,
and missing values render as readable unavailable states. "Reload stored
evidence" issues a GET and makes no provider call.

---

## 3. Interactive Labs — modes discoverable, playback usable

### Modes

All three are now visible with plain descriptions. Unavailable ones are
**disabled with their reason** instead of being hidden — a hidden mode is
indistinguishable from one that never existed:

```
Broadcast Replay          [enabled]   Replays one saved simulated game plate
                                      appearance by plate appearance.
Play Geometry             [disabled]  Unavailable — this forecast's producer
                                      published no play-geometry supplement
                                      (available_views.geometry = false).
Simulation Population     [disabled]  Unavailable — … no population supplement
                                      (available_views.population = false).
```

### Playback verified, not assumed

The artifact holds **96 plate appearances across 11 innings**. Demonstrated on
the live page:

| Check | Result |
|---|---|
| Play at 4× | PA 1 → 14, Top 1 → Bottom 1 → Top 2 → Bottom 2 |
| Inning jump | → PA 44 Top 7 → PA 79 Top 11 |
| Scrub to end | **PA 96 of 96, Bottom 11, FINAL** |
| Feed follows | "96 · Ball in play — out — side retired", "95 · …One run scores. 12–7 after the play." |
| Scorebug | COL 12 · DET 7 · Bottom of inning 11 · 3 outs · FINAL · batting #1 · team bullpen |

Batter and pitcher **identities are not shown**, because the artifact does not
supply them — only `battingOrderPosition` and `pitcherState`.

**Controls were genuinely broken and are fixed.** At 1512×950 the whole
transport sat at y≈1029–1158, entirely below the fold: a reader saw PA 1 and a
Play button and never the step, speed or inning controls. `.theater` sets
`overflow: hidden` (to clip its radius), which also made it the sticky
containing block, so sticky alone could not help. The ballpark is now capped
outside theater mode and the transport sticks. Measured after: previous PA,
play/pause, next PA, restart, speed and inning jump all **visible**.

### Three scores kept apart

| | |
|---|---|
| One representative path | COL 12 – DET 7 — "REPRESENTATIVE PATH … one simulated game path, not a prediction of the exact play sequence" |
| Expected final | DET 4.81 – COL 4.08 — "EXPECTED FINAL" |
| Actual result | **not shown** — `settlement_state: PENDING`, "no persisted final outcome yet" |

The game row does hold 11–7, but AISP has no *governed* final for it yet, so
none is displayed. Showing the raw row would bypass the settlement contract.

### Stale authority wording, corrected

"Official Gen 1" was fixed wording from before the funded era moved. The era
endpoint reports V18 **ACTIVE** since 2026-09-12 12:00 EDT with
`gen1_fallback_allowed: false`. The tabs now read "Gen 1 baseline · Final-score
distribution" and "V18 · Saved game simulation", and the "LIVE DATA" banner —
which meant only that the API answered — is replaced by:

```
ON SCREEN                        CONTROLS FUNDED DECISIONS NOW
V18 whole-game challenger        MLB_V18_HGB_PA
MLB_V18_WHOLE_GAME               ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1
mlb-v18-daily-2123…5c81f4        effective Sep 12, 12:00 PM EDT
Evidence cutoff Sep 12, 10:49 AM EDT
Research shadow — no wagering authority.

A saved simulation is not live game coverage.
```

---

## 4. Forecast identity across the three surfaces

The screenshots differ **on purpose**, and that is now explicit:

| Surface | Model | Home win |
|---|---|---|
| Games & Forecasts, Markets & Crowd | Gen 1 official (`asp_mlb_ensemble 2.0.0-gate2-08cca634c81b`) | **58.89%** |
| Diamond Simulator, V18 track | `MLB_V18_WHOLE_GAME` (`mlb-v18-daily-2123…`) | **60.24%** |

Markets now labels its column "Gen 1 model" and states that the V18 forecast in
the simulator is a different model and a different number. No Gen 1 score is
combined with a V18 probability or path anywhere.

---

## 5. Producer lead — still true, with a Codex handoff

The September 11 lead **remains true for this artifact**, verified rather than
assumed:

```
shadow_forecasts.lineage_detail.availableViews
  = { "broadcast": true, "geometry": false, "population": false }
research_supplements  = 266 rows, all NFL (PLAY_LEVEL_SIMULATION 217,
                        DRIVE_SIMULATION 32, JOINT_SCORE_SIMULATION 16,
                        MODEL_BENCHMARK_STUDY 1) — none bound to this forecast
```

Nothing was synthesised from score marginals, no fixture was written to
production, and the modes are described as unavailable, not restored.

### For Codex

- **Game:** `b163682f-fbad-4ee0-86e9-771bc0449208` (COL @ DET, source `824224`,
  slate 2026-09-12)
- **Forecast:** `mlb-v18-daily-21239787e7773ffa160e98251cd999d427f1b9d99875f7f29b07d68f425c81f4`,
  engine `MLB_V18_WHOLE_GAME`, parent bundle
  `mlb-v18-whole-game-a6e7e99122f1b60701a4728b`, path digest
  `ac1f1d84b380736d37d1adbb4f67af4fc719cbf84fc0cd31b3bbbd405c1ee2dc`
- **Failed boundary:** the current-state exporter still emits
  `V18_EXCHANGE_VIEW_SUPPLEMENT_EXPORT_NOT_SUPPORTED`; `availableViews` is
  published with geometry and population `false`.
- **Expected supplements:** a `PLAY_LEVEL_SIMULATION`-shaped supplement for
  geometry and a population supplement, bound to the forecast id above, through
  the existing `ResearchSupplement` contract (`kind`, `contract_version`,
  `forecast_id`, `shadow_forecast_id`, `source_cutoff_utc`, `published_utc`).
- **Bounded follow-up:** reuse
  `server/sports_current_statcast/mlb_v18_daily_views.py` in the current-state
  generation/export path and extend `mlb_v18_exchange.py` through the existing
  contract. Preserve the original forecast identity and numbers; bind each
  supplement and its own creation time immutably. Visualization integration
  only — no retraining, no change to forecast authority.

AISP needs no change to render them: the mode buttons, routes and renderers
already exist and will enable themselves when `availableViews` reports true.

---

## 6. Verification

**29 focused tests**, on changed behaviour rather than colour constants:
side naming for away/home/no-published-side/no-price; started-game handling;
that STANDARD EDGE is never promoted to STRONG and an unknown status stays
neutral; quoted-side resolution for away, home, no-match, near-even-money and
missing inputs; that a price is never complemented; and quote-kind wording.

**No regressions.** Full client suite **43 failures — identical to the session
baseline**, zero new. Three existing tests legitimately encoded behaviour this
task changed and were updated: the two "Official Gen 1" headings, "exposes only
artifact-supported V18 views" (now asserts the disabled button *and* its
reason), and the SGO-fallback label (now asserted in the drawer).

I also fixed a real guard I had broken: `phase15` requires no font declaration
below 11px, and my new CSS used 10 and 10.5px. All of this session's
declarations are now at the 11px floor, leaving exactly the 5 pre-existing
violations the guard already had.

**Browser**, authenticated, at 1512×950 and 1024×820: status colours on real
states, the rewritten Markets row, the orientation column, multi-inning playback
to PA 96 FINAL, and every mode's availability. No provider call and no model run
on navigation; cached return navigation still instant.

**Deployment.** `vite build` succeeds. Backend restarted through the Windows
Scheduled Task "ASP Backend 8010"; the supervisor reclaimed the stale owner.
After: **one listener on 8010**, one scheduler claim
(`c63890b5042b471cb80053255a777c6d`), task Running. MLB and NFL jobs all
continued — `freeze-shadow-experiment`, `collect-odds-checkpoint`,
`open-model-era-at-boundary`, `evaluate-mlb-v18-research-forecasts`,
`freeze-nfl-final-pregame`, `import-nfl-shadow-forecast-bundle`,
`evaluate-nfl-research-forecasts`. No policy, threshold, account or provider
budget was touched.

**Commit** `066cbc7`, 15 files. `styles.css` and `ui.tsx` also carry this
session's earlier uncommitted page-performance and season-account work: the
11px typography fix necessarily touched those blocks, so splitting them out
would have left the tree failing a guard it now passes. No unrelated refactor
is included.

---

## 7. Remaining limitations

1. **STANDARD EDGE and STRONG EDGE greens are verified by fixture**, not on a
   live upcoming game — no upcoming game held either band at snapshot time. The
   fixtures are tests and are not in production.
2. **Play Geometry and Population stay unavailable** until Codex publishes the
   supplements above. AISP is ready.
3. **No actual final is shown in the simulator** because AISP holds no governed
   final for this game yet (`settlement_state: PENDING`). When one is persisted
   it will appear through the existing settlement path.
4. **`MarketPulseRow` carries no model version or forecast timestamp**, so the
   Markets row names the model ("Gen 1 model") but cannot show its version and
   generation time compactly without a backend field. The game analysis page,
   linked from the drawer, has both.
