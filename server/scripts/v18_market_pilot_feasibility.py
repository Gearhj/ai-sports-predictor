"""FEASIBILITY ASSESSMENT ONLY -- not enrollment, not sealed, not evidence for the pilot.

Runs the SAME builder the V18 market-pilot sealer uses
(``app.research_exports.v18_market_pilot.build_packet``) retrospectively over
the retained V18 era, to measure whether AISP's existing, already authorised
captures could ever meet the frozen protocol's 15-minute price rule at T-15.

The frozen protocol (f75aeccd...) forbids backfill: nothing produced here can
become a packet, an enrollment or an assessment input. Every output is labelled
accordingly, and no file is written to the packet directory.

Read-only: one database transaction under ``SET TRANSACTION READ ONLY``,
rolled back at the end. No provider request, no outcome/score/settlement
column, no write to the database or to the model exchange.

Usage (from the repository's server directory):

    python scripts/v18_market_pilot_feasibility.py \
        --env-file C:\\Users\\james\\ai-sports-predictor\\.env \
        --raw-data-root C:\\Users\\james\\ai-sports-predictor\\data\\raw
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
SERVER = HERE.parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

DEFAULT_OUT = Path.home() / "aisp-analysis" / "wagering-record-20260918" / "consumer-followup" / "pilot-feasibility"
LABEL = "FEASIBILITY_ASSESSMENT_ONLY_NOT_ENROLLMENT_NOT_SEALED"
AGE_BUCKETS = ((0, 15, "0-15"), (15, 30, "15-30"), (30, 60, "30-60"), (60, 120, "60-120"),
               (120, 240, "120-240"), (240, 10**9, ">240"))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bucket(minutes: float | None) -> str:
    if minutes is None:
        return "NO_RECEIPTED_QUOTE_BY_ANCHOR"
    if minutes < 0:
        return "NEGATIVE"
    for low, high, label in AGE_BUCKETS:
        if (low == 0 and 0 <= minutes <= high) or (low < minutes <= high):
            return label
    return ">240"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=str(SERVER.parent / ".env"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--start", default="2026-09-12T04:00:00Z", help="first research lock (UTC)")
    parser.add_argument("--end", default="2026-09-19T04:00:00Z", help="exclusive last research lock (UTC)")
    parser.add_argument("--raw-data-root", default=None)
    parser.add_argument("--quarantine-ledger", default=None)
    parser.add_argument("--protocol-dir", default=None)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(args.env_file)
    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.models import OddsSnapshot, ProviderRequestLedger
    from app.research_exports import v18_market_pilot as pilot
    from app.research_exports.v18_market_pilot_seal import AttemptLedger

    settings = Settings(_env_file=args.env_file)
    raw_root = Path(args.raw_data_root or settings.raw_data_root)
    ledger_path = Path(args.quarantine_ledger or settings.v18_market_pilot_quarantine_ledger_path)
    protocol = pilot.load_frozen_protocol(args.protocol_dir or settings.v18_market_pilot_protocol_dir)
    start, end = _parse_utc(args.start), _parse_utc(args.end)
    run_at = datetime.now(UTC)
    out = Path(args.out)
    packets_dir = out / "retrospective-packets-NOT-SEALED-NOT-ENROLLMENT"
    out.mkdir(parents=True, exist_ok=True)
    packets_dir.mkdir(parents=True, exist_ok=True)
    run_ledger = AttemptLedger(out, name="feasibility-run-ledger.jsonl")

    engine = create_engine(os.environ["DATABASE_URL"])
    rows: list[dict[str, Any]] = []
    context: dict[str, Any] = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        try:
            session = Session(bind=connection, autoflush=False)
            games = [g for g in pilot.mlb_games_with_locks_between(session, start, end)
                     if pilot.aware(g.starts_at) < run_at]
            inputs = pilot.BuildInputs(protocol=protocol, quarantine_ledger_path=ledger_path,
                                       raw_data_root=raw_root, mode=pilot.MODE_FEASIBILITY)
            for game in games:
                lock = pilot.research_lock(game.starts_at)
                ref = run_ledger.append({"kind": LABEL, "canonicalGameId": game.id,
                                         "researchLockUtc": pilot.iso(lock)}, now=datetime.now(UTC))
                build = pilot.build_packet(session, game, inputs=inputs, built_at=datetime.now(UTC),
                                           attempt_ref=ref)
                if session.new or session.dirty or session.deleted:
                    raise RuntimeError("builder modified the session")
                wrapper = {"notice": LABEL,
                           "meaning": "Retrospective feasibility build over games already played. Not a "
                                      "sealed packet, not an enrollment and never admissible to the pilot. "
                                      "sealedAtUtc below is only this build's clock.",
                           "validator": build.validator, "schemaErrors": build.schema_errors,
                           "evidenceFile": build.evidence_name, "packet": build.packet}
                (packets_dir / f"{build.episode_key}.feasibility.json").write_bytes(
                    pilot.canonical_bytes(wrapper))
                (packets_dir / build.evidence_name).write_bytes(build.evidence_bytes)
                rows.append(_row(build, game))
            context = _acquisition_context(session, start, end, pilot, OddsSnapshot, ProviderRequestLedger,
                                           func, select)
        finally:
            connection.rollback()

    summary = _summarise(rows, context, protocol, start, end, run_at, raw_root, ledger_path, pilot)
    _write_csv(out / "per_game.csv", rows)
    _write_exclusions(out / "exclusion_reasons.csv", summary["exclusionReasonTable"])
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str),
                                      encoding="utf-8")
    (out / "FEASIBILITY_CONSUMER.md").write_text(_markdown(summary), encoding="utf-8")
    print(json.dumps({"games": len(rows), "out": str(out), "funnel": summary["funnel"]["overall"]}, indent=2))
    return 0


def _row(build: Any, game: Any) -> dict[str, Any]:
    packet, diag = build.packet, build.diagnostics
    market = packet["market"]
    forecast = packet["forecast"] or {}
    eligible_books = [c["bookKey"] for c in market["candidates"] if c["eligible"]]
    return {
        "episodeKey": build.episode_key,
        "canonicalGameId": game.id,
        "mlbGamePk": diag.get("gamePk"),
        "localDate": packet["game"]["localDate"],
        "scheduledStartUtc": packet["game"]["scheduledStartUtc"],
        "researchLockUtc": packet["anchor"]["researchLockUtc"],
        "phase": packet["game"]["phase"],
        "phaseEvidence": "RAW_SCHEDULE_CAPTURE" if packet["game"]["phaseEvidenceRef"] else "NONE",
        "quarantineEligible": packet["game"]["quarantineIdentityGate"]["eligible"],
        "anchorKind": packet["anchor"]["kind"],
        "anchorUtc": packet["anchor"]["atUtc"],
        "decisionId": packet["anchor"]["decisionId"],
        "selectionStatus": packet["selection"]["statusAtResearchLock"],
        "forecastIdentity": forecast.get("forecastIdentity"),
        "forecastCreatedAtUtc": forecast.get("createdAtUtc"),
        "forecastPublicationCompletedAtUtc": forecast.get("publicationCompletedAtUtc"),
        "forecastImportReceiptUtc": forecast.get("importReceiptCompletedAtUtc"),
        "forecastHasScorePopulation": diag.get("forecastHasScorePopulation"),
        "forecastEligible": packet["eligibility"]["forecastEligible"],
        "marketStatus": market["status"],
        "priceEvaluable": packet["eligibility"]["priceEvaluable"],
        "primaryBook": market["selectedBookKey"],
        "primaryProvider": market["selectedProviderKey"],
        "books": diag.get("books", 0),
        "booksWithCompletePair": diag.get("booksWithCompletePair", 0),
        "eligibleBooks": len(eligible_books),
        "eligibleBooksLedgerOnlyBound": diag.get("eligibleBooksLedgerOnlyBound", 0),
        "quoteRowsFetchedByAnchor": diag.get("quoteRowsFetchedByAnchor", 0),
        "latestFetchClockUtc": diag.get("latestFetchClockUtc"),
        "minutesLatestFetchBeforeAnchor": diag.get("minutesLatestFetchBeforeAnchor"),
        "freshestLegAgeMinutes": diag.get("freshestLegAgeMinutes"),
        "freshestAvailableLegAgeMinutes": diag.get("freshestAvailableLegAgeMinutes"),
        "freshestReceiptedPairOlderLegAgeMinutes": diag.get("freshestReceiptedPairOlderLegAgeMinutes"),
        "marketFailureCategory": diag.get("marketFailureCategory"),
        "firstFailure": packet["eligibility"]["firstFailure"],
        "selectionAscertained": packet["eligibility"]["selectionAscertained"],
        "schemaValid": not build.schema_errors,
        "validator": build.validator,
        "reasonCodes": "|".join(packet["eligibility"]["reasonCodes"]),
    }


def _acquisition_context(session, start, end, pilot, OddsSnapshot, ProviderRequestLedger, func, select):
    """Metadata only: how and when quotes were acquired in the era (no prices, no outcomes)."""

    from app.models import Game, Market, MarketOutcome, MarketType
    from app.services.league_scope import MLB, in_league

    ledger = session.execute(
        select(ProviderRequestLedger.provider, ProviderRequestLedger.endpoint_key,
               ProviderRequestLedger.requested_at, ProviderRequestLedger.completed_at,
               ProviderRequestLedger.records_received)
        .where(ProviderRequestLedger.status == "SUCCEEDED",
               ProviderRequestLedger.requested_at >= start - timedelta(hours=12),
               ProviderRequestLedger.requested_at < end)
    ).all()
    per_day: dict[str, Counter] = defaultdict(Counter)
    receipt_seconds: list[float] = []
    for row in ledger:
        day = pilot.aware(row.requested_at).astimezone(pilot.EASTERN).date().isoformat()
        per_day[day][f"{row.provider}:{row.endpoint_key}:requests"] += 1
        per_day[day][f"{row.provider}:{row.endpoint_key}:objects"] += int(row.records_received or 0)
        if row.provider == pilot.SGO and row.endpoint_key == "sgo_mlb_events" and row.completed_at:
            receipt_seconds.append((pilot.aware(row.completed_at) - pilot.aware(row.requested_at)).total_seconds())
    fetches = session.execute(
        select(OddsSnapshot.source, OddsSnapshot.available_as_of, Market.game_id, Game.starts_at,
               func.min(OddsSnapshot.observed_at).label("oldest"), func.max(OddsSnapshot.observed_at).label("newest"),
               func.count().label("legs"))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Game, Game.id == Market.game_id)
        .where(Market.market_type == MarketType.MONEYLINE, OddsSnapshot.source_mode == "LIVE", in_league(MLB),
               Game.starts_at >= start + timedelta(minutes=15), Game.starts_at < end + timedelta(minutes=15))
        .group_by(OddsSnapshot.source, OddsSnapshot.available_as_of, Market.game_id, Game.starts_at)
    ).all()
    leads: list[float] = []
    lags: list[float] = []
    per_game = Counter()
    sources = Counter()
    other_fetch_clocks: dict[str, set[str]] = defaultdict(set)
    by_date: dict[str, dict[str, Any]] = defaultdict(lambda: {"games": set(), "fetches": 0, "leads": []})
    for f in fetches:
        sources[f.source] += int(f.legs)
        fetch = pilot.aware(f.available_as_of)
        start_at = pilot.aware(f.starts_at)
        if f.source != pilot.SGO:
            other_fetch_clocks[f.source].add(pilot.iso(fetch))
            continue
        lags.append((fetch - pilot.aware(f.oldest)).total_seconds() / 60.0)
        if fetch < start_at:
            lead = (start_at - fetch).total_seconds() / 60.0
            leads.append(lead)
            per_game[str(f.game_id)] += 1
            day = start_at.astimezone(pilot.EASTERN).date().isoformat()
            by_date[day]["games"].add(str(f.game_id))
            by_date[day]["fetches"] += 1
            by_date[day]["leads"].append(lead)

    def dist(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"n": 0}
        ordered = sorted(values)
        return {"n": len(ordered), "min": round(ordered[0], 2), "p25": round(ordered[len(ordered) // 4], 2),
                "median": round(statistics.median(ordered), 2),
                "p75": round(ordered[(3 * len(ordered)) // 4], 2), "max": round(ordered[-1], 2)}

    lead_buckets = Counter()
    for lead in leads:
        if lead <= 20:
            lead_buckets["T-0..20"] += 1
        elif lead <= 45:
            lead_buckets["T-20..45"] += 1
        elif lead <= 90:
            lead_buckets["T-45..90"] += 1
        elif lead <= 180:
            lead_buckets["T-90..180"] += 1
        else:
            lead_buckets[">T-180"] += 1
    return {
        "ledgerSucceededRequestsByEasternDate": {k: dict(v) for k, v in sorted(per_day.items())},
        "moneylineQuoteRowsBySourceForEraGames": dict(sources),
        "nonSgoFetchClocksForEraGames": {k: sorted(v) for k, v in other_fetch_clocks.items()},
        "sgoPregameFetchesPerGame": dist([float(v) for v in per_game.values()]),
        "sgoPregameFetchLeadMinutesBeforeStart": dist(leads),
        "sgoPregameFetchLeadBuckets": dict(lead_buckets),
        "sgoPregameByEasternDate": {
            day: {"gamesWithAnSgoFetch": len(v["games"]), "fetchGamePairs": v["fetches"],
                  "leadMinutes": dist(v["leads"]),
                  "leadsWithin15To30": sum(1 for x in v["leads"] if 15 <= x <= 30),
                  "sgoMlbRequests": per_day.get(day, {}).get(f"{pilot.SGO}:sgo_mlb_events:requests", 0),
                  "sgoMlbObjects": per_day.get(day, {}).get(f"{pilot.SGO}:sgo_mlb_events:objects", 0)}
            for day, v in sorted(by_date.items())},
        "sgoOlderLegLagAtAcquisitionMinutes": dist(lags),
        "sgoLedgerReceiptSecondsAfterRequest": dist(receipt_seconds),
        "note": "Acquisition lag = fetch clock (available_as_of = requested_at) minus the pair's older provider "
                "update stamp. Metadata only; no price value or outcome is read.",
    }


def _summarise(rows, context, protocol, start, end, run_at, raw_root, ledger_path, pilot) -> dict[str, Any]:
    def funnel(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "attempts": len(subset),
            "phaseR": sum(1 for r in subset if r["phase"] == "R"),
            "v18ForecastEligibleByAnchor": sum(1 for r in subset if r["forecastEligible"]),
            "fresh15MinutePair": sum(1 for r in subset if r["priceEvaluable"]),
            "forecastAndFreshPair": sum(1 for r in subset if r["forecastEligible"] and r["priceEvaluable"]),
            "lackingAnyEligibleBook": sum(1 for r in subset if not r["priceEvaluable"]),
            "anyQuoteFetchedByAnchor": sum(1 for r in subset if r["quoteRowsFetchedByAnchor"]),
            "completeReceiptedPairByAnchorAnyAge": sum(
                1 for r in subset if r["freshestReceiptedPairOlderLegAgeMinutes"] is not None),
        }

    by_selection = defaultdict(list)
    by_date = defaultdict(list)
    by_anchor = defaultdict(list)
    for r in rows:
        by_selection[r["selectionStatus"]].append(r)
        by_date[r["localDate"]].append(r)
        by_anchor[r["anchorKind"]].append(r)
    reasons = Counter()
    for r in rows:
        for code in filter(None, r["reasonCodes"].split("|")):
            reasons[code] += 1
    first = Counter(r["firstFailure"] for r in rows)
    market_fail = Counter(r["marketFailureCategory"] or "NONE_FRESH_PAIRED" for r in rows)
    ages = Counter(_bucket(r["freshestAvailableLegAgeMinutes"]) for r in rows)
    ages_pair = Counter(_bucket(r["freshestReceiptedPairOlderLegAgeMinutes"]) for r in rows)
    fresh_values = [r["freshestAvailableLegAgeMinutes"] for r in rows if r["freshestAvailableLegAgeMinutes"] is not None]
    table = []
    for code, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
        table.append({"reasonCode": code, "games": count,
                      "kind": "LIMITATION" if code.startswith("LIMITATION_") else "REASON"})
    return {
        "label": LABEL,
        "statement": ("Retrospective feasibility over retained captures of games already played. Not "
                      "enrollment, not sealed, not admissible; the frozen protocol forbids backfill."),
        "protocolSha256": protocol.sha256,
        "packetSchemaSha256": protocol.schema_sha256,
        "externalProtocolCheck": protocol.external_check,
        "builder": "app.research_exports.v18_market_pilot.build_packet (same code as the sealer)",
        "validator": rows[0]["validator"] if rows else None,
        "schemaValidPackets": sum(1 for r in rows if r["schemaValid"]),
        "lockRangeUtc": [pilot.iso(start), pilot.iso(end)],
        "runAtUtc": pilot.iso(run_at),
        "rawDataRoot": str(raw_root),
        "quarantineLedger": str(ledger_path),
        "readOnly": {"transaction": "SET TRANSACTION READ ONLY; rolled back", "providerRequests": 0,
                     "outcomeColumnsRead": 0, "databaseWrites": 0},
        "funnel": {
            "overall": funnel(rows),
            "bySelectionStatus": {k: funnel(v) for k, v in sorted(by_selection.items())},
            "byLocalDate": {k: funnel(v) for k, v in sorted(by_date.items())},
            "byAnchorKind": {k: funnel(v) for k, v in sorted(by_anchor.items())},
        },
        "firstFailureCounts": dict(first.most_common()),
        "marketFailureCategoryCounts": dict(market_fail.most_common()),
        "exclusionReasonTable": table,
        "freshestAvailableLegAgeAtAnchorMinutes": {
            "buckets": {label: ages.get(label, 0) for label in
                        ["0-15", "15-30", "30-60", "60-120", "120-240", ">240", "NEGATIVE",
                         "NO_RECEIPTED_QUOTE_BY_ANCHOR"]},
            "min": round(min(fresh_values), 2) if fresh_values else None,
            "median": round(statistics.median(fresh_values), 2) if fresh_values else None,
            "max": round(max(fresh_values), 2) if fresh_values else None,
        },
        "freshestReceiptedPairOlderLegAgeAtAnchorMinutes": {
            "buckets": {label: ages_pair.get(label, 0) for label in
                        ["0-15", "15-30", "30-60", "60-120", "120-240", ">240", "NEGATIVE",
                         "NO_RECEIPTED_QUOTE_BY_ANCHOR"]},
        },
        "receiptBoundSensitivity": {
            "gamesWithEligibleBookJobBound": sum(1 for r in rows if r["eligibleBooks"]),
            "gamesWithEligibleBookLedgerOnlyBound": sum(1 for r in rows if r["eligibleBooksLedgerOnlyBound"]),
        },
        "forecastScorePopulationRetained": sum(1 for r in rows if r["forecastHasScorePopulation"]),
        "acquisitionContext": context,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_exclusions(path: Path, table: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["reasonCode", "games", "kind"])
        writer.writeheader()
        writer.writerows(table)


def _markdown(summary: dict[str, Any]) -> str:
    overall = summary["funnel"]["overall"]
    ages = summary["freshestAvailableLegAgeAtAnchorMinutes"]
    ctx = summary["acquisitionContext"]
    fresh = overall["fresh15MinutePair"]
    paired = overall["forecastAndFreshPair"]
    by_date = summary["funnel"]["byLocalDate"]
    dates = sorted(by_date)
    trailing: list[str] = []
    for day in reversed(dates):
        if by_date[day]["fresh15MinutePair"]:
            break
        trailing.append(day)
    trailing_games = sum(by_date[d]["attempts"] for d in trailing)
    fresh_dates = [d for d in dates if by_date[d]["fresh15MinutePair"]]
    if fresh == 0:
        answer = "NO. Existing authorised captures never met the 15-minute rule at the anchor in this era"
    elif len(trailing) >= 3:
        answer = (f"NO, not under the acquisition schedule now in force. All {fresh} fresh pairs come from "
                  f"{', '.join(fresh_dates)}; the most recent {len(trailing)} dates ({trailing[-1]} to "
                  f"{trailing[0]}, {trailing_games} games) have none")
    else:
        answer = (f"PARTIALLY. {fresh} of {overall['attempts']} games had a fresh same-book pair at the "
                  f"anchor, on {', '.join(fresh_dates)}")
    lines = [
        "# V18 market pilot: consumer-side feasibility (retrospective, NOT enrollment)",
        "",
        f"**{summary['label']}.** {summary['statement']}",
        "",
        f"Protocol `{summary['protocolSha256']}`; schema `{summary['packetSchemaSha256']}`. Built by "
        f"{summary['builder']}; {summary['schemaValidPackets']}/{overall['attempts']} packets pass "
        f"{summary['validator']}. Research locks {summary['lockRangeUtc'][0]} to "
        f"{summary['lockRangeUtc'][1]} (exclusive), games started before {summary['runAtUtc']}.",
        "",
        "## Plain answer",
        "",
        f"**{answer}.** Games with a V18 forecast available by the anchor: "
        f"{overall['v18ForecastEligibleByAnchor']}. Games with a fresh (0-15 minute, receipted, same-book, "
        f"two-sided) pair at the anchor: {fresh}. Both: {paired}. Games lacking any eligible book: "
        f"{overall['lackingAnyEligibleBook']}.",
        "",
        "## Funnel",
        "",
        "| Stage | Games |",
        "| --- | ---: |",
        f"| Attempts (locks in range, already started) | {overall['attempts']} |",
        f"| Phase R evidenced by a retained schedule capture | {overall['phaseR']} |",
        f"| V18 forecast eligible and available by the anchor | {overall['v18ForecastEligibleByAnchor']} |",
        f"| Any moneyline quote fetched by the anchor | {overall['anyQuoteFetchedByAnchor']} |",
        f"| Complete receipted two-sided pair by the anchor (any age) | "
        f"{overall['completeReceiptedPairByAnchorAnyAge']} |",
        f"| Fresh 15-minute pair (price evaluable) | {fresh} |",
        f"| Forecast eligible AND fresh pair | {paired} |",
        "",
        "### By selection state at the lock",
        "",
        "| Selection | Attempts | Forecast | Fresh pair | Both | No eligible book |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for status, f in summary["funnel"]["bySelectionStatus"].items():
        lines.append(f"| {status} | {f['attempts']} | {f['v18ForecastEligibleByAnchor']} | "
                     f"{f['fresh15MinutePair']} | {f['forecastAndFreshPair']} | {f['lackingAnyEligibleBook']} |")
    lines += ["", "### By local (Eastern) date", "",
              "| Date | Attempts | Forecast | Fresh pair | Both | No eligible book |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for day, f in summary["funnel"]["byLocalDate"].items():
        lines.append(f"| {day} | {f['attempts']} | {f['v18ForecastEligibleByAnchor']} | "
                     f"{f['fresh15MinutePair']} | {f['forecastAndFreshPair']} | {f['lackingAnyEligibleBook']} |")
    lines += ["", "## Age of the freshest receipted leg at the anchor", "",
              "| Minutes | Games |", "| --- | ---: |"]
    for label, count in ages["buckets"].items():
        lines.append(f"| {label} | {count} |")
    lines += ["", f"Median {ages['median']} minutes; minimum {ages['min']}; maximum {ages['max']}.", "",
              "## Why", "",
              "Market first-failure categories (the furthest stage any book reached):", ""]
    for code, count in summary["marketFailureCategoryCounts"].items():
        lines.append(f"- `{code}`: {count}")
    lead = ctx.get("sgoPregameFetchLeadMinutesBeforeStart", {})
    lag = ctx.get("sgoOlderLegLagAtAcquisitionMinutes", {})
    lines += [
        "",
        f"Acquisition context (metadata only): SGO pregame moneyline fetches per game median "
        f"{ctx.get('sgoPregameFetchesPerGame', {}).get('median')}; fetch lead before first pitch median "
        f"{lead.get('median')} minutes (min {lead.get('min')}, max {lead.get('max')}); buckets "
        f"{ctx.get('sgoPregameFetchLeadBuckets')}. Older-leg provider lag at acquisition median {lag.get('median')} "
        f"minutes, max {lag.get('max')}. Quote rows by source: {ctx.get('moneylineQuoteRowsBySourceForEraGames')}; "
        f"non-SGO fetch clocks: {ctx.get('nonSgoFetchClocksForEraGames')}.",
        "",
        "| Eastern date | SGO MLB requests | Objects | Games with a fetch | Fetch-game pairs | Min lead (min) "
        "| Median lead (min) | Fetches in T-30..T-15 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[f"| {day} | {v['sgoMlbRequests']} | {v['sgoMlbObjects']} | {v['gamesWithAnSgoFetch']} | "
          f"{v['fetchGamePairs']} | {v['leadMinutes'].get('min')} | {v['leadMinutes'].get('median')} | "
          f"{v['leadsWithin15To30']} |" for day, v in ctx.get("sgoPregameByEasternDate", {}).items()],
        "",
        _why(ctx, trailing, lag),
        "",
        "## Limitations",
        "",
        "- SGO rows store the pair's older provider update stamp on both legs; per-side stamps are not retained.",
        "- The pitcher/action convention of the SGO moneyline is not retained.",
        "- The receipt bound is max(ledger completed_at, requesting job completed_at); the ledger-only bound "
        f"would give {summary['receiptBoundSensitivity']['gamesWithEligibleBookLedgerOnlyBound']} games with an "
        "eligible book.",
        "- Phase comes only from AISP's retained raw MLB Stats API schedule capture (gameType), archived by the "
        "lock.",
        "- Exchange object files were read only where this process had access; otherwise refs name the "
        "database-held content addresses.",
        "",
        "Files: `per_game.csv`, `summary.json`, `exclusion_reasons.csv`, "
        "`retrospective-packets-NOT-SEALED-NOT-ENROLLMENT/`.",
        "",
    ]
    return "\n".join(lines)


def _why(ctx: dict[str, Any], trailing: list[str], lag: dict[str, Any]) -> str:
    per_date = ctx.get("sgoPregameByEasternDate", {})
    recent = [per_date[d]["leadMinutes"].get("min") for d in trailing if d in per_date
              and per_date[d]["leadMinutes"].get("min") is not None]
    if not recent:
        return ("A pair is 0-15 minutes old at the anchor only if it was fetched in roughly the 15 minutes "
                "before the anchor and its provider update stamps are themselves recent. This assessment "
                "requests nothing.")
    floor = min(recent)
    return (f"On the most recent {len(trailing)} dates every SGO MLB purchase was made at least {floor} minutes "
            f"before first pitch (fetch leads cluster just inside T-255, where the collector's 240-minute "
            f"consumer-policy freshness window before the T-15 cutoff opens), so at T-15 each quote was already "
            f"at least {round(floor - 15, 1)} minutes past its fetch, on top of a provider update lag at "
            f"acquisition of median {lag.get('median')} and max {lag.get('max')} minutes. A 0-15 minute pair "
            "needs a purchase in roughly the last 15 minutes before the anchor; the current allowance and "
            "schedule do not make one, and this assessment requests nothing.")


if __name__ == "__main__":
    raise SystemExit(main())
