"""Governed base/out transitions and a deterministic Gen-2 game simulator.

This module is deliberately provider-free.  It consumes already-governed event
probabilities and canonical plate-appearance transitions; it never fabricates
historical evidence or performs acquisition.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from app.gen2.event_taxonomy import PlateAppearanceOutcome, ordered_outcomes


TRANSITION_MODEL_VERSION = "mlb_gen2_base_out_transition_v1"
SIMULATOR_VERSION = "mlb_gen2_discrete_event_simulator_v1"
PROBABILITY_TOLERANCE = 1e-12


class SimulationError(ValueError):
    """Raised when a transition or simulation violates a governed invariant."""


def _canonical_checksum(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True, order=True)
class BaseOutState:
    """One of 24 ordinary states or the single inning-end state."""

    outs: int
    base_mask: int

    def __post_init__(self) -> None:
        if self.outs not in (0, 1, 2, 3):
            raise SimulationError("outs must be in 0..3")
        if not 0 <= self.base_mask <= 7:
            raise SimulationError("base_mask must be in 0..7")
        if self.outs == 3 and self.base_mask != 0:
            raise SimulationError("inning-end state must clear the bases")

    @property
    def inning_end(self) -> bool:
        return self.outs == 3

    @property
    def runners(self) -> int:
        return int(self.base_mask).bit_count()

    @classmethod
    def ordinary_states(cls) -> tuple[BaseOutState, ...]:
        return tuple(cls(outs, mask) for outs in range(3) for mask in range(8))

    @classmethod
    def inning_end_state(cls) -> BaseOutState:
        return cls(3, 0)


@dataclass(frozen=True, order=True)
class TransitionResult:
    """A sampled state transition for one completed plate appearance."""

    after: BaseOutState
    runs_scored: int

    def __post_init__(self) -> None:
        if self.runs_scored < 0:
            raise SimulationError("runs_scored must be nonnegative")


@dataclass(frozen=True)
class TransitionObservation:
    """One canonical plate-appearance transition used to fit the state model."""

    observation_id: str
    game_id: str
    plate_appearance_id: str
    outcome: PlateAppearanceOutcome
    before: BaseOutState
    after: BaseOutState
    runs_scored: int
    evidence_checksum: str
    event_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    source_evidence_ids: tuple[str, ...]
    evidence_tier: str

    def __post_init__(self) -> None:
        if not self.observation_id or not self.game_id or not self.plate_appearance_id:
            raise SimulationError("transition identities are required")
        if self.before.inning_end:
            raise SimulationError("a plate appearance cannot start after inning end")
        if self.runs_scored < 0:
            raise SimulationError("runs_scored must be nonnegative")
        if len(self.evidence_checksum) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.evidence_checksum.lower()
        ):
            raise SimulationError("evidence_checksum must be SHA-256")
        if (
            not self.source_evidence_ids
            or any(not identity for identity in self.source_evidence_ids)
            or len(set(self.source_evidence_ids)) != len(self.source_evidence_ids)
        ):
            raise SimulationError("transition source evidence identities must be unique")
        if self.evidence_tier not in {"A", "B"}:
            raise SimulationError("transition evidence tier must be A or B")
        if _utc(self.evidence_end_at) < _utc(self.event_at):
            raise SimulationError("transition evidence cannot end before the event")
        if _utc(self.eligible_at) < _utc(self.evidence_end_at):
            raise SimulationError("transition cannot be eligible before evidence ends")
        outs_added = self.after.outs - self.before.outs
        if outs_added < 0 or outs_added > 3 - self.before.outs:
            raise SimulationError("outs transition is impossible")
        available = self.before.runners + 1
        accounted = self.after.runners + outs_added + self.runs_scored
        if self.after.inning_end:
            if accounted > available:
                raise SimulationError("inning-end transition creates runners or runs")
        elif accounted != available:
            raise SimulationError("transition does not conserve batter/runners/outs/runs")
        if self.outcome is PlateAppearanceOutcome.HOME_RUN and (
            self.after.inning_end
            or self.after.outs != self.before.outs
            or self.after.base_mask != 0
            or self.runs_scored != self.before.runners + 1
        ):
            raise SimulationError("home-run transition does not clear and score all runners")


@dataclass(frozen=True)
class TransitionProbability:
    result: TransitionResult
    probability: float


def _result_is_valid(before: BaseOutState, result: TransitionResult) -> bool:
    try:
        TransitionObservation(
            observation_id="validation",
            game_id="validation",
            plate_appearance_id="validation",
            outcome=ordered_outcomes()[0],
            before=before,
            after=result.after,
            runs_scored=result.runs_scored,
            evidence_checksum="0" * 64,
            event_at=datetime(2000, 1, 1, tzinfo=UTC),
            evidence_end_at=datetime(2000, 1, 1, tzinfo=UTC),
            eligible_at=datetime(2000, 1, 1, tzinfo=UTC),
            source_evidence_ids=("validation",),
            evidence_tier="A",
        )
    except SimulationError:
        return False
    return True


@dataclass(frozen=True)
class BaseOutTransitionModel:
    """Dirichlet-shrunk transition distributions by event and base/out state."""

    version: str
    alpha: float
    distributions: Mapping[
        tuple[PlateAppearanceOutcome, BaseOutState],
        tuple[TransitionProbability, ...],
    ]
    evidence_checksum: str
    observation_count: int
    fit_cutoff: datetime
    source_evidence_ids: tuple[str, ...]

    @property
    def support_coverage(self) -> float:
        return len(self.distributions) / (len(ordered_outcomes()) * 24)

    def missing_support(self) -> tuple[tuple[PlateAppearanceOutcome, BaseOutState], ...]:
        return tuple(
            (outcome, state)
            for outcome in ordered_outcomes()
            for state in BaseOutState.ordinary_states()
            if (outcome, state) not in self.distributions
        )

    def probabilities(
        self,
        outcome: PlateAppearanceOutcome,
        before: BaseOutState,
    ) -> tuple[TransitionProbability, ...]:
        if before.inning_end:
            raise SimulationError("cannot transition from inning end")
        values = self.distributions.get((outcome, before))
        if not values:
            raise SimulationError(
                f"no governed transition support for {outcome.value} at "
                f"outs={before.outs},base_mask={before.base_mask}"
            )
        total = sum(item.probability for item in values)
        if abs(total - 1.0) > PROBABILITY_TOLERANCE:
            raise SimulationError("transition probabilities do not sum to one")
        return values

    def sample(
        self,
        outcome: PlateAppearanceOutcome,
        before: BaseOutState,
        rng: np.random.Generator,
    ) -> TransitionResult:
        values = self.probabilities(outcome, before)
        index = int(rng.choice(len(values), p=[item.probability for item in values]))
        return values[index].result


def fit_base_out_transition_model(
    observations: Iterable[TransitionObservation],
    *,
    cutoff: datetime,
    current_game_id: str,
    allowed_evidence_tiers: tuple[str, ...] = ("A",),
    alpha: float = 2.0,
) -> BaseOutTransitionModel:
    """Fit exact-state posteriors with a valid event/outs-level Dirichlet prior."""

    if not isfinite(alpha) or alpha < 0:
        raise SimulationError("alpha must be finite and nonnegative")
    boundary = _utc(cutoff)
    if not current_game_id:
        raise SimulationError("current_game_id is required")
    if (
        not allowed_evidence_tiers
        or any(tier not in {"A", "B"} for tier in allowed_evidence_tiers)
        or len(set(allowed_evidence_tiers)) != len(allowed_evidence_tiers)
    ):
        raise SimulationError("allowed evidence tiers are invalid")
    source_rows = tuple(observations)
    rows = tuple(
        row
        for row in source_rows
        if row.game_id != current_game_id
        and _utc(row.evidence_end_at) < boundary
        and _utc(row.eligible_at) <= boundary
        and row.evidence_tier in allowed_evidence_tiers
    )
    if not rows:
        raise SimulationError("at least one transition observation is required")
    semantic: dict[tuple[str, str], TransitionObservation] = {}
    for row in rows:
        key = (row.game_id, row.plate_appearance_id)
        prior = semantic.get(key)
        if prior is not None and prior != row:
            raise SimulationError("conflicting duplicate plate-appearance transition")
        semantic[key] = row
    unique_rows = tuple(sorted(semantic.values(), key=lambda row: row.observation_id))

    exact_counts: dict[
        tuple[PlateAppearanceOutcome, BaseOutState], Counter[TransitionResult]
    ] = defaultdict(Counter)
    for row in unique_rows:
        result = TransitionResult(row.after, row.runs_scored)
        exact_counts[(row.outcome, row.before)][result] += 1

    distributions: dict[
        tuple[PlateAppearanceOutcome, BaseOutState],
        tuple[TransitionProbability, ...],
    ] = {}
    for key, observed in exact_counts.items():
        support = sorted(observed)
        weights: list[tuple[TransitionResult, float]] = []
        for result in support:
            prior_mass = 1.0 / len(support)
            weights.append((result, observed.get(result, 0) + alpha * prior_mass))
        denominator = sum(weight for _, weight in weights)
        if denominator <= 0:
            raise SimulationError("transition posterior has no positive mass")
        distributions[key] = tuple(
            TransitionProbability(result, weight / denominator)
            for result, weight in weights
            if weight > 0
        )

    evidence = [
        {
            "observation_id": row.observation_id,
            "game_id": row.game_id,
            "plate_appearance_id": row.plate_appearance_id,
            "outcome": row.outcome.value,
            "before": [row.before.outs, row.before.base_mask],
            "after": [row.after.outs, row.after.base_mask],
            "runs_scored": row.runs_scored,
            "evidence_checksum": row.evidence_checksum,
            "event_at": _utc(row.event_at).isoformat(),
            "evidence_end_at": _utc(row.evidence_end_at).isoformat(),
            "eligible_at": _utc(row.eligible_at).isoformat(),
            "source_evidence_ids": sorted(row.source_evidence_ids),
            "evidence_tier": row.evidence_tier,
        }
        for row in unique_rows
    ]
    return BaseOutTransitionModel(
        version=TRANSITION_MODEL_VERSION,
        alpha=alpha,
        distributions=distributions,
        evidence_checksum=_canonical_checksum(
            {
                "version": TRANSITION_MODEL_VERSION,
                "fit_cutoff": boundary.isoformat(),
                "current_game_id": current_game_id,
                "allowed_evidence_tiers": allowed_evidence_tiers,
                "alpha": alpha,
                "rows": evidence,
            }
        ),
        observation_count=len(unique_rows),
        fit_cutoff=boundary,
        source_evidence_ids=tuple(
            sorted({identity for row in unique_rows for identity in row.source_evidence_ids})
        ),
    )


def validate_outcome_probabilities(
    probabilities: Mapping[PlateAppearanceOutcome, float],
) -> tuple[float, ...]:
    """Return the frozen-order vector after strict probability validation."""

    expected = ordered_outcomes()
    if set(probabilities) != set(expected):
        raise SimulationError("event probability vector has missing or unexpected outcomes")
    values = tuple(float(probabilities[outcome]) for outcome in expected)
    if any(not isfinite(value) or value < 0 for value in values):
        raise SimulationError("event probabilities must be finite and nonnegative")
    if abs(sum(values) - 1.0) > PROBABILITY_TOLERANCE:
        raise SimulationError("event probabilities must sum to one within 1e-12")
    return values


@dataclass(frozen=True)
class OutcomeContext:
    game_id: str
    batting_team_id: str
    fielding_team_id: str
    batter_id: str
    pitcher_id: str
    inning: int
    top: bool
    state: BaseOutState
    batting_score: int
    fielding_score: int


OutcomeProbabilityProvider = Callable[
    [OutcomeContext], Mapping[PlateAppearanceOutcome, float]
]


@dataclass(frozen=True)
class RelieverOption:
    player_id: str
    selection_weight: float
    max_batters: int = 6

    def __post_init__(self) -> None:
        if not self.player_id:
            raise SimulationError("reliever player_id is required")
        if not isfinite(self.selection_weight) or self.selection_weight < 0:
            raise SimulationError("reliever selection_weight must be nonnegative")
        if self.max_batters <= 0:
            raise SimulationError("max_batters must be positive")


@dataclass(frozen=True)
class TeamSimulationInput:
    team_id: str
    batting_order: tuple[str, ...]
    starter_id: str
    starter_exit_hazards: tuple[float, ...]
    relievers: tuple[RelieverOption, ...]

    def __post_init__(self) -> None:
        if not self.team_id or not self.starter_id:
            raise SimulationError("team and starter identities are required")
        if len(self.batting_order) != 9 or len(set(self.batting_order)) != 9:
            raise SimulationError("batting_order must contain nine unique players")
        if not self.starter_exit_hazards:
            raise SimulationError("starter exit hazards are required")
        if any(not isfinite(value) or not 0 <= value <= 1 for value in self.starter_exit_hazards):
            raise SimulationError("starter exit hazards must be probabilities")
        if not self.relievers or sum(row.selection_weight for row in self.relievers) <= 0:
            raise SimulationError("at least one weighted reliever is required")
        reliever_ids = tuple(row.player_id for row in self.relievers)
        if len(reliever_ids) != len(set(reliever_ids)):
            raise SimulationError("reliever identities must be unique")
        if self.starter_id in reliever_ids:
            raise SimulationError("starter cannot also appear in the governed reliever pool")


@dataclass(frozen=True)
class GameSimulationInput:
    game_id: str
    season: int
    away: TeamSimulationInput
    home: TeamSimulationInput
    game_type: str
    automatic_runner_start_inning: int | None
    total_line: float = 8.5
    home_run_line: float = -1.5
    regulation_innings: int = 9
    max_innings: int = 30
    max_plate_appearances: int = 500

    def __post_init__(self) -> None:
        if not self.game_id or self.away.team_id == self.home.team_id:
            raise SimulationError("game and distinct team identities are required")
        if self.season < 1876:
            raise SimulationError("season is invalid")
        if self.game_type not in {"R", "F", "D", "L", "W", "S", "E", "A"}:
            raise SimulationError("game_type is outside the governed MLB codes")
        if self.regulation_innings < 1 or self.max_innings < self.regulation_innings:
            raise SimulationError("inning bounds are invalid")
        if self.automatic_runner_start_inning is not None and (
            self.automatic_runner_start_inning <= self.regulation_innings
            or self.automatic_runner_start_inning > self.max_innings
        ):
            raise SimulationError("automatic-runner inning is invalid")
        if not isfinite(self.total_line) or not isfinite(self.home_run_line):
            raise SimulationError("simulation lines must be finite")
        if self.max_plate_appearances <= 0:
            raise SimulationError("max_plate_appearances must be positive")


@dataclass(frozen=True)
class PlateAppearanceTrace:
    inning: int
    top: bool
    batter_id: str
    pitcher_id: str
    outcome: PlateAppearanceOutcome
    before: BaseOutState
    after: BaseOutState | None
    runs_scored: int
    terminal_state_unobserved: bool = False


@dataclass(frozen=True)
class SingleGameSimulation:
    away_runs: int
    home_runs: int
    innings: int
    plate_appearances: int
    trace: tuple[PlateAppearanceTrace, ...]

    def __post_init__(self) -> None:
        if self.away_runs < 0 or self.home_runs < 0:
            raise SimulationError("final runs must be nonnegative")
        if self.away_runs == self.home_runs:
            raise SimulationError("a completed simulation cannot end tied")


@dataclass(frozen=True)
class ScoreProbability:
    away_runs: int
    home_runs: int
    probability: float


@dataclass(frozen=True)
class GameSimulationSummary:
    version: str
    game_id: str
    simulations: int
    seed_checksum: str
    home_win_probability: float
    away_win_probability: float
    home_run_line_cover_probability: float
    total_over_probability: float
    mean_home_runs: float
    mean_away_runs: float
    mean_total_runs: float
    mean_margin: float
    total_quantiles: Mapping[str, float]
    margin_quantiles: Mapping[str, float]
    score_distribution: tuple[ScoreProbability, ...]
    checksum: str


@dataclass
class _PitcherState:
    player_id: str
    is_starter: bool
    starter_batters: int = 0
    reliever_batters: int = 0
    used_relievers: tuple[str, ...] = ()


def _choose_reliever(
    team: TeamSimulationInput,
    used: Sequence[str],
    rng: np.random.Generator,
) -> RelieverOption:
    available = [row for row in team.relievers if row.player_id not in used]
    if not available:
        raise SimulationError("governed bullpen pool is exhausted; pitcher re-entry is forbidden")
    weights = np.asarray([row.selection_weight for row in available], dtype=float)
    if float(weights.sum()) <= 0:
        raise SimulationError("available reliever weights have no positive mass")
    weights /= weights.sum()
    return available[int(rng.choice(len(available), p=weights))]


def _maybe_change_pitcher(
    state: _PitcherState,
    team: TeamSimulationInput,
    rng: np.random.Generator,
) -> _PitcherState:
    if state.is_starter:
        index = min(state.starter_batters - 1, len(team.starter_exit_hazards) - 1)
        if state.starter_batters > 0 and rng.random() < team.starter_exit_hazards[index]:
            selected = _choose_reliever(team, state.used_relievers, rng)
            return _PitcherState(
                player_id=selected.player_id,
                is_starter=False,
                reliever_batters=0,
                used_relievers=state.used_relievers + (selected.player_id,),
            )
        return state
    current = next((row for row in team.relievers if row.player_id == state.player_id), None)
    if current is None:
        raise SimulationError("active reliever is absent from the governed pool")
    if state.reliever_batters >= current.max_batters:
        selected = _choose_reliever(team, state.used_relievers, rng)
        return _PitcherState(
            player_id=selected.player_id,
            is_starter=False,
            reliever_batters=0,
            used_relievers=state.used_relievers + (selected.player_id,),
        )
    return state


def _simulate_half_inning(
    *,
    game: GameSimulationInput,
    inning: int,
    top: bool,
    batting: TeamSimulationInput,
    fielding: TeamSimulationInput,
    batter_index: int,
    pitcher: _PitcherState,
    batting_score: int,
    fielding_score: int,
    transitions: BaseOutTransitionModel,
    probability_provider: OutcomeProbabilityProvider,
    rng: np.random.Generator,
    remaining_pa: int,
    capture_trace: bool,
) -> tuple[int, int, _PitcherState, int, tuple[PlateAppearanceTrace, ...], bool]:
    state = BaseOutState(
        0,
        2
        if game.automatic_runner_start_inning is not None
        and inning >= game.automatic_runner_start_inning
        else 0,
    )
    runs = 0
    trace: list[PlateAppearanceTrace] = []
    walkoff = False
    used = 0
    while not state.inning_end:
        if used >= remaining_pa:
            raise SimulationError("simulation exceeded the plate-appearance cap")
        batter_id = batting.batting_order[batter_index]
        context = OutcomeContext(
            game_id=game.game_id,
            batting_team_id=batting.team_id,
            fielding_team_id=fielding.team_id,
            batter_id=batter_id,
            pitcher_id=pitcher.player_id,
            inning=inning,
            top=top,
            state=state,
            batting_score=batting_score + runs,
            fielding_score=fielding_score,
        )
        vector = validate_outcome_probabilities(probability_provider(context))
        outcome = ordered_outcomes()[int(rng.choice(len(vector), p=vector))]
        before = state
        result = transitions.sample(outcome, before, rng)
        state = result.after
        credited_runs = result.runs_scored
        terminal_state_unobserved = False
        if (
            not top
            and inning >= game.regulation_innings
            and outcome is not PlateAppearanceOutcome.HOME_RUN
            and batting_score + runs + credited_runs > fielding_score
        ):
            # On a non-home-run walk-off, play ends when the winning run scores.
            credited_runs = max(0, fielding_score - (batting_score + runs) + 1)
            terminal_state_unobserved = credited_runs != result.runs_scored
        runs += credited_runs
        used += 1
        if capture_trace:
            trace.append(
                PlateAppearanceTrace(
                    inning=inning,
                    top=top,
                    batter_id=batter_id,
                    pitcher_id=pitcher.player_id,
                    outcome=outcome,
                    before=before,
                    after=None if terminal_state_unobserved else state,
                    runs_scored=credited_runs,
                    terminal_state_unobserved=terminal_state_unobserved,
                )
            )
        batter_index = (batter_index + 1) % 9
        if pitcher.is_starter:
            pitcher.starter_batters += 1
        else:
            pitcher.reliever_batters += 1
        if not top and inning >= game.regulation_innings and batting_score + runs > fielding_score:
            walkoff = True
            break
        game_ends_at_half = (
            state.inning_end
            and inning >= game.regulation_innings
            and (
                (top and fielding_score > batting_score + runs)
                or (not top and batting_score + runs != fielding_score)
            )
        )
        if not game_ends_at_half:
            pitcher = _maybe_change_pitcher(pitcher, fielding, rng)
    return runs, batter_index, pitcher, used, tuple(trace), walkoff


def simulate_once(
    game: GameSimulationInput,
    transitions: BaseOutTransitionModel,
    probability_provider: OutcomeProbabilityProvider,
    *,
    seed: str | int,
    capture_trace: bool = False,
) -> SingleGameSimulation:
    """Simulate one complete, untied game with deterministic seed derivation."""

    seed_bytes = str(seed).encode("utf-8")
    seed_int = int.from_bytes(sha256(seed_bytes + game.game_id.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed_int)
    away_index = 0
    home_index = 0
    away_pitcher = _PitcherState(game.away.starter_id, True)
    home_pitcher = _PitcherState(game.home.starter_id, True)
    away_runs = 0
    home_runs = 0
    total_pa = 0
    trace: list[PlateAppearanceTrace] = []

    for inning in range(1, game.max_innings + 1):
        top_runs, away_index, home_pitcher, used, half_trace, _ = _simulate_half_inning(
            game=game,
            inning=inning,
            top=True,
            batting=game.away,
            fielding=game.home,
            batter_index=away_index,
            pitcher=home_pitcher,
            batting_score=away_runs,
            fielding_score=home_runs,
            transitions=transitions,
            probability_provider=probability_provider,
            rng=rng,
            remaining_pa=game.max_plate_appearances - total_pa,
            capture_trace=capture_trace,
        )
        away_runs += top_runs
        total_pa += used
        trace.extend(half_trace)

        if inning >= game.regulation_innings and home_runs > away_runs:
            return SingleGameSimulation(away_runs, home_runs, inning, total_pa, tuple(trace))

        bottom_runs, home_index, away_pitcher, used, half_trace, walkoff = _simulate_half_inning(
            game=game,
            inning=inning,
            top=False,
            batting=game.home,
            fielding=game.away,
            batter_index=home_index,
            pitcher=away_pitcher,
            batting_score=home_runs,
            fielding_score=away_runs,
            transitions=transitions,
            probability_provider=probability_provider,
            rng=rng,
            remaining_pa=game.max_plate_appearances - total_pa,
            capture_trace=capture_trace,
        )
        home_runs += bottom_runs
        total_pa += used
        trace.extend(half_trace)
        if inning >= game.regulation_innings and (walkoff or home_runs != away_runs):
            return SingleGameSimulation(away_runs, home_runs, inning, total_pa, tuple(trace))

    raise SimulationError("simulation reached the inning cap without an untied final")


def simulate_game(
    game: GameSimulationInput,
    transitions: BaseOutTransitionModel,
    probability_provider: OutcomeProbabilityProvider,
    *,
    simulations: int = 50_000,
    seed: str | int,
    probability_provider_identity_checksum: str,
) -> GameSimulationSummary:
    """Return deterministic joint-score and betting-target research summaries."""

    if simulations <= 0 or simulations > 100_000:
        raise SimulationError("simulations must be in 1..100000")
    if len(probability_provider_identity_checksum) != 64 or any(
        character not in "0123456789abcdef"
        for character in probability_provider_identity_checksum.lower()
    ):
        raise SimulationError("probability-provider identity must be SHA-256")
    seed_material = f"{SIMULATOR_VERSION}|{game.game_id}|{seed}"
    scores: list[tuple[int, int]] = []
    for index in range(simulations):
        result = simulate_once(
            game,
            transitions,
            probability_provider,
            seed=f"{seed_material}|{index}",
        )
        scores.append((result.away_runs, result.home_runs))

    array = np.asarray(scores, dtype=float)
    away = array[:, 0]
    home = array[:, 1]
    totals = away + home
    margins = home - away
    score_counts = Counter(scores)
    score_distribution = tuple(
        ScoreProbability(a, h, count / simulations)
        for (a, h), count in sorted(score_counts.items())
    )
    quantile_points = (0.05, 0.25, 0.5, 0.75, 0.95)
    total_quantiles = {
        str(point): float(np.quantile(totals, point)) for point in quantile_points
    }
    margin_quantiles = {
        str(point): float(np.quantile(margins, point)) for point in quantile_points
    }
    home_win_probability = float(np.mean(home > away))
    away_win_probability = float(np.mean(away > home))
    home_cover_probability = float(np.mean(margins > -game.home_run_line))
    total_over_probability = float(np.mean(totals > game.total_line))
    mean_home_runs = float(np.mean(home))
    mean_away_runs = float(np.mean(away))
    mean_total_runs = float(np.mean(totals))
    mean_margin = float(np.mean(margins))
    payload = {
        "version": SIMULATOR_VERSION,
        "game": _game_payload(game),
        "transition_model": {
            "version": transitions.version,
            "alpha": transitions.alpha,
            "evidence_checksum": transitions.evidence_checksum,
            "observation_count": transitions.observation_count,
            "fit_cutoff": _utc(transitions.fit_cutoff).isoformat(),
            "source_evidence_ids": sorted(transitions.source_evidence_ids),
            "support_coverage": transitions.support_coverage,
        },
        "probability_provider_identity_checksum": probability_provider_identity_checksum,
        "simulations": simulations,
        "seed_checksum": sha256(seed_material.encode()).hexdigest(),
        "scores": [[row.away_runs, row.home_runs, row.probability] for row in score_distribution],
        "total_quantiles": total_quantiles,
        "margin_quantiles": margin_quantiles,
        "home_win_probability": home_win_probability,
        "away_win_probability": away_win_probability,
        "home_run_line_cover_probability": home_cover_probability,
        "total_over_probability": total_over_probability,
        "mean_home_runs": mean_home_runs,
        "mean_away_runs": mean_away_runs,
        "mean_total_runs": mean_total_runs,
        "mean_margin": mean_margin,
    }
    return GameSimulationSummary(
        version=SIMULATOR_VERSION,
        game_id=game.game_id,
        simulations=simulations,
        seed_checksum=payload["seed_checksum"],
        home_win_probability=home_win_probability,
        away_win_probability=away_win_probability,
        home_run_line_cover_probability=home_cover_probability,
        total_over_probability=total_over_probability,
        mean_home_runs=mean_home_runs,
        mean_away_runs=mean_away_runs,
        mean_total_runs=mean_total_runs,
        mean_margin=mean_margin,
        total_quantiles=total_quantiles,
        margin_quantiles=margin_quantiles,
        score_distribution=score_distribution,
        checksum=_canonical_checksum(payload),
    )


def sample_outcome_frequencies(
    probabilities: Mapping[PlateAppearanceOutcome, float],
    *,
    draws: int,
    seed: str | int,
) -> Mapping[PlateAppearanceOutcome, float]:
    """Vectorized frequency validation helper for large Monte Carlo checks."""

    if draws <= 0:
        raise SimulationError("draws must be positive")
    vector = np.asarray(validate_outcome_probabilities(probabilities), dtype=float)
    seed_int = int.from_bytes(sha256(str(seed).encode()).digest()[:8], "big")
    counts = np.random.default_rng(seed_int).multinomial(draws, vector)
    return {
        outcome: float(count / draws)
        for outcome, count in zip(ordered_outcomes(), counts, strict=True)
    }


def _team_payload(team: TeamSimulationInput) -> dict[str, object]:
    return {
        "team_id": team.team_id,
        "batting_order": team.batting_order,
        "starter_id": team.starter_id,
        "starter_exit_hazards": team.starter_exit_hazards,
        "relievers": [
            {
                "player_id": row.player_id,
                "selection_weight": row.selection_weight,
                "max_batters": row.max_batters,
            }
            for row in team.relievers
        ],
    }


def _game_payload(game: GameSimulationInput) -> dict[str, object]:
    return {
        "game_id": game.game_id,
        "season": game.season,
        "game_type": game.game_type,
        "away": _team_payload(game.away),
        "home": _team_payload(game.home),
        "total_line": game.total_line,
        "home_run_line": game.home_run_line,
        "automatic_runner_start_inning": game.automatic_runner_start_inning,
        "regulation_innings": game.regulation_innings,
        "max_innings": game.max_innings,
        "max_plate_appearances": game.max_plate_appearances,
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SimulationError("transition timestamps must be timezone-aware")
    return value.astimezone(UTC)
