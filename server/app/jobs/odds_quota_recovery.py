"""Bounded recovery from a latched Odds API quota guard.

THE LATCH
---------
The quota guard reads ``requests_remaining`` from the newest SUCCEEDED
``THE_ODDS_API`` ingestion run and refuses to spend when it is at or below the
floor. That reading can only ever be refreshed by making a request -- which is
exactly what the guard forbids. Once the provider reports a low figure, the
guard blocks forever, and it keeps blocking long after the provider's own quota
period has rolled over and the real remaining balance has recovered.

THE RECOVERY
------------
A fresh low reading is trustworthy and must be respected. A stale one is not
evidence about the balance now, only about the balance then. So exactly one
"probe" request is permitted when, and only when, every one of these holds:

  1. The guard is actually latched (newest reading <= floor).
  2. That reading is older than ``odds_quota_probe_after_hours`` -- long enough
     that it can no longer be treated as a statement about the balance now.
  3. No probe has been attempted within ``odds_quota_probe_cooldown_hours``,
     measured from persisted ledger evidence, so a provider that is genuinely
     exhausted is asked at most once per cooldown.

The probe is not an exemption from anything else. It still consumes the daily
request budget, still passes through the normal manual cooldown, and is still
reserved in the provider ledger before it is sent, so two workers cannot probe
concurrently. It never lowers the floor and never enables polling: it recovers
one reading, and the guard then applies to that reading normally.

If the probe comes back at or below the floor, the guard re-latches immediately
and the cooldown prevents another attempt.

ANCHORING, AND WHY IT IS FAIL-CLOSED
------------------------------------
The reading is anchored to the newest successful run that actually CARRIES a
``requests_remaining`` figure. A newer successful run without one says nothing
about the balance, so it must not be allowed to clear a latch that a real
reading set: absence of evidence is not evidence of quota.

RENEWAL IS A CALENDAR RESET, NOT AN ELAPSED WINDOW
--------------------------------------------------
The provider's credits reset on the FIRST OF EACH MONTH
(https://the-odds-api.com/manage/faqs.html). A reading taken before the most
recent reset is therefore obsolete as a statement about the balance now --
regardless of how many days old it is. A reading taken 40 days ago inside the
same billing month is still current; one taken 3 days ago across a month
boundary is not.

Crossing that boundary establishes only that the old number is UNCONFIRMED. It
does NOT establish that credits are available. The guard stays latched and
reports ``MAY_HAVE_RENEWED_PROBE_REQUIRED`` until an actual provider response
supplies a balance. Nothing is probed automatically: only an explicitly
authorized check refreshes the reading, and the documented ``/v4/sports/``
endpoint returns the quota headers without consuming a usage credit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import IngestionRun, ProviderRequestLedger, RunStatus

PROVIDER = "THE_ODDS_API"

#: Ledger endpoint key that marks a request as a quota-recovery probe. Probes
#: are countable and auditable exactly like any other provider request.
PROBE_ENDPOINT_KEY = "quota_recovery_probe"

#: Machine-readable guard states. A consumer must be able to tell "we know the
#: balance is below the floor" from "the persisted reading is too old to be a
#: statement about the balance now", because they call for different actions.
GUARD_NOT_LATCHED = "NOT_LATCHED"
GUARD_KNOWN_BELOW_FLOOR = "KNOWN_BELOW_FLOOR"
GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED = "MAY_HAVE_RENEWED_PROBE_REQUIRED"
GUARD_MAY_HAVE_RENEWED_PROBE_COOLING_DOWN = "MAY_HAVE_RENEWED_PROBE_COOLING_DOWN"
GUARD_NO_READING = "NO_QUOTA_READING_RECORDED"

#: The exact operator action that refreshes the persisted reading. It is never
#: run automatically and never by this module; it is reported so the one
#: authorized call is unambiguous.
AUTHORIZED_PROBE_COMMAND = (
    "python -m scripts.odds_quota_recovery_probe --balance-only  "
    "(one request to the documented zero-credit /v4/sports/ endpoint, which "
    "returns the quota headers without consuming a usage credit; add "
    "--dry-run to evaluate the guard without contacting the provider)"
)


@dataclass(frozen=True)
class QuotaState:
    """Everything the guard and the UI need, from persisted evidence only."""

    latched: bool
    remaining: int | None
    floor: int
    observed_at: datetime | None
    reading_age_hours: float | None
    probe_allowed: bool
    probe_blocked_reason: str | None
    last_probe_at: datetime | None
    latest_success_at: datetime | None
    #: One of the GUARD_* constants above.
    guard_state: str = GUARD_NO_READING
    #: True when the persisted reading is older than the provider's own quota
    #: period, so it can no longer describe the balance now.
    reading_may_have_renewed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "latched": self.latched,
            "remaining": self.remaining,
            "floor": self.floor,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "reading_age_hours": (
                round(self.reading_age_hours, 2)
                if self.reading_age_hours is not None else None
            ),
            "probe_allowed": self.probe_allowed,
            "probe_blocked_reason": self.probe_blocked_reason,
            "last_probe_at": (
                self.last_probe_at.isoformat() if self.last_probe_at else None
            ),
            "latest_successful_observation_at": (
                self.latest_success_at.isoformat() if self.latest_success_at else None
            ),
            "guard_state": self.guard_state,
            "reading_may_have_renewed": self.reading_may_have_renewed,
            "renewal_basis": "PROVIDER_MONTHLY_CREDIT_RESET_ON_THE_1ST",
            "balance_confirmed": self.remaining is not None and not self.reading_may_have_renewed,
            "authorized_probe_command": AUTHORIZED_PROBE_COMMAND,
        }


def _last_monthly_reset(now: datetime) -> datetime:
    """The most recent 1st-of-month boundary at or before ``now`` (UTC)."""

    return now.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _crossed_monthly_reset(observed_at: datetime | None, now: datetime) -> bool:
    """Whether the reading was taken before the most recent credit reset."""

    if observed_at is None:
        return False
    return observed_at.astimezone(UTC) < _last_monthly_reset(now)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def latest_successful_run(session: Session) -> IngestionRun | None:
    """The newest successful run that actually carries a quota reading.

    Fail-closed on purpose. A newer successful run whose metadata has no
    ``requests_remaining`` is silent about the balance; letting it become the
    anchor would clear a latch set by a real low reading and quietly resume
    spending on no evidence at all.
    """

    runs = session.scalars(
        select(IngestionRun)
        .where(IngestionRun.source == PROVIDER, IngestionRun.status == RunStatus.SUCCEEDED)
        .order_by(IngestionRun.completed_at.desc())
        .limit(_ANCHOR_SCAN_LIMIT)
    ).all()
    for run in runs:
        if (run.source_metadata or {}).get("requests_remaining") is not None:
            return run
    return None


#: How far back to look for a run carrying a quota reading. Bounded so this
#: never turns into a full-table scan.
_ANCHOR_SCAN_LIMIT = 50


def last_probe_at(session: Session) -> datetime | None:
    """When a probe was last RESERVED, whatever its outcome.

    Reserved rather than succeeded on purpose: a probe that failed still asked
    the provider, and must still start the cooldown.
    """

    return _aware(session.scalar(
        select(func.max(ProviderRequestLedger.requested_at)).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == PROBE_ENDPOINT_KEY,
        )
    ))


def quota_state(
    session: Session,
    *,
    floor: int,
    probe_after_hours: float,
    probe_cooldown_hours: float,
    # Accepted for call-site compatibility and no longer used to decide
    # obsolescence: renewal is the provider's monthly calendar reset, not an
    # elapsed window. Retained so a caller can still record what it assumed.
    renewal_days: float | None = None,
    now: datetime | None = None,
) -> QuotaState:
    """Evaluate the guard and whether one recovery probe is currently allowed."""

    now = now or datetime.now(UTC)
    run = latest_successful_run(session)
    observed_at = _aware(run.completed_at) if run else None
    remaining_raw = (run.source_metadata or {}).get("requests_remaining") if run else None
    remaining = int(remaining_raw) if remaining_raw is not None else None
    probe_at = last_probe_at(session)

    age_hours = (
        (now - observed_at).total_seconds() / 3600.0 if observed_at else None
    )
    latched = remaining is not None and remaining <= floor

    # Obsolete when the reading predates the most recent monthly reset, not
    # when some number of days has elapsed.
    may_have_renewed = bool(latched and _crossed_monthly_reset(observed_at, now))

    if not latched:
        return QuotaState(
            latched=False, remaining=remaining, floor=floor, observed_at=observed_at,
            reading_age_hours=age_hours, probe_allowed=False,
            probe_blocked_reason=(
                "The quota guard is not latched; no probe is needed."
                if remaining is not None
                else "No quota reading has been recorded, so the guard cannot latch."
            ),
            last_probe_at=probe_at, latest_success_at=observed_at,
            guard_state=GUARD_NOT_LATCHED if remaining is not None else GUARD_NO_READING,
            reading_may_have_renewed=False,
        )

    reason: str | None = None
    if age_hours is None:
        reason = "The latest quota reading has no timestamp, so its age cannot be established."
    elif age_hours < probe_after_hours:
        reason = (
            f"The quota reading is {age_hours:.1f}h old, still inside the "
            f"{probe_after_hours:.0f}h trust window; a fresh low reading is respected."
        )
    elif probe_at is not None and (now - probe_at) < timedelta(hours=probe_cooldown_hours):
        remaining_cooldown = timedelta(hours=probe_cooldown_hours) - (now - probe_at)
        reason = (
            "A recovery probe was already attempted "
            f"{int(remaining_cooldown.total_seconds() // 60)} minutes before its "
            f"{probe_cooldown_hours:.0f}h cooldown expires."
        )
    probe_allowed = reason is None

    if may_have_renewed and reason is not None and probe_at is not None:
        guard = GUARD_MAY_HAVE_RENEWED_PROBE_COOLING_DOWN
    elif may_have_renewed:
        guard = GUARD_MAY_HAVE_RENEWED_PROBE_REQUIRED
    else:
        guard = GUARD_KNOWN_BELOW_FLOOR

    if may_have_renewed:
        # The number is unconfirmed, which is not the same as available. This
        # changes no spending decision by itself.
        renewal_note = (
            "The reading predates the provider's monthly credit reset on the "
            f"1st ({_last_monthly_reset(now).date().isoformat()}), so it is "
            "unconfirmed rather than current. It does not establish that "
            "credits are available; only an authorized balance check can."
        )
        reason = f"{reason} {renewal_note}".strip() if reason else renewal_note

    return QuotaState(
        latched=True, remaining=remaining, floor=floor, observed_at=observed_at,
        reading_age_hours=age_hours,
        # A probe is still never taken automatically: this only reports that
        # the preconditions for one explicitly requested probe are satisfied.
        probe_allowed=probe_allowed,
        probe_blocked_reason=reason, last_probe_at=probe_at,
        latest_success_at=observed_at,
        guard_state=guard, reading_may_have_renewed=may_have_renewed,
    )


def missing_market_reason(state: QuotaState, latest_observation: datetime | None) -> str:
    """The actual reason sportsbook data is missing, in consumer language.

    Never says the feed is fine because the application is reachable: this
    describes the ingestion state, not the API's own health.
    """

    if not state.latched:
        if latest_observation is None:
            return "No sportsbook observation has been recorded yet."
        return (
            "Sportsbook ingestion is not currently blocked. The most recent "
            f"recorded observation is from {latest_observation.isoformat()}."
        )
    remaining = state.remaining
    detail = (
        f"The odds provider last reported {remaining} requests remaining, at or "
        f"below the configured floor of {state.floor}, so AISP stopped spending "
        "provider credit."
    )
    if state.probe_allowed:
        return (
            f"{detail} That reading is now stale, and a single bounded recovery "
            "probe is permitted on the next refresh."
        )
    return f"{detail} {state.probe_blocked_reason or ''}".strip()
