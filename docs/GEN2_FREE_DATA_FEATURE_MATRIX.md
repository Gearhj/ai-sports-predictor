# Generation-2 Free-Data Feature Matrix

**Gate:** `3C-FD1`

**Matrix status:** terminal warehouse publication finalized;
`READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS`

**As of:** 2026-08-15

This matrix records what each desired Generation-2 feature may use from the
authorized sources now acquired by FD1. The Retrosheet, Chadwick, and Lahman
archives are immutable and source-registered, and the corresponding adapters,
quality contracts, temporal controls, and Parquet publisher are implemented.
The finalized warehouse contains 329 governed Parquet partitions, 3,738,096
rows, and 337,421,353 bytes. This matrix remains the feature-use contract; the
terminal row ledger, active-player identity audit, 2025 reconciliation, and
readiness evidence are recorded in the FD1 final report and immutable closeout
artifacts.

The governing rights and public-output duties are recorded in
[FREE_BASEBALL_DATA_SOURCE_INVENTORY.md](FREE_BASEBALL_DATA_SOURCE_INVENTORY.md)
and [OPEN_DATA_ATTRIBUTION.md](OPEN_DATA_ATTRIBUTION.md).

## Eligibility vocabulary

| Value | Meaning |
| --- | --- |
| `ELIGIBLE_LAGGED` | May enter the strict historically deployable reconstruction only when every contributing observation is final and available before the game cutoff. |
| `CONDITIONAL_CUTOFF` | Eligible only if an observation timestamp and source lifecycle prove availability at or before the cutoff. |
| `EXCLUDED_TIMING_RISK` | Useful retrospective evidence, but actual publication/announcement timing is unproven; excluded from the strict population. |
| `TARGET_ONLY` | May define a training target or fit a prior-period model, but may not enter the same game's predictors. |
| `RESEARCH_ONLY` | May enter a separately labeled enhanced or local-research population, never the strict/public path. |
| `PROSPECTIVE_ONLY` | Requires separately governed, timestamped future evidence; the free historical sources do not reconstruct it. |
| `UNAVAILABLE` | The authorized free sources do not provide the feature. |

Source-quality statements below describe the source contract and implemented
transformation. Where a row reports measured coverage, it uses the finalized
2021-2025 publication counts; any missingness not separately measured remains
explicit and must not be interpreted as complete coverage.

## Hitter talent

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PA outcome true talent | Retrosheet `plays.csv`: `batter`, `pitcher`, `pa`, `single`, `double`, `triple`, `hr`, `hbp`, `walk`, `k`, `xi`, `roe`, `fc`, `othout`, `noout`; `batting.csv`: `b_pa` through `b_roe` | Completed events before target cutoff; 2021-2022 prior-only and 2023-2025 modern regime | 913,349 terminal PA rows published; event/box/deduced quality and `stattype` remain attached | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible with explicit quality weights | No | Map terminal events to the versioned eight-class taxonomy, then fit chronologically shrunk player rates; never use the target PA/game |
| Prior-season batting level | Lahman `Batting.csv`: `playerID`, `yearID`, `G`, `AB`, `R`, `H`, `2B`, `3B`, `HR`, `BB`, `SO`, `HBP`, `SF`; Retrosheet player-game batting fields | Season totals through Y-1 only | 21,823 annual player-prior rows published; aggregated season evidence has no within-season timing | Lahman CC BY-SA 3.0; Retrosheet attribution when joined | `ELIGIBLE_LAGGED` | Eligible | No | Build career/rolling priors ending before season Y; do not apply full-Y totals backward within Y |
| Age, experience, debut, batting hand | Lahman `People.csv`: birth fields, `debut`, `bats`; Retrosheet `allplayers.csv`: `bat`, `first_g`, `last_g`, `season`; Chadwick identity keys | Biographical fields known before game, subject to source revision | Identity conflicts remain unresolved; missing DOB/hand stays missing | Lahman CC BY-SA; Retrosheet attribution; Chadwick ODC-By | `ELIGIBLE_LAGGED` | Eligible | No | Resolve by canonical person ID, compute age at game date and prior MLB experience without future appearances |
| Recent hitter state | Retrosheet lagged PA/player-game outcomes | Only completed, cutoff-eligible events | 913,349 PA rows published with source quality retained; any lower-quality evidence requires separate reliability | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Chronological decay over underlying event components with posterior uncertainty; no streak bonus or same-game feedback |

## Pitcher talent

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PA outcome true talent allowed | Retrosheet `plays.csv` terminal fields keyed by `pitcher`; `pitching.csv`: `p_bfp`, `p_h`, `p_d`, `p_t`, `p_hr`, `p_w`, `p_k`, `p_hbp` | Completed events before target cutoff | Event detail varies; source quality and `stattype` retained | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible with quality weights | No | Chronologically pool pitcher effects by role and hand; target PA/game excluded |
| Prior-season pitching level | Lahman `Pitching.csv`: `playerID`, `yearID`, `G`, `GS`, `IPouts`, `BF`, `H`, `HR`, `BB`, `SO`, `HBP`, `ER`; Retrosheet pitcher-game history | Through Y-1 for season-Y prior; within-season only from earlier eligible games | Season totals lack game timing; Retrosheet supplies game history where present | Lahman CC BY-SA; Retrosheet attribution | `ELIGIBLE_LAGGED` | Eligible | No | Role-aware career/rolling shrinkage with uncertainty and explicit season boundary |
| Starter/reliever role history | Retrosheet `pitching.csv`: `p_seq`, `gs`, `gf`, `p_ipouts`, `p_bfp`; Lahman `G`, `GS`, `GF` | Prior appearances only | Actual historical use is postgame truth; role prior is laggable | Retrosheet attribution; Lahman CC BY-SA | `ELIGIBLE_LAGGED` | Eligible | No | Estimate prior role probability and role-specific talent; never use target-game `gs` as a strict predictor |
| Recent pitcher state | Retrosheet lagged appearances, outs, batters faced, and event outcomes | Completed appearances before cutoff | Pitch counts can be absent; do not infer them from outs | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Chronological workload and skill-state decay with separate missingness/reliability |

## Lineup

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Actual starting batting order | Retrosheet `teamstats.csv`: `start_l1`-`start_l9`, `start_f1`-`start_f10`; event `start` records; parsed play lineup fields `l1`-`l9` | Retrospective actual participation; publication time not supplied | Complete-lineup evidence for 12,094 of 12,148 games; publication-timing risk remains | Retrosheet attribution required | `EXCLUDED_TIMING_RISK` | Eligible as `GEN2_ACTUAL_LINEUP_UPPER_BOUND` | No | Resolve players/positions, preserve batting order, label `HISTORICAL_ACTUAL_LINEUP_TIMING_RISK` |
| Substitutions and actual participant sequence | Retrosheet event `sub` records and parsed play lineup/fielding-state fields | Retrospective event time only | Event-file dependent; box-only games cannot supply complete sequence | Retrosheet attribution required | `TARGET_ONLY` | Eligible for simulator/event validation | No | Build ordered participant-state changes; never present as pregame availability |
| Pregame projected/confirmed lineup | Existing ASP timestamped prospective lineup observations | Only when `available_as_of <= cutoff` | Historical reconstruction remains unavailable; prospective coverage source-dependent | Existing governed ASP evidence | `CONDITIONAL_CUTOFF` | Eligible | Yes | Select by lifecycle/status without letting later confirmation leak backward |
| Lineup aggregate talent | Hitter priors joined to eligible participant identities | Depends on lineup identity timing | Missing slots and identity failures remain explicit | Inherits all contributing-source duties | Conditional: only a cutoff-eligible lineup | Eligible with actual-lineup risk label | Yes for strict future use | Batting-order/expected-PA weighted posterior talent plus coverage and uncertainty |

## Starter duration

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Actual starter identity | Retrosheet `pitching.csv`: `id`, `p_seq`, `gs`; game/event batteries | Retrospective actual starter; announcement time absent | Identity/sequence reconciliation required | Retrosheet attribution required | `EXCLUDED_TIMING_RISK` | Eligible as reconstruction | Future probable/confirmed starter is prospective | Label `ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION`; do not imply announcement timing |
| Starter duration target | Retrosheet `p_ipouts`, `p_bfp`, and pitch count when present; play-level pitching changes | Postgame target | Outs/BF broadly useful; pitch-count coverage may be partial | Retrosheet attribution required | `TARGET_ONLY` | Eligible | No | Convert outs/BF to duration and exit-hazard targets; preserve missing pitch count rather than infer it |
| Starter rest/workload predictors | Prior Retrosheet appearances: dates, `p_ipouts`, `p_bfp`, pitch count where present | Prior completed appearances only | Same-day doubleheader order requires cutoff proof; pitch-count missingness explicit | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Compute days rest and rolling prior workload by canonical pitcher |
| Target-game announced starter | Existing ASP timestamped probable/confirmed pitcher evidence | Timestamped future lifecycle only | Not reconstructable from actual starter truth | Existing governed ASP evidence | `CONDITIONAL_CUTOFF` | Eligible | Yes | Bind source observation and cutoff; no historical backfill from final box score |

## Bullpen usage

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Reliever appearance/order target | Retrosheet `pitching.csv`: `id`, `p_seq`, `gs`, `gf`, `p_ipouts`, `p_bfp`; play-level pitching changes | Postgame target | 80,314 bullpen-usage rows published; Version 6 materialization retains source-native manager identity and exact source lineage | Retrosheet attribution required | `TARGET_ONLY` | Eligible | No | Derive appearance, order, inning/score state, role proxy, BF, outs, finish target, and source-proven manager context |
| Prior 1/2/3-day reliever workload | Earlier Retrosheet pitcher appearances and pitch counts where supplied | Completed appearances before cutoff only | Missing pitch count remains missing; outs/BF are separate signals | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Rolling workloads, consecutive-use flags, rest, and role-aware uncertainty |
| Appearance probability / weighted pool | Lagged bullpen history plus cutoff-eligible participant evidence | Prior evidence; current roster/availability needed for deployment | Historical participant pool is a proxy, not observed active roster | Retrosheet attribution plus existing ASP evidence if used | Conditional; proxy must be labeled and no postgame target identity may enter | Eligible | Current availability is prospective-only | Fit usage target on historical appearances; construct eligible pool separately at forecast time |
| Injury/option/activation availability | No authorized free historical point-in-time source | Unavailable historically | Actual participation cannot establish announcement timing | N/A | `UNAVAILABLE` | Participation proxy only | Yes | Preserve unknown; do not infer injury or active status from nonappearance |

## Platoon

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Batter/pitcher hand interaction | Retrosheet `plays.csv`: `bathand`, `pithand`, terminal PA fields; `allplayers.csv`: `bat`, `throw` | Earlier PAs only | Switch/unknown hand and missing event rows retained | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Partial pooling by batter hand, pitcher hand, role, and league environment |
| Handedness-specific player talent | Same lagged PA records keyed to canonical people | Earlier seasons/PAs only | Sparse splits need strong shrinkage and uncertainty | Retrosheet attribution; Chadwick ODC-By for identity | `ELIGIBLE_LAGGED` | Eligible | No | Hierarchical player-by-platoon posterior; unseen matchup falls back to pooled prior |

## Park

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Canonical park identity | Retrosheet `gameinfo.csv`: `site`; ballpark code file; Lahman `Parks.csv` and `HomeGames.csv`; existing ASP venue mapping | Game-static when schedule/venue known | Temporary/neutral sites and unresolved crosswalks remain separate | Retrosheet attribution; Lahman CC BY-SA; existing ASP evidence | `ELIGIBLE_LAGGED` or game-static when independently known by cutoff | Eligible | No | Map source park IDs without fabricating coordinates/orientation |
| Prior run factor | Retrosheet prior game outcomes keyed by `site` | Seasons before Y or rolling window ending before game | Small/new parks require shrinkage; sample and uncertainty mandatory | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Versioned park/league run ratio with chronological window and uncertainty |
| Prior HR/handedness factor | Retrosheet prior HR outcomes and `bathand`/`pithand` by `site` | Prior-only | Sparse handedness samples may make feature unavailable | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Shrunk park-event interaction; suppress when sample gate fails |
| Roof state and park orientation | No dependable field in authorized free sources | Unavailable | Must not be fabricated from park identity or weather | N/A | `UNAVAILABLE` | `UNAVAILABLE` | Potential future source | Preserve missingness |

## Weather

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Retrosheet game weather | `gameinfo.csv`: `fieldcond`, `precip`, `sky`, `temp`, `winddir`, `windspeed` | Retrospective game context; exact pregame publication time unproven | 12,148 game rows published as `COMPLETE_RETROSHEET`; categorical-field missingness and timing limits remain explicit | Retrosheet attribution required | `EXCLUDED_TIMING_RISK` unless a separate timestamp proves cutoff availability | Eligible upper-bound/context | No | Parse units/categories, preserve original values and missingness |
| NOAA pre-cutoff station weather | LCDv2: `DATE`, hourly temperature/dew point/humidity, pressure, wind, gust, precipitation, sky, visibility, quality fields | Observation timestamp must be at/before declared cutoff | Nearest station may be distant or incomplete; station/time delta and flags mandatory | NOAA dataset citation | `CONDITIONAL_CUTOFF` | Eligible | No | Nearest justified station, unit normalization, time-zone normalization, no post-cutoff observation |
| NOAA actual game-time context | Same LCDv2 fields nearest start time | May occur after strict pregame cutoff | Observation, not ballpark sensor; missing variables vary by station | NOAA dataset citation | `EXCLUDED_TIMING_RISK` if after cutoff | Eligible upper-bound/context | No | Separate population and timing label; never overwrite Retrosheet weather silently |
| Open-Meteo historical fallback | Historical API response fields and model/reanalysis identity | Request-time reconstruction, not contemporaneously archived pregame evidence | Modeled reanalysis; response can change; use only after Retrosheet/NOAA gaps | Free hosted API noncommercial only; CC BY 4.0 | `RESEARCH_ONLY` | Local private research only | No | Persist query/model/checksum; exclude from public/commercial artifacts |

## Umpire

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Actual umpire crew/plate umpire | Retrosheet `gameinfo.csv`: `umphome`, `ump1b`, `ump2b`, `ump3b`, `umplf`, `umprf`; `plays.csv` umpire fields | Retrospective actual identity; assignment timing unproven | 12,148 game-context rows published with 119 unique source umpire IDs; Version 8 materialization retains crew roles and exact source lineage | Retrosheet attribution; Chadwick ODC-By where identity crosswalk used | `EXCLUDED_TIMING_RISK` for target-game identity | Eligible | Future assignment is prospective-only | Preserve role/game identity and source version/checksum; do not use names as keys |
| Prior umpire run/BB/K environment | Prior Retrosheet games/PAs keyed to umpire | Prior games only, but target umpire must also be cutoff-eligible | Sparse/confounded; minimum sample and shrinkage required | Retrosheet attribution required | Conditional on a cutoff-eligible target assignment | Eligible with actual identity timing disclosure | Yes for strict future deployment | Fit lagged hierarchical effects; current game outcome excluded |
| Called-strike tendency | Retrosheet pitch-sequence codes only if semantics/coverage support it | Prior validated sequences | Not equivalent to pitch location or Statcast called-strike data | Retrosheet attribution required | Conditional; likely unavailable after coverage gate | Research candidate | No | Do not materialize unless sequence parser and sample/semantic tests pass |

## Defense

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Player-position participation | Retrosheet `fielding.csv`: `id`, `d_pos`, `d_ifouts`, `d_gs`; play/lineup fielding positions | Actual retrospective participation | Player-position-game rows; fielding innings may be partial/uncertain | Retrosheet attribution required | `TARGET_ONLY` for target game; lagged history eligible | Eligible | No | Build position-usage priors and actual-participant validation rows |
| Lagged basic fielding performance | Retrosheet `d_po`, `d_a`, `d_e`, `d_dp`, `d_tp`, `d_pb`, `d_wp`, `d_sb`, `d_cs`; Lahman `Fielding.csv` | Prior games/seasons only | Opportunity/context dependent; `stattype` and sample size retained | Retrosheet attribution; Lahman CC BY-SA | `ELIGIBLE_LAGGED` | Eligible | No | Conservative shrunk team/player-position rates with explicit opportunities |
| Advanced range/OAA/DRS | Not supplied by Retrosheet/Lahman/NOAA/Chadwick | Unavailable | Proprietary/tracking-derived metrics must not be renamed from basic fielding | N/A | `UNAVAILABLE` | `UNAVAILABLE` | Potential future source | No proxy may be labeled OAA, DRS, or proprietary defense |

## Baserunning

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SB/CS tendency | Retrosheet batting/team fields `b_sb`, `b_cs`; plays `sb2`, `sb3`, `sbh`, `cs2`, `cs3`, `csh`; Lahman batting totals | Prior games/seasons only | Event detail and attempt opportunities vary; missing opportunity evidence remains explicit | Retrosheet attribution; Lahman CC BY-SA | `ELIGIBLE_LAGGED` | Eligible | No | Shrunk attempt and success rates by runner/state/catcher where supported |
| Runner advancement by state/event | Retrosheet `plays.csv`: `br1_pre`-`br3_post`, runner IDs, run/out indicators, event fields | Earlier plays only for predictors; current play is transition target | Requires runner/run/out conservation; unsafe partitions blocked | Retrosheet attribution required | `ELIGIBLE_LAGGED` as fitted prior | Eligible | No | Estimate advancement transition probabilities with state, event, and uncertainty |
| Pinch-runner use | Retrosheet substitutions and `batting.csv` `pr` | Actual use is postgame truth | Timing within event file where present; box-only detail limited | Retrosheet attribution required | `TARGET_ONLY` | Eligible | No | Role/usage target and lagged manager/team prior |
| Sprint speed / lead distance | Not supplied by authorized free sources | Unavailable | Tracking data gap | N/A | `UNAVAILABLE` | `UNAVAILABLE` | Potential future tracking source | Preserve missingness; do not infer Statcast sprint speed |

## Rest and travel

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Team days since prior game | Retrosheet `gameinfo.csv`: `date`, `visteam`, `hometeam`, `number`, final/game status | Latest eligible completed game before cutoff | Suspended games/doubleheaders require explicit ordering | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Calendar/elapsed rest from prior eligible final; no schedule-only inference |
| Team travel distance | Prior and target canonical parks plus existing ASP reconciled coordinates | Prior park and target venue must be known by cutoff | Unresolved/temporary venues yield missing; coordinates are never fabricated | Inherits Retrosheet/Lahman/existing ASP duties | `ELIGIBLE_LAGGED` when park mapping is resolved | Eligible | No | Great-circle distance from prior completed-game venue to target venue |
| Player travel / time-zone burden | No complete player movement/transaction timing from free sources | Partially unavailable | Team travel is not proof of individual travel; transactions create uncertainty | N/A / contributing source duties | `UNAVAILABLE` unless roster membership is cutoff-observed | Proxy-only research | Prospective roster evidence needed | Do not assign team travel to an unverified player |

## Base/out transition

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24-state transition posterior | Retrosheet `plays.csv`: `outs_pre`, `outs_post`, `br1_pre`-`br3_post`, event flags, `runs`, batter/pitcher/runner IDs | Fit from earlier eligible plays | Full/deduced/box quality separated; every partition must pass runner/out/run/score conservation | Retrosheet attribution required | `ELIGIBLE_LAGGED` | Eligible | No | Canonicalize base mask and outs; estimate state/event transitions using only prior folds |
| Same-play runs/outs/runner movements | Retrosheet transition and run/out fields | Postgame target/current event truth | Cannot enter same-play predictors; unmapped/unsafe event blocks partition | Retrosheet attribution required | `TARGET_ONLY` | Eligible for simulator validation | No | Deterministic transition record with source checksum and taxonomy version |
| Extra-inning automatic-runner regime | Game date/rule fields plus transition starts | Rule/game context known before game; event state observed retrospectively | 2020+ rule context; source transition must be validated | Retrosheet attribution required | Game-rule flag eligible; event realization target-only | Eligible | No | Version rule by season/game and validate inning-start state |

## Pitch sequence

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Retrosheet pitch-code sequence | `plays.csv`: `pitches`, `nump`, `balls`, `strikes`, `count`; traditional event pitch-sequence field | Postgame sequence; may train lagged categorical tendencies | Partial coverage and code uncertainty; not Statcast | Retrosheet attribution required | `ELIGIBLE_LAGGED` only after parser/coverage validation | Eligible with coverage flag | No | Preserve raw string, parsed tokens, uncertainty, and game/PA identity |
| Count-state tendency | Validated prior pitch codes and terminal outcomes | Earlier PAs only | Depends on pitch-code coverage; missing sequence is not zero pitches | Retrosheet attribution required | Conditional on quality/sample gate | Eligible | No | Chronological count-state rates with missingness and source-code version |
| Velocity/movement/location | Not supplied by Retrosheet pitch strings | Unavailable | Full historical Statcast gap | N/A | `UNAVAILABLE` | Existing one-day Tier-B research only | Future manual/paid source | Never infer tracking measurements from pitch codes |

## Statcast and contact quality

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Exit velocity, launch angle, barrel, hard-hit, xwOBA | No newly authorized automated source; existing local one-day Statcast archive only | One retained day under existing local-research decision | Grossly incomplete historically; cannot represent 2021-2025 | Existing local governed evidence; automated MLB/Savant blocked | `UNAVAILABLE` for strict multi-season model | `GEN2_EXISTING_STATCAST_RESEARCH` only | Future lawful manual export or separate authorization | Keep Tier-B/local-research isolation; never relabel Retrosheet outcomes as contact tracking |
| Pitch velocity, spin, movement, location | Same boundary as above | One retained day at most | Full historical population unavailable | Existing local governed evidence; automated acquisition blocked | `UNAVAILABLE` | One-day reconciliation/research only | Future lawful manual/paid source | Manual-file import seam only; no automated adapter construction |
| Chase/contact/zone metrics | Requires pitch location and swing/contact tracking not present in free imports | Unavailable | Cannot be reconstructed reliably from terminal PA/pitch codes | N/A | `UNAVAILABLE` | `UNAVAILABLE` beyond one-day local research | Potential future data purchase | Do not create branded Statcast-like proxies |

## Injury and availability

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Historical team membership interval | Retrosheet annual rosters, `allplayers.csv` team/season fields, and actual game participation | Retrospective season/participation evidence | Does not prove activation, option, injury, or announcement time; the official Lahman 2025 folder does not publish `Appearances.csv`, so no Lahman appearance rows are inferred | Retrosheet attribution; Chadwick ODC-By where identity crosswalks are used | `EXCLUDED_TIMING_RISK` as current availability | Eligible as `HISTORICAL_PARTICIPATION_INTERVAL` | No | Build conservative Retrosheet membership intervals with provenance; never label `OBSERVED_ACTIVE_PREGAME` |
| Historical injured-list status and transaction timing | No authorized complete point-in-time free source in this phase | Unavailable | Nonappearance is not injury evidence | N/A | `UNAVAILABLE` | `UNAVAILABLE` | Yes, if future governed source is timestamped | Preserve unknown; do not impute injuries from playing gaps |
| Current roster/scratch/availability | Existing ASP prospective observations when separately collected and timestamped | Future lifecycle only | Source and coverage dependent; collection authorization remains separate | Existing governed ASP evidence | `CONDITIONAL_CUTOFF` | Eligible | Yes | Bind observation time, source, status, and cutoff; never backfill with final participation |

## Market data

| Desired feature | Source and raw field(s) | Temporal availability | Quality and missingness | Rights status | Strict deployable | Enhanced research | Prospective-only | Transformation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Timestamped moneyline/total snapshot | Existing prospective The Odds API snapshots only | Prospective snapshots at their recorded timestamps | Historical 2021-2025 coverage unavailable on current plan | Existing provider/subscription lineage | Excluded from `ASP Independent`; conditional only for a separately declared market model | Deferred market-augmented research | Yes | Convert to no-vig only from actual paired quotes; keep provider/cutoff lineage |
| Historical closing/opening market | No free authorized source; paid historical service not acquired | Unavailable | Must not be inferred from current odds, results, or team strength | Historical Odds rejected for FD1 | `UNAVAILABLE` | `UNAVAILABLE` | Potential future paid source | Never fabricate or interpolate missing lines |
| Market movement | Requires multiple actual timestamped quotes for the same game/market | Prospective only under bounded quota | Sparse snapshots remain sparse; no synthetic movement | Existing provider lineage | Excluded from independent model | Deferred | Yes | Append-only quote deltas with exact timestamps and bookmaker/market identity |

## Cross-cutting identity and evidence controls

These controls are not predictive features, but every feature row depends on
them.

| Control | Source and raw field(s) | Required rule | Current evidence state |
| --- | --- | --- | --- |
| Person identity | Chadwick `people-*.csv`: stable UUID and available Retrosheet, MLBAM, Lahman, Baseball-Reference, and FanGraphs crosswalk columns | Stable Chadwick UUID is the spine; source row, revision, checksum, confidence, and conflicts remain auditable; names are not primary keys | 721,320 canonical person mappings published; 833 person rows quarantined. The 2023-2025 active population has 2,071 unique identities across 12,838,083 references, all exact and with no active-population issue. |
| Team/franchise identity | Retrosheet team/franchise files; Lahman `Teams.csv` / `TeamsFranchises.csv`; existing ASP/MLB team IDs | Map team-season separately from franchise; handle neutral sites, doubleheaders, historical names, and Athletics naming without display-name guessing | 13,873 team and 203 franchise mappings published; 13 historical team rows quarantined; all 150 target-era team-seasons mapped exactly |
| Canonical game identity | Retrosheet `gid`, `date`, `visteam`, `hometeam`, `number`, `site`, `gametype`; existing ASP canonical game | Never match only on team/date when multiple games exist; preserve both sources on discrepancy | All 2,430 selected 2025 games match exactly on game identity, team/date/number, and final score; venue, starters, lineups, statistics, and participation remain `NOT_COMPARABLE` rather than asserted equal |
| Evidence timing | Source record timestamp, game/event time, `available_as_of`, cutoff, source mode | Retrospective reconstruction never becomes prospective observation; actual lineups/starters remain timing-risk evidence | Final 2023-2025 populations contain 1,460,755 strict rows and 785,808 timing-labeled enhanced rows; both temporal folds passed without leakage |
| Quality semantics | Retrosheet `stattype`, `box`, `pbp`; archive/release checksums; partition quality report | Preserve value/official/lower/upper, full/deduced/box-only, uncertainty, missingness, and source checksum | All 12,148 selected games are `FULL_PBP`; deduced and no-PBP counts are zero, with source quality retained in every partition |
| Lahman release scope | Official Version 2025 published table set and FD1 table-use declaration | Never synthesize a table absent upstream or call an acquired-only table materialized | Eight tables are consumed (`People`, `Batting`, `Pitching`, `Fielding`, `Teams`, `TeamsFranchises`, `Parks`, `HomeGames`); 11 published tables are retained-only; `Appearances`, `AllstarFull`, and `AwardsPlayers` are `SOURCE_RELEASE_NOT_PUBLISHED` |

## What remains missing after the acquired free-data sources

The following are the final FD1 source-availability conclusions after the
authorized Retrosheet, Chadwick, and Lahman archives were published and audited.
Modeling may proceed under the documented free-data limitations; any future
purchase remains a separately governed decision and is not required to begin
the approved free-data model path.

| Remaining gap | Why the authorized free plan does not close it | Modeling impact | Earliest legitimate resolution |
| --- | --- | --- | --- |
| Multi-season pitch velocity, spin, movement, and location | Retrosheet pitch codes are categorical event notation, not tracking measurements; automated MLB/Savant acquisition remains blocked | Limits pitch-quality, arsenal, command, and detailed count models | Lawfully obtained manual export or separately reviewed historical tracking-data purchase |
| Multi-season exit velocity, launch angle, barrel, hard-hit, and xwOBA | Only one existing local Statcast day is retained | Limits contact-quality decomposition beyond PA outcomes | Lawfully obtained manual export or historical contact-data purchase |
| Detailed chase, zone, whiff, and contact metrics | Require pitch-by-pitch location/swing tracking absent from the free sources | Limits plate-discipline and batter/pitcher mechanism features | Same tracking-data path as above |
| Advanced defense such as OAA/route/range | Retrosheet/Lahman provide participation and basic fielding, not tracking defense | Defense remains conservative and opportunity-based | Licensed advanced-defense or player-tracking source |
| Sprint speed and lead/jump tracking | Play-by-play supplies outcomes and movements, not tracking geometry | Baserunning state can be modeled, player speed mechanism remains coarse | Licensed player-tracking source |
| Historical point-in-time injuries, transactions, activations, and scratches | Rosters/participation do not prove pregame announcement timing | Strict historical availability and participant pools remain incomplete | Timestamped transaction/injury archive with clear rights |
| Historical publication timestamps for actual lineups/starters/umpires | Retrosheet records who participated, not when the assignment was known | Actual-participant upper bound cannot be called strict deployable | Timestamped pregame feed/archive or prospective collection |
| Historical market odds | Free phase rejects paid history and fabrication | No retrospective market-augmented comparator | Separately justified licensed historical-odds purchase, not an independent-model input |
| Roof state and park orientation | Not reliably supplied by the authorized sources | Some park/weather interactions remain missing | Governed venue-configuration source; never fabricate |
| 2023-2024 canonical application `Game` mappings | FD1 preserves exact Retrosheet source-game identities in Parquet but does not create live canonical ORM `Game` rows for these seasons | The analytical warehouse is source-addressable, but application-level historical joins remain unavailable | A separately governed canonical mapping that proves required start-time/time-zone fields, or a schema contract that can represent their justified missingness; never fabricate |

The official Lahman release omissions and the retained-only Lahman tables are
source/import-scope limitations, not a reason to buy replacement rows. They must
remain visible after closeout, and the acquired-only tables cannot be counted as
Gen-2 features unless a later governed adapter actually materializes them.

## Materialization gate

A feature is not buildable merely because this matrix names a source. FD1 now
has a strict direct-API publisher that verifies extraction manifests and source
bytes, reserves at least 100 GiB by default, writes one Parquet artifact and
checkpoint per evidence batch, rejects duplicate/conflicting identity maps,
and records schema, leakage, and quality metadata. The sole migration head/live
revision `20260815_0015` includes the finalized FD1 recovery state, and
v6 usage rows and v8 weather-context rows retain manager/umpire identities with
exact source lineage. The one-week 2024 pilot proved all nine Retrosheet
materialization families can be produced; the annual `player_prior` family is
produced separately from the Lahman prior builder. The terminal 2021-2025
publication is finalized under recovery run
`92d6f851-815a-5a53-9b5d-6340549121c1`: 329 partitions, 3,738,096 rows, and
337,421,353 bytes. The exact-manifest idempotency pass reused all 736,056
published identity mappings and inserted zero new rows; the independent
readiness decision is `READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS`.
