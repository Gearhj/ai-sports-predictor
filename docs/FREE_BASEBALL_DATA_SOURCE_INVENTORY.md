# Free Baseball Data Source Inventory

**Gate:** `3C-FD1`

**Rights review date:** 2026-08-14

**Scope:** source discovery, rights classification, and the controlled FD1
acquisition that followed the preflight review.

This inventory records the official publisher locations and the minimum use
conditions that must travel with each source. It is an engineering control, not
legal advice. A technically accessible URL is not, by itself, authorization.
Every acquisition must pin immutable bytes, record the observed source version,
and re-check the linked terms at download time. The FD1 acquisition did so for
the five acquired source bundles listed below; this document does not expose
their local raw paths or redistribute their contents.

## Controlled-acquisition result

The following facts are acquisition evidence. Each bundle has a source-specific
registry row, immutable download manifests, content checksums, and its own
owner/provider authorization record. Final warehouse-publication totals are
recorded separately below so they are not confused with raw-source footprints.

| Source bundle | Registry source-version ID | Acquired revision | Governed raw footprint | Result |
| --- | --- | --- | ---: | --- |
| Chadwick Persons Register | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | Commit `2e8e73355f9c77b963115377bd98c784cfeec10f` | 95,633,049 bytes | `ACQUIRED` |
| Retrosheet auxiliary/identity files | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | Through 2025, content-addressed | 53,868,016 bytes | `ACQUIRED` |
| Retrosheet master CSV | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | Through 2025, content-addressed | 11,680,218,471 bytes | `ACQUIRED_AND_EXTRACTED` |
| Retrosheet 2021-2025 events/boxes | `9a08b5fd-e5ed-4043-a984-04021584f15e` | Annual 2021-2025 official archives | 147,898,359 bytes | `ACQUIRED_AND_EXTRACTED` |
| SABR Lahman CSV | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | Version 2025 | 30,614,538 bytes | `ACQUIRED_AND_EXTRACTED` |

The frozen warehouse-readiness contract uses these exact registry keys for
the four source contributors it binds:

| Registry source key | Bound source / frozen version |
| --- | --- |
| `CHADWICK_REGISTER_ARCHIVES` | Chadwick Persons Register / `4a4956de-0769-4bb0-9dc4-cc30515ba16a` / commit `2e8e73355f9c77b963115377bd98c784cfeec10f` |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | Retrosheet auxiliary/identity files / `fef044cc-35e1-499a-8971-a8b29cd9dd7c` / through 2025 |
| `RETROSHEET_CSV_DOWNLOADS` | Retrosheet master CSV / `64db602a-090a-4e37-b8f2-2a48d36dd7f2` / through 2025 |
| `SABR_LAHMAN_BOX_DOWNLOADS` | SABR Lahman CSV / `3af682f0-ecdb-47d8-8e14-660ec5f7455c` / Version 2025 |

The combined Retrosheet CSV ZIP is 798,907,450 bytes with SHA-256
`7e346b078b39feff3b58c21c2d1a50ebe4626c346df546b86908373be7d4ef5f`.
Its seven extracted members total approximately 10.881 GB; `plays.csv` alone
is 9,267,481,903 bytes. The five bundles added 12,008,232,433 bytes; the
complete `data/raw` tree, including its 329,219,861-byte baseline, was measured
at 12,337,452,294 bytes after acquisition. The finalized recovery manifest
governs 329 Parquet artifacts totaling 337,421,353 bytes and 3,738,096 rows;
the closeout database-size snapshot was 3,195,608,087 bytes.

NOAA and Open-Meteo were not called: the selected 2021-2025 Retrosheet game
rows contained the required weather fields, so the governed gap-fill condition
did not activate. No MLB/Baseball Savant or historical-odds acquisition was
performed.

## Decision vocabulary

| Decision | Meaning |
| --- | --- |
| `IMPORT` | Eligible for a governed local download after the storage, checksum, and source-version preflight passes. |
| `INVENTORY ONLY` | Useful context or fallback, but not needed for the first warehouse import or subject to a narrower use boundary. |
| `REJECT` | Must not be used for acquisition in Gate 3C-FD1. |
| `MANUAL IMPORT ONLY` | Only a lawfully obtained local file may enter through the manual seam; no automated collection is authorized. |

## Rights decisions at a glance

| Source | Rights classification | Gate 3C-FD1 decision | Commercial ASP use | Raw redistribution by ASP |
| --- | --- | --- | --- | --- |
| Retrosheet official downloads | `FREE_DOWNLOAD / COMMERCIAL_DERIVED_USE_ALLOWED_WITH_REQUIRED_ATTRIBUTION` | `IMPORT` | Allowed by publisher if its exact notice is displayed prominently | Publisher permits transfer with notice; ASP policy still prohibits public raw-archive hosting |
| Chadwick Persons Register | `ODC_BY_1_0 / ATTRIBUTION_REQUIRED` | `IMPORT` | Allowed under ODC-By 1.0 with attribution | License conditions apply; ASP policy prohibits public raw-archive hosting |
| SABR Lahman 2025 | `CC_BY_SA_3_0 / ATTRIBUTION_AND_SHAREALIKE_REQUIRED` | `IMPORT`, modern MLB tables only | Allowed subject to CC BY-SA 3.0 obligations | ASP policy prohibits public raw-archive hosting |
| Lahman Negro Leagues subset | `SEPARATE_UPSTREAM_SEAMHEADS_RIGHTS` | `INVENTORY ONLY` and isolate | `REVIEW_REQUIRED` before public/commercial use | Prohibited pending separate embedded-rights review |
| NOAA/NCEI LCDv2 | `FREE_GOVERNMENT / DATASET_SPECIFIC_CITATION` | `IMPORT GAPS ONLY` | Permitted subject to dataset citation and no implied NOAA endorsement | ASP policy prohibits public raw-archive hosting |
| Open-Meteo free hosted API | `FREE_HOSTED_API_NONCOMMERCIAL_ONLY / CC_BY_4_0_DATA` | `INVENTORY ONLY`; local fallback after Retrosheet/NOAA | Not allowed on the free hosted tier | Prohibited by ASP policy; service and source-data attribution still required |
| Chadwick retrosplits | `ODBL_1_0 + DBCL_1_0 + RETROSHEET_NOTICE` | `INVENTORY ONLY` | Possible with layered obligations | Do not redistribute from ASP |
| pybaseball / baseballr | Software tools, not source-data licenses | `REJECT` as acquisition mechanisms | Not evaluated | Not applicable |
| Existing local Statcast archive | `EXISTING_LOCAL_GOVERNED_EVIDENCE` | `MANUAL IMPORT ONLY` | Not established | Prohibited |
| Baseball Savant / MLB Digital Properties | `AUTOMATED_ACQUISITION_BLOCKED` | `MANUAL IMPORT ONLY` | Not established | Prohibited |
| Existing prospective Odds snapshots | `EXISTING_ASP_PROSPECTIVE` | Reuse existing rows only | Subscription-tier review remains required | Prohibited |
| Historical Odds API | Paid historical service | `REJECT` for this free-data phase | Plan-dependent | Prohibited |

The exact notices and public-output rules are frozen in
[OPEN_DATA_ATTRIBUTION.md](OPEN_DATA_ATTRIBUTION.md).

## 1. Retrosheet

### Publisher, release, and use terms

- **Official publisher:** Retrosheet, an all-volunteer 501(c)(3).
- **Current public data horizon:** through the 2025 season. The official CSV
  page describes seven master files covering games from 1897 through 2025; the
  event page lists 2025 event and box-score archives. The website reports a
  last update of **2026-08-09**. See the
  [CSV download page](https://retrosheet.org/downloads/csvdownloads.html) and
  [event-file page](https://www.retrosheet.org/game.htm).
- **Release identity:** Retrosheet does not publish a semantic version for these
  archives. The acquisition record must therefore use `through_2025`, the
  page-update date, retrieval timestamp, HTTP metadata, archive SHA-256, and
  payload SHA-256 as the immutable revision identity.
- **Use terms:** Retrosheet's official
  [use notice](https://www.retrosheet.org/notice.txt) expressly permits sale,
  transfer, giveaway, and commercial products, subject to prominent display of
  its required statement. Retrosheet disclaims accuracy guarantees and warns
  that records may be corrected.
- **Attribution:** mandatory, verbatim; see the attribution document.
- **Update cadence:** no fixed SLA. Treat every fetch as a potentially new
  revision even when the filename is unchanged.
- **Acquired size:** the combined ZIP is 798,907,450 bytes; the seven extracted
  members total approximately 10.881 GB. Upstream does not publish a stable
  archive-size manifest, so the immutable manifests and checksums are the
  authoritative byte identities.

### Retrosheet candidate inventory

| Candidate | Exact official location | Data and coverage | Format | Gen-2 value | Overlap / limitations | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Seven-file master CSV package | [csvdownloads.zip](https://retrosheet.org/downloads/csvdownloads.zip) | `allplayers`, `gameinfo`, `teamstats`, `batting`, `pitching`, `fielding`, `plays`; 1897-2025 compiled games | ZIP of CSV | Games, lineups, player-game batting/pitching/fielding, parks, umpires, parsed plays | `stattype` value/official/lower/upper semantics must be preserved; play-by-play includes deduced files; overlaps many traditional files | `IMPORT` as primary Retrosheet package |
| Individual master files | [allplayers](https://retrosheet.org/downloads/allplayers.zip), [gameinfo](https://retrosheet.org/downloads/gameinfo.zip), [teamstats](https://retrosheet.org/downloads/teamstats.zip), [batting](https://retrosheet.org/downloads/batting.zip), [pitching](https://retrosheet.org/downloads/pitching.zip), [fielding](https://retrosheet.org/downloads/fielding.zip), [plays](https://www.retrosheet.org/downloads/plays/plays.zip) | Same families as the combined package | ZIP of CSV | Targeted retry or validation | Duplicate of the master package; do not download all both ways | `INVENTORY ONLY`; use only to repair a missing/corrupt member |
| Parsed plays by target season | [2021](https://www.retrosheet.org/downloads/plays/2021plays.zip), [2022](https://www.retrosheet.org/downloads/plays/2022plays.zip), [2023](https://www.retrosheet.org/downloads/plays/2023plays.zip), [2024](https://www.retrosheet.org/downloads/plays/2024plays.zip), [2025](https://www.retrosheet.org/downloads/plays/2025plays.zip) | Parsed event rows with game state, batter, pitcher, pitch-sequence coverage, outcomes, runs, and runner movement | ZIP of CSV | PA taxonomy and base/out transitions | The current all-years page reports 208,684 games and 16,756,076 lines; a Retrosheet play is not a Statcast pitch | `INVENTORY ONLY` if already present in master package; targeted fallback only |
| Regular-season event files | [event landing page](https://www.retrosheet.org/game.htm); exact annual archives follow `https://www.retrosheet.org/events/{YYYY}eve.zip`, including [2025](https://www.retrosheet.org/events/2025eve.zip) | DiamondWare regular-season play-by-play; annual rosters are included/required for parsing | ZIP of EVA/EVN and roster/team files | Starts/substitutions, pitch-sequence strings where present, pitching changes, runner movements, source-level QA | Some games are deduced or incomplete; event semantics require conservation tests | `IMPORT` for 2021-2025 only after CSV pilot |
| Box-score event files | Exact annual archives follow `https://www.retrosheet.org/events/{YYYY}box.zip`, including [2025](https://www.retrosheet.org/events/2025box.zip) | BSEF lineups and game batting/pitching/fielding totals for games without complete play-by-play | ZIP of EBA/EBN | Coverage and reconciliation where event detail is absent | No full play sequence; must be labeled `BOX_SCORE_ONLY`, never transition-complete | `IMPORT` only for coverage/QA |
| Annual rosters | [rosters.zip](https://www.retrosheet.org/rosters.zip) | Annual roster/person-team records through 2025 | ZIP text | Historical participation intervals and ID support | Does not prove exact active/injury or pregame announcement status | `IMPORT` |
| Game logs | [official landing page](https://www.retrosheet.org/gamelogs/index.html); [2020-2025 archive](https://www.retrosheet.org/gamelogs/gl2020_25.zip); [2025](https://www.retrosheet.org/gamelogs/gl2025.zip) | 161-field game records, 1871-2025; page says annual files updated 2025-12-07 | ZIP text | Final-score and identity QA, starters, umpires, parks, selected weather | Duplicates game/box data; completeness varies with Retrosheet research | `IMPORT` for independent reconciliation only |
| Original schedules | [official landing page](https://www.retrosheet.org/schedule/index.html); [all schedules](https://www.retrosheet.org/schedule/schedule.zip); [2025](https://www.retrosheet.org/schedule/2025SKED.zip) | Planned schedules 1877-2026, including postponement/makeup evidence | ZIP text | Doubleheaders, scheduled identity, postponement QA | Planned schedule is not final game history; 2020 has original and revised versions | `IMPORT` 2021-2025 for QA |
| Person/manager/coach/umpire IDs | [biofile.zip](https://www.retrosheet.org/biofile.zip) | Retrosheet IDs and biographical/role identities | ZIP text | Source identity and umpire mapping | Chadwick Register is the primary crosswalk; names are never primary keys | `IMPORT` as source-native IDs |
| Ballpark codes | [ballparks.zip](https://www.retrosheet.org/ballparks.zip) | Retrosheet park IDs and metadata | ZIP text | Canonical park crosswalk | Coordinates/orientation are not guaranteed; never fabricate them | `IMPORT` |
| Team/franchise codes | [teams.zip](https://www.retrosheet.org/teams.zip) | Retrosheet team/franchise IDs | ZIP text | Season-team and franchise crosswalk | Display names cannot safely identify franchise/team-season | `IMPORT` |
| Umpire logs | [official landing page](https://www.retrosheet.org/downloads/csvumpires.html); [umplogs.zip](https://www.retrosheet.org/downloads/umplogs.zip) | Game logs by umpire, including an ejection count | ZIP of CSV | Umpire-history QA | Duplicates `gameinfo`; ejection coverage is incomplete for Negro Leagues | `INVENTORY ONLY` |
| Player logs | [official landing page](https://www.retrosheet.org/downloads/csvplayers.html); [playerlogs.zip](https://www.retrosheet.org/downloads/playerlogs.zip) | Per-player mirrors of batting/pitching/fielding plus ejection flag | ZIP of CSV | Targeted person QA | Duplicates master player-game files | `INVENTORY ONLY` |
| Official daily totals | [official landing page](https://www.retrosheet.org/officialtotals/official.htm) | Selected official daily totals | ZIP/text by season | Independent stat-line QA where available | Partial temporal coverage; not the primary event source | `INVENTORY ONLY` |

**Retrosheet acquisition rule:** prefer the single master CSV archive, then the
2021-2025 event/box files and small identity files. Do not download duplicate
per-player, per-umpire, per-team, or per-park packages unless a documented QA
gap requires them.

## 2. Chadwick Baseball Bureau Persons Register

- **Official publisher:** Chadwick Baseball Bureau.
- **Official repository:** [chadwickbureau/register](https://github.com/chadwickbureau/register).
- **Immutable download:** pin commit
  [`2e8e73355f9c77b963115377bd98c784cfeec10f`](https://github.com/chadwickbureau/register/commit/2e8e73355f9c77b963115377bd98c784cfeec10f)
  and use its immutable repository archive, not a moving `master` snapshot.
- **Current public revision observed:** a live `git ls-remote --symref` against
  the official repository on 2026-08-14 resolved `HEAD` to
  `refs/heads/master`; both were
  `2e8e73355f9c77b963115377bd98c784cfeec10f`, and no release tags were
  advertised. An earlier indexed/cached commit page showed the older
  2026-07-01 revision `7e23e7df...`; that stale web view is not the acquisition
  identity. The publisher describes updates as roughly weekly, but the exact
  acquired commit—not that cadence claim—is authoritative for reproducibility.
- **Data offered:** 16 `people-0.csv` through `people-f.csv` files plus
  `names.csv` and `countries.csv`. Crosswalks include UUID/Chadwick, Retrosheet,
  MLBAM, Baseball-Reference, FanGraphs, NPB, sports-reference, and Wikidata IDs.
- **Format/size:** immutable repository archive of CSV files. The governed raw
  bundle occupies 95,633,049 bytes; the first stored archive revision was
  27,670,481 transport bytes.
- **Temporal coverage:** broad historical person authority data; not a game
  dataset. The public extract is delayed relative to Chadwick's private system.
- **Quality limits:** identity clusters can merge or split; the short
  `key_person` can theoretically be reused over time, while `key_uuid` is the
  stable primary identity. Alternate names are explicitly incomplete.
- **License:** the repository states
  [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/). That license
  permits commercial use, database modification, and redistribution subject to
  its notice/attribution conditions.
- **Decision:** `IMPORT`. Preserve commit SHA, every file checksum, and the
  provided UUID. Ambiguous mappings remain unresolved.

## 3. SABR Lahman Baseball Database

- **Official publisher:** Society for American Baseball Research (SABR).
- **Official landing/download:** [SABR Lahman Database](https://sabr.org/lahman-database/),
  with its official [comma-delimited Box distribution](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd).
- **Official release documentation:**
  [readme2025.txt](https://sabr.box.com/shared/static/qtgh1olzcaauz5x234wqx8huixizff8l.txt).
- **README byte identity:** Box shared-static locator
  `qtgh1olzcaauz5x234wqx8huixizff8l`, response filename `readme2025.txt`,
  `47,982` bytes, SHA-256
  `8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab`.
  The official response does not expose a stable numeric Box item ID, so the
  shared-static locator, URL, filename, byte count, and checksum are the
  complete observed content identity; do not invent an item ID.
- **Current release identity:** `Version 2025`, statistics through 2025. The
  landing page says released **2026-01-02**, with CSV byte-order-mark corrections
  on **2026-02-18**; the release README itself says **2025-12-10**. Preserve all
  three metadata facts and use downloaded checksums as the decisive byte
  revision.
- **Catalog scope versus the actual 2025 folder:** SABR describes a broader
  historical database covering season/career batting, pitching, fielding,
  people, teams/franchises, parks/home games, managers, schools/college,
  Hall of Fame, and postseason evidence. The official Version 2025 Box folder
  actually publishes 19 CSV tables: `Batting.csv`, `BattingPost.csv`,
  `CollegePlaying.csv`, `Fielding.csv`, `FieldingPost.csv`, `HallOfFame.csv`,
  `HomeGames.csv`, `Managers.csv`, `ManagersHalf.csv`, `Parks.csv`,
  `People.csv`, `Pitching.csv`, `PitchingPost.csv`, `Salaries.csv`,
  `Schools.csv`, `SeriesPost.csv`, `Teams.csv`, `TeamsFranchises.csv`, and
  `TeamsHalf.csv`.
- **Upstream release limitations:** `Appearances.csv`, `AllstarFull.csv`, and
  `AwardsPlayers.csv` are not present in that official Version 2025 published
  CSV set. FD1 records each as `SOURCE_RELEASE_NOT_PUBLISHED`, permits no
  substitute, and fabricates zero replacement rows.
- **FD1 consumption scope:** the current identity/prior/organization import
  consumes `People.csv`, `Batting.csv`, `Pitching.csv`, `Fielding.csv`,
  `Teams.csv`, `TeamsFranchises.csv`, `Parks.csv`, and `HomeGames.csv`. The
  other 11 published tables were acquired and source-registered but remain
  retained-only; acquisition must not be described as Gen-2 materialization.
- **Format/size:** CSV ZIP (also SQL and Access variants). The governed raw
  bundle occupies 30,614,538 bytes. Its individual package and extracted-file
  checksums are retained in immutable manifests. The README checksum above is
  not the CSV-package checksum.
- **Update cadence:** annual, with possible same-release file corrections.
- **Included license status:** **confirmed in the official 2025 README**. The
  database is copyright 1996-2025 by SABR, following Sean Lahman's donation,
  and is licensed under
  [Creative Commons Attribution-ShareAlike 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/).
- **Gen-2 value:** prior-season career/season batting, pitching, fielding,
  appearances, age/debut, position history, team/franchise and park support.
- **Limitations:** aggregated season evidence cannot supply game-event timing;
  same-season totals must not leak into earlier games. Awards and salary are not
  authorized as direct features merely because they exist.
- **Negro Leagues boundary:** SABR says that portion is licensed from
  Seamheads. Keep it physically/logically separate from the current MLB
  forecasting population. Public/commercial use or redistribution of that
  subset remains `REVIEW_REQUIRED` despite the package-level README license.
- **Decision:** `IMPORT` for the modern MLB prior tables, with CC BY-SA
  attribution/share-alike controls. `INVENTORY ONLY` for the isolated Negro
  Leagues subset pending separate review.

## 4. NOAA/NCEI historical hourly weather

- **Official publisher:** NOAA National Centers for Environmental Information.
- **Dataset:** [Local Climatological Data, Version 2](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01689)
  (`LCDv2`). Identifiers: `NCEI DSI 2075_02`,
  `gov.noaa.ncdc:C01689`, and `local-climatological-data-v2`.
- **Current revision metadata:** Edition `Version 2`, published **2023-11-01**,
  metadata last modified **2025-11-25T14:30:17**, updated daily. LCDv2 covers
  approximately 1,000 U.S. stations from 2005 to present.
- **Exact access:** the official
  [object-store root](https://www.ncei.noaa.gov/oa/local-climatological-data/index.html#v2/access/)
  and station/year files at
  `https://www.ncei.noaa.gov/oa/local-climatological-data/v2/access/{YYYY}/LCD_{GHCN_STATION}_{YYYY}.csv`.
  The [LCDv2 documentation](https://www.ncei.noaa.gov/oa/local-climatological-data/v2/doc/lcdv2_DOCUMENTATION.pdf)
  defines the file layout.
- **Data offered:** hourly temperature, dew point/humidity, wind, precipitation,
  sky/weather type, pressure, visibility, quality information, plus daily and
  monthly summaries.
- **Format/size:** station-year CSV. Do not download global/yearwide archives.
  No station-year files were required or downloaded in FD1 because the
  Retrosheet weather-gap predicate did not activate.
- **Use/citation:** public NOAA dataset with a dataset-specific citation and
  warranty disclaimer. Do not imply NOAA endorsement; inspect any file-specific
  annotation before use.
- **Quality limits:** stations differ in variables and periods of record and can
  contain gaps. Station distance, observation-time delta, coordinate evidence,
  and quality flags must remain attached. LCDv2 uses Local Standard Time in
  some products; temporal normalization must follow the documentation.
- **Decision:** `IMPORT GAPS ONLY`, after Retrosheet weather. Restrict requests
  to mapped MLB-park stations and 2021-2025.

## 5. Open-Meteo

- **Official operator:** OpenMeteo GmbH.
- **Official API/documentation:**
  [Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api),
  endpoint `https://archive-api.open-meteo.com/v1/archive`.
- **Coverage:** gap-free reanalysis from 1940 onward; ERA5, ERA5-Land, and
  newer-model options. This is modeled reanalysis, not a direct ballpark
  station observation.
- **Release identity:** no numbered dataset release is shown. Pin retrieval
  timestamp, query, model selection, response checksum, and the terms/license
  snapshot reviewed on 2026-08-14.
- **Format/size/update:** JSON API; request-bounded size. Historical products
  update as underlying/model inputs evolve; no immutable response guarantee is
  claimed.
- **Free hosted-tier restriction:** the official
  [terms](https://open-meteo.com/en/terms) restrict the free hosted API to
  non-commercial use and state limits of 600 calls/minute, 5,000/hour,
  10,000/day, and 300,000/month. Subscription or ad-supported ASP is commercial
  under those terms. Commercial access requires a paid API plan and customer
  endpoint.
- **Data license/attribution:** API data are offered under
  [CC BY 4.0](https://open-meteo.com/en/license), requiring credit, a license
  link, and indication of changes. The hosted-service restriction and the data
  license are separate obligations; CC BY 4.0 does not turn the free hosted API
  into a commercial tier.
- **Decision:** `INVENTORY ONLY` for local, private, non-commercial fallback
  after Retrosheet and NOAA. Never place free-tier-derived evidence in a public,
  subscription, advertising, or other commercial ASP output.

## 6. Additional candidates and explicit exclusions

### Chadwick retrosplits

The official [Chadwick retrosplits repository](https://github.com/chadwickbureau/retrosplits)
contains day-by-day and split statistics derived from Retrosheet. It is offered
under ODbL 1.0, its contents under DBCL 1.0, and it repeats a Retrosheet source
notice. It adds convenient derived splits, but largely duplicates data ASP can
derive from the official Retrosheet files and adds layered share-alike duties.
Decision: `INVENTORY ONLY`; do not download in the first pass.

### pybaseball and baseballr

[pybaseball](https://github.com/jldbc/pybaseball) describes itself as scraping
Baseball Reference, Baseball Savant, and FanGraphs. The official
[baseballr documentation](https://billpetti.github.io/baseballr/articles/baseballr.html)
likewise exposes wrappers for Baseball Savant, Baseball Reference, FanGraphs,
MLB endpoints, and other sites. A software license does not grant rights to the
third-party data it fetches. Decision: `REJECT` as acquisition mechanisms.
They may be inventoried as software only; no Savant/MLB or rejected-site
wrapper may be constructed or called.

### Existing ASP and market evidence

- Existing 2025 canonical warehouse and the retained one-day Statcast archive:
  reuse only under their existing provenance decisions. No new Savant/MLB
  collection. Decision: `MANUAL IMPORT ONLY` for future lawfully obtained
  exports.
- Existing prospective The Odds API rows: reuse the already governed snapshots;
  make no historical acquisition in this phase. The official
  [v4 documentation](https://the-odds-api.com/liveapi/guides/v4/) says
  historical snapshots are available only on paid plans (from 2020 for featured
  markets, with later five-minute cadence). Decision: `REJECT` historical odds
  for this free-data phase; never infer or fabricate missing lines.

## Pre-download hard gates

No `IMPORT` decision above is itself permission to start a download. These
gates were applied to the completed FD1 acquisition and remain mandatory for
any later revision or gap-fill request:

1. exact URL, source version/revision, applicable rights record, and required
   attribution identity;
2. `Content-Length` or a conservative bounded estimate plus extracted-size
   projection, with at least 100 GiB free-space reserve and the 25 GiB hard cap;
3. ignored raw/curated/database paths and a clean secret scan;
4. atomic temporary path, archive-traversal checks, SHA-256 plan, and immutable
   same-filename/new-bytes revision behavior;
5. a zero-call assertion for MLB/Baseball Savant and rejected scraping tools;
6. isolation for Lahman/Seamheads Negro Leagues rows and Open-Meteo free-tier
   research rows.

## Rights conflicts and stops

- **No whole-source conflict was found for Retrosheet, the Chadwick Register,
  modern-MLB Lahman tables, or NOAA LCDv2.** Their stated obligations are
  compatible with governed local research and derived ASP work.
- Retrosheet's linked canonical `notice.txt` uses a website-address notice,
  while some current download pages print a postal-address variant. Preserve
  the canonical statement exactly. Before any public Retrosheet-derived output,
  capture and checksum the exact applicable postal form from its official page
  and display it with the canonical statement until Retrosheet clarifies the
  discrepancy. This inventory does not claim that supplemental string is
  already frozen. A public release that cannot prove and display both is a
  **hard stop**; local acquisition is not.
- Lahman's package license is confirmed, but its Negro Leagues content is
  separately sourced from Seamheads. Public/commercial release or redistribution
  of that subset is a **hard stop** pending separate rights review.
- Open-Meteo free hosted access is explicitly non-commercial. Any use of that
  tier in a subscription, advertising-supported, promotional, or otherwise
  commercial ASP product is a **hard stop**.
