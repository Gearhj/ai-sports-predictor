# Gate 3C-X1 Sports Dataset Exchange Report

## Executive Result

**Terminal result:** `EXCHANGE_PACKAGE_FINALIZED_WITH_DOCUMENTED_LIMITATIONS`

Gate 3C-X1 published the finalized free-data FD1 corpus as one portable,
content-addressed, read-only package. The final package independently validates,
an exact rerun reuses it without copying or rewriting files, and the authoritative
AI Sports Predictor warehouse remains unchanged. This is neither a public-data
release nor production-model approval.

## Source FD1 Identity

| Evidence | Final value |
| --- | --- |
| Recovery run | `92d6f851-815a-5a53-9b5d-6340549121c1` |
| Semantic source-manifest checksum | `f9e788df4f746fe749e3cd731576bab3d191bea7559eb0b6c75230149589535c` |
| Database migration | `20260815_0015` |
| Recovery state | `FINALIZED` / last stage `FINALIZED` |
| Source corpus | 329 Parquet files; 3,738,096 rows; 337,421,353 bytes |
| Terminal readiness | `READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS` |
| Generation revision | `8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7` |
| Finalizer revision | `e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db` |

The source identity was verified from the exact content-addressed recovery,
readiness, result, active-identity, and quarantine artifacts. PostgreSQL checks
used a transaction declared read-only and rolled it back.

The terminal readiness artifact binds the pre-closeout source inventory at
28,135 bytes / SHA-256
`695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1`.
The current committed closeout inventory is separately governed at 28,216 bytes /
SHA-256
`6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f`;
the historic readiness binding was not silently relabeled.

## Exchange Contract

`SPORTS_DATASET_MANIFEST_V1` is implemented as a strict Pydantic contract with a
committed Draft 2020-12 JSON Schema and a package-only validator. Runtime parsing
uses strict JSON scalar types, so strings cannot be coerced to numbers or
booleans. Standalone module commands preserve the already-frozen FD1 CLI/finalizer
revision inputs:

```text
python -m app.gen2.exchange.publisher
python -m app.gen2.exchange.validator
```

The publishing-code revision bound into the final manifest is
`e3c84383df5163e8c35b254f4bce6d267dca254a9da922c4b204642b3b54dd54`.
The exact all-field 329-partition authority projection is SHA-256
`8dec143b06622af068665b9fd1ad419a0353e76f0e0083b4c3459b8fce3cb437`.

The committed JSON Schema is 31,430 bytes with SHA-256
`3babfcb15ea3d866c0595e1198c42fc18e4fa65aeb4519b1e95cb819db760516`.

## Canonicalization and Digest

`SPORTS_CANONICAL_JSON_V1` uses UTF-8, sorted object keys, compact separators,
normalized LF endings, duplicate-key rejection, and no NaN values. The semantic
projection excludes exactly these self-derived or publication-act fields:

- `manifest_id`
- `manifest_sha256`
- `dataset_version_id`
- `package_id`
- `published_at`

Every other manifest field participates. The serialized manifest file has
SHA-256 `5195eb32f7efbc2b24a34efed35ab0e9d013bf9449498cba2c1ba3f51b5158b2`;
this is deliberately distinct from the semantic manifest digest.

The semantic manifest includes exact checksums for `attribution.md`,
`schema-registry.json`, and `source-rights.json`. `validation-report.json` is
generated afterward, and its byte checksum is bound by deterministic `README.md`
bytes to avoid a self-referential cycle.

## Neutral Exchange Location

The neutral root is `C:\Users\james\asp-model-exchange`. Each finalized package
is an immutable digest directory under `datasets/mlb-fd1`; there is no mutable
`latest` alias and no shared mutable storage, symlink, junction, or hardlink into
AI Sports Predictor runtime data. The canonical corrected digest is identified
below. An earlier finalized metadata revision is retained unchanged, as required
by immutability, and is explicitly superseded.

## Files Published

The finalized package contains exactly 335 files: 329 Parquet partitions and six
metadata files. The 329 Parquet files were copied byte-for-byte from the fixed
recovery-manifest list. No Parquet was regenerated, transformed, recompressed, or
renamed without retaining its original logical identity. No raw archive or source
tree was scanned.

| Family | Files | Rows | Parquet bytes |
| --- | ---: | ---: | ---: |
| `bullpen_usage` | 36 | 80,314 | 9,902,702 |
| `pitch_sequence` | 36 | 943,682 | 80,347,024 |
| `plate_appearance` | 36 | 913,349 | 79,421,951 |
| `play_transition` | 36 | 949,217 | 88,260,346 |
| `player_game_batting` | 36 | 356,498 | 30,584,501 |
| `player_game_fielding` | 36 | 332,159 | 27,997,674 |
| `player_game_pitching` | 36 | 104,610 | 10,736,025 |
| `player_prior` | 5 | 21,823 | 2,512,357 |
| `starter_usage` | 36 | 24,296 | 4,558,900 |
| `weather` | 36 | 12,148 | 3,099,873 |
| **Total** | **329** | **3,738,096** | **337,421,353** |

The six metadata files total 2,397,204 bytes. The whole package therefore holds
339,818,557 file bytes.

## Schema Registry

The registry contains ten logical family schemas and 33 explicitly versioned
physical variants. Compatible-partition counts reconcile to all 329 files. Every
package footer reproduced its ordered fields, types, row count, date bounds, and
physical fingerprint. Source keys and source-version IDs are explicitly
provenance-only rather than modeling features or player identities. The registry
is 775,300 bytes with SHA-256
`b4d65e6b97a6f60e0d4ea7e2969ba162c98249aac78e0a7bbfcaf273b0631274`.

## Population Definitions

| Population | Rows | Role |
| --- | ---: | --- |
| `strict_deployable` | 1,460,755 | Primary-season targets or safely laggable rows after timing-risk separation |
| `enhanced_research` | 785,808 | Primary-season actual lineup/starter/reliever, play-state, and untimestamped-weather timing-risk rows |
| `prior_only` | 1,491,533 | 2021-2022 history retained for later prior construction |

The versioned declarative predicates bind readiness operator
`b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766`.
They use prior/enhanced/strict precedence. Null `eq`/`in` leaves resolve false
before Boolean composition; this is necessary for a nullable non-starter flag to
satisfy `NOT actual_start_flag == 1`. An exact scan of all finalized rows produced
the three counts above with zero overlapping or unassigned rows.

## Season Coverage

| Season | Role | Games | PA | Transitions | Starter targets | Bullpen appearances | Weather | Complete lineups | Date range |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2021 | Prior-only | 2,429 | 181,818 | 190,700 | 4,858 | 16,683 | 2,429/2,429 | 2,412/2,429 | 2021-04-01 to 2021-12-31 |
| 2022 | Prior-only | 2,430 | 182,052 | 187,969 | 4,860 | 16,018 | 2,430/2,430 | 2,421/2,430 | 2022-04-07 to 2022-12-31 |
| 2023 | Primary | 2,430 | 184,104 | 191,073 | 4,860 | 15,770 | 2,430/2,430 | 2,423/2,430 | 2023-03-30 to 2023-12-31 |
| 2024 | Primary | 2,429 | 182,449 | 189,536 | 4,858 | 15,835 | 2,429/2,429 | 2,418/2,429 | 2024-03-20 to 2024-12-31 |
| 2025 | Primary | 2,430 | 182,926 | 189,939 | 4,860 | 16,008 | 2,430/2,430 | 2,420/2,430 | 2025-03-18 to 2025-12-31 |

December 31 bounds arise from annual `player_prior` facts; game/event families end
at the regular-season event date recorded by the source.

## Target Definitions

The package explicitly defines:

- eight-class plate-appearance outcome;
- play/base-out transition;
- starter-duration target;
- bullpen-usage target;
- chronological player priors; and
- game/context data from batting, fielding, pitching, and weather families.

The source-authoritative plate-appearance probability-vector order is
`STRIKEOUT,WALK_HBP_CI,HOME_RUN,SINGLE,DOUBLE,TRIPLE,BIP_OUT,OTHER_REACH`.
Its semantic taxonomy is `mlb_gen2_pa_outcome_taxonomy_v1`, identity
`20e50d93f47423579173b95d619676236a2a1874b4e0dcb6811b9c40730a987a`.
This enum order differs from the alphabetized human count presentation and was
not silently reordered.

## Temporal Contract

The contract preserves 2021-2022 as prior-only history and 2023-2025 as the
primary population. Annual player priors are usable only by Y+1 or later target
seasons. Current-game and future evidence are excluded; actual lineup, starter,
reliever, play-state, and untimestamped-weather timing risk is isolated. Source
game order, doubleheaders, and suspended-game completion are governed explicitly.

The two supported rolling-origin folds are:

| Fold | Training | Validation | Rows |
| --- | --- | --- | --- |
| `train-2023-validate-2024` | 2023 through 2023-10-01 | 2024-03-20 to 2024-09-30 | 184,104 / 182,449 |
| `train-through-2024-validate-2025` | 2023-2024 through 2024-09-30 | 2025-03-18 to 2025-09-28 | 366,553 / 182,926 |

The package does not claim 2025 as an untouched final holdout.

## Identity Coverage

| Scope | Unique players | References | Exact reference rate | Exact unique rate |
| --- | ---: | ---: | ---: | ---: |
| 2021-2025 | 2,693 | 21,356,559 | 100% | 100% |
| Primary 2023-2025 | 2,071 | 12,838,083 | 100% | 100% |

The governed quarantine contains 1,203 rows: 1,187 unresolved and 16 ambiguous,
with zero downstream impacted rows. The package binds 736,056 canonical identity
mappings through coverage/lineage evidence, but exports neither PostgreSQL nor
the 737,259-row identity staging table.

## Source Rights and Attribution

The package freezes six controls:

- Retrosheet;
- Chadwick Persons Register;
- SABR Lahman modern MLB;
- Lahman/Seamheads excluded population;
- NOAA/NCEI zero use; and
- Open-Meteo zero use.

The exact three-line canonical Retrosheet notice is preserved without paraphrase.
The package allows only `LOCAL_PRIVATE_MODEL_DEVELOPMENT`,
`INTERNAL_DERIVED_ANALYTICS`, and `READ_ONLY_REGISTRATION`. It authorizes no
public bulk download, raw resale, public raw redistribution, or unreviewed
commercial redistribution. Public Retrosheet-derived output remains subject to
the already-finalized attribution/publication review boundary.

`source-rights.json` is 14,968 bytes / SHA-256
`8215be7ed1a472dfaad6f93c3253be8ce3e8393e685d7395171bed20800311f7`.
`attribution.md` is 1,985 bytes / SHA-256
`2f79f4f280191df180db9a49028f4487b4603517079604e9bdbd8afe724865db`.

## Known Free-Data Limitations

The manifest preserves:

- incomplete actual-lineup coverage;
- historical actual-lineup, actual-starter, and actual-weather timing risk;
- no multi-season Statcast corpus;
- no velocity, spin, movement, or location tracking;
- no launch speed/angle, barrel, hard-hit, or xwOBA evidence;
- no tracking-based defense or runner speed;
- no historical market, closing-line, or CLV evidence;
- retrospective reconstruction is not proof of prospective availability; and
- dataset publication is not production-model approval.

## Publication Performance

The canonical finalized run completed in **30.000 seconds**. It copied and source-verified
329 files / 337,421,353 bytes, then performed both base-package and full-package
validation before atomic promotion. Provider/network calls and database writes
were both zero.

An earlier pre-promotion validation correctly stopped on a nullable predicate
evaluation edge case. No finalized path was exposed. The exact implementation-
owned staging directory and checkpoint were verified under the approved neutral
root and removed; those generated copies were recoverable by recopying from the
unchanged source. After fixing two-valued leaf semantics, an exact 329-file scan
reconciled all 3,738,096 population memberships before the successful run.

After the first successful finalization, a final staged-diff review found that
`source_key` and `source_version_id` were labeled too broadly in schema metadata.
The affected `sha256-6c6cde072028c227d2372f950f30dcf3bd652be2efa92b9a2894c4660d22efc4`
directory was not altered or deleted. It remains a valid, immutable but
superseded metadata revision and must not be registered by future consumers. The
classification was narrowed to provenance-only, all validation was repeated, and
the corrected canonical digest documented below was finalized as a new package.

## Package Validation

The standalone validator completed in **5.375 seconds** with status `VALID` using
only the final package plus validator code. It did not read AI Sports Predictor's
database, `.runtime`, raw data, or source manifests. It checked all 335 declared
files, all 329 Parquet hashes/bytes/rows/schemas/date bounds, ten family totals,
five season totals, three population counts, exact partition identities, portable
URIs, source-rights references, attribution, targets, temporal rules, and
limitations.

`validation-report.json` is 2,076 bytes / SHA-256
`48854d42ab97ac174877f05ea9a84e22ebd54ec3ead2af34e444f562ff09c5cf`.
`README.md` binds that checksum and is 1,184 bytes / SHA-256
`2e6c216881c3098d99c5d0575ce78c0b0afe3e56250bbc7ec98eb8efaaa8fe34`.

## Idempotency

The required publisher rerun completed in **7.829 seconds** with status `REUSED`.
It returned the same manifest ID and package path, copied zero files and zero
bytes, performed zero writes and provider calls, and created no duplicate semantic
package.

## Security and Secret Audit

The exact package inventory contains only 329 `.parquet` files and these metadata
files: `sports-dataset-manifest.json`, `schema-registry.json`,
`source-rights.json`, `attribution.md`, `README.md`, and
`validation-report.json`. Validator checks rejected links/hardlinks, undeclared
files, case collisions, path traversal, absolute/UNC/file URIs, and package-root
escapes.

The package metadata scan found zero forbidden file names, absolute runtime or
sibling-repository paths, credential URIs, private keys, or high-confidence API,
GitHub, or AWS key patterns. The source-controlled X1 paths likewise produced
zero high-confidence secret matches and zero executable dependencies on either
forbidden sibling repository. No `.env`, database, dump, archive, raw source,
runtime log, provider log, model artifact, user account, or security database is
present in the package or staged for Git.

## Tests

All test commands used provider-disabled settings. Aggregate suites additionally
routed external proxy variables to closed loopback port `127.0.0.1:9` while
allowing localhost only.

| Verification | Result |
| --- | --- |
| Focused Gate 3C-X1 | 37 passed, 2 expected platform skips, 0 failed; 28.84 seconds |
| All Gate 3C modules (46 files) | 515 passed, 5 expected skips, 0 failed; 111.20 seconds |
| Full provider-disabled backend (102 files) | 941 passed, 5 expected skips, 0 failed; 235.91 seconds |
| Python compilation | 278 files compiled; exit 0 |
| Alembic | Fresh SQLite upgrade; sole head/current `20260815_0015`; `alembic check` found no new operations |
| JSON Schema synchronization | Generated and committed schemas were semantically and byte identical |
| Adversarial contract review | GO; no remaining P0/P1/P2 finding |
| `git diff --check` | Passed |
| Frontend | Not run because no frontend file changed |

The five aggregate-suite skips were three established Windows symlink/opt-in
PostgreSQL cases plus the X1 symlink-privilege and case-distinct-filesystem cases.

## ASP Warehouse Integrity

Pre- and post-publication read-only PostgreSQL snapshots were identical:

- recovery run, semantic checksum, `FINALIZED` status/stage, provider-call count,
  and `updated_at` remained unchanged;
- governed partition rows remained 329 / 3,738,096 / 337,421,353;
- canonical identity mappings remained 736,056;
- provider request-ledger rows remained four (the same pre-existing rows before
  and after X1); and
- Alembic remained at `20260815_0015`.

A post-publication exact-manifest rehash checked all 329 original source files and
337,421,353 bytes with zero hash or size mismatch. X1 made zero database writes,
zero provider/network calls, zero Parquet writes, and zero raw-source scans. No
frontend/backend service was stopped or restarted, and no forecast, scheduler,
quota, model registry, user account, or other operational state was altered.

## ASP Model Studio Boundary

ASP Model Studio was not accessed, inspected, executed, copied from, or modified.
The immutable neutral package is the only future handoff surface and needs no AI
Sports Predictor database or runtime directory.

Financial-services ATLAS was not accessed, inspected, executed, copied from, or
modified. Neither sibling repository appears as an executable dependency.

## Final Package Path

`C:\Users\james\asp-model-exchange\datasets\mlb-fd1\sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49`

## Manifest ID and Checksum

- Manifest ID:
  `sports-dataset-manifest:sha256:1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49`
- Semantic SHA-256:
  `1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49`
- Package ID:
  `sha256-1b58d5d1def2c9b79041472974e93bc9a0f4c5317210099c1b1ee531fbfe8b49`
- Publication timestamp: `2026-08-16T05:21:08.171241Z`

## Next Consumer Step

A future ASP Model Studio phase may copy or mount this immutable package root and
perform read-only registration after independently reproducing the V1 semantic
digest, partition authority, rights boundary, schema registry, and population
reconciliation. No importer, feature matrix, model training, forecast, or Gate 4
work is part of Gate 3C-X1.
