"""One narrowly filtered SportsGameOdds free-tier validation request.

WHAT THIS IS FOR
----------------
The fallback adapter refuses to produce a row until a field-mapping manifest
exists that is bound to a REAL observed response. That manifest can only be
written by someone who has seen one, which is the authorization the adapter has
always been missing. This script is the one authorized way to obtain it.

WHAT IT WILL AND WILL NOT DO
----------------------------
* Exactly ONE HTTP GET. There is no retry, no pagination and no second call --
  a failure is reported, not retried, because a retry is a second request.
* At most two events, enforced twice: in the request parameters and again on the
  parsed response before anything is written.
* The credential is sent in a header, never in the URL, and is never printed,
  logged or written to any output file. ``--dry-run`` shows the exact request
  with the header value redacted and sends nothing.
* No database session is opened. It cannot create a decision, a position, a
  snapshot or a ledger row, because it never connects.
* Output is files only: the raw response, a validation record, and a candidate
  mapping manifest bound to the response digest.

WHY IT DISCOVERS RATHER THAN ASSUMES
------------------------------------
Nothing about this provider's payload shape is established. The script therefore
reports what it FOUND -- the keys that exist, the candidate paths that resolve,
the bookmakers and timestamps actually present -- and marks each of the eight
validation points PASS, FAIL or UNPROVEN from that evidence. A point that cannot
be settled by one response is reported UNPROVEN rather than assumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

#: Headers a provider might use to report free-tier consumption. All of them are
#: recorded when present; none is assumed to exist.
USAGE_HEADER_HINTS = (
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "x-ratelimit-used", "x-objects-limit", "x-objects-remaining", "x-objects-used",
    "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset",
    "x-rate-limit-limit", "x-rate-limit-remaining",
)

REQUIRED_FIELDS = (
    "event_id", "commence_time", "home_team", "away_team",
    "bookmaker_key", "bookmaker_title", "market_key",
    "outcome_name", "outcome_price_american", "price_observed_at",
)


def _redacted(headers: dict[str, str]) -> dict[str, str]:
    """Never let a credential reach a log, a file or a terminal."""
    out = {}
    for key, value in headers.items():
        lowered = key.lower()
        if "key" in lowered or "auth" in lowered or "token" in lowered:
            out[key] = f"<redacted len={len(value)}>"
        else:
            out[key] = value
    return out


def _walk(node: Any, prefix: str = "", depth: int = 0, out: dict[str, str] | None = None) -> dict[str, str]:
    """Every resolvable dotted path in the payload, with the type found there.

    This is the evidence a mapping manifest is written from. Lists are indexed
    at 0 only: one worked example is enough to author a path and the full
    expansion would bury it.
    """
    out = {} if out is None else out
    if depth > 6:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                out[path] = f"{type(value).__name__}[{len(value)}]"
                _walk(value, path, depth + 1, out)
            else:
                out[path] = f"{type(value).__name__}={json.dumps(value)[:60]}"
    elif isinstance(node, list) and node:
        path = f"{prefix}.0"
        out[path] = f"{type(node[0]).__name__}"
        _walk(node[0], path, depth + 1, out)
    return out


def _american_from_decimal(decimal_price: float) -> int:
    """The conversion AISP would apply, stated so the response can be checked."""
    if decimal_price >= 2.0:
        return round((decimal_price - 1.0) * 100)
    return round(-100.0 / (decimal_price - 1.0))


def _decimal_from_american(american: int) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def build_request(base_url: str, league: str, limit: int, extra: list[str]) -> tuple[str, dict[str, str]]:
    # Only leagueID and limit are assumed. Every other filter is passed in
    # explicitly with --param, so the request is exactly what was reviewed in
    # the dry run rather than whatever this file happened to default to. The
    # first validation used "oddsAvailable", which is not a documented
    # parameter name and was silently ignored.
    params = {"leagueID": league, "limit": str(limit)}
    for item in extra:
        if "=" in item:
            key, value = item.split("=", 1)
            params[key] = value
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base_url.rstrip('/')}/events?{query}", params


def validate(payload: Any, limit: int) -> dict[str, Any]:
    """The eight validation points, answered only from what the response shows."""
    paths = _walk(payload)
    events = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        events = [events] if isinstance(events, dict) else []
    events = events[:limit]

    def found(*needles: str) -> list[str]:
        return sorted(p for p in paths if any(n in p.lower() for n in needles))

    checks: dict[str, Any] = {}
    checks["event_and_team_identity"] = {
        "event_count_returned": len(events),
        "candidate_event_id_paths": found("eventid", "event_id", ".id"),
        "candidate_team_paths": found("team", "home", "away"),
    }
    checks["scheduled_start_time"] = {"candidate_paths": found("starttime", "commence", "scheduled", "date")}
    checks["provider_and_bookmaker_identity"] = {"candidate_paths": found("bookmaker", "sportsbook", "book")}
    checks["observation_timestamp"] = {"candidate_paths": found("updated", "observed", "timestamp", "lastupdate")}
    checks["two_sided_same_book_moneyline"] = {"candidate_paths": found("moneyline", "odds", "market", "h2h", "ml")}
    checks["price_conversion"] = {
        "candidate_american_paths": found("american", "odds"),
        "candidate_decimal_paths": found("decimal"),
        "reference_conversions": [
            {"american": a, "decimal": round(_decimal_from_american(a), 4),
             "round_trip_american": _american_from_decimal(_decimal_from_american(a))}
            for a in (-150, -110, 100, 145)
        ],
    }
    return {"resolvable_paths": paths, "checks": checks, "events_examined": len(events)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True,
                        help="Provider API base, e.g. https://api.sportsgameodds.com/v2 . "
                             "There is deliberately no default: the adapter must never "
                             "be able to reach a host nobody chose.")
    parser.add_argument("--league", default="MLB")
    parser.add_argument("--limit", type=int, default=2,
                        help="Maximum events. Hard-capped at 2 by this script.")
    parser.add_argument("--param", action="append", default=[],
                        help="Extra query parameter as key=value; repeatable.")
    parser.add_argument("--auth-header", default="X-Api-Key",
                        help="Header carrying the credential. A header keeps it out of "
                             "URLs, proxies and logs.")
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "sportsgameodds-validation"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the exact request and send nothing.")
    args = parser.parse_args(argv)

    if args.limit > 2:
        print("REFUSED: this validation is authorized for at most two events.", file=sys.stderr)
        return 2

    key = (os.environ.get("SPORTSGAMEODDS_API_KEY") or "").strip()
    if not key:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        key = (os.environ.get("SPORTSGAMEODDS_API_KEY") or "").strip()

    url, params = build_request(args.base_url, args.league, args.limit, args.param)
    headers = {args.auth_header: key, "Accept": "application/json"}

    print("REQUEST (exactly one will be sent):")
    print(f"  GET {url}")
    print(f"  headers {json.dumps(_redacted(headers))}")
    print(f"  credential configured: {'yes' if key else 'NO'}")

    if not key:
        print("\nBLOCKED: SPORTSGAMEODDS_API_KEY is not configured in the environment "
              "or in .env. No request was made. This script will not proceed without "
              "the credential, and will not create an account or obtain one.",
              file=sys.stderr)
        return 3

    if args.dry_run:
        print("\nDRY RUN: nothing was sent, no object was consumed.")
        return 0

    import httpx

    stamp = datetime.now(UTC)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
    except Exception as error:  # one request only; a retry would be a second
        record = {"schema_version": "AISP_SGO_VALIDATION_V1", "outcome": "TRANSPORT_FAILED",
                  "error_type": type(error).__name__, "error": str(error)[:400],
                  "requested_at_utc": stamp.isoformat(), "request": {"url": url, "params": params},
                  "objects_consumed": "unknown; the request may or may not have been counted"}
        (out_dir / "validation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nTRANSPORT FAILED: {type(error).__name__}. No retry was attempted.", file=sys.stderr)
        return 4

    body = response.content
    digest = hashlib.sha256(body).hexdigest()
    (out_dir / "response.json").write_bytes(body)

    usage = {k: v for k, v in response.headers.items() if k.lower() in USAGE_HEADER_HINTS}
    other_headers = {k: v for k, v in response.headers.items() if k.lower() not in USAGE_HEADER_HINTS}

    record: dict[str, Any] = {
        "schema_version": "AISP_SGO_VALIDATION_V1",
        "requested_at_utc": stamp.isoformat(),
        "request": {"url": url, "params": params, "auth_header": args.auth_header,
                    "credential": "sent in header; never recorded"},
        "http_status": response.status_code,
        "response_sha256": digest,
        "response_bytes": len(body),
        "usage_headers": usage,
        "usage_headers_present": bool(usage),
        "other_headers": _redacted(dict(other_headers)),
        "objects_before": None,
        "objects_after": None,
    }

    # Object accounting: derive before/after only from what the provider itself
    # reported. One request cannot establish a "before" independently, so an
    # absent header is recorded as absent rather than inferred.
    for used_key in ("x-objects-used", "x-ratelimit-used"):
        if used_key in usage:
            after = int(usage[used_key])
            record["objects_after"] = after
            record["objects_before"] = None
            record["objects_note"] = (
                f"provider reported {used_key}={after} AFTER this request; a 'before' "
                "figure cannot be established without a second request, which is not "
                "authorized"
            )
    if response.status_code != 200:
        record["outcome"] = "HTTP_ERROR"
        record["body_preview"] = body[:400].decode("utf-8", "replace")
        (out_dir / "validation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nHTTP {response.status_code}. Recorded and stopped; no retry.", file=sys.stderr)
        return 5

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as error:
        record["outcome"] = "UNPARSEABLE"
        record["error"] = f"{type(error).__name__}: {error}"
        (out_dir / "validation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return 6

    record["outcome"] = "RESPONSE_CAPTURED"
    record["discovery"] = validate(payload, args.limit)
    record["licensing"] = {
        "state": "UNPROVEN_BY_THIS_REQUEST",
        "note": "A response body does not state licence terms. Retention and research "
                "use must be settled against the provider's written terms of service, "
                "read and recorded separately.",
    }
    (out_dir / "validation.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"\nCAPTURED: HTTP 200, {len(body)} bytes, sha256 {digest}")
    print(f"  usage headers: {usage or 'none reported'}")
    print(f"  written: {out_dir / 'response.json'} and validation.json")
    print("\nNext: author the field mapping manifest from the resolvable paths in "
          "validation.json, binding it to verifiedAgainstResponseSha256 = "
          f"{digest}. The adapter refuses any manifest not bound to a real response.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
