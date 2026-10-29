"""The Arena league store on the production backend (owner 2026-10-29: the full platform, Arena included).

Ported from the test-mode preview (aisp-preview/arena_league.py) with one change that matters on a site with real
users: a player IS their signed-in account. The browser's chosen "profile" is ignored; the store is keyed by the
account id and shows the account's display name. Nothing here touches AISP's tables or bankroll: Arena calls are a
game, kept in one JSON file under ``<runtime>/arena/``.

Rules enforced here, not just in the browser:
  - a call can be created or have its side/power changed only while the game has not started (server clock);
  - the first start time stored for a game is kept, so a client cannot move it later to reopen the game;
  - a result is accepted only after that start, once, and never on a brand-new call;
  - another player's side is revealed only after the requester has locked the same game, or the game started
    (until then it is a Ghost: "ELVIS has locked", side hidden);
  - at most 60 players, 500 calls per request, 64 characters per field.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()
FIELDS = ("game_id", "sport", "away", "home", "starts_at", "side", "power", "blind", "at", "house", "result")
MAX_PLAYERS = 60


def store_path(settings: Any) -> Path:
    from app.nba.availability import root

    return root(settings).parent / "arena" / "arena_league.json"


def label_of(display_name: str | None) -> str:
    name = re.sub(r"[^A-Z0-9_]", "", str(display_name or "").upper().replace(" ", "_"))[:16]
    return name if len(name) >= 2 else "PLAYER"


def _ts(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("players"), dict) else {"players": {}}
    except (OSError, ValueError, AttributeError):
        return {"players": {}}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def sync(path: Path, user_id: str, display_name: str | None, payload: dict, *, now: datetime | None = None) -> tuple[dict, int]:
    calls = payload.get("calls")
    if not isinstance(calls, list) or len(calls) > 500:
        return {"error": "calls must be a list (max 500)"}, 400
    remove = payload.get("remove") or []
    if not isinstance(remove, list) or len(remove) > 500:
        return {"error": "remove must be a list (max 500)"}, 400
    now = now or datetime.now(UTC)
    refused: list[str] = []
    with _lock:
        data = _read(path)
        if user_id not in data["players"] and len(data["players"]) >= MAX_PLAYERS:
            return {"error": "the league is full"}, 403
        player = data["players"].setdefault(user_id, {"calls": {}, "joined": now.isoformat()})
        player["label"] = label_of(display_name)
        mine = player["calls"]
        for c in calls:
            if not isinstance(c, dict) or not isinstance(c.get("game_id"), str) or c.get("side") not in ("HOME", "AWAY"):
                continue
            if len(c["game_id"]) > 64:
                continue
            prev = mine.get(c["game_id"])
            clean = {k: (c.get(k)[:64] if isinstance(c.get(k), str) else c.get(k) if isinstance(c.get(k), (int, float, bool)) or c.get(k) is None else None) for k in FIELDS}
            if prev is not None and prev.get("starts_at"):
                clean["starts_at"] = prev["starts_at"]
            start = _ts(clean.get("starts_at"))
            if clean.get("result") and (prev is None or start is None or start > now or prev.get("result")):
                clean["result"] = prev.get("result") if prev else None
            changed = prev is None or prev.get("side") != clean["side"] or prev.get("power") != clean["power"]
            if changed and (start is None or start <= now):
                refused.append(c["game_id"])  # too late: the game started
                if prev is not None and clean.get("result") and not prev.get("result"):
                    prev["result"] = clean["result"]
                continue
            if prev is not None and not changed:
                if clean.get("result"):
                    prev["result"] = clean["result"]
                continue
            mine[c["game_id"]] = clean
        for gid in remove:
            prev = mine.get(str(gid))
            start = _ts(prev.get("starts_at")) if prev else None
            if prev and start is not None and start > now and not prev.get("result"):
                del mine[str(gid)]
        _write(path, data)
    return {"ok": True, "profile": player["label"], "stored": len(mine), "refused_late": refused}, 200


def league(path: Path, user_id: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    data = _read(path)
    me = data["players"].get(user_id, {})
    mine = me.get("calls", {})
    out = []
    for uid, p in sorted(data["players"].items(), key=lambda kv: kv[1].get("label") or ""):
        calls = []
        for gid, c in p.get("calls", {}).items():
            start = _ts(c.get("starts_at"))
            visible = uid == user_id or gid in mine or (start is not None and start <= now)
            calls.append(c if visible else {"game_id": gid, "starts_at": c.get("starts_at"), "hidden": True})
        out.append({"profile": p.get("label") or "PLAYER", "calls": calls})
    return {"players": out, "me": me.get("label"), "as_of": now.isoformat()}
