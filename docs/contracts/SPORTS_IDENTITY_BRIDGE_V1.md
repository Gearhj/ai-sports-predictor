# SPORTS_IDENTITY_BRIDGE_V1

Status: finalized Gate 3C-X2 companion-package contract. The frozen release
outcome is `IDENTITY_BRIDGE_FINALIZED_WITH_DOCUMENTED_LIMITATIONS`. The
publisher produced the content-addressed package and the standalone validator
accepted it using only that package, the exact parent package, and validator
code.

The normative manifest model is
`server/app/gen2/exchange/identity_bridge/contracts.py`. Canonical JSON and
content identity are defined in `canonical.py`; the exact parent-package scan in
`parent.py`; row and Parquet rules in `rows.py`; source authority in `source.py`;
rights projection in `rights.py`; and package security in `security.py`. The
generated Draft 2020-12 structural companion is
`SPORTS_IDENTITY_BRIDGE_V1.schema.json` in this directory.

This document describes the contract, publication protocol, and the exact
release finalized on `2026-08-17T04:20:53.179362Z`. Its semantic manifest
SHA-256 is
`6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf`;
all release-time self anchors below are frozen.

## Purpose and scope

The bridge is an immutable, portable companion to one exact MLB FD1 exchange. It
lets consumers convert governed source player identifiers into the canonical
lowercase UUID used by the parent `player_prior` family without a database,
provider, network, raw archive, or sibling application at consumption time.

V1 publishes identifiers and auditable lineage only. It does not publish player
names, name-derived matches, fuzzy or probabilistic matches, birth-date matching,
raw source archives, database exports, forecasts, sportsbook data, credentials,
runtime paths, or user data. A source identifier is never a canonical identifier
merely because the strings happen to resemble one another.

Every Pydantic contract object is frozen, strict, and rejects extra fields.
Strings, integers, numbers, and booleans are not coerced across JSON scalar
types. JSON arrays represent Python tuples. The JSON Schema covers structure;
the Pydantic model and standalone package validator are required for semantic
and byte-level validation.

## Frozen parent binding

V1 is bound to one parent package and is not a generic MLB identity envelope.

| Anchor | Exact required value |
| --- | --- |
| Parent dataset ID | `asp.mlb.open-data.fd1` |
| Parent manifest semantic SHA-256 | `1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Parent dataset version ID | `asp.mlb.open-data.fd1:sha256:1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Parent manifest ID | `sports-dataset-manifest:sha256:1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Parent package ID | `sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Parent partition-authority SHA-256 | `8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437` |
| Evidence cutoff | `2026-08-15` |

The exact plate-appearance class order is array-order-sensitive:

1. `STRIKEOUT`
2. `WALK_HBP_CI`
3. `HOME_RUN`
4. `SINGLE`
5. `DOUBLE`
6. `TRIPLE`
7. `BIP_OUT`
8. `OTHER_REACH`

The bridge validator first runs the complete parent FD1 validator. It then
rechecks the exact parent IDs and digests above, family aggregates and source
versions, the player-bearing physical fields and Retrosheet raw mirrors, and all
28 `player_prior` fields and their lineage. A recomputed parent digest does not
replace any compiled trust anchor.

## Documented governed-versus-portable limitation

The frozen upstream active-identity evidence governs 2,693 unique players for
2021–2025 and 2,071 for the primary 2023–2025 window. The immutable portable
parent physically enumerates 2,692 and 2,070 distinct player identifiers,
respectively. One roster-only governed identity is not enumerated in the allowed
finalized evidence, so V1 does not manufacture an alias or dimension row for it.

Consequently:

- `active_unique_players_2021_2025` remains 2,693 and
  `primary_unique_players_2023_2025` remains 2,071;
- `portable_parent_unique_players_2021_2025` is 2,692 and
  `portable_parent_unique_players_2023_2025` is 2,070;
- each `unrepresented_governed_players_*` value is 1, and the manifest enforces
  `governed = portable + unrepresented` for both windows;
- `totals.active_player_rows` is 2,692, because the dimension represents the
  portable parent, not an unobservable upstream row; and
- all physical identifiers present in the parent must still join exactly at
  100%, with zero unresolved, ambiguous, or multiply mapped references.

The governed nonduplicating reference totals remain 21,356,559 for 2021–2025
and 12,838,083 for 2023–2025. The corresponding nonduplicating physical parent
inventories are 12,411,678 and 7,460,143 references. The parent intentionally
omits the complete upstream `allplayers` population and some player cells from
plays, including LOB, responsible-pitcher, and fielding-position cells. Derived
duplicate families and automatic-runner mirrors are excluded from this physical
denominator. The broader minimum-join inventory is validated separately and
must not be substituted for it.

This gap is a package-content limitation, not a failed physical join and not
permission to retrieve an ungoverned replacement from the database, a provider,
or the network. `downstream_impacted_rows` remains zero because every physical
parent reference is covered.

## Fixed contract identity

| Item | Required value |
| --- | --- |
| `contract_name` | `SPORTS_IDENTITY_BRIDGE` |
| `contract_version` | `SPORTS_IDENTITY_BRIDGE_V1` |
| `canonicalization_version` | `SPORTS_CANONICAL_JSON_V1` |
| `publisher` | `AI Sports Predictor` |
| Sport / league | `Baseball` / `MLB` |
| Evidence cutoff | `2026-08-15` |
| Semantic bridge files | 4 |
| Final package files | 7 |
| Y-1 eligibility records | 6 |
| Allowed namespaces | `RETROSHEET`, `LAHMAN`, `CHADWICK`, `MLBAM`, `BASEBALL_REFERENCE` |

`EXPECTED_BRIDGE_MANIFEST_SHA256`,
`EXPECTED_ALIAS_AUTHORITY_SHA256`, and
`EXPECTED_DIMENSION_AUTHORITY_SHA256` are the only release-time self anchors.
They live in `release_trust.py` and were frozen only after canonical package
bytes passed package-only validation:

| Release anchor | Frozen value |
| --- | --- |
| Bridge manifest semantic SHA-256 | `6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Bridge manifest ID | `sports-identity-bridge-manifest:sha256:6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Bridge package ID | `sha256-6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Alias row-authority SHA-256 | `ddb828aee391b2f725ba4540a874865963313e6c210f246eacd7bbcf28025661` |
| Dimension row-authority SHA-256 | `6f30e1b30ca41002d6cbb2dea8b90da5abfad4f62f96237e780fbee518005acf` |
| Independent validation | `PASS`; 7 files; 2,071,740 bytes |
| Validation-report byte SHA-256 | `caacf363c6cd4205b823b5c0137fb734d9c640de12cdbffcca0e4fed972eef9b` |

`release_trust.py` is excluded from the publishing-code revision so freezing
these values did not alter the already-validated semantic identity; semantic
`trust.py` remains inside that revision. Every subsequent validation and
idempotent reuse must match all three frozen self anchors exactly.

## Top-level manifest object

All fields are required. No unlisted field is allowed.

| Field | JSON type | Contract |
| --- | --- | --- |
| `contract_name` | string | Literal `SPORTS_IDENTITY_BRIDGE`. |
| `contract_version` | string | Literal `SPORTS_IDENTITY_BRIDGE_V1`. |
| `canonicalization_version` | string | Literal `SPORTS_CANONICAL_JSON_V1`. |
| `bridge_manifest_id` | string | Derived as `sports-identity-bridge-manifest:sha256:<D>`. |
| `bridge_manifest_sha256` | string | Lowercase semantic digest `D`. It is not the byte hash of the persisted manifest. |
| `bridge_package_id` | string | Derived as `sha256-<D>`. |
| `publisher` | string | Literal `AI Sports Predictor`. |
| `publication_timestamp` | date-time string | Must be timezone-aware. The publisher uses UTC; this field is excluded from semantic identity. |
| `sport` | string | Literal `Baseball`. |
| `league` | string | Literal `MLB`. |
| `parent_binding` | object | Exact `ParentDatasetBinding` described below. |
| `evidence_cutoff` | string | Exact ISO date `2026-08-15`. |
| `source_lineage` | object | Governed evidence artifacts, source releases, code revisions, and zero-mutation/network boundary. |
| `identity_namespaces` | array | Present namespace summaries in canonical allowed-namespace order; `RETROSHEET` is mandatory. |
| `bridge_files` | array | The exact four ordered semantic files. |
| `coverage_evidence` | object | Governed and portable population/reference reconciliation. |
| `join_contracts` | array | Unique contracts sorted by `join_contract_id`. |
| `join_validation` | array | Unique validation records covering every and only declared contract. |
| `y_minus_1_prior_eligibility` | array | Exactly six ordered target-season/role records. |
| `source_rights` | object | Rights IDs and package-level controls. |
| `limitations` | array | Unique limitations sorted by `limitation_id`. The governed-versus-portable gap must be disclosed by publication. |
| `totals` | object | File, row, contract, byte, and row-authority reconciliation. |

### `ParentDatasetBinding`

| Field | Type | Rule |
| --- | --- | --- |
| `parent_dataset_id` | string | Exact frozen parent dataset ID. |
| `parent_dataset_version_id` | string | Exact frozen digest-derived version ID. |
| `parent_dataset_manifest_id` | string | Exact frozen manifest ID. |
| `parent_dataset_manifest_sha256` | string | Exact frozen manifest semantic digest. |
| `parent_partition_authority_sha256` | string | Exact frozen 329-partition authority. |
| `parent_package_id` | string | Exact frozen package ID. |
| `plate_appearance_class_order` | array of strings | Exact eight-value order above. |

### `SourceLineage` and evidence identities

`ArtifactIdentity` fields are `artifact_id`, `logical_uri`, `sha256`, and a
positive `byte_count`. IDs are portable identifiers, URIs are portable relative
logical identities, and SHA-256 values are lowercase 64-hex. Evidence artifacts
are metadata identities; their logical URIs do not assert that source evidence
is copied into the seven-file package. Artifact IDs and case-folded URIs must be
unique.

`SourceRelease` fields are `source_key`, canonical lowercase UUID
`source_version_id`, and lowercase SHA-256 `identity_checksum`. Release pairs
are unique and sorted by source key/version.

`SourceLineage` contains:

| Field | Type | Rule |
| --- | --- | --- |
| `canonical_identity_mapping_count` | positive integer | Must equal coverage evidence; governed value is 736,056. |
| `evidence_artifacts` | array | Structurally at least two unique records; pre-freeze package validation requires the exact ordered three-artifact authority below. |
| `source_releases` | array | At least one structurally; the frozen publication carries all five exact governed releases above in canonical order. |
| `extraction_revision` | string | Lowercase SHA-256 of the extraction implementation. |
| `publishing_code_revision` | string | Lowercase SHA-256 of the bridge publishing implementation inventory, excluding only release-time `release_trust.py`. |
| `database_transaction_mode` | string | Literal `READ_ONLY`. |
| `database_writes` | integer | Literal 0. |
| `provider_network_calls` | integer | Literal 0. |
| `external_network_calls` | integer | Literal 0. |

The exact ordered governed-evidence authority is:

| Artifact ID | Logical URI | SHA-256 | Bytes |
| --- | --- | --- | ---: |
| `active_identity` | `governed-evidence/active-identity/sha256-0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json` | `0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07` | 54,046 |
| `final_result` | `governed-evidence/finalized-recovery/sha256-08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json` | `08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720` | 33,302 |
| `quarantine_impact` | `governed-evidence/quarantine-impact/sha256-cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json` | `cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b` | 2,140,874 |

The source adapter is pinned to database migration `20260815_0015`, the one
finalized FD1 FULL recovery run, 329 manifest files, 737,259 identity-stage rows,
736,056 promoted canonical mapping rows, and 1,203 quarantined rows: 1,187
unresolved and 16 source-declared conflicts, with zero blocking conflicts. It
uses a PostgreSQL transaction explicitly marked read-only, rejects mutating SQL,
and rolls back on every exit path.

The exact historical source-release authority is:

| Source key | Source version ID | Dataset version | Identity checksum |
| --- | --- | --- | --- |
| `CHADWICK_REGISTER_ARCHIVES` | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | `2e8e73355f9c77b963115377bd98c784cfeec10f` | `8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0` |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | `retrosheet_through_2025` | `47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e` |
| `RETROSHEET_CSV_DOWNLOADS` | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | `retrosheet_through_2025` | `d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52` |
| `RETROSHEET_EVENT_ARCHIVES` | `9a08b5fd-e5ed-4043-a984-04021584f15e` | `retrosheet_through_2025` | `2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8` |
| `SABR_LAHMAN_BOX_DOWNLOADS` | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | `lahman_1871-2025_csv` | `61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5` |

The frozen FULL identity stage is validated against its governed source set,
including Retrosheet auxiliary evidence. Published person-alias rows themselves
are contributed only by `CHADWICK_REGISTER_ARCHIVES` (Retrosheet, Chadwick,
MLBAM, and Baseball-Reference namespaces) and
`SABR_LAHMAN_BOX_DOWNLOADS` (Lahman). The remaining releases bind parent event,
auxiliary, and lineage evidence. Package-only validation pins all five lineage
releases and the exact two alias-contributing releases without importing the
database extractor. A publisher never invents an alias namespace merely because
a source format could contain one.

## Namespace authority

All aliases resolve to the canonical source namespace `chadwick_uuid`. The
manifest uses the following uppercase portable names, in this order when
present:

| Manifest namespace | Governed source namespace | Parent use |
| --- | --- | --- |
| `RETROSHEET` | `retrosheet` | Required. Exact join from player-bearing Retrosheet parent fields through `parent_join_key`. |
| `LAHMAN` | `lahman` | Exact Lahman identifier lineage and the source identity carried by `player_prior`. |
| `CHADWICK` | `chadwick_person` | Chadwick Persons Register identity authority. |
| `MLBAM` | `mlbam` | Exact promoted crosswalk alias; never inferred. Its absence fails frozen-release validation. |
| `BASEBALL_REFERENCE` | `baseball_reference` | Exact promoted crosswalk alias; never inferred. Its absence fails frozen-release validation. |

Each `IdentityNamespace` has `namespace`, sorted unique nonempty `source_keys`,
sorted unique canonical UUID `source_version_ids`, positive `alias_rows`,
`unique_source_ids`, and `canonical_players`. V1 requires exactly one semantic
alias row per namespace/source ID, so `alias_rows == unique_source_ids`, while
`canonical_players <= alias_rows`. Namespace names are unique. The structural
manifest model requires `RETROSHEET`; the stronger frozen package validator
requires all five namespaces, each with positive observed aliases, in the exact
order above. A missing governed namespace fails publication rather than being
invented or represented by a zero-row record.

## Semantic files and seven-file layout

The final package has exactly seven regular, independent files and no extra
file, directory, case-colliding path, link, junction, reparse point, or hardlink.

| Logical URI | Role | Semantic digest member |
| --- | --- | --- |
| `sports-identity-bridge-manifest.json` | Self-describing canonical manifest. Its semantic projection binds the four files below. | The manifest content defines `D`; it is outside `bridge_files` to avoid a file-digest cycle. |
| `active-player-dimension.parquet` | One row per portable parent canonical player UUID. | Yes |
| `attribution.md` | Deterministic applicable attribution text. | Yes |
| `player-identity-aliases.parquet` | Exact source-ID-to-canonical-UUID aliases. | Yes |
| `source-rights.json` | Deterministic applicable parent-rights projection. | Yes |
| `README.md` | Deterministic consumer instructions and validation-report byte digest. | No |
| `validation-report.json` | Canonical, deterministic independent-validation result. | No |

The `bridge_files` array is exactly the four semantic rows shown above in
lexical URI order. Every row has a portable `logical_uri`, exact `media_type`,
lowercase file-byte `sha256`, positive `byte_count`, and literal
`semantic_digest_member: true`. Parquet rows additionally require positive
`row_count`, `physical_schema_fingerprint`, and `row_authority_sha256`.
Non-Parquet rows must set those three fields to JSON null.

`totals.semantic_file_bytes` is the sum of the four file byte counts.
`totals.alias_rows` and `totals.active_player_rows` equal the respective Parquet
row counts. The two `totals.*_authority_sha256` values equal the corresponding
`bridge_files` authorities. The remaining totals reconcile the actual namespace,
join-contract, join-validation, eligibility, semantic-file, and final-package
inventories; `semantic_file_count` is 4,
`y_minus_1_eligibility_records` is 6, and `final_package_file_count` is 7.

## `player-identity-aliases.parquet`

The physical schema and field order are exact:

| Ordinal | Field | Polars type / nullability | Rule |
| ---: | --- | --- | --- |
| 1 | `canonical_player_uuid` | `String`, non-null | Canonical lowercase UUID in the `chadwick_uuid` spine. |
| 2 | `namespace` | `String`, non-null | One of the five manifest namespaces. |
| 3 | `source_player_id` | `String`, non-null | Trimmed, nonempty exact source identifier. |
| 4 | `parent_join_key` | `String`, nullable | Retrosheet: `retrosheet:player:<source_player_id>`. Lahman: raw `source_player_id`. All other namespaces: null. |
| 5 | `source_key` | `String`, non-null | Governed source-release key. |
| 6 | `source_version_id` | `String`, non-null | Canonical lowercase source-version UUID. |
| 7 | `source_version_identity_sha256` | `String`, non-null | Lowercase source-release identity digest. |
| 8 | `mapping_confidence` | `String`, non-null | `EXACT` or `HIGH` only. |
| 9 | `mapping_status` | `String`, non-null | Literal `RESOLVED`. |
| 10 | `mapping_basis` | `String`, non-null | Trimmed evidence basis; must not contain `NAME`, `FUZZY`, `LEVENSHTEIN`, `BIRTH_DATE`, or `PROBABILISTIC` variants. |
| 11 | `first_represented_season` | `Int64`, non-null | 2021–2025 inclusive and no later than `last_represented_season`. |
| 12 | `last_represented_season` | `Int64`, non-null | 2021–2025 inclusive. |
| 13 | `primary_2023_2025` | `Boolean`, non-null | Whether the alias is observed in actual parent event-player evidence for 2023–2025; prior-only rows do not make it primary. |
| 14 | `source_row_number` | `Int64`, non-null | Positive source-row ordinal. |
| 15 | `source_row_sha256` | `String`, non-null | Lowercase source-row digest. |
| 16 | `source_row_identity` | `String`, non-null | Exactly `sha256:<source_row_sha256>`. |
| 17 | `evidence_logical_uri` | `String`, non-null | Portable relative evidence URI. |
| 18 | `mapping_identity_sha256` | `String`, non-null | Unique lowercase promoted-mapping identity digest. |
| 19 | `row_sha256` | `String`, non-null | Canonical row digest defined below. |

The represented-season bounds are the minimum and maximum seasons actually
observed for the canonical player across portable parent event-player evidence
and governed `player_prior` evidence. They summarize observations; they do not
assert that every intervening season is present.

The natural key `(namespace, source_player_id)` is unique and maps to one and
only one `canonical_player_uuid`. Duplicate semantic aliases, duplicate mapping
identities, unresolved rows, ambiguous rows, one alias mapped to multiple UUIDs,
invalid UUIDs, unsupported namespaces, and name/fuzzy bases are fatal.

Rows are sorted by
`(namespace, source_player_id, canonical_player_uuid, source_version_id,
source_row_number)` before serialization and must remain in that order.

## `active-player-dimension.parquet`

The physical schema and field order are exact:

| Ordinal | Field | Polars type / nullability | Rule |
| ---: | --- | --- | --- |
| 1 | `canonical_player_uuid` | `String`, non-null | Unique canonical lowercase UUID. |
| 2 | `first_represented_season` | `Int64`, non-null | 2021–2025 inclusive, not after last represented season. |
| 3 | `last_represented_season` | `Int64`, non-null | 2021–2025 inclusive. |
| 4 | `prior_history_2021_2022` | `Boolean`, non-null | Whether actual parent `player_prior` evidence exists for evidence season 2021 or 2022; an interval overlap alone is insufficient. |
| 5 | `primary_active_2023_2025` | `Boolean`, non-null | Whether the UUID is observed in actual parent event-player evidence for 2023–2025; prior-only rows do not make it primary. |
| 6 | `has_retrosheet_alias` | `Boolean`, non-null | Exact namespace-presence indicator. |
| 7 | `has_lahman_alias` | `Boolean`, non-null | Exact namespace-presence indicator. |
| 8 | `has_chadwick_alias` | `Boolean`, non-null | Exact namespace-presence indicator. |
| 9 | `has_mlbam_alias` | `Boolean`, non-null | Exact namespace-presence indicator. |
| 10 | `has_baseball_reference_alias` | `Boolean`, non-null | Exact namespace-presence indicator. |
| 11 | `has_player_prior` | `Boolean`, non-null | Whether a governed parent `player_prior` row exists. |
| 12 | `minimum_player_prior_target_season` | `Int64`, nullable | Non-null 2021–2026 only when `has_player_prior`; no later than maximum. |
| 13 | `maximum_player_prior_target_season` | `Int64`, nullable | Non-null 2021–2026 only when `has_player_prior`. |
| 14 | `batter_reference_count` | `Int64`, non-null | Nonnegative portable parent reference count. |
| 15 | `pitcher_reference_count` | `Int64`, non-null | Nonnegative portable parent reference count. |
| 16 | `other_player_reference_count` | `Int64`, non-null | Nonnegative portable parent reference count. |
| 17 | `player_prior_row_count` | `Int64`, non-null | Positive iff `has_player_prior`; otherwise zero. |
| 18 | `row_sha256` | `String`, non-null | Canonical row digest defined below. |

The dimension uses the same actual-observation represented-season bounds as the
alias rows for that UUID. Primary status still comes only from event-player
evidence, and prior-history status still comes only from prior rows.

When `has_player_prior` is false, both target-season bounds are null and
`player_prior_row_count` is zero. Canonical UUIDs are unique and rows are sorted
ascending by `canonical_player_uuid`.

### Row hashes, schema fingerprints, authorities, and Parquet bytes

For either row type:

1. Copy the row and remove `row_sha256`.
2. Encode the remaining object with `SPORTS_CANONICAL_JSON_V1` without a final
   newline.
3. Set `row_sha256` to the lowercase SHA-256 of those bytes.

The physical schema fingerprint is the canonical SHA-256 of an array of
`{"name": <field>, "dtype": <Polars dtype string>}` objects in the exact field
order above. The row-authority SHA-256 is the canonical SHA-256 of the complete
array of canonically sorted rows, including each `row_sha256`.

The file SHA-256 is separately computed over the exact Parquet bytes. The
deterministic writer uses Zstandard compression level 9, full statistics,
8,192-row groups, and 1,048,576-byte data pages. Validation rejects unreadable or
tampered Parquet, a changed field order or type, changed row order, row-hash
tampering, changed schema fingerprint, changed row authority, or changed file
digest.

These are separate authorities:

| Authority | Projection |
| --- | --- |
| `row_sha256` | One semantic row without its self-digest. |
| `row_authority_sha256` | Ordered full semantic row array, including row hashes. |
| `physical_schema_fingerprint` | Ordered physical field/type array. |
| `bridge_files[].sha256` | Exact serialized file bytes. |
| `bridge_manifest_sha256` | Canonical semantic manifest projection, including the declared file authorities. |

## Exact consumer joins

Every Retrosheet event-family join filters
`player-identity-aliases.parquet` to `namespace == "RETROSHEET"` and joins the
parent physical identifier exactly to alias `parent_join_key`. The result is
`canonical_player_uuid`. No case folding, trimming, name matching, fuzzy
matching, fallback namespace, or implicit prefix conversion is allowed at
consumption time.

The parent scanner validates the following minimum physical player-field
inventory. It is intentionally broader than the manifest's consumer join
contracts because it also establishes population/reference evidence and exact
raw-mirror behavior:

| Source family | Physical field | Shape | Required | Raw mirror |
| --- | --- | --- | --- | --- |
| `pitch_sequence` | `batter_id` | scalar | yes | — |
| `pitch_sequence` | `pitcher_id` | scalar | yes | — |
| `pitch_sequence` | `automatic_runner_player_id` | scalar | no | `retrosheet_automatic_runner_id` |
| `plate_appearance` | `batter_id` | scalar | yes | — |
| `plate_appearance` | `pitcher_id` | scalar | yes | — |
| `plate_appearance` | `automatic_runner_player_id` | scalar | no | `retrosheet_automatic_runner_id` |
| `play_transition` | `batter_id` | scalar | yes | — |
| `play_transition` | `pitcher_id` | scalar | yes | — |
| `play_transition` | `automatic_runner_player_id` | scalar | no | `retrosheet_automatic_runner_id` |
| `play_transition` | `lineup_player_ids` | list of exactly 9 | yes | `retrosheet_lineup_slots` |
| `play_transition` | `runner_on_first_before` | scalar | no | — |
| `play_transition` | `runner_on_second_before` | scalar | no | — |
| `play_transition` | `runner_on_third_before` | scalar | no | — |
| `play_transition` | `runner_on_first_after` | scalar | no | — |
| `play_transition` | `runner_on_second_after` | scalar | no | — |
| `play_transition` | `runner_on_third_after` | scalar | no | — |
| `player_game_batting` | `player_id` | scalar | yes | `retrosheet_player_id` |
| `player_game_fielding` | `player_id` | scalar | yes | `retrosheet_player_id` |
| `player_game_pitching` | `player_id` | scalar | yes | `retrosheet_player_id` |
| `starter_usage` | `player_id` | scalar | yes | `retrosheet_player_id` |
| `bullpen_usage` | `player_id` | scalar | yes | `retrosheet_player_id` |

The manifest publishes exactly ten sorted join contracts. The exact contract
IDs and join surfaces are:

| `join_contract_id` | Namespace | Source field | Target field |
| --- | --- | --- | --- |
| `bullpen_usage.player_id.retrosheet-to-canonical` | `RETROSHEET` | `bullpen_usage.player_id` | `player-identity-aliases.canonical_player_uuid` |
| `pitch_sequence.batter_id.retrosheet-to-canonical` | `RETROSHEET` | `pitch_sequence.batter_id` | `player-identity-aliases.canonical_player_uuid` |
| `pitch_sequence.pitcher_id.retrosheet-to-canonical` | `RETROSHEET` | `pitch_sequence.pitcher_id` | `player-identity-aliases.canonical_player_uuid` |
| `plate_appearance.batter_id.retrosheet-to-canonical` | `RETROSHEET` | `plate_appearance.batter_id` | `player-identity-aliases.canonical_player_uuid` |
| `plate_appearance.pitcher_id.retrosheet-to-canonical` | `RETROSHEET` | `plate_appearance.pitcher_id` | `player-identity-aliases.canonical_player_uuid` |
| `player_game_batting.player_id.retrosheet-to-canonical` | `RETROSHEET` | `player_game_batting.player_id` | `player-identity-aliases.canonical_player_uuid` |
| `player_game_fielding.player_id.retrosheet-to-canonical` | `RETROSHEET` | `player_game_fielding.player_id` | `player-identity-aliases.canonical_player_uuid` |
| `player_game_pitching.player_id.retrosheet-to-canonical` | `RETROSHEET` | `player_game_pitching.player_id` | `player-identity-aliases.canonical_player_uuid` |
| `player_prior.canonical_player_id.canonical-to-dimension` | `CHADWICK` | `player_prior.canonical_player_id` | `active-player-dimension.canonical_player_uuid` |
| `starter_usage.player_id.retrosheet-to-canonical` | `RETROSHEET` | `starter_usage.player_id` | `player-identity-aliases.canonical_player_uuid` |

For list fields, the contract applies per non-null element. Optional nulls are
excluded according to the field inventory before join-reference validation; an
evaluated reference may never be null or blank. A declared raw mirror must
encode the same underlying Retrosheet identifier as the normalized field. The
exact event-family manifest policy values are:

| Policy field | Required value |
| --- | --- |
| `namespace` | `RETROSHEET` |
| `target_canonical_field` | `player-identity-aliases.canonical_player_uuid` |
| `join_type` | `EXACT_PARENT_JOIN_KEY_TO_ALIAS` |
| `null_policy` | `REJECT_NULL_OR_BLANK` |
| `duplicate_policy` | `REQUIRE_ONE_TARGET_PER_NAMESPACE_SOURCE_ID` |
| `unresolved_policy` | `FAIL_CLOSED` |

The parent `player_prior` family is already canonical. Join
`player_prior.canonical_player_id` directly to
`active-player-dimension.parquet.canonical_player_uuid`. Its exact policy values
are `join_type = EXACT_CANONICAL_UUID`,
`null_policy = REJECT_NULL_OR_BLANK`,
`duplicate_policy = ALLOW_MULTIPLE_PRIOR_ROWS_PER_CANONICAL_UUID`, and
`unresolved_policy = FAIL_CLOSED`. Multiple prior evidence rows may target one
unique dimension UUID; duplicate dimension UUIDs remain forbidden. Null or
unresolved canonical IDs are rejected. `lahman_player_id` remains source lineage
and is not the canonical consumer join.

A `JoinContract` record contains `join_contract_id`, `source_family`,
`physical_field_name`, `namespace`, `target_canonical_field`, `join_type`,
`null_policy`, `duplicate_policy`, and `unresolved_policy`. IDs are unique,
portable, and sorted. A `JoinValidation` record contains the corresponding ID;
sorted unique seasons within 2021–2025; positive `reference_count`; equal
`resolved_reference_count`; literal-zero unresolved, ambiguous, and multiply
mapped counts; positive unique source/canonical counts; and `mapping_rate`. The
rate equals resolved divided by total and must be 1.0 within `1e-12`. Every and
only declared contract has validation.

The nonduplicating physical reference denominator uses only
`play_transition` batter, pitcher, lineup, and six runner-state fields plus the
three `player_game_*` `player_id` fields. It excludes automatic-runner mirrors
and the derived `pitch_sequence`, `plate_appearance`, `starter_usage`, and
`bullpen_usage` families to avoid double counting. Those excluded families
remain mandatory join-validation surfaces.

## Y-1 prior eligibility

The `y_minus_1_prior_eligibility` array contains exactly these six keys, in this
order:

1. `(2023, BATTER)`
2. `(2023, PITCHER)`
3. `(2024, BATTER)`
4. `(2024, PITCHER)`
5. `(2025, BATTER)`
6. `(2025, PITCHER)`

`role` has only the domains `BATTER` and `PITCHER`. Governed `player_prior`
source domains are `BATTING`, `FIELDING`, and `PITCHING`, but the eligibility
projection is exact: `BATTER` uses `BATTING` only and `PITCHER` uses `PITCHING`
only. `FIELDING` remains governed prior evidence but does not enter either of
the six role eligibility records. Identity resolution is independent of whether
a role has historical evidence.

Each record has positive `reference_rows`; nonnegative
`rows_with_eligible_prior` and `legitimate_missing_prior_rows` that sum to the
reference total; `maximum_eligible_prior_season == target_season - 1`; and
`eligible_prior_rate == rows_with_eligible_prior / reference_rows` within
`1e-12`. `unresolved_identity_rows`, `ambiguous_identity_rows`, and
`multiply_mapped_identity_rows` are literal zero.

A parent prior row has `eligible_from_season == season + 1` and is usable only
when its evidence season is no later than target Y-1 and its eligibility boundary
has been reached. Same-season predictor use is forbidden. A rookie, new player,
or player without an eligible governed prior is counted in
`legitimate_missing_prior_rows`; that is not an identity-resolution failure.

## Coverage, rights, limitations, and totals

`CoverageEvidence` contains:

| Field group | Required reconciliation |
| --- | --- |
| `canonical_identity_mapping_count` | Positive and equal to `source_lineage`. |
| Governed unique players | 2,693 for 2021–2025 and 2,071 for 2023–2025. |
| Portable unique players | 2,692 and 2,070, with one unrepresented governed player in each window. |
| Governed references | 21,356,559 and 12,838,083. |
| Portable references | 12,411,678 and 7,460,143 nonduplicating physical references. |
| Four exact rates | Each exactly 1.0 within `1e-12`. |
| Quarantine counts | Nonnegative unresolved and ambiguous evidence counts; these are source evidence, not accepted bridge rows. |
| `downstream_impacted_rows` | Literal 0. |

`SourceRights` fields are sorted unique `rights_control_ids`, exact
`allowed_purposes` and `prohibited_purposes` arrays, literal
`NO_NEW_LEGAL_CONCLUSIONS`, and four literal-false authorization flags for
public raw download, public raw redistribution, identity-data resale, and
unrestricted commercial redistribution.

The applicable rights-control IDs are exactly
`CHADWICK_PERSONS_REGISTER`, `RETROSHEET`, and
`SABR_LAHMAN_MODERN_MLB`. `source-rights.json` is a deterministic projection of
those records from the already-validated parent `SPORTS_DATASET_SOURCE_RIGHTS_V1`
artifact; `attribution.md` preserves the parent Retrosheet notice, Chadwick
ODC-By attribution, and SABR Lahman CC BY-SA 3.0 attribution byte-for-byte where
required. The bridge makes no new legal conclusion.

Allowed purposes, in exact order, are:

1. `INTERNAL_DERIVED_ANALYTICS`
2. `LOCAL_PRIVATE_MODEL_DEVELOPMENT`
3. `READ_ONLY_REGISTRATION`

Prohibited purposes, in exact order, are:

1. `IDENTITY_DATA_RESALE`
2. `PUBLIC_BULK_DOWNLOAD`
3. `PUBLIC_RAW_BRIDGE_DOWNLOAD`
4. `PUBLIC_RAW_REDISTRIBUTION`
5. `UNRESTRICTED_COMMERCIAL_REDISTRIBUTION`

Each `Limitation` has a unique sorted portable `limitation_id`, nonempty trimmed
`description`, and literal scope `IDENTITY_BRIDGE`. Limitations disclose facts;
they do not weaken any digest, identity, join, security, or rights invariant.

The frozen publication carries exactly these sorted limitation IDs:

| Limitation ID | Required rationale |
| --- | --- |
| `NO_NAMES_OR_FUZZY_MATCHING` | Names, normalized names, fuzzy matching, probabilistic matching, and invented aliases are outside this contract. |
| `PARENT_PHYSICAL_SCOPE_OMITS_ONE_GOVERNED_ROSTER_IDENTITY` | The finalized upstream active audit includes one roster-only identity that is not enumerated in any immutable parent Parquet field or allowed finalized evidence artifact; the portable bridge therefore contains 2,692 physical-parent players while preserving the governed 2,693 count. |
| `PUBLIC_RAW_DISTRIBUTION_NOT_AUTHORIZED` | This package does not authorize public raw bridge distribution or resale. |
| `ROOKIE_MISSING_PRIOR_IS_VALID` | An exact identity with no eligible earlier-domain prior is a legitimate missing-prior case, not an identity failure. |
| `Y_MINUS_1_ONLY` | Player-prior evidence is eligible only for target season Y when its source season is Y-1 or earlier. |

`BridgeTotals` contains positive `alias_rows`, `active_player_rows`,
`namespace_count`, `join_contract_count`, `join_validation_count`, and
`semantic_file_bytes`; literal counts 6, 4, and 7 for eligibility, semantic
files, and final files; and lowercase alias and dimension authority digests.
All values reconcile against the corresponding arrays and files.

## Canonical JSON and semantic bridge identity

`SPORTS_CANONICAL_JSON_V1` is UTF-8 JSON with:

- keys sorted recursively;
- compact separators with no insignificant whitespace;
- Unicode preserved rather than ASCII-escaped;
- no NaN or infinity; and
- exactly one final LF for persisted JSON files.

The canonical loader rejects unreadable UTF-8, duplicate object keys, nonfinite
numbers, any noncanonical key or whitespace encoding, and any final-newline
difference.

To compute bridge digest `D`:

1. Deep-copy the complete manifest object.
2. Remove exactly `bridge_manifest_id`, `bridge_manifest_sha256`,
   `bridge_package_id`, and `publication_timestamp`.
3. Canonically encode the remaining object without a trailing newline.
4. Compute lowercase SHA-256 `D`.
5. Require:
   - `bridge_manifest_sha256 = D`;
   - `bridge_manifest_id = sports-identity-bridge-manifest:sha256:D`; and
   - `bridge_package_id = sha256-D`.

No other field is excluded. Array order is semantic. In particular, source
lineage, namespaces, semantic file byte digests, schema fingerprints, row
authorities, coverage evidence, join evidence, Y-1 evidence, rights,
limitations, totals, and publishing code revision all contribute to `D`.
Changing only the timezone-aware publication timestamp does not change `D`.

The persisted manifest byte SHA-256 can differ from `D` because the file contains
the digest-derived identifiers and final newline. Consumers must not substitute
one digest for the other.

## Security and portability

Publishing and validation fail closed unless the package is an exact,
link-free, local tree. Logical URIs are normalized portable relative paths:
absolute paths, drive-qualified paths, UNC paths, `..` escapes, backslashes,
control characters, and case collisions are forbidden. Package files must be
regular independent files, not symlinks, junctions, reparse points, or
hardlinks.

The complete file inventory and decoded row values are scanned. Forbidden
content includes:

- `.env`, credential files, private keys, database dumps/databases, and logs;
- database connection strings and assignments such as `DATABASE_URL`,
  `PASSWORD`, `API_KEY`, `ACCESS_TOKEN`, or `SECRET_KEY`;
- private-key headers, GitHub tokens, AWS access-key markers, absolute Windows,
  UNC, Unix-home, `file://`, ASP runtime, or curated-data paths; and
- raw source archives, local runtime identities, credentials, forecasts,
  sportsbook data, or user data.

The package carries no executable SQL and the standalone validator performs no
database, provider, or external-network access.

## Canonical command-line interfaces

Run from the `server` directory (or another environment where `app` is
importable). The top-level wrappers are the canonical interfaces even if the
internal `app.gen2.exchange.identity_bridge.publisher` and
`app.gen2.exchange.identity_bridge.validator` modules are also executable.

Publisher:

~~~text
python -m app.gen2.exchange.identity_bridge_publisher \
  --output-root <immutable-output-root> \
  --parent-package-root <validated-parent-package> \
  --heartbeat-seconds <positive-seconds> \
  --max-runtime-minutes <positive-minutes>
~~~

| Flag | Meaning |
| --- | --- |
| `--output-root` | Governed root beneath which the content-addressed bridge package is finalized. |
| `--parent-package-root` | Exact frozen MLB FD1 package to validate and scan. |
| `--resume` | Resume only a compatible existing checkpoint/staging package; it fails if no staging exists. Staged identity and bytes remain subject to full validation. |
| `--validate-only` | Validate the already-finalized content-addressed package without extraction or writes. It requires a frozen bridge-manifest trust anchor and fails if that exact package is absent. |
| `--heartbeat-seconds` | Progress/heartbeat interval from 1 through 30 seconds; default 30. |
| `--max-runtime-minutes` | Positive safe runtime bound; default 90. |
| `--staging-root` | Optional isolated staging root for tests or controlled runs. |

Validator:

~~~text
python -m app.gen2.exchange.identity_bridge_validator \
  --bridge-package-root <bridge-package> \
  --parent-package-root <validated-parent-package> \
  --heartbeat-seconds <positive-seconds> \
  --max-runtime-minutes <positive-minutes>
~~~

| Flag | Meaning |
| --- | --- |
| `--bridge-package-root` | Exact seven-file bridge package to validate. |
| `--parent-package-root` | Exact parent package to validate independently and use for package-only join checks. |
| `--heartbeat-seconds` | Progress/heartbeat interval from 1 through 30 seconds; default 30. |
| `--max-runtime-minutes` | Positive safe runtime bound; default 90. |

Publisher staging is private and atomic. Finalization occurs only after complete
independent validation. An existing content-addressed destination is idempotent
only when its inventory and bytes validate as the same package; any collision or
partial/different content fails closed. `--resume` never converts incompatible
staging into a valid release. The final path is
`<output-root>/identity-bridges/mlb-fd1/manifest-sha256-<parent-manifest-sha256>/sha256-<bridge-manifest-sha256>`.
Publication creates no mutable `latest` alias, pointer, directory, or index;
consumers address the finalized package by its content-derived package ID. A
deadline aborts before finalization and reports typed progress.

The publisher's operational result is `FINALIZED` for a new atomic release or
`REUSED` for a fully validated frozen package. The validator result is `PASS`.
Deadline exits use code 75 and include progress/resume information; invalid or
failed execution uses code 2. The Gate 3C-X2 governance disposition is
`IDENTITY_BRIDGE_FINALIZED_WITH_DOCUMENTED_LIMITATIONS`, not an alternative
publisher status: the exact package is valid while its manifest explicitly
preserves the unobservable one-player upstream/portable gap and the other
enumerated limitations.

## Required fail-closed checks

A successful exit requires all applicable checks below. Any exception,
mismatch, timeout, unexpected file, or unverifiable fact produces a nonzero
result and no newly finalized package.

1. The full parent FD1 validator succeeds, and every frozen parent identity,
   partition, class-order, family, schema, source, and `player_prior` anchor
   matches.
2. During publication, the exact finalized source authority is read in an
   explicitly read-only PostgreSQL transaction, no provider/network call or
   write occurs, and the transaction is rolled back.
3. The manifest is unique-key canonical JSON, matches the strict V1 schema and
   semantic model, has no missing or extra field, has a timezone-aware
   publication timestamp, and has correct digest-derived identifiers.
4. The final inventory is exactly seven portable regular independent files; the
   semantic inventory is exactly four ordered files; every byte count, file
   digest, row count, schema fingerprint, row authority, and aggregate total
   reconciles.
5. Alias and dimension Parquet schemas, row hashes, sorting, natural keys,
   mapping identities, UUIDs, namespace rules, parent join keys, flags, prior
   nullability, and reference counts satisfy their exact contracts.
6. Every required parent player field and every non-null optional field resolves
   exactly once. Every join-validation rate is 1.0 with zero unresolved,
   ambiguous, or multiply mapped references.
7. All six Y-1 role records reconcile, never use same-season evidence, and
   distinguish legitimate missing history from identity failure.
8. The rights and attribution files equal deterministic projections from the
   validated parent, exact package controls remain restrictive, and no new legal
   conclusion is introduced.
9. Security/path scanning finds no secret, credential, local path, forbidden
   file type, link, collision, unexpected content, database export, raw source,
   forecast, sportsbook, or user data.
10. The frozen release-time bridge, alias-row, and dimension-row trust anchors
    must all match exactly.

A JSON-Schema-only success is insufficient because Draft 2020-12 cannot express
cross-record ordering, exact parent anchors, digest algorithms, byte equality,
join completeness, row-hash authority, aggregate reconciliation, filesystem
safety, or deterministic rights projection.

## JSON Schema synchronization

`SPORTS_IDENTITY_BRIDGE_V1.schema.json` is generated directly from
`manifest_json_schema()`, with recursive key sorting, two-space indentation,
UTF-8, no NaN, and exactly one final LF. Its `$schema` is Draft 2020-12 and its
`$id` is
`https://ai-sports-predictor.local/contracts/SPORTS_IDENTITY_BRIDGE_V1.schema.json`.

Any implementation change that affects the generated schema requires
regeneration and a byte-for-byte synchronization check. The Markdown contract,
semantic Pydantic validation, package validator, and generated JSON Schema must
be reviewed together.
