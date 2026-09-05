# AISP database-credential history sanitization plan

Status: **PLAN ONLY — NOT EXECUTED.** This document is the coordinated procedure
for removing committed database credentials from Git history. Nothing in it was
run during the critical-integrity repair. History rewriting is a distinct,
operator-authorized activity and is **not a substitute for credential rotation**.

## 1. Scope: what is in history

The working tree no longer contains any credential-bearing database URL (enforced
by the SEC-DB-001 scanner test). Git history, however, still contains:

- the shared default `asp:<password>@localhost:5433/asp` in `server/app/core/config.py`,
  `server/app/db/session.py`, `.env.example`, `docker-compose.yml`,
  `server/alembic.ini`, and three prior audit documents; and
- an operational connection string using role `asp_user` embedded in five tracked
  integration/readiness reports under `docs/` (discovered during this repair).

Both are removed from the current tree; the rewrite below removes them from all
historical revisions.

## 2. Precondition: preserve verified backups before any rewrite

1. Confirm the OneDrive Git bundle backups of the repository are present and
   verify each with `git bundle verify <bundle>`; record each bundle's SHA-256.
2. Create a fresh, dated full mirror as an additional rollback point:
   `git clone --mirror <repo> aisp-premirror-<date>.git`, then
   `git bundle create aisp-prererewrite-<date>.bundle --all`. Store both off-box
   next to the OneDrive bundles. Record their SHA-256.
3. Record the current `git rev-parse --all` output and the branch/tag inventory
   (section 3) so reachability can be re-verified afterward.

## 3. Inventory all refs

- `git branch -a` and `git tag --list` — enumerate every local and remote branch
  and tag; save to `refs-before.txt`.
- `git worktree list` — every attached worktree (all must be clean and, ideally,
  removed or re-based after the rewrite because their objects change).
- `git for-each-ref --format='%(refname) %(objectname)'` — the authoritative
  before-image of every ref.

## 4. Rewrite on a disposable mirror (never in place)

1. `git clone --mirror <repo> sanitize.git` (disposable working mirror).
2. Prepare a `replacements.txt` for `git filter-repo --replace-text` that maps each
   literal secret to `***REMOVED***` (list the exact byte strings; keep this file
   out of the sanitized result and never commit it). Add one mapping line per
   secret, of the form `<exact secret string>==><replacement>`:
   - the shared-default connection string (role `asp`, port 5433) and its bare
     password token, each mapped to a credential-free replacement such as
     `postgresql+psycopg://REDACTED@localhost:5433/asp`;
   - the operational connection string (role `asp_user`, port 5432) and its bare
     password token, each mapped to `postgresql+psycopg://REDACTED@localhost:5432/asp`.
   Do not write the `user:password@` form anywhere in this plan or in the
   replacement file's committed copies; the committed scanner gate rejects it.
3. In the mirror: `git filter-repo --replace-text replacements.txt` (or an
   equivalent BFG run). `filter-repo` rewrites every commit that contains the
   strings across all branches and tags while preserving branch and tag topology.
4. Do **not** delete or drop any branch/tag; `--replace-text` edits content only.

## 5. Verify the rewrite before publishing

- `git for-each-ref` on the mirror vs `refs-before.txt`: the **set of refs** must
  be identical (only their commit SHAs change).
- `git log --all -S'<secret>'` and `git grep -a '<secret>' $(git rev-list --all)`
  must return nothing for each secret.
- `git fsck --full --strict` on the mirror: no missing/unreachable objects.
- Spot-check that current tree tips still build and test (checkout the rewritten
  primary branch into a scratch worktree; run the backend + build).
- Confirm the two untracked operator files (`docs/MONTE_CARLO_SIMULATION_THEATER.md`,
  `install-supervision.ps1`) are unaffected (they are untracked and never enter
  history).

## 6. One-time sanitized first remote publication

This repository currently has **no remote**. The rewrite must therefore be
coordinated with the *first* remote publication so that only sanitized history is
ever pushed:

1. Freeze new commits during the window (announce to any collaborators/agents).
2. Publish the sanitized mirror as the first push to the new remote:
   `git push --mirror <new-remote>` from `sanitize.git`.
3. Replace local clones/worktrees by re-cloning from the sanitized remote rather
   than fetching (their old objects are incompatible). Remove stale worktrees
   first with `git worktree remove`.
4. If a remote already exists by the time this runs, treat it as a force-publish
   of rewritten history and require every consumer to re-clone; never merge
   pre-rewrite and post-rewrite histories.

## 7. Prevent reintroduction

- Keep the SEC-DB-001 scanner test (`test_no_committed_credential_bearing_database_url`)
  in CI as a required gate.
- Add a pre-commit hook running the same regex over staged files.
- Keep `.env` untracked (only `.env.example` with placeholders is committed).

## 8. Rotation (separate from history rewrite)

- Rotate the credential of **every installation that ever used the shared default**
  (`asp` / shared password). History rewriting does not rotate a live secret.
- Treat the operational `asp_user` password found in tracked docs as disclosed:
  rotate it and review database access logs for use of the disclosed value. This
  repair intentionally did **not** change the operational credential or role.
- After rotation, update each installation's `.env` / secret store; the app now
  fails closed if the URL is absent.

## 9. Rollback and recovery

- If any verification in section 5 fails, discard `sanitize.git` and start over
  from a fresh mirror; the original repository is untouched until section 6.
- If a published rewrite must be reverted, restore from the pre-rewrite OneDrive
  Git bundle (`git clone <bundle> restored.git`) or the dated
  `aisp-prererewrite-<date>.bundle`, verify with `git fsck`, and re-publish.
- Keep all pre-rewrite bundles until the rewrite has been validated in production
  for an agreed retention window.
