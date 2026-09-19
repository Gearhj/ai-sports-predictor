"""The NBA Gen1 production consumer: verify discovery, import packages, bind games.

WHAT RUNS IT
------------
The existing exchange consumer (``consume-model-exchange-research-artifacts``)
calls :func:`consume` when -- and only when -- the handoff is pinned, verified
and ``NBA_PRODUCTION_CONSUMER_ENABLED`` is on (status ``ACTIVE``). The NBA paper
checkpoint calls it too, just before deciding. One function, no second schedule.

TWO DISCOVERY FORMATS
---------------------
* V2 (``slate-discovery/current.json`` -> content-addressed immutable manifest):
  the slate publisher's format. Each package entry carries a bound publication
  receipt (whose ``committedAtUtc`` is THE publication clock), ``currentlyEligible``
  and a lease ``eligibilityValidUntilUtc``. Predecessors are walked and retained.
  The manifest's release must bind the pinned model handoff.
* V1 (``discovery/current.json``): the one-game file behind PHI at NYK. It carries
  no lease at all, so a V1 package is imported for display and scoring but is
  NEVER decision-eligible (lease state ``NO_LEASE_IN_SCHEMA``).

THREE THINGS KEPT APART (as for CFB)
------------------------------------
Forecast history (immutable rows, never deleted), current display (newest
verified revision by the designated publication clock), and present decision
eligibility (:func:`present_eligibility`: the NEWEST verified manifest lists the
exact forecast as currently eligible with an unexpired lease, before tipoff).
AISP never extends a lease.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nba import gen1_common as common
from app.nba import gen1_handoff as handoff
from app.nba import gen1_identity as identity
from app.nba import gen1_package as package
from app.nba import gen1_source as source

CURRENT = "CURRENT"
EXPIRED_LEASE = "EXPIRED_LEASE"
HISTORICAL = "HISTORICAL"
NO_LEASE = "NO_LEASE_IN_SCHEMA"
NOT_IN_CURRENT_DISCOVERY = "NOT_IN_CURRENT_DISCOVERY"
STARTED = "GAME_STARTED"
NO_DISCOVERY = "NO_VERIFIED_DISCOVERY"

CLOCK_V1 = "V1_PACKAGE_PUBLISHED_AT"
CLOCK_V2 = "V2_DISCOVERY_RECEIPT_COMMITTED_AT"


def _lease(entry: dict[str, Any], now: datetime) -> tuple[str, datetime | None]:
    until = common.ts(entry.get("eligibilityValidUntilUtc"))
    if entry.get("currentlyEligible") is True:
        return (CURRENT if until is not None and now < until else EXPIRED_LEASE), until
    return HISTORICAL, until


def _orientation(session: Session, verified: dict[str, Any], objects: list[source.SourceObject]) -> dict[str, Any]:
    """Independent orientation from the retained ESPN schedule object the forecast names."""

    game = verified["forecast"]["game"]
    obj, row = source.row_for(objects, str(game["source_game_id"]))
    if row is None:
        return {"state": "UNCONFIRMED_NO_RETAINED_SOURCE_ROW"}
    revision = str(game.get("source_revision") or "")
    detail = {"source_object_sha256": obj.sha256, "names_this_object": obj.sha256 in revision}
    tipoff = common.ts(game.get("scheduled_tipoff_utc"))
    raw_start = common.ts(row.get("start_date"))
    if raw_start is None or tipoff is None or raw_start != tipoff:
        return {**detail, "state": "DISAGREES", "reason": "RETAINED_SOURCE_TIPOFF_DIFFERS",
                "source_tipoff": common.iso(raw_start)}
    from app.models import ExternalIdMapping

    known = {}
    for side in ("home", "away"):
        mapping = session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_FRANCHISE,
            ExternalIdMapping.external_id == game[f"{side}_team_id"]))
        espn_team = None
        if mapping is not None:
            espn_team = session.scalar(select(ExternalIdMapping.external_id).where(
                ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_ESPN_TEAM,
                ExternalIdMapping.entity_id == mapping.entity_id))
        known[side] = espn_team
    raw = {"home": str(row["home_id"]), "away": str(row["away_id"])}
    detail["source_espn_team_ids"] = raw
    if known["home"] and known["away"]:
        if known == raw:
            return {**detail, "state": "CONFIRMED", "espn_team_ids": raw}
        return {**detail, "state": "DISAGREES", "reason": "HOME_AWAY_DIFFER_FROM_BOUND_ESPN_TEAMS",
                "bound_espn_team_ids": known}
    if (known["home"] and known["home"] != raw["home"]) or (known["away"] and known["away"] != raw["away"]):
        return {**detail, "state": "DISAGREES", "reason": "ONE_SIDE_DIFFERS_FROM_BOUND_ESPN_TEAM"}
    return {**detail, "state": "BOUND_FROM_THIS_ROW", "espn_team_ids": raw,
            "note": "No earlier ESPN team binding existed; this row now binds the franchises."}


def import_verified(session: Session, verified: dict[str, Any], *, now: datetime,
                    discovery: dict[str, Any] | None, objects: list[source.SourceObject]) -> dict[str, Any]:
    """Write one immutable row (idempotent by forecast id + bytes) and bind its game."""

    from app.models import NbaGen1Forecast

    forecast = verified["forecast"]
    forecast_id = str(forecast["forecastId"])
    known = session.scalar(select(NbaGen1Forecast).where(NbaGen1Forecast.forecast_id == forecast_id))
    if known is not None:
        if known.forecast_sha256 != verified["forecast_sha256"]:
            raise package.PackageRefused([f"CONFLICTING_BYTES_FOR_KNOWN_FORECAST:{forecast_id}"])
        return {"result": "ALREADY_PRESENT", "row": known}
    orientation = _orientation(session, verified, objects)
    if orientation["state"] == "DISAGREES":
        raise package.PackageRefused([f"ORIENTATION_{orientation.get('reason')}"])
    game = forecast["game"]
    ridge, elo = forecast["ridgePointExpectations"], forecast["eloWinnerProbabilities"]
    summary = verified["joint_summary"]
    if discovery and discovery.get("committed_at_utc"):
        published, clock_source = common.ts(discovery["committed_at_utc"]), CLOCK_V2
    else:
        published, clock_source = common.ts(forecast.get("publishedAtUtc")), CLOCK_V1
    tipoff = common.ts(game["scheduled_tipoff_utc"])
    if published is None or published >= tipoff:
        raise package.PackageRefused(["PUBLICATION_CLOCK_MISSING_OR_NOT_BEFORE_TIPOFF"])
    row = NbaGen1Forecast(
        forecast_id=forecast_id, forecast_sha256=verified["forecast_sha256"],
        forecast_bytes=verified["forecast_bytes"], forecast_schema=str(forecast["schema"]),
        package_index_sha256=verified["index_sha256"], package_index_bytes=verified["index_bytes"],
        envelope_schema=verified["schema"], revision_key=verified.get("revision_key"),
        configuration_identity=forecast["configurationIdentity"],
        distribution_configuration_identity=forecast["distributionConfigurationIdentity"],
        handoff_sha256=verified["handoff_sha256"], slate_release_sha256=verified.get("slate_release_sha256"),
        # The package's OWN clock, as its own bytes state it. For V2 it was proved
        # against the slate release THIS package binds, never against a newer one.
        prospective_effective_at_utc=common.ts(forecast.get("prospectiveEffectiveAtUtc")),
        codex_game_id=str(game["game_id"]), source_game_id=str(game["source_game_id"]),
        home_franchise_id=game["home_team_id"], away_franchise_id=game["away_team_id"],
        home_team_code=game["home_team_code"], away_team_code=game["away_team_code"],
        season_end=game.get("season_end"), season_type=game.get("season_type"),
        neutral_site=bool(game.get("neutral_site")), schedule_source_revision=game.get("source_revision"),
        captured_tipoff_utc=tipoff, captured_game_date=datetime.fromisoformat(str(game["game_date"])).date(),
        ridge_home_points=float(ridge["home"]), ridge_away_points=float(ridge["away"]),
        ridge_total_points=float(ridge["total"]), ridge_home_margin=float(ridge["homeMargin"]),
        elo_home_win_probability=float(elo["home"]), elo_away_win_probability=float(elo["away"]),
        elo_tie_probability=float(elo.get("tie", 0.0)), elo_method=elo.get("method"),
        joint_draw_count=summary["draw_count"], joint_home_win_probability=summary["winner"]["home"],
        joint_away_win_probability=summary["winner"]["away"], joint_home_win_mcse=summary["winner"]["home_mcse"],
        joint_expected_home=summary["expected"]["home"], joint_expected_away=summary["expected"]["away"],
        joint_expected_total=summary["expected"]["total"], joint_expected_margin=summary["expected"]["margin"],
        joint_summary={k: summary[k] for k in ("pmfs", "intervals", "illustrative_totals", "winner", "expected")},
        population_artifact_sha256=verified["population_artifact_sha256"],
        population_logical_sha256=verified["population_logical_sha256"],
        source_cutoff_utc=common.ts(forecast.get("sourceCutoffUtc")),
        forecast_created_utc=common.ts(forecast.get("createdAtUtc")),
        producer_published_utc=common.ts(forecast.get("publishedAtUtc")),
        exchange_published_utc=published, publication_clock_source=clock_source,
        discovery_receipt_sha256=(discovery or {}).get("receipt_sha256"),
        first_manifest_sha256=(discovery or {}).get("manifest_sha256"), imported_at=now,
        detail={"channel_semantics": forecast.get("channelSemantics"), "limitations": forecast.get("limitations"),
                "monitoring": forecast.get("monitoring"), "authority": forecast.get("authority"),
                "bindings": forecast.get("bindings"), "close_score_challenger": forecast.get("closeScoreChallenger"),
                "orientation": orientation, "members": verified["members"],
                "forecast_horizon": forecast.get("forecastHorizon")})
    session.add(row)
    session.flush()
    espn_ids = orientation.get("espn_team_ids") or {}
    admitted = identity.admit_game(
        session, codex_game_id=game["game_id"], home_franchise_id=game["home_team_id"],
        away_franchise_id=game["away_team_id"], home_code=game["home_team_code"], away_code=game["away_team_code"],
        tipoff_utc=tipoff, season_end=int(game.get("season_end") or tipoff.year + 1),
        home_espn_team_id=espn_ids.get("home"), away_espn_team_id=espn_ids.get("away"))
    row.canonical_game_id = uuid.UUID(admitted["game_id"])
    session.flush()
    return {"result": "IMPORTED", "row": row, "admission": admitted, "orientation": orientation["state"]}


def _record(session: Session, *, sha: str, raw: bytes, doc: dict[str, Any], schema: str, lease_semantics: str,
            verdict: handoff.HandoffVerdict, now: datetime, predecessor: str | None, release: str | None):
    from app.models import NbaDiscoveryManifest

    existing = session.scalar(select(NbaDiscoveryManifest).where(NbaDiscoveryManifest.manifest_sha256 == sha))
    if existing is not None:
        return existing, False
    record = NbaDiscoveryManifest(
        manifest_sha256=sha, manifest_bytes=len(raw), schema_version=schema,
        producer_created_utc=common.ts(doc.get("createdAtUtc") or doc.get("updatedAtUtc")),
        predecessor_sha256=predecessor, release_sha256=release, handoff_sha256=verdict.detail.get("handoff_sha256"),
        packages_listed=len(doc.get("packages") or []), lease_semantics=lease_semantics,
        dispositions=_dispositions(doc), first_verified_at=now)
    session.add(record)
    session.flush()
    return record, True


def _dispositions(doc: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for game in doc.get("games") or []:
        key = str(game.get("disposition") or "?")
        out[key] = out.get(key, 0) + 1
    return out


def _entries(session: Session, record, entries: list[dict[str, Any]], *, verdict, now: datetime,
             objects: list[source.SourceObject], v2: bool, summary: dict[str, Any]) -> None:
    from app.models import NbaDiscoveryObservation

    # The PINNED MODEL HANDOFF's clock. V1 packages are compared against it; a V2
    # package carries its own slate release and is compared against THAT (see
    # gen1_package.verify_package), so a newer release never re-dates an older
    # preserved package.
    effective = (verdict.detail.get("identity") or {}).get("prospective_effective_at_utc")
    verified_count = current_count = 0
    for entry in entries:
        problems: list[str] = []
        forecast_id = str(entry.get("forecastId"))
        row_id = None
        lease, until = (_lease(entry, now) if v2 else (NO_LEASE, None))
        discovery = None
        try:
            ref = entry["index"]
            common.bound(ref, "package index")
            root = pathlib.Path(ref["path"]).parent
            if str(entry.get("packageAddress", "")).lower() != root.name.lower():
                problems.append("PACKAGE_ADDRESS_DIFFERS_FROM_DIRECTORY")
            if v2:
                receipt_raw = common.bound(entry["publicationReceipt"], "publication receipt")
                receipt = json.loads(receipt_raw)
                for key in ("forecastId", "gameId", "packageAddress", "revisionKey", "committedAtUtc"):
                    if str(receipt.get(key)) != str(entry.get(key)):
                        problems.append(f"RECEIPT_{key.upper()}_MISMATCH")
                discovery = {"committed_at_utc": entry.get("committedAtUtc"),
                             "receipt_sha256": str(entry["publicationReceipt"]["sha256"]).lower(),
                             "manifest_sha256": record.manifest_sha256}
            else:
                discovery = {"manifest_sha256": record.manifest_sha256}
            from app.models import NbaGen1Forecast

            known = session.scalar(select(NbaGen1Forecast).where(NbaGen1Forecast.forecast_id == forecast_id))
            if not problems and known is not None and known.package_index_sha256 == root.name.lower():
                # Light path: the population and every summary number were proved at the
                # first import, and the bytes cannot change without the index digest --
                # the directory name, just re-bound above -- changing too.
                if v2 and known.discovery_receipt_sha256 and \
                        known.discovery_receipt_sha256 != discovery.get("receipt_sha256"):
                    problems.append("RECEIPT_DIFFERS_FROM_FIRST_IMPORT")
                else:
                    row_id = known.id
                    summary["duplicate"] += 1
            elif not problems:
                verified = package.verify_package(root, handoff_sha256=verdict.detail["handoff_sha256"],
                                                  effective_at=effective)
                if str(verified["forecast"]["forecastId"]) != forecast_id:
                    problems.append("FORECAST_ID_DIFFERS_FROM_DISCOVERY")
                if str(verified["forecast"]["game"]["game_id"]) != str(entry.get("gameId")):
                    problems.append("GAME_ID_DIFFERS_FROM_DISCOVERY")
                if v2:
                    committed = common.ts(entry.get("committedAtUtc"))
                    created = common.ts(verified["forecast"].get("createdAtUtc"))
                    if committed is None or created is None or committed < created:
                        problems.append("COMMITTED_BEFORE_CREATED")
                if not problems:
                    # One SAVEPOINT per package: a refusal after the row was written
                    # (e.g. the game admission) leaves no half-imported forecast behind.
                    nested = session.begin_nested()
                    try:
                        result = import_verified(session, verified, now=now, discovery=discovery, objects=objects)
                        nested.commit()
                    except Exception:
                        nested.rollback()
                        raise
                    row_id = result["row"].id
                    summary["imported" if result["result"] == "IMPORTED" else "duplicate"] += 1
        except package.PackageRefused as exc:
            problems.extend(exc.problems)
        except (common.BindingRefused, identity.IdentityRefused, KeyError, ValueError, OSError) as exc:
            problems.append(f"{type(exc).__name__}:{exc}"[:200])
        verified_ok = not problems
        if verified_ok:
            verified_count += 1
            current_count += int(lease == CURRENT)
        else:
            summary["rejected"] += 1
            summary["rejections"].append({"forecast_id": forecast_id, "problems": problems[:4]})
        session.add(NbaDiscoveryObservation(
            manifest_id=record.id, manifest_sha256=record.manifest_sha256, forecast_id=forecast_id,
            nba_gen1_forecast_id=row_id if verified_ok else None, codex_game_id=str(entry.get("gameId")),
            package_address=str(entry.get("packageAddress", "")).lower(), producer_status=entry.get("status"),
            currently_eligible=bool(entry.get("currentlyEligible")) if v2 else False,
            eligibility_valid_until_utc=until, lease_at_check=lease, verified=verified_ok,
            problems=problems or None, checked_at=now))
    record.packages_verified = verified_count
    record.packages_current_at_check = current_count
    session.flush()


def consume(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass. Idempotent: a recorded discovery document is not re-imported."""

    moment = now or datetime.now(UTC)
    verdict = handoff.production_status(settings)
    summary: dict[str, Any] = {"handoff": {"status": verdict.status, "reason": verdict.reason,
                                           "handoff_sha256": verdict.detail.get("handoff_sha256")},
                               "checked_at": moment.isoformat(),
                               "imported": 0, "duplicate": 0, "rejected": 0, "rejections": [],
                               "manifests_recorded": [], "provider_requests": 0}
    if verdict.status != handoff.ACTIVE:
        summary["status"] = f"CONSUMER_NOT_ACTIVE:{verdict.status}:{verdict.reason}"[:200]
        return summary
    objects, source_problems = source.load_objects(settings)
    summary["source_objects"] = [o.describe() for o in objects]
    summary["source_problems"] = source_problems
    root = pathlib.Path(str(getattr(settings, "nba_exchange_root", "") or ""))
    pointer = root / "slate-discovery" / "current.json"
    if pointer.is_file():
        summary["format"] = "V2"
        from app.models import NbaDiscoveryManifest

        recorded = {sha for (sha,) in session.execute(select(NbaDiscoveryManifest.manifest_sha256)).all()}
        try:
            chain = _v2_chain(pointer, verdict, recorded=recorded)
        except (common.BindingRefused, KeyError, ValueError) as exc:
            summary["status"] = f"DISCOVERY_REFUSED:{exc}"[:300]
            return summary
        for ref, doc, raw, release_sha in chain:
            record, created = _record(session, sha=str(ref["sha256"]).lower(), raw=raw, doc=doc,
                                      schema=common.SLATE_DISCOVERY_SCHEMA, lease_semantics="V2_LEASE_PER_PACKAGE",
                                      verdict=verdict, now=moment,
                                      predecessor=str((doc.get("predecessor") or {}).get("sha256") or "") or None,
                                      release=release_sha)
            if created:
                _entries(session, record, list(doc.get("packages") or []), verdict=verdict, now=moment,
                         objects=objects, v2=True, summary=summary)
                summary["manifests_recorded"].append(record.manifest_sha256)
        summary["status"] = "V2_DISCOVERY_RECORDED" if summary["manifests_recorded"] else "V2_ALREADY_RECORDED"
        return summary
    legacy = root / "discovery" / "current.json"
    if not legacy.is_file():
        summary["status"] = "NO_DISCOVERY_DOCUMENT"
        return summary
    summary["format"] = "V1"
    raw = legacy.read_bytes()
    doc = json.loads(raw)
    if doc.get("schemaVersion") != common.DISCOVERY_V1_SCHEMA:
        summary["status"] = f"DISCOVERY_SCHEMA_UNKNOWN:{doc.get('schemaVersion')!r}"
        return summary
    record, created = _record(session, sha=common.sha256_bytes(raw), raw=raw, doc=doc,
                              schema=common.DISCOVERY_V1_SCHEMA, lease_semantics=NO_LEASE, verdict=verdict,
                              now=moment, predecessor=None, release=None)
    if created:
        _entries(session, record, list(doc.get("packages") or []), verdict=verdict, now=moment, objects=objects,
                 v2=False, summary=summary)
        summary["manifests_recorded"].append(record.manifest_sha256)
    summary["status"] = "V1_DISCOVERY_RECORDED" if created else "V1_ALREADY_RECORDED"
    return summary


def _v2_chain(pointer: pathlib.Path, verdict: handoff.HandoffVerdict, *, recorded: set[str] | None = None,
              max_depth: int = 500):
    """Pointer -> manifest -> predecessors, oldest first; each content-addressed and bound.

    The walk stops at the first manifest AISP already recorded: its ancestors
    were verified when it was. A 15-minute producer tick adds one manifest, so
    only the new ones are ever re-read.
    """

    doc_pointer = json.loads(pointer.read_bytes())
    if doc_pointer.get("schemaVersion") != common.SLATE_POINTER_SCHEMA:
        raise ValueError(f"pointer schema {doc_pointer.get('schemaVersion')!r}")
    chain = []
    ref = doc_pointer["manifest"]
    for _ in range(max_depth):
        if recorded and str(ref.get("sha256", "")).lower() in recorded:
            return list(reversed(chain))
        raw = common.bound(ref, "slate discovery manifest")
        if pathlib.Path(ref["path"]).parent.name.lower() != str(ref["sha256"]).lower():
            raise ValueError("discovery manifest is not stored at its content address")
        doc = json.loads(raw)
        if doc.get("schemaVersion") != common.SLATE_DISCOVERY_SCHEMA:
            raise ValueError(f"manifest schema {doc.get('schemaVersion')!r}")
        release = json.loads(common.bound(doc["release"], "slate release"))
        model = str((release.get("modelHandoff") or {}).get("sha256", "")).lower()
        if model != verdict.detail.get("handoff_sha256"):
            raise ValueError("RELEASE_DOES_NOT_BIND_PINNED_MODEL_HANDOFF")
        chain.append((ref, doc, raw, str(doc["release"]["sha256"]).lower()))
        if not doc.get("predecessor"):
            return list(reversed(chain))
        ref = doc["predecessor"]
    raise ValueError("CHAIN_TOO_LONG")


def newest_manifest(session: Session):
    from app.models import NbaDiscoveryManifest

    return session.scalar(select(NbaDiscoveryManifest).order_by(
        NbaDiscoveryManifest.producer_created_utc.desc().nullslast(),
        NbaDiscoveryManifest.first_verified_at.desc()).limit(1))


def present_eligibility(session: Session, forecast, *, now: datetime | None = None) -> dict[str, Any]:
    """Is this exact forecast decision-eligible at ``now``? Never extends a lease."""

    from app.models import NbaDiscoveryObservation

    moment = now or datetime.now(UTC)
    base = {"forecast_id": forecast.forecast_id, "evaluated_at": moment.isoformat()}
    tipoff = common.aware(forecast.captured_tipoff_utc)
    if tipoff is not None and moment >= tipoff:
        return {**base, "state": STARTED, "eligible": False,
                "detail": "The game has started; a pregame forecast gains no new authority."}
    manifest = newest_manifest(session)
    if manifest is None:
        return {**base, "state": NO_DISCOVERY, "eligible": False, "detail": "AISP has verified no NBA discovery."}
    observation = session.scalar(select(NbaDiscoveryObservation).where(
        NbaDiscoveryObservation.manifest_id == manifest.id,
        NbaDiscoveryObservation.forecast_id == forecast.forecast_id))
    base = {**base, "manifest_sha256": manifest.manifest_sha256, "manifest_schema": manifest.schema_version}
    if observation is None:
        return {**base, "state": NOT_IN_CURRENT_DISCOVERY, "eligible": False,
                "detail": "The newest verified discovery does not list this forecast."}
    if not observation.verified:
        return {**base, "state": "REJECTED", "eligible": False,
                "detail": "AISP could not verify this package in the newest discovery."}
    if observation.lease_at_check == NO_LEASE:
        return {**base, "state": NO_LEASE, "eligible": False,
                "detail": ("V1 discovery carries no eligibility lease, so this package is displayed and scored "
                           "but can never be decision-eligible. A V2 slate discovery lease is required.")}
    if not observation.currently_eligible:
        return {**base, "state": HISTORICAL, "eligible": False,
                "detail": "The producer lists this package as a preserved historical version."}
    until = common.aware(observation.eligibility_valid_until_utc)
    state = CURRENT if until is not None and moment < until else EXPIRED_LEASE
    return {**base, "state": state, "eligible": state == CURRENT, "lease_valid_until": common.iso(until),
            "detail": ("The newest verified discovery lists this forecast as currently eligible."
                       if state == CURRENT else "The producer's eligibility lease has expired.")}


def current_forecast_for_game(session: Session, *, espn_game_id: str | None = None, game_id: Any = None):
    """The newest verified production revision for a game, by its designated publication clock."""

    from app.models import NbaGen1Forecast

    query = select(NbaGen1Forecast)
    if espn_game_id is not None:
        query = query.where(NbaGen1Forecast.source_game_id == str(espn_game_id))
    if game_id is not None:
        query = query.where(NbaGen1Forecast.canonical_game_id == game_id)
    return session.scalar(query.order_by(NbaGen1Forecast.exchange_published_utc.desc().nullslast(),
                                         NbaGen1Forecast.imported_at.desc()).limit(1))


__all__ = ["CLOCK_V1", "CLOCK_V2", "CURRENT", "EXPIRED_LEASE", "HISTORICAL", "NO_DISCOVERY", "NO_LEASE",
           "NOT_IN_CURRENT_DISCOVERY", "STARTED", "consume", "current_forecast_for_game", "import_verified",
           "newest_manifest", "present_eligibility"]
