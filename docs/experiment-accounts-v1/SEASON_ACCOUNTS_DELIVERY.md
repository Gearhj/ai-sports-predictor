# $1,000 paper bankrolls by sport and season

**As of** 2026-09-12 · **Branch** `claude/aisp-v18-and-price-coverage-v1` (working tree)
**Status** Deployed. Migration applied, backfill run, backend restarted through
the supervised task, page verified in a real browser against the live MLB ledger.

MLB was **not** reset to $1,000 to make the page look uniform. Its account
reports the $1,000 it was contributed and the −$3.98 it has since settled.

---

## 1. The account model, and why capital moved

Capital used to live on `ShadowExperiment`, which is a **model era**, not a
season. `current_bankroll` was `experiment.starting_bankroll + realized profit`,
and V18's `starting_bankroll` is **993.52** — the carried available capital at
the boundary. That number is right as an era opening balance and wrong as a
contribution. Two things followed from it:

- Summing eras claimed **$4,993.52** of capital where **$1,000** was ever
  contributed. The before-reconciliation prints this figure deliberately.
- When Gen1's last open position settles, its stake returns to *Gen1*. V18 —
  whose opening balance already had that 2.50 deducted — would never see it.
  The money would simply leave the season.

So the season now owns the money and eras are things that happen inside it:

```
ExperimentAccount                one sport, one season, one $1,000
  ├── ExperimentAccountFunding         the opening contribution, once
  └── ExperimentAccountMembership      each model era that ran in it
```

`ShadowExperiment` is append-only and stays that way. An era is linked to its
account through a **membership row**, not a backfilled column, so no evidence
was rewritten to record something that was always true.

### Mapping as built

| Account | Era | Role | Basis |
|---|---|---|---|
| MLB 2026 | v1 `AISP_OPPORTUNITY_POLICY_V1` | **Unfunded control** | Structurally unfunded; records decisions for comparison |
| MLB 2026 | v2 `…_POLICY_V2` | Research candidate | Superseded before activation, 0 positions |
| MLB 2026 | v3 `…_POLICY_V2_1` | **Funded era** | Gen1: 19 positions, 18 settled, −3.98 |
| MLB 2026 | v4 `…V18…_V1` | Research candidate | Boundary passed without opening, 0 positions |
| MLB 2026 | v5 `…_R1:ARMED` | Research candidate | Staging row, never active |
| MLB 2026 | v6 `…_R1` | **Funded era · controlling now** | V18: 2 open, 3.52 reserved |
| NFL 2026, NBA 2026-27, NHL 2026-27, MMA 2026 | — | — | Planned, no era attached |

Classification rests on **capital, not naming**: an era is funded if it ever
held a position or is still active. v2 was initially filed as a funded era
because its `is_active` flag was never cleared after it was superseded; the
classifier now consults the same superseded-policy list the experiment service
already uses, and that row was corrected before anything depended on it.

### Opening rules

| Sport | Season id | Window | Basis |
|---|---|---|---|
| MLB | `2026` | 1 Mar → 30 Nov | Configured accounting window |
| NFL | `2026` | 1 Sep → **28 Feb 2027** | Identified by the year it begins; postseason settles inside the season it completes |
| NBA | `2026-27` | 1 Oct → 30 Jun | Crosses New Year, identified by both years |
| NHL | `2026-27` | 1 Oct → 30 Jun | Crosses New Year, identified by both years |
| MMA | `2026` | 1 Jan → 31 Dec | **Product convention** — a calendar-year campaign, not a league season |

These are *accounting* boundaries and say so. AISP does not ingest official
season calendars for NFL, NBA, NHL or MMA, so every rule carries a `basis`
sentence that the page displays, and no window is presented as a published
league date.

A season funds **once**, when two conditions both hold: its window has opened,
and it has at least one funded era attached. Funding before the window would
place capital against games that cannot exist; funding an account no era can
reach would report capital at risk of nothing.

Two different questions are answered by two different functions, deliberately:
`season_for_date` attributes activity (a September settlement still belongs to
the season that produced it), while `program_season_for` decides what the
overview shows (in the off-season, the season about to open). Using one for the
other would either show a finished season as current all summer, or file a
settlement into a season that has not started.

---

## 2. MLB reconciliation, before and after

`server/scripts/experiment_account_reconcile.py` is read-only. It resolves
nothing: if the two views disagree it prints the disagreement rather than
posting an entry that hides it. Full output in `outputs/experiment-accounts/`.

### Before — per-era ledger

| v | Policy / era | Start | Settled P&L | Reserved | Pos | Settled |
|---:|---|---:|---:|---:|---:|---:|
| 1 | `AISP_OPPORTUNITY_POLICY_V1` *(unfunded control)* | 1000.00 | 0.00 | 0.00 | 0 | 0 |
| 2 | `…_POLICY_V2` | 1000.00 | 0.00 | 0.00 | 0 | 0 |
| 3 | `…_POLICY_V2_1` | 1000.00 | −3.98 | 2.50 | 19 | 18 |
| 4 | `…V18…_V1` | 1000.00 | 0.00 | 0.00 | 0 | 0 |
| 5 | `…_R1:ARMED` | 1000.00 | 0.00 | 0.00 | 0 | 0 |
| 6 | `…_R1` | **993.52** | 0.00 | 3.52 | 2 | 0 |

```
naive sum of era opening balances : 4993.52   <- what a per-era sum claims
settled net P&L (funded eras)     : -3.98
reserved (funded eras, open only) : 6.02
positions / settlements           : 21 / 18
```

### After — MLB 2026 season account

| | |
|---|---:|
| Status | ACTIVE |
| Tracking started | **2026-09-01** (partial season) |
| Original contribution | **1,000.00** |
| Settled net P&L | **−3.98** |
| Realized bankroll | **996.02** |
| Reserved stakes | **6.02** |
| Available capital | **990.00** |
| Settled stake | 43.43 |
| Season return | **−0.40%** |
| Settled-stake ROI | **−9.16%** |
| Record | 6–12–0 |
| Open / settled | 3 / 18 |

**Reconciliation: OK** — per-era and season-account views agree on settled net
P&L, reserved stake, settlement count, and every derived figure.

### On the reported anchors

The 2026-09-12 handover reported original 1,000.00, settled net −3.98, realized
996.02, reserved 2.50, available **993.52**. The first three are reproduced
exactly. The last two are **derived from the ledger, not forced**: V18 has since
opened two positions totalling 3.52, so the account's reserve is now 6.02 and
its available capital 990.00. The 993.52 was true at the boundary and is not the
current state.

The 993.52 is treated as **carried available capital**, never as a deposit and
never as a replacement for the 1,000.00 contribution. Era opening balances are
not summed anywhere.

---

## 3. What the page shows

`/experiment` now leads with the season-account surface; the per-era experiment
view stays below it, because a model era is still worth reading on its own — it
just no longer decides what the bankroll is.

**Browser evidence** (rendered text captured from the live page at
127.0.0.1:5173; screenshots were blocked by an obscured window, so the rendered
DOM text is given verbatim):

```
FUNDED CAPITAL   $1,000
  across 1 account (MLB 2026) · realized $996.02 · available $990.00
PLANNED ALLOCATION   $4,000
  proposed for 4 sports that have not started — not funded and not available today

MLB 2026  ACTIVE   $996.02   -0.40% season return   $990.00 available · 6–12–0
NFL 2026     NOT STARTED  $1,000 planned  Not funded · nothing tracked
NBA 2026-27  NOT STARTED  $1,000 planned  Not funded · nothing tracked
NHL 2026-27  NOT STARTED  $1,000 planned  Not funded · nothing tracked
MMA 2026     NOT STARTED  $1,000 planned  Not funded · nothing tracked

MLB · BASEBALL (MLB) 2026 — Season account                        ACTIVE
  ORIGINAL CONTRIBUTION $1,000.00     REALIZED BANKROLL $996.02
  RESERVED STAKES $6.02               AVAILABLE CAPITAL $990.00
  SETTLED NET P&L -$3.98
  SEASON RETURN -0.40%       settled P&L ÷ $1,000.00 contribution
  SETTLED-STAKE ROI -9.16%   settled P&L ÷ $43.43 settled stakes
  RECORD 6–12–0              POSITIONS 3 open · 18 settled

  Tracking started 2026-09-01 · partial season — the season window opened
  2026-03-01 and no result is claimed before the actual start.
  Controlling model: ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1.
  A model change within a season does not reset this bankroll.

EQUITY — Realized bankroll this season        (reference line at $1,000)
  2026-09-01  $1,000.00   $0.00   Opening
  2026-09-01    $997.50  -$2.50   1
  2026-09-02  $1,005.08  +$5.08   2
  …
  2026-09-12    $996.02  -$3.98   2

MODEL ERAS
  v1 · AISP_BASELINE                     Unfunded control    Excluded
  v2 · AISP_BASELINE                     Research candidate  Excluded
  v3 · AISP_BASELINE                     Funded era          -$3.98  $2.50  1  18
  v4 · …V18…_V1                          Research candidate  Excluded
  v5 · …_R1:ARMED                        Research candidate  Excluded
  v6 · …_R1 · controlling now            Funded era           $0.00  $3.52  2   0

LEDGER
  Sep 12  …_R1          $2.48  2.100  Open   —       —
  Sep 12  …_R1          $1.04  1.625  Open   —       —
  Sep 11  AISP_BASELINE $1.64  2.400  Loss   -$1.64  Sep 12
  …
```

Design decisions that are load-bearing rather than cosmetic:

- **Planned is not funded, structurally.** A planned account's money fields are
  `null`, not `0`. A client summing `original_contribution` gets the funded
  total whatever it does. The summary keeps funded and planned in two boxes
  with separate labels, and the planned box is dashed and unfilled.
- **Both denominators are printed under their ratio**, because "season return"
  and "settled-stake ROI" answer different questions and the difference *is*
  the divisor.
- **Zero settled stake gives "Unavailable — no settled stake"**, never 0.00%.
- **The equity curve starts at the real tracking start** (2026-09-01), never at
  the configured season opening. It carries a visible $1,000 reference line,
  and the chart is marked decorative with a full value table as the accessible
  representation — not a fallback.
- **Era changes are visible in the ledger**: every position names the era that
  placed it, so the Gen1→V18 handover is legible in the position list itself.
- Unfunded controls and research candidates are **listed and marked Excluded**,
  rather than hidden or silently counted.

Both reads use the caching work from earlier today, keyed per account and
season, so switching sports and back is instant and one sport's figures can
never render under another's heading.

---

## 4. Funded accounts versus planned allocations

| | Accounts | Amount |
|---|---:|---:|
| **Funded** | 1 (MLB 2026) | **$1,000.00** contributed · $996.02 realized · $990.00 available |
| **Planned** | 4 (NFL 2026, NBA 2026-27, NHL 2026-27, MMA 2026) | **$4,000.00** proposed — not funded, not available |

Five planned sports represent $5,000 of *proposed* paper capital. That figure
appears nowhere as a single number, because $1,000 of it is funded and $4,000 is
not. Each unstarted sport states why:

- **NFL** — research shadow with no wagering authority; no funded account opens
  until a governed NFL decision policy is activated.
- **NBA** — ingestion built but blocked at its access boundary
  (`NBA_REAL_DATA_PILOT_ACCESS_BLOCKED`); no real NBA game has been ingested.
- **NHL / MMA** — no ingestion, forecasting or decision policy exists yet.

---

## 5. Isolation, rollover and policy

A position reserves only from its own account: `available_capital` is computed
from that account's own funding, settlements and open stakes, so one sport's
capital is structurally unreachable from another's. Position creation now sizes
against the **account**, with two consequences that matter:

- Kelly sizes on the season's realized bankroll (996.02), not the era's
  `starting_bankroll` (993.52). The latter is already net of the predecessor's
  reserve — an *available* figure — and sizing on it quietly shrinks the base
  every time an era changes hands with a position open.
- Same-game and daily caps are account-wide, so an era change mid-slate cannot
  reset a cap to zero.

An era without an account, or one that is not a funded era, keeps its previous
per-era behaviour exactly. The **unfunded control never draws on season
capital** — letting it size against funded money would put real season capital
behind a comparison that is supposed to cost nothing.

Eligibility, model authority, the 2.00% minimum edge and the staking policy are
untouched. At rollover nothing moves: prior-season liabilities and their later
settlements stay in the originating account, the new season gets its own fresh
$1,000, and `CLOSED_FOR_NEW_POSITIONS` is kept distinct from `ARCHIVED` so a
season awaiting final settlement is not reported as finished.

---

## 6. Verification

**23 focused tests**, all passing (`tests/test_experiment_season_accounts.py`):

| Required check | Tests |
|---|---|
| Preserved MLB capital and transaction counts | contribution 1,000 / P&L −3.98 / realized 996.02 / reserved 6.02 / 18 settled / 3 open; partial-season start preserved |
| No duplicated funding across Gen1→V18 | naive sum 1,993.52 vs one contribution of 1,000.00; exactly one funding row; **predecessor settlement returns capital to the season** |
| Cross-sport reservation isolation | a 400.00 MLB reserve leaves NFL at 1,000.00; staking basis is each account's own; unfunded control gets no account capital and is excluded from performance |
| Idempotent season opening | second `open_season` is a no-op; provisioner no-op on second run; not funded before the window opens; not funded with no funded era |
| Prior-season settlement after rollover | a November position settling in March lands in 2026, not 2027; new season opens at a clean 1,000; closed-with-open-position is not archived |
| Return denominators and unstarted presentation | −40 on 1,000 is −4.00% while −40 on 40 settled is −100%; zero settled stake → ROI `None`; planned accounts expose no money field; summary keeps 4,000 planned out of 1,000 funded |
| Season identity | NFL February playoffs → 2026; NBA/NHL → 2026-27; MMA labelled a convention; off-season shows the upcoming season while a settlement still attributes to the finished one |
| Presentation | exactly one era is "controlling now" |

**Live reconciliation**: `after` vs the saved `before` baseline — OK on every
checked figure, exit 0.

**Idempotency, executed for real**: the opening job ran three times through the
orchestrator. All three succeeded, all three reported no change, and the
database still holds one funding row totalling $1,000.00.

**No regressions.** Client suite 43 failures, identical to the baseline set
captured before this session's work, zero new. One test legitimately needed
updating and was: `BankrollPage.credibility` pinned the four endpoints the page
reads, and the season-account surface adds two — both GETs of stored ledger
rows, with the "no writes" assertion left intact.

`test_time_scheduler::test_scheduler_registration_is_quota_safe` was **already
failing before this task** (it compares the whole registration against a frozen
list that had drifted by four jobs). Rather than re-pin a list that goes stale
whenever anyone adds a no-cost job, it now asserts the property it is named for:
no provider-spending job is registered while polling is off, and each feature
flag adds exactly its own jobs. A second test pins that the season opener is
registered and spends nothing.

**Deployment**: migration `20260912_0034` applied (additive; no existing table
altered). Backend restarted through the Windows Scheduled Task "ASP Backend
8010"; the supervisor reclaimed the stale port owner and started clean. Verified
afterwards: one listener on 8010, one scheduler claim
(`2e437a60388849dd917a32e963734768`), task Running. Production build succeeds.
Codex's MLB modelling, the live ingestion/decision/scoring jobs and the NBA
connector's access boundary are untouched.

---

## 7. Remaining limitations

1. **Season windows for NFL, NBA, NHL and MMA are configured accounting
   boundaries, not ingested league schedules.** They are labelled as such on the
   page. If a real schedule is later ingested, `season_calendar.py` is the one
   place to change, and each account keeps the boundaries it was created with so
   a configuration change cannot restate a season that has already run.

2. **Only MLB can actually fund.** The other four accounts will stay PLANNED
   until a governed decision policy exists for that sport. The machinery to fund
   them is built, tested and scheduled; nothing about it is waiting on this
   work.

3. **The `bankroll` endpoint and the per-era view still report era capital.**
   They are unchanged on purpose — that surface is about a model era, and its
   `starting_bankroll` is the right number *for an era*. The season account is
   the authority for what the sport's bankroll is, and the two now sit on the
   same page saying different, both-true things. Consolidating them would be a
   larger change to a surface this task did not ask me to replace.

4. **One MMA judgement call.** A calendar-year campaign is a product convention
   adopted so the sport can hold an account on the same terms as the others. It
   is marked as a convention in the data and shown as one on the page.
