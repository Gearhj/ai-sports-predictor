"""Clock-safe NBA role/availability features and an isolated market-offset challenger.

Consumes supplied records only: no network, database, scheduler or wagering writes.
The operating top-two rule is reproduced with a strict T-90 information boundary.
Minutes represent workload, not player quality or a made-up points adjustment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable

SCHEMA = "NBA_INJURY_FEATURES_V1"
ROLE_RULE = "NBA_S2D_MPG_QUARTER_FLOOR_T90_V1"
MODEL_RULE = "NBA_MARKET_OFFSET_INJURY_L2_1_V1"
STATUSES = ("OUT", "DOUBTFUL", "QUESTIONABLE", "PROBABLE", "AVAILABLE", "UNKNOWN")
TRIGGERS = frozenset(STATUSES[:3])
MIN_TRAIN_GAMES = 1000


def clock(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("NAIVE_CLOCK")
    return dt.astimezone(timezone.utc)


def identity(value: Any) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise ValueError("INVALID_NATIVE_ID")
    return value


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("INVALID_NUMBER")
    return float(value)


def probability(value: Any) -> float:
    p = number(value)
    if not 0 < p < 1:
        raise ValueError("INVALID_PROBABILITY")
    return p


def _unknown(reason: str, **detail: Any) -> dict[str, Any]:
    return {"state": "UNKNOWN", "reason": reason, **detail}


def roles(team_id: str, schedule: dict, boxes: dict[str, dict], *, starts_at: str,
          as_of: str, listed_at_freeze: Iterable[str] = (), retrospective: bool = False) -> dict:
    """Season-to-date operating rule. Caller supplies per-record held_at_utc receipts.

    The decision-time injury list must NEVER be passed as listed_at_freeze unless
    it was held before min(as_of,T90). A current roster is not inferred from names.
    Retrospective mode exposes timing failures and cannot become forward evidence.
    """
    tid = identity(team_id)
    cutoff = min(clock(as_of), clock(starts_at) - timedelta(minutes=90))
    listed = {identity(x) for x in listed_at_freeze}
    grade = "EX_POST_ROLE_RECONSTRUCTION" if retrospective else "PROSPECTIVE_HELD_INPUTS"
    base = {"role_rule": ROLE_RULE, "team_id": tid, "role_cutoff_utc": cutoff.isoformat(),
            "target_starts_at": clock(starts_at).isoformat(),
            "timing_grade": grade, "roster_coverage": "OBSERVED_APPEARANCES_ONLY"}
    if str(schedule.get("team")) != tid:
        return _unknown("SCHEDULE_TEAM_MISMATCH", **base)
    if not retrospective and (not schedule.get("held_at_utc") or clock(schedule["held_at_utc"]) > cutoff):
        return _unknown("SCHEDULE_NOT_HELD_BY_ROLE_CUTOFF", **base)
    events = schedule.get("events") or []
    ids = [identity(e["id"]) for e in events]
    if len(ids) != len(set(ids)):
        return _unknown("DUPLICATE_SCHEDULE_EVENT", **base)
    done = sorted((e for e in events if e.get("completed") is True and clock(e["date"]) < cutoff),
                  key=lambda e: (clock(e["date"]), e["id"]))
    target=clock(starts_at)
    season_start=datetime(target.year if target.month>=7 else target.year-1,7,1,tzinfo=timezone.utc)
    if any(clock(e["date"])<season_start for e in done):
        return _unknown("CROSS_SEASON_SCHEDULE_INPUT", **base)
    base.update(team_games=len(done), min_appearances=max(2, math.ceil(len(done) / 4)))
    if len(done) < 2:
        return _unknown("FEWER_THAN_TWO_PRIOR_GAMES", **base)
    played: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    recent: set[str] = set()
    for k, e in enumerate(done):
        b = boxes.get(e["id"])
        if b is None:
            return _unknown("BOX_NOT_HELD", missing_event=e["id"], **base)
        if b.get("espn_event_id") != e["id"] or b.get("completed") is not True or clock(b["date"]) != clock(e["date"]):
            return _unknown("BOX_IDENTITY_OR_COMPLETION_MISMATCH", missing_event=e["id"], **base)
        if b.get("season_type") != 2 and schedule.get("regular_season_scope") != "CONSUMER_TEAM_SCHEDULE_SEASONTYPE_2":
            return _unknown("NON_REGULAR_SEASON_BOX", **base)
        if not retrospective and (not b.get("held_at_utc") or clock(b["held_at_utc"]) > cutoff):
            return _unknown("BOX_NOT_HELD_BY_ROLE_CUTOFF", missing_event=e["id"], **base)
        rows = (b.get("teams") or {}).get(tid)
        if not rows:
            return _unknown("BOX_TEAM_NOT_PRESENT", missing_event=e["id"], **base)
        seen = set()
        total = 0.0
        for r in rows:
            aid = identity(r["espn_athlete_id"])
            if aid in seen:
                return _unknown("DUPLICATE_PLAYER_IN_TEAM_BOX", **base)
            seen.add(aid)
            minutes = number(r["minutes"])
            if not 0 <= minutes <= 80:
                return _unknown("INVALID_PLAYER_MINUTES", **base)
            total += minutes
            if minutes > 0:
                played.setdefault(aid, []).append(minutes)
                names[aid] = r.get("name", aid)
                if k >= len(done) - 5:
                    recent.add(aid)
        # ESPN's whole-minute display can round ~10 player entries either way.
        if not 228 <= total <= 400:
            return _unknown("INCOMPLETE_OR_INVALID_TEAM_MINUTES", team_minutes=total, **base)
    players = [{"espn_athlete_id": aid, "name": names[aid], "appearances": len(m),
                "minutes_per_appearance": sum(m) / len(m), "minutes_per_team_game": sum(m) / len(done),
                "recent_appearance": aid in recent}
               for aid, m in played.items()
               if len(m) >= base["min_appearances"] and (aid in recent or aid in listed)]
    players.sort(key=lambda r: (-r["minutes_per_appearance"], r["espn_athlete_id"]))
    if len(players) < 2:
        return _unknown("FEWER_THAN_TWO_ELIGIBLE_PLAYERS", **base)
    return {**base, "state": "KNOWN", "players": players, "stars": players[:2],
            "prior_event_ids": [e["id"] for e in done],
            "unseen_or_never_appeared_players": "UNKNOWN_NOT_ASSIGNED_ZERO_IMPACT"}


def select_snapshot(snapshots: Iterable[dict], *, event_id: str, as_of: str) -> dict | None:
    cutoff = clock(as_of)
    eligible = [s for s in snapshots if str(s.get("espn_event_id", s.get("espn_game_id"))) == identity(event_id)
                and clock(s["observed_at_utc"]) <= cutoff
                and (not s.get("published_at_utc") or clock(s["published_at_utc"]) <= cutoff)]
    if not eligible:
        return None
    eligible.sort(key=lambda s: (clock(s["observed_at_utc"]), s.get("source_sha256", "")))
    latest = eligible[-1]
    tied = [s for s in eligible if s["observed_at_utc"] == latest["observed_at_utc"]]
    if len({str(s.get("source_sha256", s.get("injuries"))) for s in tied}) > 1:
        raise ValueError("CONFLICTING_SNAPSHOT_CLOCK")
    return latest


def availability(team_roles: dict, snapshot: dict | None, *, as_of: str,
                 absence_means_available: bool = False) -> dict:
    """Explicit per-team completeness is required even if snapshot has an injury section."""
    base={"team_id":team_roles.get("team_id"),"target_starts_at":team_roles.get("target_starts_at"),
          "assessed_as_of":clock(as_of).isoformat(),"snapshot_event_id":None if snapshot is None else snapshot.get("espn_event_id",snapshot.get("espn_game_id"))}
    def unknown(reason,**detail):return _unknown(reason,**base,**detail)
    if team_roles.get("state") != "KNOWN":
        return unknown("ROLE_UNKNOWN", role_reason=team_roles.get("reason"))
    if snapshot is None:
        return unknown("NO_SNAPSHOT_HELD")
    cutoff, observed = clock(as_of), clock(snapshot["observed_at_utc"])
    if observed > cutoff or (snapshot.get("published_at_utc") and clock(snapshot["published_at_utc"]) > cutoff):
        return unknown("SNAPSHOT_AFTER_CUTOFF")
    if cutoff - observed > timedelta(minutes=45):
        return unknown("SNAPSHOT_STALE")
    tid = team_roles["team_id"]
    if not snapshot.get("injury_section_present") or tid not in (snapshot.get("injuries") or {}):
        return unknown("TEAM_INJURY_LIST_NOT_PRESENT")
    # A public report can say NOT_YET_SUBMITTED; an exception list is not an all-clear.
    coverage = (snapshot.get("team_coverage") or {}).get(tid, "UNQUALIFIED")
    if coverage != "COMPLETE":
        return unknown("TEAM_REPORT_NOT_QUALIFIED_COMPLETE", team_coverage=coverage)
    listed = {}
    for r in snapshot["injuries"][tid]:
        aid = identity(r["espn_athlete_id"])
        if aid in listed:
            return unknown("DUPLICATE_PLAYER_STATUS")
        if r.get("listed_at") and clock(r["listed_at"]) > cutoff:
            return unknown("PLAYER_STATUS_AFTER_CUTOFF")
        listed[aid] = r
    players = []
    burden = {s: 0.0 for s in STATUSES}
    stars = {r["espn_athlete_id"] for r in team_roles["stars"]}
    for p in team_roles["players"]:
        aid = p["espn_athlete_id"]
        status = listed.get(aid, {}).get("status", "AVAILABLE" if absence_means_available else "UNKNOWN")
        if status not in STATUSES:
            status = "UNKNOWN"
        burden[status] += p["minutes_per_team_game"]
        players.append({**p, "status": status, "star": aid in stars})
    star_out = [p for p in players if p["star"] and p["status"] in TRIGGERS]
    has_unknown = any(p["status"] == "UNKNOWN" for p in players)
    unranked = sorted(set(listed) - {p["espn_athlete_id"] for p in players})
    return {**base,"state": "KEY_OUT" if star_out else "UNKNOWN" if has_unknown else "CLEAR",
            "reason": "TOP_TWO_TRIGGER" if star_out else "UNRESOLVED_PLAYER_STATUS" if has_unknown else "NO_TOP_TWO_TRIGGER",
            "players": players, "stars": [p for p in players if p["star"]], "burden_minutes": burden,
            "burden_complete_for_observed_roles": not has_unknown,
            "unranked_injury_player_ids": unranked, "unranked_impact": "UNKNOWN",
            "snapshot_observed_at_utc": observed.isoformat(), "source_sha256": snapshot.get("source_sha256"),
            "absence_means_available": absence_means_available, "coverage": coverage}


def game_features(home: dict, away: dict, *, event_id: str, starts_at: str, as_of: str,
                  home_team_id: str | None = None, away_team_id: str | None = None) -> dict:
    if clock(as_of) >= clock(starts_at):
        raise ValueError("PREGAME_FEATURE_AFTER_TIPOFF")
    for team,expected in ((home,home_team_id),(away,away_team_id)):
        if team.get("snapshot_event_id") is not None and team["snapshot_event_id"]!=identity(event_id):
            raise ValueError("AVAILABILITY_EVENT_MISMATCH")
        if team.get("target_starts_at") and clock(team["target_starts_at"])!=clock(starts_at):
            raise ValueError("ROLE_TARGET_START_MISMATCH")
        if team.get("assessed_as_of") and clock(team["assessed_as_of"])!=clock(as_of):
            raise ValueError("AVAILABILITY_ASOF_MISMATCH")
        if expected is not None and team.get("team_id") not in (None,identity(expected)):
            raise ValueError("AVAILABILITY_TEAM_MISMATCH")
    known = all(t.get("burden_complete_for_observed_roles") and not t.get("unranked_injury_player_ids") for t in (home, away))
    x = None if not known else [(away["burden_minutes"][s] - home["burden_minutes"][s]) / 240 for s in STATUSES[:3]]
    state = "KEY_OUT" if any(t.get("state") == "KEY_OUT" for t in (home, away)) else "CLEAR" if all(t.get("state") == "CLEAR" for t in (home, away)) else "UNKNOWN"
    return {"schema": SCHEMA, "event_id": identity(event_id), "starts_at": clock(starts_at).isoformat(),
            "as_of": clock(as_of).isoformat(), "state": state, "home": home, "away": away,
            "injury_vector": x, "injury_vector_known": known,
            "feature_order": ["away_minus_home_out_minutes_over_240", "away_minus_home_doubtful_minutes_over_240",
                              "away_minus_home_questionable_minutes_over_240"],
            "workload_is_player_quality": False, "wagering_authority": False}


def assess_supplied_game(game: dict, schedules: dict[str, dict], boxes: dict[str, dict],
                         snapshots: list[dict], *, as_of: str,
                         absence_means_available: bool = False) -> dict:
    """Consumer entry point for ONE already-normalized, native-ID-mapped source.

    The existing consumer owns capture/parsing/imports. This function reads supplied
    dictionaries, freezes role inputs at min(as_of,T90), and returns no action or
    adjusted probability. Invoke per independent source, then merge_team_sources()
    only with qualified dated official-to-ESPN mappings. Never mix source editions.
    """
    eid=identity(game["espn_event_id"])
    starts=clock(game["starts_at"])
    cutoff=min(clock(as_of),starts-timedelta(minutes=90))
    snapshot=select_snapshot(snapshots,event_id=eid,as_of=as_of)
    role_snapshot=select_snapshot(snapshots,event_id=eid,as_of=cutoff.isoformat())
    teams={}
    for side in ("home","away"):
        tid=identity(game["teams"][side]["espn_team_id"])
        listed=[]
        if role_snapshot and (role_snapshot.get("teams") or {}).get(side,{}).get("espn_team_id")==tid:
            listed=[r["espn_athlete_id"] for r in role_snapshot.get("injuries",{}).get(tid,[])]
        try:
            r=roles(tid,schedules.get(tid,{"team":tid}),boxes,starts_at=game["starts_at"],as_of=as_of,
                    listed_at_freeze=listed)
            if snapshot and (snapshot.get("teams") or {}).get(side,{}).get("espn_team_id")!=tid:
                status=_unknown("SNAPSHOT_TEAM_BINDING_MISMATCH")
            else:
                status=availability(r,snapshot,as_of=as_of,absence_means_available=absence_means_available)
            teams[side]={**status,"roles":r}
        except (ValueError,KeyError,TypeError) as error:
            teams[side]=_unknown("SUPPLIED_INPUT_REJECTED:"+str(error))
    result=game_features(teams["home"],teams["away"],event_id=eid,starts_at=game["starts_at"],as_of=as_of,
                        home_team_id=game["teams"]["home"]["espn_team_id"],away_team_id=game["teams"]["away"]["espn_team_id"])
    result["role_stage"]="EARLY_PRE_T90" if clock(as_of)<starts-timedelta(minutes=90) else "T90_OR_LATER"
    return result


def merge_team_sources(team_roles: dict, sources: list[dict], *, as_of: str) -> dict:
    """Combine independently qualified, already ID-mapped source assessments.

    This is not a PDF parser or player-name matcher. Each assessment must have
    passed availability() with the SAME roles/cutoff. Unknown required sources
    prevent a complete workload vector; an explicit top-two trigger still holds.
    """
    if team_roles.get("state") != "KNOWN":
        return _unknown("ROLE_UNKNOWN")
    if not sources:
        return _unknown("NO_QUALIFIED_SOURCES")
    cutoff=clock(as_of)
    ids={p["espn_athlete_id"] for p in team_roles["players"]}
    star_ids={p["espn_athlete_id"] for p in team_roles["stars"]}
    order={s:i for i,s in enumerate(("AVAILABLE","PROBABLE","QUESTIONABLE","DOUBTFUL","OUT"))}
    complete=True
    valid=[]
    source_events={s["snapshot_event_id"] for s in sources if s.get("snapshot_event_id")}
    if len(source_events)>1:raise ValueError("MULTISOURCE_EVENT_MISMATCH")
    for source in sources:
        if (not source.get("snapshot_observed_at_utc") or clock(source["snapshot_observed_at_utc"])>cutoff
                or cutoff-clock(source["snapshot_observed_at_utc"])>timedelta(minutes=45)
                or source.get("team_id")!=team_roles["team_id"]
                or source.get("target_starts_at")!=team_roles["target_starts_at"]
                or source.get("assessed_as_of")!=cutoff.isoformat()):
            complete=False;continue
        supplied={p["espn_athlete_id"] for p in source.get("players",[])}
        if supplied!=ids:
            complete=False;continue
        valid.append({p["espn_athlete_id"]:p for p in source["players"]})
        if not source.get("burden_complete_for_observed_roles") or source.get("unranked_injury_player_ids"):
            complete=False
    players=[];burden={s:0.0 for s in STATUSES};conflicts=[]
    for p in team_roles["players"]:
        statuses=[s[p["espn_athlete_id"]]["status"] for s in valid]
        recognized=[s for s in statuses if s in order]
        status=max(recognized,key=order.get) if recognized else "UNKNOWN"
        if "UNKNOWN" in statuses or not complete:
            # Known adverse evidence is retained; an unresolved source cannot clear it.
            if status not in TRIGGERS:status="UNKNOWN"
        if len(set(recognized))>1:conflicts.append(p["espn_athlete_id"])
        players.append({**p,"status":status,"star":p["espn_athlete_id"] in star_ids})
        burden[status]+=p["minutes_per_team_game"]
    trigger=any(p["star"] and p["status"] in TRIGGERS for p in players)
    unknown=not complete or any(p["status"]=="UNKNOWN" for p in players)
    return {"state":"KEY_OUT" if trigger else "UNKNOWN" if unknown else "CLEAR",
            "team_id":team_roles["team_id"],"target_starts_at":team_roles["target_starts_at"],
            "assessed_as_of":cutoff.isoformat(),"snapshot_event_id":next(iter(source_events),None),
            "reason":"MULTISOURCE_TOP_TWO_TRIGGER" if trigger else "MULTISOURCE_UNRESOLVED" if unknown else "MULTISOURCE_COMPLETE",
            "players":players,"stars":[p for p in players if p["star"]],"burden_minutes":burden,
            "burden_complete_for_observed_roles":not unknown,"unranked_injury_player_ids":sorted(set().union(*(set(s.get("unranked_injury_player_ids",[])) for s in sources))),
            "source_conflict_player_ids":conflicts,"source_count":len(sources),
            "snapshot_observed_at_utc":max((s["snapshot_observed_at_utc"] for s in sources if s.get("snapshot_observed_at_utc")),key=clock,default=None),
            "source_sha256s":[s.get("source_sha256") for s in sources]}


def market_consensus(pairs: Iterable[dict], *, event_id: str, as_of: str, min_books: int = 3,
                     max_age_minutes: int = 45) -> dict:
    """One equal-weight mean of book-wise two-sided no-vig probabilities at SAME cutoff.

    Pair records carry matching home/away game, period, contract, source and market
    IDs. The caller must not pair across books/sources or substitute closing odds.
    """
    cutoff = clock(as_of)
    latest: dict[str, dict] = {}
    ambiguous: set[str] = set()
    rejected = []
    for pair in pairs:
        try:
            if pair.get("event_id") != identity(event_id) or pair.get("contract") != "NBA_FULL_GAME_OT_TWO_WAY_MONEYLINE":
                raise ValueError("PAIR_IDENTITY_OR_CONTRACT")
            if not pair.get("book") or not pair.get("source") or not pair.get("pair_id"):
                raise ValueError("PAIR_LINEAGE_MISSING")
            leg_times = [clock(pair[k]) for k in ("home_observed_at", "away_observed_at", "home_available_at", "away_available_at")]
            if max(leg_times) > cutoff or cutoff - min(leg_times[:2]) > timedelta(minutes=max_age_minutes):
                raise ValueError("PAIR_CLOCK")
            hd, ad = number(pair["home_decimal"]), number(pair["away_decimal"])
            if min(hd, ad) <= 1:
                raise ValueError("PAIR_ODDS")
            key = str(pair["book"])
            record = {**pair, "q_home": (1 / hd) / (1 / hd + 1 / ad), "_clock": max(leg_times)}
            if key not in latest or record["_clock"] > latest[key]["_clock"]:
                latest[key] = record
            elif record["_clock"] == latest[key]["_clock"] and record["pair_id"] != latest[key]["pair_id"]:
                ambiguous.add(key)
                raise ValueError("AMBIGUOUS_BOOK_PAIR")
        except (ValueError, KeyError, TypeError) as error:
            rejected.append(str(error))
    accepted = [{k: v for k, v in r.items() if k != "_clock"} for key, r in sorted(latest.items()) if key not in ambiguous]
    return {"state": "KNOWN" if len(accepted) >= min_books else "INSUFFICIENT_BOOKS", "book_count": len(accepted),
            "q_home": sum(r["q_home"] for r in accepted) / len(accepted) if len(accepted) >= min_books else None,
            "pairs": accepted, "rejected": rejected, "as_of": cutoff.isoformat()}


def _logit(p: float) -> float:
    p = probability(p)
    return math.log(p) - math.log1p(-p)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


@dataclass(frozen=True)
class ResidualModel:
    """R and RI fitted only on explicit admitted, earlier, unique-game rows.

    No threshold tuning, no automatic promotion, no fabricated injury coefficients.
    Fit gate is the standing >=1000 priced-game requirement, not 150/300 scorecards.
    """
    control_weight: float
    injury_weights: tuple[float, float, float, float] | None
    trained_through: str
    trained_games: int
    injury_trained_games: int
    fitted_at: str
    rule: str = MODEL_RULE

    def __post_init__(self):
        if not 0 <= number(self.control_weight) <= 1 or self.trained_games < MIN_TRAIN_GAMES:
            raise ValueError("INVALID_CONTROL_MODEL")
        if self.injury_weights is not None:
            if len(self.injury_weights) != 4 or self.injury_trained_games < MIN_TRAIN_GAMES:
                raise ValueError("INVALID_INJURY_MODEL")
            if not 0 <= number(self.injury_weights[0]) <= 1:
                raise ValueError("INVALID_INJURY_CONTROL_WEIGHT")
            for w in self.injury_weights:
                number(w)
        if clock(self.trained_through) >= clock(self.fitted_at):
            raise ValueError("FIT_BEFORE_TRAINING_COMPLETE")

    def predict(self, *, q_home: float, gen1_home: float | None, features: dict, as_of: str) -> dict:
        if clock(self.fitted_at) >= clock(as_of):
            raise ValueError("TRAINING_NOT_STRICTLY_EARLIER")
        q = probability(q_home)
        if gen1_home is None:
            return {"market": q, "control": q, "injury": q, "fallback": "NO_GEN1_MARKET_EXACT"}
        gap = _logit(gen1_home) - _logit(q)
        r = _sigmoid(_logit(q) + self.control_weight * gap)
        if clock(features["as_of"]) > clock(as_of):
            raise ValueError("FEATURES_AFTER_PREDICTION")
        if not features.get("injury_vector_known") or self.injury_weights is None:
            return {"market": q, "control": r, "injury": r, "fallback": "UNKNOWN_OR_UNFITTED_INJURY_CONTROL_EXACT"}
        x = [gap] + [number(v) for v in features["injury_vector"]]
        if len(x) != 4:
            raise ValueError("FEATURE_DIMENSION")
        ri = _sigmoid(_logit(q) + sum(w * v for w, v in zip(self.injury_weights, x)))
        return {"market": q, "control": r, "injury": ri, "fallback": None}

    def to_dict(self) -> dict:
        return asdict(self)


def fit_residual(rows: list[dict], *, fit_at: str) -> ResidualModel:
    """Fixed summed Bernoulli NLL + 0.5*||beta||², no intercept, beta_r in [0,1].

    Caller admission must confirm rights, as-known sources, original forecast,
    three-book same-cutoff market and permitted labels. Previously inspected rows
    remain Development. This function does not confer admission or fit authority.
    """
    import numpy as np
    from scipy.optimize import minimize
    if len(rows) < MIN_TRAIN_GAMES:
        raise ValueError("NEED_1000_ADMITTED_PRICED_GAMES")
    ids = [r["event_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_TRAINING_GAME")
    for r in rows:
        identity(r["event_id"])
        if r.get("admitted") is not True or r.get("timing_grade") != "PROSPECTIVE_HELD_INPUTS":
            raise ValueError("UNADMITTED_TRAINING_ROW")
        if not (clock(r["as_of"]) < clock(r["starts_at"]) <= clock(r["outcome_available_at"]) < clock(fit_at)):
            raise ValueError("TRAINING_CLOCK")
        if r["y_home"] not in (0, 1) or isinstance(r["y_home"], bool):
            raise ValueError("INVALID_OUTCOME")
        if r.get("market_book_count", 0) < 3:
            raise ValueError("INSUFFICIENT_TRAINING_MARKET")
        probability(r["q_home"]); probability(r["gen1_home"])

    def train(subset: list[dict], injury: bool):
        offset = np.array([_logit(r["q_home"]) for r in subset])
        x = np.array([[_logit(r["gen1_home"]) - _logit(r["q_home"])] +
                      ([number(v) for v in r["injury_vector"]] if injury else []) for r in subset])
        if x.shape[1] != (4 if injury else 1):
            raise ValueError("FEATURE_DIMENSION")
        y = np.array([r["y_home"] for r in subset])
        def loss(beta):
            z = offset + x @ beta
            obj = np.logaddexp(0, z).sum() - y @ z + .5 * (beta @ beta)
            p = np.array([_sigmoid(float(v)) for v in z])
            return obj, x.T @ (p - y) + beta
        result = minimize(loss, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B",
                          bounds=[(0, 1)] + ([(None, None)] * 3 if injury else []))
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError("FIT_FAILED")
        return tuple(float(v) for v in result.x)
    complete = [r for r in rows if r.get("injury_vector_known") is True]
    control = train(rows, False)[0]
    weights = train(complete, True) if len(complete) >= MIN_TRAIN_GAMES else None
    through = max(clock(r["outcome_available_at"]) for r in rows).isoformat()
    return ResidualModel(control, weights, through, len(rows), len(complete), clock(fit_at).isoformat())
