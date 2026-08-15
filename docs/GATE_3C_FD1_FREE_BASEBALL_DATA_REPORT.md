**Gate 3C-FD1 Free Baseball Data Final Closeout Report**

**Report status:** `FINAL_CLOSEOUT`

**Evidence cutoff:** 2026-08-15

**Scope:** independent verification, recovery closeout, data-readiness analysis,
and commit review. This report does not authorize acquisition, source rescans,
Parquet generation, model fitting, deployment, or a push.

## 1. Executive Result

Gate 3C-FD1 is finalized. The authoritative recovery run is
`92d6f851-815a-5a53-9b5d-6340549121c1`, its last completed stage is
`FINALIZED`, and its terminal readiness decision is
`READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS`. The readiness engine sets
`modeling_can_proceed=true`; no model was fitted in this closeout.

| Final fact | Persisted result |
| --- | ---: |
| Existing Parquet files reused / finalized partitions | 329 / 329 |
| Existing Parquet bytes reused | 337,421,353 |
| Registered Parquet rows | 3,738,096 |
| Identity mappings staged | 737,259 |
| Canonical identity mappings inserted | 736,056 |
| Canonical identity mappings reused on initial finalization | 0 |
| Source-declared unresolved mappings quarantined | 1,187 |
| Source-declared ambiguities quarantined | 16 |
| Blocking canonical conflicts | 0 |
| Provider calls / network requests / provider transports constructed | 0 / 0 / 0 |
| Finalization elapsed time | 637.484 seconds |
| Finalized PostgreSQL migration | `20260815_0015` |

The 1,203 quarantined identity records do not affect a 2021-2025 player,
game, plate appearance, lineup, starter, reliever appearance, player prior, or
partition. That zero-impact result is evidence-based, but it is not the reason
for declaring readiness; the separate readiness engine also verified the
primary population, taxonomy, transition conservation, temporal separation,
target availability, and fold formability.

The finalized result, exact manifest, quarantine analysis, idempotency result,
and readiness result are content-addressed runtime evidence. They remain under
`.runtime/`, are ignored by Git, and must not be committed.

## 2. Interrupted-Run Recovery

The immutable incident record remains
[`GATE_3C_FD1_INTERRUPTED_EXECUTION_REPORT.md`](GATE_3C_FD1_INTERRUPTED_EXECUTION_REPORT.md).
It has not been rewritten as a success report.

The interrupted publisher had already generated the 329 Parquet files but was
suspected to be spending excessive time in per-record identity ORM query/flush
work. The recovery manifest records the affected execute attempt beginning at
`2026-08-15T12:06:22Z` and stopping at `2026-08-15T14:31:59Z`; the incident
record separately preserves the longer operator-observed lifecycle. The
interrupted SQL transaction rolled back, leaving no partially published FD1
run, mapping, checkpoint, or partition. The 329 content-addressed Parquet files
remained readable and reusable.

Recovery replaced the slow path with a manifest-bound bulk `COPY` identity
stage, byte-offset checkpoints, durable 30-second heartbeats, a 120-minute safe
stop, a mandatory sample rehearsal with rollback proof, staged publication,
warehouse validation, and one atomic promotion. The finalized database audit
found no open FD1 transaction, no blocked or blocking FD1 lock, and no remaining
FD1 recovery process. Normal canonical reads see the finalized registry state;
staging is not a second visible warehouse.

No source provider was called, no raw source was rescanned, and no Parquet was
regenerated. No ATLAS file, database, service, configuration, or history was
read or changed. An early broad process census displayed unrelated process
command lines only; it caused no interaction or mutation.

## 3. Sources Acquired

The recovery manifest binds five live source releases and 22 exact input
artifacts. These were acquired before recovery; recovery performed zero
acquisition calls.

| Source key | Final source-version ID | Dataset/revision | Identity checksum | Manifest artifacts used | Use |
| --- | --- | --- | --- | ---: | --- |
| `CHADWICK_REGISTER_ARCHIVES` | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | commit `2e8e73355f9c77b963115377bd98c784cfeec10f` | `8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0` | 1 | Identity crosswalk |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | `retrosheet_through_2025` | `47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e` | 2 | Park and team identity support |
| `RETROSHEET_CSV_DOWNLOADS` | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | `retrosheet_through_2025` | `d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52` | 1 | Primary 2021-2025 materialization |
| `RETROSHEET_EVENT_ARCHIVES` | `9a08b5fd-e5ed-4043-a984-04021584f15e` | `retrosheet_through_2025` | `2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8` | 10 | Annual event/box governance and QA |
| `SABR_LAHMAN_BOX_DOWNLOADS` | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | `lahman_1871-2025_csv` | `61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5` | 8 | Identity support and chronological priors |

The 798,907,450-byte Retrosheet master archive has SHA-256
`7e346b078b39feff3b58c21c2d1a50ebe4626c346df546b86908373be7d4ef5f`;
the 27,670,481-byte Chadwick archive has SHA-256
`57ec93f760238a0590db16f85e74a62b90efe41a5cd82161e18545e5d426c48c`.
The manifest records individual payload SHA-256 values for every annual
Retrosheet and consumed Lahman artifact; it does not invent a package checksum
where the publisher supplied separate files.

The broader controlled-acquisition ledger records 43 successful request
observations: 39 immutable revisions and four unchanged-byte revalidations.
That historical acquisition activity is reported separately from the recovery's
zero calls. NOAA/NCEI and Open-Meteo supplied no bytes. No automated MLB or
Baseball Savant acquisition and no historical odds acquisition was introduced.

Existing ASP local evidence is not a sixth acquired open-data release. Its
2,430 canonical 2025 regular-season finals were used read-only for the bounded
2025 reconciliation. No 2023-2024 canonical-game population is claimed, the
pre-existing one-day Statcast research slice was not used to fill historical
coverage, and prospective odds snapshots were not converted into historical
market evidence.

## 4. Source Rights and Attribution

The frozen rights controls are in
[`FREE_BASEBALL_DATA_SOURCE_INVENTORY.md`](FREE_BASEBALL_DATA_SOURCE_INVENTORY.md)
and [`OPEN_DATA_ATTRIBUTION.md`](OPEN_DATA_ATTRIBUTION.md). The readiness audit
verified attribution, authorization, license, checksum, and use-classification
records for every required source key.

| Source | Final classification | Required control |
| --- | --- | --- |
| Retrosheet | Free download; commercial derived use allowed with required attribution | Preserve its exact notice, source release, checksum, and no-endorsement language |
| Chadwick Persons Register | ODC-By 1.0 | Attribution, license URI, pinned commit, and transformation disclosure |
| SABR Lahman modern MLB data | CC BY-SA 3.0 | Attribution and share-alike controls for a publicly conveyed derivative database |
| Lahman Negro Leagues subset | Separate upstream Seamheads rights | Isolated and not used in the 2021-2025 MLB target; public/commercial redistribution remains blocked pending separate review |
| NOAA/NCEI LCDv2 | Public government dataset with dataset-specific citation | Citation, row-level station/file lineage, disclaimer, and no implied endorsement if later used |
| Open-Meteo free hosted API | Noncommercial hosted-service boundary; data CC BY 4.0 | Research-only fallback; not used in FD1 and not approved for a commercial ASP product |

The exact Retrosheet canonical notice remains preserved verbatim:

> The information used here was obtained free of
> charge from and is copyrighted by Retrosheet.  Interested
> parties may contact Retrosheet at "www.retrosheet.org".

Some Retrosheet download pages expose a postal-address variant. That separate
text is not claimed as frozen; public Retrosheet-derived output remains blocked
until the applicable variant is independently captured, checksummed, and shown
with the canonical notice. This is a publication-rights limitation, not a local
modeling-integrity failure.

## 5. Recovery Manifest

The authoritative manifest is
`.runtime/gate3c-fd1/recovery/manifests/fd1-recovery-manifest-sha256-bbe93dae347dd0a02703d14dfb1b40b71979e8add8d8386d92880a54798143b7.json`.

| Manifest identity | Exact value |
| --- | --- |
| Artifact SHA-256 / filename identity | `bbe93dae347dd0a02703d14dfb1b40b71979e8add8d8386d92880a54798143b7` |
| Artifact bytes | 1,483,359 |
| Semantic manifest checksum | `f9e788df4f746fe749e3cd731576bab3d191bea7559eb0b6c75230149589535c` |
| Recovery ID | `92d6f851-815a-5a53-9b5d-6340549121c1` |
| Files / bytes / rows | 329 / 337,421,353 / 3,738,096 |
| Date range | 2021-04-01 through 2025-12-31; the latter date is the annual 2025 prior snapshot |
| Generation revision | `8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7` |
| Finalizer revision | `e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db` |
| Identity spool | 737,259 rows / 568,697,553 bytes / SHA-256 `e85eaf044199049bb6aacf301abc6fb0b3d8fa11fd03cea7dc6e893524a59bca` |

Each of the 329 file entries binds logical URI, family, season/month,
row count, byte count, min/max date, declared and physical schemas, physical
schema fingerprint, source key/version, governance checksum, partition identity
checksum, file SHA-256, mtime, and checkpoint ID. There is no separately
persisted family-season aggregate checksum; the per-file SHA-256 values and the
semantic manifest checksum are the authoritative checksum evidence.

The independent readiness database audit executed 1,133 read statements in a
read-only transaction, rejected no statement, rolled the transaction back, and
wrote zero rows. It observed all six recovery-stage checkpoints plus the
canonical publication counts: 404 ingestion runs, 329 data checkpoints, 329
partitions, and 62 derived-use decisions. It also observed 736,056 resolved
identity mappings and 1,203 recovery quarantine outcomes. These counts are the
normal-read-path verification of the atomically finalized registry.

The finalized evidence is
`.runtime/gate3c-fd1/recovery/reports/fd1-recovery-result-sha256-08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json`
(33,302 bytes; artifact SHA-256 `08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720`;
internal result checksum
`f28a23bec7f29949a61e27d70ec407c86bc6c863eb6b0d5d19a0c15ace5facca`).

## 6. Existing Parquet Reuse

All 329 files were reused in place. Recovery did not invoke a writer or scan
the source archives. It read only the manifest-listed Parquet files for hash,
schema, row, and scientific validation and reused all 337,421,353 bytes and
3,738,096 rows without regeneration.

The validator passed exact manifest bindings, the ten-family schema registry,
the complete 329-file population, per-file checksum uniqueness, the eight-class
plate-appearance domain, transition conservation, timing-risk separation, and
active-identity thresholds. It recorded zero partition-level rejected rows,
zero partition-level quarantined rows, and zero partitions made training-
ineligible. Identity quarantine is a separate mapping-stage disposition.

The recovery path had no provider, raw-source reader, materializer, or Parquet
writer reachability. The generation revision is preserved so reused bytes are
bound to the code that originally produced them; the finalizer revision
separately identifies the recovery code that validated and published them.

## 7. Retrosheet Import

The finalized Retrosheet population contains 12,148 selected regular-season
games: 2,429 / 2,430 / 2,430 / 2,429 / 2,430 for 2021-2025. All 12,148 have
full play-by-play and final records; deduced-play-by-play games, games without
play-by-play, and incomplete selected games are all zero.

Retrosheet contributes nine Parquet families totaling 3,716,273 rows. The
import preserves 22 completed suspended games by season (8 / 1 / 6 / 3 / 4),
one relocated completion, 3,002 automatic-runner placements, source-native
manager and umpire identities, lineup role codes, and exact source lineage.

The annual event/box archives are governance and parser-QA evidence. The final
Parquet family source binding is the master CSV source-version ID
`64db602a-090a-4e37-b8f2-2a48d36dd7f2`. No event file, raw CSV, or 9.27-GB
plays source was rescanned during recovery or closeout.

## 8. Lahman Import

The acquired release is SABR Lahman Version 2025, dataset identity
`lahman_1871-2025_csv`, source-version ID
`3af682f0-ecdb-47d8-8e14-660ec5f7455c`. Nineteen CSV tables published in the
official release are retained. Eight are consumed by FD1: `People`, `Batting`,
`Pitching`, `Fielding`, `Teams`, `TeamsFranchises`, `Parks`, and `HomeGames`.
The other 11 are retained-only and are not represented as transformed Gen-2
families.

The official release did not publish `Appearances.csv`, `AllstarFull.csv`, or
`AwardsPlayers.csv`; no substitute rows were fabricated. The modern MLB data
is separated from the upstream Seamheads Negro Leagues subset.

Lahman produced 21,823 chronological `player_prior` rows: 4,517 / 4,432 /
4,295 / 4,245 / 4,334 for target seasons 2021-2025. Its identity contribution
includes 24,270 person specifications, of which 23,437 are exact, 828 are
source-declared unresolved, and five are source-declared ambiguous. No
quarantined Lahman person appears in finalized `player_prior` evidence.

## 9. Chadwick Identity Spine

The Chadwick register is pinned to commit
`2e8e73355f9c77b963115377bd98c784cfeec10f`. The identity plan contains
722,153 person mapping rows across its sources, including 518,743 unique
Chadwick UUID mappings, 129,658 MLBAM mappings, 25,543 Retrosheet mappings,
23,939 Baseball-Reference mappings, and the Lahman mappings described above.
Names are never used as identity keys.

The full spool contains 737,259 mappings by entity: 722,153 person, 13,886
team, 1,017 park, and 203 franchise. Confidence distribution is 735,492
`EXACT`, 564 `HIGH`, 1,187 `UNRESOLVED`, and 16 `CONFLICT` records. The last two
classes have no canonical ID and are quarantined rather than promoted.

The content-addressed active-identity artifact is
`.runtime/gate3c-fd1/artifacts/active-identity-actual-sha256-0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json`
(54,046 bytes; SHA-256
`0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07`;
derivation checksum
`84b9e134b40d01143fc72d5fa30a87114c5db8d53845361826ce47dfed616a98`).
It reports a 1.0 primary reference match rate, a 1.0 primary unique-player
match rate, zero affected active players, and zero conflicting references.

## 10. NOAA Weather

NOAA/NCEI Local Climatological Data Version 2 remains the authorized targeted
gap-fill source. Its identifiers are `NCEI DSI 2075_02`,
`gov.noaa.ncdc:C01689`, and `local-climatological-data-v2`; the required
citation and station/file lineage contract are preserved in the attribution
document.

The gap predicate did not activate because Retrosheet supplied complete weather
rows for all selected games. FD1 therefore made zero NOAA requests, downloaded
zero NOAA files, and imported zero NOAA observations. No NOAA coverage is
implied by the Retrosheet weather family.

## 11. 2021–2025 Population

`Lineups` below means games with a complete actual historical lineup;
`Starters` and `Relievers` are target rows/appearances, not unique players.

| Season | Population | Games | Finals | PAs | Plays/transitions | Complete lineups | Starters | Reliever appearances | Fielding rows | Weather |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2021 | Prior-only | 2,429 | 2,429 | 181,818 | 190,700 | 2,412 | 4,858 | 16,683 | 66,885 | 2,429 |
| 2022 | Prior-only | 2,430 | 2,430 | 182,052 | 187,969 | 2,421 | 4,860 | 16,018 | 66,184 | 2,430 |
| 2023 | Primary | 2,430 | 2,430 | 184,104 | 191,073 | 2,423 | 4,860 | 15,770 | 66,397 | 2,430 |
| 2024 | Primary | 2,429 | 2,429 | 182,449 | 189,536 | 2,418 | 4,858 | 15,835 | 66,180 | 2,429 |
| 2025 | Primary | 2,430 | 2,430 | 182,926 | 189,939 | 2,420 | 4,860 | 16,008 | 66,513 | 2,430 |
| Total | — | 12,148 | 12,148 | 913,349 | 949,217 | 12,094 | 24,296 | 80,314 | 332,159 | 12,148 |

Unique player counts are exact within season: 1,509 / 1,495 / 1,457 / 1,454 /
1,471, each with 100% identity coverage. The active-identity artifact also
persists the de-duplicated unions: 2,693 unique players across 2021-2025
(21,356,559 references), 1,893 across prior-only 2021-2022 (8,518,476
references), and 2,071 across primary 2023-2025 (12,838,083 references), all
at 100% exact coverage. The source-ID census is 30 teams each season and 31
across the five-season source namespace; parks are 34 / 32 / 33 / 35 / 33 and
42 overall; umpires are 103 / 96 / 95 / 90 / 93 and 119 overall.

All 7,289 games in 2023-2025 are primary Gen-2 eligible. All 4,859 games in
2021-2022 are prior-only. Historical actual starters make every primary game a
timing-risk enhanced candidate; complete actual lineups exist for 7,261 of
those games. No selected regular-season game is excluded for missing PBP,
deduction, incompleteness, identity quarantine, or partition failure. The
source-selection gate excluded 213 non-regular-season games (38 / 41 / 42 /
44 / 48); DEMO, Spring Training, All-Star, exhibition, and postseason evidence
is not in the primary population.

## 12. Game Reconciliation

The content-addressed 2025 reconciliation artifact is
`.runtime/gate3c-fd1/artifacts/publish-actual-dry-run-2025-asp-retrosheet-full-reconciliation-sha256-fc2e15eb706313a85bb6f96b6c5637102e052368a2240f97301d391354da3fe7.json`
(32,459,839 bytes; SHA-256
`fc2e15eb706313a85bb6f96b6c5637102e052368a2240f97301d391354da3fe7`).
It is read-only and records zero writes.

For 2025, 2,430 Retrosheet regular-season finals map one-to-one to 2,430
canonical ASP finals: 2,374 by exact date/home/away and 56 doubleheader games
by exact date/home/away plus ordinal start order. There are zero unmatched,
ambiguous, missing, or conflicting games. `game_identity`,
`team_date_number`, and `final_score` are each 2,430 `MATCH` and zero conflict.

The claim boundary is strict: this reconciliation covers 2025 only. The ASP
side does not expose comparable fields for venue, starters, lineups, batting
participation, pitching participation, or player participation, so every one
of those family comparisons is `NOT_COMPARABLE` for all 2,430 games. The report
does not turn absence of a comparable field into a match.

## 13. Player Identity Coverage

The finalized primary-era identity audit reports:

| Season | Identity references | Resolved references | Unique players | Resolved unique players | Rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023 | 4,303,891 | 4,303,891 | 1,457 | 1,457 | 100% |
| 2024 | 4,260,240 | 4,260,240 | 1,454 | 1,454 | 100% |
| 2025 | 4,273,952 | 4,273,952 | 1,471 | 1,471 | 100% |

The 2021 and 2022 prior-only seasons are also 100% resolved: 4,293,498 / 1,509
and 4,224,978 / 1,495 references/unique players. The active-era threshold was
99.5%; both reference and unique-player rates are 100%, with zero conflict
references and zero affected active players.

Canonical promotion inserted 736,056 resolved mappings. The 1,203
source-declared noncanonical rows were excluded before canonical promotion, so
the canonical mapping table contains none of their target mappings and none
collides with an existing mapping.

## 14. Unresolved and Ambiguous Mapping Impact

The machine-readable quarantine report is
`.runtime/gate3c-fd1/recovery/reports/fd1-identity-quarantine-impact-sha256-cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json`
(2,140,874 bytes; artifact SHA-256
`cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b`;
analysis checksum
`48741790431aaba222fdb91d6fc1405479eb5a004dc1cf9f1fda42d84d6c8e5a`).

| Entity | Source-declared unresolved | Source-declared ambiguous | Total |
| --- | ---: | ---: | ---: |
| Person | 828 | 5 | 833 |
| Park | 357 | 0 | 357 |
| Team | 2 | 11 | 13 |
| Total | 1,187 | 16 | 1,203 |

| Source release | Unresolved | Ambiguous | Total |
| --- | ---: | ---: | ---: |
| Retrosheet auxiliary through 2025 | 357 | 0 | 357 |
| SABR Lahman 1871-2025 | 830 | 16 | 846 |

Thirteen team mappings carry explicit historical seasons (1882, 1897, 1898,
1899, 1905, 1906, 1923, 1933, two in 1939, 1942, 1946, and 1947). The other
1,190 records do not encode a validity season. Therefore they are classified
as `NO_FD1_ACTIVE_DEPENDENCY_SEASON_NOT_DETERMINABLE`, not guessed into an
active or historical season. The 833 person rows do not encode career role,
so player role is `NOT_DETERMINABLE_FROM_ALLOWED_IDENTITY_AND_MANIFEST_EVIDENCE`.

| Dependent impact | Count |
| --- | ---: |
| 2023-2025 primary rows | 0 |
| Games | 0 |
| Plate appearances | 0 |
| Player-lineup rows | 0 |
| Starters | 0 |
| Reliever appearances | 0 |
| Player-prior rows | 0 |
| Full games made training-ineligible | 0 |
| Partitions made training-ineligible | 0 |

All 1,203 mappings remain quarantined and excluded from canonical identity.
All 329 partitions remain finalized. This conclusion follows exact namespace-
correct dependency matching against the manifest and active-identity artifact;
it does not infer names, seasons, or player roles and does not independently
establish overall readiness.

## 15. Lineups and Substitutions

Complete actual historical lineup coverage is 2,412 / 2,421 / 2,423 / 2,418 /
2,420 games by season, or 12,094 of 12,148 overall. The 54 games without a
complete actual lineup are retained; they are not falsely completed. Primary-
era coverage is 2,423/2,430 (99.7119%), 2,418/2,429 (99.5471%), and
2,420/2,430 (99.5885%). These are the three documented readiness limitations.

The materialization preserves 64,823 nonstarting lineup-role occurrences and
five source reconciliation adjustments for lineup positions. Pinch hitters,
pinch runners, defensive substitutions, and player continuity are represented
from source evidence. Actual historical participants do not prove pregame
availability. All 218,664 historical actual-lineup rows are excluded from the
strict population: 87,462 from 2021-2022 remain prior-only, and 131,202 from
2023-2025 are isolated in the enhanced population.

## 16. Starting Pitchers

The `starter_usage` family contains 24,296 rows, exactly two starters per game:
4,858 / 4,860 / 4,860 / 4,858 / 4,860 by season. Starter sequencing and duration
targets pass for all 12,148 games. The 2023-2025 primary population provides
14,578 starter-duration target rows.

All starter identities and manager lineage are populated. Because the rows
describe historical actual starters rather than timestamped pregame
announcements, all 24,296 are marked as historical reconstruction and excluded
from the strict deployable population: 9,718 from 2021-2022 remain prior-only,
and 14,578 from 2023-2025 are valid enhanced/research targets.

## 17. Bullpen Usage

The `bullpen_usage` family contains 80,314 reliever-appearance rows: 16,683 /
16,018 / 15,770 / 15,835 / 16,008 by season. Every selected game has bullpen
usage coverage, and the primary population contributes 47,613 trainable usage
rows.

Pitching-change order, prior appearances, source-native manager identity, and
teamstats lineage are preserved. Manager coverage is complete for all 12,148
games and all 104,610 pitching usage/stat rows; 83 unique manager source IDs
occur across the period. Same-game and future evidence are excluded from
pregame priors.

## 18. Batting / Pitching / Fielding

| Season | Batting rows | Pitching rows | Fielding rows |
| --- | ---: | ---: | ---: |
| 2021 | 71,621 | 21,541 | 66,885 |
| 2022 | 70,911 | 20,878 | 66,184 |
| 2023 | 71,144 | 20,630 | 66,397 |
| 2024 | 71,272 | 20,693 | 66,180 |
| 2025 | 71,550 | 20,868 | 66,513 |
| Total | 356,498 | 104,610 | 332,159 |

All selected games have fielding and pitching-team lineage coverage. Retrosheet
`stattype` semantics remain source-native; the import does not upgrade a value,
lower bound, or upper bound to an official statistic. Batting, pitching, team,
score, and participation conservation checks passed within the imported
warehouse. The 2025 ASP cross-source ledger does not expose these detailed
families and therefore labels them `NOT_COMPARABLE`, as documented in Game
Reconciliation.

## 19. Play-by-Play

The finalized warehouse has 949,217 play/base-out transition rows spanning all
12,148 games. Full-PBP game counts equal selected game counts in every season;
deduced-PBP, box-score-only/no-PBP, and incomplete selected games are zero.

The importer preserves source event identity, game/order identity, batter,
pitcher, runners, score, outs, bases, substitutions, suspended-game segments,
and quality/lineage fields. It retains 22 completed suspended games and 3,002
automatic-runner placements without breaking game chronology. No row is
reclassified as Statcast evidence.

## 20. Plate-Appearance Taxonomy

All 913,349 terminal plate appearances map to the exact eight-class taxonomy;
there are zero unsafe or unmapped terminal events.

| Taxonomy outcome | Rows |
| --- | ---: |
| `BIP_OUT` | 410,309 |
| `DOUBLE` | 39,547 |
| `HOME_RUN` | 28,130 |
| `OTHER_REACH` | 9,027 |
| `SINGLE` | 128,931 |
| `STRIKEOUT` | 206,642 |
| `TRIPLE` | 3,351 |
| `WALK_HBP_CI` | 87,412 |
| Total | 913,349 |

The 2023-2025 primary population supplies 549,479 event-training rows and all
eight outcome classes. No market, odds, CLV, or tracking-derived outcome is
present.

## 21. Base-Out Transitions

The validator checked all 949,217 transition rows across 12,148 games and
937,069 within-game adjacencies. Base/out continuity, runner conservation, run
conservation, score conservation, and automatic-runner placement rules passed
with zero conservation conflicts.

The validator explicitly accepts the three legal terminal `NP` records whose
outs remain 3-to-3, bases remain empty, and runs remain zero; they are not
misclassified as conservation failures. The primary population provides
570,548 transition-training rows, covers all 192 state/outcome cells, and
supports simulator validation across 7,289 games.

## 22. Pitch-Sequence Coverage

The `pitch_sequence` family contains 943,682 records: 187,288 / 187,459 /
190,505 / 189,041 / 189,389 by season. At least one sequence record is present
in every selected game. Raw and parsed Retrosheet sequence forms, parser/schema
version, uncertainty, game identity, and plate-appearance linkage are retained.

This family is Retrosheet event notation, not pitch tracking. It does not
provide calibrated velocity, spin, movement, location, release measurements,
launch metrics, or xwOBA. Sequence coverage is therefore not described as
Statcast coverage.

## 23. Umpires

| Season | Games | Games with plate umpire | Role assignments | Unique source IDs | Missing plate umpire |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2021 | 2,429 | 2,429 | 9,712 | 103 | 0 |
| 2022 | 2,430 | 2,430 | 9,711 | 96 | 0 |
| 2023 | 2,430 | 2,430 | 9,716 | 95 | 0 |
| 2024 | 2,429 | 2,429 | 9,715 | 90 | 0 |
| 2025 | 2,430 | 2,430 | 9,719 | 93 | 0 |

There are 119 unique umpire source IDs across the period. Plate-umpire and
gameinfo lineage is complete for all 12,148 games. Umpire identity is preserved
in the weather-context schema; a target game's actual crew is not automatically
strict-pregame eligible unless announcement timing is separately proven.

## 24. Parks and Park Factors

The source-ID census contains 42 parks overall: 34 / 32 / 33 / 35 / 33 by
season. Each season contains 30 clubs, while 31 team source IDs occur across
the whole period because source identities are season/franchise aware.

The primary readiness population has 96 prior-period park-factor rows, 32 for
each of 2023, 2024, and 2025. Factors use only earlier eligible games; 2023 uses
4,854 assigned prior games, 2024 uses 7,279, and 2025 uses 9,700. One completed
suspended game with two source venues cannot be assigned to a single park and
is excluded only from the park-factor source set, not from its finalized game
or partition.

No coordinates, orientation, roof state, or venue crosswalk is fabricated.
Temporary and special venues remain source-explicit.

## 25. Weather Coverage

The `weather` family contains exactly one row for each of 12,148 selected games:
2,429 / 2,430 / 2,430 / 2,429 / 2,430. All carry
`COMPLETE_RETROSHEET` coverage and exact `gameinfo` lineage. NOAA and Open-Meteo
contributed zero rows and zero calls.

Coverage completeness does not prove pregame availability. Untimestamped
actual game weather is isolated as timing-risk/enhanced evidence unless a
separate pregame cutoff can be demonstrated. Roof state and missing physical
observations are not inferred.

## 26. Defense and Baserunning

The warehouse contains 332,159 player-game fielding rows with complete game
coverage. Conservative defensive evidence includes position participation,
putouts, assists, errors, double plays, passed balls, catcher participation,
and source-defined opportunities.

Baserunning evidence is encoded in the 949,217 transition rows and includes
steals/caught stealing, runner advancement, advancement outcomes, substitutions,
pinch runners, and the 3,002 source/rule-supported automatic-runner placements.
No basic fielding or runner result is renamed OAA, DRS, sprint speed, or another
tracking metric.

## 27. Player Priors

The `player_prior` family contains 21,823 rows: 4,517 / 4,432 / 4,295 / 4,245 /
4,334 by target season. The primary 2023-2025 training population uses 12,972
chronological prior rows.

The temporal contract is Y-1 or earlier: 2023 uses evidence no later than 2022,
2024 no later than 2023, and 2025 no later than 2024. Same-game, same-season
hindsight, and future-season evidence are excluded. The full readiness audit
checked chronology across all 3,738,096 manifested rows and found zero temporal
leakage. No unresolved or conflicting identity appears in the prior family.

## 28. Parquet Warehouse

The following is the exact family-level ledger. All Retrosheet families bind
source-version `64db602a-090a-4e37-b8f2-2a48d36dd7f2`; `player_prior` binds
Lahman source-version `3af682f0-ecdb-47d8-8e14-660ec5f7455c`.

| Family | Schema | Files | Rows | Bytes | Date range |
| --- | --- | ---: | ---: | ---: | --- |
| `bullpen_usage` | `retrosheet_fd1_bullpen_usage_v6` | 36 | 80,314 | 9,902,702 | 2021-04-01..2025-09-28 |
| `pitch_sequence` | `retrosheet_fd1_pitch_sequence_v5` | 36 | 943,682 | 80,347,024 | 2021-04-01..2025-09-28 |
| `plate_appearance` | `retrosheet_fd1_plate_appearance_v5` | 36 | 913,349 | 79,421,951 | 2021-04-01..2025-09-28 |
| `play_transition` | `retrosheet_fd1_play_transition_v5` | 36 | 949,217 | 88,260,346 | 2021-04-01..2025-09-28 |
| `player_game_batting` | `retrosheet_fd1_player_game_batting_v5` | 36 | 356,498 | 30,584,501 | 2021-04-01..2025-09-28 |
| `player_game_fielding` | `retrosheet_fd1_player_game_fielding_v5` | 36 | 332,159 | 27,997,674 | 2021-04-01..2025-09-28 |
| `player_game_pitching` | `retrosheet_fd1_player_game_pitching_v5` | 36 | 104,610 | 10,736,025 | 2021-04-01..2025-09-28 |
| `player_prior` | `gate3c_fd1_identity_prior_v1` | 5 | 21,823 | 2,512,357 | 2021-12-31..2025-12-31 |
| `starter_usage` | `retrosheet_fd1_starter_usage_v6` | 36 | 24,296 | 4,558,900 | 2021-04-01..2025-09-28 |
| `weather` | `retrosheet_fd1_weather_v8` | 36 | 12,148 | 3,099,873 | 2021-04-01..2025-09-28 |
| Total | 10 declared schemas | 329 | 3,738,096 | 337,421,353 | 2021-04-01..2025-12-31 |

The exact family-season ledger follows. Every row has zero recovery rejection,
zero partition quarantine, and complete per-file SHA-256 coverage in the
manifest.

| Family | Season | Files | Rows | Bytes | Min date | Max date |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `bullpen_usage` | 2021 | 7 | 16,683 | 2,031,118 | 2021-04-01 | 2021-10-03 |
| `bullpen_usage` | 2022 | 7 | 16,018 | 1,969,231 | 2022-04-07 | 2022-10-05 |
| `bullpen_usage` | 2023 | 8 | 15,770 | 1,985,180 | 2023-03-30 | 2023-10-01 |
| `bullpen_usage` | 2024 | 7 | 15,835 | 1,950,466 | 2024-03-20 | 2024-09-30 |
| `bullpen_usage` | 2025 | 7 | 16,008 | 1,966,707 | 2025-03-18 | 2025-09-28 |
| `pitch_sequence` | 2021 | 7 | 187,288 | 16,004,427 | 2021-04-01 | 2021-10-03 |
| `pitch_sequence` | 2022 | 7 | 187,459 | 15,900,773 | 2022-04-07 | 2022-10-05 |
| `pitch_sequence` | 2023 | 8 | 190,505 | 16,258,236 | 2023-03-30 | 2023-10-01 |
| `pitch_sequence` | 2024 | 7 | 189,041 | 16,081,252 | 2024-03-20 | 2024-09-30 |
| `pitch_sequence` | 2025 | 7 | 189,389 | 16,102,336 | 2025-03-18 | 2025-09-28 |
| `plate_appearance` | 2021 | 7 | 181,818 | 15,850,962 | 2021-04-01 | 2021-10-03 |
| `plate_appearance` | 2022 | 7 | 182,052 | 15,812,841 | 2022-04-07 | 2022-10-05 |
| `plate_appearance` | 2023 | 8 | 184,104 | 16,043,427 | 2023-03-30 | 2023-10-01 |
| `plate_appearance` | 2024 | 7 | 182,449 | 15,818,685 | 2024-03-20 | 2024-09-30 |
| `plate_appearance` | 2025 | 7 | 182,926 | 15,896,036 | 2025-03-18 | 2025-09-28 |
| `play_transition` | 2021 | 7 | 190,700 | 17,902,536 | 2021-04-01 | 2021-10-03 |
| `play_transition` | 2022 | 7 | 187,969 | 17,383,547 | 2022-04-07 | 2022-10-05 |
| `play_transition` | 2023 | 8 | 191,073 | 17,776,310 | 2023-03-30 | 2023-10-01 |
| `play_transition` | 2024 | 7 | 189,536 | 17,571,538 | 2024-03-20 | 2024-09-30 |
| `play_transition` | 2025 | 7 | 189,939 | 17,626,415 | 2025-03-18 | 2025-09-28 |
| `player_game_batting` | 2021 | 7 | 71,621 | 6,153,365 | 2021-04-01 | 2021-10-03 |
| `player_game_batting` | 2022 | 7 | 70,911 | 6,088,101 | 2022-04-07 | 2022-10-05 |
| `player_game_batting` | 2023 | 8 | 71,144 | 6,119,163 | 2023-03-30 | 2023-10-01 |
| `player_game_batting` | 2024 | 7 | 71,272 | 6,088,723 | 2024-03-20 | 2024-09-30 |
| `player_game_batting` | 2025 | 7 | 71,550 | 6,135,149 | 2025-03-18 | 2025-09-28 |
| `player_game_fielding` | 2021 | 7 | 66,885 | 5,636,241 | 2021-04-01 | 2021-10-03 |
| `player_game_fielding` | 2022 | 7 | 66,184 | 5,594,568 | 2022-04-07 | 2022-10-05 |
| `player_game_fielding` | 2023 | 8 | 66,397 | 5,612,562 | 2023-03-30 | 2023-10-01 |
| `player_game_fielding` | 2024 | 7 | 66,180 | 5,565,023 | 2024-03-20 | 2024-09-30 |
| `player_game_fielding` | 2025 | 7 | 66,513 | 5,589,280 | 2025-03-18 | 2025-09-28 |
| `player_game_pitching` | 2021 | 7 | 21,541 | 2,197,276 | 2021-04-01 | 2021-10-03 |
| `player_game_pitching` | 2022 | 7 | 20,878 | 2,137,052 | 2022-04-07 | 2022-10-05 |
| `player_game_pitching` | 2023 | 8 | 20,630 | 2,147,016 | 2023-03-30 | 2023-10-01 |
| `player_game_pitching` | 2024 | 7 | 20,693 | 2,119,801 | 2024-03-20 | 2024-09-30 |
| `player_game_pitching` | 2025 | 7 | 20,868 | 2,134,880 | 2025-03-18 | 2025-09-28 |
| `player_prior` | 2021 | 1 | 4,517 | 519,844 | 2021-12-31 | 2021-12-31 |
| `player_prior` | 2022 | 1 | 4,432 | 508,028 | 2022-12-31 | 2022-12-31 |
| `player_prior` | 2023 | 1 | 4,295 | 490,878 | 2023-12-31 | 2023-12-31 |
| `player_prior` | 2024 | 1 | 4,245 | 490,343 | 2024-12-31 | 2024-12-31 |
| `player_prior` | 2025 | 1 | 4,334 | 503,264 | 2025-12-31 | 2025-12-31 |
| `starter_usage` | 2021 | 7 | 4,858 | 906,869 | 2021-04-01 | 2021-10-03 |
| `starter_usage` | 2022 | 7 | 4,860 | 905,460 | 2022-04-07 | 2022-10-05 |
| `starter_usage` | 2023 | 8 | 4,860 | 940,465 | 2023-03-30 | 2023-10-01 |
| `starter_usage` | 2024 | 7 | 4,858 | 903,353 | 2024-03-20 | 2024-09-30 |
| `starter_usage` | 2025 | 7 | 4,860 | 902,753 | 2025-03-18 | 2025-09-28 |
| `weather` | 2021 | 7 | 2,429 | 613,507 | 2021-04-01 | 2021-10-03 |
| `weather` | 2022 | 7 | 2,430 | 614,061 | 2022-04-07 | 2022-10-05 |
| `weather` | 2023 | 8 | 2,430 | 647,786 | 2023-03-30 | 2023-10-01 |
| `weather` | 2024 | 7 | 2,429 | 612,077 | 2024-03-20 | 2024-09-30 |
| `weather` | 2025 | 7 | 2,430 | 612,442 | 2025-03-18 | 2025-09-28 |

The validator found no duplicate row checksums among the 3,716,273
checksum-bearing Retrosheet rows and no blocking semantic identity conflict.
The 21,823 prior rows are governed by their declared prior schema and separate
temporal/identity checks. All 329 exact file SHA-256 values and physical schema
fingerprints remain in the manifest rather than being duplicated in this
human report.

The independent canonical-partition audit found 329 distinct semantic
partition identities, 329 distinct logical URIs, 329 distinct file SHA-256
values, and 329 distinct source/family/season/month slots: zero duplicates,
zero canonical partitions outside the manifest, and zero manifested partitions
missing from canonical reads.

## 29. Strict vs Enhanced Populations

The readiness operator projects every manifested row into exactly one disjoint
eligibility unit:

| Population | Rows | Governing treatment |
| --- | ---: | --- |
| Strict deployable | 1,460,755 | Target or laggable evidence that does not depend on historical actual participant or untimestamped actual-context leakage |
| Enhanced research | 785,808 | Actual lineups, actual starters/relievers/play state, and untimestamped actual weather kept for upper-bound/research use |
| Prior-only | 1,491,533 | 2021-2022 history and chronological priors used only for lagged context |
| Total classified | 3,738,096 | Exactly equals the manifest row count |

There are zero tier leaks. All actual-lineup and actual-starter rows are
excluded from strict: the 87,462 lineup and 9,718 starter rows from 2021-2022
have prior-only precedence, while the 131,202 lineup and 14,578 starter rows
from 2023-2025 are enhanced. The two formable temporal folds are 2023-to-2024
(184,104 training PAs and 182,449 validation PAs) and
through-2024-to-2025 (366,553 training PAs and 182,926 validation PAs). There
is zero temporal leakage.

## 30. Data Quality and Uncertainty

The full warehouse validator passed all nine terminal checks: exact manifest
bindings, trusted dry-run binding, declared schemas, complete population,
row-checksum uniqueness, eight-class PA taxonomy, transition conservation,
timing-risk separation, and active-identity coverage. It records zero future-
evidence rows, zero prohibited-evidence rows, zero strict/timing-risk overlap,
zero unsafe taxonomy events, zero conservation conflicts, zero tier leaks, and
zero blocked partitions.

Provider-free integrity checks also confirmed one row per required semantic
event identity, complete terminal-event mapping, runner/run/score conservation,
batting-pitching-team consistency, starter and reliever order, lineup and
substitution order, exact doubleheader ordinals, Y-1 player priors with same-
game/current/future exclusion, `DEMO` exclusion, and absence of historical
market or CLV fields.

The important claim limits are:

- Cross-source game reconciliation covers 2025 only. Only game identity,
  team/date/game number, and final score are comparable. Venue, starters,
  lineups, batting, pitching, and player participation are all explicitly
  `NOT_COMPARABLE`.
- Complete actual lineup coverage is below 100% in every primary season:
  99.7119%, 99.5471%, and 99.5885% for 2023-2025. Missing complete lineups
  remain missing and actual lineups are enhanced-only.
- The quarantine analysis cannot determine validity seasons for 1,190 rows or
  career roles for 833 person rows from allowed evidence, and it does not infer
  them.
- The recovery result's `peak_rss_bytes=0` is an unmeasured sentinel, not proof
  of zero memory use. Peak RSS is therefore `UNMEASURED`.
- A stale diagnostic progress record may still display `status=RUNNING`.
  Authoritative database state plus the content-addressed final result report
  `FINALIZED`; the diagnostic value is not used for readiness or idempotency.
- Weather and actual participant coverage are historical actual context, not
  proof of availability at a pregame deployment cutoff.
- The free warehouse does not contain multi-season tracking/contact-quality
  data or historical market/CLV evidence.

These limitations explain the qualified readiness decision; none is hidden by
the high aggregate identity-resolution rate.

## 31. Network Request Accounting

The recovery, idempotency check, quarantine analysis, and readiness audit each
record zero provider calls and zero network requests. Recovery also records
zero provider transports constructed. Historical acquisition is separate:

| Historical source operation | Successful observations | New revisions | Unchanged revalidations | Transport bytes | First-revision bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retrosheet master CSV | 4 | 2 | 2 | 798,910,363 | 798,908,421 |
| Retrosheet auxiliary/identity | 6 | 6 | 0 | 9,864,307 | 9,864,307 |
| Retrosheet 2021-2025 event/box | 10 | 10 | 0 | 21,345,905 | 21,345,905 |
| SABR Lahman metadata/README/CSV | 21 | 20 | 1 | 30,593,620 | 30,545,638 |
| Chadwick revision/archive | 2 | 1 | 1 | 55,340,962 | 27,670,481 |
| Total | 43 | 39 | 4 | 916,055,157 | 888,334,752 |

Two earlier fail-closed attempts occurred before immutable successful manifests
were created; they are not counted as successes. NOAA, Open-Meteo, automated
MLB/Baseball Savant, and historical odds each contributed zero successful
requests in FD1.

## 32. Storage and Runtime

The live database is at sole Alembic head `20260815_0015` and occupies
3,195,608,087 bytes at closeout. Material relation sizes were:

| Relation group | Bytes |
| --- | ---: |
| Recovery identity stage | 1,693,908,992 |
| Canonical identity mappings | 711,270,400 |
| Publication stage | 5,169,152 |
| Canonical partitions | 1,646,592 |
| Identity quarantine | 557,056 |
| Recovery manifest rows | 663,552 |
| Recovery checkpoints | 196,608 |
| Recovery run | 237,568 |

The reused curated Parquet population is 337,421,353 bytes. Free disk at the
closeout snapshot was 266,140,012,544 bytes (247.862 GiB), above the 100-GiB
reserve. Raw storage was not rescanned or remeasured in this recovery-only
closeout.

All result JSON, manifests, spools, NDJSON heartbeats, and other runtime
diagnostics remain below `.runtime/`. `git check-ignore` resolves each cited
recovery artifact to the repository's `.runtime/` ignore rule. No runtime JSON,
CSV spool, Parquet, raw archive, database, or log belongs in the commit.

Peak RSS is unmeasured. The zero stored in the result is a sentinel and is not
reported as actual zero memory consumption.

## 33. Recovery Performance

| Recovery phase | Measured result |
| --- | --- |
| Validate-only | 7.016 seconds; validated but did not finalize |
| Sample rehearsal | 211.031 seconds; durable 5,000-row rehearsal with rollback proof |
| Full finalization | 637.484 seconds |
| Identity `COPY` | 737,259 rows in 170.656 seconds; 4,320.147 rows/second; eight committed chunks |
| Identity spool consumed | 568,697,553 bytes; final byte offset exactly equals spool size |
| Staging-table size | 1,004,470,272 bytes at the measured copy snapshot |
| Stage `ANALYZE` | 1.922 seconds |
| Identity reconciliation | 126.344 seconds |
| Identity promotion | 78.453 seconds |
| Partition registration | 45.750 seconds |
| Final atomic promotion | 84.250 seconds |
| Database activity | 9,289 queries; peak two active connections |

The second invocation used exact-finalized reuse. Its content-addressed result
is `.runtime/gate3c-fd1/recovery/reports/fd1-recovery-result-sha256-bfe409156e6729b825210266f33ae7c19e4395120f01a71a8e84a18a4ab782d5.json`
(1,536 bytes; artifact SHA-256
`bfe409156e6729b825210266f33ae7c19e4395120f01a71a8e84a18a4ab782d5`;
internal result checksum
`c9c519b842e160ccd2a24555625d689de0fd3193fd9978d8f2fde6ad455bfd63`).
It completed in 3.297 internal seconds (6.923 seconds wall), used one peak active
connection and eight queries, staged zero mappings, inserted zero mappings,
reused all 736,056 canonical mappings, created zero new partition rows, reused
all 329 partitions, created or wrote zero Parquet files, performed no source
scan or source-data staging, made zero calls, and returned the same run ID,
manifest checksum, and `FINALIZED` state. It did not create a second semantic
finalized run.

## 34. Test Results

All Python test runs used `APP_ENV=test`, `DATABASE_URL=sqlite://`, empty
provider/API keys, disabled odds/Open-Meteo/Statcast/scheduler flags, and
`PYTHONDONTWRITEBYTECODE=1`. The full backend run additionally routed HTTP,
HTTPS, and ALL proxy variables to the closed loopback port `127.0.0.1:9` while
allowing only localhost. Commands ran from `server` with explicit test-file
lists and ignored workspace-local pytest temp/cache directories.

| Verification | Result |
| --- | --- |
| Focused FD1 (`test_gate3c_fd1*.py`, 26 modules) | 266 passed, 2 expected skips, 0 failed; 55.08 seconds pytest / 60.715 seconds wall |
| Full Gate 3C (`test_gate3c_*.py`, 42 modules) | 478 passed, 3 expected skips, 0 failed; 109.70 seconds pytest / 118.001 seconds wall |
| Full provider-disabled backend (`test_*.py`, 98 modules) | 904 passed, 3 expected skips, 0 failed; 235.45 seconds pytest / 244.515 seconds wall |
| Opt-in PostgreSQL bulk-identity integration | 1 passed, 0 failed in 6.86 seconds; random fail-closed schema created and removed |
| Python compilation | 263 discovered Python files compiled; exit 0 in 2.980 seconds |
| Alembic | sole head/current `20260815_0015`; `alembic check` reported no new upgrade operations |
| Frontend | not run because no file under `client/` changed |

The regular-suite skips were two Windows symlink-privilege cases and the
opt-in PostgreSQL write rehearsal. The latter was then run separately against
the current tree in its isolated random schema and passed. Pytest reported 15,
29, and 364 warnings in the three aggregate runs; the repository suppresses
warning detail with `--disable-warnings`. Sandboxed attempts encountered the
known Windows pytest temporary-directory ACL problem; approved workspace-local
runs completed cleanly and did not change application data.

The precommit staged-content audit covered 69 explicit source-controlled paths:
implementation, three migrations, tests, one durable preparation script, five
FD1 documents, and the line-ending policy. It found zero runtime, raw,
curated, feature, Parquet, CSV/archive, database, result-JSON, log, temporary,
secret, or frontend paths. `git diff --cached --check` exited zero. Runtime,
raw, curated, Parquet, database, and test-artifact probes resolve to Git-ignore
rules.

Neither `gitleaks` nor `trufflehog` is installed. The fallback staged
high-confidence credential/private-key/credential-URI scan found no credential;
its only underlying value is the deliberate
`https://user:password@data.example.test/...` rejection fixture, appearing in
the test and in this report's disclosure. A broader secret-keyword review found
only configuration names, redaction/block lists, documented placeholders, and
deliberate invalid fixtures.

The root line-ending policy pins `server/**/*.py` to LF so raw-byte recovery
revisions remain reproducible under Windows `core.autocrlf=true`. It also
preserves the already-finalized SQL-fragment spacing and the immutable incident
report's Markdown hard breaks. The finalizer revision recomputes exactly to
`e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db`.

## 35. Gen-2 Data Readiness

**Decision:** `READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS`

**Modeling may proceed after this closeout is committed:** `true`

The authoritative readiness artifact is
`.runtime/gate3c-fd1/recovery/readiness/fd1-recovery-readiness-sha256-9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76.json`
(90,754 bytes; artifact SHA-256
`9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76`;
terminal decision checksum
`a917a281495989311898224747d4197b928e1a3da60065107afaa5ad58d8f1c1`;
operator revision
`b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766`;
warehouse derivation checksum
`7c04562ee46d5068392f2112fcd21d6ed8297d2c9ddd762aa74577a8e96b0b32`).

The pure readiness engine passed all required questions: complete 2023-2025
games, both outcome classes, full PA and transition game coverage, 100% primary
identity references, isolated actual lineups, complete starter/bullpen targets,
chronological priors, prior-only park factors, full weather and fielding game
coverage, complete terminal taxonomy, disjoint tiers, two rolling folds, eight
event outcome classes, 192 transition state/outcome cells, and explicit
tracking-data gaps. It reports no readiness blocker, integrity failure,
identity conflict, conservation conflict, temporal leak, or tier leak.

The documented free-data limitations are the incomplete actual-lineup coverage
in 2023, 2024, and 2025, the enhanced-only timing status of historical actual
context, and the absent tracking/contact-quality fields. This is data readiness
for a governed provider-free Gen-2 baseline; it is not production deployment
approval, public redistribution approval, or authorization to fit a model in
this closeout pass.

## 36. Remaining Statcast / Paid-Data Gaps

The terminal gap list is: pitch velocity, pitch spin, pitch movement, pitch
location, launch speed, launch angle, barrel, hard-hit, xwOBA, detailed
chase/contact, tracking-based defense, and tracking-based runner speed.

The one-day pre-existing local Statcast research slice is not multi-season
coverage and is not promoted into the 2021-2025 free warehouse. Retrosheet
pitch strings and traditional fielding/baserunning outcomes are not renamed as
tracking metrics. No historical odds, closing lines, market prices, or CLV
fields were acquired or inferred.

These gaps do not block the free-data event model, transition simulator,
starter-duration target, bullpen-usage target, or rolling temporal evaluation.
They limit tracking-aware/contact-quality extensions and should be evaluated
only after the provider-free baseline establishes their incremental value.

## 37. Recommended Next Step

The source-controlled closeout must be committed locally with no runtime/data
artifact and no push. Its commit identity belongs in the delivery handoff,
because a commit cannot contain its own final hash.

After the closeout commit, open a separately authorized Gen-2 modeling phase using the
strict population as the deployable baseline, the enhanced population only as
an explicitly labeled upper-bound analysis, the 2021-2022 rows only as prior
history, and the two frozen temporal folds for evaluation. Do not reacquire or
regenerate FD1 data. Do not purchase tracking data before the free baseline
quantifies the incremental need, and do not start model fitting as part of this
closeout.
