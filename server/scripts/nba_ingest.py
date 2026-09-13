"""Runnable NBA ingestion command.

    python server/scripts/nba_ingest.py status
    python server/scripts/nba_ingest.py ingest --from-dir tests/fixtures/nba
    python server/scripts/nba_ingest.py ingest --from-dir tests/fixtures/nba --replay

``status`` prints the entitlement assessment and what is currently stored. It
makes no network request and needs no credential.

``ingest`` reads payloads from a LOCAL directory and writes canonical rows. It
also makes no network request: the transport refuses to be constructed while
``app.nba.entitlement`` says collection is not permitted, so the only source of
bytes today is a local directory, and everything it writes is marked FIXTURE.

``--replay`` re-runs the same files to demonstrate idempotency without any
provider request at all.

When an entitlement is eventually evidenced, the same command gains a
``--fetch`` path; until then it is deliberately absent rather than present and
disabled, so there is nothing to switch on by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.data_sources.nba_sportsdataio import (  # noqa: E402
    ENDPOINTS, Envelope, parse_box_score, parse_games, parse_players, parse_teams,
)
from app.models import IngestionRun  # noqa: E402
from app.models.nba import (  # noqa: E402
    NbaGame, NbaPlayer, NbaPlayerGameStat, NbaRosterMembership,
    NbaSourcePayload, NbaTeam, NbaTeamGameStat,
)
from app.nba.entitlement import DataMode, assess  # noqa: E402
from app.nba.intake import IntakeOutcome, NbaIntake, reconcile_scores  # noqa: E402

#: Which fixture filename feeds which endpoint. Mirrors the documented paths so
#: the same code serves a real response the day one is permitted.
FILE_ENDPOINTS = {
    "AllTeams.json": ("teams", {}),
    "Players_AUR_BRI.json": ("players_by_team", {"team": "AUR"}),
    "Games_2026.json": ("schedule", {"season": "2026"}),
}


def _engine():
    load_dotenv(REPO_ROOT / ".env")
    return create_engine(os.environ["DATABASE_URL"])


def _counts(session: Session) -> dict[str, Any]:
    def n(model, **where):
        stmt = select(func.count()).select_from(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return session.scalar(stmt) or 0

    by_mode: dict[str, dict[str, int]] = {}
    for mode in ("REAL", "SCRAMBLED", "FIXTURE", "UNKNOWN"):
        counts = {
            "payloads": n(NbaSourcePayload, data_mode=mode),
            "teams": n(NbaTeam, data_mode=mode),
            "players": n(NbaPlayer, data_mode=mode),
            "games": n(NbaGame, data_mode=mode),
            "currentGames": session.scalar(
                select(func.count()).select_from(NbaGame).where(
                    NbaGame.data_mode == mode, NbaGame.is_current.is_(True))) or 0,
            "teamGameRows": n(NbaTeamGameStat, data_mode=mode),
            "playerGameRows": n(NbaPlayerGameStat, data_mode=mode),
            "rosterRows": n(NbaRosterMembership, data_mode=mode),
        }
        if any(counts.values()):
            by_mode[mode] = counts
    bytes_admitted = session.scalar(
        select(func.coalesce(func.sum(NbaSourcePayload.byte_count), 0))) or 0
    last = session.scalar(
        select(func.max(NbaSourcePayload.retrieved_at)))
    return {
        "byDataMode": by_mode,
        "totalOriginalBytesAdmitted": int(bytes_admitted),
        "lastSuccessfulIngestUtc": last.isoformat() if last else None,
    }


def cmd_status(args: argparse.Namespace) -> int:
    settings = Settings()
    with Session(_engine()) as session:
        runs = session.scalar(
            select(func.count()).select_from(IngestionRun).where(
                IngestionRun.source == "SPORTSDATAIO")) or 0
        entitlement = assess(settings, sportsdataio_runs=runs)
        payload = {
            "entitlement": entitlement.as_dict(),
            "stored": _counts(session),
            "endpointsImplemented": sorted(ENDPOINTS),
        }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = Settings()
    source_dir = Path(args.from_dir)
    if not source_dir.is_dir():
        raise SystemExit(f"no such directory: {source_dir}")

    engine = _engine()
    with Session(engine) as session:
        runs = session.scalar(
            select(func.count()).select_from(IngestionRun).where(
                IngestionRun.source == "SPORTSDATAIO")) or 0
        entitlement = assess(settings, sportsdataio_runs=runs)

        # The only mode a local directory can produce. Never REAL: these bytes
        # did not come from the provider, and saying so is the whole point.
        data_mode = DataMode.FIXTURE
        now = datetime.now(UTC)
        run = IngestionRun(
            source="SPORTSDATAIO_NBA_OFFLINE", job_name="nba-offline-ingest",
            status="RUNNING", started_at=now, records_processed=0,
            schema_version="AISP_NBA_SPORTSDATAIO_ADAPTER_V1",
            source_metadata={
                "dataMode": str(data_mode),
                "sourceDirectory": str(source_dir),
                "providerRequests": 0,
                "entitlementVerdict": entitlement.verdict,
                "networkCollectionAllowed": entitlement.network_collection_allowed,
            },
        )
        session.add(run)
        session.flush()

        intake = NbaIntake(session, data_mode=data_mode, retention_permitted=True)
        outcome = IntakeOutcome()
        reconciliations: list[dict[str, Any]] = []

        def envelope_for(path: Path, endpoint_key: str, parameters: dict) -> Envelope:
            return Envelope(
                endpoint_key=endpoint_key,
                url_template=ENDPOINTS.get(endpoint_key, "(local)"),
                parameters=parameters, payload=path.read_bytes(),
                retrieved_at=now, data_mode=data_mode,
                notes=(f"read from local file {path.name}; no provider request",),
            )

        # Identity first: teams, then players, then games and boxes.
        for name, (endpoint_key, parameters) in FILE_ENDPOINTS.items():
            path = source_dir / name
            if not path.is_file():
                continue
            envelope = envelope_for(path, endpoint_key, parameters)
            payload_row, created = intake.store_payload(envelope, ingestion_run_id=run.id)
            outcome.payloads_stored += int(created)
            outcome.payloads_reused += int(not created)
            if created:
                outcome.bytes_admitted += envelope.byte_count
            document = envelope.decode()
            if endpoint_key == "teams":
                for team in parse_teams(document):
                    _, made = intake.team(team, now=now, payload_id=payload_row.id)
                    outcome.teams_created += int(made)
            elif endpoint_key == "players_by_team":
                for parsed in parse_players(document):
                    player, made = intake.player(parsed, now=now, payload_id=payload_row.id)
                    outcome.players_created += int(made)
                    team = intake.team_by_provider_id(parsed.provider_team_id)
                    if team is not None and intake.roster(
                        parsed, team, player, observed_at=now, payload_id=payload_row.id
                    ):
                        outcome.roster_rows += 1
            elif endpoint_key == "schedule":
                for parsed in parse_games(document):
                    _, disposition = intake.game(
                        parsed, retrieved_at=now, payload_id=payload_row.id)
                    if disposition == "CREATED":
                        outcome.games_created += 1
                    elif disposition == "REVISED":
                        outcome.game_revisions += 1
                    elif disposition == "REUSED":
                        outcome.games_reused += 1
                    else:
                        outcome.notes.append(
                            f"game {parsed.provider_game_id}: {disposition}")

        for path in sorted(source_dir.glob("BoxScoreFinal_*.json")):
            envelope = envelope_for(path, "box_score_final",
                                    {"gameid": path.stem.split("_")[1]})
            payload_row, created = intake.store_payload(envelope, ingestion_run_id=run.id)
            outcome.payloads_stored += int(created)
            outcome.payloads_reused += int(not created)
            if created:
                outcome.bytes_admitted += envelope.byte_count
            box = parse_box_score(envelope.decode())
            intake.box_score(box, retrieved_at=now, payload_id=payload_row.id,
                             outcome=outcome)
            reconciliations.append({
                "providerGameId": box.game.provider_game_id,
                "file": path.name,
                **reconcile_scores(box),
            })

        run.status = "SUCCEEDED"
        run.completed_at = datetime.now(UTC)
        run.records_processed = (
            outcome.games_created + outcome.game_revisions
            + outcome.team_game_rows + outcome.player_game_rows
        )
        run.source_metadata = {**(run.source_metadata or {}),
                               "outcome": outcome.as_dict()}
        session.commit()

        print(json.dumps({
            "ingestionRunId": str(run.id),
            "dataMode": str(data_mode),
            "providerRequests": 0,
            "providerBytesDownloaded": 0,
            "originalBytesAdmittedThisRun": outcome.bytes_admitted,
            "outcome": outcome.as_dict(),
            "reconciliation": reconciliations,
            "stored": _counts(session),
        }, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="entitlement assessment and stored counts")
    ingest = sub.add_parser("ingest", help="ingest payloads from a local directory")
    ingest.add_argument("--from-dir", required=True)
    ingest.add_argument("--replay", action="store_true",
                        help="informational; a second run is idempotent by design")
    args = parser.parse_args()
    return {"status": cmd_status, "ingest": cmd_ingest}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
