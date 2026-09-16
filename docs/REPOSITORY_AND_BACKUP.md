# AISP repository, publication and backup

Status as of **2026-09-16**. Read this before pushing anything.

> **Do not push this repository yet.** Every branch — its history *and* its
> current files — contains database and admin credentials. The GitHub remote is
> configured with pushing **disabled** on purpose. §4 lists what has to happen
> first. A private repository limits who can read those credentials; it does
> not make publishing them safe.

## 1. Versions

| What | Branch / source | Commit |
|---|---|---|
| **Active development** | `claude/aisp-v18-and-price-coverage-v1` | the branch tip (moves as work lands) |
| **Deployed backend** | supervised task *ASP Backend 8010*, process started 2026-09-15 22:44:45 ET from a clean working tree | `6f4f01ffbe20255e0e289f618ee03723d7bee8b5` |
| **Deployed frontend** | `client/dist`, served by the backend; `/frontendz` build stamp | `a3223ec8bc56a262c07f712add7eb54a33fd8182` |

The frontend stamp and the backend commit differ only because `client/` was not
rebuilt for later server-only commits: `client/` is byte-identical between
`a3223ec` and `6f4f01f`, so the served UI is exactly what `6f4f01f` would build.

Commits after `6f4f01f` on the development branch that are documentation or
ignore-rule only do not change the deployed application. To confirm what is
actually running, compare the 8010 listener's start time with the commit time,
and read `/frontendz` — never rely on `/readyz` alone (see the runtime memory on
restart behaviour).

`main` (`92f92dd`, 2026-08-18) is **not** the current line. It is far behind and
must not be read as production.

## 2. Remote

| | |
|---|---|
| URL | `https://github.com/Gearhj/ai-sports-predictor` |
| Visibility | **Private** (verified 2026-09-16: owner `Gearhj`, anonymous API request returns 404) |
| Contents | **Empty.** Nothing has been published. |
| Local remote | `origin` — fetch URL set; **push URL deliberately set to a non-existent path** so an ordinary `git push` fails instead of publishing credentials |

```bash
git remote -v
# origin  https://github.com/Gearhj/ai-sports-predictor.git (fetch)
# origin  PUSH-DISABLED-history-contains-credentials-see-docs-REPOSITORY_AND_BACKUP (push)
```

## 3. Why publication is blocked

A full-history scan on 2026-09-16 (values held in memory only, never printed)
found three real credentials. `docs/AISP_DATABASE_CREDENTIAL_HISTORY_SANITIZATION_PLAN.md`
(on `claude/aisp-critical-integrity-repair-v1`) already records the first two
and requires that **only sanitized history is ever pushed**.

| Credential | Current files that still contain it (development tip) | In history since |
|---|---|---|
| Shared default database password (role `asp`) | `.env.example`, `docker-compose.yml`, `server/alembic.ini`, `server/app/core/config.py`, `server/app/db/session.py`, `docs/AISP_FABLE51_FINDINGS.json`, `docs/AISP_FABLE51_PRODUCTION_READINESS_AUDIT.md`, `docs/AISP_FABLE51_THREAT_MODEL.md` | first commit |
| Earlier operational database password (role `asp_user`) | `server/scripts/odds_acceptance_replay.py` | — |
| Default administrator password | `.env.example`, `server/app/core/config.py`, `docs/AISP_SECURITY_AUDIT_FINDINGS_2026-08-18.md` | first commit (`fca2ef2`) |

What the scan established:

- **All 33 branches are affected in their history, and all 33 branch tips are
  affected in their current files.** No branch, and no snapshot of any branch,
  is clean.
- The credential cleanup on `claude/aisp-critical-integrity-repair-v1`
  (SEC-DB-001) was **never merged** into the development line, so the
  development tip still carries every item above.
- None of the provider API keys, the application `SECRET_KEY` or the current
  operational database password appears anywhere in history.
- The remaining scanner hits were test fixtures and ordinary code (variable
  names such as `token`, `api_key = <expression>`), not credentials.

The administrator default is guarded only in production:
`Settings.production_guards` rejects it when `APP_ENV=production`, and this
installation runs with `APP_ENV=development`.

## 4. What has to happen before the first push

These are owner decisions and are **not** part of routine work.

1. **Rotate the administrator password** to a unique value, in `.env`. This is
   needed regardless of GitHub: the default is known to anyone who can read the
   repository.
2. **Rotate the operational database password** if the value in
   `server/scripts/odds_acceptance_replay.py` was ever valid, and confirm no
   installation still uses the shared `asp` default.
3. **Remove credentials from the current files** on the development line — by
   porting the SEC-DB-001 cleanup from `claude/aisp-critical-integrity-repair-v1`
   and replacing the administrator default — as a reviewed engineering change.
4. **Sanitize history** exactly as the sanitization plan describes (on a
   disposable mirror, with `git filter-repo --replace-text`, verified before
   publishing). This rewrites every commit SHA in the repository.
5. **Publish the sanitized result as the first push.** The remote is still empty,
   so that first push lands cleanly. Afterwards, re-clone; do not fetch
   sanitized history into this checkout.

Only then restore the push URL:

```bash
git remote set-url --push origin https://github.com/Gearhj/ai-sports-predictor.git
```

## 5. Branch inventory (2026-09-16)

35 local branches, 0 tags, 0 stashes. **None is published.** After sanitization
each should map one-to-one to a remote branch of the same name.

- **Active development:** `claude/aisp-v18-and-price-coverage-v1`
- **Oldest line:** `main`
- **Historical feature and repair lines** (31): every other `claude/*`,
  `codex/*` and `feature/*` branch. Several are checked out in their own
  worktrees; see `git worktree list`.
- **Checkpoints of uncommitted worktree work** (2, created 2026-09-16, local only):

| Checkpoint branch | Commit | Preserves |
|---|---|---|
| `checkpoint/wip-dreamy-heyrovsky-c5873b-2026-09-16` | `79b74ba3fa531e690f9975ed610f4f1a786cfb2b` | the uncommitted fix to `server/tests/test_shadow_experiment.py` (time-of-day fixture) |
| `checkpoint/wip-intelligent-williamson-38f096-2026-09-16` | `eea6b3dadfd766d3ff527a33bc6f92c38616fd5f` | the uncommitted `server/app/ingestion/normalizer.py` duplicate-key fix and its new regression test |

Both are **unfinished and untested**, and were copied without touching their
source worktrees — those worktrees still hold the same uncommitted changes.
They exist so the work survives if a worktree is removed.

Also present but not project history: two `refs/codex/turn-diffs/*` refs (Codex
tool state). They add no commits beyond the branches and are not published.

## 6. Local-only material

Files that exist only on this disk and are not in any commit:

- `docs/AISP_PROJECT_HANDOFF.md` — untracked handoff note (2026-09-08).
- `docs/MONTE_CARLO_SIMULATION_THEATER.md`, `install-supervision.ps1` —
  deliberately untracked operator files (the sanitization plan relies on this).
- `.claude/launch.json` — local preview-server configuration.
- `.env` — live secrets. Never commit.
- Uncommitted changes in the two worktrees above (checkpointed, see §5).
- `audit-output/` and `_audit_tmp/` in the detached Codex audit worktree under
  `C:\Users\james\.codex\visualizations\...` — generated audit output, not source.

## 7. Committing and pushing future work

Use this once §4 is complete and pushing is re-enabled.

```bash
git status --short
git diff
```

Stage files **by explicit path**, never with `git add .` or `git add -A`:

```bash
git add path/to/changed_file.py path/to/its_test.py
```

```bash
git diff --cached --stat
```

Before every commit, check the staged content for credentials. The SEC-DB-001
scanner test on the cleanup branch exists for this; once merged, run it.

```bash
git commit
```

Push the branch you worked on, by name, with an ordinary push:

```bash
git push origin <branch-name>
```

Then confirm GitHub holds the same commit:

```bash
git rev-parse <branch-name>
```

```bash
git ls-remote origin refs/heads/<branch-name>
```

Rules that keep this safe:

- **Never** `push --force`, `push --mirror`, delete a remote branch, or rewrite
  published history. The one exception is the planned sanitization in §4, done
  once, deliberately.
- Keep unfinished work on its own clearly named branch rather than on the
  development line.
- Uncommitted changes and stashes are **not** backed up by any push. Commit them
  to a branch first.
- A push saves source only. It does not back up the database, the model
  exchange or `.env` (see §8).

## 8. Backup coverage — what git does not protect

A source clone, even a complete one, **cannot restore AISP**. The state that
matters most lives outside git.

| Asset | Where | Backed up? |
|---|---|---|
| Source history | this repository | **Partly.** One OneDrive bundle from 2026-09-05 (verified complete for that date) covers 84 of today's 283 commits. The development tip is 190 commits beyond it. GitHub holds nothing yet. |
| PostgreSQL database `asp` (~8.8 GB, 142 tables) | local PostgreSQL 16 | **No.** No dump exists and no backup job is scheduled. It holds the append-only experiment record — decisions, positions, settlements, imported forecasts and the provider request ledger — which cannot be rebuilt from source. |
| Model Exchange | `C:\Users\james\asp-model-exchange` | **No.** Not a git repository and not under OneDrive. AISP can re-import from it, but it is the only copy of the published model artifacts on this side. |
| Model Studio source | `C:\Users\james\asp-model-studio` | **Partly.** It has its own private GitHub repository (`Gearhj/asp-model-studio`, 55 branches) and a 2026-09-05 OneDrive bundle, but on 2026-09-16 no remote branch pointed at its current development commit (`0c786297534c1ffc9892fbcd199d7d7af5ec8bae`). Its large `outputs/` artifacts are git-ignored. Model Studio is a separate project with its own owner; this document only records what was observed. |
| Live configuration and secrets | `.env` | **No.** It must never go to git. It needs a separate secure store. |
| Built frontend | `client/dist` | Regenerable from source with `scripts/build-frontend.ps1`. |
| Scheduled-task and supervisor setup | Windows Task Scheduler, `scripts/asp-backend-supervisor.ps1` | The script is in git. The task definition itself is not exported. |

The 2026-09-05 OneDrive backup rated itself
`PARTIAL_BACKUP_MISSING_REQUIRED_FILES` and recorded that cloud synchronization
was **not verified**.
