"""Pinning SportsGameOdds College Football identities: an exact binder and its review artifact.

WHY A REVIEW, AND NOT A MATCHER
-------------------------------
AISP has never observed a SportsGameOdds College Football event, so neither the
provider's league key nor a single team id is known. Guessing them (a league
spelled from the docs, a team id spelled from a school name) is how a price for
one game ends up on another. So an identity becomes usable only through two
steps, each leaving evidence:

1. ``review_events`` reads a RETAINED response (the raw archive's exact bytes)
   and proposes, per event, the CFBD game it can only be, and the two team-id
   pairs that follow from it. Nothing it proposes is used by any price path.
2. A person approves proposals by id; ``apply_review`` writes the reviewed team
   map (``AISP_CFB_SGO_TEAM_MAP_V1``) that ``sgo_price.load_binding`` reads, and
   the owner pins ``CFB_SGO_LEAGUE_ID`` to the league the artifact observed.

WHAT MAKES A PROPOSAL EXACT
---------------------------
* the event's ``leagueID`` is the pinned league (or, before pinning, the
  documented candidate the request was filtered by) -- one league per artifact;
* its ``status.startsAt`` is within five minutes of exactly ONE CFBD kickoff
  (every classification, from the independent schedule captures), and that
  CFBD game is within five minutes of exactly one event in the response --
  unique both ways;
* orientation is carried over as listed: SGO home -> CFBD home, SGO away ->
  CFBD away. Names never bind; they only VETO: when the provider's names read
  as the reverse orientation, the proposal is withheld (``ORIENTATION_SUSPECT``);
* the result stays one-to-one: no SGO team id for two schools, no school for two
  SGO ids, against the reviewed map and within the artifact.

Anything else is receipted with its reason and proposes nothing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

REVIEW_SCHEMA = "AISP_CFB_SGO_BINDING_REVIEW_V1"
TOLERANCE = timedelta(minutes=5)

PROPOSED = "PROPOSED"
ALREADY_REVIEWED = "ALREADY_REVIEWED"
INCOMPLETE_EVENT = "INCOMPLETE_EVENT"
LEAGUE_UNEXPECTED = "LEAGUE_UNEXPECTED"
NO_CFBD_CANDIDATE = "NO_CFBD_CANDIDATE"
AMBIGUOUS_START = "AMBIGUOUS_START"
AMBIGUOUS_IN_RESPONSE = "AMBIGUOUS_IN_RESPONSE"
CONFLICTS_WITH_REVIEWED_MAP = "CONFLICTS_WITH_REVIEWED_MAP"
CONFLICT_WITHIN_ARTIFACT = "CONFLICT_WITHIN_ARTIFACT"
ORIENTATION_SUSPECT = "ORIENTATION_SUSPECT"
SAME_TEAM_BOTH_SIDES = "SAME_TEAM_BOTH_SIDES"

#: Words that say nothing about which school a name is.
_NOISE = {"university", "of", "the", "at", "college", "u"}


@dataclass(frozen=True)
class ScheduleGame:
    cfbd_game_id: str
    kickoff: datetime
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    neutral_site: bool


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_schedule(settings: Any) -> list[ScheduleGame]:
    """Every timed CFBD game in the independent raw captures (all classifications), newest capture wins."""

    rows: dict[str, dict[str, Any]] = {}
    for raw_path in str(getattr(settings, "cfb_independent_schedule_path", "") or "").split(";"):
        path = pathlib.Path(raw_path.strip())
        if not raw_path.strip() or not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            rows[str(row.get("id"))] = row
    return schedule_from_rows(rows.values())


def schedule_from_rows(rows: Any) -> list[ScheduleGame]:
    out = []
    for row in rows:
        kickoff = _parse_time(row.get("startDate"))
        if kickoff is None or row.get("startTimeTBD") or row.get("homeId") is None or row.get("awayId") is None:
            continue
        out.append(ScheduleGame(str(row["id"]), kickoff, str(row["homeId"]), str(row["awayId"]),
                                str(row.get("homeTeam") or ""), str(row.get("awayTeam") or ""),
                                bool(row.get("neutralSite"))))
    return out


def _tokens(*names: Any) -> set[str]:
    words: set[str] = set()
    for name in names:
        words |= {w for w in re.split(r"[^a-z0-9]+", str(name or "").lower()) if w and w not in _NOISE}
    return words


def _likeness(sgo_names: dict[str, Any], cfbd_name: str) -> float:
    ours = _tokens(cfbd_name)
    theirs = _tokens(*(sgo_names or {}).values())
    if not ours or not theirs:
        return 0.0
    return len(ours & theirs) / len(ours)


def orientation_evidence(home_names: dict[str, Any], away_names: dict[str, Any], game: ScheduleGame) -> str:
    """Advisory only: names never bind, but names that read reversed withhold a proposal."""

    as_listed = _likeness(home_names, game.home_name) + _likeness(away_names, game.away_name)
    reversed_ = _likeness(home_names, game.away_name) + _likeness(away_names, game.home_name)
    if as_listed == 0 and reversed_ == 0:
        return "NAMES_INCONCLUSIVE"
    if reversed_ > as_listed:
        return "NAMES_READ_REVERSED"
    if as_listed > reversed_:
        return "NAMES_SUPPORT_ORIENTATION"
    return "NAMES_INCONCLUSIVE"


def body_from_archive(path: str | pathlib.Path) -> tuple[bytes, dict[str, Any]]:
    """The exact response bytes a raw-archive wrapper names, verified against its recorded sha256."""

    wrapper_path = pathlib.Path(path)
    if wrapper_path.name.endswith(".body.gz"):
        body = gzip.decompress(wrapper_path.read_bytes())
        return body, {"response_body_sha256": hashlib.sha256(body).hexdigest(), "wrapper": None}
    wrapper = json.loads(wrapper_path.read_text(encoding="utf-8"))
    meta = wrapper.get("metadata") or {}
    historical = meta.get("historical_metadata") or meta
    body_name = historical.get("response_body_file")
    if not body_name:
        raise ValueError("the archive wrapper names no response body")
    body = gzip.decompress((wrapper_path.parent / body_name).read_bytes())
    digest = hashlib.sha256(body).hexdigest()
    if historical.get("response_body_sha256") and historical["response_body_sha256"] != digest:
        raise ValueError("the retained response body does not match its recorded sha256")
    return body, {"response_body_sha256": digest, "wrapper": wrapper_path.name,
                  "requested_at": historical.get("requested_at"), "lane": historical.get("lane"),
                  "request_filters": historical.get("request_filters")}


def review_events(payload: Any, *, schedule: list[ScheduleGame], binding: Any, league_candidate: str | None,
                  source: dict[str, Any] | None = None, generated_at: datetime | None = None) -> dict[str, Any]:
    """Every event in one retained response, each with the exact binding it supports or why not."""

    data = [e for e in ((payload or {}).get("data") or []) if isinstance(e, dict)]
    pinned = getattr(binding, "league_id", None)
    expected_league = pinned or (league_candidate or None)
    reviewed = dict(getattr(binding, "team_map", {}) or {})
    reviewed_reverse = {cfbd: sgo for sgo, cfbd in reviewed.items()}

    starts = {str(e.get("eventID") or ""): _parse_time((e.get("status") or {}).get("startsAt")) for e in data}
    events: list[dict[str, Any]] = []
    for event in data:
        event_id = str(event.get("eventID") or "")
        league = str(event.get("leagueID") or "")
        start = starts.get(event_id)
        teams = event.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        home_id, away_id = str(home.get("teamID") or ""), str(away.get("teamID") or "")
        record: dict[str, Any] = {
            "event_id": event_id, "league_id": league, "starts_at": start.isoformat() if start else None,
            "home": {"team_id": home_id or None, "names": home.get("names") or {}},
            "away": {"team_id": away_id or None, "names": away.get("names") or {}},
            "odds_present": bool((event.get("status") or {}).get("oddsPresent")),
            "proposals": [],
        }
        if not (event_id and league and start and home_id and away_id):
            events.append({**record, "status": INCOMPLETE_EVENT})
            continue
        if expected_league and league != expected_league:
            events.append({**record, "status": LEAGUE_UNEXPECTED, "expected_league": expected_league})
            continue
        if home_id == away_id:
            events.append({**record, "status": SAME_TEAM_BOTH_SIDES})
            continue
        candidates = [g for g in schedule if abs(g.kickoff - start) <= TOLERANCE]
        record["cfbd_candidates"] = [g.cfbd_game_id for g in candidates]
        if not candidates:
            events.append({**record, "status": NO_CFBD_CANDIDATE})
            continue
        if len(candidates) > 1:
            events.append({**record, "status": AMBIGUOUS_START})
            continue
        game = candidates[0]
        rivals = [eid for eid, other in starts.items()
                  if eid != event_id and other is not None and abs(other - game.kickoff) <= TOLERANCE]
        if rivals:
            events.append({**record, "status": AMBIGUOUS_IN_RESPONSE, "rival_event_ids": rivals})
            continue
        evidence = orientation_evidence(home.get("names") or {}, away.get("names") or {}, game)
        record.update({
            "cfbd_game": {"game_id": game.cfbd_game_id, "kickoff": game.kickoff.isoformat(),
                          "home_id": game.home_id, "home_name": game.home_name,
                          "away_id": game.away_id, "away_name": game.away_name,
                          "neutral_site": game.neutral_site},
            "start_difference_seconds": round((start - game.kickoff).total_seconds(), 1),
            "name_evidence": evidence,
            "flags": (["NEUTRAL_SITE_ORIENTATION_NEEDS_REVIEW"] if game.neutral_site else []),
        })
        conflicts = []
        for side, sgo, cfbd in (("home", home_id, game.home_id), ("away", away_id, game.away_id)):
            if sgo in reviewed and reviewed[sgo] != cfbd:
                conflicts.append({"side": side, "sgo_team_id": sgo, "reviewed_cfbd_id": reviewed[sgo], "here": cfbd})
            if cfbd in reviewed_reverse and reviewed_reverse[cfbd] != sgo:
                conflicts.append({"side": side, "cfbd_team_id": cfbd, "reviewed_sgo_id": reviewed_reverse[cfbd],
                                  "here": sgo})
        if conflicts:
            events.append({**record, "status": CONFLICTS_WITH_REVIEWED_MAP, "conflicts": conflicts})
            continue
        if evidence == "NAMES_READ_REVERSED":
            events.append({**record, "status": ORIENTATION_SUSPECT})
            continue
        if reviewed.get(home_id) == game.home_id and reviewed.get(away_id) == game.away_id:
            events.append({**record, "status": ALREADY_REVIEWED})
            continue
        record["proposals"] = [
            {"proposal_id": f"{event_id}:{side}", "side": side, "sgo_team_id": sgo, "cfbd_team_id": cfbd,
             "cfbd_team_name": name, "sgo_names": names, "already_reviewed": reviewed.get(sgo) == cfbd}
            for side, sgo, cfbd, name, names in (
                ("home", home_id, game.home_id, game.home_name, home.get("names") or {}),
                ("away", away_id, game.away_id, game.away_name, away.get("names") or {}))]
        events.append({**record, "status": PROPOSED})

    # One-to-one inside the artifact too: an id proposed for two schools (or a
    # school for two ids) withdraws every proposal involved.
    by_sgo: dict[str, set[str]] = {}
    by_cfbd: dict[str, set[str]] = {}
    for record in events:
        for p in record["proposals"]:
            by_sgo.setdefault(p["sgo_team_id"], set()).add(p["cfbd_team_id"])
            by_cfbd.setdefault(p["cfbd_team_id"], set()).add(p["sgo_team_id"])
    for record in events:
        clash = [p for p in record["proposals"]
                 if len(by_sgo[p["sgo_team_id"]]) > 1 or len(by_cfbd[p["cfbd_team_id"]]) > 1]
        if clash:
            record["status"] = CONFLICT_WITHIN_ARTIFACT
            record["conflicts"] = [{"proposal_id": p["proposal_id"]} for p in clash]
            record["proposals"] = []

    observed = sorted({e["league_id"] for e in events if e["league_id"]})
    proposals = [p for e in events for p in e["proposals"]]
    artifact = {
        "schema": REVIEW_SCHEMA,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "source": dict(source or {}),
        # Pinnable only when the response returned exactly the league it was
        # filtered by (or the one already pinned). Anything else is for a person.
        "league": {"pinned": pinned, "request_candidate": league_candidate or None, "observed": observed,
                   "pinnable": observed[0] if len(observed) == 1 and observed[0] == (expected_league or observed[0])
                   else None},
        "tolerance_seconds": int(TOLERANCE.total_seconds()),
        "events": events,
        "proposals": proposals,
        "counts": {status: sum(1 for e in events if e["status"] == status)
                   for status in sorted({e["status"] for e in events})},
        "review_instructions": (
            "Approve a proposal only after confirming, for its event: the league, both schools and their "
            "home/away orientation (neutral-site games especially), and the kickoff. Approved entries are written "
            "to the reviewed team map by apply_review; pin CFB_SGO_LEAGUE_ID to league.pinnable. Nothing in this "
            "artifact prices a game."),
    }
    artifact["artifact_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in artifact.items() if k != "generated_at"}, sort_keys=True,
                   default=str).encode()).hexdigest()
    return artifact


def write_artifact(artifact: dict[str, Any], directory: str | pathlib.Path) -> str:
    """Write-once, content-addressed. Returns the path."""

    folder = pathlib.Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"cfb-sgo-binding-review-{artifact['artifact_sha256'][:16]}.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump(artifact, stream, indent=1, sort_keys=True, default=str)
    return str(path)


def review_directory(settings: Any) -> pathlib.Path | None:
    """Beside the raw archive (git-ignored). None when no raw root is configured: nothing is written then."""

    root = getattr(settings, "raw_data_root", None)
    return pathlib.Path(root) / "sportsgameodds-cfb-binding-review" if root else None


def apply_review(artifact: dict[str, Any], *, approve: list[str], reviewer: str,
                 existing: dict[str, Any] | None = None, reviewed_at: datetime | None = None) -> dict[str, Any]:
    """The reviewed team map after approving these proposal ids. Refuses anything not exact."""

    from app.cfb.sgo_price import TEAM_MAP_SCHEMA

    if artifact.get("schema") != REVIEW_SCHEMA:
        raise ValueError("not a CFB SportsGameOdds binding review artifact")
    if not reviewer.strip():
        raise ValueError("a reviewer must be named")
    league = (artifact.get("league") or {}).get("pinnable")
    if not league:
        raise ValueError("the artifact does not observe exactly one league")
    document = json.loads(json.dumps(existing)) if existing else {
        "schema": TEAM_MAP_SCHEMA, "leagueID": league, "entries": []}
    if document.get("schema") != TEAM_MAP_SCHEMA or document.get("leagueID") != league:
        raise ValueError("the existing team map is for another schema or league")
    proposals = {p["proposal_id"]: (e, p) for e in artifact.get("events") or [] for p in e.get("proposals") or []}
    unknown = [pid for pid in approve if pid not in proposals]
    if unknown:
        raise ValueError(f"not an approvable proposal: {unknown}")
    moment = (reviewed_at or datetime.now(UTC)).isoformat()
    entries = list(document.get("entries") or [])
    for pid in approve:
        event, proposal = proposals[pid]
        if event.get("status") != PROPOSED:
            raise ValueError(f"{pid} is {event.get('status')}, not PROPOSED")
        if any(e.get("sgoTeamID") == proposal["sgo_team_id"] and str(e.get("cfbdTeamId")) == proposal["cfbd_team_id"]
               for e in entries):
            continue
        entries.append({"sgoTeamID": proposal["sgo_team_id"], "cfbdTeamId": int(proposal["cfbd_team_id"]),
                        "reviewed": True, "reviewer": reviewer.strip(), "reviewedAtUtc": moment,
                        "evidence": {"artifactSha256": artifact.get("artifact_sha256"),
                                     "responseBodySha256": (artifact.get("source") or {}).get("response_body_sha256"),
                                     "eventID": event["event_id"], "side": proposal["side"],
                                     "cfbdGameId": (event.get("cfbd_game") or {}).get("game_id"),
                                     "sgoNames": proposal.get("sgo_names"),
                                     "cfbdTeamName": proposal.get("cfbd_team_name")}})
    sgo_ids = [e["sgoTeamID"] for e in entries if e.get("reviewed") is True]
    cfbd_ids = [str(e["cfbdTeamId"]) for e in entries if e.get("reviewed") is True]
    if len(set(sgo_ids)) != len(sgo_ids) or len(set(cfbd_ids)) != len(cfbd_ids):
        raise ValueError("the reviewed map would not be one-to-one")
    document["entries"] = entries
    return document


__all__ = [
    "ALREADY_REVIEWED", "AMBIGUOUS_IN_RESPONSE", "AMBIGUOUS_START", "CONFLICTS_WITH_REVIEWED_MAP",
    "CONFLICT_WITHIN_ARTIFACT", "INCOMPLETE_EVENT", "LEAGUE_UNEXPECTED", "NO_CFBD_CANDIDATE", "ORIENTATION_SUSPECT",
    "PROPOSED", "REVIEW_SCHEMA", "SAME_TEAM_BOTH_SIDES", "ScheduleGame", "apply_review", "body_from_archive",
    "load_schedule", "orientation_evidence", "review_directory", "review_events", "schedule_from_rows",
    "write_artifact",
]
