# SPORTS_DATASET_MANIFEST_V1

Status: implemented Gate 3C-X1 consumer contract.

The normative implementation is `server/app/gen2/exchange/contracts.py`, with
identity rules in `canonical_json.py`, population and manifest projection in
`manifest_builder.py`, schema projection in `schema_registry.py`, and independent
package checks in `validator.py`. The generated Draft 2020-12 JSON Schema is
`SPORTS_DATASET_MANIFEST_V1.schema.json` in this directory.

This document specifies the contract and the content-addressing algorithm. It does
not claim that publication has run. No final runtime output root, package path,
publication timestamp, manifest digest, or digest-derived identifier is recorded
here.

## Fixed contract identity and corpus totals

| Item | Required value |
| --- | --- |
| Contract name | `SPORTS_DATASET_MANIFEST` |
| Contract version | `SPORTS_DATASET_MANIFEST_V1` |
| Canonicalization version | `SPORTS_CANONICAL_JSON_V1` |
| Supported consumer range | `>=1.0.0,<2.0.0` |
| Dataset ID | `asp.mlb.open-data.fd1` |
| Publisher | `AI Sports Predictor` |
| Sport / league | `Baseball` / `MLB` |
| Evidence cutoff | `2026-08-15` |
| Database migration | `20260815_0015` |
| Parquet files | 329 |
| Rows | 3,738,096 |
| Parquet bytes | 337,421,353 |
| Logical data families | 10 |
| Physical schema variants | 33 |
| Partition authority SHA-256 | `8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437` |
| Final package files | 335: 329 Parquet plus six metadata/auxiliary files |

Every Pydantic contract object is frozen and rejects extra fields. JSON arrays are
used for Python tuples. Unless a stronger rule is stated below, a `string` field is
structurally a string; consumers must not invent constraints absent from the V1
implementation. Runtime model validation uses strict JSON scalar types: strings
are not coerced into integers, numbers, or booleans, and booleans are not accepted
as integer substitutes.

## Frozen authority and fail-closed validation

The JSON Schema is the machine-readable structural companion, but JSON Schema
cannot express every cross-record and exact-corpus invariant in V1. The normative
semantic validator is `SportsDatasetManifestV1` in
`server/app/gen2/exchange/contracts.py`, followed by the package-only checks in
`server/app/gen2/exchange/validator.py`. A consumer that only validates against
the JSON Schema has not completed V1 validation.

V1 is a contract for one frozen FD1 corpus, not a generic manifest envelope. Its
offline trust anchors are compiled into the contract rather than inferred from
digest-derived fields that an attacker could recompute. Validation fails closed
unless all of the following agree exactly:

- top-level publisher, evidence cutoff, supported-consumer range, development
  purpose, dataset/sport/league identity, and the four digest-derived IDs;
- recovery run, semantic source-manifest checksum, migration, generation and
  finalizer revisions, four evidence-artifact identities, and five source-release
  identities;
- all five season records, three population policies and narratives, ten family
  authorities and semantics, 329 sorted partition records, identity evidence,
  six targets, temporal narratives/folds, data-quality values, twelve full
  limitation records, and six source-right IDs;
- exact aggregate reconciliation for partition, family, population, row, byte,
  date, target-eligibility, source-right, and schema evidence; and
- exactly the three semantic package artifacts in the documented order.

The compact offline trust anchor for the complete 329-record partition inventory
is SHA-256
`8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437`.
Its projection and purpose are defined in the partition section below.

The fixed execution revisions are:

| Authority | Exact value |
| --- | --- |
| Generation revision | `8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7` |
| Finalizer revision | `e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db` |
| Readiness operator revision | `b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766` |

`published_at` and `publishing_code_revision` are deliberately not fixed corpus
constants. The former is a timezone-aware publication fact excluded from semantic
identity. The latter is the content hash of the publishing implementation, is
included in semantic identity, and therefore causes changed exchange code to
produce a new package identity.

The exact source-evidence anchors are metadata identities only; these source files
are not copied into the package:

| Manifest field / artifact ID | Logical URI | SHA-256 | Bytes |
| --- | --- | --- | ---: |
| `finalized_evidence` | `source-evidence/finalized_evidence/sha256-08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json` | `08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720` | 33,302 |
| `readiness_evidence` | `source-evidence/readiness_evidence/sha256-9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76.json` | `9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76` | 90,754 |
| `active_identity_evidence` | `source-evidence/active_identity_evidence/sha256-0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json` | `0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07` | 54,046 |
| `quarantine_impact_evidence` | `source-evidence/quarantine_impact_evidence/sha256-cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json` | `cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b` | 2,140,874 |

The readiness binding note is also exact:

```text
The terminal readiness artifact binds the finalized pre-closeout source inventory at 28135 bytes / sha256 695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1; the committed closeout inventory is separately governed at 28216 bytes / sha256 6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f.
```

The exact sorted source-release authority is:

| Source key | Source version ID | Dataset version | Identity checksum |
| --- | --- | --- | --- |
| `CHADWICK_REGISTER_ARCHIVES` | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | `2e8e73355f9c77b963115377bd98c784cfeec10f` | `8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0` |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | `retrosheet_through_2025` | `47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e` |
| `RETROSHEET_CSV_DOWNLOADS` | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | `retrosheet_through_2025` | `d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52` |
| `RETROSHEET_EVENT_ARCHIVES` | `9a08b5fd-e5ed-4043-a984-04021584f15e` | `retrosheet_through_2025` | `2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8` |
| `SABR_LAHMAN_BOX_DOWNLOADS` | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | `lahman_1871-2025_csv` | `61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5` |

## Top-level manifest object

All fields in this table are required.

| Field | JSON type | Contract |
| --- | --- | --- |
| `contract_name` | string | Literal `SPORTS_DATASET_MANIFEST`. |
| `contract_version` | string | Literal `SPORTS_DATASET_MANIFEST_V1`. |
| `canonicalization_version` | string | Literal `SPORTS_CANONICAL_JSON_V1`. |
| `supported_consumer_range` | string | Exact V1 value `>=1.0.0,<2.0.0`. |
| `manifest_id` | string | Derived as `sports-dataset-manifest:sha256:<D>`. |
| `manifest_sha256` | string | Semantic digest `D`; this is not defined as the byte hash of `sports-dataset-manifest.json`. |
| `dataset_id` | string | Literal `asp.mlb.open-data.fd1`. |
| `dataset_version_id` | string | Derived as `asp.mlb.open-data.fd1:sha256:<D>`. |
| `package_id` | string | Derived as `sha256-<D>`. |
| `publisher` | string | Exact V1 value `AI Sports Predictor`. |
| `published_at` | RFC 3339 date-time | Must be timezone-aware. The publisher serializes UTC with a trailing `Z`. It is excluded from semantic identity. |
| `sport` | string | Literal `Baseball`. |
| `league` | string | Literal `MLB`. |
| `development_purpose` | string | `Portable read-only local/private model development, internal derived analytics, and future read-only registration.` |
| `evidence_cutoff` | string | Exact V1 value `2026-08-15`. |
| `source_lineage` | object | `SourceLineage`, defined below. |
| `seasons` | array | Five `SeasonCoverage` objects, ordered 2021 through 2025. |
| `populations` | array | Three `PopulationDefinition` objects in precedence order: `prior_only`, `enhanced_research`, `strict_deployable`. |
| `data_families` | array | Exactly ten `DataFamily` objects, sorted uniquely by `family_id`. |
| `partitions` | array | Exactly 329 `Partition` objects, sorted by `package_relative_uri`, with no case-folded URI collision. |
| `identity_coverage` | object | `IdentityCoverage`, defined below. |
| `target_definitions` | array | Six `TargetDefinition` objects in the implemented projection. |
| `temporal_contract` | object | `TemporalContract`, defined below. |
| `data_quality_evidence` | object | `DataQualityEvidence`, defined below. |
| `known_limitations` | array | Exactly the twelve ordered `Limitation` records listed below. |
| `source_rights_ids` | array | Six sorted, unique governed rights-control IDs listed below. |
| `package_artifacts` | array | Exactly three semantic `PackageArtifact` records: attribution, schema registry, and source rights, sorted by logical URI. |
| `totals` | object | Exactly `{"parquet_bytes":337421353,"parquet_files":329,"rows":3738096}`. |

The top-level semantic validator also requires the sum of partition rows and bytes
to equal `totals`, the set of partition families to equal the declared family set,
and `untouched_final_holdout_claimed` to be `false`.

## Nested objects

### Lineage and coverage objects

`DateRange`

| Field | Type | Rule |
| --- | --- | --- |
| `minimum` | string | Inclusive lower date value. |
| `maximum` | string | Inclusive upper date value; must compare lexically greater than or equal to `minimum`. |

`Coverage`

| Field | Type | Rule |
| --- | --- | --- |
| `covered` | integer | At least zero and no greater than `eligible`. |
| `eligible` | integer | At least zero. |
| `ratio` | number | In `[0,1]`; equals `covered / eligible` within `1e-12`, or exactly `1.0` when `eligible` is zero. |

`ArtifactIdentity`

| Field | Type | Rule |
| --- | --- | --- |
| `artifact_id` | string | Stable public evidence label. |
| `logical_uri` | string | Portable relative logical identity. It identifies source evidence but does not assert that the evidence file is copied into the exchange. |
| `sha256` | string | Lowercase 64-hex SHA-256. |
| `byte_count` | integer | Greater than zero. |

`SourceRelease`

| Field | Type | Rule |
| --- | --- | --- |
| `source_key` | string | Governed source-registry key. |
| `source_version_id` | string | Frozen source-version identifier. |
| `dataset_version` | string | Upstream version or revision. |
| `identity_checksum` | string | Lowercase 64-hex SHA-256. |

`SourceLineage`

| Field | Type | Contract |
| --- | --- | --- |
| `finalized_fd1_recovery_run_id` | string | Finalized recovery identity. |
| `source_semantic_manifest_checksum` | string | Exact bare SHA-256 `f9e788df4f746fe749e3cd731576bab3d191bea7559eb0b6c75230149589535c`. The nested type recognizes an optional `sha256:` prefix structurally, but the frozen V1 authority requires this exact spelling. |
| `finalized_evidence` | `ArtifactIdentity` | Content identity for final recovery evidence. |
| `readiness_evidence` | `ArtifactIdentity` | Content identity for terminal readiness evidence. |
| `active_identity_evidence` | `ArtifactIdentity` | Content identity for active-player identity coverage. |
| `quarantine_impact_evidence` | `ArtifactIdentity` | Content identity for quarantine impact evidence. |
| `generation_revision` | string | Exact bare SHA-256 in the frozen execution-revision table above. |
| `finalizer_revision` | string | Exact bare SHA-256 in the frozen execution-revision table above. |
| `publishing_code_revision` | string | SHA-256, structurally allowing a `sha256:` prefix; the publisher emits the bare digest after hashing sorted exchange Python paths and their exact bytes. |
| `database_migration` | string | Exact V1 value `20260815_0015`. |
| `source_releases` | array of `SourceRelease` | Sorted uniquely by `source_key`. |
| `readiness_inventory_binding_note` | string | Records that terminal readiness binds the pre-closeout inventory at 28,135 bytes / `695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1`, while the committed closeout inventory is separately governed at 28,216 bytes / `6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f`. |

`SeasonCoverage`

| Field | Type | Rule |
| --- | --- | --- |
| `season` | integer | 2021 through 2025. |
| `role` | string | `PRIOR_ONLY_HISTORY` for 2021-2022; `PRIMARY_GEN2_MODELING` for 2023-2025. |
| `games` | integer | Greater than zero. |
| `plate_appearances` | integer | Greater than zero. |
| `transitions` | integer | Greater than zero. |
| `starter_targets` | integer | Greater than zero. |
| `bullpen_appearances` | integer | Greater than zero. |
| `weather_coverage` | `Coverage` | Reconciled per-season coverage. |
| `complete_lineup_coverage` | `Coverage` | Reconciled per-season coverage. |
| `date_range` | `DateRange` | Bounds across manifested partitions for the season. |

The five records are exact corpus authority, not merely examples:

| Season | Role | Games | Plate appearances | Transitions | Starter targets | Bullpen appearances | Weather | Complete lineups | Manifest date range |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2021 | `PRIOR_ONLY_HISTORY` | 2,429 | 181,818 | 190,700 | 4,858 | 16,683 | 2,429 / 2,429 | 2,412 / 2,429 | 2021-04-01 through 2021-12-31 |
| 2022 | `PRIOR_ONLY_HISTORY` | 2,430 | 182,052 | 187,969 | 4,860 | 16,018 | 2,430 / 2,430 | 2,421 / 2,430 | 2022-04-07 through 2022-12-31 |
| 2023 | `PRIMARY_GEN2_MODELING` | 2,430 | 184,104 | 191,073 | 4,860 | 15,770 | 2,430 / 2,430 | 2,423 / 2,430 | 2023-03-30 through 2023-12-31 |
| 2024 | `PRIMARY_GEN2_MODELING` | 2,429 | 182,449 | 189,536 | 4,858 | 15,835 | 2,429 / 2,429 | 2,418 / 2,429 | 2024-03-20 through 2024-12-31 |
| 2025 | `PRIMARY_GEN2_MODELING` | 2,430 | 182,926 | 189,939 | 4,860 | 16,008 | 2,430 / 2,430 | 2,420 / 2,430 | 2025-03-18 through 2025-12-31 |

Weather and lineup cells show `covered / eligible`; each stored ratio must still
reconcile under the `Coverage` rule. The December 31 maxima reflect manifested
annual `player_prior` records, not a claim that MLB games were played on those
dates.

### Data, identity, target, and quality objects

`DataFamily`

| Field | Type | Contract |
| --- | --- | --- |
| `family_id` | string | One of the ten frozen family names. |
| `schema_id` | string | Frozen logical schema version. |
| `purpose` | string | Human-readable family purpose from the builder. |
| `target_feature_role` | array of strings | Governed target/feature roles. |
| `temporal_classification` | array of strings | Family-level temporal rules. |
| `population_eligibility` | array of strings | Populations in which family rows may appear. |
| `file_count` | integer | Greater than zero. |
| `row_count` | integer | Greater than zero. |
| `byte_count` | integer | Greater than zero. |
| `date_range` | `DateRange` | Family-wide bounds. |
| `source_key` | string | `RETROSHEET_CSV_DOWNLOADS` for nine families; `SABR_LAHMAN_BOX_DOWNLOADS` for `player_prior`. |
| `source_version_id` | string | Frozen source-version UUID. |
| `rights_control_ids` | array of strings | Applicable source-right controls. |
| `known_limitations` | array of strings | Family-specific limitations. |

`PhysicalField` has exactly `name: string` and `dtype: string`.

`Partition`

| Field | Type | Contract |
| --- | --- | --- |
| `partition_id` | string | Lowercase 64-hex semantic partition identity; it must equal the exact lineage projection digest below. |
| `family` | string | Declared family ID. |
| `season` | integer | 2021 through 2025. |
| `month` | integer | 1 through 12. |
| `package_relative_uri` | string | `parquet/<family>/season=YYYY/month=MM/part-<file_sha256>.parquet`. |
| `original_source_logical_identity` | string | Portable logical identity of the authoritative curated file; it must match the exact identity-derived template below and is never an absolute source path. |
| `row_count` | integer | Greater than zero. |
| `byte_count` | integer | Greater than zero. |
| `minimum_date` | string | ISO calendar-date partition minimum; its year/month must equal `season`/`month`. |
| `maximum_date` | string | ISO calendar-date partition maximum, not before `minimum_date`, with the same required year/month. |
| `file_sha256` | string | Lowercase 64-hex checksum of exact Parquet bytes. |
| `schema_id` | string | Frozen logical schema version. |
| `physical_schema_fingerprint` | string | Lowercase 64-hex digest of the ordered physical field document. |
| `physical_schema` | array of `PhysicalField` | Exact ordered Parquet footer fields. |
| `source_key` | string | Governed source key. |
| `source_version_id` | string | Frozen source-version ID. |
| `checkpoint_lineage_id` | string | Recovery checkpoint identity. |
| `governance_checksum` | string | Lowercase 64-hex governance digest. |
| `temporal_classification` | array of strings | Must exactly equal the frozen temporal classification of the declared family. |
| `target_eligibility` | array of strings | Target IDs to which this partition contributes. |
| `source_rights_ids` | array of strings | Applicable governed rights IDs. |

Each partition URI is reconstructed from its other fields and must match exactly:

```text
parquet/<family>/season=<YYYY>/month=<MM>/part-<file_sha256>.parquet
```

The original source identity must also match exactly:

```text
historical-events/source=<lowercase-source-key>/family=<family>/season=<YYYY>/month=<MM>/schema=<schema_id>/source-version=<source_version_id>/checkpoint=<checkpoint_lineage_id>/governance=<governance_checksum>/part-<file_sha256>.parquet
```

The validator therefore rejects a portable-looking URI that does not agree with
the partition's family, date key, schema, source version, checkpoint, governance
checksum, or file checksum.

`partition_id` is independently recomputed with compact sorted-key canonical JSON
and no final newline:

```text
partition_id = sha256(canonical_json({
  "checkpoint_id": checkpoint_lineage_id,
  "declared_schema": schema_id,
  "family": family,
  "governance_checksum": governance_checksum,
  "month": month,
  "season": season,
  "source_key": lowercase(source_key),
  "source_version_id": source_version_id
}))
```

In addition to those record-local checks, `partition_authority_sha256` projects
every field of every `Partition` object, converts each through Pydantic's JSON
representation, sorts the 329 records by `package_relative_uri`, and hashes the
array with the contract's compact sorted-key canonical JSON encoding and no final
newline. The result must equal
`8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437`.
This independent constant prevents a coherently relabeled or redigested corpus
from becoming trusted merely because its manifest identities were recomputed.
Both `partition_id` and `checkpoint_lineage_id` must also be globally unique.

`IdentitySlice`

| Field | Type | Rule |
| --- | --- | --- |
| `label` | string | Evidence slice label. |
| `seasons` | array of integers | Seasons represented. |
| `unique_players` | integer | Greater than zero. |
| `reference_count` | integer | Greater than zero. |
| `exact_reference_rate` | number | In `[0,1]`. |
| `exact_unique_rate` | number | In `[0,1]`. |
| `unresolved_references` | integer | At least zero. |
| `ambiguous_references` | integer | At least zero. |

`IdentityCoverage`

| Field | Type |
| --- | --- |
| `all_2021_2025` | `IdentitySlice` |
| `primary_2023_2025` | `IdentitySlice` |
| `quarantine_rows` | nonnegative integer |
| `unresolved_quarantine_rows` | nonnegative integer |
| `ambiguous_quarantine_rows` | nonnegative integer |
| `downstream_impacted_rows` | nonnegative integer; the frozen projection is zero |
| `canonical_identity_mapping_count` | positive integer |
| `identity_source_versions` | array of strings, sorted by the builder |
| `derivation_checksum` | string |

The two exact identity slices are:

| Manifest field | Label | Seasons | Unique players | References | Exact reference / unique rates | Unresolved / ambiguous |
| --- | --- | --- | ---: | ---: | --- | ---: |
| `all_2021_2025` | `GEN2_ACTIVE_2021_2025` | 2021, 2022, 2023, 2024, 2025 | 2,693 | 21,356,559 | 1.0 / 1.0 | 0 / 0 |
| `primary_2023_2025` | `GEN2_PRIMARY_2023_2025` | 2023, 2024, 2025 | 2,071 | 12,838,083 | 1.0 / 1.0 | 0 / 0 |

The exact quarantine and mapping values are 1,203 quarantine rows, 1,187
unresolved rows, 16 ambiguous rows, zero downstream-impacted rows, and 736,056
canonical identity mappings. `identity_source_versions` is the sorted sequence
`3af682f0-ecdb-47d8-8e14-660ec5f7455c`,
`4a4956de-0769-4bb0-9dc4-cc30515ba16a`,
`64db602a-090a-4e37-b8f2-2a48d36dd7f2`,
`9a08b5fd-e5ed-4043-a984-04021584f15e`, and
`fef044cc-35e1-499a-8971-a8b29cd9dd7c`. The exact derivation checksum is
`84b9e134b40d01143fc72d5fa30a87114c5db8d53845361826ce47dfed616a98`.

`TargetDefinition`

| Field | Type | Rule |
| --- | --- | --- |
| `target_id` | string | Governed target identity. |
| `family_ids` | array of strings | Contributing families. |
| `role` | string | One of `TARGET`, `FEATURE`, `TARGET_AND_FEATURE`, `CONTEXT`. |
| `description` | string | Semantic definition. |
| `ordered_classes` | array of strings or null | Required in the implemented plate-appearance target; otherwise omitted/null. |
| `taxonomy_version` | string or null | Taxonomy version where applicable. |
| `taxonomy_identity` | string or null | Taxonomy content identity where applicable. |
| `limitations` | array of strings | Defaults to an empty array. |

`DataQualityEvidence`

| Field | Type / rule |
| --- | --- |
| `eight_class_taxonomy_complete` | boolean |
| `unsafe_terminal_events` | nonnegative integer; frozen projection is zero |
| `transition_conservation_passed` | boolean |
| `score_run_runner_conservation_passed` | boolean |
| `identity_coverage_passed` | boolean |
| `tier_leaks` | nonnegative integer |
| `temporal_leaks` | nonnegative integer |
| `unique_partitions` | boolean |
| `schema_validation_passed` | boolean |
| `quarantine_downstream_impact` | nonnegative integer; frozen projection is zero |

`Limitation` has exactly `limitation_id: string`, `description: string`, and
`scope: string`.

`PackageArtifact`

| Field | Type | Rule |
| --- | --- | --- |
| `logical_uri` | string | Portable package-relative URI. |
| `sha256` | string | Lowercase 64-hex byte digest. |
| `byte_count` | integer | Greater than zero. |
| `semantic_digest_member` | boolean | `true` for every V1 manifest artifact record. |

## Canonical JSON and semantic identity

Let `M` be the complete manifest object and let `S(M)` be a deep copy of `M`
with exactly these five top-level fields removed:

1. `manifest_id`
2. `manifest_sha256`
3. `dataset_version_id`
4. `package_id`
5. `published_at`

No other field is excluded. In particular, source lineage, code revisions,
population predicates, ordered classes, limitations, partition identities,
package-relative URIs, Parquet byte hashes, and the three semantic artifact
records all participate.

Canonical JSON bytes are produced with UTF-8, lexicographically sorted object
keys, compact `,` and `:` separators, Unicode preserved rather than ASCII-escaped,
and non-finite numbers rejected. Array order is significant. The digest input has
no trailing newline:

```text
D = lowercase_hex(SHA-256(canonical_json_bytes(S(M), trailing_newline=false)))
```

The four digest-derived fields are then populated as follows:

```text
manifest_sha256  = D
manifest_id      = sports-dataset-manifest:sha256:D
dataset_version_id = asp.mlb.open-data.fd1:sha256:D
package_id       = sha256-D
```

`manifest_sha256` is therefore the semantic manifest digest, not a claim about
the exact byte hash of the serialized manifest file. Canonical JSON files in the
exchange are stored with exactly one final LF. Loading rejects invalid UTF-8,
duplicate object keys, non-canonical spacing/key order/escaping, and any missing
or extra final bytes.

The final package location is derived, not chosen independently:

```text
<output-root>/datasets/mlb-fd1/sha256-D/
```

`<output-root>` and `D` remain unresolved until a successful publication. Neither
an absolute machine path nor a runtime timestamp participates in `D`.

## Semantic members, auxiliary files, and cycle avoidance

The final package layout is:

```text
sha256-D/
  sports-dataset-manifest.json
  attribution.md
  schema-registry.json
  source-rights.json
  README.md
  validation-report.json
  parquet/<family>/season=YYYY/month=MM/part-<file-sha256>.parquet
```

The manifest's `package_artifacts` array contains exactly these three sorted
records, each with `semantic_digest_member: true`:

1. `attribution.md`
2. `schema-registry.json`
3. `source-rights.json`

Their exact byte SHA-256 and byte count are inside the semantic projection. Each
of the 329 Parquet files participates through its full `Partition` record,
including its exact file SHA-256, size, rows, schema, lineage, and URI.

`sports-dataset-manifest.json` cannot include its own byte digest without a
self-cycle, so it is not a `package_artifacts` entry. `validation-report.json` is
created only after validating the manifest and partitions; including it would
create another cycle because the report binds `manifest_sha256`. `README.md` is
also auxiliary and binds the exact validation-report byte hash with the marker
`validation-report-sha256: <hash>` (rendered in backticks in the file). The final
validator requires both auxiliary files and checks that binding, but neither is a
semantic digest member.

The checksum dependency is intentionally one-way and acyclic:

```text
Parquet partitions + attribution.md + schema-registry.json + source-rights.json
    -> manifest semantic digest D
    -> deterministic validation-report.json bytes
    -> SHA-256 embedded in deterministic README.md bytes
```

The publisher first validates the base package without the two auxiliary files,
writes the canonical validation report, derives the README from those exact report
bytes, and then validates the complete 335-file package. On resume, a README
without a validation report is rejected; an existing report must be canonical and
an existing README must exactly match the deterministic bytes derived from it.

## Population predicate contract

### AST shape and evaluation

Every `PopulationDefinition` has these required fields:

| Field | Type / rule |
| --- | --- |
| `population_id` | Literal `prior_only`, `enhanced_research`, or `strict_deployable` |
| `description` | string |
| `expected_row_count` | positive integer |
| `allowed_uses` | array of strings |
| `prohibited_uses` | array of strings |
| `season_scope` | array of integers |
| `temporal_meaning` | string |
| `predicate_contract_version` | string; V1 builder emits `SPORTS_FD1_POPULATION_PREDICATE_V1` |
| `readiness_operator_revision` | string copied from terminal readiness evidence |
| `evaluation_precedence` | integer at least 1 |
| `row_selection` | `Predicate` AST |

The predicate operators and their exclusive shapes are:

| `op` | Required operands | Forbidden operands | Meaning |
| --- | --- | --- | --- |
| `and` | `args` with at least two predicates | `field`, `value`, `values`, `arg` | Conjunction, folded in array order. |
| `or` | `args` with at least two predicates | `field`, `value`, `values`, `arg` | Disjunction, folded in array order. |
| `not` | one `arg` predicate | `field`, `value`, `values`, `args` | Logical negation. |
| `eq` | `field` and non-null `value` | `values`, `args`, `arg` | Equality to a literal. |
| `in` | `field` and a nonempty `values` array | `value`, `args`, `arg` | Membership in literal values. |

Fields must begin with `partition.` or `row.`. V1 binds
`partition.family` and `partition.season` to manifest partition metadata. A
`row.<name>` reference binds to the Parquet column when present and to null when
absent. Every `eq` or `in` leaf resolves null as false before `and`, `or`, or
`not` composition; the completed predicate is also filled null-as-false before
counting. This two-valued rule is required so `NOT (nullable_flag == value)`
classifies a null flag as not matching the value. Validation evaluates every
predicate over every row and requires the three counts to be exhaustive and
disjoint.

### Exact V1 predicates and precedence

Define:

```text
PRIOR   := partition.season in [2021, 2022]
PRIMARY := partition.season in [2023, 2024, 2025]

ENHANCED_CONDITION :=
    partition.family in [bullpen_usage, play_transition, starter_usage, weather]
    OR (partition.family == player_game_batting
        AND row.actual_starting_lineup == true)
    OR (partition.family == player_game_pitching
        AND row.actual_start_flag == 1)
```

The ordered definitions are:

| Precedence | Population | Predicate | Rows | Additional prohibition |
| ---: | --- | --- | ---: | --- |
| 1 | `prior_only` | `PRIOR` | 1,491,533 | `PRIMARY_2023_2025_TARGET_TRAINING` |
| 2 | `enhanced_research` | `PRIMARY AND ENHANCED_CONDITION` | 785,808 | `STRICT_DEPLOYABLE_PREGAME_FEATURE_USE` |
| 3 | `strict_deployable` | `PRIMARY AND NOT ENHANCED_CONDITION` | 1,460,755 | None beyond common redistribution limits |

The list order and numeric precedence are normative. The ASTs are already made
disjoint; consumers must not reinterpret precedence as permission to change a
predicate. All three records bind the same terminal readiness
`operator_revision`. The frozen FD1 report records that revision as
`b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766`.

Descriptions and temporal meanings are also exact contract values:

| Population | Exact description | Exact temporal meaning |
| --- | --- | --- |
| `prior_only` | All manifested rows from the 2021-2022 history seasons. | History retained for prior construction; never primary target rows. |
| `enhanced_research` | Primary-season actual lineup/starter/reliever, play-state, and untimestamped-weather rows isolated by the finalized readiness operator. | Retrospective timing-risk evidence; separate from strict deployment. |
| `strict_deployable` | Remaining primary-season target or safely laggable rows after prior-only and enhanced timing-risk precedence. | Targets remain postgame-only; feature use must still obey `final_available_date` and Y-1 prior rules. |

Population `allowed_uses`, in builder order, are
`INTERNAL_DERIVED_ANALYTICS`, `LOCAL_PRIVATE_MODEL_DEVELOPMENT`, and
`READ_ONLY_REGISTRATION`. Common prohibitions are `PUBLIC_BULK_DOWNLOAD`,
`PUBLIC_RAW_REDISTRIBUTION`, `RAW_DATA_RESALE`, and
`UNREVIEWED_COMMERCIAL_REDISTRIBUTION`.

## Logical and physical schemas

The schema registry is `SPORTS_DATASET_SCHEMA_REGISTRY_V1`, version
`sports_dataset_schema_registry_v1`. It must contain exactly ten sorted families,
329 compatible partitions, and 33 physical variants in aggregate.

| Family | Logical schema | Columns in frozen recovery contract | Files | Rows | Bytes |
| --- | --- | ---: | ---: | ---: | ---: |
| `bullpen_usage` | `retrosheet_fd1_bullpen_usage_v6` | 82 | 36 | 80,314 | 9,902,702 |
| `pitch_sequence` | `retrosheet_fd1_pitch_sequence_v5` | 64 | 36 | 943,682 | 80,347,024 |
| `plate_appearance` | `retrosheet_fd1_plate_appearance_v5` | 69 | 36 | 913,349 | 79,421,951 |
| `play_transition` | `retrosheet_fd1_play_transition_v5` | 81 | 36 | 949,217 | 88,260,346 |
| `player_game_batting` | `retrosheet_fd1_player_game_batting_v5` | 78 | 36 | 356,498 | 30,584,501 |
| `player_game_fielding` | `retrosheet_fd1_player_game_fielding_v5` | 61 | 36 | 332,159 | 27,997,674 |
| `player_game_pitching` | `retrosheet_fd1_player_game_pitching_v5` | 71 | 36 | 104,610 | 10,736,025 |
| `player_prior` | `gate3c_fd1_identity_prior_v1` | 28 | 5 | 21,823 | 2,512,357 |
| `starter_usage` | `retrosheet_fd1_starter_usage_v6` | 82 | 36 | 24,296 | 4,558,900 |
| `weather` | `retrosheet_fd1_weather_v8` | 71 | 36 | 12,148 | 3,099,873 |

Family semantics are also frozen exactly:

| Family | Purpose | Target/feature roles | Temporal classification | Population eligibility | Known limitation |
| --- | --- | --- | --- | --- | --- |
| `bullpen_usage` | Retrospective reliever appearance and bullpen-usage targets. | `TARGET`, `RETROSPECTIVE_CONTEXT` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL`, `ENHANCED_TIMING_RISK` | `enhanced_research`, `prior_only` | Actual reliever use is known after the game and is enhanced-only. |
| `pitch_sequence` | Retrosheet pitch-sequence strings for retrospective event research. | `TARGET_INPUT`, `RETROSPECTIVE_CONTEXT` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL` | `prior_only`, `strict_deployable` | Retrosheet pitch strings are not Statcast pitch tracking. |
| `plate_appearance` | Canonical eight-class terminal plate-appearance outcomes. | `TARGET` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL` | `prior_only`, `strict_deployable` | Outcomes are retrospective targets, not same-game pregame features. |
| `play_transition` | Conserved base/out, runner, run, and score state transitions. | `TARGET`, `SIMULATOR_VALIDATION` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL`, `ENHANCED_TIMING_RISK` | `enhanced_research`, `prior_only` | Play state is retrospective and enhanced-only for tier projection. |
| `player_game_batting` | Retrospective player-game batting evidence and lineup roles. | `TARGET`, `LAGGABLE_FEATURE` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL` | `enhanced_research`, `prior_only`, `strict_deployable` | Actual starting-lineup rows carry historical timing risk. |
| `player_game_fielding` | Retrospective player-game fielding evidence. | `TARGET`, `LAGGABLE_FEATURE` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL` | `prior_only`, `strict_deployable` | No tracking-based defensive measurements are present. |
| `player_game_pitching` | Retrospective player-game pitching evidence and actual starts. | `TARGET`, `LAGGABLE_FEATURE` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL` | `enhanced_research`, `prior_only`, `strict_deployable` | Actual starter rows carry historical timing risk. |
| `player_prior` | Chronological prior-period player batting, pitching, and fielding evidence. | `PRIOR_FEATURE` | `Y_MINUS_1_ONLY`, `SAME_SEASON_PREDICTOR_INELIGIBLE` | `prior_only`, `strict_deployable` | A target season may use only Y-1 or earlier prior evidence. |
| `starter_usage` | Retrospective actual-starter duration targets. | `TARGET` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL`, `ENHANCED_TIMING_RISK` | `enhanced_research`, `prior_only` | Actual starter identity and duration are postgame targets. |
| `weather` | Retrospective game and weather context from Retrosheet game information. | `CONTEXT` | `RETROSPECTIVE`, `AVAILABLE_ONLY_AFTER_GAME_FINAL`, `ENHANCED_TIMING_RISK` | `enhanced_research`, `prior_only` | Historical actual weather timing is not proven pregame. |

The nine non-prior families use source key `RETROSHEET_CSV_DOWNLOADS`, source
version `64db602a-090a-4e37-b8f2-2a48d36dd7f2`, and rights IDs
`CHADWICK_PERSONS_REGISTER`, `RETROSHEET`. `player_prior` uses source key
`SABR_LAHMAN_BOX_DOWNLOADS`, source version
`3af682f0-ecdb-47d8-8e14-660ec5f7455c`, and rights IDs
`CHADWICK_PERSONS_REGISTER`, `SABR_LAHMAN_MODERN_MLB`. Every family record must
reconcile its file count, rows, bytes, and date bounds to its partition records.

The 33 variants are discovered from the frozen recovery manifest, not guessed
from logical schemas. A physical fingerprint is the SHA-256 of the ordered
`[{"name":...,"dtype":...}]` field document. For each family/fingerprint pair,
the registry records:

- `physical_schema_version` as
  `<logical_schema_version>+physical-sha256-<fingerprint>`;
- `physical_schema_fingerprint`;
- positive `compatible_partition_count`; and
- ordered `fields`.

Every registry field contains `name`, `dtype`, `nullable: true`,
`semantic_description`, `target_feature_identity_classification`,
`temporal_risk_classification`, and `source_field_lineage`. Treating every field
as nullable is deliberately conservative because the recovered Polars schema does
not preserve Parquet required/optional repetition levels. Variant counts must
reconcile to each family count and to all 329 partitions. The validator reads each
Parquet footer independently and requires both its ordered fields and recomputed
fingerprint to match the partition and registry.

`source_key` and `source_version_id` are provenance-only fields, never modeling
features or player identities. Their target/feature/identity classification is
`PROVENANCE` and their temporal-risk classification is `PROVENANCE_ONLY`.

## Targets and the normative probability-vector order

The implemented target definitions are:

| Target ID | Families in contract order | Role | Exact description | Exact limitation |
| --- | --- | --- | --- | --- |
| `bullpen_usage` | `bullpen_usage` | `TARGET` | Actual relief appearances and postgame bullpen usage. | Actual appearance evidence is enhanced-only. |
| `chronological_player_priors` | `player_prior` | `FEATURE` | Annual player evidence available only to later target seasons. | Y-1 or earlier; same-season future totals are prohibited. |
| `game_context` | `player_game_batting`, `player_game_fielding`, `player_game_pitching`, `weather` | `CONTEXT` | Game, park, participant, fielding, and actual-weather context. | Historical actual-weather timing is not proven pregame. |
| `plate_appearance_outcome` | `pitch_sequence`, `plate_appearance` | `TARGET` | Canonical terminal eight-class plate-appearance outcome. | FD1 rows carry importer taxonomy `retrosheet_to_mlb_gen2_pa_v1`; probability vectors use the semantic model order recorded here. |
| `play_base_out_transition` | `play_transition` | `TARGET_AND_FEATURE` | Conserved outs, bases, runners, runs, and score transition. | Retrospective play state is enhanced-only. |
| `starter_duration` | `player_game_pitching`, `starter_usage` | `TARGET` | Actual starter identity and innings/batters-faced duration. | Actual starter evidence is not proven prospectively timed. |

The actual immutable probability-vector class order, with zero-based indices, is:

| Index | Class |
| ---: | --- |
| 0 | `STRIKEOUT` |
| 1 | `WALK_HBP_CI` |
| 2 | `HOME_RUN` |
| 3 | `SINGLE` |
| 4 | `DOUBLE` |
| 5 | `TRIPLE` |
| 6 | `BIP_OUT` |
| 7 | `OTHER_REACH` |

In compact form:

```text
STRIKEOUT,WALK_HBP_CI,HOME_RUN,SINGLE,DOUBLE,TRIPLE,BIP_OUT,OTHER_REACH
```

This is the enum order in `event_taxonomy.py`, the `PA_OUTCOMES` recovery
validation order, and the order enforced by `CANONICAL_PA_CLASS_ORDER`. It is the
only valid probability-vector order. The frozen FD1 human report presents its
outcome-count inventory alphabetically as `BIP_OUT`, `DOUBLE`, `HOME_RUN`,
`OTHER_REACH`, `SINGLE`, `STRIKEOUT`, `TRIPLE`, `WALK_HBP_CI`; that is a reporting
inventory order, not a vector index contract. Any user-spec inventory sequence
that follows that presentation must not override the verified source authority.

The plate-appearance taxonomy version is
`mlb_gen2_pa_outcome_taxonomy_v1`, with taxonomy identity
`20e50d93f47423579173b95d619676236a2a1874b4e0dcb6811b9c40730a987a`.

## Temporal contract and folds

The `TemporalContract` object has exactly these fields:

| Field | V1 meaning |
| --- | --- |
| `season_roles` | `2021-2022: PRIOR_ONLY_HISTORY`; `2023-2025: PRIMARY_GEN2_MODELING`. |
| `player_prior_rule` | Annual Dec-31 `player_prior` rows have `eligible_from_season=season+1`; target season Y may use only Y-1 or earlier evidence. |
| `no_current_game_evidence` | `true`. |
| `no_current_season_future_evidence` | `true`. |
| `strict_timing_risk_separation` | Actual lineup, starter, reliever, play-state, and untimestamped-weather rows are isolated in `enhanced_research`. |
| `same_date_game_ordering` | Use `final_available_date`, source `game_number`, `doubleheader_id`, and canonical game identity; never borrow later same-date game evidence. |
| `suspended_game_handling` | Retain original game identity/date and delay availability until the source completion/final date. |
| `doubleheader_handling` | Preserve source `game_number` and `doubleheader_id` as distinct ordered games. |
| `fold_compatibility` | Rolling-origin only; `training_end` precedes `validation_start`. |
| `folds` | Ordered `Fold` records below. |
| `untouched_final_holdout_claimed` | Must be `false`; V1 does not claim 2025 as untouched. |

Each `Fold` contains `fold_id`, `training_seasons`, `validation_season`,
`training_end`, `validation_start`, `validation_end`, positive `training_rows`, and
positive `validation_rows`. Date strings must satisfy
`training_end < validation_start <= validation_end`.

| Fold | Training seasons | Validation | Training end | Validation range | Training / validation rows |
| --- | --- | ---: | --- | --- | ---: |
| `train-2023-validate-2024` | 2023 | 2024 | 2023-10-01 | 2024-03-20 through 2024-09-30 | 184,104 / 182,449 |
| `train-through-2024-validate-2025` | 2023, 2024 | 2025 | 2024-09-30 | 2025-03-18 through 2025-09-28 | 366,553 / 182,926 |

## Source-right controls

`source-rights.json` has schema version `SPORTS_DATASET_SOURCE_RIGHTS_V1`,
policy version `gate3c-fd1-open-data-v1`, and exactly six sorted records. Its
`identity_checksum` is the SHA-256 of the complete object with that field removed,
encoded as compact, sorted-key JSON with ASCII escaping. For the frozen V1 object
the checksum is
`9efb280cc2e1604a546b061238b01e92df6e9cd038d25e72f91f117105342fdf`.
The package validator requires byte-bound artifact identity and exact equality to
the deterministic V1 policy object; it does not accept a merely self-consistent
replacement rights document.

The manifest's complete `source_rights_ids` array is sorted exactly as follows:

1. `CHADWICK_PERSONS_REGISTER`
2. `LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED`
3. `NOAA_NCEI_LCDV2_ZERO_USE`
4. `OPEN_METEO_FREE_HOSTED_ZERO_USE`
5. `RETROSHEET`
6. `SABR_LAHMAN_MODERN_MLB`

Family and partition records carry the applicable subset. Retrosheet-derived
families bind Retrosheet plus Chadwick identity rights; `player_prior` binds SABR
Lahman plus Chadwick identity rights. The manifest retains the excluded
Lahman/Seamheads population and zero-use weather registrations at package scope so
their non-use remains explicit.

| Rights control | FD1 status | Manifest artifact count | Scope |
| --- | --- | ---: | --- |
| `CHADWICK_PERSONS_REGISTER` | `USED` | 1 | `IDENTITY_SPINE` |
| `LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED` | `EXCLUDED_NOT_USED` | 0 | `ISOLATED_POPULATION_RECORD_ONLY` |
| `NOAA_NCEI_LCDV2_ZERO_USE` | `ZERO_USE_NO_DOWNLOAD` | 0 | `RETROSHEET_WEATHER_GAP_FALLBACK_NOT_ACTIVATED` |
| `OPEN_METEO_FREE_HOSTED_ZERO_USE` | `ZERO_USE_NO_REQUEST` | 0 | `LOCAL_NONCOMMERCIAL_FALLBACK_NOT_ACTIVATED` |
| `RETROSHEET` | `USED` | 13 | `PRIMARY_EVENTS_AND_GOVERNANCE_SUPPORT` |
| `SABR_LAHMAN_MODERN_MLB` | `USED_MODERN_MLB_ONLY` | 8 | `CHRONOLOGICAL_PLAYER_PRIORS_AND_IDENTITY_SUPPORT` |

`source-rights.json` separately requires package purposes in this exact order:
`INTERNAL_DERIVED_ANALYTICS`, `LOCAL_PRIVATE_MODEL_DEVELOPMENT`,
`READ_ONLY_REGISTRATION`. It prohibits at least `PUBLIC_BULK_DOWNLOAD`,
`PUBLIC_RAW_REDISTRIBUTION`, `RAW_DATA_RESALE`, and
`UNREVIEWED_COMMERCIAL_REDISTRIBUTION`. The exchange makes no new legal
conclusion, publishes no raw source archive, and must include the canonical
Retrosheet attribution.

The machine-readable required Retrosheet attribution text is exact, including its
line breaks and two spaces after `Retrosheet.`:

```text
The information used here was obtained free of
charge from and is copyrighted by Retrosheet.  Interested
parties may contact Retrosheet at "www.retrosheet.org".
```

The package-wide controls are exactly `legal_conclusion:
NO_NEW_LEGAL_CONCLUSIONS` and `false` for public redistribution, raw
redistribution, and unreviewed commercial redistribution authorization.

## Required limitation inventory

The contract requires this exact order; every record has scope `FD1_EXCHANGE`.

| Limitation ID | Implemented description |
| --- | --- |
| `INCOMPLETE_ACTUAL_LINEUP_COVERAGE` | Actual-lineup coverage is 2423/2430 in 2023, 2418/2429 in 2024, and 2420/2430 in 2025. |
| `HISTORICAL_ACTUAL_LINEUP_TIMING_RISK` | Historical actual lineups are not proven pregame-known. |
| `HISTORICAL_ACTUAL_STARTER_TIMING_RISK` | Historical actual starters are not proven pregame-known. |
| `HISTORICAL_ACTUAL_WEATHER_TIMING_RISK` | Historical actual weather is not proven pregame-known. |
| `NO_MULTI_SEASON_STATCAST` | No multi-season Statcast corpus is present. |
| `NO_VELOCITY_SPIN_MOVEMENT_LOCATION` | No velocity, spin, movement, or pitch-location tracking is present. |
| `NO_LAUNCH_SPEED_ANGLE` | No launch speed or launch angle is present. |
| `NO_BARREL_HARD_HIT_XWOBA` | No barrel, hard-hit, or xwOBA evidence is present. |
| `NO_TRACKING_DEFENSE_OR_RUNNER_SPEED` | No tracking-based defense or runner-speed evidence is present. |
| `NO_HISTORICAL_MARKET_CLOSING_LINE_OR_CLV` | No historical market, closing-line, or CLV evidence is present. |
| `RETROSPECTIVE_EVIDENCE_NOT_PROSPECTIVE_PROOF` | Retrospective reconstruction does not prove prospective availability. |
| `NOT_PRODUCTION_MODEL_APPROVAL` | This package is dataset evidence, not production-model approval. |

## Portability and independent validation

Every consumer URI is a normalized, nonempty, package-relative POSIX path. V1
rejects backslashes, NULs, drive-qualified paths, UNC paths, `file:` URIs,
absolute paths, empty/`.`/`..` components, non-normalized forms, and case-folded
collisions. Publication and validation reject symlinks, junctions/reparse points,
and hardlinked package files; each copied file must be an independent regular
file.

The final validator operates from the package root alone. It intentionally has
no dependency on ASP settings, its database, the source runtime, raw archives,
providers, or network access. It independently verifies:

- canonical JSON and all digest-derived manifest identities;
- the exact final inventory of 329 Parquet files plus six metadata/auxiliary files;
- semantic artifact sizes and SHA-256 values;
- six sorted source-right records and their package use boundaries;
- ten logical schema families, all 33 physical variants, and their partition counts;
- every Parquet file's independent-file status, byte count, SHA-256, ordered footer
  schema, physical fingerprint, row count, and date bounds;
- population predicates against actual rows, including exhaustive/disjoint totals;
- family, season, population, row, byte, and file reconciliations;
- target class ordering, chronological fold rules, and the ordered limitation
  inventory; and
- exact regeneration of `validation-report.json` plus its SHA-256 binding in
  `README.md`.

Successful validation reports `database_accessed: false`,
`source_runtime_accessed: false`, and `provider_network_calls: 0`. Those are
properties of the independent package proof, not permissions to access any source
system during consumption.

### Command-line entry points

The V1 commands are standalone modules; they are not subcommands of `app.cli`.
From the repository's `server` directory, publication and resumable continuation
use:

```powershell
..\.venv\Scripts\python.exe -m app.gen2.exchange.publisher `
  --output-root "<neutral-exchange-root>" `
  --resume `
  --heartbeat-seconds 30 `
  --max-runtime-minutes 90
```

The publisher also supports `--validate-only` with `--output-root`; that mode
derives the expected package identity from the frozen source evidence, requires
the corresponding final package to exist, and performs a complete validation
without copying or rewriting finalized files.

Independent package-only validation uses only the supplied package root:

```powershell
..\.venv\Scripts\python.exe -m app.gen2.exchange.validator `
  --package-root "<final-package-path>" `
  --heartbeat-seconds 30 `
  --max-runtime-minutes 90
```

The publisher supports `--output-root`, `--resume`, `--validate-only`,
`--heartbeat-seconds`, and `--max-runtime-minutes`. The validator supports
`--package-root`, `--heartbeat-seconds`, and `--max-runtime-minutes`. Both default
to a 30-second heartbeat and a 90-minute runtime bound. The publisher retains
durable staging/checkpoint state and exits with a resume point when that bound is
reached; the validator exits invalid on a validation deadline.
