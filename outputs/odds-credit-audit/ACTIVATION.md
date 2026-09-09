# Event-aware odds collection — operational activation

Performed 2026-09-09. All figures below are read from the running system and the
live database, not from configuration intent.

---

## 1. Legacy ten-minute three-market poller — DISABLED

It had its own flag added so that disabling it is an explicit act rather than a
side effect of turning off live shadow operation (which also drives two
provider-free jobs that must keep running).

```
ODDS_LEGACY_PREGAME_REFRESH_ENABLED=false     # asp-live-odds-refresh
NFL_SHADOW_ODDS_ENABLED=false                 # asp-nfl-shadow-odds
```

Measured before activation, on the supervised process: `refresh-odds` FAILED 18
times in three hours and `refresh-nfl-shadow-odds` 18 times — one pair every ten
minutes, each refused by the quota floor. Measured after activation:

| job | runs in the 3h before | runs since activation |
|---|---|---|
| `refresh-odds` | 18 | **0** |
| `refresh-nfl-shadow-odds` | 18 | **0** |
| `collect-odds-checkpoint` | 0 | **2** (16:16:39, 16:21:39 — the 5-minute tick) |

## 2. Scheduler ownership — EXACTLY ONE

Ownership is no longer a matter of which processes happen to be running. Every
credit-spending odds job is gated on `odds_collection_owner` matching the
environment variable `ASP_ODDS_COLLECTION_OWNER`, which the supervisor script
exports and nothing else does. Building the scheduler under each case:

| process | odds jobs scheduled |
|---|---|
| owner token `asp-backend-8010` | `['asp-odds-checkpoint']` |
| a backend with no token | none |
| a backend with the wrong token | none |

Fail-closed twice: an unset token on either side means no owner.

This mattered immediately. At the start of this activation the host had **four**
backends listening — 8010 (supervised), 8018 (deliberately single-purpose,
`SCHEDULER_CORE_JOBS_ENABLED=false`), and two strays left by earlier sessions of
this same assignment, which have been stopped. A flag in `.env` alone would have
armed collection in all of them.

**Owner:** Windows Task Scheduler task `ASP Backend 8010` →
`scripts\asp-backend-supervisor.ps1` → uvicorn **pid 62204**, started
2026-09-09 16:11:35, `Running`, `/readyz` 200.

### A three-day orphan found and fixed on the way

Stopping the task kills the powershell and cmd wrappers but leaves the elevated
uvicorn holding port 8010. Every later run of the supervisor then found the port
occupied, refused to start a duplicate, and exited — so a stale process kept
serving, and kept polling the provider, for three days. The supervisor log's
last line before this activation was that refusal, dated 2026-09-06, while the
process answering 8010 had been started on 09-08.

The supervisor now asks the holder whether it is actually serving: a healthy
backend is a genuine duplicate and is left alone; one that will not answer
`/readyz` in three seconds is this task's own wreckage, and the supervisor runs
elevated, so it is the only thing that can clear it. It cleared pid 44916 and
started pid 62204 on the current code.

## 3. Event-aware collector — ENABLED

```
ODDS_CHECKPOINT_COLLECTION_ENABLED=true
ODDS_COLLECTION_OWNER=asp-backend-8010
```

Live confirmation from the supervised process
(`GET /api/v1/admin/odds-collection` on 8010):

```
collection_mode                 PROVIDER_FLOOR_LATCHED
legacy_blanket_polling_enabled  False
```

## 4. Quota floor and protected reserve — PRESERVED

Now stated explicitly in `.env` rather than inherited from code defaults, so the
protections are visible where an operator looks:

```
ODDS_QUOTA_FLOOR=10
ODDS_MONTHLY_CREDIT_CAP=500
ODDS_MONTHLY_CREDIT_RESERVE=150
ODDS_DAILY_CREDIT_BUDGET=18
```

Live lanes: MLB_REQUIRED 0/150, MLB_RECOVERY 0/100 (both protected),
NFL_RESEARCH 0/60, OPTIONAL_MARKETS 501/90 (September's damage),
**protected_unspent 250**.

## 5. Thresholds — NONE CHANGED

The only threshold reference in the entire activation diff is the new alert
*reading* `COVERAGE_CHECK_MINUTES` to decide when to fire. Unchanged and
verified in place: `FINAL_PREGAME_CUTOFF_MINUTES=15`,
`MAX_QUOTE_STALENESS_MINUTES=240`, `FRESH_TARGET_MINUTES=60`,
`COVERAGE_CHECK_MINUTES=90`, `RECOVERY_PRIMARY_MINUTES=45`,
`RECOVERY_FINAL_MINUTES=25`, `MIN_ADJUSTED_EDGE=0.02`, quota floor 10.

No policy, selection or decision code was touched.

## 6. Zero provider calls, zero credits — VERIFIED

The scheduled job was run end to end before activation and has run twice since:

```
reason          PROVIDER_FLOOR_LATCHED
should_request  False
provider_called False
records         0
detail          Provider balance 5 is at or below the floor of 10.
                The floor is never lowered to make a request possible.
```

`provider_request_ledger` holds **654 rows, unchanged**, newest
2026-09-08 20:48. Zero rows since activation. Zero credits spent.

## 7. Automatic start after the October reset is confirmed

Verified in two stages against the real guard:

**Before confirmation** — at 2026-10-01T12:00Z with no fresh reading, the guard
is still latched on the September figure of 5. Crossing a month boundary
establishes only that the old number is *unconfirmed*; it does not establish
that credits are available, so the guard stays closed. Optional lanes are locked
outright in that state, while required MLB collection stays eligible under lane
accounting alone.

**After confirmation** — with a fresh in-window provider reading, `decide()`
returns `should_request=True` at the first checkpoint with **no code or
configuration change**. Collection resumes on its own.

The one action that confirms the reset is a single request to the documented
zero-credit endpoint:

```bash
python -m scripts.odds_quota_recovery_probe --balance-only
```

It hits `/v4/sports/`, which returns the quota headers without consuming a usage
credit; `--dry-run` evaluates the guard without contacting the provider at all.
It is deliberately never automatic — nothing in this system contacts a provider
on a timer — so on or after 1 October that one command is what starts October's
collection. I did not run it: no provider call was authorised for this session.

## 8. Optional work stays subordinate to required MLB

| precedence | lane | monthly | protected |
|---|---|---|---|
| 1 | MLB_REQUIRED | 150 | yes |
| 2 | MLB_RECOVERY | 100 | yes |
| 3 | NFL_RESEARCH | 60 | no |
| 4 | OPTIONAL_MARKETS | 90 | no |
| 5 | OTHER_RESEARCH | 0 | no |

MLB policy is `h2h` only at 1 credit, priority 1. NFL is `h2h,spreads,totals` at
3 credits, priority 2, and its spreads/totals fall to OPTIONAL_MARKETS. An
optional lane may spend only what is left after every protected lane's unspent
allocation. Tested in an October window with no provider reading yet: both
optional lanes refuse, and MLB_RECOVERY is still allowed.

## 9. Ninety-minute coverage alert — ADDED

A game inside 90 minutes of its cutoff with no complete two-sided same-book
quote will not get one by accident: the scheduled cover has already had its
chance, so only the recovery ladder is left, and after the cutoff nothing helps.
That is now an alert with its own severity (`CUTOFF_MISSED` >
`NO_QUOTE_INSIDE_90_MINUTES` > `CLEAR`), rendered above everything else on the
operator page rather than as one row among fifteen.

Live against today's slate: **CUTOFF_MISSED — 5 game(s) reached their decision
cutoff with no usable quote**, each named with its time past cutoff and
`QUOTA_GUARD_BLOCKED`. Screenshot:
`outputs/nfl-wow-walkthrough/08-activated-coverage-alert.png`.

## 10. Confirmations

| | |
|---|---|
| **Commit SHA** | `084cf3978e6818602682870d02b29655b797b893` |
| **Branch** | `claude/aisp-nfl-wow-and-odds-recovery-v1` |
| **Active settings** | `ODDS_CHECKPOINT_COLLECTION_ENABLED=true`, `ODDS_COLLECTION_OWNER=asp-backend-8010`, `ODDS_LEGACY_PREGAME_REFRESH_ENABLED=false`, `NFL_SHADOW_ODDS_ENABLED=false`, `ODDS_QUOTA_FLOOR=10`, `ODDS_MONTHLY_CREDIT_RESERVE=150` |
| **Scheduler owner** | Task `ASP Backend 8010` → `asp-backend-supervisor.ps1` → uvicorn pid 62204 (started 16:11:35, Running, readyz 200). No other process schedules an odds job. |
| **Next planned checkpoint** | MLB **2026-09-09 17:45 ET**, then 21:20 ET — 1 credit each, `h2h` only, 2 credits for the day. Both will be skipped while the floor holds, and re-planned from the live slate. |
| **Rollback** | `.env` is git-ignored, so config rollback is by setting: `ODDS_CHECKPOINT_COLLECTION_ENABLED=false` and, only if the old behaviour is genuinely wanted back, `ODDS_LEGACY_PREGAME_REFRESH_ENABLED=true` + `NFL_SHADOW_ODDS_ENABLED=true`. Then `Stop-ScheduledTask -TaskName 'ASP Backend 8010'; Start-ScheduledTask -TaskName 'ASP Backend 8010'`. Code rollback: `git revert 084cf39 f5a6779` and restart the same way. Setting `ODDS_COLLECTION_OWNER=` (empty) is the instant kill switch — it stops every credit-spending job in every process without touching anything else. |

---

## SportsGameOdds fallback — implemented, NOT validated

**Implemented:** yes. A disabled adapter with its own provider identity, its own
`sgo:` bookmaker namespace so its rows can never masquerade as The Odds API,
and a transport boundary that refuses to send anything without a field-mapping
manifest bound to a real observed response.

**Validated:** no, and it cannot be. It has no credential, no base URL and no
mapping manifest. Live readiness reports `mapping_state: UNMAPPED`. The database
proves it has never been contacted: `provider_request_ledger` holds 654 rows,
all `THE_ODDS_API`; `odds_snapshots` holds 222,318 rows, all `THE_ODDS_API`.
Zero SportsGameOdds rows of any kind.

Seven assumptions remain unverified, each recorded with the exact evidence that
would settle it — league coverage, bookmaker identifiers, per-price timestamps,
paired moneyline sides from one book, free-tier request/object accounting,
licence terms, and event-identity mapping. Every one requires a response from
the provider.

**Blocked on:** explicit authorization to contact the provider. No account was
created, no credential configured and no request made, in line with the standing
instruction.

---

## Verdict

**`AISP_EVENT_AWARE_ODDS_COLLECTION_ACTIVATED`**

All four conditions hold on the running system: the legacy poller is disabled
(0 runs since activation, against 18 per three hours before), the event-aware
collector is enabled and running on its five-minute tick, exactly one supervised
scheduler owns it and no other process can schedule an odds job, and the quota
guard is preventing every provider call — 654 ledger rows, unchanged, zero
credits spent.
