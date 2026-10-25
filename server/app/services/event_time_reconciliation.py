"""Pure event-clock reconciliation. Matching tolerance NEVER extends betting time.
Caller verifies sport/participants/occurrence, provider-ID mappings and quote age.
No network, model, database, schedule mutation, lease renewal or wagering here.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo
SLATE_TIMEZONE = ZoneInfo("America/New_York")
CONTRACT = "AISP_EVENT_CLOCK_WINDOW_V1"
MATCH_WINDOW = timedelta(hours=2)
AMBIGUITY_WINDOW = timedelta(hours=12)
#: LEGACY LEGS (hotfix 2026-10-24, review HIGH-4). A retained quote row written
#: before this contract carries no ``event_commence_time``. Per sport, the
#: minutes by which the kickoff such a row retained may differ from the
#: canonical start and still bind; a Settings field of the same name overrides
#: (``<sport>_legacy_quote_clock_tolerance_minutes``). None = refuse legacy legs.
LEGACY_TOLERANCE_DEFAULT_MINUTES = {"NFL": 120, "CFB": 120, "NHL": 20, "NBA": 20, "MLB": 20}
LEGACY_NO_PROVIDER_CLOCK = "LEGACY_NO_PROVIDER_CLOCK"
#: Where a legacy row kept the kickoff it was written against, in order of trust.
_LEGACY_START_KEYS = ("commence_time", "scheduled_start_utc", "kickoff_utc")


def legacy_clock_tolerance(sport: str, settings: Any = None) -> timedelta | None:
    """The legacy-leg tolerance for ``sport``: the setting when one is held, else the default.

    Read from ``settings`` when given, else from the process settings; never
    wider than MATCH_WINDOW. An unknown sport has no tolerance (legacy refused).
    """
    code = str(sport or "").upper()
    key = f"{code.lower()}_legacy_quote_clock_tolerance_minutes"
    if settings is None:
        try:
            from app.core.config import get_settings
            settings = get_settings()
        except Exception:  # noqa: BLE001 -- no process settings (pure callers): defaults
            settings = None
    minutes = getattr(settings, key, None) if settings is not None else None
    if minutes is None:
        minutes = LEGACY_TOLERANCE_DEFAULT_MINUTES.get(code)
    if minutes is None:
        return None
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return None
    if minutes < 0:
        return None
    return min(timedelta(minutes=minutes), MATCH_WINDOW)


def retained_start(raw: Mapping[str, Any]) -> Any:
    """The kickoff a quote row was written against: the event clock, else a legacy field."""
    if raw.get("event_commence_time"):
        return raw["event_commence_time"]
    for key in _LEGACY_START_KEYS:
        if raw.get(key):
            return raw[key]
    mapping = raw.get("identity_mapping")
    if isinstance(mapping, Mapping) and mapping.get("scheduled_start_utc"):
        return mapping["scheduled_start_utc"]
    return None

def utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return value.astimezone(UTC)

@dataclass(frozen=True)
class ClockAssessment:
    accepted: bool
    reason: str
    canonical_start: datetime | None = None
    provider_start: datetime | None = None
    earliest_start: datetime | None = None
    decision_deadline: datetime | None = None
    delta_seconds: float | None = None
    def receipt(self) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k,v in asdict(self).items()}

def reconcile_clock_identity(canonical_start, provider_start, *, window=MATCH_WINDOW):
    """Stage-independent clock compatibility, including postgame result identity.
    This says NOTHING about permission to trade, freshness or final-result status.
    Funding callers must additionally use assess_clock and the existing gates.
    """
    try: a,b=utc(canonical_start),utc(provider_start)
    except (ValueError,TypeError,OverflowError):
        return ClockAssessment(False,"MISSING_OR_INVALID_EVENT_CLOCK")
    if window < timedelta(0) or window > MATCH_WINDOW:
        raise ValueError("Window must be between zero and two hours")
    args=dict(canonical_start=a,provider_start=b,earliest_start=min(a,b),delta_seconds=(b-a).total_seconds())
    if abs(b-a)>window: return ClockAssessment(False,"START_OUTSIDE_MATCH_WINDOW",**args)
    if a.astimezone(SLATE_TIMEZONE).date()!=b.astimezone(SLATE_TIMEZONE).date():
        return ClockAssessment(False,"DIFFERENT_EASTERN_SLATE_DAY",**args)
    return ClockAssessment(True,"EXACT_START" if a==b else "BOUNDED_START_DIFFERENCE",**args)

def assess_clock(canonical_start, provider_start, observed_at, *, close_before=None, window=MATCH_WINDOW):
    """At intake close_before=None; at decision use the ORIGINAL policy close margin.
    Also recheck at commit after any slow I/O. The original inclusive T-15 endpoint
    is preserved, but a time at or after earliest first play always fails.
    """
    try:
        a,b,now = utc(canonical_start),utc(provider_start),utc(observed_at)
    except (ValueError,TypeError,OverflowError):
        return ClockAssessment(False,"MISSING_OR_INVALID_EVENT_CLOCK")
    if window < timedelta(0) or window > MATCH_WINDOW:
        raise ValueError("Window must be between zero and two hours")
    if close_before is not None and close_before < timedelta(0):
        raise ValueError("Negative pregame close margin")
    earliest = min(a,b)
    deadline = earliest-close_before if close_before is not None else earliest
    args=dict(canonical_start=a,provider_start=b,earliest_start=earliest,decision_deadline=deadline,delta_seconds=(b-a).total_seconds())
    if abs(b-a)>window: return ClockAssessment(False,"START_OUTSIDE_MATCH_WINDOW",**args)
    if a.astimezone(SLATE_TIMEZONE).date()!=b.astimezone(SLATE_TIMEZONE).date():
        return ClockAssessment(False,"DIFFERENT_EASTERN_SLATE_DAY",**args)
    if now>=earliest: return ClockAssessment(False,"EARLIEST_START_REACHED",**args)
    if close_before is not None and now>deadline: return ClockAssessment(False,"PREGAME_DEADLINE_PASSED",**args)
    return ClockAssessment(True,"EXACT_START" if a==b else "BOUNDED_START_DIFFERENCE",**args)

def resolve_candidate(*,sport,home_id,away_id,provider_start,observed_at,games,linked_game_id=None,occurrence=None):
    """Resolve exact identities first. Never pick nearest from a doubleheader.
    A previously verified provider ID or explicit occurrence may disambiguate.
    A contradictory mapping never silently falls back to another game.
    """
    if not sport or not home_id or not away_id or home_id==away_id:
        return {"accepted":False,"reason":"INVALID_PARTICIPANT_IDENTITY"}
    try: tip=utc(provider_start); utc(observed_at)
    except (TypeError,ValueError,OverflowError):
        return {"accepted":False,"reason":"MISSING_OR_INVALID_EVENT_CLOCK"}
    same=[]
    for game in games:
        if game.get("sport")!=sport or str(game.get("home"))!=home_id or str(game.get("away"))!=away_id: continue
        try: distance=abs(utc(game["starts_at"])-tip)
        except (KeyError,ValueError,TypeError,OverflowError):
            return {"accepted":False,"reason":"INVALID_CANONICAL_SCHEDULE"}
        if distance<=AMBIGUITY_WINDOW: same.append(game)
    if linked_game_id is not None:
        same=[g for g in same if str(g["game_id"])==linked_game_id]
        if len(same)!=1: return {"accepted":False,"reason":"PERSISTED_EVENT_ID_CONFLICT"}
    elif occurrence is not None: same=[g for g in same if g.get("occurrence")==occurrence]
    if not same: return {"accepted":False,"reason":"NO_GAME_FOR_EXACT_PARTICIPANTS"}
    if len(same)!=1: return {"accepted":False,"reason":"AMBIGUOUS_OCCURRENCE","candidate_ids":[str(g["game_id"]) for g in same]}
    game=same[0]
    status=getattr(game.get("status"),"value",game.get("status"))
    if str(status).upper()!="SCHEDULED": return {"accepted":False,"reason":"GAME_NOT_SCHEDULED"}
    check=assess_clock(game["starts_at"],tip,observed_at)
    return {**check.receipt(),"game_id":str(game["game_id"])}

def make_binding(*,game_id,sport,home_id,away_id,provider_event_id,canonical_start,provider_start,received_at):
    check=assess_clock(canonical_start,provider_start,received_at)
    if not check.accepted: raise ValueError(check.reason)
    if any(v is None or str(v)=="" for v in (game_id,sport,home_id,away_id,provider_event_id)):
        raise ValueError("Missing binding identity")
    if str(home_id)==str(away_id): raise ValueError("Participants must differ")
    return {"contract":CONTRACT,"game_id":str(game_id),"sport":sport,"home_id":str(home_id),"away_id":str(away_id),"provider_event_id":str(provider_event_id),"bound_at":utc(received_at).isoformat(),**check.receipt()}

def legacy_pair_verdict(home_raw,away_raw,*,home_id,away_id,canonical_start,moment,close_before,tolerance):
    """A pair with at least one leg written before the event-clock contract (hotfix 2026-10-24, HIGH-4).

    Identity is still exact: both legs must name the same non-empty provider
    event, agree on any identity mapping they retained, and keep their
    orientation where they state one. The clock is cushioned: the kickoff each
    leg retained must agree with the canonical start within ``tolerance``. The
    canonical start is then used as the provider clock, so the original
    availability / earliest-start / pregame-deadline gates apply unchanged and
    the cushion can never extend betting time. ``tolerance`` None refuses.
    """
    if tolerance is None:
        return {"accepted":False,"reason":"MISSING_OR_INVALID_EVENT_CLOCK"}
    try:
        canonical,now=utc(canonical_start),utc(moment)
        tolerance=min(timedelta(seconds=max(0.0,tolerance.total_seconds())),MATCH_WINDOW)
    except (ValueError,TypeError,OverflowError,AttributeError):
        return {"accepted":False,"reason":"MISSING_OR_INVALID_EVENT_CLOCK"}
    legs=(home_raw,away_raw)
    event_ids={str(r.get("provider_event_id") or "") for r in legs}
    if len(event_ids)!=1 or "" in event_ids:
        return {"accepted":False,"reason":"PAIR_PROVIDER_EVENT_DISAGREEMENT"}
    mappings=[r.get("identity_mapping") for r in legs]
    if all(isinstance(m,Mapping) for m in mappings):
        for key in ("canonical_event_id","home_team_id","away_team_id"):
            if mappings[0].get(key)!=mappings[1].get(key):
                return {"accepted":False,"reason":"LEGACY_IDENTITY_DISAGREEMENT"}
    for raw,expected in ((home_raw,"HOME"),(away_raw,"AWAY")):
        if raw.get("side") not in (None,"",expected):
            return {"accepted":False,"reason":"PAIR_ORIENTATION_CONFLICT"}
    try:
        starts=[utc(retained_start(r)) for r in legs]
    except (ValueError,TypeError,OverflowError):
        return {"accepted":False,"reason":"MISSING_OR_INVALID_EVENT_CLOCK"}
    deltas=[(s-canonical).total_seconds() for s in starts]
    legacy={"retained_starts":[s.isoformat() for s in starts],"retained_delta_seconds":deltas,
            "tolerance_seconds":tolerance.total_seconds(),"provider_event_id":event_ids.pop()}
    if any(abs(d)>tolerance.total_seconds() for d in deltas):
        return {"accepted":False,"reason":"LEGACY_START_OUTSIDE_TOLERANCE","legacy":legacy,
                "canonical_start":canonical.isoformat()}
    check=assess_clock(canonical,canonical,now,close_before=close_before)
    if not check.accepted: return {**check.receipt(),"legacy":legacy}
    return {**check.receipt(),"reason":LEGACY_NO_PROVIDER_CLOCK,"legacy":legacy}

def pair_clock_verdict(home_raw,away_raw,*,game_id,sport,home_id,away_id,canonical_start,moment,close_before,
                       legacy_tolerance=None):
    """Both original quote legs must bind the same current event and clocks.
    Existing exact-clock legacy pairs retain acceptance. Nonexact pairs require
    actual system-generated binding evidence on BOTH legs; no made-up receipt.
    A leg with NO event clock at all is a legacy leg: ``legacy_pair_verdict``
    (identity exact, clock within ``legacy_tolerance``; None refuses as before).
    Quote age, provider/book/market, grants/caps and commit guard remain external.
    """
    if not isinstance(home_raw,Mapping) or not isinstance(away_raw,Mapping):
        return {"accepted":False,"reason":"MISSING_RETAINED_LEG"}
    if any(not r.get("event_commence_time") for r in (home_raw,away_raw)):
        return legacy_pair_verdict(home_raw,away_raw,home_id=home_id,away_id=away_id,canonical_start=canonical_start,
                                   moment=moment,close_before=close_before,tolerance=legacy_tolerance)
    try:
        canonical,now=utc(canonical_start),utc(moment)
        starts=[utc(r["event_commence_time"]) for r in (home_raw,away_raw)]
    except (KeyError,ValueError,TypeError,OverflowError):
        return {"accepted":False,"reason":"MISSING_OR_INVALID_EVENT_CLOCK"}
    if starts[0]!=starts[1]: return {"accepted":False,"reason":"PAIR_EVENT_CLOCK_DISAGREEMENT"}
    check=assess_clock(canonical,starts[0],now,close_before=close_before)
    if not check.accepted: return check.receipt()
    bindings=[r.get("event_clock_binding") for r in (home_raw,away_raw)]
    if not any(bindings) and starts[0]==canonical: return {**check.receipt(),"reason":"LEGACY_EXACT_START"}
    if not all(isinstance(b,Mapping) for b in bindings) or bindings[0]!=bindings[1]:
        return {"accepted":False,"reason":"MISSING_OR_CONFLICTING_BINDING"}
    binding=bindings[0]
    expected={"contract":CONTRACT,"game_id":str(game_id),"sport":sport,"home_id":str(home_id),"away_id":str(away_id),"canonical_start":canonical.isoformat(),"provider_start":starts[0].isoformat()}
    if any(binding.get(k)!=v for k,v in expected.items()): return {"accepted":False,"reason":"BINDING_IDENTITY_OR_SCHEDULE_CHANGED"}
    event_id=binding.get("provider_event_id")
    if not event_id or any(str(r.get("provider_event_id",""))!=event_id for r in (home_raw,away_raw)):
        return {"accepted":False,"reason":"PAIR_PROVIDER_EVENT_DISAGREEMENT"}
    if home_raw.get("side")!="HOME" or away_raw.get("side")!="AWAY":
        return {"accepted":False,"reason":"PAIR_ORIENTATION_CONFLICT"}
    try:
        bound_at=utc(binding["bound_at"])
        if bound_at>now or bound_at>=check.earliest_start: return {"accepted":False,"reason":"BINDING_NOT_AVAILABLE_PREGAME"}
        for raw in (home_raw,away_raw):
            received=utc(raw["response_received_at"])
            if received!=bound_at or received>now: return {"accepted":False,"reason":"RECEIPT_BINDING_CLOCK_CONFLICT"}
    except (KeyError,ValueError,TypeError,OverflowError):
        return {"accepted":False,"reason":"MISSING_BINDING_CLOCK"}
    return check.receipt()
