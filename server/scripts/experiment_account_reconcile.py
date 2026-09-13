"""Before/after ledger reconciliation for the season-account migration.

Read-only. It opens a session, reads the existing per-era ledger and the
season-account ledger, and prints both side by side. It writes nothing and
resolves nothing: if the two disagree, the job is to see the disagreement, not
to post an entry that makes it go away.

    python server/scripts/experiment_account_reconcile.py before
    python server/scripts/experiment_account_reconcile.py after

``before`` reads only the pre-existing structures, so it runs on a database
that has not been migrated yet. ``after`` additionally reads the accounts and
compares them to the same per-era figures.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVER_ROOT.parent / ".env")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.models import (  # noqa: E402
    OfficialAspPosition,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
)

UNFUNDED_CONTROL_POLICY = "AISP_OPPORTUNITY_POLICY_V1"


def era_rows(session: Session) -> list[dict[str, Any]]:
    """Per-era capital exactly as the pre-existing service computes it."""

    rows: list[dict[str, Any]] = []
    for experiment in session.scalars(
        select(ShadowExperiment).order_by(ShadowExperiment.version)
    ):
        realized_pl = float(session.scalar(
            select(func.coalesce(func.sum(PositionSettlement.profit_loss), 0))
            .select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0.0)
        reserved = float(session.scalar(
            select(func.coalesce(func.sum(OfficialAspPosition.stake), 0)).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                OfficialAspPosition.status == PositionStatus.OPEN,
            )
        ) or 0.0)
        positions = int(session.scalar(
            select(func.count()).select_from(OfficialAspPosition).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id
            )
        ) or 0)
        settlements = int(session.scalar(
            select(func.count()).select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0)
        starting = float(experiment.starting_bankroll)
        rows.append({
            "version": experiment.version,
            "sport": experiment.sport,
            "policy": experiment.decision_policy,
            "era": experiment.model_era_id,
            "active": bool(experiment.is_active),
            "funded": experiment.decision_policy != UNFUNDED_CONTROL_POLICY,
            "starting_bankroll": starting,
            "realized_pl": round(realized_pl, 2),
            "era_bankroll": round(starting + realized_pl, 2),
            "reserved": round(reserved, 2),
            "positions": positions,
            "settlements": settlements,
        })
    return rows


def before(session: Session) -> dict[str, Any]:
    rows = era_rows(session)
    funded = [row for row in rows if row["funded"]]
    with_activity = [row for row in funded if row["positions"] or row["settlements"]]
    return {
        "eras": rows,
        "totals": {
            # Deliberately shown: this is the number a naive per-era sum
            # produces, and it is exactly what the season account must not
            # report. 1,000 was contributed; summing era openings claims more.
            "sum_of_era_starting_bankrolls": round(
                sum(row["starting_bankroll"] for row in funded), 2
            ),
            "settled_net_pl_all_funded_eras": round(
                sum(row["realized_pl"] for row in funded), 2
            ),
            "reserved_all_funded_eras": round(sum(row["reserved"] for row in funded), 2),
            "positions_all_funded_eras": sum(row["positions"] for row in funded),
            "settlements_all_funded_eras": sum(row["settlements"] for row in funded),
            "funded_eras_with_activity": len(with_activity),
        },
    }


def after(session: Session, settings: Settings) -> dict[str, Any]:
    from app.services.experiment_accounts import ExperimentAccountService

    service = ExperimentAccountService(session, settings)
    from app.models import ExperimentAccount

    payload: list[dict[str, Any]] = []
    for account in session.scalars(
        select(ExperimentAccount).order_by(ExperimentAccount.sport, ExperimentAccount.season_id)
    ):
        ledger = service.ledger(account)
        payload.append({
            "sport": account.sport,
            "season": account.season_id,
            "status": account.status.value,
            "tracking_started_on": (
                account.tracking_started_on.isoformat() if account.tracking_started_on else None
            ),
            "original_contribution": ledger.original_contribution,
            "settled_net_pl": ledger.settled_net_pl,
            "realized_bankroll": ledger.realized_bankroll,
            "reserved_stake": ledger.reserved_stake,
            "available_capital": ledger.available_capital,
            "settled_stake": ledger.settled_stake,
            "season_return_pct": ledger.season_return_pct,
            "settled_stake_roi_pct": ledger.settled_stake_roi_pct,
            "record": ledger.record,
            "open_positions": ledger.open_positions,
            "settled_positions": ledger.settled_positions,
            "eras": [
                {
                    "version": era.version,
                    "role": era.role.value,
                    "era": era.model_era_id,
                    "engine": era.controlling_engine,
                    "settled_net_pl": era.settled_net_pl,
                    "reserved": era.reserved_stake,
                    "open": era.open_positions,
                    "settled": era.settled_positions,
                }
                for era in ledger.eras
            ],
        })
    return {"accounts": payload}


def reconcile(pre: dict[str, Any], post: dict[str, Any]) -> list[str]:
    """Checks that must hold between the two views, stated as failures."""

    problems: list[str] = []
    funded_accounts = [a for a in post["accounts"] if a["original_contribution"] > 0]

    era_pl = pre["totals"]["settled_net_pl_all_funded_eras"]
    account_pl = round(sum(a["settled_net_pl"] for a in funded_accounts), 2)
    if abs(era_pl - account_pl) > 0.005:
        problems.append(
            f"Settled net P&L disagrees: per-era {era_pl} vs accounts {account_pl}. "
            "Every settlement must be counted once and only once."
        )

    era_reserved = pre["totals"]["reserved_all_funded_eras"]
    account_reserved = round(sum(a["reserved_stake"] for a in funded_accounts), 2)
    if abs(era_reserved - account_reserved) > 0.005:
        problems.append(
            f"Reserved stake disagrees: per-era {era_reserved} vs accounts {account_reserved}."
        )

    era_settlements = pre["totals"]["settlements_all_funded_eras"]
    account_settlements = sum(a["settled_positions"] for a in funded_accounts)
    if era_settlements != account_settlements:
        problems.append(
            f"Settlement count disagrees: per-era {era_settlements} vs accounts "
            f"{account_settlements}."
        )

    for account in funded_accounts:
        if abs(account["original_contribution"] - 1000.00) > 0.005:
            problems.append(
                f"{account['sport']} {account['season']} contributed "
                f"{account['original_contribution']}, not 1000.00. A season receives "
                "exactly one opening contribution."
            )
        expected = round(account["original_contribution"] + account["settled_net_pl"], 2)
        if abs(account["realized_bankroll"] - expected) > 0.005:
            problems.append(
                f"{account['sport']} {account['season']} realized bankroll "
                f"{account['realized_bankroll']} != contribution + settled P&L {expected}."
            )
        expected_available = round(
            max(0.0, account["realized_bankroll"] - account["reserved_stake"]), 2
        )
        if abs(account["available_capital"] - expected_available) > 0.005:
            problems.append(
                f"{account['sport']} {account['season']} available capital "
                f"{account['available_capital']} != realized - reserved {expected_available}."
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["before", "after"])
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--baseline", help="path to a saved 'before' JSON, for 'after'")
    parser.add_argument("--out", help="write the JSON payload here as well")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    settings = Settings()
    with Session(engine) as session:
        pre = before(session)
        payload: dict[str, Any] = {"phase": args.phase, "before": pre}
        if args.phase == "after":
            payload["after"] = after(session, settings)
            baseline = pre
            if args.baseline:
                baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))["before"]
            payload["reconciliation"] = reconcile(baseline, payload["after"])

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 1 if payload.get("reconciliation") else 0

    print(f"=== {args.phase.upper()}: per-era ledger (existing structures) ===")
    header = f"{'v':>2} {'policy':<38} {'era':<24} {'start':>9} {'P&L':>8} {'resv':>7} {'pos':>4} {'setl':>5}"
    print(header)
    for row in pre["eras"]:
        flag = "" if row["funded"] else "  (unfunded control)"
        print(
            f"{row['version']:>2} {row['policy']:<38} {str(row['era'] or '-')[:24]:<24} "
            f"{row['starting_bankroll']:>9.2f} {row['realized_pl']:>8.2f} "
            f"{row['reserved']:>7.2f} {row['positions']:>4} {row['settlements']:>5}{flag}"
        )
    totals = pre["totals"]
    print(f"\n  naive sum of era opening balances : {totals['sum_of_era_starting_bankrolls']:.2f}"
          "   <- what a per-era sum would claim was contributed")
    print(f"  settled net P&L (funded eras)     : {totals['settled_net_pl_all_funded_eras']:.2f}")
    print(f"  reserved (funded eras, open only) : {totals['reserved_all_funded_eras']:.2f}")
    print(f"  positions / settlements           : {totals['positions_all_funded_eras']} / "
          f"{totals['settlements_all_funded_eras']}")

    if args.phase == "after":
        print("\n=== AFTER: season accounts ===")
        for account in payload["after"]["accounts"]:
            print(f"\n  {account['sport']} {account['season']}  [{account['status']}]")
            if account["original_contribution"] <= 0:
                print("    planned only — no capital contributed")
                continue
            print(f"    tracking started      : {account['tracking_started_on']}")
            print(f"    original contribution : {account['original_contribution']:.2f}")
            print(f"    settled net P&L       : {account['settled_net_pl']:+.2f}")
            print(f"    realized bankroll     : {account['realized_bankroll']:.2f}")
            print(f"    reserved stake        : {account['reserved_stake']:.2f}")
            print(f"    available capital     : {account['available_capital']:.2f}")
            roi = account["settled_stake_roi_pct"]
            print(f"    season return         : {account['season_return_pct']:+.2f}%")
            print("    settled-stake ROI     : "
                  + ("unavailable (no settled stake)" if roi is None else f"{roi:+.2f}%"))
            print(f"    record / open / settled: {account['record']} / "
                  f"{account['open_positions']} / {account['settled_positions']}")
            for era in account["eras"]:
                print(f"      era v{era['version']:<3} {era['role']:<18} "
                      f"{str(era['era'] or era['engine'])[:36]:<36} "
                      f"P&L {era['settled_net_pl']:+7.2f}  resv {era['reserved']:6.2f}  "
                      f"open {era['open']}  settled {era['settled']}")

        problems = payload["reconciliation"]
        print("\n=== RECONCILIATION ===")
        if not problems:
            print("  OK — per-era and season-account views agree on every checked figure.")
            return 0
        for problem in problems:
            print(f"  MISMATCH: {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
