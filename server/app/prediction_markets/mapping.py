"""Fail-closed game-identity mapping + contract-semantics review.

Never forces a match on name similarity alone. Ambiguity, reversed teams,
kickoff mismatch, doubleheaders, or duplicate events all fail closed to a
non-accepted status. Accepted mappings are EXACT (deterministic) or
MANUAL_APPROVED (audited human decision). Everything is append-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.markets.odds import team_name_match
from app.models import ExternalIdMapping, Game, GameTeam, League, Team
from app.prediction_markets.contracts import MappingStatus, SemanticsStatus

# A provider start time within this window of an AISP game is "aligned".
ET = ZoneInfo("America/New_York")
EXACT_START_WINDOW = timedelta(minutes=90)
CANDIDATE_START_WINDOW = timedelta(hours=6)


@dataclass
class MappingDecision:
    status: MappingStatus
    canonical_game_id: Any | None = None
    reason: str = ""
    start_time_delta_seconds: int | None = None
    candidate_game_ids: list[str] = field(default_factory=list)
    home_is_yes: bool | None = None  # for single-sided (Kalshi) contracts


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def resolve_league_id(session: Session, league_code: str) -> Any | None:
    return session.scalar(select(League.id).where(League.code == league_code))


def map_event_to_game(
    session: Session,
    *,
    provider: str,
    provider_event_id: str,
    league_code: str,
    provider_home_team: str | None,
    provider_away_team: str | None,
    scheduled_start_at: datetime | None,
    provider_home_abbr: str | None = None,
    provider_away_abbr: str | None = None,
    doubleheader_game_number: int | None = None,
    start_is_date_only: bool = False,
) -> MappingDecision:
    """Resolve a provider event to a canonical AISP game, failing closed.

    When the provider supplies registry-resolved abbreviations (Kalshi event
    tickers, Polymarket team names through the alias registry) identity is an
    EXACT abbreviation match on both teams in the stated orientation; the
    doubleheader ordinal (game 1 / game 2) selects between same-day games of
    the same teams, and without one the nearest start within the exact window
    wins only when it is unambiguous."""

    # 1) Stable identity: a prior ExternalIdMapping is authoritative (EXACT).
    mapping = session.scalar(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.source == provider,
        ExternalIdMapping.external_id == provider_event_id,
    ))
    if mapping is not None:
        return MappingDecision(MappingStatus.EXACT, mapping.entity_id, reason="Existing identity mapping")

    league_id = resolve_league_id(session, league_code)
    if league_id is None:
        return MappingDecision(MappingStatus.UNRESOLVED, reason=f"No canonical league for {league_code}")
    if not scheduled_start_at:
        return MappingDecision(MappingStatus.UNRESOLVED, reason="Provider event has no scheduled start time")

    start = _aware(scheduled_start_at)
    window_lo = start - CANDIDATE_START_WINDOW
    window_hi = start + CANDIDATE_START_WINDOW
    if start_is_date_only:
        # The provider names only the ET calendar day (Kalshi NFL/NBA/NHL tickers). Identity then comes from
        # the exact registered teams within that ET game day (00:00 to 06:00 the next morning), never a guessed time.
        day = start.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
        window_lo, window_hi = day, day + timedelta(hours=30)
    games = list(session.scalars(select(Game).where(
        Game.league_id == league_id,
        Game.starts_at >= window_lo,
        Game.starts_at <= window_hi,
    )))
    if not games:
        return MappingDecision(MappingStatus.UNRESOLVED, reason="No AISP game near the provider start time")

    if provider_home_abbr and provider_away_abbr:
        decision = _map_by_abbreviation(
            session, games, start, home_abbr=provider_home_abbr.upper(), away_abbr=provider_away_abbr.upper(),
            game_number=doubleheader_game_number, date_only=start_is_date_only,
        )
        # Kalshi identity is abbreviation-only (registry) and fails closed here.
        # Polymarket supplies full names too; when the registry abbreviations
        # match no game (e.g. a league whose AISP abbreviations differ) the
        # deterministic full-name path below still decides.
        if decision.status != MappingStatus.UNRESOLVED or not (provider_home_team and provider_away_team):
            return decision

    scored: list[tuple[Game, dict[str, Any]]] = []
    for game in games:
        match = _team_orientation(session, game, provider_home_team, provider_away_team)
        if match is None:
            continue
        delta = abs((_aware(game.starts_at) - start).total_seconds())
        scored.append((game, {**match, "delta": delta}))

    if not scored:
        return MappingDecision(MappingStatus.UNRESOLVED, reason="No AISP game matched the provider teams")

    # Reversed-teams-only candidates that are not a clean forward match fail closed.
    forward = [(g, m) for g, m in scored if m["orientation"] == "FORWARD"]
    reversed_only = [(g, m) for g, m in scored if m["orientation"] == "REVERSED"]
    single = [(g, m) for g, m in scored if m["orientation"] == "SINGLE"]

    candidates = forward or single
    if not candidates and reversed_only:
        return MappingDecision(
            MappingStatus.REVIEW_REQUIRED,
            reason="Provider teams appear reversed vs the AISP game; needs review",
            candidate_game_ids=[str(g.id) for g, _ in reversed_only],
        )

    # Multiple distinct games (e.g. doubleheader / concurrent) => ambiguous.
    within_exact = [(g, m) for g, m in candidates if m["delta"] <= EXACT_START_WINDOW.total_seconds()]
    pool = within_exact or candidates
    distinct_games = {str(g.id) for g, _ in pool}
    if len(distinct_games) > 1:
        return MappingDecision(
            MappingStatus.AMBIGUOUS,
            reason="Multiple AISP games match (possible doubleheader / concurrent events)",
            candidate_game_ids=sorted(distinct_games),
        )

    game, meta = pool[0]
    delta_seconds = int(meta["delta"])
    if delta_seconds > EXACT_START_WINDOW.total_seconds():
        return MappingDecision(
            MappingStatus.REVIEW_REQUIRED,
            canonical_game_id=game.id,
            reason=f"Start-time mismatch of {delta_seconds}s exceeds the exact window",
            start_time_delta_seconds=delta_seconds,
            candidate_game_ids=[str(game.id)],
            home_is_yes=meta.get("home_is_yes"),
        )
    return MappingDecision(
        MappingStatus.EXACT,
        canonical_game_id=game.id,
        reason="Deterministic team + start-time match",
        start_time_delta_seconds=delta_seconds,
        candidate_game_ids=[str(game.id)],
        home_is_yes=meta.get("home_is_yes"),
    )


def _map_by_abbreviation(
    session: Session, games: list[Game], start: datetime, *, home_abbr: str, away_abbr: str, game_number: int | None,
    date_only: bool = False,
) -> MappingDecision:
    forward: list[Game] = []
    reversed_: list[Game] = []
    for game in games:
        home = _team(session, game, is_home=True)
        away = _team(session, game, is_home=False)
        if home is None or away is None:
            continue
        if home.abbreviation.upper() == home_abbr and away.abbreviation.upper() == away_abbr:
            forward.append(game)
        elif home.abbreviation.upper() == away_abbr and away.abbreviation.upper() == home_abbr:
            reversed_.append(game)
    if not forward:
        if reversed_:
            return MappingDecision(MappingStatus.REVIEW_REQUIRED,
                                   reason="Provider teams appear reversed vs the AISP game; needs review",
                                   candidate_game_ids=[str(g.id) for g in reversed_])
        return MappingDecision(MappingStatus.UNRESOLVED, reason="No AISP game matched the registered team abbreviations")
    forward.sort(key=lambda g: _aware(g.starts_at))
    if len(forward) > 1:
        # Same teams, same window: a doubleheader. The provider ordinal decides;
        # otherwise only an unambiguous exact-window start may decide.
        if game_number is not None and 1 <= game_number <= len(forward):
            game = forward[game_number - 1]
        else:
            within = [g for g in forward if abs((_aware(g.starts_at) - start).total_seconds()) <= EXACT_START_WINDOW.total_seconds()]
            if len(within) != 1:
                return MappingDecision(MappingStatus.AMBIGUOUS,
                                       reason="Multiple AISP games match (doubleheader); provider ordinal missing",
                                       candidate_game_ids=[str(g.id) for g in forward])
            game = within[0]
    else:
        game = forward[0]
    if date_only:
        if len(forward) > 1 and game_number is None:
            return MappingDecision(MappingStatus.AMBIGUOUS, reason="Same teams twice on one ET day; provider ordinal missing",
                                   candidate_game_ids=[str(g.id) for g in forward])
        return MappingDecision(MappingStatus.EXACT, canonical_game_id=game.id,
                               reason="Deterministic team-abbreviation match within the provider's ET game day (ticker carries no time)",
                               start_time_delta_seconds=None, candidate_game_ids=[str(game.id)], home_is_yes=True)
    delta_seconds = int(abs((_aware(game.starts_at) - start).total_seconds()))
    if delta_seconds > EXACT_START_WINDOW.total_seconds():
        return MappingDecision(MappingStatus.REVIEW_REQUIRED, canonical_game_id=game.id,
                               reason=f"Start-time mismatch of {delta_seconds}s exceeds the exact window",
                               start_time_delta_seconds=delta_seconds, candidate_game_ids=[str(game.id)], home_is_yes=True)
    return MappingDecision(MappingStatus.EXACT, canonical_game_id=game.id,
                           reason="Deterministic team-abbreviation + start-time match" + (f" (game {game_number})" if game_number else ""),
                           start_time_delta_seconds=delta_seconds, candidate_game_ids=[str(game.id)], home_is_yes=True)


def _team_orientation(
    session: Session, game: Game, provider_home: str | None, provider_away: str | None
) -> dict[str, Any] | None:
    """Classify how the provider teams line up with the AISP game.

    Returns orientation FORWARD (home==home, away==away), REVERSED (crossed),
    or SINGLE (only one provider team known — Kalshi YES side), with home_is_yes.
    """
    home_team = _team(session, game, is_home=True)
    away_team = _team(session, game, is_home=False)
    if home_team is None or away_team is None:
        return None
    ph = provider_home.strip() if provider_home else ""
    pa = provider_away.strip() if provider_away else ""

    def matches(team: Team, name: str) -> bool:
        if not name:
            return False
        return team_name_match(team.name, name) or team_name_match(team.abbreviation, name) or team_name_match(f"{team.city or ''} {team.name}".strip(), name)

    if ph and pa:
        if matches(home_team, ph) and matches(away_team, pa):
            return {"orientation": "FORWARD", "home_is_yes": True}
        if matches(home_team, pa) and matches(away_team, ph):
            return {"orientation": "REVERSED", "home_is_yes": False}
        return None
    # Single provider team (Kalshi YES side): match to home OR away.
    yes_name = ph or pa
    if matches(home_team, yes_name):
        return {"orientation": "SINGLE", "home_is_yes": True}
    if matches(away_team, yes_name):
        return {"orientation": "SINGLE", "home_is_yes": False}
    return None


def _team(session: Session, game: Game, *, is_home: bool) -> Team | None:
    gt = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(is_home)))
    return session.get(Team, gt.team_id) if gt else None


# --------------------------------------------------------------------------
# Contract-semantics review (section 10) — a matched identity is not enough.
# --------------------------------------------------------------------------
_REGULATION_HINTS = ("regulation", "excluding overtime", "not including ot", "60 minutes", "9 innings only")
_FULLGAME_HINTS = ("including overtime", "final score", "wins the game", "to win")


def review_semantics(
    *,
    market_question: str | None,
    resolution_rules: str | None,
    market_status: str | None,
) -> tuple[SemanticsStatus, str]:
    """Conservative semantics gate. Only clear full-game contracts pass; a
    regulation-only or unknown contract fails closed (never silently combined)."""

    blob = " ".join(filter(None, [market_question, resolution_rules])).lower()
    if not blob:
        return SemanticsStatus.SEMANTICS_UNKNOWN, "No rules/title text to review"
    if any(hint in blob for hint in _REGULATION_HINTS):
        return SemanticsStatus.SEMANTICS_MISMATCH, "Contract appears regulation-only (overtime treatment differs)"
    if any(hint in blob for hint in _FULLGAME_HINTS):
        return SemanticsStatus.SEMANTICS_MATCHED, "Full-game winner semantics recognized"
    return SemanticsStatus.SEMANTICS_REVIEW_REQUIRED, "Semantics not automatically confirmed; needs review"


__all__ = ["MappingDecision", "map_event_to_game", "review_semantics", "resolve_league_id", "EXACT_START_WINDOW"]
