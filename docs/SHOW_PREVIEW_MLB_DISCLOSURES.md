# What a genuine MLB Show Preview forecast must be shown with

Status: **implemented and inactive, 2026-09-15.** No genuine MLB SHOW_PREVIEW
package exists, so every MLB row on the Show Board still reads *"Preview
forecast awaiting producer."* The rules below are in the code now so that the
first package to arrive cannot be displayed without them.

## Why this file exists

Model Studio implemented `MLB_TEAM_LOGISTIC_C1_POISSON_A1_SHOW_PREVIEW_V1`
(commits `21a91155`, `b9c31ab8`) and published package
`3f445ec0648729d905324c74132bae1e2809a110667cdee654a5b1a86d1fda33`. That package
is a **handoff**: schema `ASP_SHOW_PREVIEW_HANDOFF_PACKAGE_V1`,
`forecastPackagesPublished: 0`, `consumerActivation: false`, and no
`forecast.json` anywhere inside it. It says the model exists and is waiting on
certified current inputs. It is not a forecast and must never be imported as
one.

The model it describes produces numbers that are easy to read as more than they
are. It has sixteen team score/date features, a logistic winner head and two
**independently estimated** Poisson score heads. Its expected runs are not a
joint distribution, and its `draw_count` is zero with null distributions. Put a
"projected total" beside a sportsbook total with no qualification and a viewer
will read an Over/Under opinion the model never formed.

## The rules, as implemented

`app/show_preview/contract.py` → `MLB_PREVIEW_DISCLOSURES` and
`mandatory_disclosures(sport, view)`. The board attaches them per game
(`game.disclosures`) and to the board-level disclosure list; the client renders
them under the rundown row and keeps them in the print view.

| Code | What it says |
|---|---|
| `TEAM_HISTORY_MODEL_ONLY` | Team score history only. No pitchers, lineups, injuries, park, rest or market inputs. |
| `STARTERS_AND_LINEUPS_UNKNOWN` | Starters and lineups are unknown, not merely unconfirmed. |
| `NO_JOINT_SCORE_DISTRIBUTION` | Win probability and the two expected scores are estimated separately. Not one joint distribution, and not a simulated population. |
| `NO_TOTAL_OR_RUN_LINE_PROBABILITY` | No total or run-line probability is produced. Expected runs are not an Over/Under or run-line probability. |
| `RESEARCH_ONLY` | Research and editorial only. No decision, stake, position or wagering authority. |
| `NOT_FINAL_V18` | Not the Final V18 forecast, and never a substitute for one. |

Two are **conditional**, because a disclosure that contradicts the evidence is a
second inaccuracy rather than a safeguard:

* `STARTERS_AND_LINEUPS_UNKNOWN` is dropped if the package states a starter or
  lineup status other than `UNKNOWN`. The existing `PROBABLE_STARTERS` and
  `LINEUPS_NOT_CONFIRMED` notes then cover what it does say.
* `NO_JOINT_SCORE_DISTRIBUTION` is dropped if the package actually carries a
  distribution.

The other four are unconditional. Nothing in the display path can suppress
them, and they are attached by the consumer rather than copied from whatever the
producer chose to declare.

## Whose side of the line this is

AISP's side is display: the forecast is imported through the existing validator
and shown with the disclosures above, and until a genuine package exists the MLB
rows say "Preview forecast awaiting producer."

Everything upstream of that belongs to Model Studio: `CURRENT_INPUT_REQUEST.json`,
historical and current modeling-data acquisition, source admissibility and
chronology, feature engineering, the MLB matchup/starter shrinkage research,
model fitting and evaluation, and producing genuine MLB preview packages. AISP
does not assess whether its own data could satisfy those requirements and does
not build or propose a model-input export.

## What is pinned in tests

`server/tests/test_show_preview_mlb_status.py`:

* the discovery index names 17 packages, all NFL or CFB, none under `handoffs/`;
* the handoff manifest declares zero forecasts and has no `forecast.json`;
* the handoff manifest fed to `validate_forecast` raises `ContractViolation`;
* `verify_index` pointed at the handoff address fails closed;
* all six disclosures attach to an MLB preview view, in order;
* each conditional one drops when the package supplies what it denies, and the
  four unconditional ones never drop;
* no forecast means no disclosures, and NFL / CFB / NBA are never given MLB's
  limitations.
