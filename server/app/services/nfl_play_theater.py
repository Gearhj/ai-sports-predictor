"""Read model for the play-level NFL theater and scenario laboratory.

WHAT THIS SERVES
----------------
Two shapes, deliberately separate:

``theater_index`` is cheap. It answers "what can I watch?" -- which scenarios
exist for this event, which six representative games each one published, what
each one's score and shape is, and what the producer says the evidence cannot
support. It carries no plays, so a page can render its whole chrome before a
single animation frame is fetched.

``representative`` is the expensive one, and it returns EXACTLY ONE game. A
representative is ~180 plays each carrying its own geometry; six of them is 2.7
MB of JSON. Serving them together would make the page wait on five games it is
not showing.

THE HONESTY THAT SHAPES THE API
-------------------------------
The 1,000-draw population is seed-paired across all seven scenarios: draw *i*
uses the same seed in BASE and in every sensitivity. Aggregate deltas are
therefore a common-random-numbers comparison, and may be reported as
like-for-like.

The six DISPLAYED representatives are not. Each scenario re-selects its own
Typical/Close/Home win/Away win/High scoring/Low scoring draws, so BASE's
"Typical" and PASS_HEAVY's "Typical" are different draws. Playing them side by
side and calling it the same game under two settings would be false.

But a genuine pairing does exist, and this service finds it: some simulation
indices appear in the representative set of BOTH scenarios, and for those the
two games ARE the same draw under two settings. ``pairing`` reports which, so
the page can offer real like-for-like playback and label everything else as the
illustrative comparison it is.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.forecast_exchange.nfl_play_level_v1 import (
    KIND_PLAY,
    PlayEvidenceUnavailable,
    read_representative_game,
)
from app.models import ResearchSupplement

SCHEMA_VERSION = "AISP_NFL_PLAY_THEATER_READ_V1"
SPORT = "NFL"

#: The order scenarios are offered in: BASE first, then the sensitivities in
#: matched pairs so a reader sees faster next to slower, pass next to run.
SCENARIO_ORDER = (
    "BASE", "FASTER_PACE", "SLOWER_PACE", "PASS_HEAVY", "RUN_HEAVY",
    "AGGRESSIVE_FOURTH", "ELEVATED_TURNOVER",
)
_SCENARIO_RANK = {name: index for index, name in enumerate(SCENARIO_ORDER)}

SCENARIO_LABELS = {
    "BASE": "Base",
    "FASTER_PACE": "Faster pace",
    "SLOWER_PACE": "Slower pace",
    "PASS_HEAVY": "Pass-heavy",
    "RUN_HEAVY": "Run-heavy",
    "AGGRESSIVE_FOURTH": "Aggressive on fourth down",
    "ELEVATED_TURNOVER": "Elevated turnover risk",
}
SCENARIO_PLAIN_ENGLISH = {
    "BASE": "The engine's own assumptions, unchanged. Every other scenario is a "
            "single deliberate change away from this one.",
    "FASTER_PACE": "Each play is modeled to consume 10% less clock. More snaps fit "
                   "in the same sixty minutes.",
    "SLOWER_PACE": "Each play is modeled to consume 10% more clock. Fewer snaps fit "
                   "in the same sixty minutes.",
    "PASS_HEAVY": "The odds of choosing to pass rise 25% relative to the other "
                  "intents. It does not make the passes better, only more frequent.",
    "RUN_HEAVY": "The odds of choosing to pass fall to 80% of base, so more "
                 "possessions end up on the ground.",
    "AGGRESSIVE_FOURTH": "The odds of going for it on fourth down rise 50% relative "
                         "to kicking. Some drives that would have punted continue.",
    "ELEVATED_TURNOVER": "Turnover-donating odds rise 25%. This raises turnover RISK; "
                         "it is not a variance-only knob.",
}

#: The state fields the renderer needs. Named exactly as the contract names them.
_STATE = (
    "possession", "quarter", "clockSeconds", "gameSecondsRemaining", "down",
    "distance", "yardline100", "homeScore", "awayScore", "phase",
)
_OUTCOME = (
    "endYardline100", "seconds", "homePointsAdded", "awayPointsAdded", "scoringType",
    "turnover", "turnoverDowns", "turnoverYardline100", "returnEndYardline100",
    "fumble", "fumbleLost", "targetYardline100",
)


def _point(raw: dict[str, Any]) -> list[float]:
    return [raw["x"], raw["y"]]


def _project_play(play: dict[str, Any]) -> dict[str, Any]:
    """Everything the renderer draws, and nothing the artifact merely repeats.

    The stored geometry object restates the play's clock, down, score, type and
    description alongside the path. Those are dropped here: one source for each
    fact means the scoreboard and the animation can never disagree.
    """
    geometry = play["geometry"]
    outcome = play["outcome"]
    return {
        "play_number": play["playNumber"],
        "drive_number": play["driveNumber"],
        "play_type": play["playType"],
        "description": play["description"],
        "before": {key: play["stateBefore"][key] for key in _STATE},
        "after": {key: play["stateAfter"][key] for key in _STATE},
        "outcome": {key: outcome[key] for key in _OUTCOME if outcome.get(key) is not None},
        "path": {
            "kind": geometry["ballPathType"],
            "direction": geometry["possessionDirection"],
            "segments": [{
                "kind": segment["kind"],
                "interpolation": segment["interpolation"],
                "possession": segment["possession"],
                "start": _point(segment["start"]),
                "control": [_point(p) for p in (segment.get("controlPoints") or [])],
                "end": _point(segment["end"]),
            } for segment in geometry["segments"]],
            "markers": [{"kind": marker["kind"], "point": _point(marker["point"])}
                        for marker in (geometry.get("markers") or [])],
        },
    }


class NflPlayTheaterService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- rows ----------------------------------------------------------------
    def _rows(self, event_id: str) -> dict[str, ResearchSupplement]:
        """Latest row per scenario for one event."""
        rows = self.session.scalars(
            select(ResearchSupplement)
            .where(
                ResearchSupplement.sport == SPORT,
                ResearchSupplement.event_id == event_id,
                ResearchSupplement.kind == KIND_PLAY,
            )
            .order_by(ResearchSupplement.imported_at.desc())
        )
        latest: dict[str, ResearchSupplement] = {}
        for row in rows:
            scenario = str((row.evidence or {}).get("scenario_id") or "")
            if scenario and scenario not in latest:
                latest[scenario] = row
        return latest

    @staticmethod
    def _scenario_block(row: ResearchSupplement) -> dict[str, Any]:
        payload = row.payload or {}
        artifact = payload.get("artifact") or {}
        scenario = artifact.get("scenario") or {}
        scenario_id = str(scenario.get("scenarioId") or "")
        receipt = (row.evidence or {}).get("verification_receipt") or {}
        return {
            "scenario_id": scenario_id,
            "label": SCENARIO_LABELS.get(scenario_id, scenario_id),
            "explanation": SCENARIO_PLAIN_ENGLISH.get(scenario_id),
            "parameters": scenario.get("parameters"),
            "interpretation": scenario.get("interpretation"),
            "turnover_sensitivity_meaning": scenario.get("turnoverSensitivityMeaning"),
            "model_input_change": scenario.get("modelInputChange"),
            "score_summary": artifact.get("scoreSummary"),
            "scenario_delta": artifact.get("scenarioDelta"),
            "quantiles": artifact.get("quantiles"),
            "reaching_overtime_probability": artifact.get("reachingOvertimeProbability"),
            "final_tie_probability": artifact.get("finalTieProbability"),
            "draw_count": artifact.get("drawCount"),
            "seed": artifact.get("seed"),
            "population_accounting": artifact.get("populationAccounting"),
            "representatives": payload.get("representatives") or [],
            "supplement_sha256": row.supplement_sha256,
            "play_count": receipt.get("plays"),
            "population_drive_stats": _population_drive_stats(artifact),
        }

    # -- index ---------------------------------------------------------------
    def theater_index(self, event_id: str) -> dict[str, Any] | None:
        rows = self._rows(event_id)
        if not rows:
            return None
        blocks = sorted(
            (self._scenario_block(row) for row in rows.values()),
            key=lambda block: (_SCENARIO_RANK.get(block["scenario_id"], 99), block["scenario_id"]),
        )
        anchor = rows.get("BASE") or next(iter(rows.values()))
        artifact = (anchor.payload or {}).get("artifact") or {}
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "engine": {
                "engine_id": anchor.engine_id,
                "label": "Play-level development engine",
                "version": (artifact.get("model") or {}).get("engineVersion"),
                "training_cutoff": (artifact.get("model") or {}).get("trainingCutoff"),
                "generating_law": artifact.get("generatingLaw"),
                "historical_evidence_through_date": artifact.get("historicalEvidenceThroughDate"),
                "historical_evidence_precision": artifact.get("historicalEvidencePrecision"),
                "input_treatment": artifact.get("inputTreatment"),
                "explains_m1_forecast": artifact.get("explainsM1Forecast", False),
                "related_m1_forecast_id": artifact.get("relatedM1ForecastId"),
            },
            "governance": {
                "status": artifact.get("status"),
                "promotion_status": artifact.get("promotionStatus"),
                "execution_allowed_in_aisp": artifact.get("executionAllowedInAISP"),
                "wagering_authority": artifact.get("wageringAuthority"),
            },
            "limitations": artifact.get("limitations") or [],
            "m1_comparison": artifact.get("m1Comparison"),
            "home_team": artifact.get("homeTeam"),
            "away_team": artifact.get("awayTeam"),
            "scenarios": blocks,
            "pairing": self._pairing(rows),
            "geometry_notice": (
                "Ball paths are modeled schematic geometry derived from each play's own "
                "state and outcome. They are not measured tracking, not player routes and "
                "carry no player identities."
            ),
        }

    @staticmethod
    def _pairing(rows: dict[str, ResearchSupplement]) -> dict[str, Any]:
        """Which representative games are the SAME draw in two scenarios.

        The population is seed-paired, so any simulation index published as a
        representative by both scenarios gives a true like-for-like pair. Where
        no index is shared, a comparison can still be shown, but it is two
        different draws and must say so.
        """
        indices: dict[str, dict[int, dict[str, Any]]] = {}
        for scenario_id, row in rows.items():
            indices[scenario_id] = {
                int(rep["simulationIndex"]): rep
                for rep in ((row.payload or {}).get("representatives") or [])
            }
        base = indices.get("BASE") or {}
        pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for scenario_id, reps in indices.items():
            if scenario_id == "BASE":
                continue
            for index in sorted(set(base) & set(reps)):
                pairs[scenario_id].append({
                    "simulation_index": index,
                    "seed": base[index]["seed"],
                    "base": {"label": base[index]["label"], "position": base[index]["position"],
                             "home_score": base[index]["homeScore"], "away_score": base[index]["awayScore"]},
                    "challenger": {"label": reps[index]["label"], "position": reps[index]["position"],
                                   "home_score": reps[index]["homeScore"], "away_score": reps[index]["awayScore"]},
                })
        return {
            "population_is_seed_paired": True,
            "population_note": (
                "Every scenario re-runs the same 1,000 seeds, so the aggregate differences "
                "below are a like-for-like comparison of the same draws under two settings."
            ),
            "representatives_are_seed_paired": False,
            "representative_note": (
                "Each scenario selects its own six representative games, so two games with "
                "the same label are usually different draws. Pairs listed here are the "
                "exception: the same draw published by both scenarios."
            ),
            "seed_paired_games": dict(pairs),
        }

    # -- one game ------------------------------------------------------------
    def representative(self, event_id: str, scenario_id: str, position: int) -> dict[str, Any] | None:
        rows = self._rows(event_id)
        row = rows.get(scenario_id)
        if row is None:
            return None
        try:
            game = read_representative_game(row, position=position)
        except PlayEvidenceUnavailable as error:
            # An exact cause, never an empty animation.
            return {
                "schema_version": SCHEMA_VERSION, "event_id": event_id,
                "scenario_id": scenario_id, "position": position,
                "available": False, "cause": str(error).split(":", 1)[0],
                "detail": str(error),
            }
        diagnostics = game.get("diagnostics") or {}
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "scenario_id": scenario_id,
            "position": position,
            "available": True,
            "supplement_sha256": row.supplement_sha256,
            "label": game["label"],
            "simulation_index": game["simulationIndex"],
            "seed": game["seed"],
            "home_team": game.get("homeTeam"),
            "away_team": game.get("awayTeam"),
            "home_score": game["homeScore"],
            "away_score": game["awayScore"],
            "regulation_home_score": game.get("regulationHomeScore"),
            "regulation_away_score": game.get("regulationAwayScore"),
            "reached_overtime": game.get("reachedOvertime"),
            "final_tie": game.get("finalTie"),
            "diagnostics": {
                "drives": diagnostics.get("drives"),
                "scrimmage_plays": diagnostics.get("scrimmagePlays"),
                "offensive_yards": diagnostics.get("offensiveYards"),
                "first_downs": diagnostics.get("firstDowns"),
                "third_down_attempts": diagnostics.get("thirdDownAttempts"),
                "third_down_conversions": diagnostics.get("thirdDownConversions"),
                "fourth_down_attempts": diagnostics.get("fourthDownAttempts"),
                "fourth_down_conversions": diagnostics.get("fourthDownConversions"),
                "completions": diagnostics.get("completions"),
                "pass_attempts": diagnostics.get("passAttempts"),
                "rush_plays": diagnostics.get("rushPlays"),
                "sacks": diagnostics.get("sacks"),
                "turnovers": diagnostics.get("turnovers"),
                "points": diagnostics.get("points"),
                "play_type_counts": diagnostics.get("playTypeCounts"),
                "impossible_state_count": diagnostics.get("impossibleStateCount"),
            },
            "plays": [_project_play(play) for play in game["plays"]],
        }


def _population_drive_stats(artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Per-game aggregates over the full 1,000 draws, not the six shown games.

    The representatives are label-selected -- Typical, High scoring, Overtime --
    so averaging them describes the selection, not the simulation. Anything
    presented as a property of the scenario has to come from the population.
    """
    population = artifact.get("populationFinals") or []
    if not population:
        return None
    count = len(population)
    home = sum(m["homeScore"] for m in population)
    away = sum(m["awayScore"] for m in population)
    drives = sum(m.get("driveCount") or 0 for m in population)
    plays = sum(m.get("playCount") or 0 for m in population)
    overtime = sum(1 for m in population if m.get("reachedOvertime"))
    return {
        "draws": count,
        "mean_home_score": home / count,
        "mean_away_score": away / count,
        "mean_total": (home + away) / count,
        "mean_drives": drives / count if drives else None,
        "mean_plays": plays / count if plays else None,
        "plays_per_drive": plays / drives if drives else None,
        "points_per_drive": (home + away) / drives if drives else None,
        "reached_overtime": overtime / count,
    }
