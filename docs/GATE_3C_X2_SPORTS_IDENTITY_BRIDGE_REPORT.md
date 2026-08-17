# Gate 3C-X2 Sports Identity Bridge Report

## Executive Result

Terminal result:

`IDENTITY_BRIDGE_FINALIZED_WITH_DOCUMENTED_LIMITATIONS`

Gate 3C-X2 published `SPORTS_IDENTITY_BRIDGE_V1` as an immutable,
content-addressed companion to the approved MLB FD1 dataset. Independent
package-only validation returned `PASS` for the exact seven-file,
2,071,740-byte package. All 4,633,762 declared join references resolved exactly
once, and every unresolved, ambiguous, and multiply mapped active-reference
count is zero.

The documented limitation is real and deliberately preserved: governed
upstream evidence covers 2,693 players for 2021–2025 and 2,071 for 2023–2025,
while the immutable portable parent physically enumerates 2,692 and 2,070.
The one-player gap is a roster-only governed identity absent from every
permitted immutable parent field and finalized evidence artifact. No alias was
guessed or manufactured. All physical parent references remain covered at
100%, with zero downstream impact.

Publication used no provider or external-network calls and made zero ASP
database writes. ASP Model Studio, `ds-workflow-starter`, and financial ATLAS
were not accessed. No sibling application was launched, no ASP process was
stopped or restarted, no Git remote was added, and nothing was pushed.

## Parent Dataset Binding

The companion fails closed unless every parent anchor matches:

| Parent anchor | Exact value |
| --- | --- |
| Dataset ID | `asp.mlb.open-data.fd1` |
| Manifest ID | `sports-dataset-manifest:sha256:1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Manifest semantic SHA-256 | `1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Dataset version ID | `asp.mlb.open-data.fd1:sha256:1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Package ID | `sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Partition-authority SHA-256 | `8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437` |
| Plate-appearance class order | `STRIKEOUT,WALK_HBP_CI,HOME_RUN,SINGLE,DOUBLE,TRIPLE,BIP_OUT,OTHER_REACH` |

The independent bridge validator reran the complete parent validator, which
returned `VALID`, checked all 329 Parquet files, and reported no database or
source-runtime access and zero provider-network calls. The parent remained
unchanged at 335 files and 339,818,557 bytes. Its serialized manifest file
SHA-256 remained
`5195eb32f7efbc2b24a34efed35ab0e9d013bf9449498cba2c1ba3f51b5158b2`,
and the full relative-path, size, and file-hash inventory authority remained
`129e591c6497a3f680b058360a7d329e4e4fd64a5df1133659fe17b0cdb48692`.
No file was added to or changed inside the parent.

## Authoritative Identity Sources

The publication used the finalized canonical identity mapping in an explicitly
read-only PostgreSQL transaction, the governed evidence artifacts, the source
version registry, quarantine-impact evidence, the exact parent package, and
canonical `player_prior` partitions. It did not read raw source archives or
perform any provider, web, name-based, fuzzy, or probabilistic recovery.

| Authority fact | Verified value |
| --- | ---: |
| Canonical identity mappings | 736,056 |
| Recovery staging rows | 737,259 |
| Unresolved quarantine rows | 1,187 |
| Ambiguous quarantine rows | 16 |
| Quarantine rows with downstream impact | 0 |
| Governed active players, 2021–2025 | 2,693 |
| Governed active references, 2021–2025 | 21,356,559 |
| Governed primary players, 2023–2025 | 2,071 |
| Governed primary references, 2023–2025 | 12,838,083 |
| Evidence cutoff | `2026-08-15` |

The exact frozen evidence identities are:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `active_identity` | 54,046 | `0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07` |
| `final_result` | 33,302 | `08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720` |
| `quarantine_impact` | 2,140,874 | `cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b` |

The five governed source releases are:

| Source key | Source version ID | Identity checksum |
| --- | --- | --- |
| `CHADWICK_REGISTER_ARCHIVES` | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | `8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0` |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | `47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e` |
| `RETROSHEET_CSV_DOWNLOADS` | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | `d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52` |
| `RETROSHEET_EVENT_ARCHIVES` | `9a08b5fd-e5ed-4043-a984-04021584f15e` | `2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8` |
| `SABR_LAHMAN_BOX_DOWNLOADS` | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | `61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5` |

## Bridge Scope

The bridge contains only identities needed by the immutable 2021–2025 MLB FD1
modeling corpus and `player_prior`. It publishes 2,692 active-player dimension
rows and 13,460 governed alias rows across five namespaces. It contains no
parks, teams, franchises, managers, umpires, names, birth dates, unrelated
historical mappings, raw staging rows, or canonical identity database export.

The exact consumer surfaces are Retrosheet identifiers from
`plate_appearance`, `pitch_sequence`, `player_game_batting`,
`player_game_pitching`, `player_game_fielding`, `starter_usage`, and
`bullpen_usage`, plus canonical UUIDs from `player_prior`. The parent scan also
validates all player-bearing `play_transition` fields and raw mirrors so that
the bridge cannot hide a missing physical identifier.

## Canonicalization and Digest

The manifest uses `SPORTS_CANONICAL_JSON_V1`: UTF-8, recursively sorted object
keys, compact separators, preserved array order, duplicate-key rejection,
finite numbers only, and one final LF for persisted JSON. The semantic digest
removes exactly `bridge_manifest_id`, `bridge_manifest_sha256`,
`bridge_package_id`, and `publication_timestamp` before canonical encoding and
SHA-256. No lineage, coverage, rights, limitation, schema, row-authority, or
semantic-file field is excluded.

| Digest input or result | Exact value |
| --- | --- |
| Bridge manifest semantic SHA-256 | `6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Persisted manifest file SHA-256 | `1e10d23eb1b9fc313b02483f631595b8ff11a91284e9a56ff8ddad6a92965475` |
| Alias row authority | `ddb828aee391b2f725ba4540a874865963313e6c210f246eacd7bbcf28025661` |
| Dimension row authority | `6f30e1b30ca41002d6cbb2dea8b90da5abfad4f62f96237e780fbee518005acf` |
| Extraction revision | `ddf5426f20c9cc4a03d32424c7dae7526ab810e559185383247712525ec48e14` |
| Publishing-code revision | `410729c1011a92dd286129c43b731247ee24e234a7101e86f77a73973c449f15` |
| Publication timestamp | `2026-08-17T04:20:53.179362Z` |

The three release-only trust anchors are frozen to the semantic manifest,
alias-row authority, and dimension-row authority values above. Freezing them
did not change the semantic digest because only `release_trust.py` is excluded
from the publishing-code revision.

## Alias File

`player-identity-aliases.parquet` contains one canonically sorted row per
governed source alias. Every `(namespace, source_player_id)` natural key maps to
exactly one lowercase canonical UUID. No accepted row is unresolved, ambiguous,
name-only, ungoverned, or duplicated.

| Property | Exact value |
| --- | --- |
| Rows | 13,460 |
| Bytes | 1,848,549 |
| File SHA-256 | `21890289fdf6e9ba5a5b88d74ae131bc6f6526438a1029281573ff96a301abe6` |
| Schema fingerprint | `7c7e838d1ad5b15488cefc3b34e0f4d34ca52ddf01348e2fdf61f0d9afd6e037` |
| Row-authority SHA-256 | `ddb828aee391b2f725ba4540a874865963313e6c210f246eacd7bbcf28025661` |
| Natural-key conflicts | 0 |

## Active Player Dimension

`active-player-dimension.parquet` contains one unique row for each canonical
player physically participating in the portable parent corpus. It records
represented seasons, prior-history and primary-window flags, namespace
availability, `player_prior` availability and eligible target range, role
reference counts, prior row counts, and deterministic row hashes. Names are not
present or used as keys.

| Property | Exact value |
| --- | --- |
| Rows / unique canonical UUIDs | 2,692 |
| Bytes | 183,341 |
| File SHA-256 | `2e398636c5de415ad6c90fff190666397030163fd30c49cf44a25f3e2031656c` |
| Schema fingerprint | `ba7486358a5897b19be44512eeff04549562598e53a3b37683a769283c295526` |
| Row-authority SHA-256 | `6f30e1b30ca41002d6cbb2dea8b90da5abfad4f62f96237e780fbee518005acf` |
| Duplicate canonical UUIDs | 0 |

## Namespace Coverage

Every permitted namespace was actually present and validated. Each contains
2,692 aliases, 2,692 unique source IDs, and 2,692 canonical players.

| Namespace | Alias rows | Unique source IDs | Canonical players | Governed source |
| --- | ---: | ---: | ---: | --- |
| `RETROSHEET` | 2,692 | 2,692 | 2,692 | `CHADWICK_REGISTER_ARCHIVES` |
| `LAHMAN` | 2,692 | 2,692 | 2,692 | `SABR_LAHMAN_BOX_DOWNLOADS` |
| `CHADWICK` | 2,692 | 2,692 | 2,692 | `CHADWICK_REGISTER_ARCHIVES` |
| `MLBAM` | 2,692 | 2,692 | 2,692 | `CHADWICK_REGISTER_ARCHIVES` |
| `BASEBALL_REFERENCE` | 2,692 | 2,692 | 2,692 | `CHADWICK_REGISTER_ARCHIVES` |

## 2021–2025 Coverage

| Coverage measure | Governed upstream | Portable parent |
| --- | ---: | ---: |
| Unique players | 2,693 | 2,692 |
| Nonduplicating player references | 21,356,559 | 12,411,678 |
| Unrepresented governed players | — | 1 |
| Exact unique-player rate | 100% | 100% of physically represented identities |
| Exact reference rate | 100% | 100% of physical references |

The denominators differ by design: the portable parent omits the complete
upstream roster population and some player cells not exported into the FD1
contract. Derived duplicate families and automatic-runner mirrors are excluded
from the nonduplicating physical denominator. This does not conceal a join
miss; every parent player reference resolves exactly.

## 2023–2025 Primary Coverage

| Coverage measure | Governed upstream | Portable parent |
| --- | ---: | ---: |
| Unique players | 2,071 | 2,070 |
| Nonduplicating player references | 12,838,083 | 7,460,143 |
| Unrepresented governed players | — | 1 |
| Exact unique-player rate | 100% | 100% of physically represented identities |
| Exact reference rate | 100% | 100% of physical references |

The same roster-only identity accounts for the one-player difference. It does
not occur in a permitted immutable field, so it cannot affect any portable
consumer join.

## Plate-Appearance Batter Join

The exact join is
`plate_appearance.batter_id -> RETROSHEET alias parent_join_key -> canonical_player_uuid`.

| Measure | 2021–2025 | Primary 2023–2025 |
| --- | ---: | ---: |
| References | 913,349 | 549,479 |
| Resolved | 913,349 | 549,479 |
| Unique source IDs / canonical players | 1,557 / 1,557 | Validated primary subset |
| Mapping rate | 100% | 100% |
| Unresolved / ambiguous / multiply mapped | 0 / 0 / 0 | 0 / 0 / 0 |

The primary reference count reconciles to the three target-season Y-1
denominators: 184,104 + 182,449 + 182,926 = 549,479.

## Plate-Appearance Pitcher Join

The exact join is
`plate_appearance.pitcher_id -> RETROSHEET alias parent_join_key -> canonical_player_uuid`.

| Measure | 2021–2025 | Primary 2023–2025 |
| --- | ---: | ---: |
| References | 913,349 | 549,479 |
| Resolved | 913,349 | 549,479 |
| Unique source IDs / canonical players | 1,743 / 1,743 | Validated primary subset |
| Mapping rate | 100% | 100% |
| Unresolved / ambiguous / multiply mapped | 0 / 0 / 0 | 0 / 0 / 0 |

## Player-Prior Join

`player_prior.canonical_player_id` joins directly and exactly to
`active-player-dimension.canonical_player_uuid`. The validator recomputed all
parent prior lineage and resolved 21,823 of 21,823 rows across 2,692 canonical
UUIDs, for a 100% mapping rate. Unresolved, ambiguous, and multiply mapped
identity counts are all zero. Multiple temporal prior rows per canonical UUID
are valid; duplicate dimension UUIDs are not.

## Y-minus-1 Prior Eligibility

`BATTER` eligibility uses only governed `BATTING` priors, and `PITCHER` uses
only governed `PITCHING` priors. A target season may use evidence through Y-1;
same-season evidence is forbidden.

| Target | Role | PA rows | Rows with eligible prior | Legitimate missing prior | Eligible-prior rate | Maximum prior season | Identity failures |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023 | `BATTER` | 184,104 | 169,005 | 15,099 | 0.9179865728066745 | 2022 | 0 |
| 2023 | `PITCHER` | 184,104 | 159,568 | 24,536 | 0.8667275018467823 | 2022 | 0 |
| 2024 | `BATTER` | 182,449 | 170,594 | 11,855 | 0.9350229379168973 | 2023 | 0 |
| 2024 | `PITCHER` | 182,449 | 160,914 | 21,535 | 0.8819670154399312 | 2023 | 0 |
| 2025 | `BATTER` | 182,926 | 167,720 | 15,206 | 0.9168734898264872 | 2024 | 0 |
| 2025 | `PITCHER` | 182,926 | 164,678 | 18,248 | 0.9002438144386254 | 2024 | 0 |
| Total | Both roles | 1,098,958 | 992,479 | 106,479 | — | — | 0 |

For every row, unresolved, ambiguous, and multiply mapped identity counts are
individually zero.

## Legitimate Missing-Prior Cases

The 106,479 missing-prior role references above are valid identities that lack
eligible evidence in the required earlier domain. They include rookies, new
players, and players without an eligible role-specific history. They are
reported as model-feature missingness, never as identity failures, and never
backfilled with same-season or name-derived information.

## Other Family Coverage

Independent validation resolved every declared non-PA family reference exactly:

| Join surface | References | Resolved | Unique source IDs | Mapping rate | Unresolved / ambiguous / multiple |
| --- | ---: | ---: | ---: | ---: | ---: |
| `pitch_sequence.batter_id` | 943,682 | 943,682 | 1,560 | 100% | 0 / 0 / 0 |
| `pitch_sequence.pitcher_id` | 943,682 | 943,682 | 1,743 | 100% | 0 / 0 / 0 |
| `player_game_batting.player_id` | 356,498 | 356,498 | 2,692 | 100% | 0 / 0 / 0 |
| `player_game_pitching.player_id` | 104,610 | 104,610 | 1,743 | 100% | 0 / 0 / 0 |
| `player_game_fielding.player_id` | 332,159 | 332,159 | 2,684 | 100% | 0 / 0 / 0 |
| `starter_usage.player_id` | 24,296 | 24,296 | 850 | 100% | 0 / 0 / 0 |
| `bullpen_usage.player_id` | 80,314 | 80,314 | 1,621 | 100% | 0 / 0 / 0 |

All ten declared join contracts, including the two PA joins and `player_prior`,
sum to 4,633,762 validated references.

## Quarantine Exclusion

The finalized recovery evidence contains 1,203 quarantined rows: 1,187
unresolved and 16 ambiguous. All were excluded from accepted aliases. Their
downstream-impact count is zero, and no physical parent reference depends on
them. The bridge has zero unresolved active references, zero ambiguous active
references, zero multiply mapped active references, and zero natural-key
conflicts.

## Source Rights

`source-rights.json` and `attribution.md` preserve the already-approved parent
controls and attribution for `RETROSHEET`, `CHADWICK_PERSONS_REGISTER`, and
`SABR_LAHMAN_MODERN_MLB`. The bridge makes `NO_NEW_LEGAL_CONCLUSIONS`.

Allowed purposes, in exact order:

1. `INTERNAL_DERIVED_ANALYTICS`
2. `LOCAL_PRIVATE_MODEL_DEVELOPMENT`
3. `READ_ONLY_REGISTRATION`

Prohibited purposes, in exact order:

1. `IDENTITY_DATA_RESALE`
2. `PUBLIC_BULK_DOWNLOAD`
3. `PUBLIC_RAW_BRIDGE_DOWNLOAD`
4. `PUBLIC_RAW_REDISTRIBUTION`
5. `UNRESTRICTED_COMMERCIAL_REDISTRIBUTION`

Public raw bridge download, public raw redistribution, identity-data resale,
and unrestricted commercial redistribution are explicitly unauthorized.

## Package Validation

The standalone validator returned `PASS` using only the exact parent package,
the exact bridge package, and validator code. It required no ASP database,
runtime files, raw archives, provider, network, ASP Model Studio, or financial
ATLAS access.

| Validation result | Exact value |
| --- | --- |
| Status | `PASS` |
| Final files | 7 |
| Total bytes | 2,071,740 |
| Semantic files / bytes | 4 / 2,042,890 |
| Alias rows | 13,460 |
| Active-player rows | 2,692 |
| Namespaces | 5 |
| Join contracts / references | 10 / 4,633,762 |
| Y-1 records | 6 |
| Validation-report SHA-256 | `caacf363c6cd4205b823b5c0137fb734d9c640de12cdbffcca0e4fed972eef9b` |

Every reported check passed: canonical manifest identity; exact final
inventory; parent rescan; Parquet schemas, row hashes, sorting, and authorities;
all five namespaces; exact Retrosheet joins; exact `player_prior` joins; Y-1
recomputation; rights and attribution equality; no name, fuzzy, unresolved, or
invented alias; dimension recomputation; and the portable security scan.

The exact package inventory is:

| File | Role | Bytes | File SHA-256 |
| --- | --- | ---: | --- |
| `active-player-dimension.parquet` | Semantic | 183,341 | `2e398636c5de415ad6c90fff190666397030163fd30c49cf44a25f3e2031656c` |
| `attribution.md` | Semantic | 1,424 | `d456cc1799e6194e1ad9d1db673b2565e2e64fbcf9b1425e5b74188ab2ba9e79` |
| `player-identity-aliases.parquet` | Semantic | 1,848,549 | `21890289fdf6e9ba5a5b88d74ae131bc6f6526438a1029281573ff96a301abe6` |
| `README.md` | Auxiliary | 2,027 | `4d987b2b57fa8fbdf952680cb3a3fd56d5416355e4e553d3e7947aa7e0964fd7` |
| `source-rights.json` | Semantic | 9,576 | `a67e626865f43911c8de14cb8287bdf810e846a625d28470b8c73573564b1e02` |
| `sports-identity-bridge-manifest.json` | Auxiliary | 18,467 | `1e10d23eb1b9fc313b02483f631595b8ff11a91284e9a56ff8ddad6a92965475` |
| `validation-report.json` | Auxiliary | 8,356 | `caacf363c6cd4205b823b5c0137fb734d9c640de12cdbffcca0e4fed972eef9b` |
| Total | 7 files | 2,071,740 | — |

## Idempotency

| Invocation | Result | Runtime | Finalized files/bytes written | Database/provider/network activity |
| --- | --- | ---: | ---: | --- |
| First finalization | `FINALIZED` | 138.391 s | Exact seven-file package finalized atomically | Read-only extraction; 0 DB writes; 0 provider/network calls |
| Exact second run, before trust-anchor freeze | `REUSED` | 21.031 s | 0 files / 0 bytes | 0 DB calls, writes, provider calls, or network calls |
| Reuse after frozen trust anchors | `REUSED` | 21.438 s | 0 files / 0 bytes | 0 DB calls, writes, provider calls, or network calls |

Both reuse runs returned the same manifest ID and package path, created no
duplicate semantic package, and created no mutable `latest` alias. The frozen
reuse additionally proved all three compiled release anchors.

## Provider and Database Accounting

| Account | Final value |
| --- | ---: |
| Provider calls | 0 |
| External-network calls | 0 |
| ASP database writes | 0 |
| Reuse-run database calls | 0 |
| Sibling application/database calls | 0 |

The first publication extracted the canonical authority inside a read-only
transaction and rolled it back. Source lineage records
`database_transaction_mode = READ_ONLY`, `database_writes = 0`,
`provider_network_calls = 0`, and `external_network_calls = 0`. Independent
validation and reuse did not connect to the database. No live games, forecasts,
scheduler state, Odds quota, users, model registry, or provider configuration
were mutated.

## Tests

Independent package validation is `PASS` as recorded above. All Python test
runs used provider-disabled settings. Aggregate runs routed HTTP, HTTPS, and
ALL proxy variables to closed loopback port `127.0.0.1:9`, allowed localhost
only, and used the repository's Windows-safe mode-0777 temporary-directory
harness. The first unwrapped aggregate attempt encountered only the documented
Windows `tmp_path` ACL problem; the exact ACL-safe rerun below completed with
no failure.

| Verification | Result |
| --- | --- |
| Focused Gate 3C-X2 (6 modules) | 178 passed, 2 expected Windows symlink-permission skips, 0 failed |
| Affected X1 exchange compatibility (4 modules) | 37 passed, 2 expected Windows skips, 0 failed; legacy X1 code revision remained `e3c84383df5163e8c35b254f4bce6d267dca254a9da922c4b204642b3b54dd54` |
| Affected FD1 identity/evidence/IO compatibility (8 modules) | 63 passed, 1 expected Windows skip, 0 failed |
| Full provider-disabled backend (108 modules, 1,126 cases) | 1,119 passed, 7 expected skips, 0 failed |
| Python compilation | 303 discovered Python files compiled in memory; exit 0 |
| Alembic | sole head/current `20260815_0015`; `alembic check` reported no new upgrade operations |
| JSON Schema synchronization | byte-identical; 25,498 bytes; SHA-256 `4cfaa81cb8d3586e9a9ad86b7c826448a058169514f3c3bbc616571d20f5618a` |
| Independent parent/package validation | parent `VALID`; bridge `PASS`; exact final 7-file inventory and authorities accepted |
| Adversarial release review | `GO`; no remaining P0, P1, or P2 finding |
| Secret/path scan | zero real credential or secret; the only matches were detector source text and two deliberately synthetic rejection fixtures |
| `git diff --check` | Passed |
| Frontend | Not run because no frontend file changed |

## Security Audit

The package-only scan passed for all seven files and decoded Parquet values.
The inventory contains independent regular files only: no symlink, junction,
reparse point, hardlink, absolute path, traversal, drive-qualified URI, UNC
path, or mutable pointer. It contains no environment file, credential,
password, token, private key, database or dump, runtime path, raw archive, log,
forecast, sportsbook information, ASP user data, or executable SQL.

The bridge contains only public professional-baseball identifiers required by
the governed corpus. It contains no player names and performs no normalized-name,
birth-date-plus-name, fuzzy, probabilistic, external lookup, or manually
invented mapping. The rights projection remains restrictive. ASP Model Studio
and financial ATLAS were not accessed. Nothing was pushed.

## Final Package Path

`C:\Users\james\asp-model-exchange\identity-bridges\mlb-fd1\manifest-sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49\sha256-6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf`

No generated package, Parquet file, manifest, validation report, staging state,
database, runtime file, log, source archive, or secret is committed to the
repository.

## Manifest ID and SHA

| Identity | Exact value |
| --- | --- |
| Manifest ID | `sports-identity-bridge-manifest:sha256:6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Manifest semantic SHA-256 | `6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Package ID | `sha256-6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf` |
| Persisted manifest file SHA-256 | `1e10d23eb1b9fc313b02483f631595b8ff11a91284e9a56ff8ddad6a92965475` |
| Parent manifest semantic SHA-256 | `1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49` |
| Publication timestamp | `2026-08-17T04:20:53.179362Z` |

## Consumer Instructions

Before any join, run the standalone validator from an environment where
`app` is importable:

~~~powershell
python -m app.gen2.exchange.identity_bridge_validator --bridge-package-root "C:\Users\james\asp-model-exchange\identity-bridges\mlb-fd1\manifest-sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49\sha256-6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf" --parent-package-root "C:\Users\james\asp-model-exchange\datasets\mlb-fd1\sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49" --heartbeat-seconds 30 --max-runtime-minutes 90
~~~

Accept only `status = PASS` and the exact manifest, parent, partition, alias-row,
and dimension-row anchors in this report.

For Retrosheet event families:

1. Filter `player-identity-aliases.parquet` to `namespace == "RETROSHEET"`.
2. Join the physical `batter_id`, `pitcher_id`, or `player_id` exactly to
   `parent_join_key`.
3. Use `canonical_player_uuid` from the matched alias row.
4. Fail closed on a null, absent, ambiguous, or duplicate match.

For `player_prior`, join `canonical_player_id` directly to
`active-player-dimension.canonical_player_uuid`. For target season Y, use only
prior rows whose `eligible_from_season <= Y`; never use same-season evidence.
A missing eligible prior remains a legitimate nullable model input.

Do not use names, source IDs as canonical model features, fuzzy matching,
provider lookups, ASP runtime files, or the canonical database to repair a
consumer join.

## Next ASP Model Studio Step

In a separately authorized ASP Model Studio task, register the exact immutable
parent and bridge package IDs, run the standalone package validator, and build
the Retrosheet-to-canonical and canonical-to-`player_prior` joins described
above. Persist the manifest SHA, parent SHA, alias authority, dimension
authority, and validation-report SHA with the read-only registration so any
changed byte fails closed. Then expose leakage-safe Y-1 player-prior features
while preserving legitimate missing-prior values.

ASP Model Studio was intentionally not accessed during Gate 3C-X2. That
registration and model-feature work is the next gate, not part of this
publication.

## Terminal Result

`IDENTITY_BRIDGE_FINALIZED_WITH_DOCUMENTED_LIMITATIONS`
