# NBA data ingestion — connector complete, real access still blocked

**As of** 2026-09-12 · **Branch** `claude/aisp-v18-and-price-coverage-v1` (working tree)
**Verdict** `NBA_REAL_DATA_PILOT_ACCESS_BLOCKED` — unchanged from Model Studio's
2026-09-10 assessment, and re-derived here from AISP's own evidence.

**No provider request was made.** Zero NBA bytes were downloaded, $0 spent, and
no trial, account, plan or contact was created. Everything below runs on locally
authored fixtures, and every row it writes says so.

---

## 1. What the existing account is evidenced to support

Assessed by `app/nba/entitlement.py` from configuration and stored state only.
It makes no network call — including to the provider's own utility endpoints,
because no documented free entitlement or billing introspection endpoint exists,
so a probe would spend an unknown allowance to learn only that the key works.

| Finding | What it establishes | What it does **not** |
|---|---|---|
| `SPORTSDATAIO_API_KEY` present (32 chars) | a credential exists | product, league, season, endpoint entitlement, cost, or licensed use |
| `SPORTSDATAIO_ENABLED` unset → `False` | the operator has not turned it on | anything about entitlement |
| `sportsdataio_base_url` = `…/v3/mlb` | the only configured scope is MLB | NBA entitlement |
| `app/core/config.py` records the feed as "trial/scrambled and therefore non-authoritative" | AISP's own standing determination that accessible data is not real | that another product on the same account would be |
| **0** SportsDataIO ingestion runs, ever | no usage, quota or billing history exists | remaining allowance or cost position |
| No documented entitlement/billing introspection endpoint | entitlement cannot be settled by a request | that none exists undocumented |
| Free Trial and Dev Key are scrambled; Discovery Lab is real but next-day delayed and **personal use**; commercial needs an agreement | real NBA data here would at best be delayed and personal-use | which of these this key is — **the missing fact** |

**Three permissions, recorded separately and never inferred from one another:**

| | |
|---|---|
| API access | `UNVERIFIED` |
| Training / research use | `UNVERIFIED` |
| Subscriber display use | `UNVERIFIED` |
| Incremental cost position | `UNVERIFIED` — no $0 position is evidenced for any NBA endpoint |

A configured key and an HTTP 200 would establish that a request *worked*. Neither
establishes that it was permitted or what it cost.

### Pilot ceiling, carried forward not reset

3 completed games and 25,000,000 original response bytes. Prior intake was **0
games and 0 bytes**, so the entire allowance remains. Fixture bytes do not
consume it and are counted separately.

---

## 2. What was built

| Component | Path |
|---|---|
| Entitlement gate | `server/app/nba/entitlement.py` |
| Provider adapter and parsers | `server/app/data_sources/nba_sportsdataio.py` |
| Canonical intake, identity, revisions | `server/app/nba/intake.py` |
| Admin read model | `server/app/nba/read_service.py` |
| Tables (7) | `server/app/models/nba.py`, migration `20260912_0033` |
| Runnable command | `server/scripts/nba_ingest.py` |
| Endpoints | `GET /api/v1/admin/nba/data-status`, `GET /api/v1/admin/nba/games` |
| Fixtures (locally authored) | `server/tests/fixtures/nba/` |
| Tests | `server/tests/test_nba_ingestion.py` — 17 passing |

```bash
python server/scripts/nba_ingest.py status
```
```bash
python server/scripts/nba_ingest.py ingest --from-dir server/tests/fixtures/nba
```

**The transport refuses to be constructed** while the entitlement says
collection is not permitted — the check runs before a client exists, not before
a request is sent. There is no `--fetch` flag: it is absent rather than present
and disabled, so there is nothing to switch on by accident. Authentication,
when permitted, uses the `Ocp-Apim-Subscription-Key` **header**, never the query
string, so a credential cannot reach a URL, a log or a traceback.

### Scope covered

Provider→canonical identity for teams, players and games; schedules; completed
final scores with period lines; team and player box scores; and roster
relationships. **Current roster state and historical game participation are
separate tables** — `nba_roster_memberships` says where a player is listed now,
`nba_player_game_stats` says who appeared in a past game. Keeping them apart is
what stops a traded player's current club being back-projected onto games they
played for someone else.

Three clocks stay distinct on every payload: `retrieved_at` (when AISP received
the bytes), `provider_updated_at` (the provider's own stamp), and the event
time. Provider datetimes arrive as US Eastern wall clock **without an offset**;
they are stored as naive in `scheduled_local` and never guessed into an instant
that would be wrong for half the year.

Season types are normalised (`REGULAR`, `PRESEASON`, `POSTSEASON`, `ALLSTAR`).
Games are keyed on the provider `GameID`, so a **rescheduled game keeps its
mapping** — they are never joined by team names and a date.

### Isolation

Seven `nba_*` tables, none shared with MLB or NFL. Every row carries `data_mode`
under a CHECK constraint (`REAL`/`SCRAMBLED`/`FIXTURE`/`UNKNOWN`). A fixture
envelope written through a `REAL` intake raises rather than being coerced, and a
test asserts that ingesting fixtures leaves `games`, `shadow_forecasts` and
`official_asp_positions` at zero. No customer-facing surface reads these tables.

---

## 3. What actually ran

**Real provider data: 0 games, 0 players, 0 box-score rows, 0 requests, 0 bytes,
$0.** Nothing else would be true.

Fixture ingestion, from `server/tests/fixtures/nba/`:

| | |
|---|---:|
| Payloads stored | 6 |
| Original bytes admitted | 8,637 |
| Teams / players | 3 / 4 |
| Roster observations | 4 |
| Game rows (all revisions) | 4 — 3 current + 1 superseded |
| Team box-score rows | 6 |
| Player box-score rows | 12 |
| **Provider requests** | **0** |

**Idempotent replay**, no provider request: 0 payloads stored / 6 reused, 0 games
created, 0 revised, 0 rows added, 0 bytes admitted. Stored totals unchanged.

**Reconciliation** ran on each box score and reported rather than repaired:
the 4-period game and the 5-period overtime game both agree across final score,
period totals, team points and player point sums. The deliberately corrupted
correction fixture **disagrees**, and is reported as such — that is the
reconciler catching a real mismatch, not a defect.

Three defects were found and fixed during this build, each by a test rather than
by inspection: replayed payloads oscillated between two revisions of one game;
roster observations keyed on wall-clock time churned on every run; and period
lines from a box score were discarded when a schedule row already existed.

---

## 4. Exchange package

**None.** An immutable exchange package is produced only when permitted real
data has been acquired, and none has. Publishing fixtures to Model Studio would
be handing over data that looks like a feed and is not.

---

## 5. The one activation prerequisite

Written account evidence naming, **for this specific SportsDataIO key**:

1. the product, and its NBA league / season / endpoint entitlement;
2. that NBA scores, box-score and schedule calls return **real** rather than
   scrambled data;
3. a **$0** charge position, the remaining request allowance, and confirmation
   that no shared odds quota is consumed;
4. permission for internal research retention of original payloads, and
   separately for any subscriber-facing display.

The prepared inquiry at `docs/nba-sportsdataio-pilot-v1/SPORTSDATAIO_INQUIRY.md`
(Model Studio, branch `codex/nba-sportsdataio-pilot-v1`) asks exactly these and
**has not been sent**. Sending it, or supplying an existing contract that answers
them, is the only thing standing between this connector and the capped pilot.

On receipt of that evidence: set the mode to `REAL`, select three completed
games from an entitled season, and run the same command with a fetch path. The
parsers, identity crosswalk, idempotency, revision handling and reconciler are
already exercised and do not change.

---

## 6. Suggested cadence — **not enabled**

No recurring NBA job is registered and none should be until entitlement is
settled. When it is, a bounded shape that respects a 100–1,000 call/day tier:

| Job | Cadence | Calls/day | Purpose |
|---|---|---:|---|
| Schedule refresh | daily, 06:00 ET | 1 | forward horizon for one season |
| Final games by date | daily, 04:00 ET | 1 | previous day's completed games |
| Box scores by date | daily, 04:15 ET | 1 | team and player rows for that date |
| Rosters | weekly | 30 | current roster state |

≈ **3 calls/day** plus ~30 weekly, well inside the lowest advertised tier, with
no live polling. Play-by-play is deliberately excluded: it is optional for this
milestone and its entitlement is not documented for any free product.

---

## Accurate language

Real historical ingestion is **not** a live NBA feed — nothing here polls, and
no recurring acquisition is enabled. Box-score coverage is **not**
possession-model readiness — possession segmentation, five-player stint
reconstruction and play-by-play ingestion are not implemented, and are named as
such on the status endpoint itself.

MLB and NFL work was untouched throughout: the supervised scheduler, the V18
era activated at 16:00:06Z, its collectors and the research scorer all continued
running during this build.
