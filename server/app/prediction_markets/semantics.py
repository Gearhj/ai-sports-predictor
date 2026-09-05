"""Versioned provider-market-template semantic authority.

Individual markets are never approved one by one. A *template* describes one
provider contract family (exact sport/league, exact full-game-winner
semantics, extra-innings treatment, resolution rules, outcome orientation,
pregame availability, provider series/template identifier, effective date and
a source hash). A human approves or rejects the template once (audited,
append-only versions). An individual market then INHERITS the approval only
when it satisfies every template condition; anything ambiguous stays held
closed (SEMANTICS_REVIEW_REQUIRED).

Templates are seeded HELD_CLOSED. Nothing here approves anything silently.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PredictionMarketSemanticTemplate
from app.prediction_markets.contracts import CanonicalMarketType, ProviderContractDTO, SemanticsStatus

TEMPLATE_AUTHORITY_VERSION = "AISP_PM_SEMANTIC_TEMPLATE_AUTHORITY_V1"

STATUS_HELD = "HELD_CLOSED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

KALSHI_MLB_WINNER = "KALSHI_MLB_FULL_GAME_WINNER"
POLYMARKET_MLB_WINNER = "POLYMARKET_MLB_FULL_GAME_WINNER"

# Kalshi KXMLBGAME rules_primary (verified live 2026-09-04):
#   "If Cincinnati wins the Cincinnati vs Los Angeles D professional baseball game
#    originally scheduled for Sep 7, 2026 at 9:10 PM EDT, then the market resolves to Yes."
KALSHI_RULES_PATTERN = (
    r"^If (?P<team>.+?) wins the (?P<matchup>.+?) professional baseball game originally scheduled for "
    r"(?P<when>.+?), then the market resolves to Yes\.?$"
)
# Polymarket MLB moneyline description (persisted contract, verified 2026-09-04):
#   "In the upcoming MLB game between the St. Louis Cardinals and Colorado Rockies, scheduled for
#    September 6 at 3:10PM ET:\n\nThis market will resolve to "St. Louis Cardinals" if the St. Louis
#    Cardinals win the game.\n\nThis market will resolve to "Colorado Rockies" if the Colorado Rockies
#    win the game.\n\nIf the game is postponed, this market will remain open until the game has been
#    completed. If the game is canceled entirely, with no make-up game, or ends in a tie, this market
#    will resolve 50-50. ..."
POLYMARKET_RULES_PATTERN = (
    r"^In the upcoming MLB game between the (?P<a>.+?) and (?P<b>.+?), scheduled for (?P<when>.+?):\s+"
    r"This market will resolve to \"(?P<a2>.+?)\" if the (?P<a3>.+?) win the game\.\s+"
    r"This market will resolve to \"(?P<b2>.+?)\" if the (?P<b3>.+?) win the game\.\s+"
    r"If the game is postponed, this market will remain open until the game has been completed\. "
    r"If the game is canceled entirely, with no make-up game, or ends in a tie, this market will resolve 50-50\."
)

SEED_TEMPLATES: list[dict[str, Any]] = [
    {
        "provider": "KALSHI", "template_key": KALSHI_MLB_WINNER, "sport": "BASEBALL", "league": "MLB",
        "market_type": CanonicalMarketType.FULL_GAME_MONEYLINE.value,
        "conditions": {
            "series_ticker": "KXMLBGAME",
            "market_semantics": "Named team wins the full game (result of the completed game, extra innings included)",
            "extra_innings": "INCLUDED — the market resolves on the game's winner, not a fixed inning count",
            "rules_pattern": KALSHI_RULES_PATTERN,
            "rules_requirements": [
                "rules_primary matches the KXMLBGAME winner template exactly",
                "the named team equals the market's yes_sub_title",
                "the matchup names both teams of the mapped canonical game",
            ],
            "outcome_orientation": "YES = named team wins; canonical selection HOME/AWAY assigned from the event ticker (<AWAY><HOME>)",
            "pregame_availability": "markets open days before first pitch; observations after first pitch are in-play and never pregame",
            "settlement_sources": ["ESPN", "Fox Sports", "the Governing League (mlb.com)"],
            "provider_template_reference": "https://assets.kalshi.com/regulatory/product-certifications/MLBGAME.pdf",
        },
        "effective_date": "2026-09-04",
        "notes": "Public REST market data; no account, key, or trading capability involved.",
    },
    {
        "provider": "POLYMARKET", "template_key": POLYMARKET_MLB_WINNER, "sport": "BASEBALL", "league": "MLB",
        "market_type": CanonicalMarketType.FULL_GAME_MONEYLINE.value,
        "conditions": {
            "sports_market_type": "moneyline",
            "event_tag": "mlb",
            "market_semantics": "Two-outcome market resolving to the team that wins the game",
            "extra_innings": "INCLUDED — 'win the game'; a canceled game with no make-up or a tie resolves 50-50 (never a winner)",
            "rules_pattern": POLYMARKET_RULES_PATTERN,
            "rules_requirements": [
                "description matches the Polymarket MLB moneyline template exactly",
                "outcome labels are exactly the two team names named in the description",
                "the two teams equal the mapped canonical game's teams",
                "postponement keeps the market open (rescheduled start follows the canonical game)",
            ],
            "outcome_orientation": "each outcome token names its team; canonical selection HOME/AWAY assigned from the event title 'Away vs. Home'",
            "pregame_availability": "market opens days before; acceptingOrders continues in-play, so only observations before first pitch are pregame",
            "settlement_sources": ["official final statistics as recognized by the governing body (mlb.com)"],
            "provider_template_reference": "Gamma event tag 'mlb', market.sportsMarketType == 'moneyline'",
        },
        "effective_date": "2026-09-04",
        "notes": "Public Gamma/CLOB market data; no account, wallet, key, or trading capability involved.",
    },
]


def source_hash(seed: dict[str, Any]) -> str:
    payload = {k: seed[k] for k in ("provider", "template_key", "sport", "league", "market_type", "conditions")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class TemplateVerdict:
    status: SemanticsStatus
    reason: str
    template_key: str | None = None
    template_version: int | None = None
    template_status: str | None = None

    @property
    def authority(self) -> str:
        if self.template_key is None:
            return "NONE"
        return f"TEMPLATE:{self.template_key}@v{self.template_version}:{self.template_status}"


class SemanticTemplateAuthority:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- seeding / lookup ---------------------------------------------------
    def ensure_seeded(self) -> int:
        """Idempotently create the HELD_CLOSED v1 of each seed template."""
        created = 0
        for seed in SEED_TEMPLATES:
            if self.latest(seed["provider"], seed["template_key"]) is None:
                self.session.add(PredictionMarketSemanticTemplate(
                    provider=seed["provider"], template_key=seed["template_key"], version=1,
                    sport=seed["sport"], league=seed["league"], market_type=seed["market_type"],
                    conditions=seed["conditions"], source_hash=source_hash(seed), status=STATUS_HELD,
                    effective_date=date.fromisoformat(seed["effective_date"]), notes=seed["notes"],
                    decided_at=datetime.now(UTC), decision_source="SEED",
                ))
                created += 1
        if created:
            self.session.commit()
        return created

    def latest(self, provider: str, template_key: str) -> PredictionMarketSemanticTemplate | None:
        return self.session.scalar(select(PredictionMarketSemanticTemplate).where(
            PredictionMarketSemanticTemplate.provider == provider,
            PredictionMarketSemanticTemplate.template_key == template_key,
        ).order_by(PredictionMarketSemanticTemplate.version.desc()))

    def all_latest(self) -> list[PredictionMarketSemanticTemplate]:
        rows = list(self.session.scalars(select(PredictionMarketSemanticTemplate).order_by(
            PredictionMarketSemanticTemplate.provider, PredictionMarketSemanticTemplate.template_key,
            PredictionMarketSemanticTemplate.version.desc())))
        seen: set[tuple[str, str]] = set()
        out: list[PredictionMarketSemanticTemplate] = []
        for r in rows:
            key = (r.provider, r.template_key)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    def decide(self, *, provider: str, template_key: str, status: str, decided_by_user_id: Any | None,
               notes: str | None, now: datetime | None = None) -> PredictionMarketSemanticTemplate:
        """Append a new version carrying the human decision (never edits a row)."""
        if status not in {STATUS_APPROVED, STATUS_REJECTED, STATUS_HELD}:
            raise ValueError(f"unsupported template status {status!r}")
        prior = self.latest(provider, template_key)
        if prior is None:
            raise ValueError("unknown template")
        row = PredictionMarketSemanticTemplate(
            provider=prior.provider, template_key=prior.template_key, version=prior.version + 1,
            sport=prior.sport, league=prior.league, market_type=prior.market_type, conditions=prior.conditions,
            source_hash=prior.source_hash, status=status, effective_date=prior.effective_date,
            notes=notes or prior.notes, decided_at=now or datetime.now(UTC), decision_source="MANUAL",
            decided_by_user_id=decided_by_user_id, supersedes_id=prior.id,
        )
        self.session.add(row)
        self.session.commit()
        return row

    # -- evaluation -----------------------------------------------------------
    def evaluate(self, dto: ProviderContractDTO, *, home_abbr: str | None = None, away_abbr: str | None = None,
                 home_name: str | None = None, away_name: str | None = None) -> TemplateVerdict:
        """Semantics for one contract from the template authority only.

        APPROVED template + every condition satisfied -> SEMANTICS_MATCHED (inherited).
        Template HELD/REJECTED or any unmet condition -> held closed.
        """
        provider = dto.provider.value
        key = {"KALSHI": KALSHI_MLB_WINNER, "POLYMARKET": POLYMARKET_MLB_WINNER}.get(provider)
        if key is None or dto.league != "MLB" or dto.market_type != CanonicalMarketType.FULL_GAME_MONEYLINE:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_REVIEW_REQUIRED, "No template governs this contract family")
        tpl = self.latest(provider, key)
        if tpl is None:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_REVIEW_REQUIRED, "Template authority not seeded")
        ok, why = self._satisfies(tpl, dto, home_abbr=home_abbr, away_abbr=away_abbr, home_name=home_name, away_name=away_name)
        if not ok:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_REVIEW_REQUIRED, f"Held closed: {why}", key, tpl.version, tpl.status)
        if tpl.status == STATUS_REJECTED:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_MISMATCH, "Template rejected by human review", key, tpl.version, tpl.status)
        if tpl.status != STATUS_APPROVED:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_REVIEW_REQUIRED,
                                   "Contract satisfies the template; template awaits human approval", key, tpl.version, tpl.status)
        if dto.scheduled_start_at is not None and dto.scheduled_start_at.date() < tpl.effective_date:
            return TemplateVerdict(SemanticsStatus.SEMANTICS_REVIEW_REQUIRED, "Before template effective date", key, tpl.version, tpl.status)
        return TemplateVerdict(SemanticsStatus.SEMANTICS_MATCHED, "Inherited from approved template", key, tpl.version, tpl.status)

    @staticmethod
    def _satisfies(tpl: PredictionMarketSemanticTemplate, dto: ProviderContractDTO, *, home_abbr, away_abbr,
                   home_name, away_name) -> tuple[bool, str]:
        cond = tpl.conditions or {}
        rules = (dto.resolution_rules or "").strip()
        if tpl.provider == "KALSHI":
            series = str((dto.raw or {}).get("series_ticker") or dto.provider_event_id.split("-", 1)[0]).upper()
            if series != cond.get("series_ticker"):
                return False, f"series {series} is not {cond.get('series_ticker')}"
            m = re.match(cond["rules_pattern"], rules, flags=re.S)
            if not m:
                return False, "rules_primary does not match the winner template"
            named = m.group("team").strip().lower()
            yes_label = str(dto.outcome_label or "").strip().lower()
            if named != yes_label:
                return False, "named team differs from the market's YES side"
            if home_abbr and away_abbr and (dto.provider_home_team, dto.provider_away_team) != (home_abbr, away_abbr):
                return False, "event teams differ from the mapped canonical game"
            return True, "all conditions satisfied"
        if tpl.provider == "POLYMARKET":
            smt = str((dto.raw or {}).get("sportsMarketType") or "").lower()
            if smt != cond.get("sports_market_type"):
                return False, f"sportsMarketType {smt or 'missing'} is not moneyline"
            m = re.match(cond["rules_pattern"], rules, flags=re.S)
            if not m:
                return False, "description does not match the moneyline template"
            a, b = m.group("a").strip(), m.group("b").strip()
            if {m.group("a2").strip(), m.group("b2").strip()} != {a, b} or {m.group("a3").strip(), m.group("b3").strip()} != {a, b}:
                return False, "resolution clauses name different teams than the header"
            if str(dto.outcome_label).strip() not in {a, b}:
                return False, "outcome label is not one of the two named teams"
            if home_name and away_name and {home_name, away_name} != {a, b}:
                return False, "named teams differ from the mapped canonical game"
            return True, "all conditions satisfied"
        return False, "unknown provider"

    # -- review packet ----------------------------------------------------------
    def review_packet(self, *, sample_contracts: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
        packet: list[dict[str, Any]] = []
        for tpl in self.all_latest():
            packet.append({
                "provider": tpl.provider, "template_key": tpl.template_key, "version": tpl.version,
                "status": tpl.status, "sport": tpl.sport, "league": tpl.league, "market_type": tpl.market_type,
                "effective_date": tpl.effective_date.isoformat(), "source_hash": tpl.source_hash,
                "conditions": tpl.conditions, "notes": tpl.notes, "decided_at": tpl.decided_at.isoformat() if tpl.decided_at else None,
                "decision_source": tpl.decision_source,
                "decision_required": tpl.status == STATUS_HELD,
                "decision": (
                    f"Approve or reject {tpl.template_key} v{tpl.version}: every {tpl.provider} MLB full-game winner "
                    "contract satisfying the listed conditions inherits SEMANTICS_MATCHED; ambiguous contracts stay held."
                ),
                "sample_contracts": (sample_contracts or {}).get(tpl.provider, []),
                "authority_version": TEMPLATE_AUTHORITY_VERSION,
            })
        return packet


__all__ = [
    "SemanticTemplateAuthority", "TemplateVerdict", "SEED_TEMPLATES", "STATUS_HELD", "STATUS_APPROVED",
    "STATUS_REJECTED", "KALSHI_MLB_WINNER", "POLYMARKET_MLB_WINNER", "KALSHI_RULES_PATTERN",
    "POLYMARKET_RULES_PATTERN", "TEMPLATE_AUTHORITY_VERSION", "source_hash",
]
