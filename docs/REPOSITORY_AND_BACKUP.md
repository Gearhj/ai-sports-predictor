# AISP repository, publication and backup

Status as of **2026-09-16**. Read this before committing, pushing, deploying or
restoring anything.

## 1. The two checkouts

| | Clean checkout | Original checkout |
|---|---|---|
| Path | `C:\Users\james\ai-sports-predictor-github` | `C:\Users\james\ai-sports-predictor` |
| History | the **sanitized** history published on GitHub | the original history, which contains three old credentials |
| Push | ordinary pushes to `origin`, guarded by the secret-guard hooks (§5) | **disabled on purpose** (push URL points nowhere) and must stay that way |
| Used for | **all development, commits and pushes from now on** | the running deployment, `.env`, `data/`, `.runtime/`, and the existing agent worktrees until they are wound down |

The two histories share **no commits**: every SHA was rewritten, starting with
the first commit. Git will refuse to merge them, and nobody should force it
(`--allow-unrelated-histories` is never correct here). Work moves between them
only as patches, as described in §6.

A background fetch has created `origin/claude/aisp-v18-and-price-coverage-v1`
in the original checkout. That ref points at sanitized history. It is harmless
and useful for comparing trees (§6), but never merge, rebase onto or pull it
there.

```bash
git -C C:/Users/james/ai-sports-predictor remote get-url --push origin
# PUSH-DISABLED-history-contains-credentials-see-docs-REPOSITORY_AND_BACKUP
```

## 2. GitHub and version mapping

| | |
|---|---|
| Repository | `https://github.com/Gearhj/ai-sports-predictor` |
| Visibility | **Private** (checked 2026-09-16: owner `Gearhj`; an anonymous request returns 404) |
| Contents | 35 branches, 0 tags, 286 commits, published 2026-09-16 by ordinary (non-force, non-mirror) pushes from an isolated sanitized copy |
| Default branch | `claude/aisp-v18-and-price-coverage-v1` (active development) |

Published SHAs are **not** the SHAs that were built, tested or deployed. The
mapping is authoritative:

| What | Original SHA (as run, tested and recorded) | Published SHA |
|---|---|---|
| Development tip at publication | `e0a7e306bf5fed43bf09da9d5e98c2036c104341` | `f220c2d6bb47a036c849b8e1c77f8a05398c3f58` |
| **Deployed backend** (task *ASP Backend 8010*, started 2026-09-15 22:44:45 ET) | `6f4f01ffbe20255e0e289f618ee03723d7bee8b5` | `973a53fab43825131cfc20a70406fcf5f048fa3d` |
| **Deployed frontend** (`/frontendz` build stamp) | `a3223ec8bc56a262c07f712add7eb54a33fd8182` | `693a15eb0bc0bfbc1b392af1b997e60d25ae3517` |
| Previous development tip | `81e8e7c202626ad4a866eae5830808eb2fffca93` | `844792145bc7a916eac2d5f94cbc9e4d9f3d4132` |
| `main` (2026-08-18, far behind, not production) | `92f92dde5d5ec6b8d1d68e9d69284368dbd0525b` | `fb3d9ab0f57b64d4d9940603b33abd4a7feacbd3` |

- Every commit: [`docs/provenance/sanitized-history-commit-map.txt`](provenance/sanitized-history-commit-map.txt).
- Every branch tip: [`docs/provenance/sanitized-history-branch-map.tsv`](provenance/sanitized-history-branch-map.tsv).

**Evidence identifiers are unchanged.** Commit SHAs quoted in reports, receipts,
forecast lineage, the `/frontendz` stamp and the Model Exchange are *original*
SHAs. Translate them with the commit map; never reinterpret a published SHA as
the commit that ran. Forecast receipts, evidence hashes and exchange artifacts
live outside Git and were not modified.

The deployed frontend stamp and backend commit differ only because `client/`
was not rebuilt for later server-only commits (`client/` is byte-identical
between `a3223ec` and `6f4f01f`). To confirm what is running, compare the 8010
listener's start time with the commit time and read `/frontendz`; never rely on
`/readyz` alone.

## 3. What sanitization changed

The original history contained three real credentials: the shared default
database password (role `asp`), an earlier operational database password (role
`asp_user`) and the default administrator password. On an isolated bare copy
fetched with `refs/heads/*` only, `git filter-repo --replace-text
--replace-message` replaced each literal value with `REDACTED-CREDENTIAL` in
every commit of the 35 branches. Nothing else changed:

- File sets are identical at every branch tip. Where a file differs, undoing
  the redaction reproduces the original bytes exactly (checked at all 35 tips).
- A full rescan of the published copy (2,639 blobs, all commit messages and
  author/committer fields) found **none** of the three values and **none** of
  the live `.env` values.
- No committed evidence receipt records a hash of a changed file.
  `docs/AISP_CRITICAL_REPAIR_TEST_RECEIPT.json`, a test receipt that records no
  file hashes, had the credential in two scanner-evidence strings. They now read
  `REDACTED-CREDENTIAL`.
- Branch names are unchanged. Not published: the two `refs/codex/turn-diffs/*`
  tool refs and the original worktree `HEAD`s. Both are preserved in the
  encrypted original-history archive (§8).

Current source no longer ships any credential. `DATABASE_URL` and `SECRET_KEY`
must be set explicitly, and startup fails with a clear message when they are
missing. An administrator is created only with a password of at least 12
characters (SEC-DB-001, `server/tests/test_sec_db_001_database_credentials.py`).

## 4. Credential status (2026-09-16)

| Credential | State |
|---|---|
| Administrator login (`admin@example.com`) | **Rotated** 2026-09-16 13:07 UTC to a generated 32-character password. The old password and every token issued before rotation are rejected (the stored password hash and `token_version` changed). No restart was needed. |
| Where James finds it | Windows Credential Manager entry `AISP admin login (http://127.0.0.1:8010)`, shown in a dialog by `C:\Users\james\AISP-Recovery\Show AISP admin password.cmd`, and in the live `.env`. **James must also store it off this PC** (password manager). |
| Database password (`asp_user`), `SECRET_KEY`, provider API keys | Not rotated. None of these values ever appeared in any commit. |
| Old `asp_user` password found in history | Differs from the current one. The live login does not use it. |
| Shared default `asp` password | The role `asp` does not exist on this server. Any other installation using that default must change it. |

## 5. Everyday development (clean checkout)

One-time setup per clone:

```bash
cd C:/Users/james/ai-sports-predictor-github
```

```bash
sh scripts/install-git-hooks.sh
```

The hooks run `scripts/secret_guard.py` before every commit (on staged lines)
and before every push (on the new commits and their messages). A finding blocks
the operation and names the file, line and rule, never the value. A genuine
test fixture may be accepted only by a trailing `secret-guard: test-fixture`
comment on that exact line. Never bypass the hooks with `--no-verify`.

The clean checkout has no `.env`, virtual environment or `node_modules`. To run
code there, create them locally; never commit them. Copy `.env` by hand from
the original checkout and keep it out of Git (it is already ignored).

Workflow:

```bash
git switch <branch>
```

```bash
git status --short
```

Stage **by explicit path**, never `git add .` or `git add -A`:

```bash
git add path/to/file.py path/to/test_file.py
```

```bash
git diff --cached --stat
```

```bash
git commit
```

```bash
git push origin <branch>
```

Then confirm that GitHub holds the same commit:

```bash
git ls-remote origin refs/heads/<branch>
```

Rules:

- Never `push --force`, `push --mirror`, delete a remote branch or rewrite
  published history.
- Keep unfinished work on its own named branch. Uncommitted changes and stashes
  are not backed up by a push.
- For parallel work, use worktrees of the **clean** checkout
  (`git worktree add ../aisp-wt-<name> <branch>`), not of the original.

## 6. Moving work out of the original checkout

Until every active session has moved, some work may still land in the original
checkout or its worktrees (`.claude/worktrees/*`). Ordinary commit-and-push is
**not** possible or safe there. Transfer each change deliberately, as a patch:

1. **Stop the session** that is working in the original checkout, or let it
   finish its commit. Do not start new sessions there.
2. **Find the base.** Look up the original SHA of the published starting point
   in the commit map (for the development line, the last transferred commit;
   at publication, `e0a7e306bf5f`).
3. **Scan the new commits** (run from the original checkout; the scanner also
   checks the live `.env` values):

   ```bash
   python C:/Users/james/ai-sports-predictor-github/scripts/secret_guard.py --range <base>..<branch>
   ```

4. **Export them as patches** to a folder outside both repositories:

   ```bash
   git format-patch <base>..<branch> -o C:/Users/james/aisp-transfer/<branch>
   ```

   For uncommitted work, use `git diff > C:/Users/james/aisp-transfer/<name>.patch`.
5. **Apply them in the clean checkout** on the mapped branch:

   ```bash
   git -C C:/Users/james/ai-sports-predictor-github am --3way C:/Users/james/aisp-transfer/<branch>/*.patch
   ```

6. **Verify the transfer.** The source trees must be identical, unless the
   change deliberately touched a redacted line.

   ```bash
   git rev-parse <branch>^{tree}
   ```

   Run that in both checkouts and compare the two tree IDs.
7. **Push from the clean checkout** (§5). The pre-push hook rescans. Record the
   new original→published pair when it matters for evidence.
8. Delete the transfer folder.

Never fetch, merge, cherry-pick or pull between the two repositories, and never
add the original checkout as a remote of the clean one.

Two unfinished worktree changes are preserved on
`checkpoint/wip-dreamy-heyrovsky-c5873b-2026-09-16` and
`checkpoint/wip-intelligent-williamson-38f096-2026-09-16`. Both are published
and **unfinished and untested**. Continue them from the clean checkout.

## 7. Deployment is separate

The running deployment still uses the **original checkout**, its `.env`, its
`.venv` and its `client/dist`, supervised by the scheduled task *ASP Backend
8010*. Publication did not change it.

To deploy a change that was committed in the clean checkout, bring the same
patch into the original checkout (`git format-patch` in the clean checkout,
then `git am` in the original). Confirm that the tree IDs match, then follow
the existing restart and verification procedure. Moving the deployment itself
onto the clean checkout is a separate, planned change that needs its own
`.env`, virtual environment, frontend build and task definition.

## 8. Backups and recovery

A source clone cannot restore AISP. The recovery set made on 2026-09-16 is in
`C:\Users\james\OneDrive\ASP-Platform-Backups\2026-09-16\AISP\`. The earlier
2026-09-05 and 2026-09-06 sets remain untouched beside it.

**Format.** Each archive is `tar` → `zstd` → GnuPG symmetric **AES-256**
(`*.tar.zst.gpg`). File names and headers are inside the encrypted stream.
Archives were built and fully verified outside OneDrive before being copied.

**Passphrase.** The passphrase is stored in the Windows Credential Manager
entry `AISP recovery archive key 2026-09-16`. It is shown in a dialog by
`C:\Users\james\AISP-Recovery\Show AISP recovery archive key.cmd`. It is not in
any archive, file, repository or document. **James must copy it off this PC**;
until then, losing this PC makes the set unreadable.

| Archive | Contents |
|---|---|
| `aisp-database-20260916T131407Z` | `pg_dump -Fc` of `asp`, taken from one exported REPEATABLE READ snapshot at 13:14:07 UTC; roles and globals (`pg_dumpall --globals-only`, no role passwords); per-table row counts and checksums; restore-test record |
| `aisp-data-open-and-derived-20260916` | `data/raw/open-data` (Retrosheet, Chadwick, Lahman), `open_meteo` (CC BY 4.0), `nflverse` (CC BY 4.0), demo fixtures, `data/curated` (Retrosheet and Lahman derived) |
| `aisp-config-and-untracked-20260916` | live `.env`, untracked operator files, scheduled-task XML, supervisor launcher, dependency and runtime versions, deployment receipt, administrator-rotation rollback record |
| `aisp-git-original-history-20260916` | **credential-bearing** bundles of the complete original history (all 35 branches, checkpoints, worktree HEADs, tool refs), ref and worktree inventories, the uncommitted worktree files, the sanitization manifest and commit map |
| `aisp-recovery-file-manifests-20260916` | per-file SHA-256 lists for every archive (encrypted, because they name files) |

`RECOVERY-MANIFEST.json` in the same folder lists each archive's size,
SHA-256, file count, snapshot window and verification result. `RESTORE.md`
there gives the restore steps.

**Restore test.** The database dump was restored into a disposable PostgreSQL
16 cluster (port 55433, then deleted): 142 of 142 tables, 17,094,420 rows,
with zero row-count or checksum mismatches, and matching extensions and Alembic
revision. Production was never touched.

**Not in OneDrive.** These items are held under their recorded terms, not by
default:

| Excluded | Size | Why | Where it is |
|---|---|---|---|
| SportsGameOdds captures (`data/raw/sportsgameodds`, `outputs/sportsgameodds-validation` payloads) | 1.3 MB + 39 MB | Recorded terms forbid redistribution and bulk export; payloads stay local | local only, plus an encrypted local archive |
| The Odds API captures (`data/raw/the_odds_api`) | 72 MB | Storage and retention terms are recorded as unverified; "unverified" is not permission | same |
| MLB Stats API captures (`data/raw/mlb_stats_api`, `data/raw/historical/mlb_stats_api`) | 319 MB + ~330 MB | Retention not approved; historical evidence authorized for local/private research only | same |
| Baseball Savant trial (`data/raw/historical/baseball_savant_statcast`) | 2.5 MB | Archival and retention terms unconfirmed | same |
| Model Exchange (`C:\Users\james\asp-model-exchange`) | 1.9 GB | Backed up by its owner (Codex/Model Studio) to avoid duplication | its owner's set |
| `.runtime/`, `.venv/`, `node_modules/`, `client/dist`, logs, pytest scratch | — | Disposable or regenerable | — |

The encrypted local archive of the excluded captures is
`C:\Users\james\AISP-Recovery\staging\archives\aisp-data-licensed-captures-20260916.tar.zst.gpg`.
It is on the same disk, so it does **not** protect against losing this PC. If
an owner decision records that private, encrypted off-machine storage is
allowed for a provider, copy that archive, or a per-provider split of it.

The database dump includes normalized provider observations (prices, schedules,
scores) as rows. It was included because the owner directed a database
backup.

**Restore outline** (full steps are in `RESTORE.md`):

```bash
gpg --decrypt <archive>.tar.zst.gpg | zstd -d | tar -x -C <empty folder>
```

```bash
pg_restore --create --dbname postgres --jobs 4 asp-20260916T131407Z.dump
```

Before `pg_restore`, recreate roles from `globals-*.sql` and set their
passwords from the restored `.env`. Source comes from GitHub. For the original
history, clone a bundle (`git clone <bundle>`) into a private folder; it
contains credentials and must never be pushed.

**Still open**

- The archive passphrase and the administrator password exist only on this PC
  until James copies them to an off-machine password manager.
- Licensed provider captures have no off-machine copy.
- Nothing runs on a schedule: this is a one-time set. Re-run it after
  significant data or database changes.
- `C:\Users\james\asp-model-studio` is a separate project. On 2026-09-16, its
  current commit `0c78629` was not on any remote branch, and its `outputs/`
  (~8.3 GB) are Git-ignored.
