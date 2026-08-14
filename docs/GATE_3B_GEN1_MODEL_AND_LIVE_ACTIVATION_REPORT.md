# Gate 3B Gen-1 Model and Live Activation Report

> **Completion status:** `RESEARCH_CHALLENGER_LIVE_REFRESH_COMPLETE_RELEASE_VERIFIED`.
> Provider-free Phase 1 development, the single governed Phase 2 holdout
> evaluation, one controlled live refresh, and a provider-free forecast-lineage
> remediation are complete. Automated verification, responsive browser QA, and
> the secret/release-integrity review passed. This report is included in the
> requested final commit; its hash is reported in the release handoff.

## Executive Result

Gate 3B admitted the exact approved 2025 strict Tier A A2 dataset under the
separate `MLB_GEN1_RESEARCH_POLICY_V1`, froze a Generation 1 run-strength model
with Platt calibration, and consumed the locked holdout exactly once. The
terminal decision is `RESEARCH_CHALLENGER` with
`RESEARCH_ONLY_NO_PROMOTION`.

The candidate improved point-estimate Brier score and log loss versus the home
prior, Elo, and regularized logistic comparators on the same 505 holdout games.
It did not clear every frozen calibration, OOD, run-model, and bootstrap
decision check. It is therefore credible research evidence, not a production
champion or an authorization to publish official positions.

| Outcome | Observed result |
| --- | --- |
| Gen-1 research decision | `RESEARCH_CHALLENGER` |
| Terminal evaluation status | `RESEARCH_EVALUATED_NO_PROMOTION` |
| Promotion recommendation | `RESEARCH_ONLY_NO_PROMOTION` |
| Selected model / recent variant / calibrator | run strength / `recent_none` / Platt |
| Locked holdout | 505 games, 37 dates, 2025-08-23 through 2025-09-28 |
| Candidate Brier / log loss / ECE | 0.24189382768082518 / 0.6766197598504301 / 0.08282638285346813 |
| Candidate accuracy / ROC-AUC | 0.5584158415841585 / 0.5848393099315609 |
| Controlled live activation | complete: 14 LIVE games, 12 latest forecasts, 2 explicit skips |
| Browser QA | passed at 1440, 1024, 768, and 390 CSS pixels; post-fix 375-pixel explorer retest passed; no body overflow or console errors |
| Official virtual positions | `DISABLED` |
| Historical market / CLV analysis | `NOT AVAILABLE / NOT PERFORMED` |
| Production promotion | `NOT AUTHORIZED` |

## Research Authorization and Boundary

`MLB_GEN1_RESEARCH_POLICY_V1` is a separately authorized research policy. It
does not weaken or replace `gate3-promotion-v1`. Its frozen predictive families
are the supported strict Tier A families `team`, `talent`, `rating`, `recent`,
`hitting`, `pitching`, `bullpen`, and `schedule`. Lifecycle constants,
target-context fields, Tier B starter/lineup context, Statcast, park/weather,
defense/baserunning, opponent adjustment, markets, CLV, and DEMO evidence are
excluded.

Historical admission, model development, holdout evaluation, and the lineage
remediation made zero provider calls. The separately authorized live validation
made current-data calls and exactly one Odds HTTP request. No work acquired new
historical data, accessed a historical Odds endpoint, expanded Statcast,
inferred prices or closing lines, backfilled wagers, activated schedulers, or
published positions.

## Exact Historical Source Admission

The only admitted source was the immutable strict Tier A A2 artifact:

| Source identity | Exact value |
| --- | --- |
| Dataset ID / version | `mlb-gate3-a2-3059f81db1d0e278` |
| Database manifest ID | `d4087299-8a04-4176-bad2-a9ec19128b62` |
| Manifest identity SHA-256 | `3059f81db1d0e2783d0357cf765fbc027958a1d0786c8da83611bae82dca1748` |
| Rows/artifact SHA-256 | `bc627c16f85ac3372cb2cb3fb1c9dab6767291704dac790fb5ffe07b8af406bb` |
| Source feature-version ID | `7cdfa80f-725d-4061-8ca5-5967d9ea5701` |
| Source feature-version checksum | `42162a3055c2d58b1f41a73ef515ca4164b0b6bbc90596ad3f37b32b99155a1d` |
| Source schema / SHA-256 | `mlb_historical_pregame_a2_v2` / `e29cf4f37487590ff09266f189323f9c42f9c8e99f0d64d8d61bdf41d7b9b9ff` |
| Population / date range | 2,428 strict Tier A games; 184 event dates; 2025-03-18 through 2025-09-28 |
| Source / temporal semantics | `LIVE` / `RETROSPECTIVE_RECONSTRUCTION` |
| Historical cutoff policy | `mlb_t_minus_6h_v1` |
| Development partition | 1,923 games; 147 dates |
| Locked holdout | 505 games; 37 dates; starts 2025-08-23 |
| Rolling folds | 16 grouped expanding-origin folds |

Admission returned `ADMITTED_FOR_MLB_GEN1_RESEARCH_ONLY`. Exact dataset,
manifest, rows, feature-version, season, Tier A, source-mode, cutoff, and
supported-family checks passed. All 2,428 rows loaded, both outcome classes were
present, and the adapter explicitly indexed the frozen feature order rather
than trusting JSON object key order. Inspection made zero provider calls, fit no
model, and did not materialize locked-holdout outcomes.

Observed supported-family coverage at admission was:

| Analysis family | Coverage |
| --- | ---: |
| rating | 1.0000000000000000 |
| team | 0.9962932454695222 |
| recent_form | 0.9958813838550247 |
| hitting | 0.9973523181925159 |
| pitching | 0.9974258649093904 |
| bullpen | 1.0000000000000000 |
| rest_travel | 0.8833974006955885 |

This research admission does not change A2's stricter production-promotion
readiness result. Missing starter, lineup, Statcast, weather, market, and other
families remain missing for that gate.

## Frozen Gen-1 Feature and Temporal Contract

The model-input universe contains 230 raw strict A2 specifications and 460
ordered value/missing-indicator outputs. Its ordered model-input schema SHA-256
is `586a39dad2b0d4cd8f386cf6162b16694108aab7afc37e72faf1642f33ebfc4a`.
A2 `talent` maps into the Gen-1 `team` analysis family, `recent` maps to
`recent_form`, and `schedule` maps to `rest_travel`; source-family identity is
retained per feature.

The separately registered derived Gen-1 feature version is:

| Registry field | Exact value |
| --- | --- |
| Feature-version ID | `2bcd3353-dddb-563f-9be8-03698e42519d` |
| Version | `gen1-v1-381e0854cb5d26c0` |
| Schema SHA-256 | `381e0854cb5d26c0eb53d6c7c5120233f7b17dd4f0eda78b1a3138965b0217b3` |
| Definition checksum | `6f83eb239de89bcfc94621d4708d23aab95b6ec2adb1aba79dac0db04e5a26e6` |

Same-date games remained grouped. The first 147 date groups were used only for
development and 16 expanding rolling-origin folds. The newest 37 date groups
were locked. Fold preprocessing, recent-form choice, model and hyperparameter
selection, calibration, ensemble selection, and the research-decision rule
were frozen without dereferencing holdout outcomes.

Phase 2 first consumed a global claim keyed independently of candidate identity,
then reloaded and checksum-verified the exact A2 source artifact, rejected
duplicate game IDs, selected the 505 locked rows internally, and only then
opened their labels. This prevents a replacement candidate or alternate
artifact root from reusing the same holdout.

## Phase 1 Development and Candidate Freeze

### Superseded pre-Phase-2 candidate

The first candidate,
`438d9fa23fef15fa434418cb320f59fd9a4ae88e567f2629401510bdca827795`,
selected run strength but emitted 505 `UNAVAILABLE` driver records because the
selected model did not yet have a governed local attribution method. It was
invalidated and superseded before Phase 2. It created no holdout claim,
evaluation, or ORM model/evaluation row, made no provider call, and did not
access holdout labels. The immutable candidate was not rewritten.

### Driver remediation

The replacement implements deterministic run-strength attribution in additive
log-odds space. The four governed feature terms are home offense, away run
prevention, away offense, and home run prevention, with explicit run-bound and
probability-bound adjustments. Every term discloses its paired `__missing`
state and neutral-fill semantics.

Support is fail-closed rather than self-attested. For every row the pipeline
independently recomputes clipped expected runs, the direct stable-sigmoid
probability, the model API probability, the frozen selected raw probability,
and the calibrated final probability. It rejects drift before artifact write.
The probability bridge from the 0.5 zero-component algebraic origin through raw
and calibration adjustments reconciles to the final probability. Unsupported
selected-model branches remain explicitly unavailable and cannot be frozen.

### Approved replacement candidate

| Phase 1 identity | Exact value |
| --- | --- |
| Candidate artifact identity | `0c207742c1b1d60661c6b92efcd5a4b8776bc12799cb83ef8827e87bb994dceb` |
| Development identity | `97bd965a89d27a5dc44807c6a54b38066628de9ead2d8495ffefaa80ba1396d0` |
| Holdout identity | `b7ab9335c85a5b88d354ad59f274139a2a867a89a77dbb99289b50ee0a1698c8` |
| Content code revision | `gate3b-content-5c2babb3b16bb646ff179f311133499a1aa131c65568adf0522d6873b199ace4` |
| Baseline Git commit | `ce5efb3b6e14954d3f4685caebac3585bc340b7e` |
| Model SHA-256 | `2a10d414bf48d5f975c2c643637ef5d1f2050cd09f3af9bb066bddfff2716a97` |
| Development SHA-256 | `e2979f4688ae5e51c9913a5aca91bdd799dcc505fca45520238460d6ffc1d7a4` |
| Blinded predictions SHA-256 | `c448194bb55f8b4c55df6fca39281f3fca00c393e9a3a2114cb054f1d273e5cc` |
| Candidate artifact bytes | 23,582,840 |
| Holdout labels / provider calls / ORM writes in Phase 1 | false / 0 / 0 |

Development selected `recent_none`, run strength, and Platt calibration. The
learned ensemble was dropped: its Brier gain was 0.000236, below the frozen
0.0005 materiality threshold, and its fold stability was 46.7%, below the 60%
threshold. The negative-binomial component was retained by its development-only
rule in all 16 folds; it is honestly described as Poisson mean plus NB2
dispersion, not an independent mean model.

Run-strength development OOF metrics were:

| Metric | Value |
| --- | ---: |
| Brier | 0.2469301531 |
| Log loss | 0.6867645325 |
| ECE | 0.0518173 |
| Calibration slope | 0.6744 |
| Accuracy | 0.53627 |
| ROC-AUC | 0.55337 |

The complete development OOF probability comparison used the same 1,406
cross-fitted observations:

| Development model | Brier | Log loss | ECE |
| --- | ---: | ---: | ---: |
| Run strength | 0.2469301531 | 0.6867645325 | 0.0518173444 |
| Empirical-Bayesian team | 0.2488576223 | 0.6907630291 | 0.0506409076 |
| Elo | 0.2500746394 | 0.6935510663 | 0.0739981608 |
| Home prior | 0.2510748267 | 0.6953588057 | 0.0414811543 |
| Poisson mean + NB2 dispersion | 0.2517542596 | 0.6966735239 | 0.0615290867 |
| XGBoost | 0.2549633672 | 0.7036631386 | 0.0754143430 |
| Poisson | 0.2598594331 | 0.7149775889 | 0.0939771251 |
| Regularized logistic | 0.2608152946 | 0.7169215002 | 0.0973982383 |

Recent-form selection compared all three predeclared treatments on 1,501
development OOF rows. `recent_none` won with Brier 0.2618857293, log loss
0.7207047426, and ECE 0.0995457241. Raw recent form scored
0.2771715537 / 0.7603848591 / 0.1345235608; Bayesian recent form scored
0.2793408242 / 0.7673677869 / 0.1412727757. Each recent-form alternative was no
worse than `recent_none` on Brier and log loss in only 2 of 16 folds.

The calibrator selection used 970 earlier cross-fitted rows for fitting and 436
later rows for temporal selection, never holdout labels:

| Calibrator | Selection Brier | Log loss | ECE | Selected |
| --- | ---: | ---: | ---: | --- |
| Identity | 0.2512387217 | 0.6957363180 | 0.0566003742 | no |
| Isotonic | 0.2514914341 | 0.6979797701 | 0.0630166592 | no |
| Platt | 0.2497299854 | 0.6926045445 | 0.0524207941 | yes |

Seven leave-one-analysis-family-out logistic ablations were also frozen. A
negative delta means the reduced variant had a lower development OOF metric
than the 212-output full variant; these diagnostics are not causal evidence and
were not used to reopen the holdout.

| Ablation | Outputs | Brier | Delta vs full | Log loss | Delta vs full |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full | 212 | 0.2618857293 | - | 0.7207047426 | - |
| Without bullpen | 156 | 0.2558302472 | -0.0060554821 | 0.7069999426 | -0.0137047999 |
| Without hitting | 156 | 0.2591460493 | -0.0027396800 | 0.7146956797 | -0.0060090629 |
| Without pitching | 164 | 0.2601433438 | -0.0017423855 | 0.7158225537 | -0.0048821889 |
| Without rating | 202 | 0.2608718192 | -0.0010139102 | 0.7183444087 | -0.0023603339 |
| Without recent form | 212 | 0.2618857293 | 0.0000000000 | 0.7207047426 | 0.0000000000 |
| Without rest/travel | 194 | 0.2603455178 | -0.0015402116 | 0.7165374384 | -0.0041673042 |
| Without team/talent | 188 | 0.2609607233 | -0.0009250060 | 0.7185201940 | -0.0021845486 |

The paired neutral-value-plus-missing-indicator strategy scored Brier
0.2618857293 and log loss 0.7207047426. A training-fold-only neutral strategy
scored 0.2616641179 / 0.7202241931, while XGBoost native missingness scored
0.2558306417 / 0.7059638491. These diagnostics stayed development-only. The NB2
distribution branch was retained because it improved development Brier by
0.0080199956 and log loss by 0.0181166020 versus its paired Poisson comparison,
with material overdispersion in all 16 folds; its mean remained the shared
Poisson mean rather than an independently fitted negative-binomial mean.

The frozen covariate-only OOD diagnostic classified the 505 locked rows as 98
`NORMAL`, 407 `CAUTION`, and 0 `LOW_RELIABILITY`. All 505 prediction-driver
records were `SUPPORTED_SELECTED_MODEL`, exactly reconciled, and contained
nonempty positive and negative drivers. The maximum numerical reconciliation
error was `1.17e-15`; the frozen method is
`a2_run_strength_additive_log_odds_v1`.

## Phase 2 Locked-Holdout Evaluation

Phase 2 consumed the global claim once, revalidated the exact source and
candidate identities, evaluated the frozen model without reselection, persisted
the terminal artifact, and registered the derived feature version, model
version, and model evaluation. It made zero provider calls.

| Phase 2 identity | Exact value |
| --- | --- |
| Evaluation artifact identity | `a03a7ae520e0b0bb16cb4de1987d78e1b97eb99cab9e6318b03226ae81d4bdd1` |
| Evaluation identity | `66c50c8f9a09fc54460b6ecf7f566a2b9495cc8766af0b80cf8a1d57ddc548ab` |
| Candidate holdout-claim identity | `433ce6d4b2d09a10f44489896dd850f6cfb35aa6bc7ea642cee046d004da0d1d` |
| Global single-use claim identity | `71f27c14b17ed74e2082cbcdef4a92c665548f487e933255cd58786220795ed4` |
| Model-version ID | `7c4366a1-a92e-49a7-8760-7ecf971289db` |
| Model-evaluation ID | `34784593-4174-43b1-91d7-6bc0783703b6` |
| Evaluation SHA-256 | `3931c71c20e04a11c1f36f6c8844e915af220c7f8ee49634e3895f780e301d4c` |
| Metrics SHA-256 | `334867da86fb380ab7b8c279873a30d165125c45d68ec99e17359251eec758e5` |
| Predictions SHA-256 | `78a1db133dd923630ebc22b233866a2b448d421a9cc8604b3696c901e46f802d` |
| Prediction rows checksum | `f6d005b2282a8944be9d7a1bf07e1e2f234bc0f491a43326ff22f563ae493052` |
| Evaluation artifact bytes / rows | 763,443 / 505 |

### Probability metrics on identical holdout games

| Frozen model/comparator | Brier | Log loss |
| --- | ---: | ---: |
| Selected run strength + Platt | 0.24189382768082518 | 0.6766197598504301 |
| Raw run strength | 0.24093075579945858 | 0.6743488329964926 |
| Poisson | 0.24042935303892926 | 0.6732426206116429 |
| Poisson mean + NB2 dispersion | 0.24118846950760686 | 0.6751629229590345 |
| Empirical-Bayesian team model | 0.24270208017257988 | 0.6780959752636923 |
| XGBoost | 0.2438283902771666 | 0.6803818933440149 |
| Chronological Elo | 0.24621501509436508 | 0.6858039038425919 |
| Regularized logistic | 0.2472825970172262 | 0.6882225469873418 |
| Historical home prior | 0.24871853716479927 | 0.6905827034316326 |

The selected calibrated candidate's ECE was 0.08282638285346813,
calibration intercept was -0.0606989108245339, calibration slope was
1.798791852597088, probability RMSE was 0.49182703024622915, accuracy was
0.5584158415841585, and ROC-AUC was 0.5848393099315609. Raw run strength had
ECE 0.08151175 and calibration slope 1.2127592.

### Time-block bootstrap

| Quantity | Point estimate | 95% date-block interval |
| --- | ---: | --- |
| Candidate Brier | 0.24189382768082518 | [0.23611242637392643, 0.24741269403948896] |
| Candidate log loss | 0.6766197598504301 | [0.6653011636656083, 0.6876919378522478] |

The paired differences below are comparator minus candidate; positive values
favor the selected candidate. Every interval used 1,000 resamples of the same
37 date groups.

| Comparator | Brier difference [95% interval] | Log-loss difference [95% interval] |
| --- | --- | --- |
| Historical home prior | 0.0068247095 [0.0031382197, 0.0107368579] | 0.0139629436 [0.0062149082, 0.0217310447] |
| Chronological Elo | 0.0043211874 [-0.0024497511, 0.0106323003] | 0.0091841440 [-0.0059507542, 0.0239159028] |
| Regularized logistic | 0.0053887693 [-0.0016884413, 0.0121921370] | 0.0116027871 [-0.0021343844, 0.0259258947] |
| Empirical-Bayesian team | 0.0008082525 [-0.0032911521, 0.0048423506] | 0.0014762154 [-0.0066821456, 0.0092742568] |
| XGBoost | 0.0019345626 [-0.0015112026, 0.0052941670] | 0.0037621335 [-0.0037725539, 0.0109413426] |
| Raw run strength | -0.0009630719 [-0.0028807640, 0.0008349102] | -0.0022709269 [-0.0061182190, 0.0014510965] |
| Poisson | -0.0014644746 [-0.0065683344, 0.0037530886] | -0.0033771392 [-0.0133859971, 0.0069587173] |
| Poisson mean + NB2 dispersion | -0.0007053582 [-0.0039112138, 0.0025504768] | -0.0014568369 [-0.0075069597, 0.0045057555] |

Only the home-prior comparison had a positive lower bound for both metrics.
The candidate therefore showed a statistically supported improvement over that
single comparator, not general superiority over the comparator set. The frozen
rule also required calibration, stability, OOD, and run-model conditions; the
home-prior result alone did not authorize candidate status or promotion.

### Run-output error

Only RMSE values were included in the sanitized terminal evidence summary:

| Model | Home | Away | Total | Differential |
| --- | ---: | ---: | ---: | ---: |
| Run strength | 3.17894 | 3.15789 | 4.42843 | 4.53266 |
| Poisson | 3.23662 | 3.15080 | 4.49524 | 4.53864 |
| Poisson mean + NB2 dispersion | 3.23662 | 3.15080 | 4.49524 | 4.53864 |

### Frozen decision rule

| Predeclared check | Result |
| --- | --- |
| Complete matched evidence | pass |
| No statistically clear degradation | pass |
| Not grossly degraded versus home prior | pass |
| Point improvement versus home prior, Elo, and logistic | pass |
| Calibration thresholds | fail |
| OOD clear before label access | fail |
| Run-model non-degradation | fail |
| All relevant time-block bootstrap lower bounds nonnegative | fail |
| Research-only / no promotion boundary | pass |

The predeclared rule therefore returned `RESEARCH_CHALLENGER`, not
`RESEARCH_CANDIDATE`. The ORM lifecycle status is `CHALLENGER`; the exact
research decision remains separately persisted, and promotion remains false.

## Explainability, Subgroups, and OOD

The selected-model driver interface is supported for every evaluated row. It
reports the selected model, baseline and final probabilities, governed positive
and negative terms, input missingness/neutral-fill state, structural clipping
adjustments, and the calibration adjustment. It describes model mechanics and
statistical association, not cause.

Development artifacts also bind the full logistic coefficient/preprocessing
evidence, Bayesian team posterior evidence, XGBoost development importance,
recent-form alternatives, seven leave-one-family-out ablations, missingness
strategies, probability bands, and predeclared holdout subgroup results. The
artifact checksums above are authoritative for those detailed payloads. This
release summary does not manufacture unreported subgroup values or describe
small samples as conclusive.

The OOD diagnostic is covariate-only and used no outcome labels. Its aggregate
status was `CAUTION`: 98 `NORMAL`, 407 `CAUTION`, and 0 `LOW_RELIABILITY`.
Maximum robust z-score was 9.8397202142, mean robust z-score was 0.7569964216,
missingness shift was 0.0078003120, and the mean outside-training-range fraction
was 0.1282832057; the aggregate trigger was `robust_z`. The policy also treats
sub-one-day rest, doubleheaders, travel above 2,500 miles, and incomplete
schedule state as caution conditions. `CAUTION` is not a confidence interval or
an outcome-derived label. Its 407-of-505 result is one reason the frozen
candidate rule did not clear.

## Public and Registry Status

The persisted model version is immutable, uses the separate derived Gen-1
feature version, and retains exact source-feature lineage back to A2. The
persisted evaluation exposes nested validation and locked-holdout evidence,
research decision, checksums, and `NOT_PROMOTED` status to the public read
boundary. It is not `CHAMPION` and does not automatically replace the
operational live forecast.

The new `/research/history` UI and public read endpoints expose governed
historical warehouse summaries separately from Today and live forward
performance. The published summary contains 2,430 canonical 2025 LIVE official
games, 2,428 strict Tier A rows, 4,860 team-game rows, 127,872 player-game rows,
85.4483% mean strict feature coverage (85.4% in the UI), and a passing
target/cutoff/market/lineage integrity status. Strict and enhanced populations
are never pooled.

The paginated game explorer filters by team, month, Tier A/B, source, and page.
It publishes canonical pregame snapshots, completeness, missing starter/lineup
state, priors, and run-strength context without exposing targets, outcomes, raw
payloads, artifact locations, or invented market evidence. Its integrity query
is bound to the exact feature versions named by the latest strict and enhanced
manifests (`7cdfa80f-725d-4061-8ca5-5967d9ea5701` and
`b14debd6-b72a-4f16-aa34-73c771a733a6`). Superseded immutable feature versions
do not block the current explorer, while broken lineage inside either current
version still withholds rows. Post-correction browser verification passed at
1440, 1024, 768, and 390 pixels, plus a 375-pixel explorer retest. It showed all
2,428 strict rows available, no integrity warning, and contained horizontal
scrolling for the explorer table on narrow screens.

## Live Data Activation

The one authorized current-day run completed once, without retry, under job
`0fe5cf93-2905-4e0f-9f02-a793506e6347` in 69.564 seconds. Preflight confirmed
the development environment, a configured Odds credential without printing it,
a one-request budget, a quota floor of 10, 497 credits remaining, and zero
current LIVE/DEMO games or forecasts. Schedulers, Odds polling, Statcast,
official-position publication, and automated positions were disabled;
Open-Meteo was enabled for the bounded weather step.

| Live evidence | Status |
| --- | --- |
| Target local date | 2026-08-14 |
| Schedule | 14 LIVE games, 0 DEMO games |
| Current MLB context | 758 canonical rows: 28 team-stat, 729 roster, 0 bullpen, 1 lineup |
| Weather | 13 snapshots; Tropicana Field lacked maintained coordinates and remained explicit |
| Current Odds | exactly 1 HTTP request; 14 provider events; 756 normalized snapshots |
| Odds ledger | `requests_last=3`; cumulative used 3 -> 6; remaining 497 -> 494 |
| Feature snapshots | 14 validated |
| Initial forecast publications | 12 published; 2 explicitly skipped |
| Scheduler / Odds polling | disabled / disabled |
| Statcast / official positions | disabled / disabled; 0 Statcast calls and 0 positions |

The two forecast skips were both honest missing-probable-pitcher failures, not
substituted estimates:

| Game ID | Readiness | Reason |
| --- | ---: | --- |
| `637411cc-3855-4d2e-838c-80baee8c144d` | 50 | `probable_pitchers` unavailable |
| `bbb04623-e581-4118-b934-d6a439ccca16` | 60 | `probable_pitchers` unavailable |

The controlled run did not write `GameRefreshState.last_refresh`, and the MLB
snapshots retain a null `ingestion_run_id`. Those are operational-observability
limitations: the immutable job record, provider ledger, snapshot timestamps,
and canonical row counts are the available run evidence, but Health must not
invent the two missing lineage fields.

### Provider-free market-lineage remediation

Post-run integrity review found that the 12 immutable version-1 forecasts had
stored only the first 500 characters of their market-snapshot reference. Those
rows were preserved as audit evidence and superseded, not edited. Linear
migration `20260814_0009` changes
`forecasts.market_snapshot_reference` from `VARCHAR(500)` to unbounded `TEXT`,
and forecast generation now records the complete sorted, de-duplicated UUID
set.

Provider-free job `a195ef10-94ca-42cc-b91d-b37e9530a20e` regenerated the 12
eligible forecasts from the already persisted live evidence. It created 12
immutable version-2 forecasts, each resolving exactly 54 of 54 market snapshot
IDs (648 resolved references in total), and made zero provider calls. Public
reads select those latest version-2 rows; the two original missing-pitcher games
remain skipped. No Odds, MLB, Weather, or Statcast request was retried during
remediation, so the terminal Odds ledger remains six credits used and 494
remaining.

The migration downgrade is intentionally conditional: reverting `TEXT` to
`VARCHAR(500)` is safe only when no stored value exceeds 500 characters.
Current complete version-2 references exceed that bound, so PostgreSQL should
reject a downgrade rather than silently truncate lineage unless an explicit
data-retention decision is made first.

### User-visible state

- **Today:** LIVE DATA / DEVELOPMENT with 14 games, 12 latest forecasts, six
  market edges, and two explicit forecast-unavailable cards.
- **Data Health:** live and retrospective evidence remain separate; current
  Odds quota and disabled polling/Statcast/positions are visible. The missing
  refresh-state timestamp and null MLB ingestion-run lineage remain disclosed
  limitations.
- **Performance:** `AVAILABLE`, `RESEARCH_CHALLENGER`, and `NOT_PROMOTED`; the
  locked historical evaluation is not mixed with live forward results and does
  not claim profitability.

Training and historical modeling provider-call counts are exactly zero. No
historical Odds data, closing lines, CLV, historical edge, or ROI evidence
exists.

## Automated Verification

The final repository verification after model-driver, historical-explorer, and
live-lineage corrections produced:

| Verification | Observed result |
| --- | --- |
| Full backend provider-disabled suite | 426 passed in 123 seconds |
| Ordinary sandbox caveat | 2 Windows temporary-directory ACL setup errors; both isolated elevated reruns passed 2/2 |
| Client tests | 47 passed |
| Client production build | passed; one nonblocking 521.73 kB `AnalyticsChart` chunk warning |
| Provider calls during tests | 0 |

The 426-test elevated run is the clean full backend result; the sandbox errors
were host ACL setup failures before test execution, not application failures.
Repository verification commands remain documented in `README.md`.

### Live browser QA

The final responsive sweep passed at 1440, 1024, 768, and 390 CSS pixels with no
body-level horizontal overflow or console errors. Today showed LIVE DATA,
DEVELOPMENT, 14 games, 12 forecasts, six edges, and the two honest unavailable
forecasts with readiness 50/60 and populated `data_as_of`. Performance kept the
505-game offline `RESEARCH_CHALLENGER` evaluation separate from empty forward
metrics and `NOT_PROMOTED`; Health separately showed live score 84, quota 494,
756 Odds snapshots, and the historical warehouse facts. The historical route
showed the canonical counts, strict/enhanced separation, passing integrity, and
all 2,428 strict explorer rows after the feature-version scoping correction.
At 390 pixels the banner wrapped, population columns collapsed, and the native
navigation control remained available. A post-fix 375-pixel Historical Research
retest confirmed the wide explorer table scrolled inside its own container. The
final browser tab was left open on Today.

## Remaining Risks and Next Investment

Gate 3B remains bounded by one reconstructed season, a 505-game one-time
holdout, 407 OOD `CAUTION` rows, imperfect holdout calibration, and absent Tier
A starter/lineup, Statcast, park/weather, defense/baserunning, opponent
adjustment, and market evidence. Retrospective completion proxies order prior
facts but do not prove contemporary final-publication time. No result here is
prospective proof of stability or profitability.

The live validation is one development-day observation, not an operating SLA.
It produced only one lineup record, no bullpen or Statcast rows, no official
positions, two missing-pitcher forecast skips, no refresh-state timestamp, and
null MLB ingestion-run lineage. The superseded version-1 forecasts retain their
truncated market-reference audit evidence; only the latest version-2 forecasts
have complete 54-of-54 lineage. Migration downgrade remains conditional as
described above.

The evidence-backed next investments are:

1. Add governed 2024 and 2023 seasons to test calibration, OOD, prior, and
   regime stability on genuinely new evidence rather than reusing the consumed
   2025 holdout.
2. Establish verifiable historical starter and lineup publication timing,
   preserving missingness when the timestamp cannot be proved.
3. Evaluate governed park/weather and Statcast history only after source terms,
   timing semantics, and availability can support the intended tier.
4. Accumulate prospective LIVE outcomes and lifecycle snapshots with complete
   refresh-state and ingestion-run lineage before judging forward stability.
5. Revisit historical markets and CLV only after entitlement, timestamp,
   retention, and budget review; never use a fabricated closing line.

Historical Odds purchase remains a separate licensing and budget decision. It
is not required to improve probability modeling and must not be inferred from
this result.

## Gate 4 Readiness

**Decision:** `NOT_READY_FOR_PRODUCTION_PROMOTION`.

Gate 3B produced an immutable research challenger and consumed the first valid
2025 holdout evaluation. It did not authorize `CHAMPION`, official positions,
production promotion, continuous provider polling, or historical performance
claims. Any model change must be a new versioned experiment and use new
evidence rather than reopening the consumed holdout.

Gate 4 may proceed only as a separately governed data-expansion and prospective
forward-monitoring stage. Promotion review requires genuinely new seasons or
prospective outcomes, improved calibration/OOD/run-model evidence, complete
operational lineage, and a new predeclared evaluation decision; Gate 3B supplies
no claim of comparator-wide superiority.

## Reproducibility and Release Integrity

| Release check | Result |
| --- | --- |
| Baseline branch / commit | `main` / `ce5efb3b6e14954d3f4685caebac3585bc340b7e` |
| Gate 3B content revision | `gate3b-content-5c2babb3b16bb646ff179f311133499a1aa131c65568adf0522d6873b199ace4` |
| Phase 1 / Phase 2 artifact identities | `0c207742c1b1d60661c6b92efcd5a4b8776bc12799cb83ef8827e87bb994dceb` / `a03a7ae520e0b0bb16cb4de1987d78e1b97eb99cab9e6318b03226ae81d4bdd1` |
| Controlled live / provider-free remediation jobs | `0fe5cf93-2905-4e0f-9f02-a793506e6347` / `a195ef10-94ca-42cc-b91d-b37e9530a20e` |
| Forecast lineage migration | `20260814_0009`; 12 latest v2 rows at 54/54 references each |
| Artifact root | ignored `.runtime/gate3` hierarchy |
| Provider calls during training/evaluation | 0 |
| Controlled Odds request / terminal quota | exactly 1 HTTP request / 494 remaining |
| Final automated verification | backend 426 passed; client 47 passed; production build passed |
| Final browser QA | pass at 1440/1024/768/390; 375-pixel post-fix explorer retest passed; no body overflow or console errors |
| Secret and staging review | pass; the exact 42-path Gate 3B staged scope contains no credential findings or unrelated paths |
| Runtime/model/data/database ignore review | pass; `.env`, `client/dist`, `.runtime`, raw data, runtime models, and database artifacts remained ignored and unstaged |
| Final repository `git diff --check` | pass; no whitespace errors |
| ATLAS | not accessed, inspected, executed, or modified |
| Final commit | this implementation and report are included in `feat: train Gen 1 MLB research model and restore live forecasts`; hash reported in the release handoff |
| Push | not performed |
